"""Request rules: the condition language and the channel field (unitysvc/unitysvc#2947)."""

import json
from importlib import resources

import pytest
from pydantic import ValidationError

from unitysvc_core.models import HttpChannel, RequestRule, UpstreamAccessConfig
from unitysvc_core.rules import (
    MAX_CONDITION_LENGTH,
    MAX_RULES,
    Condition,
    Ref,
    RuleSyntaxError,
    Slot,
    evaluate,
    parse_condition,
)

_CASES = json.loads(resources.files("unitysvc_core").joinpath("rules_conformance.json").read_text())["cases"]


def _channel(config: dict):
    return UpstreamAccessConfig.model_validate({"default": config}).root["default"]


def _rules_channel(*rules: dict) -> HttpChannel:
    channel = _channel({"base_url": "https://api.example.com", "request_rules": list(rules)})
    assert isinstance(channel, HttpChannel)
    return channel


class TestConformance:
    """The cases the gateway's evaluator must also pass."""

    def test_case_names_are_unique(self) -> None:
        names = [case["name"] for case in _CASES]
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("case", _CASES, ids=[case["name"] for case in _CASES])
    def test_case(self, case: dict) -> None:
        if "error" in case:
            with pytest.raises(RuleSyntaxError) as excinfo:
                parse_condition(case["when"])
            assert case["error"] in str(excinfo.value)
        else:
            condition = parse_condition(case["when"])
            assert evaluate(condition, case.get("request", {}), case.get("slots")) is case["match"]


class TestParse:
    def test_parsed_shape(self) -> None:
        assert parse_condition("header['X-Event'] == 'ping'") == Condition("==", Ref("header", ("x-event",)), "ping")
        assert parse_condition("'[bot]' in body.sender.login") == Condition(
            "in", Ref("body", ("sender", "login")), "[bot]", literal_first=True
        )

    def test_slots(self) -> None:
        condition = parse_condition("body.id == '${ customer_secrets.HOOK_ID ?? none }'")
        assert condition.literal == Slot("customer_secrets", "HOOK_ID", "none")
        assert condition.literal.is_secret
        param = parse_condition("'{{ params.marker }}' in body.name").literal
        assert param == Slot("params", "marker") and not param.is_secret

    def test_condition_length_limit(self) -> None:
        with pytest.raises(RuleSyntaxError, match="longer than"):
            parse_condition("body.a == '" + "x" * MAX_CONDITION_LENGTH + "'")

    def test_ref_depth_limit(self) -> None:
        with pytest.raises(RuleSyntaxError, match="deeper than"):
            parse_condition("body" + ".a" * 17 + " == 'x'")

    def test_non_string_is_refused(self) -> None:
        with pytest.raises(RuleSyntaxError, match="non-empty string"):
            parse_condition(None)  # type: ignore[arg-type]


class TestChannelField:
    def test_rules_are_a_channel_field(self) -> None:
        channel = _rules_channel(
            {"name": "github-ping", "when": "header['x-github-event'] == 'ping'", "respond": {"status": 200}},
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
            "when": "body.user_id == '${ customer_secrets.DISCORD_WEBHOOK_ID ?? }'",
            "respond": {"status": 204},
        }
        dumped = _rules_channel(rule).model_dump(exclude_none=True)
        assert dumped["request_rules"] == [rule]

    def test_bad_condition_names_its_rule(self) -> None:
        with pytest.raises(ValidationError, match=r"request_rules\.0\.when"):
            _rules_channel({"when": "body.a != 'b'", "respond": {"status": 204}})

    def test_secret_under_in_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="may only be compared with '=='"):
            _rules_channel(
                {"when": "body.user_id in '${ customer_secrets.DISCORD_WEBHOOK_URL ?? }'", "respond": {"status": 204}}
            )

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
