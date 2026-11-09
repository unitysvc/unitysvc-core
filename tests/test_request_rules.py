"""Request rules: the channel field and what core checks of it (unitysvc/unitysvc#2947).

Conditions are Lua expressions compiled by the gateway (apisix-gateways#94);
core does not parse them.
"""

import pytest
from pydantic import ValidationError

from unitysvc_core.models import HttpChannel, RequestRule, UpstreamAccessConfig
from unitysvc_core.models.channel import MAX_CONDITION_BYTES, MAX_RULES


def _channel(config: dict):
    return UpstreamAccessConfig.model_validate({"default": config}).root["default"]


def _rules_channel(*rules: dict) -> HttpChannel:
    channel = _channel({"base_url": "https://api.example.com", "request_rules": list(rules)})
    assert isinstance(channel, HttpChannel)
    return channel


class TestCondition:
    @staticmethod
    def _rule(**fields: object) -> RequestRule:
        return RequestRule.model_validate({"respond": {"status": 204}, **fields})

    @pytest.mark.parametrize(
        "when",
        [
            'header["x-github-event"] == "ping"',
            'body.type == "ping" or string.find(body.text or "", params.MARKER, 1, true) ~= nil',
            "not body.author or body.author.bot == true",
            "(body.n or 0) > 3 and method == 'POST'",
            "this is not even Lua",  # the gateway reports syntax, core does not
        ],
    )
    def test_any_lua_text_is_accepted_unparsed(self, when: str) -> None:
        assert self._rule(when=when).when == when

    @pytest.mark.parametrize("when", ["", "   ", "\n\t"])
    def test_an_empty_condition_is_refused(self, when: str) -> None:
        with pytest.raises(ValidationError, match="must not be empty"):
            self._rule(when=when)

    def test_length_is_measured_in_utf8_bytes(self) -> None:
        assert self._rule(when="x" * MAX_CONDITION_BYTES).when
        with pytest.raises(ValidationError, match="longer than"):
            self._rule(when="测" * (MAX_CONDITION_BYTES // 3 + 1))

    @pytest.mark.parametrize(
        "when",
        [
            "body.id == '${ customer_secrets.HOOK ?? }'",
            "body.id == '${ secrets.TOKEN }'",
            "body.name == '{{ params.marker }}'",
        ],
    )
    def test_template_references_are_refused(self, when: str) -> None:
        with pytest.raises(ValidationError, match="takes no template references"):
            self._rule(when=when)

    def test_declared_customer_secrets(self) -> None:
        rule = self._rule(when='secret_equals("HOOK_ID", body.user_id)', customer_secrets=["HOOK_ID"])
        assert rule.customer_secrets == ["HOOK_ID"]

    @pytest.mark.parametrize(
        ("names", "message"),
        [
            ([], "1 to 8 names"),
            ([f"S{i}" for i in range(9)], "1 to 8 names"),
            (["hook_id"], "UPPER_SNAKE_CASE"),
            (["HOOK-ID"], "UPPER_SNAKE_CASE"),
            (["HOOK_ID\n"], "UPPER_SNAKE_CASE"),
            (["A", "A"], "twice"),
        ],
    )
    def test_bad_customer_secret_lists(self, names: list[str], message: str) -> None:
        with pytest.raises(ValidationError, match=message):
            self._rule(when="true", customer_secrets=names)


class TestChannelField:
    def test_rules_are_a_channel_field(self) -> None:
        channel = _rules_channel(
            {"name": "github-ping", "when": 'header["x-github-event"] == "ping"', "respond": {"status": 200}},
            {"respond": {"status": 503, "body": {"error": "paused"}}},
        )
        assert channel.request_rules is not None
        assert [rule.name for rule in channel.request_rules] == ["github-ping", None]
        assert channel.request_rules[1].when is None
        # Not lifted into the server like base_url or request_transformer.
        assert "request_rules" not in channel.servers[0].model_dump(exclude_none=True)

    def test_round_trip(self) -> None:
        rule = {
            "name": "echo",
            "when": 'secret_equals("DISCORD_WEBHOOK_ID", body.user_id)',
            "customer_secrets": ["DISCORD_WEBHOOK_ID"],
            "respond": {"status": 204},
        }
        dumped = _rules_channel(rule).model_dump(exclude_none=True)
        assert dumped["request_rules"] == [rule]

    def test_bad_condition_names_its_rule(self) -> None:
        with pytest.raises(ValidationError, match=r"request_rules\.0\.when"):
            _rules_channel({"when": "body.a == '{{ params.a }}'", "respond": {"status": 204}})

    @pytest.mark.parametrize(
        "config",
        [
            {"access_method": "smtp", "host": "smtp.example.com"},
            {"access_method": "s3", "bucket": "b"},
            {"access_method": "mcp", "base_url": "https://mcp.example.com/mcp"},
        ],
    )
    def test_only_http_channels_take_rules(self, config: dict) -> None:
        with pytest.raises(ValidationError, match="request_rules"):
            _channel({**config, "request_rules": [{"respond": {"status": 204}}]})

    def test_rule_count_limits(self) -> None:
        with pytest.raises(ValidationError, match="at least 1"):
            _channel({"base_url": "https://x", "request_rules": []})
        with pytest.raises(ValidationError, match=f"at most {MAX_RULES}"):
            _rules_channel(*[{"respond": {"status": 204}}] * (MAX_RULES + 1))

    def test_rule_names_are_unique(self) -> None:
        with pytest.raises(ValidationError, match="unique: a"):
            _rules_channel({"name": "a", "respond": {"status": 204}}, {"name": "a", "respond": {"status": 200}})

    def test_rule_name_pattern(self) -> None:
        with pytest.raises(ValidationError, match="name"):
            RequestRule.model_validate({"name": "Bad Name", "respond": {"status": 204}})

    def test_unknown_rule_key(self) -> None:
        with pytest.raises(ValidationError, match="if"):
            RequestRule.model_validate({"if": "method == 'GET'", "respond": {"status": 204}})


class TestResponse:
    @staticmethod
    def _respond(respond: dict) -> RequestRule:
        return RequestRule.model_validate({"respond": respond})

    def test_json_and_string_bodies(self) -> None:
        assert self._respond({"status": 400, "body": {"error": "x"}}).respond.body == {"error": "x"}
        rule = self._respond({"status": 200, "body": "<Response/>", "content_type": "application/xml"})
        assert rule.respond.content_type == "application/xml"

    @pytest.mark.parametrize("status", [100, 101, 199, 600])
    def test_status_range(self, status: int) -> None:
        with pytest.raises(ValidationError, match="status"):
            self._respond({"status": status})

    def test_status_is_required(self) -> None:
        with pytest.raises(ValidationError, match="status"):
            self._respond({})

    @pytest.mark.parametrize("status", [204, 205, 304])
    def test_no_body_statuses(self, status: int) -> None:
        with pytest.raises(ValidationError, match="has no body"):
            self._respond({"status": status, "body": {"a": 1}})

    def test_string_body_needs_content_type(self) -> None:
        with pytest.raises(ValidationError, match="needs content_type"):
            self._respond({"status": 200, "body": "ok"})

    def test_content_type_only_with_string_body(self) -> None:
        with pytest.raises(ValidationError, match="only for a string body"):
            self._respond({"status": 200, "body": {"a": 1}, "content_type": "application/json"})

    @pytest.mark.parametrize(
        "content_type",
        [
            "text/plain\r\nx-a: b",
            "text/plain\n",
            "text/plain\r\n; charset=utf-8",
            "text/plain\v; charset=utf-8",
            "text/plain\u00a0; charset=utf-8",
        ],
    )
    def test_bad_content_type(self, content_type: str) -> None:
        with pytest.raises(ValidationError, match="not a media type"):
            self._respond({"status": 200, "body": "ok", "content_type": content_type})

    @pytest.mark.parametrize("content_type", ["text/plain; charset=utf-8", "text/plain\t; charset=utf-8"])
    def test_content_type_horizontal_whitespace(self, content_type: str) -> None:
        rule = self._respond({"status": 200, "body": "ok", "content_type": content_type})
        assert rule.respond.content_type == content_type

    @pytest.mark.parametrize("body", [1, True])
    def test_scalar_body_is_refused(self, body: object) -> None:
        with pytest.raises(ValidationError):
            self._respond({"status": 200, "body": body})

    def test_body_size_limit(self) -> None:
        with pytest.raises(ValidationError, match="larger than"):
            self._respond({"status": 400, "body": {"error": "x" * 9000}})

    @pytest.mark.parametrize(
        "name", ["Content-Length", "transfer-encoding", "Connection", "Set-Cookie", "Content-Type", "X-UnitySVC-Rule"]
    )
    def test_refused_headers(self, name: str) -> None:
        with pytest.raises(ValidationError, match="managed by the gateway"):
            self._respond({"status": 200, "headers": {name: "x"}})

    def test_header_value_must_be_one_line(self) -> None:
        with pytest.raises(ValidationError, match="one line"):
            self._respond({"status": 200, "headers": {"X-Note": "a\r\nSet-Cookie: b"}})

    @pytest.mark.parametrize("name", ["X Note", "X-Note\n", "Content-Length\n"])
    def test_header_name_must_be_a_token(self, name: str) -> None:
        with pytest.raises(ValidationError, match="not valid"):
            self._respond({"status": 200, "headers": {name: "a"}})

    def test_allowed_header(self) -> None:
        assert self._respond({"status": 200, "headers": {"Cache-Control": "no-store"}}).respond.headers == {
            "Cache-Control": "no-store"
        }
