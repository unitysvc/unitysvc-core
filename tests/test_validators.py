"""Unit tests for the validator helpers in ``unitysvc_core.models.validators``."""

from __future__ import annotations

import pytest

from unitysvc_core.models.validators import (
    validate_listing_gateway_base_urls,
    validate_listing_jinja_var_references,
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
            # Hierarchical creator/model names (HuggingFace, Replicate,
            # Together, OpenRouter-style providers).
            "Qwen/Qwen2.5-Coder-7B-Instruct",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "openai/gpt-oss-120b",
            "Qwen/Qwen2.5-Coder-7B-Instruct@byok",
            "meta-llama/llama-4-scout-17b-16e-instruct@premium-eu",
        ],
    )
    def test_accepts_valid_identifiers(self, name: str) -> None:
        assert validate_service_identifier(name, "service") == name

    # --- Empty / falsy -----------------------------------------------------

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_service_identifier("", "service")

    # --- Slash handling: hierarchical names accepted, malformed rejected --

    @pytest.mark.parametrize(
        "name",
        [
            "Qwen/Qwen2.5",  # 2-segment creator/model
            "openai/gpt-4/v2",  # 3-segment (rare but allowed structurally)
            "a/b/c/d",  # would fail on segment length, not slash count
        ],
    )
    def test_accepts_or_evaluates_hierarchical_names(self, name: str) -> None:
        """``/`` is permitted; per-segment rules decide acceptance."""
        if "a/b/c" in name:
            # Single-char segments are rejected on length, not slash count
            with pytest.raises(ValueError, match="at least 2 characters"):
                validate_service_identifier(name, "service")
        else:
            assert validate_service_identifier(name, "service") == name

    @pytest.mark.parametrize(
        "name",
        [
            "/leading-slash",
            "trailing-slash/",
            "Qwen//Qwen2.5",  # consecutive '/'
        ],
    )
    def test_rejects_empty_segments(self, name: str) -> None:
        with pytest.raises(ValueError, match="empty segment|empty name"):
            validate_service_identifier(name, "service")

    # --- Multiple @ rejection ---------------------------------------------

    @pytest.mark.parametrize(
        "name",
        [
            "service@byok@premium",
            "model@@variant",
            "ab@cd@ef",
        ],
    )
    def test_rejects_multiple_at(self, name: str) -> None:
        with pytest.raises(ValueError, match="at most one '@'"):
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
    """Coverage for the gateway base_url validator.

    The gateway base_url is almost unconstrained — a provider may route many
    services through one base_url and differentiate by request fields, and specs
    are often rendered from Jinja2 templates. The only rule: a single-character
    first path segment must be ``a`` (single-letter prefixes are reserved for
    platform primitives; only ``/a/<alias>`` is available to listings). Literal
    ``<provider>/<service>`` paths are accepted.
    """

    # --- Valid: routes via {{ service_name }} -----------------------------

    @pytest.mark.parametrize(
        "base_url",
        [
            "${API_GATEWAY_BASE_URL}/{{ service_name }}",
            "${API_GATEWAY_BASE_URL}/{{service_name}}",
            # Whitespace variations inside the Jinja braces.
            "${API_GATEWAY_BASE_URL}/{{  service_name  }}",
            # Static suffix after the identifier.
            "${API_GATEWAY_BASE_URL}/{{ service_name }}/v1/messages",
            # Dynamic per-enrollment suffix.
            "${API_GATEWAY_BASE_URL}/{{ service_name }}/{{ enrollment.code }}",
            "${API_GATEWAY_BASE_URL}/{{ service_name }}/${enrollment.code}",
        ],
    )
    def test_accepts_service_name_var(self, base_url: str) -> None:
        assert validate_listing_gateway_base_urls(_uai(base_url)) == []

    # --- Valid: gateway root / entirely dynamic ---------------------------

    @pytest.mark.parametrize(
        "base_url",
        [
            # Bare gateway root (platform-native interfaces).
            "${API_GATEWAY_BASE_URL}",
            "${API_GATEWAY_BASE_URL}/",
            # Entirely dynamic from the first segment — nothing static to pin.
            "${API_GATEWAY_BASE_URL}/{{ enrollment.code }}",
            "${API_GATEWAY_BASE_URL}/{% if x %}a{% else %}b{% endif %}",
            "${API_GATEWAY_BASE_URL}/${enrollment.code}",
        ],
    )
    def test_accepts_root_or_fully_dynamic(self, base_url: str) -> None:
        assert validate_listing_gateway_base_urls(_uai(base_url)) == []

    # --- Valid: /a/<alias> movable pointer (#1139) ------------------------

    @pytest.mark.parametrize(
        "base_url",
        [
            "${API_GATEWAY_BASE_URL}/a/cohere-latest",
            "${API_GATEWAY_BASE_URL}/a/anthropic/claude-opus-latest",
            "${API_GATEWAY_BASE_URL}/a/cohere-latest@byok",
            "${API_GATEWAY_BASE_URL}/a/cohere-latest/{{ enrollment.code }}",
        ],
    )
    def test_accepts_a_prefix_movable_pointer(self, base_url: str) -> None:
        assert validate_listing_gateway_base_urls(_uai(base_url)) == []

    def test_rejects_bare_a_slash(self) -> None:
        errors = validate_listing_gateway_base_urls(_uai("${API_GATEWAY_BASE_URL}/a/"))
        assert any("incomplete" in e or "empty" in e for e in errors)

    def test_rejects_a_prefix_with_single_char_remainder(self) -> None:
        errors = validate_listing_gateway_base_urls(_uai("${API_GATEWAY_BASE_URL}/a/x"))
        assert any("at least 2 characters" in e for e in errors)

    # --- Non-API-gateway URLs skipped (other validators handle them) ------

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

    # --- Valid: literal multi-character first segments --------------------

    @pytest.mark.parametrize(
        "base_url",
        [
            # Literal <provider>/<service> paths — accepted (a provider may route
            # many services through one base_url and differentiate by request).
            "${API_GATEWAY_BASE_URL}/anthropic",
            "${API_GATEWAY_BASE_URL}/anthropic/claude-opus-4-7",
            "${API_GATEWAY_BASE_URL}/anthropic/claude-opus-4-7@byok",
            "${API_GATEWAY_BASE_URL}/huggingface/Qwen/Qwen2.5-Coder-7B-Instruct",
            "${API_GATEWAY_BASE_URL}/anthropic/{{ params.model }}",
            # Single base_url, differentiate by routing_key/model (parasail).
            "${API_GATEWAY_BASE_URL}/parasail",
            # A multi-character wrapper-stack prefix is just a literal segment.
            "${API_GATEWAY_BASE_URL}/uptime",
            # {{ service_name }} need not be the first segment.
            "${API_GATEWAY_BASE_URL}/anthropic/{{ service_name }}",
        ],
    )
    def test_accepts_literal_and_provider_paths(self, base_url: str) -> None:
        assert validate_listing_gateway_base_urls(_uai(base_url)) == []

    # --- Rejected: reserved single-letter prefixes ------------------------

    @pytest.mark.parametrize(
        "base_url",
        [
            "${API_GATEWAY_BASE_URL}/p/anthropic",
            "${API_GATEWAY_BASE_URL}/p/{{ service_name }}",
            "${API_GATEWAY_BASE_URL}/u/uptime",
            "${API_GATEWAY_BASE_URL}/u/{{ service_name }}",
            "${API_GATEWAY_BASE_URL}/b/my-alerts",
            "${API_GATEWAY_BASE_URL}/m/foo",
        ],
    )
    def test_rejects_reserved_single_letter_prefixes(self, base_url: str) -> None:
        errors = validate_listing_gateway_base_urls(_uai(base_url))
        assert any("reserved single-letter prefix" in e for e in errors)

    # --- Multi-interface error attribution -------------------------------

    def test_reports_per_interface_field_path(self) -> None:
        uai = {
            "good": {"access_method": "http", "base_url": "${API_GATEWAY_BASE_URL}/{{ service_name }}"},
            "bad": {"access_method": "http", "base_url": "${API_GATEWAY_BASE_URL}/p/anthropic"},
        }
        errors = validate_listing_gateway_base_urls(uai)
        assert len(errors) == 1
        assert "user_access_interfaces.bad.base_url" in errors[0]

    # --- Defensive: non-dict inputs --------------------------------------

    @pytest.mark.parametrize("value", [None, {}, "not a dict", []])
    def test_handles_non_dict_inputs(self, value) -> None:
        assert validate_listing_gateway_base_urls(value) == []


class TestServiceNameJinjaVar:
    """``service_name`` is a platform-injected Jinja variable, so a base_url
    referencing ``{{ service_name }}`` must not be flagged as undefined even
    when the listing declares no params / routing_vars."""

    def test_service_name_var_is_defined(self) -> None:
        data = {
            "user_access_interfaces": {
                "default": {"access_method": "http", "base_url": "${API_GATEWAY_BASE_URL}/{{ service_name }}"}
            }
        }
        assert validate_listing_jinja_var_references(data) == []

    def test_service_name_var_with_enrollment_suffix(self) -> None:
        data = {
            "user_access_interfaces": {
                "default": {
                    "access_method": "http",
                    "base_url": "${API_GATEWAY_BASE_URL}/{{ service_name }}/{{ enrollment.code }}",
                }
            },
        }
        assert validate_listing_jinja_var_references(data) == []

    def test_unknown_var_still_flagged(self) -> None:
        data = {
            "user_access_interfaces": {
                "default": {"access_method": "http", "base_url": "${API_GATEWAY_BASE_URL}/{{ bogus_var }}"}
            }
        }
        errors = validate_listing_jinja_var_references(data)
        assert any("undefined" in e for e in errors)
