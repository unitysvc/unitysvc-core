"""Data validation module for unitysvc_core."""

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jinja2 import Environment, StrictUndefined, TemplateSyntaxError, UndefinedError
from jsonschema.validators import Draft7Validator

from .utils import load_data_file


class DataValidationError(Exception):
    """Exception raised when data validation fails."""

    pass


class DataValidator:
    """Validates data files against JSON schemas."""

    def __init__(self, data_dir: Path, schema_dir: Path):
        self.data_dir = data_dir
        self.schema_dir = schema_dir
        self.schemas: dict[str, dict[str, Any]] = {}
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
                    self.schemas[schema_name] = schema
            except Exception as e:
                print(f"Error loading schema {schema_file}: {e}")

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
                        else:
                            referenced_file = file_path.parent / value
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
                        # All file_path fields must use relative paths
                        elif Path(value).is_absolute():
                            errors.append(
                                f"File path '{value}' in field '{new_path}' "
                                f"must be a relative path, not an absolute path"
                            )
                        # Check that the file exists
                        else:
                            referenced_file = file_path.parent / value
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

        check_field(data, str(file_path))
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
        - upstream_access_config.<name>.api_key
        - service_options.ops_testing_parameters.api_key

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

        # Paths where api_key is a schema definition or UI config, not an actual key
        skip_prefixes = ("user_parameters_schema.properties.", "user_parameters_ui_schema.")

        def check_api_key(obj: Any, path: str = "") -> None:
            if isinstance(obj, dict):
                for key, value in obj.items():
                    new_path = f"{path}.{key}" if path else key

                    # Check if this is an api_key field with a non-null value
                    if key == "api_key" and value is not None:
                        # Skip schema definitions and UI configs
                        if any(new_path.startswith(p) for p in skip_prefixes):
                            continue

                        if not isinstance(value, str):
                            errors.append(
                                f"Invalid api_key at '{new_path}': expected string, got {type(value).__name__}"
                            )
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
        for candidate in sorted(offering_file.parent.glob("listing*.json")):
            data, _ = load_data_file(candidate)
            if isinstance(data, dict) and data.get("schema") == "listing_v1":
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
        2. A Jinja substitution that references the listing's
           ``service_options``, rendered with the context
           ``{ params, routing_vars, enrollment_vars }`` — where each
           namespace maps to the corresponding ``service_options`` key
           (``ops_testing_parameters``, ``routing_vars``,
           ``enrollment_vars``). After expansion, the result must also be
           a plain identifier.

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

        upstream = data.get("upstream_access_config") or {}
        if not isinstance(upstream, dict) or not upstream:
            return []

        # Build the Jinja context from the sibling listing's service_options.
        # Falling back to {} keeps the validator useful even for fixture
        # directories that only have an offering — references to undefined
        # variables will still surface as clear errors from StrictUndefined.
        listing_data = self._load_sibling_listing(file_path) or {}
        service_options = listing_data.get("service_options") or {}
        context = {
            "params": service_options.get("ops_testing_parameters") or {},
            "routing_vars": service_options.get("routing_vars") or {},
            "enrollment_vars": service_options.get("enrollment_vars") or {},
        }

        jinja_env = Environment(undefined=StrictUndefined, autoescape=False)
        errors: list[str] = []

        for iface_name, iface in upstream.items():
            if not isinstance(iface, dict):
                continue
            for field, value in iface.items():
                if not isinstance(value, str) or "${" not in value:
                    continue
                field_path = f"upstream_access_config.{iface_name}.{field}"

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
                        f"Check that the listing's service_options provides the "
                        f"referenced namespace "
                        f"(params / routing_vars / enrollment_vars)."
                    )
                    continue
                except TemplateSyntaxError as exc:
                    errors.append(
                        f"{field_path}: Jinja syntax error in '{value}' — {exc.message}."
                    )
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
                            f"(with KEY from "
                            f"service_options.ops_testing_parameters). "
                            f"Bracket lookup and composite expressions are "
                            f"not supported."
                        )

        return errors

    def validate_required_parameter_defaults(self, data: dict[str, Any], schema_name: str) -> list[str]:
        """Validate that required user parameters have default values.

        For listing_v1 data, if user_parameters_schema.required lists parameters,
        each of those parameters must have a corresponding default value in
        service_options.ops_testing_parameters.

        Args:
            data: The data to validate
            schema_name: The schema name (e.g., 'listing_v1')

        Returns:
            List of validation error messages
        """
        errors: list[str] = []

        # Only validate listing_v1 schema
        if schema_name != "listing_v1":
            return errors

        # Get user_parameters_schema
        user_parameters_schema = data.get("user_parameters_schema")
        if not user_parameters_schema or not isinstance(user_parameters_schema, dict):
            return errors

        # Get required parameters from user_parameters_schema
        required_params = user_parameters_schema.get("required")
        if not required_params or not isinstance(required_params, list):
            return errors

        # No required parameters means nothing to validate
        if len(required_params) == 0:
            return errors

        # Get service_options.ops_testing_parameters
        service_options = data.get("service_options")
        if not service_options or not isinstance(service_options, dict):
            errors.append(
                f"user_parameters_schema has required parameters {required_params}, "
                f"but service_options is missing. "
                f"Add service_options.ops_testing_parameters with defaults for required parameters."
            )
            return errors

        ops_testing_parameters = service_options.get("ops_testing_parameters")
        if not ops_testing_parameters or not isinstance(ops_testing_parameters, dict):
            errors.append(
                f"user_parameters_schema has required parameters {required_params}, "
                f"but service_options.ops_testing_parameters is missing. "
                f"Add default values for all required parameters."
            )
            return errors

        # Check each required parameter has a default
        missing_defaults = []
        for param in required_params:
            if param not in ops_testing_parameters:
                missing_defaults.append(param)

        if missing_defaults:
            errors.append(
                f"Required parameters missing default values in service_options.ops_testing_parameters: "
                f"{missing_defaults}. Each required parameter must have a default value."
            )

        return errors

    def validate_connectivity_test_exists(self, data: dict[str, Any], schema_name: str) -> list[str]:
        """Validate that at least one connectivity_test document exists.

        For listing_v1 data, the documents section must contain at least one
        document with category "connectivity_test". This is required for
        automated service testing during the publish workflow.

        Args:
            data: The data to validate
            schema_name: The schema name (e.g., 'listing_v1')

        Returns:
            List of validation error messages
        """
        errors: list[str] = []

        # Only validate listing_v1 schema
        if schema_name != "listing_v1":
            return errors

        documents = data.get("documents")
        if not documents or not isinstance(documents, dict):
            errors.append(
                "Listing must contain at least one document with category 'connectivity_test'. "
                "No documents section found."
            )
            return errors

        has_connectivity_test = any(
            isinstance(doc, dict) and doc.get("category") == "connectivity_test"
            for doc in documents.values()
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

    def validate_data_file(self, file_path: Path) -> tuple[bool, list[str]]:
        """Validate a single data file (JSON or TOML)."""
        errors: list[str] = []

        data, load_errors = self.load_data_file(file_path)
        if load_errors:
            return False, load_errors

        # data could be None if loading failed
        if data is None:
            return False, ["Failed to load data file"]

        # Check for schema field
        if "schema" not in data:
            return False, ["Missing 'schema' field in data file"]

        schema_name = data["schema"]

        # Check if schema exists
        if schema_name not in self.schemas:
            return False, [f"Schema '{schema_name}' not found in schema directory"]

        schema = self.schemas[schema_name]

        # Validate against schema with format checking enabled
        try:
            validator = Draft7Validator(schema, format_checker=Draft7Validator.FORMAT_CHECKER)
            validator.check_schema(schema)  # Validate the schema itself
            validation_errors = list(validator.iter_errors(data))
            for error in validation_errors:
                errors.append(f"Schema validation error: {error.message}")
                if error.absolute_path:
                    errors.append(f"  Path: {'.'.join(str(p) for p in error.absolute_path)}")
        except Exception as e:
            errors.append(f"Validation error: {e}")

        # Also validate using Pydantic models for additional validation rules
        pydantic_errors = self.validate_with_pydantic_model(data, schema_name)
        errors.extend(pydantic_errors)

        # Find Union[str, HttpUrl] fields and validate file references
        union_fields = self.find_union_fields(schema)
        file_ref_errors = self.validate_file_references(data, file_path, union_fields)
        errors.extend(file_ref_errors)

        # Validate name consistency with directory name
        name_errors = self.validate_name_consistency(data, file_path, schema_name)
        errors.extend(name_errors)

        # Validate duplicate document titles
        dup_title_errors = self.validate_duplicate_document_titles(data, file_path)
        errors.extend(dup_title_errors)

        # Validate required parameters have defaults (listing_v1 only)
        required_param_errors = self.validate_required_parameter_defaults(data, schema_name)
        errors.extend(required_param_errors)

        # Validate api_key fields use secrets format
        api_key_errors = self.validate_api_key_secrets(data)
        errors.extend(api_key_errors)

        # Validate every ${ secrets.X } / ${ customer_secrets.X } in an
        # offering's upstream_access_config resolves to a plain identifier.
        secret_ref_errors = self.validate_secret_references(data, schema_name, file_path)
        errors.extend(secret_ref_errors)

        # Validate service_options keys and value types (listing_v1 only)
        service_options_errors = self.validate_service_options_keys(data, schema_name)
        errors.extend(service_options_errors)

        # Validate at least one connectivity_test document exists (listing_v1 only)
        connectivity_errors = self.validate_connectivity_test_exists(data, schema_name)
        errors.extend(connectivity_errors)

        return len(errors) == 0, errors

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
            # Skip hidden directories (those starting with .)
            if any(part.startswith(".") for part in file_path.parts):
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
                    is_valid, errors = self.validate_data_file(file_path)
                elif is_template:
                    is_valid, errors = self.validate_jinja2_file(file_path)
                else:
                    continue

                results[str(relative_path)] = (is_valid, errors)

        return results

