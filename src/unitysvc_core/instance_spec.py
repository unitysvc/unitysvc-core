"""Instance specs: validating a seller's Compose file and compiling it (unitysvc/unitysvc-core#105).

An *instance spec* is the file a ``container`` channel runs, stored as an
``instance_spec`` Document. Compose is the only format. Compose has many
features that mean nothing in a cluster or change its isolation, so the policy
is an allowlist: every key is *honored*, *ignored with a warning*, *ignored with
a loud warning*, or *rejected*, and a key in none of those is rejected, so a new
Compose feature is never silently accepted.

Two shared entry points, used by the seller CLI (``specs validate``) and by
backend ingest:

* :func:`validate_instance_spec` returns an :class:`InstanceSpecReport` of
  errors and warnings;
* :func:`compile_instance_spec` turns a valid spec plus the channel's
  ``resources`` into the :class:`~unitysvc_core.models.manifest.NormalizedManifest`
  the agent receives, recording what was dropped. That core compiles at all is
  the *proposal* of #105 (open question 3), not a decision.

The file is parsed with ``yaml.safe_load`` (no code execution; duplicate keys
refused). Nothing here pulls an image or contacts a cluster.
"""

from __future__ import annotations

import ipaddress
import math
import posixpath
import re
import shlex
from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field

from .models.channel import ContainerChannel
from .models.container import DNS_LABEL_RE, ResourceSet, check_resolved
from .models.manifest import (
    ManifestHealth,
    ManifestResources,
    ManifestScratch,
    ManifestService,
    NormalizedManifest,
)

DEFAULT_FORMAT = "compose"
SUPPORTED_FORMATS = ("compose",)
MAX_SPEC_BYTES = 256 * 1024

# --- the policy -------------------------------------------------------------------------------

# Honored: mapped to the manifest (or, where the agent has no field yet, recorded in `dropped`).
_HONORED = frozenset(
    {
        "image",
        "command",
        "entrypoint",
        "environment",
        "ports",
        "healthcheck",
        "working_dir",
        "user",
        "read_only",
        "volumes",  # named volumes only; scratch space (loud warning)
        "depends_on",  # ignored, loudly: no start ordering
    }
)
# Ignored with a warning: meaningless in a cluster, or decided elsewhere.
_IGNORED = frozenset({"container_name", "restart", "labels", "logging", "networks", "expose", "profiles", "links"})
# Rejected, with why (anything not listed anywhere is rejected too, as "not supported").
_REJECTED: dict[str, str] = {
    "build": "an image is built elsewhere; give an `image`",
    "privileged": "it removes the isolation instances run in",
    "cap_add": "it changes the container's isolation",
    "devices": "host devices are not available in a cluster",
    "network_mode": "it changes the network isolation of the instance",
    "pid": "it changes the isolation of the instance",
    "ipc": "it changes the isolation of the instance",
    "security_opt": "it changes the container's isolation",
    "sysctls": "it changes the container's isolation",
    "volumes_from": "volumes cannot be shared between services",
    "runtime": "the runtime is chosen by the platform",
    "extra_hosts": "it cannot work in a cluster",
    "extends": "write the whole service in this file",
    "include": "write the whole file in one piece",
    "secrets": "secrets are bound by the platform, not by a Compose `secrets` section",
    "configs": "configs are not supported; put the value in `environment`",
    "mem_limit": "resources come from the channel's `resources`",
    "cpus": "resources come from the channel's `resources`",
}
_TOP_IGNORED = frozenset({"name", "version", "networks"})
_TOP_ALLOWED = frozenset({"services", "volumes"}) | _TOP_IGNORED
_HEALTHCHECK_KEYS = frozenset({"test", "interval", "timeout", "retries", "start_period", "start_interval", "disable"})
_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
_ENV_NAME_RE = re.compile(r"^[-._a-zA-Z][-._a-zA-Z0-9]*$")
_INTERPOLATION_RE = re.compile(r"\$(\{[^}]*\}|[A-Za-z_])")
# A parameter reference belongs in the channel's ``environment``, not in the portable Compose file (which
# must run as it is with Docker): ``{{ params`` anywhere in the file is refused.
_PARAM_TEMPLATE_RE = re.compile(r"\{\{\s*params\b")
_MAX_DURATION_SECONDS = 86400.0  # far above any bound the agent accepts; keeps float overflow out of ceil()
_DURATION_RE = re.compile(r"^(?:\d+(?:\.\d+)?(?:us|ms|s|m|h))+$")
_DURATION_PART_RE = re.compile(r"(\d+(?:\.\d+)?)(us|ms|s|m|h)")
_DURATION_UNIT = {"us": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}


# --- the report -------------------------------------------------------------------------------


class InstanceSpecIssue(BaseModel):
    """One finding: ``code`` is stable for tests and tooling, ``path`` points into the spec."""

    code: str
    path: str
    message: str
    # Warnings only: a loud warning is one a seller must not miss (data loss, no start ordering).
    loud: bool = False

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


