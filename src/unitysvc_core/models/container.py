"""Container allocations and the normalized manifest consumed by the compute runner.

This module describes execution, independently of an offering's service_type.
It contains no Kubernetes clients, image pulling, placement or pricing logic.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

SERVICE_NAME = r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$"
_QUANTITY = re.compile(r"^(\+?(?:\d+(?:\.\d*)?|\.\d+))([eE][+-]?\d+|[numkMGTPE]|[KMGTPE]i)?$")
_SI = {"": 0, "n": -9, "u": -6, "m": -3, "k": 3, "M": 6, "G": 9, "T": 12, "P": 15, "E": 18}
NATIVE_RESOURCES = ("cpu", "memory", "ephemeral-storage")


def resource_quantity(value: str) -> Decimal:
    """Parse a positive Kubernetes quantity, in cores/bytes/resource units."""
    match = _QUANTITY.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid resource quantity {value!r}")
    number, suffix = match.groups()
    suffix = suffix or ""
    # Bound the exponent before exponentiation (input is seller-authored).
    if suffix.startswith(("e", "E")) and len(suffix) > 1:
        exponent = int(suffix[1:])
        if not -9 <= exponent <= 18:
            raise ValueError("resource exponent must be between -9 and 18")
        factor = Decimal(10) ** exponent
    elif suffix.endswith("i"):
        factor = Decimal(1024) ** ("KMGTPE".index(suffix[0]) + 1)
    else:
        factor = Decimal(10) ** _SI[suffix]
    try:
        quantity = Decimal(number) * factor
    except InvalidOperation as exc:
        raise ValueError(f"invalid resource quantity {value!r}") from exc
    if not 0 < quantity <= 2**63 - 1:
        raise ValueError("resource quantities must be positive and fit a signed 64-bit value")
    return quantity


def _resource_name(name: str) -> None:
    if name in NATIVE_RESOURCES:
        return
    if "/" not in name or name.startswith("requests."):
        raise ValueError(f"unsupported resource {name!r}")
    domain, key = name.split("/", 1)
    if domain == "kubernetes.io" or domain.endswith(".kubernetes.io"):
        raise ValueError("the kubernetes.io resource namespace is reserved")
    if len(domain) > 253 or not all(re.fullmatch(SERVICE_NAME, part) for part in domain.split(".")):
        raise ValueError(f"invalid resource domain {domain!r}")
    if len(key) > 63 or not re.fullmatch(r"[A-Za-z0-9](?:[-_.A-Za-z0-9]*[A-Za-z0-9])?", key):
        raise ValueError(f"invalid resource name {name!r}")


class ContainerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContainerResources(ContainerModel):
    """One replica's allocation. Each tier explicitly budgets CPU, RAM and disk.

    A resource given only as a request or limit is copied to the other side.
    Extended resources use integer quantities and equal requests and limits.
    """

    requests: dict[str, str] = Field(default_factory=dict)
    limits: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _resolve(self) -> ContainerResources:
        self.requests = dict(self.requests)
        self.limits = dict(self.limits)
        names = set(self.requests) | set(self.limits)
        if missing := set(NATIVE_RESOURCES) - names:
            raise ValueError(f"declare CPU, memory and ephemeral disk explicitly; missing {sorted(missing)}")
        for name in sorted(names):
            _resource_name(name)
            self.requests.setdefault(name, self.limits.get(name, ""))
            self.limits.setdefault(name, self.requests[name])
            request, limit = resource_quantity(self.requests[name]), resource_quantity(self.limits[name])
            if request > limit:
                raise ValueError(f"{name}: request exceeds limit")
            if name == "cpu" and (request % Decimal("0.001") or limit % Decimal("0.001")):
                raise ValueError("CPU must be specified in whole millicores (at least 1m)")
            if "/" in name and (request % 1 or limit % 1 or request != limit):
                raise ValueError(f"{name}: extended resources need whole numbers and equal request and limit")
        return self


class ContainerHealth(ContainerModel):
    type: Literal["http", "tcp", "exec"]
    path: str | None = None
    port: StrictInt | None = Field(default=None, ge=1, le=65535)
    command: list[str] | None = None
    period_seconds: StrictInt | None = Field(default=None, ge=1, le=300)
    timeout_seconds: StrictInt | None = Field(default=None, ge=1, le=300)
    success_threshold: StrictInt | None = Field(default=None, ge=1, le=10)
    failure_threshold: StrictInt | None = Field(default=None, ge=1, le=30)
    start_period_seconds: StrictInt | None = Field(default=None, ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def _handler(self) -> ContainerHealth:
        if self.type == "exec":
            if not self.command or self.path is not None or self.port is not None:
                raise ValueError("exec health needs a command and takes no path or port")
        else:
            if self.command is not None:
                raise ValueError("http/tcp health takes no command")
            if self.type == "http" and (not self.path or not self.path.startswith("/")):
                raise ValueError("http health needs a path starting with /")
            if self.type == "tcp" and self.path is not None:
                raise ValueError("tcp health takes no path")
        return self


class ContainerService(ContainerModel):
    name: str = Field(pattern=SERVICE_NAME, max_length=63)
    image: str = Field(pattern=r"^\S+@sha256:[0-9a-f]{64}$")
    ports: list[StrictInt] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    command: list[str] | None = Field(default=None, min_length=1)
    args: list[str] | None = Field(default=None, min_length=1)
    resources: ContainerResources
    replicas: StrictInt = Field(default=1, ge=1, le=2**31 - 1)
    health: ContainerHealth | None = None

    @field_validator("ports")
    @classmethod
    def _ports(cls, ports: list[int]) -> list[int]:
        if len(set(ports)) != len(ports) or any(port < 1 or port > 65535 for port in ports):
            raise ValueError("ports must be unique TCP port numbers from 1 to 65535")
        return ports

    @field_validator("env")
    @classmethod
    def _env(cls, env: dict[str, str]) -> dict[str, str]:
        if any(not re.fullmatch(r"[-._A-Za-z][-._A-Za-z0-9]*", name) for name in env):
            raise ValueError("invalid environment variable name")
        return env

    @model_validator(mode="after")
    def _health_port(self) -> ContainerService:
        if self.health and self.health.type != "exec":
            if not self.ports or (self.health.port is not None and self.health.port not in self.ports):
                raise ValueError("health must target a declared service port")
        return self


class ContainerEntry(ContainerModel):
    """A public entry target. Central assigns its hostname at instance creation."""

    name: str = Field(pattern=SERVICE_NAME, max_length=63)
    service: str = Field(pattern=SERVICE_NAME, max_length=63)
    port: StrictInt = Field(ge=1, le=65535)


class ContainerManifest(ContainerModel):
    services: list[ContainerService] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_names(self) -> ContainerManifest:
        names = [service.name for service in self.services]
        if len(set(names)) != len(names):
            raise ValueError("service names must be unique")
        return self

    def runtime_dict(self) -> dict:
        return self.model_dump(mode="json", exclude_none=True)

    def content_hash(self) -> str:
        body = json.dumps(self.runtime_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()

    def resource_totals(self, side: Literal["requests", "limits"] = "limits") -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for service in self.services:
            for name, value in getattr(service.resources, side).items():
                totals[name] = totals.get(name, Decimal(0)) + resource_quantity(value) * service.replicas
        return totals

    def validate_entries(self, entries: list[ContainerEntry]) -> None:
        names: set[str] = set()
        services = {service.name: service for service in self.services}
        for entry in entries:
            if entry.name in names:
                raise ValueError(f"duplicate entry {entry.name!r}")
            names.add(entry.name)
            if entry.service not in services or entry.port not in services[entry.service].ports:
                raise ValueError(f"entry {entry.name!r} must target a declared service port")
