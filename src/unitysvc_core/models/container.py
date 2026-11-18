"""The parts of a ``container`` channel: its sizes and its entries (unitysvc/unitysvc-core#105).

A container channel is one tier of a service that runs as instances on Kubernetes. A seller does not
write Kubernetes resources: ``resources`` names a *size* (``"nano"``) for a one-service spec, or the
size of each service (``{"web": "small", "redis": "nano"}``). The platform owns what a size is
(its table, which changes without a release of core), resolves each size to a :class:`ResourceSet`
(``limits``, optionally ``requests``, in Kubernetes' own shape) and compiles the spec with those.
Core holds no Kubernetes client and no size table: it checks the form of a size name and parses and
checks the quantities the platform resolved, so a mistake is found at ``specs validate`` rather
than when an instance fails to schedule.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from fractions import Fraction
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The resource names a tier may name. Anything else is refused: a new resource
# is a decision of the platform, not a seller's free text.
RESOURCE_NAMES = ("cpu", "memory", "ephemeral-storage", "nvidia.com/gpu")
REQUIRED_RESOURCES = ("cpu", "memory", "ephemeral-storage")
# Extended resources: whole numbers only, and a request must equal the limit.
_EXTENDED = frozenset({"nvidia.com/gpu"})

_QUANTITY_RE = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+))(e[+-]?\d+|[numkMGTPE]|[KMGTPE]i|)$", re.IGNORECASE)
_SI = {"n": Fraction(1, 10**9), "u": Fraction(1, 10**6), "m": Fraction(1, 1000), "": Fraction(1)}
_SI.update({"k": Fraction(10**3), "M": Fraction(10**6), "G": Fraction(10**9)})
_SI.update({"T": Fraction(10**12), "P": Fraction(10**15), "E": Fraction(10**18)})
_BINARY = {f"{p}i": Fraction(2 ** (10 * i)) for i, p in enumerate("KMGTPE", start=1)}

_MAX_QUANTITY_CHARS = 64
_MAX_EXPONENT = 30

# A DNS-1123 label, which a Kubernetes Service and a hostname segment must be.
DNS_LABEL_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


def parse_quantity(value: str) -> Fraction:
    """Parse a Kubernetes quantity (``250m``, ``1Gi``, ``1e3``, ``0.5``) into base units, exactly.

    Follows ``resource.ParseQuantity``: decimal SI suffixes (``n u m k M G T P E``,
    where only the lowercase ``k`` is a kilo), binary suffixes (``Ki Mi Gi Ti Pi Ei``)
    and decimal exponents. Raises ``ValueError`` for anything else, including a
    ``K`` or ``ki`` that Kubernetes refuses.
    """
    if not isinstance(value, str):
        raise ValueError(f"{value!r} is not a quantity string")
    if len(value) > _MAX_QUANTITY_CHARS:
        raise ValueError(f"a quantity is at most {_MAX_QUANTITY_CHARS} characters")
    match = _QUANTITY_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"{value!r} is not a Kubernetes quantity (like 250m, 1, 512Mi, 2Gi)")
    number, suffix = match.groups()
    if suffix in _BINARY:
        factor = _BINARY[suffix]
    elif len(suffix) > 1 and suffix[0] in "eE":
        exponent = int(suffix[1:])
        if abs(exponent) > _MAX_EXPONENT:  # before the power: 1e1000000000 must not be computed
            raise ValueError(f"{value!r}: the exponent must be between -{_MAX_EXPONENT} and {_MAX_EXPONENT}")
        factor = Fraction(10) ** exponent
    elif suffix in _SI:
        factor = _SI[suffix]
    else:
        raise ValueError(f"{value!r} is not a Kubernetes quantity (suffix {suffix!r} is not one Kubernetes accepts)")
    return Fraction(number) * factor


def _check_quantities(where: str, quantities: dict[str, str]) -> dict[str, Fraction]:
    parsed: dict[str, Fraction] = {}
    for name, text in quantities.items():
        if name not in RESOURCE_NAMES:
            raise ValueError(f"{where}.{name}: unknown resource; the names are {', '.join(RESOURCE_NAMES)}")
        try:
            amount = parse_quantity(text)
        except ValueError as exc:
            raise ValueError(f"{where}.{name}: {exc}") from None
        if amount <= 0:
            raise ValueError(f"{where}.{name}: {text!r} must be positive")
        if name in _EXTENDED and amount.denominator != 1:
            raise ValueError(f"{where}.{name}: {text!r} must be a whole number")
        parsed[name] = amount
    return parsed


class ResourceSet(BaseModel):
    """Requests and limits, in Kubernetes' own shape."""

    model_config = ConfigDict(extra="forbid")

    limits: dict[str, str] = Field(description="Most each resource may use: resource name to quantity")
    requests: dict[str, str] | None = Field(
        default=None, description="What is reserved; each at most its limit. Absent: the limits are reserved"
    )

    def effective(self, where: str = "resources") -> tuple[dict[str, Fraction], dict[str, Fraction]]:
        """``(requests, limits)`` as the agent resolves them: a resource with no request is requested at its limit."""
        requests, limits = self.parsed(where)
        return {**limits, **requests}, limits

    def parsed(self, where: str = "resources") -> tuple[dict[str, Fraction], dict[str, Fraction]]:
        """``(requests, limits)`` as exact amounts; requests default to the limits."""
        limits = _check_quantities(f"{where}.limits", self.limits)
        if self.requests is None:
            return {}, limits
        requests = _check_quantities(f"{where}.requests", self.requests)
        for name, amount in requests.items():
            if name not in limits:
                raise ValueError(f"{where}.requests.{name}: no limit for it")
            if amount > limits[name]:
                raise ValueError(
                    f"{where}.requests.{name}: {self.requests[name]!r} is above the limit {self.limits[name]!r}"
                )
            if name in _EXTENDED and amount != limits[name]:
                raise ValueError(f"{where}.requests.{name}: the request must equal the limit")
        return requests, limits


