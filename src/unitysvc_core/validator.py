"""Data validation module for unitysvc_core."""

import json
import re
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jinja2 import Environment, StrictUndefined, TemplateSyntaxError, UndefinedError
from jsonschema.validators import Draft7Validator

from .grants import ExportsResolver
from .parameter_usage import validate_channel_parameters, validate_required_parameter_usage
from .utils import SCHEMA_TO_STEM, is_hidden_path, load_data_file, schema_for_path

# ``details.model``: a provider-neutral model name (``glm-5.2``, not
# ``zai-org/GLM-5.2`` or Fireworks' ``glm-5p2``). Lowercase, no ``org/``
# prefix. The backend re-checks the same pattern before it shows a name on a
# platform tier (``app/crud/platform_served.py``).
CANONICAL_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")

# svcpass api_key dispositions (#1198, #1786). On an upstream_access_config
# interface these literal values are not credentials: "" / "__strip__" strip
# the svcpass-bearing header, "__forward__" forwards svcpass verbatim, and
# "__sigv4__" strips the client credential and re-signs the finalized request
# with AWS SigV4 from the channel's access_key / secret_key /
# session_token (optional) / region (optional, default us-east-1) /
# sigv4_service fields. Any other value is an override credential (a secrets
# reference).
DISPOSITION_VALUES = ("", "__strip__", "__forward__", "__sigv4__")

# Channel fields the "__sigv4__" disposition requires (see
# validate_sigv4_disposition): without them the gateway cannot sign and
# fails the request at dispatch time — catch it at authoring time instead.
SIGV4_REQUIRED_FIELDS = ("access_key", "secret_key", "sigv4_service")

# Platform hosts svcpass may be forwarded to when api_key == "__forward__".
# svcpass is a platform credential, so forwarding to anything else is a hard
# validation error (the gateway enforces the same allowlist at runtime, #1198).
# Matched as exact host or dot-prefixed suffix.
TRUSTED_FORWARD_HOST_SUFFIXES = (".svcpass.com", ".unitysvc.com")


class UnrecognizedDataFileWarning(UserWarning):
    """Emitted when ``validate_all`` skips a JSON/TOML file because it
    isn't service data (no ``schema`` field, or a ``schema`` value that
    isn't one of the loaded schemas).

    A seller repo legitimately contains non-data files at any depth —
    e.g. ``gorse-config.example.toml`` (Docker config), ``pyproject.toml``,
    ad-hoc ``requirements.txt`` siblings — and reporting them as
    validation failures produces a noisy false positive that drowns out
    real errors.

    Standard ``warnings`` filters apply, so callers who want a strict
    "every file must declare a schema" pass can promote this with::

        warnings.simplefilter("error", UnrecognizedDataFileWarning)
    """


class DataValidationError(Exception):
    """Exception raised when data validation fails."""

    pass


