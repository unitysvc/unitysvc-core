"""The normalized manifest: what the compute agent receives for one instance (unitysvc/unitysvc-core#105).

The instance spec is the seller's file (Compose today); the normalized manifest
is its compiled, validated form, one service per Compose service. Its field
names and shapes follow the agent's wire types in unitysvc-compute-k8s
(``internal/intent/intent.go``: ``Manifest``, ``Service``, ``Resources``,
``Health``), which decodes strictly and refuses an unknown field. The JSON
Schema generated from these models (``schema/normalized_manifest_v1.json``) is
the one core owns; the agent repo vendors a copy.

Deliberate differences from ``intent.go`` (listed in the PR that added it):

* the agent's ``cpu`` and ``memory`` shorthand (request = limit) is not
  modelled: core always writes the general ``resources`` form;
* ``scratch`` (temporary storage mounts) is a core addition the agent does not decode yet; the agent repo
  vendors this schema when it adds it (unitysvc/unitysvc-core#107);
* ``persist`` (directories restored from, and copied back to, the customer's storage) has the agent's
  ``Persist`` shape exactly (unitysvc/unitysvc#3098; unitysvc-compute-k8s ``internal/intent/intent.go``);
* the agent's ``Entry`` (with a ``hostname`` central assigns) is not part of the
  manifest; it travels beside it in the intent.
"""

from __future__ import annotations

import posixpath
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .container import DNS_LABEL_RE


class ManifestResources(BaseModel):
    """What a service asks for and may use: resource name to Kubernetes quantity."""

    model_config = ConfigDict(extra="forbid")

    requests: dict[str, str] | None = None
    limits: dict[str, str] | None = None


class ManifestHealth(BaseModel):
    """A seller-defined readiness check for one service (the agent's ``Health``).

    ``http``: GET ``path`` on ``port`` (2xx or 3xx passes); ``tcp``: ``port``
    accepts a connection; ``exec``: ``command`` runs in the container and exits
    0. ``port`` defaults to the service's first port. Timing is optional; the
    agent's defaults are period 5 s, timeout 2 s, success 1, failure 3, start
    period 60 s.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["http", "tcp", "exec"]
    path: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    command: list[str] | None = None
    # The agent's bounds (internal/render/validate.go): period and timeout 1..300, success 1..10, failure 1..30,
    # start period 0..the cluster's progress deadline. The deadline is configured per cluster (default 120 s);
    # core checks the default, so a cluster configured longer may accept more than the schema does.
    period_seconds: int | None = Field(default=None, ge=1, le=300)
    timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    success_threshold: int | None = Field(default=None, ge=1, le=10)
    failure_threshold: int | None = Field(default=None, ge=1, le=30)
    start_period_seconds: int | None = Field(default=None, ge=0, le=120)

    @model_validator(mode="after")
    def _check_type(self) -> ManifestHealth:
        if self.type == "http":
            if not self.path or not self.path.startswith("/"):
                raise ValueError("an http health check needs a path that starts with '/'")
        elif self.path is not None:
            raise ValueError(f"a {self.type} health check takes no path")
        if self.type == "exec":
            if not self.command:
                raise ValueError("an exec health check needs a command")
            if self.port is not None:
                raise ValueError("an exec health check takes no port")
        elif self.command is not None:
            raise ValueError(f"a {self.type} health check takes no command")
        return self


class ManifestScratch(BaseModel):
    """A path in the container that is temporary storage (from a Compose named or anonymous volume mount).

    The cluster gives it an ``emptyDir``: the path exists and is writable, its data survives a container
    restart but not a replacement of the instance, and it counts against the service's ephemeral storage.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(pattern=r"^/.", description="Normalized absolute path inside the container, below /")

    @field_validator("path")
    @classmethod
    def _normalized(cls, value: str) -> str:
        if posixpath.normpath(value) != value or value == "/" or value.startswith("//"):
            raise ValueError("path must be normalized (no '..', '.', '//' or trailing '/') and below /")
        return value


