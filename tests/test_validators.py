"""Unit tests for the validator helpers in ``unitysvc_core.models.validators``."""

from __future__ import annotations

import pytest

from unitysvc_core.models.validators import (
    validate_listing_gateway_base_urls,
    validate_service_identifier,
)


class TestValidateServiceIdentifier:
    """Coverage for the platform service-naming convention enforcer.

    See ``docs/dev-notes/architecture/service-naming-conventions.md`` in
    the unitysvc repo (issue #1138) for the full convention.
    """

    # --- Valid identifiers -------------------------------------------------

    @pytest.mark.parametrize(
        "name",
        [
            "claude-opus-4-7",
            "gpt-4",
            "flux-1-dev-fp8",
            "minimax-m2p7",
            "kimi.k2",
            "service_with_underscore",
            "Service",  # mixed case allowed
            "ab",  # two chars — minimum length
            "service@byok",
            "claude-opus-4-7@byok",
            "gpt-4@premium-eu",
            "model@managed",
            "ab@cd",  # minimum length on both sides
            "service-1.2@variant_3",
        ],
    )
    def test_accepts_valid_identifiers(self, name: str) -> None:
        assert validate_service_identifier(name, "service") == name

    # --- Empty / falsy -----------------------------------------------------

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_service_identifier("", "service")

    # --- Slash rejection ---------------------------------------------------

    @pytest.mark.parametrize(
        "name",
        [
            "models/gpt-4",
            "provider/service",
            "api/v1/completion",
            "a/b/c",
            "/leading-slash",
            "trailing-slash/",
        ],
    )
    def test_rejects_slash_in_identifier(self, name: str) -> None:
        with pytest.raises(ValueError, match="'/' is not allowed"):
            validate_service_identifier(name, "service")

    # --- Multiple @ rejection ---------------------------------------------

    @pytest.mark.parametrize(
        "name",
        [
            "service@byok@premium",
            "model@@variant",
            "a@b@c",
        ],
    )
    def test_rejects_multiple_at(self, name: str) -> None:
        with pytest.raises(ValueError, match="At most one '@'"):
            validate_service_identifier(name, "service")

    # --- Minimum-length rejection ------------------------------------------

    @pytest.mark.parametrize("name", ["a", "x", "z"])
    def test_rejects_single_char_bare_name(self, name: str) -> None:
        with pytest.raises(ValueError, match="at least 2 characters"):
            validate_service_identifier(name, "service")

    @pytest.mark.parametrize("name", ["a@byok", "x@managed"])
    def test_rejects_single_char_bare_name_with_variant(self, name: str) -> None:
        with pytest.raises(ValueError, match="at least 2 characters"):
            validate_service_identifier(name, "service")

    def test_accepts_single_char_variant(self) -> None:
        """The 2-char minimum is on the bare name; the variant tag itself
        has no minimum length beyond the structural pattern."""
        # 'p' as a variant is allowed — variants do not collide with
        # gateway primitive prefixes (they sit after '@', not at the
        # start of a path segment).
        assert validate_service_identifier("service@p", "service") == "service@p"

    # --- Character-set rejection ------------------------------------------

    @pytest.mark.parametrize(
        "name",
        [
            "service with spaces",
            "service\twith\ttab",
            "service\nwith\nnewline",
            "-leading-dash",
            "_leading-underscore",
            ".leading-dot",
            "service#hash",
            "service?query",
            "service:colon",
            "service+plus",
            "service*star",
        ],
    )
    def test_rejects_invalid_characters(self, name: str) -> None:
        with pytest.raises(ValueError, match="Invalid service name"):
            validate_service_identifier(name, "service")

    # --- entity_type substitution -----------------------------------------

    def test_uses_entity_type_in_error_message(self) -> None:
        with pytest.raises(ValueError, match="Invalid listing name"):
            validate_service_identifier("a/b/c", "listing")


def _uai(base_url: str) -> dict:
    """Build a minimal ``user_access_interfaces`` dict for testing."""
    return {"default": {"access_method": "http", "base_url": base_url}}


