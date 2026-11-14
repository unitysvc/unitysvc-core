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
from unitysvc_core.models import ContainerChannel, ContainerResources, ContainerTierResources, OfferingV1
from unitysvc_core.models.container import resource_quantity
from unitysvc_core.validator import DataValidator

EXAMPLE = Path(__file__).parents[1] / "examples/container/specs/hashicorp/echo"


def offering_data():
    return json.loads((EXAMPLE / "offering.json").read_text())


def allocation():
    return {"cpu": "250m", "memory": "256Mi", "ephemeral_disk": "1Gi"}


def source():
    return load_container_spec(EXAMPLE / "compose.yaml")


def test_two_tiers_compile_to_runner_manifest():
    raw = offering_data()
    offering = OfferingV1.model_validate(raw)
    validate_channel_access_requests(raw["upstream_access_config"])
    for name, channel in offering.upstream_access_config.root.items():
        assert isinstance(channel, ContainerChannel) and channel.type == "enrollable"
        manifest = normalize_container_spec(source(), resources=channel.resources, entries=channel.entries)
        expected_cpu = "250m" if name == "base" else "1"
        limits = manifest.services[0].resources.limits
        assert limits["cpu"] == expected_cpu
        assert limits["ephemeral-storage"] == ("1Gi" if name == "base" else "2Gi")
        assert manifest.services[0].resources.requests == limits
        assert manifest.services[0].health.command == ["/http-echo", "-version"]
        assert (
            manifest.content_hash()
            == normalize_container_spec(source(), resources=channel.resources, entries=channel.entries).content_hash()
        )
    assert offering.service_type.value == "container"


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
        normalize_container_spec(spec, resources=allocation())


def test_host_ports_and_mutable_images_are_rejected():
    spec = source()
    spec["services"]["web"]["ports"] = ["8080:5678"]
    with pytest.raises(ValueError, match="host/published"):
        normalize_container_spec(spec, resources=allocation())
    spec = source()
    spec["services"]["web"]["image"] = "hashicorp/http-echo:latest"
    with pytest.raises(ValidationError, match="image"):
        normalize_container_spec(spec, resources=allocation())


def test_multi_service_allocations_are_explicit_and_include_replicas():
    spec = source()
    spec["services"]["web"]["deploy"] = {"replicas": 2}
    spec["services"]["worker"] = {"image": spec["services"]["web"]["image"]}
    budget = {"cpu": "1", "memory": "1Gi", "ephemeral_disk": "4Gi"}
    with pytest.raises(ValueError, match="explicitly allocate"):
        normalize_container_spec(spec, resources=budget)
    budget["services"] = {
        "web": allocation(),
        "worker": {"cpu": "100m", "memory": "128Mi", "ephemeral_disk": "512Mi"},
    }
    manifest = normalize_container_spec(spec, resources=budget)
    assert manifest.resource_totals()["cpu"] == Decimal("0.6")
    assert manifest.resource_totals()["memory"] == 640 * 1024**2
    assert manifest.resource_totals()["ephemeral-storage"] == Decimal("2.5") * 1024**3
    budget["cpu"] = "500m"
    with pytest.raises(ValueError, match="exceed the channel"):
        normalize_container_spec(spec, resources=budget)
    budget["cpu"] = "1"
    budget["services"].pop("worker")
    with pytest.raises(ValueError, match="every Compose service"):
        normalize_container_spec(spec, resources=budget)


def test_channel_resources_use_seller_units_and_reject_unsupported_hardware():
    assert ContainerTierResources.model_validate(allocation()).quantities()["cpu"] == Decimal("0.25")
    for change in ({"cpu": "0"}, {"cpu": "0.1m"}, {"memory": "-1Gi"}, {"ephemeral_disk": "0"}, {"nvidia.com/gpu": "1"}):
        with pytest.raises(ValueError):
            ContainerTierResources.model_validate({**allocation(), **change})


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
    resources = {"limits": {"cpu": "250m", "memory": "256Mi", "ephemeral-storage": "1Gi"}}
    resources["limits"].update(change)
    with pytest.raises(ValueError):
        ContainerResources.model_validate(resources)


