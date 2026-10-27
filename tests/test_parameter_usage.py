"""Required enrollment parameters must have consumers across service data."""

import json
from pathlib import Path

import pytest

from unitysvc_core.parameter_usage import referenced_param_names, validate_required_parameter_usage
from unitysvc_core.validator import DataValidator


@pytest.mark.parametrize("expression", ["params.name", "params['name']", 'params.get("name")'])
def test_named_reads(expression):
    assert referenced_param_names("{{ " + expression + " }}") == {"name"}


@pytest.mark.parametrize(
    "expression",
    [
        "params",
        "params | tojson",
        "params.items()",
        "params.keys()",
        "params.values()",
        "params.get(key)",
        "params[key]",
    ],
)
def test_dynamic_and_whole_map_reads(expression):
    assert referenced_param_names("{{ " + expression + " }}") is None


def test_strings_comments_and_non_template_text_are_not_consumers():
    assert referenced_param_names(["params.name", "{# params.name #}", '{{ "params.name" }}']) == set()


def listing(**changes):
    return {"status": "ready", "user_parameters_schema": {"required": ["host", "token"]}, **changes}


def channel(**fields):
    """An offering whose one channel carries ``fields``."""
    return {"upstream_access_config": {"plus": fields}}


def test_required_names_in_the_schema_do_not_count_as_use():
    errors = validate_required_parameter_usage(
        listing(), {"upstream_access_config": {"free": {"api_key": "${ customer_secrets.TOKEN }"}}}
    )
    assert len(errors) == 1
    assert "host, token are used by no channel" in errors[0]


@pytest.mark.parametrize(
    ("listing_changes", "offering"),
    [
        ({"display_name": "{{ params.host }} {{ params.token }}"}, {}),
        ({"description": "{{ params.host }}", "summary": "{{ params.token }}"}, {}),
        ({}, {"description": "{{ params.host }} {{ params.token }}", "name": "{{ params.host }}"}),
        # a document's metadata is not its content
        ({"documents": {"Doc": {"description": "{{ params.host }} {{ params.token }}"}}}, {}),
        ({"service_options": {"note": "{{ params.host }} {{ params.token }}"}}, {}),
    ],
)
def test_metadata_is_not_a_consumer(listing_changes, offering):
    """Only channels, interfaces and document content are rendered with
    params; a reference anywhere else must not satisfy the rule (#2649)."""
    errors = validate_required_parameter_usage(listing(**listing_changes), offering)
    assert len(errors) == 1
    assert "host, token are used by no channel" in errors[0]


def test_dynamic_use_in_metadata_does_not_disable_the_rule():
    errors = validate_required_parameter_usage(listing(description="{{ params | tojson }}"), {})
    assert "host, token" in errors[0]


def test_partial_use_is_rejected():
    errors = validate_required_parameter_usage(listing(), channel(base_url="{{ params.host }}"))
    assert "token are used by no channel" in errors[0]


def test_channel_interface_and_document_reads_count_without_proving_attribution():
    data = listing(documents={"example": {"file_content": "{{ params.token }}"}})
    assert validate_required_parameter_usage(data, channel(base_url="{{ params.host }}")) == []
    assert (
        validate_required_parameter_usage(
            listing(
                user_access_interfaces={"canonical": {"base_url": "{{ params.host }}"}},
                documents={"Example": {"file_content": "{{ params.token }}"}},
            ),
            {},
        )
        == []
    )
    # offering-level document content counts too
    assert (
        validate_required_parameter_usage(
            listing(),
            {**channel(base_url="{{ params.host }}"), "documents": {"Doc": {"file_content": "{{ params.token }}"}}},
        )
        == []
    )


def test_required_parameter_can_have_punctuation_in_its_name():
    assert (
        validate_required_parameter_usage(
            listing(user_parameters_schema={"required": ["host-name"]}), channel(host="{{ params['host-name'] }}")
        )
        == []
    )


def test_deprecated_content_can_be_retired():
    assert validate_required_parameter_usage(listing(status="deprecated"), {}) == []
    assert validate_required_parameter_usage(listing(), {"status": "deprecated"}) == []


def test_no_required_parameters():
    assert validate_required_parameter_usage({}, {}) == []