class TestValidateListingGatewayBaseUrls:
    """Coverage for the gateway base_url validator that enforces the
    ``<provider>[/<service-name>][@<variant>]`` grammar."""

    # --- Valid base_urls --------------------------------------------------

    @pytest.mark.parametrize(
        "base_url",
        [
            "${API_GATEWAY_BASE_URL}/anthropic",
            "${API_GATEWAY_BASE_URL}/cohere",
            "${API_GATEWAY_BASE_URL}/fireworks.ai",
            "${API_GATEWAY_BASE_URL}/anthropic/claude-opus-4-7",
            "${API_GATEWAY_BASE_URL}/anthropic/claude-opus-4-7@byok",
            "${API_GATEWAY_BASE_URL}/cohere/gpt-4@premium-eu",
            # Bare gateway root (used by some platform-native interfaces)
            "${API_GATEWAY_BASE_URL}",
            "${API_GATEWAY_BASE_URL}/",
        ],
    )
    def test_accepts_valid_base_urls(self, base_url: str) -> None:
        assert validate_listing_gateway_base_urls(_uai(base_url)) == []

    # --- Jinja templates: validate the static prefix --------------------

    @pytest.mark.parametrize(
        "base_url",
        [
            # Entirely Jinja after the prefix — skipped (no static identifier).
            "${API_GATEWAY_BASE_URL}/{{ provider_name }}",
            "${API_GATEWAY_BASE_URL}/{% if x %}a{% else %}b{% endif %}",
            # Static prefix is a valid 2-segment identifier; trailing
            # Jinja is dynamic per-enrollment data.
            "${API_GATEWAY_BASE_URL}/anthropic/{{ params.model }}",
            "${API_GATEWAY_BASE_URL}/demo/echo-enrollment-vars/{{ enrollment_vars.code }}",
            "${API_GATEWAY_BASE_URL}/ntfy/{{ enrollment_vars.topic }}",
            "${API_GATEWAY_BASE_URL}/notify/discord-relay/{{ enrollment_vars.code }}",
        ],
    )
    def test_accepts_valid_static_prefix_with_jinja_suffix(self, base_url: str) -> None:
        assert validate_listing_gateway_base_urls(_uai(base_url)) == []

    def test_validates_static_prefix_even_when_jinja_follows(self) -> None:
        """A bug in the static prefix (e.g. single-char segment) must still
        be reported, even when a Jinja template appears later in the path.
        Real-world example: ``/u/uptime/{{ code }}`` — ``u`` is single-char."""
        errors = validate_listing_gateway_base_urls(
            _uai("${API_GATEWAY_BASE_URL}/u/uptime/{{ enrollment_vars.code }}")
        )
        # `u` is single-char (catches the primitive-prefix collision) AND
        # the static prefix has 2 slashes (catches the path-depth rule);
        # both findings are useful.
        assert any("at least 2 characters" in e for e in errors)

    # --- Non-gateway URLs skipped ----------------------------------------

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://api.openai.com/v1",
            "${S3_GATEWAY_BASE_URL}/my-bucket",
            "${SMTP_GATEWAY_BASE_URL}",
            "${ customer_secrets.HTTP_RELAY_BASE_URL }",
        ],
    )
    def test_skips_non_api_gateway_urls(self, base_url: str) -> None:
        assert validate_listing_gateway_base_urls(_uai(base_url)) == []

    # --- Slash count rejection --------------------------------------------

    @pytest.mark.parametrize(
        "base_url",
        [
            "${API_GATEWAY_BASE_URL}/anthropic/v1/messages",
            "${API_GATEWAY_BASE_URL}/p/anthropic/v1",
            "${API_GATEWAY_BASE_URL}/a/b/c/d",
        ],
    )
    def test_rejects_too_many_slashes(self, base_url: str) -> None:
        errors = validate_listing_gateway_base_urls(_uai(base_url))
        assert any("multiple '/'" in e for e in errors)

    # --- Single-character segment rejection -------------------------------

    @pytest.mark.parametrize(
        "base_url",
        [
            "${API_GATEWAY_BASE_URL}/a",  # single-char provider
            "${API_GATEWAY_BASE_URL}/p/anthropic",  # legacy /p/ — p is single-char
            "${API_GATEWAY_BASE_URL}/anthropic/x",  # single-char service-name
        ],
    )
    def test_rejects_single_char_segments(self, base_url: str) -> None:
        errors = validate_listing_gateway_base_urls(_uai(base_url))
        assert any("at least 2 characters" in e for e in errors)

    # --- Multiple @ rejection ---------------------------------------------

    def test_rejects_multiple_at_signs(self) -> None:
        errors = validate_listing_gateway_base_urls(_uai("${API_GATEWAY_BASE_URL}/anthropic/claude@byok@premium"))
        assert any("multiple '@'" in e for e in errors)

    # --- Empty / malformed segments --------------------------------------

    @pytest.mark.parametrize(
        "base_url",
        [
            "${API_GATEWAY_BASE_URL}/anthropic@",  # empty variant
            "${API_GATEWAY_BASE_URL}/-anthropic",  # leading dash
            "${API_GATEWAY_BASE_URL}/anthropic/_service",  # leading underscore
        ],
    )
    def test_rejects_malformed_segments(self, base_url: str) -> None:
        errors = validate_listing_gateway_base_urls(_uai(base_url))
        assert len(errors) > 0

    # --- Multi-interface error attribution -------------------------------

    def test_reports_per_interface_field_path(self) -> None:
        uai = {
            "good": {"access_method": "http", "base_url": "${API_GATEWAY_BASE_URL}/anthropic"},
            "bad": {"access_method": "http", "base_url": "${API_GATEWAY_BASE_URL}/p/anthropic"},
        }
        errors = validate_listing_gateway_base_urls(uai)
        assert len(errors) == 1
        assert "user_access_interfaces.bad.base_url" in errors[0]

    # --- Defensive: non-dict inputs --------------------------------------

    @pytest.mark.parametrize("value", [None, {}, "not a dict", []])
    def test_handles_non_dict_inputs(self, value) -> None:
        assert validate_listing_gateway_base_urls(value) == []
