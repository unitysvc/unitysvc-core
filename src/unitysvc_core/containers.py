"""Pure normalization of the initial Compose subset into the compute manifest.

Images must already be prepared and pinned. Build, host access, volumes,
published ports, dependency ordering and runtime secret resolution are excluded.
"""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, StrictInt, field_validator, model_validator

from .models.container import (
    SERVICE_NAME,
    ContainerEntry,
    ContainerHealth,
    ContainerManifest,
    ContainerModel,
    ContainerService,
    ContainerTierResources,
)


def _seconds(value: str | int) -> int:
    if isinstance(value, bool):
        raise ValueError("health durations must be whole seconds")
    if isinstance(value, int):
        return value
    match = re.fullmatch(r"(\d+)(s|m|h)", value)
    if match is None:
        raise ValueError("health durations use whole seconds, minutes or hours (e.g. 5s)")
    return int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]


class ComposeHealthcheck(ContainerModel):
    test: list[str] | str | None = None
    disable: bool = False
    interval: str | StrictInt | None = None
    timeout: str | StrictInt | None = None
    retries: StrictInt | None = Field(default=None, ge=1, le=30)
    start_period: str | StrictInt | None = None

    def normalize(self) -> ContainerHealth | None:
        if self.disable or self.test == ["NONE"]:
            return None
        if isinstance(self.test, str):
            command = ["/bin/sh", "-c", self.test]
        elif self.test and self.test[0] == "CMD" and len(self.test) > 1:
            command = self.test[1:]
        elif self.test and self.test[0] == "CMD-SHELL" and len(self.test) == 2:
            command = ["/bin/sh", "-c", self.test[1]]
        else:
            raise ValueError("declare healthcheck.test as CMD, CMD-SHELL, NONE or a shell string")
        return ContainerHealth(
            type="exec",
            command=command,
            period_seconds=_seconds(self.interval) if self.interval is not None else None,
            timeout_seconds=_seconds(self.timeout) if self.timeout is not None else None,
            failure_threshold=self.retries,
            start_period_seconds=_seconds(self.start_period) if self.start_period is not None else None,
        )


class ComposeDeploy(ContainerModel):
    replicas: StrictInt = Field(default=1, ge=1, le=2**31 - 1)


class ComposeService(ContainerModel):
    image: str
    ports: list[StrictInt | str] = Field(default_factory=list)
    expose: list[StrictInt | str] = Field(default_factory=list)
    environment: dict[str, str] | list[str] = Field(default_factory=dict)
    entrypoint: list[str] | str | None = None
    command: list[str] | str | None = None
    deploy: ComposeDeploy = Field(default_factory=ComposeDeploy)
    healthcheck: ComposeHealthcheck | None = None
    health: ContainerHealth | None = None

    @field_validator("entrypoint", "command")
    @classmethod
    def _nonempty_command(cls, value: list[str] | str | None) -> list[str] | str | None:
        if value is not None and not value:
            raise ValueError("empty command/entrypoint overrides cannot be represented by the current runner")
        return value

    @model_validator(mode="after")
    def _one_health(self) -> ComposeService:
        if self.health is not None and self.healthcheck is not None:
            raise ValueError("use health or healthcheck, not both")
        return self


class ContainerSpec(ContainerModel):
    version: str | None = None
    services: dict[str, ComposeService] = Field(min_length=1)

    @field_validator("services")
    @classmethod
    def _names(cls, services: dict[str, ComposeService]) -> dict[str, ComposeService]:
        if any(not re.fullmatch(SERVICE_NAME, name) for name in services):
            raise ValueError("Compose service names must be Kubernetes-compatible DNS labels")
        return services


def _port(value: int | str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    match = re.fullmatch(r"(\d+)(?:/tcp)?", str(value))
    if match is None:
        raise ValueError("declare container TCP ports only; host/published ports and UDP are unsupported")
    return int(match[1])


def _environment(values: dict[str, str] | list[str]) -> dict[str, str]:
    if isinstance(values, dict):
        return values
    result: dict[str, str] = {}
    for value in values:
        name, separator, content = value.partition("=")
        if not separator or name in result:
            raise ValueError("environment needs distinct explicit NAME=value pairs; host inheritance is unsupported")
        result[name] = content
    return result


def normalize_container_spec(
    data: dict[str, Any],
    *,
    resources: ContainerTierResources | dict[str, Any],
    entries: list[ContainerEntry] | None = None,
) -> ContainerManifest:
    spec = ContainerSpec.model_validate(data)
    budget = (
        resources if isinstance(resources, ContainerTierResources) else ContainerTierResources.model_validate(resources)
    )
    allocations = budget.allocate({name: source.deploy.replicas for name, source in spec.services.items()})
    services = []
    for name, source in sorted(spec.services.items()):
        health = source.health or (source.healthcheck.normalize() if source.healthcheck else None)
        services.append(
            ContainerService(
                name=name,
                image=source.image,
                ports=list(dict.fromkeys(_port(port) for port in source.ports + source.expose)),
                env=_environment(source.environment),
                command=shlex.split(source.entrypoint) if isinstance(source.entrypoint, str) else source.entrypoint,
                args=shlex.split(source.command) if isinstance(source.command, str) else source.command,
                resources=allocations[name],
                replicas=source.deploy.replicas,
                health=health,
            )
        )
    manifest = ContainerManifest(services=services)
    manifest.validate_entries(entries or [])
    return manifest


def load_container_spec(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    content = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        data = json.loads(content)
    elif path.suffix == ".toml":
        data = tomllib.loads(content)
    else:
        data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError("a container specification must be an object")
    return data


def validate_container_documents(offering: dict[str, Any], service_dir: Path) -> list[str]:
    """Compile local offering documents during seller-side static validation."""
    errors = []
    for name, raw in (offering.get("upstream_access_config") or {}).items():
        if not isinstance(raw, dict) or raw.get("access_method") != "container":
            continue
        try:
            container_manifest_for_channel(offering, name, service_dir)
        except (ValueError, OSError, KeyError, yaml.YAMLError) as exc:
            errors.append(f"container channel {name!r}: {exc}")
    return errors


def container_manifest_for_channel(offering: dict[str, Any], channel_name: str, service_dir: Path) -> ContainerManifest:
    """Resolve an offering-scoped spec and compile one tier, for any consumer."""
    from .models.channel import ContainerChannel
    from .models.offering_v1 import OfferingV1

    model = OfferingV1.model_validate(offering)
    channel = model.upstream_access_config.root[channel_name]
    if not isinstance(channel, ContainerChannel):
        raise ValueError(f"channel {channel_name!r} is not a container channel")
    server = channel.servers[0]
    document = (model.documents or {})[server.spec]
    return normalize_container_spec(
        load_container_spec(service_dir / str(document.file_path)),
        resources=channel.resources,
        entries=channel.entries,
    )
