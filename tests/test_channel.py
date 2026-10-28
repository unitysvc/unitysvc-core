"""``UpstreamAccessConfig`` / ``ChannelConfig``: the typed channel model (unitysvc/unitysvc#2730)."""

import pytest
from pydantic import ValidationError

from unitysvc_core.models import (
    HttpChannel,
    HttpServer,
    McpChannel,
    S3Channel,
    SmtpChannel,
    SmtpServer,
    UpstreamAccessConfig,
)


def _channel(config: dict, name: str = "default"):
    return UpstreamAccessConfig.model_validate({name: config}).root[name]


class TestProtocols:
    def test_access_method_defaults_to_http(self) -> None:
        channel = _channel({"base_url": "https://api.example.com"})
        assert isinstance(channel, HttpChannel)
        assert channel.servers == [HttpServer(base_url="https://api.example.com")]

    @pytest.mark.parametrize(
        ("config", "model"),
        [
            ({"access_method": "smtp", "host": "smtp.example.com", "port": 587}, SmtpChannel),
            ({"access_method": "s3", "bucket": "b", "region": "us-east-1"}, S3Channel),
            ({"access_method": "mcp", "base_url": "https://mcp.example.com/mcp", "auth_mode": "none"}, McpChannel),
        ],
    )
    def test_access_method_selects_the_server_type(self, config: dict, model: type) -> None:
        assert isinstance(_channel(config), model)

    def test_unknown_access_method_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="access_method must be one of"):
            _channel({"access_method": "grpc", "base_url": "https://x"})

    def test_a_field_of_another_protocol_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="bucket"):
            _channel({"access_method": "smtp", "host": "h", "bucket": "b"})

    def test_templated_numbers_and_booleans(self) -> None:
        channel = _channel(
            {
                "access_method": "smtp",
                "host": "${ customer_secrets.HOST }",
                "port": "${ customer_secrets.PORT ?? 587 }",
                "tls": "{{ params.tls }}",
            }
        )
        assert channel.servers == [
            SmtpServer(
                host="${ customer_secrets.HOST }", port="${ customer_secrets.PORT ?? 587 }", tls="{{ params.tls }}"
            )
        ]


class TestServers:
    def test_direct_fields_are_one_server(self) -> None:
        channel = _channel({"base_url": "https://a", "api_key": "${ secrets.K }", "sort_order": 1})
        assert channel.sort_order == 1
        assert [server.model_dump(exclude_none=True) for server in channel.servers] == [
            {"base_url": "https://a", "api_key": "${ secrets.K }"}
        ]

    def test_servers_inherit_direct_fields_they_do_not_set(self) -> None:
        channel = _channel(
            {
                "api_key": "${ secrets.K }",
                "servers": [
                    {"name": "a", "base_url": "https://a"},
                    {"name": "b", "base_url": "https://b", "api_key": ""},
                ],
            }
        )
        assert [server.api_key for server in channel.servers] == ["${ secrets.K }", ""]

    def test_a_channel_without_connection_fields_has_no_servers(self) -> None:
        # A platform service's channel is routed across its members.
        assert _channel({"description": "Routed by the platform"}).servers == []

    def test_unknown_request_transformer_key_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="request_transformer"):
            _channel({"base_url": "https://a", "request_transformer": {"rewrite_everything": {}}})


class TestEnvelope:
    def test_params(self) -> None:
        channel = _channel(
            {
                "base_url": "https://u/{{ params.folder }}",
                "params": {
                    "schema": {"type": "object", "properties": {"folder": {"type": "string"}}, "required": ["folder"]},
                    "testing_defaults": {"folder": "ops"},
                },
            }
        )
        assert channel.params is not None
        assert channel.params.json_schema["required"] == ["folder"]
        assert channel.params.model_dump(by_alias=True, exclude_none=True)["schema"]["required"] == ["folder"]

    def test_params_refuses_unknown_keys(self) -> None:
        with pytest.raises(ValidationError, match="defaults"):
            _channel({"base_url": "https://u", "params": {"schema": {}, "defaults": {}}})

    def test_unknown_key_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="colour"):
            _channel({"base_url": "https://u", "colour": "blue"})

    @pytest.mark.parametrize(
        ("key", "hint"),
        [
            ("raw", "onto the channel"),
            ("is_primary", "sort_order"),
            ("user_parameters_schema", "params.schema"),
            ("ops_testing_parameters", "params.testing_defaults"),
            ("rate_limits", "rate_limit_refs"),
            ("storage_type", "access_method 's3'"),
            ("timeout_ms", "remove it"),
            ("grants", "customer_access_requests"),
            ("customer_secrets_required", "derives it"),
        ],
    )
    def test_retired_keys_say_what_to_do(self, key: str, hint: str) -> None:
        with pytest.raises(ValidationError, match=f"'{key}' is not supported: .*{hint}"):
            _channel({"base_url": "https://u", key: "x"})

    def test_raw_on_a_server_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="raw"):
            _channel({"servers": [{"base_url": "https://a"}, {"base_url": "https://b", "raw": {}}]})

    def test_channel_names_are_validated(self) -> None:
        with pytest.raises(ValidationError, match="@"):
            UpstreamAccessConfig.model_validate({"a@b": {"base_url": "https://u"}})