def test_requests_limits_and_extended_resources():
    resources = {"limits": {"cpu": "250m", "memory": "256Mi", "ephemeral-storage": "1Gi"}}
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
        normalize_container_spec(source(), resources=channel.resources, entries=[bad_entry])


def test_compose_exec_and_native_health():
    spec = source()
    spec["services"]["web"]["healthcheck"] = {"test": ["CMD-SHELL", "test -f /ready"], "interval": "2s"}
    result = normalize_container_spec(spec, resources=allocation())
    assert result.services[0].health.command == ["/bin/sh", "-c", "test -f /ready"]
    spec["services"]["web"].pop("healthcheck")
    spec["services"]["web"]["health"] = {"type": "http", "path": "/", "port": 5678}
    assert normalize_container_spec(spec, resources=allocation()).services[0].health.type == "http"


def test_first_port_order_is_preserved_for_fallback_health():
    spec = source()
    spec["services"]["web"]["ports"] = [9000, "8000/tcp"]
    spec["services"]["web"]["expose"] = [9000, 5678]
    assert normalize_container_spec(spec, resources=allocation()).services[0].ports == [9000, 8000, 5678]


def test_yaml_booleans_are_not_ports_or_replica_counts():
    for field, value in (("ports", [True]), ("deploy", {"replicas": True})):
        spec = source()
        spec["services"]["web"][field] = value
        with pytest.raises(ValidationError):
            normalize_container_spec(spec, resources=allocation())


def test_schemas_accept_the_examples():
    schema_dir = Path(__file__).parents[1] / "src" / "unitysvc_core" / "schema"
    jsonschema.validate(offering_data(), json.loads((schema_dir / "offering_v1.json").read_text()))
    jsonschema.validate(source(), json.loads((schema_dir / "container_spec_v1.json").read_text()))
    manifest = normalize_container_spec(source(), resources=allocation()).runtime_dict()
    jsonschema.validate(manifest, json.loads((schema_dir / "container_manifest_v1.json").read_text()))


def test_container_execution_does_not_force_service_classification():
    data = copy.deepcopy(offering_data())
    data["service_type"] = "llm"
    assert OfferingV1.model_validate(data).service_type.value == "llm"


def test_complete_service_folder_uses_existing_provider_offering_and_listing_validation():
    from unitysvc_core.utils import load_data_file

    provider, _ = load_data_file(EXAMPLE / "provider.json")
    listing, _ = load_data_file(EXAMPLE / "listing.json")
    schemas = Path(__file__).parents[1] / "src/unitysvc_core/schema"
    errors = DataValidator(EXAMPLE, schemas).validate_service(
        provider=provider, offering=offering_data(), listing=listing, service_dir=EXAMPLE
    )
    assert errors == {"provider": [], "offering": [], "listing": []}
    assert listing["name"] == provider["name"] + "/" + offering_data()["name"]
    assert "user_access_interfaces" not in listing


def test_spec_references_a_typed_offering_document_and_entries_declare_access():
    channel = OfferingV1.model_validate(offering_data()).upstream_access_config.root["base"]
    assert channel.servers[0].spec == "Application"
    assert channel.entries[0].auth == "enrollment_key" and channel.entries[0].path == "/"
    data = offering_data()
    data["upstream_access_config"]["base"]["spec"] = "compose.yaml"
    with pytest.raises(ValueError, match="container_spec document"):
        OfferingV1.model_validate(data)
    data = offering_data()
    data["upstream_access_config"]["base"]["entries"][0]["auth"] = "public"
    with pytest.raises(ValidationError):
        OfferingV1.model_validate(data)


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
