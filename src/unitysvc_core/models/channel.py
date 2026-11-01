"""Upstream access channels: ``upstream_access_config`` and its channels and servers.

An offering's ``upstream_access_config`` maps a channel name to a channel. A
channel describes one way the platform reaches the upstream, as a whole: its
protocol (``access_method``), rank, the formats it serves, what it asks the
enrolling customer for (``params``, ``customer_access_requests``), and its
``servers``. Each server holds the connection itself, typed by the channel's
protocol (unitysvc/unitysvc#2730)::

    "upstream_access_config": {
      "plus": {
        "access_method": "http",
        "params": {
          "schema": {"type": "object", "properties": {"folder": {"type": "string"}}, "required": ["folder"]},
          "testing_defaults": {"folder": "ops"}
        },
        "servers": [{"base_url": "https://u/{{ params.folder }}", "api_key": "${ secrets.KEY }"}]
      }
    }

The common one-server case may write the connection fields directly on the
channel; they are read as ``servers[0]``. When a channel has both, a server
inherits each direct field it does not set itself, the gateway's rule.

Any string value may be a template (``{{ params.X }}``, ``${ secrets.X }``,
``${ customer_secrets.X ?? default }``), so a numeric or boolean connection
field also accepts a string.
"""

import builtins
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    GetJsonSchemaHandler,
    RootModel,
    Tag,
    field_validator,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from .validators import validate_channel_name

# A string field that is a value or a template rendering to one.
_Templated = str


class ChannelParams(BaseModel):
    """The enrollment parameters a channel asks the customer for (unitysvc/unitysvc#2726).

    The channel's templates read them as ``{{ params.NAME }}``.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    json_schema: dict[str, Any] = Field(
        alias="schema",
        description="JSON Schema of the parameters; a property's own `default` pre-fills the customer's form",
    )
    ui_schema: dict[str, Any] | None = Field(default=None, description="Form hints (rjsf uiSchema)")
    testing_defaults: dict[str, Any] | None = Field(
        default=None,
        description="The values the platform tests the channel with; every required parameter needs one",
    )


class RequestTransformer(BaseModel):
    """How the gateway rewrites a request before sending it upstream."""

    model_config = ConfigDict(extra="forbid")

    llm_translator: str | dict[str, Any] | None = Field(
        default=None, description="Translate between LLM request dialects (a preset name or its config)"
    )
    msg_transformer: dict[str, Any] | None = Field(default=None, description="Map the standard msg envelope")
    proxy_rewrite: dict[str, Any] | None = Field(default=None, description="Rewrite the upstream path and headers")
    body_transformer: dict[str, Any] | None = Field(default=None, description="Rewrite the request body")
    set_body: str | None = Field(default=None, description="Replace the request body with this template")


class _Server(BaseModel):
    """Fields every server has, whatever its protocol."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, description="Server name, unique within the channel")
    weight: int | None = Field(default=None, description="Relative share of traffic among the channel's servers")
    acceptable_formats: list[str] | None = Field(default=None, description="Request formats this server accepts")


class HttpServer(_Server):
    """An HTTP upstream, or a platform service named by a scheme such as ``notify://`` or ``me://``."""

    base_url: _Templated = Field(description="Upstream base URL")
    api_key: _Templated | None = Field(
        default=None,
        description="A secret reference, or one of '', '__strip__', '__forward__', '__sigv4__'",
    )
    routing_key: dict[str, Any] | None = Field(default=None, description="Values the gateway routes on, e.g. model")
    upstream_format: str | None = Field(default=None, description="The request format the upstream speaks")
    request_transformer: RequestTransformer | None = None
    # AWS SigV4 signing (api_key '__sigv4__')
    access_key: _Templated | None = None
    secret_key: _Templated | None = None
    session_token: _Templated | None = None
    region: _Templated | None = None
    sigv4_service: str | None = None
    # Delivery to an HTTP endpoint (smtp-gateway, notify)
    method: str | None = None
    payload_format: _Templated | None = Field(default=None, description="Body format sent upstream, e.g. msg")
    auth_mode: str | None = Field(default=None, description="forward, static, customer_token or none")
    auth_token: _Templated | None = None
    headers: dict[str, _Templated] | None = None
    max_message_bytes: int | _Templated | None = None
    tls_verify: bool | _Templated | None = None
    # notify://smtp delivery
    smtp_target: _Templated | None = None
    smtp_host: _Templated | None = None
    smtp_port: int | _Templated | None = None
    smtp_tls: bool | _Templated | None = None
    smtp_username: _Templated | None = None
    smtp_password: _Templated | None = None
    mail_from: _Templated | None = None
    mail_to: _Templated | None = None


class SmtpServer(_Server):
    """An SMTP relay."""

    host: _Templated
    port: int | _Templated | None = None
    username: _Templated | None = None
    password: _Templated | None = None
    tls: bool | _Templated | None = None
    to: _Templated | None = Field(default=None, description="Recipient that replaces the message's own")


class S3Server(_Server):
    """An S3-compatible bucket."""

    bucket: _Templated
    region: _Templated | None = None
    s3_endpoint: _Templated | None = None
    access_key: _Templated | None = None
    secret_key: _Templated | None = None
    session_token: _Templated | None = None
    base_path: _Templated | None = Field(default=None, description="Key prefix inside the bucket")
    addressing_style: Literal["path", "virtual"] | None = None
    proxy_mode: Literal["proxy", "redirect"] | None = None


class McpServer(_Server):
    """A remote MCP server (streamable HTTP)."""

    base_url: _Templated
    auth_mode: str | None = Field(default=None, description="customer_token, forward, static or none")
    api_key: _Templated | None = None
    credential: _Templated | None = None
    headers: dict[str, _Templated] | None = None
    allowed_hosts: list[str] | None = None


