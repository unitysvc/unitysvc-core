"""The seller container contract compiles to the existing Go runner's wire shape."""

import copy
import json
from decimal import Decimal
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from unitysvc_core.containers import load_container_spec, normalize_container_spec
from unitysvc_core.grants import validate_channel_access_requests
from unitysvc_core.models import ContainerChannel, ContainerResources, OfferingV1
from unitysvc_core.models.container import resource_quantity
from unitysvc_core.validator import DataValidator

EXAMPLE = Path(__file__).parents[1] / "examples" / "container"


def offering_data():
    return json.loads((EXAMPLE / "offering.json").read_text())


def allocation():
    return {"limits": {"cpu": "250m", "memory": "256Mi", "ephemeral-storage": "1Gi"}}


def source():
    return load_container_spec(EXAMPLE / "compose.yaml")


def test_two_tiers_compile_to_runner_manifest():
    raw = offering_data()
    offering = OfferingV1.model_validate(raw)
    validate_channel_access_requests(raw["upstream_access_config"])
    for name, channel in offering.upstream_access_config.root.items():
        assert isinstance(channel, ContainerChannel) and channel.type == "enrollable"
        manifest = normalize_container_spec(source(), resources=channel.servers[0].resources, entries=channel.entries)
        assert manifest.runtime_dict() == json.loads((EXAMPLE / f"{name}.manifest.json").read_text())
        assert manifest.services[0].health.command == ["/http-echo", "-version"]
        assert (
            manifest.content_hash()
            == normalize_container_spec(
                source(), resources=channel.servers[0].resources, entries=channel.entries
            ).content_hash()
        )
    assert offering.service_type.value == "compute"


@pytest.mark.parametrize(
    "field,value",
    [
        ("privileged", True),
        ("volumes", ["/host:/data"]),
        ("build", "."),
        ("network_mode", "host"),
        ("devices", ["/dev/nvidia0"]),
        ("depends_on", ["database"]),
    ],
)
def test_unsafe_or_unimplemented_compose_fields_are_rejected(field, value):
    spec = source()
    spec["services"]["web"][field] = value
    with pytest.raises(ValidationError, match=field):
        normalize_container_spec(spec, resources={"web": allocation()})


def test_host_ports_and_mutable_images_are_rejected():
    spec = source()
    spec["services"]["web"]["ports"] = ["8080:5678"]
    with pytest.raises(ValueError, match="host/published"):
        normalize_container_spec(spec, resources={"web": allocation()})
    spec = source()
    spec["services"]["web"]["image"] = "hashicorp/http-echo:latest"
    with pytest.raises(ValidationError, match="image"):
        normalize_container_spec(spec, resources={"web": allocation()})


def test_every_service_gets_exactly_its_tier_allocation():
    with pytest.raises(ValueError, match="every Compose service"):
        normalize_container_spec(source(), resources={"wrong": allocation()})
    spec = source()
    spec["services"]["web"]["deploy"] = {"replicas": 2}
    manifest = normalize_container_spec(spec, resources={"web": allocation()})
    assert manifest.resource_totals()["cpu"] == Decimal("0.5")
    assert manifest.resource_totals()["memory"] == 512 * 1024**2
    assert manifest.resource_totals()["ephemeral-storage"] == 2 * 1024**3


@pytest.mark.parametrize(
    "change",
    [
        {"cpu": "0"},
        {"cpu": "0.1m"},
        {"memory": "-1Gi"},
        {"nvidia.com/gpu": "0.5"},
        {"hugepages-2Mi": "64Mi"},
        {"kubernetes.io/foo": "1"},
        {"disk": "1Gi"},
    ],
)
def test_resource_validation(change):
    resources = allocation()
    resources["limits"].update(change)
    with pytest.raises(ValueError):
        ContainerResources.model_validate(resources)


def test_requests_limits_and_extended_resources():
    resources = allocation()
    resources["limits"]["nvidia.com/gpu"] = "1"
    resolved = ContainerResources.model_validate(resources)
    assert resolved.requests == resolved.limits
    resources["requests"] = {"cpu": "1"}
    with pytest.raises(ValueError, match="exceeds"):
        ContainerResources.model_validate(resources)
    with pytest.raises(ValueError, match="missing"):
        ContainerResources(limits={"cpu": "1", "memory": "1Gi"})
    assert resource_quantity("1e3") == 1000
    assert resource_quantity("1Gi") == 1024**3


