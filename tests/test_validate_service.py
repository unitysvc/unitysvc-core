"""``DataValidator.validate_service``: one service's files validated in memory."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from jsonschema.validators import Draft7Validator

from unitysvc_core.utils import load_data_file
from unitysvc_core.validator import DataValidator

SCHEMA_DIR = Path(__file__).parents[1] / "src" / "unitysvc_core" / "schema"
EXAMPLES = Path(__file__).parent / "example_data"
SERVICES = [
    EXAMPLES / "provider1" / "services" / "service1",
    EXAMPLES / "provider2" / "services" / "service2",
]


def _files(service_dir: Path) -> dict[str, dict]:
    files: dict[str, dict] = {}
    for kind in ("offering", "listing"):
        path = next(service_dir.glob(f"{kind}.*"))
        files[kind], _ = load_data_file(path)
    provider_path = next(service_dir.parents[1].glob("provider.*"))
    files["provider"], _ = load_data_file(provider_path)
    return files


@pytest.fixture
def validator() -> DataValidator:
    return DataValidator(EXAMPLES, SCHEMA_DIR)


@pytest.mark.parametrize("service_dir", SERVICES, ids=lambda p: p.name)
def test_same_result_as_validating_each_file(validator: DataValidator, service_dir: Path) -> None:
    files = _files(service_dir)
    result = validator.validate_service(offering=files["offering"], listing=files["listing"], service_dir=service_dir)

    for kind in ("offering", "listing"):
        path = next(service_dir.glob(f"{kind}.*"))
        ok, errors = validator.validate_data_file(path)
        assert result[kind] == errors
        assert ok


def test_only_the_given_files_are_validated(validator: DataValidator) -> None:
    files = _files(SERVICES[0])
    assert set(validator.validate_service(listing=files["listing"])) == {"listing"}


def test_the_listing_supplies_the_offerings_routing_vars(validator: DataValidator) -> None:
    offering = {"upstream_access_config": {"api": {"api_key": "${ secrets.{{ routing_vars.region }}_KEY }"}}}

    without = validator.validate_service(offering=offering)["offering"]
    assert any("Jinja expansion failed" in e for e in without)

    listing = {"service_options": {"routing_vars": {"region": "US"}}}
    with_listing = validator.validate_service(offering=offering, listing=listing)["offering"]
    assert not any("Jinja expansion failed" in e for e in with_listing)


def test_the_provider_governs_the_listing_namespace(validator: DataValidator) -> None:
    listing = {"name": "other/svc"}

    errors = validator.validate_service(provider={"name": "mine"}, listing=listing)["listing"]
    assert any("first segment must be the provider slug 'mine'" in e for e in errors)

    errors = validator.validate_service(listing=listing)["listing"]
    assert not any("first segment" in e for e in errors)


def test_the_offering_decides_required_parameter_usage(validator: DataValidator) -> None:
    params = {"schema": {"required": ["host"]}, "testing_defaults": {"host": "h"}}
    offering = {"upstream_access_config": {"plus": {"base_url": "https://u", "params": params}}}

    errors = validator.validate_service(offering=offering, listing={"name": "svc"})["listing"]
    assert any("required parameter(s) host" in e for e in errors)

    listing = {"name": "svc", "documents": {"Guide": {"file_content": "{{ params.host }}"}}}
    errors = validator.validate_service(offering=offering, listing=listing)["listing"]
    assert not any("required parameter(s) host" in e for e in errors)


def test_without_a_folder_relative_references_are_not_resolved(validator: DataValidator, tmp_path: Path) -> None:
    listing = {"name": "svc", "documents": {"Guide": {"file_path": "missing.md", "category": "usage"}}}

    in_memory = validator.validate_service(listing=listing)["listing"]
    assert not any("does not exist" in e for e in in_memory)

    on_disk = validator.validate_service(listing=listing, service_dir=tmp_path)["listing"]
    assert any("'missing.md'" in e and "does not exist" in e for e in on_disk)


def test_schemas_are_checked_once_not_per_file(validator: DataValidator, monkeypatch: pytest.MonkeyPatch) -> None:
    """Checking a schema itself (and compiling its validator) happens when the
    schemas load; it used to run for every file validated."""
    checked: list[object] = []
    monkeypatch.setattr(Draft7Validator, "check_schema", classmethod(lambda cls, schema, **kw: checked.append(schema)))
    for service_dir in SERVICES:
        files = _files(service_dir)
        validator.validate_service(**files, service_dir=service_dir)
        validator.validate_data_file(next(service_dir.glob("offering.*")))

    assert checked == []


def test_services_validate_in_parallel_with_one_validator(validator: DataValidator) -> None:
    jobs = [(service_dir, _files(service_dir)) for service_dir in SERVICES] * 8
    expected = [validator.validate_service(**files, service_dir=d) for d, files in jobs]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda job: validator.validate_service(**job[1], service_dir=job[0]), jobs))

    assert results == expected
