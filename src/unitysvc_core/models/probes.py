"""Declarative connectivity probes for a container service (PROPOSAL, unitysvc/unitysvc-core#105).

A container channel's connectivity test is derived by the platform (start,
wait for health, fetch each entry). A seller may add a ``connectivity_test``
Document whose content is this file: a list of probes run on top of the derived
checks. It is data, not a script. Core publishes the schema
(``schema/probes_v1.json``) and shared fixtures (``tests/fixtures/probes``); the
runners live in unitysvc-sellers (``docker compose``) and unitysvc-compute-k8s
(the check intent).
"""

from __future__ import annotations

import json
from collections.abc import Collection
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .container import DNS_LABEL_RE


class Probe(BaseModel):
    """One request made to an entry, and what it must answer."""

    model_config = ConfigDict(extra="forbid")

    entry: str = Field(pattern=DNS_LABEL_RE.pattern, description="Name of a channel entry")
    path: str = Field(default="/", pattern=r"^/", description="Request path, starts with /")
    expect_status: int = Field(default=200, ge=100, le=599, description="The status the response must have")
    expect_body_contains: str | None = Field(default=None, description="Text the response body must contain")


class ProbesV1(BaseModel):
    """The content of a declarative ``connectivity_test`` Document."""

    model_config = ConfigDict(extra="forbid")

    probes: list[Probe] = Field(min_length=1)


def validate_probes_text(text: str, entries: Collection[Any]) -> list[str]:
    """Errors in a declarative probes document: bad JSON, bad shape, or a probe naming no entry."""
    try:
        document = json.loads(text)
    except ValueError as exc:
        return [f"not valid JSON: {exc}"]
    try:
        probes = ProbesV1.model_validate(document)
    except ValidationError as exc:
        return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return [
        f"probes[{index}].entry: {probe.entry!r} is not an entry of the offering's container channels"
        for index, probe in enumerate(probes.probes)
        if probe.entry not in entries
    ]