def test_entry_document_and_enrollment_validation():
    data = offering_data()
    data["upstream_access_config"]["base"]["type"] = "unbound"
    with pytest.raises(ValidationError):
        OfferingV1.model_validate(data)
    for change in ("missing document", "wrong category", "missing consent"):
        data = offering_data()
        if change == "missing document":
            data["documents"] = {}
        elif change == "wrong category":
            data["documents"]["Application"]["category"] = "other"
        else:
            data["upstream_access_config"]["base"]["customer_access_requests"] = []
        with pytest.raises(ValidationError):
            OfferingV1.model_validate(data)
    channel = OfferingV1.model_validate(offering_data()).upstream_access_config.root["base"]
    bad_entry = channel.entries[0].model_copy(update={"port": 9999})
    with pytest.raises(ValueError, match="declared service port"):
        normalize_container_spec(source(), resources=channel.servers[0].resources, entries=[bad_entry])


def test_compose_exec_and_native_health():
    spec = source()
    spec["services"]["web"]["healthcheck"] = {"test": ["CMD-SHELL", "test -f /ready"], "interval": "2s"}
    result = normalize_container_spec(spec, resources={"web": allocation()})
    assert result.services[0].health.command == ["/bin/sh", "-c", "test -f /ready"]
    spec["services"]["web"].pop("healthcheck")
    spec["services"]["web"]["health"] = {"type": "http", "path": "/", "port": 5678}
    assert normalize_container_spec(spec, resources={"web": allocation()}).services[0].health.type == "http"


def test_first_port_order_is_preserved_for_fallback_health():
    spec = source()
    spec["services"]["web"]["ports"] = [9000, "8000/tcp"]
    spec["services"]["web"]["expose"] = [9000, 5678]
    assert normalize_container_spec(spec, resources={"web": allocation()}).services[0].ports == [9000, 8000, 5678]


def test_yaml_booleans_are_not_ports_or_replica_counts():
    for field, value in (("ports", [True]), ("deploy", {"replicas": True})):
        spec = source()
        spec["services"]["web"][field] = value
        with pytest.raises(ValidationError):
            normalize_container_spec(spec, resources={"web": allocation()})


def test_schemas_accept_the_examples():
    schema_dir = Path(__file__).parents[1] / "src" / "unitysvc_core" / "schema"
    jsonschema.validate(offering_data(), json.loads((schema_dir / "offering_v1.json").read_text()))
    jsonschema.validate(source(), json.loads((schema_dir / "container_spec_v1.json").read_text()))
    manifest = json.loads((EXAMPLE / "base.manifest.json").read_text())
    jsonschema.validate(manifest, json.loads((schema_dir / "container_manifest_v1.json").read_text()))


def test_container_execution_does_not_force_service_classification():
    data = copy.deepcopy(offering_data())
    data["service_type"] = "llm"
    assert OfferingV1.model_validate(data).service_type.value == "llm"


def test_file_validation_compiles_the_referenced_document(tmp_path):
    data = offering_data()
    (tmp_path / "offering.json").write_text(json.dumps(data))
    (tmp_path / "compose.yaml").write_text((EXAMPLE / "compose.yaml").read_text())
    schemas = Path(__file__).parents[1] / "src/unitysvc_core/schema"
    validator = DataValidator(tmp_path, schemas)
    assert validator.validate_data_file(tmp_path / "offering.json") == (True, [])
    invalid = source()
    invalid["services"]["web"]["privileged"] = True
    (tmp_path / "compose.json").write_text(json.dumps(invalid))
    data["documents"]["Application"].update(file_path="compose.json", mime_type="json")
    (tmp_path / "offering.json").write_text(json.dumps(data))
    valid, errors = validator.validate_data_file(tmp_path / "offering.json")
    assert not valid and any("container channel" in error and "privileged" in error for error in errors)