class InstanceSpecReport(BaseModel):
    """The outcome of :func:`validate_instance_spec`."""

    errors: list[InstanceSpecIssue] = Field(default_factory=list)
    warnings: list[InstanceSpecIssue] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list, description="Service names, in file order")

    @property
    def ok(self) -> bool:
        return not self.errors


class DroppedKey(BaseModel):
    """A Compose key that is not in the manifest, and why."""

    service: str | None = None
    key: str
    reason: Literal["ignored", "not_carried"] = Field(
        description="ignored: meaningless here or decided elsewhere; "
        "not_carried: honored by policy but the agent contract has no field for it yet"
    )


class CompiledInstanceSpec(BaseModel):
    """The compiled spec: the manifest for the agent, and what the compile left out."""

    manifest: NormalizedManifest
    dropped: list[DroppedKey] = Field(default_factory=list)
    warnings: list[InstanceSpecIssue] = Field(default_factory=list)


class InstanceSpecError(ValueError):
    """Raised by :func:`compile_instance_spec` for a spec that does not validate."""

    def __init__(self, report: InstanceSpecReport) -> None:
        self.report = report
        super().__init__("; ".join(str(e) for e in report.errors))


def instance_spec_format(meta: dict[str, Any] | None) -> str:
    """The format a document's ``meta`` declares; ``compose`` when it says nothing."""
    value = (meta or {}).get("format", DEFAULT_FORMAT)
    return value if isinstance(value, str) else ""


INSTANCE_SPEC_TITLE = "Instance spec"
"""The title the offering-level ``instance_spec`` shortcut expands to in ``documents``."""

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")


def snapshot_url_error(url: Any) -> str | None:
    """Why ``url`` cannot be the source of an instance spec, or ``None`` if its shape is acceptable.

    Shape only: https, a host, no credentials, and no host that is plainly private
    (``localhost``, a private, loopback, link-local or reserved IP literal, an
    integer or hex IP form, ``.local`` and ``.internal`` names). Core never fetches
    anything. The backend, which does, repeats the checks on every address the host
    resolves to and on every redirect (unitysvc/unitysvc-core#107).
    """
    if not isinstance(url, str) or not url:
        return "must be an https URL"
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
        parts.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError:
        return f"{url!r} is not a valid URL"
    if parts.scheme != "https":
        return f"{url!r} must be an https URL"
    if not host:
        return f"{url!r} has no host"
    if parts.username is not None or parts.password is not None:
        return f"{url!r} must not carry credentials"
    if host == "localhost" or host.endswith(_PRIVATE_SUFFIXES) or ("." not in host and ":" not in host):
        return f"{url!r}: host {host!r} is not a public host"
    if host.isdigit() or host.startswith("0x"):
        return f"{url!r}: host {host!r} is an IP address written as a number"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if not address.is_global or address.is_multicast:
        return f"{url!r}: host {host!r} is a private, loopback or link-local address"
    return None


def sha256_error(value: Any) -> str | None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        return "must be 64 lowercase hexadecimal characters"
    return None


def expand_instance_spec(offering: dict[str, Any]) -> dict[str, Any]:
    """Expand the offering-level ``instance_spec`` shortcut into ``documents["Instance spec"]``.

    Returns a new offering dict with the shortcut removed and the Document added
    (``file_path`` for a relative path, ``external_url`` for a URL, ``meta.format``
    ``compose``, not public), the way ``logo`` expands. An offering without the
    shortcut is returned as a copy. Raises ``ValueError`` if ``documents`` already
    holds an ``instance_spec`` Document (an offering has one) or the title is taken.
    The seller tooling calls this before upload; core does not read files here.
    """
    result = {key: value for key, value in offering.items() if key != "instance_spec"}
    source = offering.get("instance_spec")
    if source is None:
        return result
    if not isinstance(source, str) or not source:
        raise ValueError("instance_spec must be a relative path or an https URL")
    documents = dict(result.get("documents") or {})
    if any(isinstance(d, dict) and d.get("category") == "instance_spec" for d in documents.values()):
        raise ValueError("an offering has one instance spec: use either 'instance_spec' or a Document, not both")
    if INSTANCE_SPEC_TITLE in documents:
        raise ValueError(f"documents already has an entry titled {INSTANCE_SPEC_TITLE!r}")
    document: dict[str, Any] = {
        "category": "instance_spec",
        "mime_type": "text",
        "meta": {"format": DEFAULT_FORMAT},
        "is_public": False,
    }
    document["external_url" if "://" in source else "file_path"] = source
    documents[INSTANCE_SPEC_TITLE] = document
    result["documents"] = documents
    return result


def unsupported_format_message(fmt: Any) -> str:
    return (
        f"instance_spec format {fmt!r} is not supported; the only format accepted is "
        f"{', '.join(repr(f) for f in SUPPORTED_FORMATS)} (the default when meta.format is absent)"
    )