# A folder of the customer's storage, relative and normalized: segments of 1 to 255 characters from the
# alphabet the platform's `service:home` grant uses for a folder (unitysvc/unitysvc key_authority.HOME_FOLDER).
_FOLDER_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,254}$")
MAX_FOLDER_LENGTH = 512
MAX_EXCLUDES = 32
MAX_EXCLUDE_LENGTH = 256


def check_folder(value: str) -> str:
    """A persisted folder: relative (no leading slash), no empty, ``.`` or ``..`` segment, no trailing slash."""
    if not value or len(value) > MAX_FOLDER_LENGTH:
        raise ValueError(f"a folder is 1 to {MAX_FOLDER_LENGTH} characters")
    if not all(_FOLDER_SEGMENT_RE.fullmatch(seg) and ".." not in seg for seg in value.split("/")):
        raise ValueError(
            f"{value!r} is not a folder: relative segments of letters, digits, '.', '_' and '-' (not starting with "
            "'.'), no empty segment, no '..', no leading or trailing slash"
        )
    return value


# `source` of a channel's persist entry: `{{ enrollment.path }}/<folder>` (the instance's enrollment folder in
# the customer's storage) or `{{ home.path }}/<folder>` (a folder of the customer's home).
_PERSIST_SOURCE_RE = re.compile(r"^\{\{\s*(enrollment|home)\.path\s*\}\}/(.+)$")
_PERSIST_STORES = {"enrollment": "storage", "home": "home"}


def parse_persist_source(source: object) -> tuple[str, str]:
    """``(store, folder)`` of a persist ``source``, which is nothing else than a folder of one of two places."""
    match = _PERSIST_SOURCE_RE.fullmatch(source) if isinstance(source, str) else None
    if match is None:
        raise ValueError(
            "`source` must be `{{ enrollment.path }}/<folder>` (the instance's own storage) or "
            "`{{ home.path }}/<folder>` (a folder of the customer's home): quote it, it starts with `{{`"
        )
    return _PERSIST_STORES[match.group(1)], check_folder(match.group(2))


def check_exclude(pattern: str) -> str:
    """An exclude glob, relative to the persisted directory: ``*``, ``?``, ``[...]`` and ``**`` across folders."""
    if not pattern or len(pattern) > MAX_EXCLUDE_LENGTH or pattern.startswith("/") or ".." in pattern:
        raise ValueError(
            f"exclude {pattern!r} must be a relative pattern of 1 to {MAX_EXCLUDE_LENGTH} characters, with no '..'"
        )
    if pattern.count("[") != pattern.count("]"):
        raise ValueError(f"exclude {pattern!r} has an unbalanced '['")
    return pattern


