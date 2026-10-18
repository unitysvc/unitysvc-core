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