def check_resolved(resources: ResourceSet, where: str = "resources") -> ResourceSet:
    """Check what the platform resolved a size to before an instance runs on it: every quantity
    parses and is positive, ``cpu``, ``memory`` and ``ephemeral-storage`` limits are all set (the
    instance's quota is built from them), a request is at most its limit. Raises ``ValueError``."""
    _, limits = resources.parsed(where)
    if missing := [name for name in REQUIRED_RESOURCES if name not in limits]:
        raise ValueError(f"{where}.limits must set {', '.join(missing)}")
    return resources


# A size is a descriptive name the platform maps to resources: ``nano``, ``small-2x``,
# ``small-largedisk``. Core checks the form only; which sizes exist, and what each is, is the
# platform's table (it changes without a release of core).
SIZE_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,30}[a-z0-9])?$")

Sizes = str | dict[str, str]


def check_size(where: str, value: object) -> str:
    if not isinstance(value, str) or not SIZE_RE.fullmatch(value):
        raise ValueError(
            f"{where}: {value!r} is not a size name "
            "(lowercase letters, digits and '-', at most 32, like nano or small-2x)"
        )
    return value


def check_sizes(value: object) -> Sizes:
    """Validate a channel's ``resources``: one size name, or a map from each service name to its size."""
    if isinstance(value, str):
        return check_size("resources", value)
    if not isinstance(value, dict) or not value:
        raise ValueError(
            'resources is a size name ("nano") for a one-service spec, or a map from each service '
            'name to its size ({"web": "small", "redis": "nano"})'
        )
    for service, size in value.items():
        if not isinstance(service, str) or not DNS_LABEL_RE.fullmatch(service):
            raise ValueError(f"resources.{service}: not a valid service name (a DNS label)")
        check_size(f"resources.{service}", size)
    return dict(value)


def service_sizes(resources: Sizes, services: Sequence[str]) -> dict[str, str]:
    """The size of each service of a spec whose services are ``services``: the one size of a
    one-service spec, or the map as given. Raises ``ValueError`` when the form does not fit
    (a size name for several services, a service without a size, a size for a service that is not
    in the spec)."""
    if isinstance(resources, str):
        if len(services) != 1:
            raise ValueError(
                f"the spec has {len(services)} services ({', '.join(services)}): resources must name "
                'each service\'s size, like {"web": "small", "redis": "nano"}'
            )
        return {services[0]: resources}
    if extra := [name for name in resources if name not in services]:
        raise ValueError(f"resources names {', '.join(extra)}, which is not a service of the spec")
    if missing := [name for name in services if name not in resources]:
        raise ValueError(f"resources has no size for {', '.join(missing)}")
    return dict(resources)


class ContainerEntry(BaseModel):
    """A URL the instance is reached at: one service port (PROVISIONAL, #105 open 1 and 2).

    Whether the instance needs URLs beyond ``servers``, whether they belong in
    the channel, and which ``auth`` values apply are not decided. ``gateway``
    is deliberately not a value (open 2): do not add it here.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Entry name, unique within the channel; a DNS label")
    service: str = Field(description="The Compose service that serves it")
    port: int = Field(ge=1, le=65535, description="The service's container port")
    path: str | None = Field(default=None, description="Path the entry opens at; starts with /")
    auth: Literal["login", "key", "app"] = Field(
        description="Who may reach it: the customer's login, a UnitySVC key, or no UnitySVC check (the app's own)"
    )

    @field_validator("name", "service")
    @classmethod
    def _dns_label(cls, value: str) -> str:
        if not DNS_LABEL_RE.fullmatch(value):
            raise ValueError(f"{value!r} must be a DNS label: lowercase letters, digits and '-', at most 63")
        return value

    @field_validator("path")
    @classmethod
    def _path(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("/"):
            raise ValueError("path must start with '/'")
        return value