# Derived by the platform. An app with no channel at all (an external app,
# unitysvc/unitysvc#2873) is not a channel type: it declares no channel.
ChannelType = Literal["unbound", "secret_bound", "enrollable", "me_bound"]

# Keys a channel no longer takes, with what to do instead.
RETIRED_CHANNEL_KEYS: dict[str, str] = {
    "raw": "move its keys onto the channel (or server) itself",
    "is_primary": "channels rank by 'sort_order' (lowest first); remove 'is_primary'",
    "user_parameters_schema": "declare the channel's parameters as params.schema",
    "user_parameters_ui_schema": "move it to params.ui_schema",
    "ops_testing_parameters": "move it to params.testing_defaults",
    "grants": "renamed to 'customer_access_requests'",
    "rate_limits": "nothing reads a channel's rate_limits; declare limits on the provider and name them in "
    "'rate_limit_refs', or remove it",
    "storage_type": "set access_method 's3' instead",
    "response_rules": "no gateway reads it; remove it",
    "timeout_ms": "no gateway reads it; remove it",
    "transport": "no gateway reads it; remove it",
    "customer_secrets_required": "the platform derives it at upload; remove it",
    "customer_secrets_optional": "the platform derives it at upload; remove it",
}


class _ChannelBase(BaseModel):
    """What describes a channel as a whole. Connection fields live in ``servers``."""

    model_config = ConfigDict(extra="forbid")

    type: ChannelType | None = Field(
        default=None,
        description="Normally derived by the platform; declare 'enrollable' to state it",
    )
    description: str | None = None
    is_active: bool = True
    sort_order: int | None = Field(default=None, description="Channel rank, lowest first")
    request_formats: list[str] | None = Field(default=None, description="Request formats the channel serves")
    rate_limit_refs: list[str] | None = Field(
        default=None, description="Names of the provider's rate limits this channel consumes"
    )
    applicable_interfaces: list[str] | None = Field(
        default=None, description="The user access interfaces that reach this channel (display only)"
    )
    customer_access_requests: list[dict[str, Any]] | None = Field(
        default=None, description="What the enrolling customer is asked to approve; see unitysvc_core.grants"
    )
    params: ChannelParams | None = None

    # The server type of this channel's protocol; set by each subclass.
    server_model: ClassVar[builtins.type[_Server]] = _Server

    @model_validator(mode="before")
    @classmethod
    def _lift_connection_fields(cls, data: Any) -> Any:
        """Refuse retired keys, then read direct connection fields as ``servers``."""
        if not isinstance(data, dict):
            return data
        if retired := [key for key in RETIRED_CHANNEL_KEYS if key in data]:
            raise ValueError("; ".join(f"'{key}' is not supported: {RETIRED_CHANNEL_KEYS[key]}" for key in retired))
        server_keys = set(cls.server_model.model_fields)
        direct = {key: value for key, value in data.items() if key in server_keys}
        if not direct:
            return data
        channel = {key: value for key, value in data.items() if key not in server_keys}
        servers = data.get("servers")
        if servers is None:
            channel["servers"] = [direct]
        elif isinstance(servers, list):
            # A server inherits each direct field it does not set (the gateway's rule).
            channel["servers"] = [{**direct, **server} if isinstance(server, dict) else server for server in servers]
        return channel

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        """Also list the server's fields on the channel, where the one-server case writes them."""
        json_schema = handler(core_schema)
        channel = handler.resolve_ref_schema(json_schema)
        server = handler.resolve_ref_schema(handler(cls.server_model.__pydantic_core_schema__))
        properties = channel.setdefault("properties", {})
        for name, field_schema in server.get("properties", {}).items():
            properties.setdefault(name, field_schema)
        return json_schema


class HttpChannel(_ChannelBase):
    server_model: ClassVar[builtins.type[_Server]] = HttpServer
    access_method: Literal["http"] = "http"
    servers: list[HttpServer] = Field(default_factory=list)


class SmtpChannel(_ChannelBase):
    server_model: ClassVar[builtins.type[_Server]] = SmtpServer
    access_method: Literal["smtp"]
    servers: list[SmtpServer] = Field(default_factory=list)


class S3Channel(_ChannelBase):
    server_model: ClassVar[builtins.type[_Server]] = S3Server
    access_method: Literal["s3"]
    servers: list[S3Server] = Field(default_factory=list)


class McpChannel(_ChannelBase):
    server_model: ClassVar[builtins.type[_Server]] = McpServer
    access_method: Literal["mcp"]
    servers: list[McpServer] = Field(default_factory=list)


def _access_method(value: Any) -> str:
    if isinstance(value, dict):
        method = value.get("access_method", "http")
        return method if isinstance(method, str) else ""
    return getattr(value, "access_method", "")


ChannelConfig = Annotated[
    Annotated[HttpChannel, Tag("http")]
    | Annotated[SmtpChannel, Tag("smtp")]
    | Annotated[S3Channel, Tag("s3")]
    | Annotated[McpChannel, Tag("mcp")],
    Discriminator(
        _access_method,
        custom_error_type="invalid_access_method",
        custom_error_message="access_method must be one of 'http', 'smtp', 's3', 'mcp'",
    ),
]
"""One upstream access channel; ``access_method`` (default ``http``) selects its server type."""


class UpstreamAccessConfig(RootModel[dict[str, ChannelConfig]]):
    """An offering's channels, keyed by channel name."""

    @field_validator("root")
    @classmethod
    def _validate_names(cls, value: dict[str, Any]) -> dict[str, Any]:
        for name in value:
            validate_channel_name(name)
        return value