class DataValidator:
    """Validates data files against JSON schemas."""

    def __init__(self, data_dir: Path, schema_dir: Path, exports_resolver: ExportsResolver | None = None):
        self.data_dir = data_dir
        self.schema_dir = schema_dir
        # Looks up the service a channel's request names; without it only the
        # shape of each customer access request is checked (see unitysvc_core.grants).
        self.exports_resolver = exports_resolver
        self.schemas: dict[str, dict[str, Any]] = {}
        # Built once per schema in load_schemas: compiling a validator and
        # checking the schema itself costs far more than validating one file.
        self._schema_validators: dict[str, Draft7Validator] = {}
        self._union_fields: dict[str, set[str]] = {}
        self.load_schemas()

    def load_schemas(self) -> None:
        """Load all JSON schemas from the schema directory."""
        if not self.schema_dir.exists():
            raise DataValidationError(
                f"Schema directory not found: {self.schema_dir}\n"
                f"This may indicate the package was not installed correctly. "
                f"Please reinstall with: pip install --force-reinstall unitysvc-core"
            )

        schema_files = list(self.schema_dir.glob("*.json"))
        if not schema_files:
            raise DataValidationError(
                f"No schema files (*.json) found in schema directory: {self.schema_dir}\n"
                f"This may indicate the package was not installed correctly. "
                f"Please reinstall with: pip install --force-reinstall unitysvc-core"
            )

        for schema_file in schema_files:
            schema_name = schema_file.stem
            try:
                with open(schema_file, encoding="utf-8") as f:
                    schema = json.load(f)
                Draft7Validator.check_schema(schema)
            except Exception as e:
                print(f"Error loading schema {schema_file}: {e}")
                continue
            self.schemas[schema_name] = schema
            self._schema_validators[schema_name] = Draft7Validator(
                schema, format_checker=Draft7Validator.FORMAT_CHECKER
            )
            self._union_fields[schema_name] = self.find_union_fields(schema)

    def is_url(self, value: str) -> bool:
        """Check if a string is a valid URL."""
        try:
            result = urlparse(value)
            return all([result.scheme, result.netloc])
        except Exception:
            return False

    def find_union_fields(self, schema: dict[str, Any]) -> set[str]:
        """Find fields that are Union[str, HttpUrl] types in the schema."""
        union_fields: set[str] = set()

        def traverse_schema(obj: Any, path: str = "") -> None:
            if isinstance(obj, dict):
                # Check for Union type with string and URL format
                if "anyOf" in obj:
                    any_of = obj["anyOf"]
                    # Count non-null items for the check
                    non_null_items = [item for item in any_of if item.get("type") != "null"]
                    has_plain_string = any(
                        item.get("type") == "string" and "format" not in item for item in non_null_items
                    )
                    has_uri_string = any(
                        item.get("type") == "string" and item.get("format") == "uri" for item in non_null_items
                    )

                    # Check for Union[str, HttpUrl] or Union[str, HttpUrl, None]
                    if len(non_null_items) == 2 and has_plain_string and has_uri_string:
                        union_fields.add(path)

                # Recursively check properties
                if "properties" in obj:
                    for prop_name, prop_schema in obj["properties"].items():
                        new_path = f"{path}.{prop_name}" if path else prop_name
                        traverse_schema(prop_schema, new_path)

                # Check other schema structures
                for key, value in obj.items():
                    if key not in ["properties", "anyOf"] and isinstance(value, dict | list):
                        traverse_schema(value, path)

            elif isinstance(obj, list):
                for item in obj:
                    traverse_schema(item, path)

        traverse_schema(schema)
        return union_fields

    def validate_file_references(self, data: dict[str, Any], file_path: Path, union_fields: set[str]) -> list[str]:
        """
        Validate that file references in Union[str, HttpUrl] fields exist.

        Also validates that all file_path fields use relative paths.
        """
        return self._file_reference_errors(data, file_path.parent, union_fields)

    def _file_reference_errors(self, data: dict[str, Any], base_dir: Path | None, union_fields: set[str]) -> list[str]:
        """File-reference checks, resolving relative paths against ``base_dir``.

        Without a ``base_dir`` (data validated in memory) a relative reference
        cannot be resolved, so only its form is checked.
        """
        errors: list[str] = []

        def check_field(obj: Any, field_path: str, current_path: str = "") -> None:
            if isinstance(obj, dict):
                for key, value in obj.items():
                    new_path = f"{current_path}.{key}" if current_path else key

                    # Check if this field is a Union[str, HttpUrl] field
                    if (
                        new_path in union_fields
                        and value is not None
                        and isinstance(value, str)
                        and not self.is_url(value)
                    ):
                        # Empty string is not a valid file reference
                        if value == "":
                            errors.append(f"Empty string in field '{new_path}' is not a valid file reference or URL")
                        # It's a file reference, must be relative path
                        elif Path(value).is_absolute():
                            errors.append(
                                f"File reference '{value}' in field '{new_path}' "
                                f"must be a relative path, not an absolute path"
                            )
                        elif base_dir is not None:
                            referenced_file = base_dir / value
                            if not referenced_file.exists():
                                errors.append(
                                    f"File reference '{value}' in field '{new_path}' "
                                    f"does not exist at {referenced_file}"
                                )

                    # Check if this is a file_path field (regardless of schema type)
                    if key == "file_path" and isinstance(value, str):
                        # file_path fields must not be URLs (use external_url instead)
                        if self.is_url(value):
                            errors.append(
                                f"File path '{value}' in field '{new_path}' "
                                f"must not be a URL. Use 'external_url' field for URLs instead."
                            )
                        elif Path(value).is_absolute():
                            # Absolute paths are only valid when they resolve to an existing
                            # file — this happens when a $doc_preset sentinel is expanded by
                            # load_data_file before validation.  A non-existent absolute path
                            # means the user hard-coded a machine-specific path, which is
                            # rejected.
                            if not Path(value).exists():
                                errors.append(
                                    f"File path '{value}' in field '{new_path}' "
                                    f"must be a relative path, not an absolute path"
                                )
                        # Check that a relative file reference exists
                        elif base_dir is not None:
                            referenced_file = base_dir / value
                            if not referenced_file.exists():
                                errors.append(
                                    f"File reference '{value}' in field '{new_path}' "
                                    f"does not exist at {referenced_file}"
                                )

                    # Recurse into nested objects
                    if isinstance(value, dict | list):
                        check_field(value, field_path, new_path)

            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    if isinstance(item, dict | list):
                        check_field(item, field_path, f"{current_path}[{i}]")

        check_field(data, "")
        return errors

    def validate_duplicate_document_titles(self, data: dict[str, Any], file_path: Path) -> list[str]:
        """Validate that document titles are unique within an entity.

        Note: With dict format, titles are dict keys which are inherently unique.
        This function is kept for backward compatibility but returns empty list.
        """
        # Documents are now stored as dict where key = title
        # Dict keys are inherently unique, so no duplicate check needed
        return []

    def validate_api_key_secrets(self, data: dict[str, Any]) -> list[str]:
        """Validate that all api_key fields use the secrets reference format.

        API keys must use one of these formats:
        - '${ secrets.VAR_NAME }' — seller-owned secret
        - '${ customer_secrets.VAR_NAME }' — customer-owned secret (BYOK)
        - '${ customer_secrets.{{ param }} }' — customer secret via Jinja2 indirection (BYOE)

        This applies to:
        - upstream_access_config.<name>.api_key (and each server's)
        - upstream_access_config.<name>.params.testing_defaults.api_key

        Args:
            data: The data to validate

        Returns:
            List of validation error messages
        """
        errors: list[str] = []

        # Seller secrets: ${ secrets.VAR_NAME }
        seller_secrets_pattern = re.compile(r"^\$\{\s*secrets\.[A-Za-z_][A-Za-z0-9_]*\s*\}$")
        # Customer secrets: ${ customer_secrets.VAR_NAME } or ${ customer_secrets.{{ jinja }} }
        customer_secrets_pattern = re.compile(r"^\$\{\s*customer_secrets\..+\s*\}$")

        # Paths where api_key is a parameter's schema definition or UI config, not an actual key
        skip_pattern = re.compile(r"\.params\.(schema|ui_schema)\.")

        def check_api_key(obj: Any, path: str = "") -> None:
            if isinstance(obj, dict):
                for key, value in obj.items():
                    new_path = f"{path}.{key}" if path else key

                    # Check if this is an api_key field with a non-null value
                    if key == "api_key" and value is not None:
                        # Skip schema definitions and UI configs
                        if skip_pattern.search(new_path):
                            continue

                        if not isinstance(value, str):
                            errors.append(
                                f"Invalid api_key at '{new_path}': expected string, got {type(value).__name__}"
                            )
                        elif (
                            new_path.startswith("upstream_access_config.")
                            and ".params." not in new_path
                            and value in DISPOSITION_VALUES
                        ):
                            # svcpass dispositions (#1198), not credentials:
                            # "" / "__strip__" => strip, "__forward__" => forward
                            # (host gate checked by validate_forward_disposition).
                            # Reserved: a sentinel elsewhere (e.g. a channel's
                            # params.testing_defaults api_key) still falls through to the
                            # secrets-format check below and is rejected.
                            pass
                        elif not seller_secrets_pattern.match(value) and not customer_secrets_pattern.match(value):
                            # Allow Jinja2 conditional templates (rendered at enrollment time)
                            if "{%" not in value:
                                errors.append(
                                    f"Invalid api_key at '{new_path}': API keys must use secrets reference format. "
                                    f"Got '{value}', expected format: "
                                    f"'${{ secrets.VAR_NAME }}' or '${{ customer_secrets.VAR_NAME }}'."
                                )

                    # Recurse into nested objects
                    if isinstance(value, dict | list):
                        check_api_key(value, new_path)

            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    if isinstance(item, dict | list):
                        check_api_key(item, f"{path}[{i}]")

        check_api_key(data)
        return errors

    @staticmethod
    def _is_trusted_forward_host(host: str) -> bool:
        """True if `host` is a platform host svcpass may be forwarded to."""
        host = host.lower()
        for suffix in TRUSTED_FORWARD_HOST_SUFFIXES:
            if host == suffix.lstrip(".") or host.endswith(suffix):
                return True
        return False

    @staticmethod
    def _static_host(base_url: str) -> str | None:
        """Return the literal host of `base_url`, or None when it can't be
        statically resolved (no host, or the host portion is templated)."""
        try:
            host = urlparse(base_url).hostname
        except (ValueError, TypeError):
            return None
        if not host or "${" in host or "{{" in host:
            return None
        return host.lower()

    def validate_forward_disposition(self, data: dict[str, Any]) -> list[str]:
        """Reject ``api_key == "__forward__"`` on an upstream interface whose
        ``base_url`` does not point at a trusted platform host (#1198).

        svcpass is a platform credential; forwarding it verbatim is only safe
        to internal/platform hosts. The gateway enforces the same allowlist at
        runtime, but catching it here fails the authoring/CI pass before deploy.
        A ``base_url`` that can't be statically resolved to a trusted host
        (templated/env-var host, external host, or missing) is a hard error.
        """
        errors: list[str] = []
        upstream = data.get("upstream_access_config")
        if not isinstance(upstream, dict):
            return errors
        allowed = ", ".join(TRUSTED_FORWARD_HOST_SUFFIXES)
        for iface_name, iface in upstream.items():
            if not isinstance(iface, dict) or iface.get("api_key") != "__forward__":
                continue
            path = f"upstream_access_config.{iface_name}"
            base_url = iface.get("base_url")
            host = self._static_host(base_url) if isinstance(base_url, str) else None
            if not host:
                errors.append(
                    f"Invalid '__forward__' disposition at '{path}.api_key': base_url "
                    f"({base_url!r}) has no statically resolvable host. svcpass is a "
                    f"platform credential and must not be forwarded to an arbitrary host; "
                    f"base_url must be a trusted platform host (allowed suffixes: {allowed})."
                )
            elif not self._is_trusted_forward_host(host):
                errors.append(
                    f"Invalid '__forward__' disposition at '{path}.api_key': base_url host "
                    f"'{host}' is not a trusted platform host (allowed suffixes: {allowed}). "
                    f"svcpass must never be forwarded to an external host."
                )
        return errors

    def validate_sigv4_disposition(self, data: dict[str, Any]) -> list[str]:
        """Require the SigV4 signing fields on a ``api_key == "__sigv4__"``
        channel (#1786).

        The gateway re-signs the finalized upstream request with AWS SigV4
        built from the channel's ``access_key`` / ``secret_key`` (usually
        secret references) and ``sigv4_service`` (the AWS service name in the
        credential scope, e.g. ``"bedrock"``); ``region`` defaults to
        us-east-1 and ``session_token`` is optional. A channel missing any
        required field fails every request at dispatch time with a 500 —
        catch it in the authoring/CI pass instead.
        """
        errors: list[str] = []
        upstream = data.get("upstream_access_config")
        if not isinstance(upstream, dict):
            return errors
        for iface_name, iface in upstream.items():
            if not isinstance(iface, dict) or iface.get("api_key") != "__sigv4__":
                continue
            path = f"upstream_access_config.{iface_name}"
            for field in SIGV4_REQUIRED_FIELDS:
                value = iface.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(
                        f"Invalid '__sigv4__' disposition at '{path}.api_key': missing or "
                        f"empty '{field}'. SigV4 signing requires "
                        f"{', '.join(SIGV4_REQUIRED_FIELDS)} on the channel "
                        f"(region is optional and defaults to us-east-1; "
                        f"session_token is optional)."
                    )
        return errors

    # Matches any ${ secrets... } or ${ customer_secrets... } expression —
    # capturing everything after the namespace so unsupported forms
    # (bracket lookup, whitespace-only, missing dot) also surface as errors
    # rather than being silently ignored.
    _SECRET_REF_RE = re.compile(r"\$\{\s*(secrets|customer_secrets)(.*?)\s*\}", re.DOTALL)
    # Valid tails after the namespace:
    #   .IDENTIFIER                    — required reference
    #   .IDENTIFIER ?? default         — optional reference with fallback
    # The default can contain anything (hyphens, slashes, spaces, URLs)
    # per the `??` syntax defined in backend/app/core/var_substitution.py.
    _SECRET_TAIL_RE = re.compile(
        r"^\.([A-Za-z_][A-Za-z0-9_]*)(?:\s*\?\?\s*(.*?))?$",
        re.DOTALL,
    )

    def _load_sibling_listing(self, offering_file: Path) -> dict[str, Any] | None:
        """Load the paired ``listing*.json`` from the same directory as the offering.

        Returns ``None`` if no sibling listing exists (e.g., offering-only
        fixture directories in tests).
        """
        # Match by schema field (legacy ``data/`` layout) or by filename (flat
        # ``specs/`` layout, where files carry no ``schema`` field and the
        # listing is named exactly ``listing.{json,toml}``).
        for candidate in sorted(offering_file.parent.glob("listing*.*")):
            if candidate.suffix not in (".json", ".toml"):
                continue
            data, _ = load_data_file(candidate)
            if not isinstance(data, dict):
                continue
            if data.get("schema") == "listing_v1" or candidate.stem == "listing":
                return data
        return None

    def validate_secret_references(
        self,
        data: dict[str, Any],
        schema_name: str,
        file_path: Path,
    ) -> list[str]:
        """Validate that every ``${ secrets.X }`` / ``${ customer_secrets.X }``
        in an offering's ``upstream_access_config`` resolves to a plain
        identifier after Jinja expansion.

        Only two forms are valid for the identifier ``X``:

        1. A literal name (letters, digits, underscores only) such as
           ``${ customer_secrets.ECHO_API_KEY }``.
        2. A Jinja substitution rendered with the context
           ``{ params, routing_vars, enrollment }``, where ``params`` is the
           channel's own ``params.testing_defaults``, ``routing_vars`` the
           listing's ``service_options.routing_vars``, and ``enrollment`` the
           intrinsic per-enrollment fields (``code`` / ``id``). After
           expansion, the result must also be a plain identifier.

        Anything else — bracket lookup (``customer_secrets[X]``),
        undefined Jinja variables, expressions that don't resolve to a
        simple identifier — is a validation error.

        Args:
            data: The offering data being validated.
            schema_name: The schema name (only ``offering_v1`` is checked).
            file_path: The offering file path, used to locate the sibling
                ``listing.json`` for the Jinja context.

        Returns:
            List of validation error messages.
        """
        if schema_name != "offering_v1":
            return []
        return self._secret_reference_errors(data, self._load_sibling_listing(file_path))

    def _secret_reference_errors(self, data: dict[str, Any], listing_data: dict[str, Any] | None) -> list[str]:
        """:meth:`validate_secret_references` for an offering and its listing in memory."""
        upstream = data.get("upstream_access_config") or {}
        if not isinstance(upstream, dict) or not upstream:
            return []

        # routing_vars come from the listing's service_options. Falling back
        # to {} keeps the validator useful even for an offering without a
        # listing — references to undefined variables will still surface as
        # clear errors from StrictUndefined.
        listing_data = listing_data or {}
        service_options = listing_data.get("service_options") or {}
        routing_vars = service_options.get("routing_vars") or {}

        jinja_env = Environment(undefined=StrictUndefined, autoescape=False)
        errors: list[str] = []

        for channel_name, channel in upstream.items():
            if not isinstance(channel, dict):
                continue
            # Rendering uses *actual* values (not just key presence) because the
            # rendered text feeds the secret-reference regex below: e.g.
            # `${ customer_secrets.{{ params.X }} }` must render to a real
            # identifier, so params are the channel's testing_defaults.
            params = channel.get("params")
            defaults = params.get("testing_defaults") if isinstance(params, dict) else None
            context = {
                "params": defaults if isinstance(defaults, dict) else {},
                "routing_vars": routing_vars,
                # Intrinsic per-enrollment fields, always available at render time
                # (#1202); placeholder values so `{{ enrollment.code }}` renders to a
                # non-empty identifier for the secret-reference regex below.
                "enrollment": {"code": "code", "id": "id"},
            }
            fields = [(f"upstream_access_config.{channel_name}.{key}", value) for key, value in channel.items()]
            servers = channel.get("servers")
            if isinstance(servers, list):
                for index, server in enumerate(servers):
                    if isinstance(server, dict):
                        prefix = f"upstream_access_config.{channel_name}.servers[{index}]"
                        fields.extend((f"{prefix}.{key}", value) for key, value in server.items())
            for field_path, value in fields:
                if not isinstance(value, str) or "${" not in value:
                    continue

                # First, Jinja-expand so `{{ params.X }}` style substitutions
                # land before we look at the secret references. Undefined
                # variables are a hard error — they'd silently render empty at
                # runtime, turning "${ customer_secrets.{{ params.X }} }" into
                # "${ customer_secrets. }" which fails the secret resolver's
                # regex and causes a confusing skip.
                try:
                    rendered = jinja_env.from_string(value).render(**context)
                except UndefinedError as exc:
                    errors.append(
                        f"{field_path}: Jinja expansion failed — {exc.message}. "
                        f"Give each parameter a value in the channel's params.testing_defaults, "
                        f"and each routing var one in the listing's service_options.routing_vars."
                    )
                    continue
                except TemplateSyntaxError as exc:
                    errors.append(f"{field_path}: Jinja syntax error in '{value}' — {exc.message}.")
                    continue

                # Then scan the rendered form for secret references. Every
                # `${ secrets... }` / `${ customer_secrets... }` must be
                # followed by `.IDENTIFIER` with nothing else; bracket
                # lookup, empty tails, hyphens, etc. are all rejected.
                for match in self._SECRET_REF_RE.finditer(rendered):
                    namespace, raw_tail = match.group(1), match.group(2)
                    tail = raw_tail.strip()
                    if not self._SECRET_TAIL_RE.match(tail):
                        errors.append(
                            f"{field_path}: secret reference '{match.group(0)}' "
                            f"is not a valid form. Use "
                            f"'${{ {namespace}.NAME }}' (required), "
                            f"'${{ {namespace}.NAME ?? default }}' (optional "
                            f"with fallback), or "
                            f"'${{ {namespace}.{{{{ params.KEY }}}} }}' "
                            f"(with KEY from the channel's "
                            f"params.testing_defaults). "
                            f"Bracket lookup and composite expressions are "
                            f"not supported."
                        )

        return errors

    def validate_llm_offering_metadata(self, data: dict[str, Any], schema_name: str) -> list[str]:
        """Validate that LLM offerings declare ``context_length`` and ``parameter_count``.

        Mirrors the backend's ingest-time check (see
        ``backend/app/workers/ingest_tasks.py``) so authors get the
        same feedback at ``usvc data validate`` time, before submitting
        to the platform.

        Rules for ``offering_v1`` files where ``service_type == "llm"``:

        - ``details.context_length`` and ``details.parameter_count`` must
          both be **present**. Missing keys are rejected.
        - Each value must be either a **positive integer** (real value)
          or **null** (asserted unknown).
        - Negative numbers, zero, booleans, and non-int types are rejected.

        Other service types (proxy, content, email, embedding, etc.) are
        unaffected.

        Args:
            data: The offering data being validated.
            schema_name: The schema name (only ``offering_v1`` is checked).

        Returns:
            List of validation error messages. Empty if the offering
            isn't an LLM service or all rules pass.
        """
        errors: list[str] = []

        if schema_name != "offering_v1":
            return errors
        if data.get("service_type") != "llm":
            return errors

        details = data.get("details")
        if not isinstance(details, dict):
            errors.append(
                "LLM offerings must declare 'details' with context_length and "
                "parameter_count. Use null for either if the value is unknown."
            )
            return errors

        for key in ("context_length", "parameter_count"):
            if key not in details:
                errors.append(
                    f"LLM offerings must declare details.{key}. Use null to assert that the value is unknown."
                )
                continue
            value = details[key]
            if value is None:
                continue
            # bool is a subclass of int in Python — reject explicitly so
            # ``parameter_count: True`` doesn't silently coerce to 1.
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                errors.append(f"details.{key} must be a positive integer or null, got {value!r}.")

        return errors

    def validate_offering_model_name(self, data: dict[str, Any], schema_name: str) -> list[str]:
        """Validate the optional ``details.model`` of an offering.

        ``details.model`` names the model an offering serves in a
        provider-neutral way, independent of the provider's own spelling
        in ``details.upstream_model``. A platform tier lists its members'
        names as the models it is currently served by (unitysvc#2567).

        Only the shape is checked (lowercase, no ``org/`` prefix); there
        is no registry of allowed names. Absent is fine.

        Args:
            data: The offering data being validated.
            schema_name: The schema name (only ``offering_v1`` is checked).

        Returns:
            List of validation error messages.
        """
        if schema_name != "offering_v1":
            return []
        details = data.get("details")
        if not isinstance(details, dict) or "model" not in details:
            return []
        value = details["model"]
        if isinstance(value, str) and CANONICAL_MODEL_RE.match(value):
            return []
        return [
            f"details.model must be a provider-neutral model name: lowercase letters, "
            f"digits and '.', '_', ':', '-', with no 'org/' prefix (e.g. 'glm-5.2'), got {value!r}."
        ]

    @staticmethod
    def _is_channel_less(offering: dict[str, Any] | None) -> bool:
        """Whether an offering declares no channel: an external app (#2873)."""
        return isinstance(offering, dict) and not offering.get("upstream_access_config")

    def validate_external_app_listing(self, data: dict[str, Any], offering: dict[str, Any] | None) -> list[str]:
        """A listing whose offering declares no channel is an external app
        (unitysvc/unitysvc#2873): reached at its homepage, so it has nothing to
        route to and no price (signup and payment happen at the seller's site)."""
        if not self._is_channel_less(offering):
            return []
        errors: list[str] = []
        if data.get("user_access_interfaces"):
            errors.append(
                "An external app (its offering declares no channel) has nothing to route to: "
                "remove user_access_interfaces"
            )
        if data.get("list_price"):
            errors.append(
                "An external app (its offering declares no channel) is paid for at the seller's site: remove list_price"
            )
        return errors

    def validate_connectivity_test_exists(
        self, data: dict[str, Any], schema_name: str, *, offering: dict[str, Any] | None = None
    ) -> list[str]:
        """Validate that at least one connectivity_test document exists.

        For listing_v1 data, the documents section must contain at least one
        document with category "connectivity_test". This is required for
        automated service testing during the publish workflow. An external
        app (its offering declares no channel) needs none: the platform checks
        its product links instead (unitysvc/unitysvc#2873).

        Args:
            data: The data to validate
            schema_name: The schema name (e.g., 'listing_v1')

        Returns:
            List of validation error messages
        """
        errors: list[str] = []

        # Only validate listing_v1 schema
        if schema_name != "listing_v1" or self._is_channel_less(offering):
            return errors

        documents = data.get("documents")
        if not documents or not isinstance(documents, dict):
            errors.append(
                "Listing must contain at least one document with category 'connectivity_test'. "
                "No documents section found."
            )
            return errors

        has_connectivity_test = any(
            isinstance(doc, dict) and doc.get("category") == "connectivity_test" for doc in documents.values()
        )

        if not has_connectivity_test:
            errors.append(
                "Listing must contain at least one document with category 'connectivity_test'. "
                "Add a connectivity test script to verify the service is reachable."
            )

        return errors

    def validate_service_options_keys(self, data: dict[str, Any], schema_name: str) -> list[str]:
        """Validate service_options keys and value types for listing_v1 files.

        Args:
            data: The data to validate
            schema_name: The schema name (e.g., 'listing_v1')

        Returns:
            List of validation error messages
        """
        if schema_name != "listing_v1":
            return []

        service_options = data.get("service_options")
        if not service_options or not isinstance(service_options, dict):
            return []

        from unitysvc_core.models.validators import validate_service_options

        return validate_service_options(service_options)

    def validate_name_consistency(self, data: dict[str, Any], file_path: Path, schema_name: str) -> list[str]:
        """Validate that the name field matches the directory name.

        Currently only validates provider_v1 files. The provider directory name
        should match the provider name for organizational clarity.

        Note: offering_v1 files are not validated because the service name is
        always read from the offering's name field, not inferred from the directory.
        """
        errors: list[str] = []

        # Only validate files with a 'name' field
        if "name" not in data:
            return errors

        name_value = data["name"]
        if not isinstance(name_value, str):
            return errors

        # Only validate provider files - directory name should match provider name
        if schema_name == "provider_v1":
            directory_name = file_path.parent.name
            if self._normalize_name(name_value) != self._normalize_name(directory_name):
                errors.append(
                    f"Provider name '{name_value}' does not match directory name '{directory_name}'. "
                    f"Expected directory name to match normalized provider name: '{self._normalize_name(name_value)}'"
                )

        return errors

    def _normalize_name(self, name: str) -> str:
        """Normalize a name for directory comparison."""
        # Convert to lowercase and replace spaces/special chars with hyphens
        normalized = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower())
        # Remove leading/trailing hyphens
        normalized = normalized.strip("-")
        return normalized

    def validate_with_pydantic_model(self, data: dict[str, Any], schema_name: str) -> list[str]:
        """
        Validate data using Pydantic models for additional validation rules.

        This complements JSON schema validation with Pydantic field validators
        like name format validation.

        Args:
            data: The data to validate
            schema_name: The schema name (e.g., 'provider_v1', 'seller_v1')

        Returns:
            List of validation error messages
        """
        from pydantic import BaseModel

        from unitysvc_core.models import ListingV1, OfferingV1, ProviderV1

        errors: list[str] = []

        # Map schema names to Pydantic model classes
        model_map: dict[str, type[BaseModel]] = {
            "provider_v1": ProviderV1,
            "offering_v1": OfferingV1,
            "listing_v1": ListingV1,
        }

        if schema_name not in model_map:
            return errors  # No Pydantic model for this schema

        model_class = model_map[schema_name]

        try:
            # Validate using the Pydantic model
            model_class.model_validate(data)

        except Exception as e:
            # Extract meaningful error message from Pydantic ValidationError
            error_msg = str(e)
            # Pydantic errors can be verbose, try to extract just the relevant part
            if "validation error" in error_msg.lower():
                errors.append(f"Pydantic validation error: {error_msg}")
            else:
                errors.append(error_msg)

        return errors

    def load_data_file(self, file_path: Path) -> tuple[dict[str, Any] | None, list[str]]:
        """Load data from JSON or TOML file."""
        errors: list[str] = []

        try:
            data, _file_format = load_data_file(file_path)
            return data, errors
        except Exception as e:
            format_name = {".json": "JSON", ".toml": "TOML"}.get(file_path.suffix, "data")
            return None, [f"Failed to parse {format_name}: {e}"]

    def validate_data_file(
        self,
        file_path: Path,
        schema_name: str | None = None,
        check_name_consistency: bool = True,
    ) -> tuple[bool, list[str]]:
        """Validate a single data file (JSON or TOML).

        Loads the file, and the sibling files its cross-file checks need, then
        runs the same checks :meth:`validate_service` runs in memory.

        Args:
            file_path: File to validate.
            schema_name: Schema to validate against. When ``None`` (the legacy
                ``data/`` layout), the schema is read from the file's ``schema``
                field, which is required. When provided (the flat ``specs/``
                layout, where the *filename* is the discriminator), the file
                need not — and by convention does not — carry a ``schema`` field.
            check_name_consistency: Whether to enforce that a provider's ``name``
                matches its directory name. Off for the flat ``specs/`` layout,
                where ``provider.json`` lives inside the per-service folder
                rather than a provider-named directory.
        """
        data, load_errors = self.load_data_file(file_path)
        if load_errors:
            return False, load_errors

        # data could be None if loading failed
        if data is None:
            return False, ["Failed to load data file"]

        if schema_name is None:
            # The file's type is its FILENAME (provider.json, offering.json, …).
            schema_name = schema_for_path(file_path)
            if schema_name is None:
                return False, [
                    f"Unrecognized data file name {file_path.name!r}: expected one of "
                    f"{', '.join(sorted(SCHEMA_TO_STEM.values()))} (.json/.toml)"
                ]

        listing: dict[str, Any] | None = None
        offering: dict[str, Any] | None = None
        provider_name: str | None = None
        platform_service = False
        if schema_name == "offering_v1":
            listing = self._load_sibling_listing(file_path)
        elif schema_name == "listing_v1":
            offering = self._load_sibling_offering(file_path)
            provider_name = self._resolve_provider_name(file_path)
            from .models.validators import PLATFORM_SERVICES_DIRNAME

            # A listing under platform-services/ is a platform service: its
            # address is /p, which every other listing is refused.
            try:
                parts = file_path.resolve().relative_to(self.data_dir.resolve()).parts
            except ValueError:
                parts = file_path.parts
            platform_service = PLATFORM_SERVICES_DIRNAME in parts[:-1]

        errors = self._validate_data(
            data,
            schema_name,
            service_dir=file_path.parent,
            listing=listing,
            offering=offering,
            provider_name=provider_name,
            platform_service=platform_service,
            check_name_consistency=check_name_consistency,
        )
        return len(errors) == 0, errors

    def validate_service(
        self,
        *,
        provider: dict[str, Any] | None = None,
        offering: dict[str, Any] | None = None,
        listing: dict[str, Any] | None = None,
        service_dir: Path | None = None,
        platform_service: bool = False,
    ) -> dict[str, list[str]]:
        """Validate one service's files, already loaded, without reading them again.

        Each given file is validated against its schema with the others as its
        context: the listing supplies the offering's ``routing_vars``, the
        offering and both files' documents decide required-parameter usage, and
        the provider's ``name`` the listing's namespace. Load the files with
        :func:`unitysvc_core.utils.load_data_file` (overrides and presets
        applied) first.

        No state is shared between calls, so services can be validated in
        parallel with one validator.

        Args:
            provider, offering, listing: The service's files as dicts (``None``
                to skip one).
            service_dir: The folder holding the files, used to resolve relative
                file references and read ``.j2`` document sources. Without it,
                relative references are checked only for their form, and only
                documents already carrying ``file_content`` count as content.
            platform_service: The listing is a platform service (addressed at
                ``/p``), as a listing under ``platform-services/`` is.

        Returns:
            ``{"provider": [...], "offering": [...], "listing": [...]}`` errors
            for each file given (empty lists when valid).
        """
        provider_name = provider.get("name") if isinstance(provider, dict) else None
        results: dict[str, list[str]] = {}
        for kind, data in (("provider", provider), ("offering", offering), ("listing", listing)):
            if data is None:
                continue
            results[kind] = self._validate_data(
                data,
                f"{kind}_v1",
                service_dir=service_dir,
                listing=listing,
                offering=offering,
                provider_name=provider_name if isinstance(provider_name, str) else None,
                platform_service=platform_service,
                check_name_consistency=False,
            )
        return results

    def _validate_data(
        self,
        data: dict[str, Any],
        schema_name: str,
        *,
        service_dir: Path | None,
        listing: dict[str, Any] | None,
        offering: dict[str, Any] | None,
        provider_name: str | None,
        platform_service: bool,
        check_name_consistency: bool,
    ) -> list[str]:
        """Every check for one file's data, given its service as context."""
        errors: list[str] = []

        # Check if schema exists
        if schema_name not in self.schemas:
            return [f"Schema '{schema_name}' not found in schema directory"]

        # Validate against schema with format checking enabled
        try:
            for error in self._schema_validators[schema_name].iter_errors(data):
                errors.append(f"Schema validation error: {error.message}")
                if error.absolute_path:
                    errors.append(f"  Path: {'.'.join(str(p) for p in error.absolute_path)}")
        except Exception as e:
            errors.append(f"Validation error: {e}")

        # Also validate using Pydantic models for additional validation rules
        pydantic_errors = self.validate_with_pydantic_model(data, schema_name)
        errors.extend(pydantic_errors)

        # Validate file references in Union[str, HttpUrl] fields and file_path fields
        errors.extend(self._file_reference_errors(data, service_dir, self._union_fields[schema_name]))

        # Validate name consistency with directory name (legacy layout only)
        if check_name_consistency and service_dir is not None:
            errors.extend(self.validate_name_consistency(data, service_dir / "_", schema_name))

        # Validate api_key fields use secrets format
        api_key_errors = self.validate_api_key_secrets(data)
        errors.extend(api_key_errors)

        # Validate the __forward__ svcpass disposition only targets trusted hosts
        errors.extend(self.validate_forward_disposition(data))

        # Validate the __sigv4__ disposition carries its signing fields
        errors.extend(self.validate_sigv4_disposition(data))

        # Validate every ${ secrets.X } / ${ customer_secrets.X } in an
        # offering's upstream_access_config resolves to a plain identifier.
        if schema_name == "offering_v1":
            errors.extend(self._secret_reference_errors(data, listing))

        # Validate LLM offerings declare context_length and parameter_count
        # (mirrors the backend's ingest-time check).
        llm_metadata_errors = self.validate_llm_offering_metadata(data, schema_name)
        errors.extend(llm_metadata_errors)

        # Validate the optional provider-neutral details.model (offering_v1 only)
        errors.extend(self.validate_offering_model_name(data, schema_name))

        # Validate MCP offerings declare an mcp upstream channel (offering_v1
        # only); the customer-facing gateway interface is checked below with
        # the other listing_v1 interface rules.
        if schema_name == "offering_v1":
            from .models.validators import validate_mcp_offering

            errors.extend(validate_mcp_offering(data))

            # Validate what this service exports and what each channel requests
            # from customers (offering_v1 only); backend ingest runs the same checks
            # (unitysvc/unitysvc#2720).
            from .grants import parse_exports, validate_channel_access_requests

            try:
                parse_exports("exports", data.get("exports"))
                validate_channel_access_requests(data.get("upstream_access_config"), self.exports_resolver)
            except ValueError as exc:
                errors.append(str(exc))

        # Validate the enrollment parameters each channel declares (offering_v1
        # only, unitysvc/unitysvc#2726).
        if schema_name == "offering_v1":
            errors.extend(validate_channel_parameters(data))

        # Validate service_options keys and value types (listing_v1 only)
        service_options_errors = self.validate_service_options_keys(data, schema_name)
        errors.extend(service_options_errors)

        # Validate at least one connectivity_test document exists (listing_v1 only)
        connectivity_errors = self.validate_connectivity_test_exists(data, schema_name, offering=offering)
        errors.extend(connectivity_errors)

        # Validate S3, SMTP, MCP, and API-gateway interfaces (listing_v1 only)
        if schema_name == "listing_v1":
            from .models.validators import (
                validate_access_interface_names,
                validate_listing_gateway_base_urls,
                validate_listing_interfaces_are_canonical,
                validate_listing_jinja_var_references,
                validate_listing_mcp_base_urls,
                validate_listing_s3_base_urls,
                validate_listing_smtp_base_urls,
            )

            uai = data.get("user_access_interfaces")
            errors.extend(validate_access_interface_names(uai))
            errors.extend(validate_listing_gateway_base_urls(uai, platform_service=platform_service))
            errors.extend(validate_listing_s3_base_urls(uai))
            errors.extend(validate_listing_smtp_base_urls(uai))
            errors.extend(validate_listing_mcp_base_urls(uai))
            errors.extend(validate_listing_jinja_var_references(data))
            errors.extend(validate_listing_interfaces_are_canonical(uai))
            errors.extend(self.validate_external_app_listing(data, offering))
            if offering is not None:
                errors.extend(
                    validate_required_parameter_usage(
                        self._with_template_documents(data, service_dir),
                        self._with_template_documents(offering, service_dir),
                    )
                )
            errors.extend(self._namespace_errors(data, provider_name))

        return errors

    def _load_sibling_offering(self, listing_file: Path) -> dict[str, Any] | None:
        """Load the paired offering from the same directory as the listing.

        Read after overrides/presets, as ``load_data_file`` applies them.
        Returns ``None`` when there is none (a standalone listing).
        """
        for candidate in sorted(listing_file.parent.glob("offering*.*")):
            if candidate.suffix not in (".json", ".toml") or ".override." in candidate.name:
                continue
            try:
                offering, _ = load_data_file(candidate)
            except Exception:
                return None
            if isinstance(offering, dict) and (offering.get("schema") == "offering_v1" or candidate.stem == "offering"):
                return offering
        return None

    def validate_required_parameter_usage(self, listing: dict[str, Any], file_path: Path) -> list[str]:
        """Check the listing against its paired offering, after overrides/presets.

        A channel's required parameters may be read by document content, which
        lives partly on the listing, so the check needs both files. Standalone
        listings without an offering cannot be checked; full service uploads are
        always checked by backend ingest too.
        """
        offering = self._load_sibling_offering(file_path)
        if offering is None:
            return []
        return validate_required_parameter_usage(
            self._with_template_documents(listing, file_path.parent),
            self._with_template_documents(offering, file_path.parent),
        )

    @staticmethod
    def _with_template_documents(data: dict[str, Any], base_dir: Path | None) -> dict[str, Any]:
        """``data`` with each ``.j2`` document's template source inlined as
        ``file_content``, as the seller SDK uploads it — so the seller check
        sees the same document content backend ingest does. Without a
        ``base_dir`` only documents already carrying ``file_content`` count."""
        documents = data.get("documents")
        if not isinstance(documents, dict) or base_dir is None:
            return data
        inlined: dict[str, Any] = {}
        for title, doc in documents.items():
            if isinstance(doc, dict) and "file_content" not in doc:
                rel = doc.get("file_path")
                if isinstance(rel, str) and rel.endswith(".j2"):
                    path = Path(rel) if Path(rel).is_absolute() else base_dir / rel
                    try:
                        doc = {**doc, "file_content": path.read_text(encoding="utf-8")}
                    except OSError:
                        pass  # a missing file is reported by the file-reference checks
            inlined[title] = doc
        return {**data, "documents": inlined}

    def _resolve_provider_name(self, file_path: Path) -> str | None:
        """Find the provider name (``provider_v1.name``) governing a listing file.

        Walks up from the listing file and returns the ``name`` of the first
        ``provider_v1`` data file found in an ancestor directory. Returns
        ``None`` when none is found — the first-segment check is then skipped
        (the backend re-checks at registration where the provider is known).
        """
        # Layout is data/<provider>/services/<svc>/listing.*, so the provider
        # file is ~3 levels up; cap the climb to stay cheap.
        for ancestor in list(file_path.parents)[:6]:
            for cand in sorted(ancestor.glob("*")):
                if not cand.is_file() or cand.suffix not in (".json", ".toml") or ".override." in cand.name:
                    continue
                try:
                    cand_data, _ = load_data_file(cand)
                except Exception:
                    continue
                if cand.stem == "provider" and isinstance(cand_data, dict):
                    name = cand_data.get("name")
                    return name if isinstance(name, str) else None
        return None

    def validate_listing_name_namespace(self, data: dict[str, Any], file_path: Path) -> list[str]:
        """Enforce the namespaced-first-segment rule on ``listing.name`` (#1138).

        ``service_name = listing.name``. Top-level vs namespaced is purely
        syntactic (presence of ``/`` in the name part before any ``@variant``):

        - **Namespaced** (has ``/``): the first segment must equal the governing
          ``provider_v1.name``. This stops a seller registering
          ``otherprovider/...`` under their own provider.
        - **Bare** (no ``/``): a *top-level* request — not rejected here; the
          backend admin-gates it against the reservation allowlist.

        Per-segment grammar of the name itself is validated by the Pydantic
        model (``validate_service_identifier``).
        """
        return self._namespace_errors(data, self._resolve_provider_name(file_path))

    @staticmethod
    def _namespace_errors(data: dict[str, Any], provider_name: str | None) -> list[str]:
        """:meth:`validate_listing_name_namespace` given the governing provider's name."""
        errors: list[str] = []

        name = data.get("name")
        if not isinstance(name, str) or not name:
            return errors

        name_part = name.split("@", 1)[0]
        if "/" not in name_part:
            # Bare top-level name — defer authorization to the backend.
            return errors

        first_segment = name_part.split("/", 1)[0]
        if provider_name is not None and first_segment != provider_name:
            errors.append(
                f"Listing name '{name}' is namespaced, so its first segment must be the provider "
                f"slug '{provider_name}', not '{first_segment}'. Use '{provider_name}/...' for a "
                f"provider service, or a bare name (no '/') for an admin-approved top-level service."
            )

        return errors

    def validate_jinja2_file(self, file_path: Path) -> tuple[bool, list[str]]:
        """Validate a file with Jinja2 template syntax.

        This validates any file ending with .j2 extension, including:
        - .md.j2 (Jinja2 markdown templates)
        - .py.j2 (Jinja2 Python code example templates)
        - .js.j2 (Jinja2 JavaScript code example templates)
        - .sh.j2 (Jinja2 shell script templates)
        """
        errors: list[str] = []

        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            if not content.strip():
                return True, []

            # Validate Jinja2 syntax
            try:
                env = Environment()
                env.parse(content)
            except TemplateSyntaxError as e:
                errors.append(f"Jinja2 syntax error: {e.message} at line {e.lineno}")
            except Exception as e:
                errors.append(f"Jinja2 validation error: {e}")

            return len(errors) == 0, errors
        except Exception as e:
            return False, [f"Failed to read template file: {e}"]

    def validate_all(self) -> dict[str, tuple[bool, list[str]]]:
        """Validate all files in the data directory."""
        results: dict[str, tuple[bool, list[str]]] = {}

        if not self.data_dir.exists():
            return results

        # Find all data and MD files recursively, skipping hidden directories
        for file_path in self.data_dir.rglob("*"):
            # Skip hidden directories (those starting with .) — relative to the
            # data dir, so a catalog that itself lives under one (~/.cache/repo)
            # still validates instead of silently reporting zero files.
            if is_hidden_path(file_path, self.data_dir):
                continue

            # Skip schema directory, docs directory, and pyproject.toml (not data files)
            if "schema" in file_path.parts or "docs" in file_path.parts or file_path.name == "pyproject.toml":
                continue

            # Check if file should be validated
            # Only .j2 files (Jinja2 templates) are validated for Jinja2 syntax
            is_template = file_path.name.endswith(".j2")
            is_data_file = file_path.suffix in [".json", ".toml"]

            # Skip override files — they are merged with base files by load_data_file()
            is_override_file = ".override." in file_path.name

            if file_path.is_file() and (is_data_file or is_template) and not is_override_file:
                relative_path = file_path.relative_to(self.data_dir)

                if is_data_file:
                    if not self._is_recognized_data_file(file_path):
                        # Non-service file (config example, ad-hoc TOML, etc.) —
                        # warn but don't fail the run.
                        continue
                    is_valid, errors = self.validate_data_file(file_path)
                elif is_template:
                    is_valid, errors = self.validate_jinja2_file(file_path)
                else:
                    continue

                results[str(relative_path)] = (is_valid, errors)

        return results

    def _is_recognized_data_file(self, file_path: Path) -> bool:
        """Return True if ``file_path`` parses and declares a known ``schema``.

        Files that aren't service data (Docker configs, ``pyproject.toml``,
        ``*.example.toml`` snippets, etc.) emit
        :class:`UnrecognizedDataFileWarning` and are skipped by
        :meth:`validate_all`.  Parse failures are *not* skipped — they
        still flow through :meth:`validate_data_file` so the user sees
        the syntax error.
        """
        # A data file is recognized by its FILENAME (provider.json, offering.json,
        # …), not an in-file ``schema`` field. Non-spec files (``service.json``
        # provenance, ``package-lock.json``, ad-hoc TOML) are skipped.
        schema_name = schema_for_path(file_path)
        if schema_name is None:
            warnings.warn(
                f"Skipping {file_path}: not a recognized spec file ({', '.join(sorted(SCHEMA_TO_STEM.values()))})",
                UnrecognizedDataFileWarning,
                stacklevel=3,
            )
            return False
        if schema_name not in self.schemas:
            warnings.warn(
                f"Skipping {file_path}: no schema {schema_name!r} in the schema directory",
                UnrecognizedDataFileWarning,
                stacklevel=3,
            )
            return False
        return True