@pytest.mark.parametrize("suffix", ["json", "toml"])
def test_file_validator_checks_paired_offering(tmp_path, suffix):
    schema_dir = Path(__file__).parents[1] / "src/unitysvc_core/schema"
    validator = DataValidator(tmp_path, schema_dir)
    data = listing()
    (tmp_path / "listing.json").write_text(json.dumps(data))
    path = tmp_path / f"offering.{suffix}"
    path.write_text("{}" if suffix == "json" else "")
    errors = validator.validate_required_parameter_usage(data, tmp_path / "listing.json")
    assert "host, token" in errors[0]
    path.write_text(
        json.dumps(channel(host="{{ params.host }}", token="{{ params.token }}"))
        if suffix == "json"
        else '[upstream_access_config.plus]\nhost = "{{ params.host }}"\ntoken = "{{ params.token }}"\n'
    )
    assert validator.validate_required_parameter_usage(data, tmp_path / "listing.json") == []


def test_file_validator_reads_template_documents_like_ingest(tmp_path):
    """The seller SDK uploads a .j2 document's source as file_content, so the
    file validator must count a param used only there, as ingest does."""
    schema_dir = Path(__file__).parents[1] / "src/unitysvc_core/schema"
    validator = DataValidator(tmp_path, schema_dir)
    (tmp_path / "offering.json").write_text(json.dumps(channel(base_url="{{ params.host }}")))
    (tmp_path / "connectivity.sh.j2").write_text('curl "$URL" -H "X-Token: {{ params.token }}"\n')
    data = listing(documents={"Connectivity": {"file_path": "connectivity.sh.j2", "category": "connectivity_test"}})
    (tmp_path / "listing.json").write_text(json.dumps(data))
    assert validator.validate_required_parameter_usage(data, tmp_path / "listing.json") == []
    # a non-template document is uploaded as-is, never rendered: not a consumer
    (tmp_path / "usage.md").write_text("{{ params.token }}")
    data = listing(documents={"Usage": {"file_path": "usage.md"}})
    errors = validator.validate_required_parameter_usage(data, tmp_path / "listing.json")
    assert "token are used by no channel" in errors[0]


def test_validation_pipeline_invokes_the_shared_rule(tmp_path, monkeypatch):
    schema_dir = Path(__file__).parents[1] / "src/unitysvc_core/schema"
    validator = DataValidator(tmp_path, schema_dir)
    path = tmp_path / "listing.json"
    path.write_text(json.dumps({"name": "labs/test"}))
    calls = []

    def check(data, file_path):
        calls.append(file_path)
        return ["unused-param-sentinel"]

    monkeypatch.setattr(validator, "validate_required_parameter_usage", check)
    valid, errors = validator.validate_data_file(path, "listing_v1")
    assert not valid
    assert "unused-param-sentinel" in errors
    assert calls == [path]


def test_invalid_jinja_does_not_bypass_the_rule():
    errors = validate_required_parameter_usage(listing(), channel(base_url="{{ params.host"))
    assert "invalid Jinja template" in errors[0]


def test_invalid_jinja_in_metadata_is_ignored():
    """Metadata is never rendered with params, so it is not parsed either."""
    errors = validate_required_parameter_usage(listing(description="Go: {{ .Name }}"), {})
    assert "host, token are used by no channel" in errors[0]


# Parameters declared on channels (unitysvc/unitysvc#2726) ---------------------------

from unitysvc_core.parameter_usage import validate_channel_parameters  # noqa: E402

_SCHEMA = {"type": "object", "properties": {"folder": {"type": "string"}}, "required": ["folder"]}


def _offering(**channels):
    return {"status": "ready", "upstream_access_config": channels}


def _params(**fields):
    params = {"schema": _SCHEMA, "testing_defaults": {"folder": "ops"}}
    params.update(fields)
    return params


def _plus(base_url="https://u/{{ params.folder }}", **params_fields):
    return {"base_url": base_url, "params": _params(**params_fields)}


def test_a_channel_declares_and_uses_its_parameters():
    offering = _offering(plus=_plus(), byok={"base_url": "https://u", "api_key": "${ customer_secrets.KEY }"})
    assert validate_channel_parameters(offering) == []
    assert validate_required_parameter_usage({"status": "ready"}, offering) == []