# --- parsing ----------------------------------------------------------------------------------


class _UniqueKeyLoader(yaml.SafeLoader):
    """``SafeLoader`` that refuses a duplicate mapping key, as Compose does."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            try:
                key = self.construct_object(key_node, deep=True)
                hash(key)
            except TypeError:
                continue
            if key in seen:
                raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep)


def _parse(text: str) -> Any:
    return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - a SafeLoader subclass


class _Checker:
    """One pass over a parsed Compose document, collecting errors, warnings and the compiled services."""

    def __init__(self, channel: ContainerChannel | None, overlay: Any = None) -> None:
        self.channel = channel
        self.overlay = overlay
        self._overlay_checked: dict[str, dict[str, str]] | None = None
        self.report = InstanceSpecReport()
        self.dropped: list[DroppedKey] = []
        self.services: list[ManifestService] = []
        self.ports: dict[str, list[int]] = {}

    # -- recording
    def error(self, code: str, path: str, message: str) -> None:
        self.report.errors.append(InstanceSpecIssue(code=code, path=path, message=message))

    def warn(self, code: str, path: str, message: str, *, loud: bool = False) -> None:
        self.report.warnings.append(InstanceSpecIssue(code=code, path=path, message=message, loud=loud))

    def drop(self, service: str | None, key: str, reason: Literal["ignored", "not_carried"] = "ignored") -> None:
        self.dropped.append(DroppedKey(service=service, key=key, reason=reason))

    # -- the document
    def check(self, doc: Any) -> None:
        if not isinstance(doc, dict):
            self.error("not_a_mapping", "", "the Compose file must be a mapping with a `services` section")
            return
        for key in doc:
            if not isinstance(key, str):
                self.error("bad_key", str(key), "keys must be strings")
            elif key in _TOP_ALLOWED:
                if key in _TOP_IGNORED:
                    self.warn("ignored_key", key, f"top-level `{key}` is ignored")
                    self.drop(None, key)
            elif key.startswith("x-"):
                self.warn("ignored_key", key, "extension fields (x-*) are ignored")
                self.drop(None, key)
            elif key in _REJECTED:
                self.error("rejected_key", key, f"`{key}` is not allowed: {_REJECTED[key]}")
            else:
                self.error("unsupported_key", key, f"top-level `{key}` is not supported")
        services = doc.get("services")
        if not isinstance(services, dict) or not services:
            self.error("no_services", "services", "the Compose file must define at least one service")
            return
        volumes = self._top_volumes(doc.get("volumes"))
        networks = doc.get("networks")
        declared_networks = set(networks) if isinstance(networks, dict) else set()
        used_volumes: dict[str, str] = {}
        service_networks: dict[str, set[str]] = {}
        for name, service in services.items():
            self._service(str(name), service, volumes, used_volumes, declared_networks, service_networks, services)
        self._network_split(service_networks)
        for service in self._overlay_env_services():
            if str(service) not in services:
                self.error(
                    "overlay_service",
                    f"environment.{service}",
                    f"{service!r} is not a service of the file; its services are {', '.join(map(str, services))}",
                )
        self._templates(doc)
        self._channel_context()

    def _templates(self, doc: dict[str, Any]) -> None:
        """``{{ params.NAME }}`` is not allowed in the file: the channel's ``environment`` is where it goes."""

        def scan(value: Any, where: str) -> None:
            if isinstance(value, str):
                if _PARAM_TEMPLATE_RE.search(value):
                    self.error(
                        "bad_template",
                        where,
                        "`{{ params.NAME }}` is not allowed in the instance spec: set the value in the channel's "
                        "`environment`, so the file stays plain Compose",
                    )
            elif isinstance(value, dict):
                for key, item in value.items():
                    scan(item, f"{where}.{key}" if where else str(key))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    scan(item, f"{where}[{index}]")

        scan(doc, "")

    def _top_volumes(self, volumes: Any) -> set[str]:
        if volumes is None:
            return set()
        if not isinstance(volumes, dict):
            self.error("bad_volumes", "volumes", "`volumes` must be a mapping of named volumes")
            return set()
        for name, volume in volumes.items():
            path = f"volumes.{name}"
            if volume is None or volume == {}:
                continue
            if not isinstance(volume, dict):
                self.error("bad_volumes", path, "a volume must be empty or a mapping")
                continue
            for key, value in volume.items():
                if key == "driver" and value == "local":
                    continue
                if isinstance(key, str) and key.startswith("x-"):
                    continue
                self.error(
                    "rejected_key", f"{path}.{key}", f"`{key}` is not allowed on a volume: only a plain scratch volume"
                )
        return {str(n) for n in volumes}

    # -- a service
    def _service(
        self,
        name: str,
        service: Any,
        volumes: set[str],
        used_volumes: dict[str, str],
        declared_networks: set[str],
        service_networks: dict[str, set[str]],
        all_services: dict[Any, Any],
    ) -> None:
        at = f"services.{name}"
        if not DNS_LABEL_RE.fullmatch(name):
            self.error(
                "bad_service_name",
                at,
                f"service name {name!r} must be a DNS label (lowercase letters, digits and '-', at most 63, "
                "starting and ending with a letter or digit): it becomes the host name siblings reach it by",
            )
        if not isinstance(service, dict):
            self.error("bad_service", at, "a service must be a mapping")
            return
        for key in service:
            if not isinstance(key, str):
                self.error("bad_key", at, f"key {key!r} must be a string")
            elif key in _HONORED:
                continue
            elif key in _IGNORED:
                self.warn("ignored_key", f"{at}.{key}", f"`{key}` is ignored: it has no meaning in a cluster")
                self.drop(name, key)
            elif key.startswith("x-"):
                self.warn("ignored_key", f"{at}.{key}", "extension fields (x-*) are ignored")
                self.drop(name, key)
            elif key == "env_file":
                self.warn(
                    "ignored_key",
                    f"{at}.env_file",
                    "`env_file` is ignored: the values it would supply are missing from the instance, so the "
                    "service may not start. Provide them with `environment` on the container channel "
                    f"(environment.{name}.NAME)",
                    loud=True,
                )
                self.drop(name, "env_file")
            elif key == "deploy":
                self._deploy(name, service["deploy"])
            elif key in _REJECTED:
                self.error("rejected_key", f"{at}.{key}", f"`{key}` is not allowed: {_REJECTED[key]}")
            else:
                self.error(
                    "unsupported_key",
                    f"{at}.{key}",
                    f"`{key}` is not supported (only the keys the platform understands are accepted)",
                )

        image = self._image(name, service.get("image"), "build" in service)
        ports = self._ports(name, service.get("ports"))
        self.ports[name] = ports
        overlay = self._overlay_env(name)
        env = {**self._environment(name, service.get("environment"), frozenset(overlay)), **overlay}
        command = self._words(f"{at}.entrypoint", service.get("entrypoint"))
        args = self._words(f"{at}.command", service.get("command"))
        health = self._healthcheck(name, service.get("healthcheck"), ports)
        scratch = self._volumes(name, service.get("volumes"), volumes, used_volumes)
        self._depends_on(name, service.get("depends_on"), all_services)
        self._networks(name, service.get("networks"), declared_networks, service_networks)
        for key, check in (("working_dir", str), ("user", (str, int)), ("read_only", bool)):
            if key in service:
                if not isinstance(service[key], check):
                    self.error("bad_value", f"{at}.{key}", f"`{key}` has the wrong type")
                self.drop(name, key, "not_carried")
                self.warn(
                    "not_carried",
                    f"{at}.{key}",
                    f"`{key}` is accepted but the agent's manifest has no field for it yet; it is not applied",
                )
        if image is not None:
            try:
                self.services.append(
                    ManifestService(
                        name=name if DNS_LABEL_RE.fullmatch(name) else "invalid",
                        image=image,
                        ports=ports or None,
                        env=env or None,
                        command=command or None,
                        args=args or None,
                        health=health,
                        scratch=[ManifestScratch(path=path) for path in scratch] or None,
                    )
                )
            except ValueError as exc:
                self.error("bad_service", at, str(exc).replace("\n", "; "))

    def _deploy(self, name: str, deploy: Any) -> None:
        at = f"services.{name}.deploy"
        if not isinstance(deploy, dict):
            self.error("bad_value", at, "`deploy` must be a mapping")
            return
        for key in deploy:
            if key == "resources":
                self.warn(
                    "ignored_key",
                    f"{at}.resources",
                    "`deploy.resources` is ignored: the channel's `resources` decide what the instance gets "
                    "(it can stay in the file for local Docker)",
                )
                self.drop(name, "deploy.resources")
            elif isinstance(key, str) and key.startswith("x-"):
                self.drop(name, f"deploy.{key}")
            else:
                self.error(
                    "unsupported_key",
                    f"{at}.{key}",
                    f"`deploy.{key}` is not supported; only `deploy.resources` is accepted (and ignored)",
                )

    def _image(self, name: str, image: Any, has_build: bool) -> str | None:
        at = f"services.{name}.image"
        if image is None:
            if not has_build:
                self.error("no_image", at, "a service needs an `image` (name:tag, or name:tag@sha256:... to pin it)")
            return None
        if not isinstance(image, str) or not image:
            self.error("bad_value", at, "`image` must be a string")
            return None
        if "$" in image:
            self.error("not_literal", at, "`image` must be written out in full: a variable cannot stand in the digest")
            return None
        if image != image.strip() or any(c.isspace() for c in image):
            self.error("bad_value", at, "`image` must not contain whitespace")
            return None
        if "@" in image and not _DIGEST_RE.search(image):
            self.error(
                "bad_digest", at, f"`image` {image!r}: a digest must be written @sha256:<64 lowercase hex digits>"
            )
            return None
        if not _DIGEST_RE.search(image):
            self.warn(
                "unpinned_image",
                at,
                f"`image` {image!r} is not pinned by digest: the platform runs it as written and reports the digest "
                "it pulled, and a moving tag can change at a restart. Write name:tag@sha256:<64 hex digits> to pin it "
                "(Compose runs the digest locally too)",
            )
        return image

    def _ports(self, name: str, ports: Any) -> list[int]:
        at = f"services.{name}.ports"
        if ports is None:
            return []
        if not isinstance(ports, list):
            self.error("bad_value", at, "`ports` must be a list")
            return []
        result: list[int] = []
        for index, item in enumerate(ports):
            where = f"{at}[{index}]"
            port, published = self._port(where, item)
            if published is not None:
                self.warn("host_port", where, f"the host side ({published}) is ignored; only the container port counts")
            if port is not None:
                if port in result:
                    self.error("duplicate_port", where, f"container port {port} is listed twice")
                else:
                    result.append(port)
        return result

    def _port(self, where: str, item: Any) -> tuple[int | None, str | None]:
        published: str | None = None
        if isinstance(item, bool):
            self.error("bad_port", where, "a port must be a number or a string")
            return None, None
        if isinstance(item, int):
            target: Any = item
        elif isinstance(item, str):
            if "$" in item:
                self.error("not_literal", where, "a port must be written out, not taken from a variable")
                return None, None
            spec, _, protocol = item.partition("/")
            if protocol and protocol != "tcp":
                self.error("bad_port", where, f"only TCP ports are supported, got {item!r}")
                return None, None
            parts = spec.split(":")
            if len(parts) > 3:
                self.error("bad_port", where, f"{item!r} is not a port mapping")
                return None, None
            target = parts[-1]
            if len(parts) > 1:
                published = ":".join(parts[:-1])
        elif isinstance(item, dict):
            extra = set(item) - {"target", "published", "protocol", "host_ip", "mode", "name", "app_protocol"}
            if extra:
                self.error("bad_port", where, f"unsupported port keys {sorted(extra)}")
                return None, None
            if item.get("protocol", "tcp") != "tcp":
                self.error("bad_port", where, "only TCP ports are supported")
                return None, None
            target = item.get("target")
            if item.get("published") is not None:
                published = str(item["published"])
        else:
            self.error("bad_port", where, "a port must be a number, a string or a mapping")
            return None, None
        try:
            number = int(target)
        except (TypeError, ValueError):
            self.error("bad_port", where, f"{target!r} is not a single port number (ranges are not supported)")
            return None, None
        if not 1 <= number <= 65535:
            self.error("bad_port", where, f"port {number} is outside 1-65535")
            return None, None
        return number, published

    def _overlay(self, where: str = "environment") -> dict[str, dict[str, str]]:
        """The channel's ``environment``, checked: ``{service: {NAME: "value"}}`` of strings."""
        overlay = self.overlay
        if overlay is None:
            return {}
        if not isinstance(overlay, dict):
            self.error("bad_overlay", where, "must be a mapping of service name to a mapping of NAME to value")
            return {}
        result: dict[str, dict[str, str]] = {}
        for service, values in overlay.items():
            at = f"{where}.{service}"
            if not isinstance(values, dict):
                self.error("bad_overlay", at, "must be a mapping of NAME to a string value")
                continue
            clean: dict[str, str] = {}
            for key, value in values.items():
                if not isinstance(key, str) or not _ENV_NAME_RE.fullmatch(key):
                    self.error("bad_env", f"{at}.{key}", f"{key!r} is not a valid environment variable name")
                elif not isinstance(value, str):
                    self.error("bad_env", f"{at}.{key}", "an environment value must be a string")
                else:
                    clean[key] = value
                    if _INTERPOLATION_RE.search(value):
                        self.warn(
                            "interpolation",
                            f"{at}.{key}",
                            "contains a variable reference, kept as written; only {{ params.NAME }} is bound per "
                            "instance, how any other secret is bound is not decided yet "
                            "(unitysvc-core#105, open 4)",
                        )
            result[str(service)] = clean
        return result

    def _checked_overlay(self) -> dict[str, dict[str, str]]:
        if self._overlay_checked is None:
            self._overlay_checked = self._overlay()
        return self._overlay_checked

    def _overlay_env_services(self) -> list[str]:
        return list(self._checked_overlay())

    def _overlay_env(self, name: str) -> dict[str, str]:
        return self._checked_overlay().get(name, {})

    def _environment(self, name: str, environment: Any, supplied: frozenset[str] = frozenset()) -> dict[str, str]:
        """The service's own environment; a bare variable the overlay (``supplied``) gives a value is no host lookup."""
        at = f"services.{name}.environment"
        pairs: list[tuple[str, Any]] = []
        if environment is None:
            return {}
        if isinstance(environment, dict):
            pairs = [(str(k), v) for k, v in environment.items()]
        elif isinstance(environment, list):
            for index, item in enumerate(environment):
                if not isinstance(item, str):
                    self.error("bad_value", f"{at}[{index}]", "an entry must be a KEY=value string")
                    continue
                key, sep, value = item.partition("=")
                pairs.append((key, value if sep else None))
        else:
            self.error("bad_value", at, "`environment` must be a mapping or a list of KEY=value")
            return {}
        env: dict[str, str] = {}
        for key, value in pairs:
            where = f"{at}.{key}"
            if not _ENV_NAME_RE.fullmatch(key):
                self.error("bad_env", where, f"{key!r} is not a valid environment variable name")
            elif value is None and key in supplied:
                continue
            elif value is None:
                self.error(
                    "bad_env",
                    where,
                    "give the variable a value: one with none is taken from the host, which is not allowed",
                )
            elif isinstance(value, (dict, list)):
                self.error("bad_env", where, "an environment value must be a string, number or boolean")
            else:
                text = ("true" if value else "false") if isinstance(value, bool) else str(value)
                env[key] = text
                if _INTERPOLATION_RE.search(text):
                    self.warn(
                        "interpolation",
                        where,
                        "contains a variable reference, kept as written; how a customer secret or parameter is bound "
                        "to it is not decided yet (unitysvc-core#105, open 4)",
                    )
        return env

    def _words(self, where: str, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            try:
                return shlex.split(value)
            except ValueError:
                self.error("bad_value", where, "the command line has unbalanced quotes")
                return []
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return list(value)
        self.error("bad_value", where, "must be a string or a list of strings")
        return []

    def _duration(self, where: str, value: Any) -> float | None:
        if not isinstance(value, str) or not _DURATION_RE.fullmatch(value):
            self.error("bad_value", where, f"{value!r} is not a duration like 10s, 1m30s or 500ms")
            return None
        seconds = sum(float(n) * _DURATION_UNIT[u] for n, u in _DURATION_PART_RE.findall(value))
        if not math.isfinite(seconds) or seconds > _MAX_DURATION_SECONDS:
            self.error("bad_value", where, f"{value[:40]!r} is too long a duration")
            return None
        return seconds

    def _healthcheck(self, name: str, check: Any, ports: list[int]) -> ManifestHealth | None:
        at = f"services.{name}.healthcheck"
        if check is None:
            return None
        if not isinstance(check, dict):
            self.error("bad_value", at, "`healthcheck` must be a mapping")
            return None
        for key in check:
            if key not in _HEALTHCHECK_KEYS:
                self.error("unsupported_key", f"{at}.{key}", f"`healthcheck.{key}` is not supported")
        test = check.get("test")
        if check.get("disable") is True or test == ["NONE"]:
            self.warn(
                "no_healthcheck",
                at,
                "the health check is disabled; readiness falls back to the first port accepting a connection",
            )
            return None
        if "start_interval" in check:
            self.warn("ignored_key", f"{at}.start_interval", "`healthcheck.start_interval` is ignored")
            self.drop(name, "healthcheck.start_interval")
        command: list[str]
        if isinstance(test, str):
            command = ["sh", "-c", test]
        elif isinstance(test, list) and test and all(isinstance(t, str) for t in test):
            if test[0] == "CMD" and len(test) > 1:
                command = test[1:]
            elif test[0] == "CMD-SHELL" and len(test) == 2:
                command = ["sh", "-c", test[1]]
            else:
                self.error(
                    "bad_value", f"{at}.test", 'a list test must be ["CMD", ...], ["CMD-SHELL", "..."] or ["NONE"]'
                )
                return None
        else:
            self.error("bad_value", f"{at}.test", "`healthcheck.test` is required: a string or a list")
            return None
        timing: dict[str, Any] = {}
        for key, field, floor in (
            ("interval", "period_seconds", 1),
            ("timeout", "timeout_seconds", 1),
            ("start_period", "start_period_seconds", 0),
        ):
            if key in check:
                seconds = self._duration(f"{at}.{key}", check[key])
                if seconds is not None:
                    timing[field] = max(floor, math.ceil(seconds))
        if "retries" in check:
            retries = check["retries"]
            if isinstance(retries, bool) or not isinstance(retries, int) or retries < 1:
                self.error("bad_value", f"{at}.retries", "`healthcheck.retries` must be a whole number, at least 1")
            else:
                timing["failure_threshold"] = retries
        try:
            return ManifestHealth(type="exec", command=command, **timing)
        except ValueError as exc:
            self.error("bad_value", at, str(exc).replace("\n", "; "))
            return None

    def _volumes(self, name: str, volumes: Any, declared: set[str], used: dict[str, str]) -> list[str]:
        """Check a service's mounts; return the scratch paths (each a named or anonymous volume mount)."""
        at = f"services.{name}.volumes"
        scratch: list[str] = []
        if volumes is None:
            return scratch
        if not isinstance(volumes, list):
            self.error("bad_value", at, "`volumes` must be a list")
            return scratch
        for index, item in enumerate(volumes):
            where = f"{at}[{index}]"
            source: Any
            target: Any
            if isinstance(item, str):
                parts = item.split(":")
                if len(parts) == 1:
                    source, target = None, parts[0]
                else:
                    source, target = parts[0], parts[1]
            elif isinstance(item, dict):
                kind = item.get("type", "volume")
                if kind != "volume":
                    self.error("host_path", where, f"a {kind} mount is not allowed: only named volumes (scratch space)")
                    continue
                if extra := set(item) - {"type", "source", "target", "read_only"}:
                    self.error("unsupported_key", where, f"unsupported volume keys {sorted(extra)}")
                    continue
                source, target = item.get("source"), item.get("target")
                if source is not None and not isinstance(source, str):
                    self.error("bad_value", where, "a volume's source must be a name")
                    continue
            else:
                self.error("bad_value", where, "a volume must be a string or a mapping")
                continue
            if not isinstance(target, str) or not target.startswith("/"):
                self.error("bad_value", where, "a volume needs an absolute path inside the container")
                continue
            if source is not None and (source.startswith((".", "/", "~")) or "/" in source or "$" in source):
                self.error(
                    "host_path",
                    where,
                    f"{source!r} is a host path or bind mount; only named volumes (scratch space) are allowed",
                )
                continue
            if source is not None:
                if source not in declared:
                    self.error("unknown_volume", where, f"volume {source!r} is not declared in the top-level `volumes`")
                    continue
                if used.setdefault(source, name) != name:
                    self.error(
                        "shared_volume",
                        where,
                        f"volume {source!r} is shared with service {used[source]!r}; a volume cannot be shared "
                        "between services",
                    )
                    continue
            target = posixpath.normpath(target)
            if target.startswith("//"):  # POSIX keeps exactly two leading slashes
                target = "/" + target.lstrip("/")
            if target == "/":
                self.error("bad_value", where, "a volume cannot be mounted at the container's root")
                continue
            if target in scratch:
                self.error("duplicate_mount", where, f"{target} is mounted twice in this service")
                continue
            scratch.append(target)
            label = f"volume {source!r}" if source else "an anonymous volume"
            self.warn(
                "scratch_volume",
                where,
                f"{label} at {target} becomes scratch space (an emptyDir the instance gets at that path, counted in "
                "ephemeral-storage): its data survives a container restart but not a replacement of the instance",
                loud=True,
            )
        return scratch

    def _depends_on(self, name: str, depends_on: Any, all_services: dict[Any, Any]) -> None:
        at = f"services.{name}.depends_on"
        if depends_on is None:
            return
        targets = list(depends_on) if isinstance(depends_on, (list, dict)) else None
        if targets is None:
            self.error("bad_value", at, "`depends_on` must be a list or a mapping")
            return
        for target in targets:
            if not isinstance(target, str):
                self.error("bad_value", at, f"{target!r} is not a service name")
            elif target not in all_services:
                self.error("unknown_service", at, f"depends on {target!r}, which is not a service of this file")
        self.warn(
            "depends_on",
            at,
            "`depends_on` is ignored: there is no start ordering, so the service must retry until its dependencies "
            "answer (readiness keeps traffic away until it does)",
            loud=True,
        )
        self.drop(name, "depends_on")

    def _networks(self, name: str, networks: Any, declared: set[str], service_networks: dict[str, set[str]]) -> None:
        at = f"services.{name}.networks"
        if networks is None:
            service_networks[name] = {"default"}
            return
        if not isinstance(networks, (list, dict)):
            self.error("bad_value", at, "`networks` must be a list or a mapping")
            service_networks[name] = {"default"}
            return
        names = {str(n) for n in networks}
        for network in sorted(names - declared - {"default"}):
            self.error("unknown_network", at, f"network {network!r} is not declared in the top-level `networks`")
        service_networks[name] = names or {"default"}

    def _network_split(self, service_networks: dict[str, set[str]]) -> None:
        """Services share one network here: a file in which two services have no network in common cannot keep that.

        Joining a third service to both networks does not connect them in Compose, so the check is on each pair.
        """
        apart = [
            (a, b)
            for index, a in enumerate(service_networks)
            for b in list(service_networks)[index + 1 :]
            if not service_networks[a] & service_networks[b]
        ]
        if apart:
            pairs = ", ".join(f"{a} and {b}" for a, b in apart[:5])
            self.error(
                "split_networks",
                "services",
                f"some services share no network ({pairs}); in an instance every service reaches every sibling, "
                "so the separation would be silently lost",
            )
        elif any(n != {"default"} for n in service_networks.values()):
            self.warn("ignored_key", "services", "service `networks` are ignored: all services share one network")

    # -- the channel
    def _channel_context(self) -> None:
        channel = self.channel
        names = [s for s in self.ports]
        if channel is None:
            return
        for entry in channel.entries or []:
            at = f"entries.{entry.name}"
            if entry.service not in self.ports:
                self.error("entry_service", at, f"targets service {entry.service!r}, which is not in the spec")
            elif entry.port not in self.ports[entry.service]:
                self.error(
                    "entry_port",
                    at,
                    f"targets port {entry.port} of {entry.service!r}, which declares "
                    f"{self.ports[entry.service] or 'no ports'}",
                )
        sized = channel.resources
        if isinstance(sized, str):
            if len(names) != 1:
                self.error(
                    "size_needs_map",
                    "resources",
                    f"the spec has {len(names)} services ({', '.join(names)}): name each service's size, "
                    'like {"web": "small", "redis": "nano"}',
                )
            return
        for service in sized:
            if service not in self.ports:
                self.error("size_service", f"resources.{service}", "is not a service of the spec")
        if missing := [n for n in names if n not in sized]:
            self.error("size_missing", "resources", f"has no size for {', '.join(missing)}")


def _check(text: str, fmt: str, channel: ContainerChannel | None, overlay: Any = None) -> _Checker:
    checker = _Checker(channel, overlay)
    if fmt not in SUPPORTED_FORMATS:
        checker.error("unsupported_format", "", unsupported_format_message(fmt))
        return checker
    if len(text.encode()) > MAX_SPEC_BYTES:
        checker.error("too_large", "", f"the spec is larger than {MAX_SPEC_BYTES // 1024} KiB")
        return checker
    try:
        doc = _parse(text)
    except RecursionError:
        checker.error("too_deep", "", "the file is nested too deeply to read")
        return checker
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        checker.error("invalid_yaml", "", f"not valid YAML{where}: {getattr(exc, 'problem', None) or exc}")
        return checker
    try:
        checker.check(doc)
    except (
        TypeError,
        AttributeError,
        ValueError,
        KeyError,
        OverflowError,
        RecursionError,
    ) as exc:  # a shape no check above anticipated
        checker.error("malformed", "", f"the file has a structure that cannot be read ({type(exc).__name__}: {exc})")
    checker.report.services = list(checker.ports)
    return checker


def validate_instance_spec(
    text: str,
    *,
    format: str = DEFAULT_FORMAT,
    channel: ContainerChannel | None = None,
    # (the channel's ``environment`` is read from ``channel``)
) -> InstanceSpecReport:
    """Validate an instance spec's content, with the channel that runs it when known.

    Checks the Compose allowlist and the static rules (literal digests,
    container-side ports, health checks, volumes, networks, service names). With
    ``channel``, also that every entry targets a declared service and port and
    that ``resources`` gives each service of the spec a size, and that each service in the
    channel's ``environment`` exists in the file. Never raises on bad input: the problems
    are in the report.
    """
    return _check(text, format, channel, channel.environment if channel else None).report


def compile_instance_spec(
    text: str,
    resources: Mapping[str, ResourceSet],
    *,
    format: str = DEFAULT_FORMAT,
    environment: dict[str, dict[str, str]] | None = None,
) -> CompiledInstanceSpec:
    """Compile a valid spec and the resources of each of its services into the agent's normalized manifest.

    ``resources`` is what the platform resolved each service's size to (see
    :func:`~unitysvc_core.models.container.service_sizes` for the sizes a channel names): a
    :class:`~unitysvc_core.models.container.ResourceSet` per service, keyed by service name, covering
    every service of the spec and no other. Raises :class:`InstanceSpecError`
    (carrying the report) if the spec is not valid for these resources.

    ``environment`` is the channel's ``environment``, merged here and only here: a value
    overrides the file's own ``environment`` for that name, and the Compose bytes are never edited.

    PROPOSAL (unitysvc-core#105, open 3): that core compiles, rather than the
    agent, is not decided.
    """
    checker = _check(text, format, None, environment)
    report = checker.report
    names = list(checker.ports)
    for service in resources:
        if service not in names:
            checker.error("size_service", f"resources.{service}", "is not a service of the spec")
    for service in names:
        if service not in resources:
            checker.error("size_missing", "resources", f"has no resources for {service}")
    for service, share in resources.items():
        try:
            check_resolved(share, f"resources.{service}")
        except ValueError as exc:
            checker.error("bad_resources", f"resources.{service}", str(exc))
    if not report.ok:
        raise InstanceSpecError(report)
    services = []
    for compiled_service in checker.services:
        share = resources[compiled_service.name]
        compiled = compiled_service.model_copy(
            update={
                "resources": ManifestResources(
                    limits=dict(share.limits), requests=share.requests and dict(share.requests)
                )
            }
        )
        services.append(compiled)
    manifest = NormalizedManifest(services=services)
    return CompiledInstanceSpec(manifest=manifest, dropped=checker.dropped, warnings=report.warnings)