class ManifestPersist(BaseModel):
    """A path in the container whose files outlive the instance (unitysvc/unitysvc#3098).

    Before the service starts the agent restores ``folder`` of ``store`` into the directory, while it runs and
    once more when it stops it copies new and changed files back (never deleting), and the same files are
    there at the next start, on this cluster or another. ``store`` is ``storage`` (the instance's enrollment
    folder, which the key the agent hands the sync derives from the enrollment, so the manifest names no
    enrollment) or ``home`` (a folder of the customer's home, which the customer must have granted).
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(pattern=r"^/.", description="Normalized absolute path inside the container, below /")
    store: Literal["storage", "home"] = Field(
        description="`storage`: the instance's enrollment folder; `home`: a folder of the customer's home"
    )
    folder: str = Field(description="Relative folder inside the store, without a leading or trailing slash")
    mode: Literal["rw", "ro"] = Field(
        default="rw", description="`rw`: restored and copied back; `ro`: restored only, never copied back"
    )
    exclude: list[str] | None = Field(default=None, description="Globs, relative to the directory, left out both ways")
    uid: int | None = Field(default=None, ge=0, description="Owner of the restored files; with gid, or neither")
    gid: int | None = Field(default=None, ge=0)

    @field_validator("path")
    @classmethod
    def _normalized(cls, value: str) -> str:
        if posixpath.normpath(value) != value or value == "/" or value.startswith("//"):
            raise ValueError(f"{value!r} must be a normalized absolute path below /")
        return value

    @field_validator("folder")
    @classmethod
    def _folder(cls, value: str) -> str:
        return check_folder(value)

    @field_validator("exclude")
    @classmethod
    def _exclude(cls, value: list[str] | None) -> list[str] | None:
        if value is not None:
            if len(value) > MAX_EXCLUDES:
                raise ValueError(f"at most {MAX_EXCLUDES} exclude patterns")
            for pattern in value:
                check_exclude(pattern)
        return value

    @model_validator(mode="after")
    def _owner(self) -> ManifestPersist:
        if (self.uid is None) != (self.gid is None):
            raise ValueError("give both uid and gid, or neither")
        return self


class ManifestService(BaseModel):
    """One container of an instance (the agent's ``Service``)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="A DNS label; the Service name siblings reach it by")
    image: str = Field(min_length=1, description="Digest-pinned image reference")
    ports: list[int] | None = Field(default=None, description="Container ports (TCP)")
    env: dict[str, str] | None = None
    command: list[str] | None = Field(default=None, description="Replaces the image's entrypoint")
    args: list[str] | None = Field(default=None, description="Replaces the image's command")
    resources: ManifestResources | None = None
    replicas: int | None = Field(default=None, ge=0)
    health: ManifestHealth | None = None
    scratch: list[ManifestScratch] | None = Field(
        default=None, description="Temporary storage mounted in the container (an emptyDir each)"
    )
    persist: list[ManifestPersist] | None = Field(
        default=None, description="Directories restored from and copied back to the customer's storage"
    )

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not DNS_LABEL_RE.fullmatch(value):
            raise ValueError(f"{value!r} must be a DNS label: lowercase letters, digits and '-', at most 63")
        return value

    @field_validator("ports")
    @classmethod
    def _ports(cls, value: list[int] | None) -> list[int] | None:
        if value is not None:
            if any(not 1 <= p <= 65535 for p in value):
                raise ValueError("ports must be 1-65535")
            if len(set(value)) != len(value):
                raise ValueError("ports must be distinct")
        return value

    @field_validator("scratch")
    @classmethod
    def _scratch(cls, value: list[ManifestScratch] | None) -> list[ManifestScratch] | None:
        if value is not None:
            paths = [s.path for s in value]
            if len(set(paths)) != len(paths):
                raise ValueError("scratch paths must be distinct")
        return value

    @model_validator(mode="after")
    def _persist(self) -> ManifestService:
        if not self.persist:
            return self
        paths = [p.path for p in self.persist]
        if len(set(paths)) != len(paths) or set(paths) & {s.path for s in self.scratch or []}:
            raise ValueError("persist paths must be distinct, and not also scratch")
        places = [(p.store, p.folder) for p in self.persist]
        if len(set(places)) != len(places):
            raise ValueError("two persisted paths cannot share a folder")
        return self

    @model_validator(mode="after")
    def _check_health_port(self) -> ManifestService:
        health = self.health
        if health is not None and health.port is not None and health.port not in (self.ports or []):
            raise ValueError(f"health.port {health.port} is not one of the service's ports")
        if health is not None and health.type in ("http", "tcp") and not self.ports:
            raise ValueError(f"a {health.type} health check needs the service to declare a port")
        return self


class NormalizedManifest(BaseModel):
    """The compiled instance spec: the agent's ``Manifest``."""

    model_config = ConfigDict(extra="forbid")

    services: list[ManifestService] = Field(min_length=1)

    @field_validator("services")
    @classmethod
    def _unique(cls, value: list[ManifestService]) -> list[ManifestService]:
        names = [s.name for s in value]
        if duplicates := sorted({n for n in names if names.count(n) > 1}):
            raise ValueError(f"service names must be unique: {', '.join(duplicates)}")
        return value