def test_two_plans_declare_different_parameters():
    managed_schema = {"type": "object", "properties": {"region": {"type": "string"}}, "required": ["region"]}
    offering = _offering(
        plus=_plus(),
        managed={
            "base_url": "https://u/{{ params.region }}",
            "params": {"schema": managed_schema, "testing_defaults": {"region": "us"}},
        },
    )
    assert validate_channel_parameters(offering) == []
    assert validate_required_parameter_usage({"status": "ready"}, offering) == []


def test_without_channel_declarations_nothing_changes():
    """The listing-level schema keeps applying to the whole service."""
    assert validate_channel_parameters(channel(base_url="{{ params.host }}")) == []


def test_a_channel_may_read_only_parameters_it_declares():
    errors = validate_channel_parameters(
        _offering(plus=_plus(base_url="https://u/{{ params.folder }}/{{ params.region }}"))
    )
    assert any("reads parameter(s) region" in e for e in errors), errors


def test_a_channel_reading_parameters_must_declare_them():
    errors = validate_channel_parameters(_offering(plus=_plus(), other={"base_url": "https://u/{{ params.folder }}"}))
    assert any("upstream_access_config.other reads enrollment parameters" in e for e in errors), errors


def test_dynamic_use_also_needs_a_declaration():
    errors = validate_channel_parameters(_offering(plus=_plus(), other={"set_body": "{{ params | tojson }}"}))
    assert any("upstream_access_config.other" in e for e in errors), errors


def test_required_parameters_need_ops_values():
    errors = validate_channel_parameters(_offering(plus=_plus(testing_defaults={})))
    assert any("folder need a value in upstream_access_config.plus.params.testing_defaults" in e for e in errors)
    errors = validate_channel_parameters(_offering(plus=_plus(testing_defaults=None)))
    assert any("testing_defaults" in e for e in errors), errors


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"schema": ["folder"]}, "params.schema must be a JSON Schema object"),
        ({"ui_schema": []}, "params.ui_schema must be an object"),
        ({"testing_defaults": ["ops"]}, "params.testing_defaults must be an object"),
        ({"ops_testing_parameters": {}}, "unknown keys"),
    ],
)
def test_malformed_channel_declarations_are_refused(fields, error):
    assert any(error in e for e in validate_channel_parameters(_offering(plus=_plus(**fields))))


def test_params_must_be_an_object():
    errors = validate_channel_parameters(_offering(plus={"base_url": "https://u", "params": ["folder"]}))
    assert any('params must be {"schema"' in e for e in errors), errors


def test_a_required_parameter_must_be_read_by_its_own_channel():
    """Another channel reading the same name does not count."""
    offering = _offering(
        plus=_plus(base_url="https://u"),
        other={"base_url": "https://u/{{ params.folder }}", "params": _params(testing_defaults={"folder": "x"})},
    )
    errors = validate_required_parameter_usage({"status": "ready"}, offering)
    assert len(errors) == 1
    assert "upstream_access_config.plus: required parameter(s) folder" in errors[0]


def test_document_content_counts_as_use_for_a_channel():
    offering = _offering(plus=_plus(base_url="https://u"))
    listing = {"status": "ready", "documents": {"Guide": {"file_content": "{{ params.folder }}"}}}
    assert validate_required_parameter_usage(listing, offering) == []


def test_parameters_on_both_the_listing_and_a_channel_are_refused():
    errors = validate_required_parameter_usage(listing(), _offering(plus=_plus()))
    assert len(errors) == 1
    assert "both on the listing" in errors[0]


def test_the_file_validator_checks_channel_parameters(tmp_path):
    service = tmp_path / "provider" / "services" / "svc"
    service.mkdir(parents=True)
    (service / "offering.json").write_text(
        json.dumps(
            {
                "name": "svc",
                "service_type": "llm",
                "summary": "s",
                "description": "d",
                "time_created": "2026-01-01T00:00:00Z",
                "details": {"context_length": None, "parameter_count": None},
                "upstream_access_config": {"plus": _plus(testing_defaults={})},
            }
        )
    )
    schema_dir = Path(__file__).parent.parent / "src" / "unitysvc_core" / "schema"
    is_valid, errors = DataValidator(tmp_path, schema_dir).validate_data_file(service / "offering.json")
    assert not is_valid
    assert any("testing_defaults" in e for e in errors), errors
