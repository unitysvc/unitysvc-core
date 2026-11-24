"""The ``container`` channel, the ``instance_spec`` document and the Compose policy (unitysvc-core#105)."""

from __future__ import annotations

import copy
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft7Validator
from pydantic import ValidationError

from unitysvc_core.instance_spec import (
    INSTANCE_SPEC_TITLE,
    InstanceSpecError,
    compile_instance_spec,
    expand_instance_spec,
    validate_instance_spec,
)
from unitysvc_core.models.channel import ContainerChannel, UpstreamAccessConfig
from unitysvc_core.models.container import ResourceSet, check_resolved, check_sizes, parse_quantity, service_sizes
from unitysvc_core.models.documents import DocumentData
from unitysvc_core.models.manifest import NormalizedManifest
from unitysvc_core.models.probes import validate_probes_text
from unitysvc_core.validator import DataValidator

ROOT = Path(__file__).parent
EXAMPLES = ROOT / "example_data"
ECHO = EXAMPLES / "hashicorp" / "services" / "echo"
WEIXIN = EXAMPLES / "kadxy" / "services" / "weixin-ilink-gateway"
WEIXIN_UPSTREAM = EXAMPLES / "kadxy" / "services" / "weixin-ilink-gateway-upstream"
FIXTURES = ROOT / "fixtures"
SCHEMA_DIR = ROOT.parent / "src" / "unitysvc_core" / "schema"
DIGEST = "@sha256:" + "a" * 64
REQUEST = {
    "object": "resource:container",
    "permissions": ["create"],
    "required": True,
    "justification": "Run an instance",
}
LIMITS = {"cpu": "250m", "memory": "256Mi", "ephemeral-storage": "1Gi"}


def _channel(**changes: Any) -> dict[str, Any]:
    channel: dict[str, Any] = {
        "access_method": "container",
        "resources": "nano",
    }
    channel.update(changes)
    return channel


def _compose(**service: Any) -> str:
    base: dict[str, Any] = {"image": f"example/app{DIGEST}", "ports": ["8080"]}
    base.update(service)
    return yaml.safe_dump({"services": {"web": base}})


def _codes(report: Any, kind: str = "errors") -> set[str]:
    return {issue.code for issue in getattr(report, kind)}


# --- quantities and resources --------------------------------------------------------------------


class TestQuantities:
    @pytest.mark.parametrize(
        ("text", "value"),
        [
            ("1", 1),
            ("250m", Fraction(1, 4)),
            ("0.5", Fraction(1, 2)),
            ("1k", 1000),
            ("1Ki", 1024),
            ("256Mi", 256 * 1024**2),
            ("1G", 10**9),
            ("1e3", 1000),
            ("1E", 10**18),
            ("12e-1", Fraction(6, 5)),
        ],
    )
    def test_parses_as_kubernetes_does(self, text: str, value: Any) -> None:
        assert parse_quantity(text) == value

    @pytest.mark.parametrize("text", ["", "K", "ki", "1KB", "1 Gi", "Gi", "1.2.3", "abc", "1mi"])
    def test_refuses_what_kubernetes_refuses(self, text: str) -> None:
        with pytest.raises(ValueError):
            parse_quantity(text)


def _resolved(**changes: Any) -> ResourceSet:
    """What the platform resolves a size to, checked as compile checks it."""
    return check_resolved(ResourceSet.model_validate({"limits": dict(LIMITS), **changes}))


def _res(**changes: Any) -> dict[str, ResourceSet]:
    """The resolved resources of ``_compose``'s one service, ``web``."""
    return {"web": _resolved(**changes)}


# What the tests' platform maps each size to: the table is the platform's, core never sees it.
SIZES = {
    "nano": {"limits": {"cpu": "250m", "memory": "256Mi", "ephemeral-storage": "1Gi"}},
    "small": {"limits": {"cpu": "1", "memory": "1Gi", "ephemeral-storage": "2Gi"}},
}


def _resolve(sizes: Any, text: str) -> dict[str, ResourceSet]:
    """Resolve a channel's ``resources`` for a spec, the way the platform does."""
    services = list(yaml.safe_load(text)["services"])
    return {name: ResourceSet.model_validate(SIZES[size]) for name, size in service_sizes(sizes, services).items()}


class TestResources:
    """The resources the platform resolves a size to: Kubernetes' own shape, checked by core."""

    def test_the_size_needs_cpu_memory_and_storage(self) -> None:
        with pytest.raises(ValueError, match="must set ephemeral-storage"):
            check_resolved(ResourceSet.model_validate({"limits": {"cpu": "1", "memory": "1Gi"}}))

    def test_only_the_reserved_names(self) -> None:
        with pytest.raises(ValueError, match="unknown resource"):
            _resolved(limits={**LIMITS, "hugepages-2Mi": "1Gi"})

    def test_gpu_is_whole_and_its_request_equals_the_limit(self) -> None:
        _resolved(limits={**LIMITS, "nvidia.com/gpu": "1"})
        with pytest.raises(ValueError, match="whole number"):
            _resolved(limits={**LIMITS, "nvidia.com/gpu": "500m"})
        with pytest.raises(ValueError, match="must equal the limit"):
            _resolved(limits={**LIMITS, "nvidia.com/gpu": "2"}, requests={"nvidia.com/gpu": "1"})

    def test_a_request_is_at_most_its_limit(self) -> None:
        with pytest.raises(ValueError, match="above the limit"):
            _resolved(requests={"cpu": "1"})
        _resolved(requests={"cpu": "100m"})

    def test_quantities_must_be_positive_and_valid(self) -> None:
        with pytest.raises(ValueError, match="not a Kubernetes quantity"):
            _resolved(limits={**LIMITS, "memory": "1KB"})
        with pytest.raises(ValueError, match="positive"):
            _resolved(limits={**LIMITS, "cpu": "0"})


class TestSizes:
    """A channel's ``resources`` names sizes; core checks the form, the platform owns the table."""

    @pytest.mark.parametrize("name", ["nano", "small", "small-2x", "small-largedisk", "a", "c-4"])
    def test_a_descriptive_name_is_a_size(self, name: str) -> None:
        assert check_sizes(name) == name
        ContainerChannel.model_validate(_channel(resources=name))

    @pytest.mark.parametrize("name", ["", "Nano", "-small", "small-", "has space", "a" * 33, "small_2x"])
    def test_anything_else_is_not(self, name: str) -> None:
        with pytest.raises(ValueError, match="not a size name"):
            check_sizes(name)

    def test_a_map_gives_each_service_its_size(self) -> None:
        sizes = {"web": "small", "redis": "nano"}
        assert check_sizes(sizes) == sizes
        assert ContainerChannel.model_validate(_channel(resources=sizes)).resources == sizes

    @pytest.mark.parametrize(
        "value", [{}, [], None, 3, {"Web": "small"}, {"web": ""}, {"web": 1}, {"web": {"cpu": "1"}}]
    )
    def test_other_forms_are_refused(self, value: Any) -> None:
        with pytest.raises(ValueError):
            check_sizes(value)
        with pytest.raises(ValidationError):
            ContainerChannel.model_validate(_channel(resources=value))

    def test_the_old_kubernetes_block_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="size name"):
            ContainerChannel.model_validate(_channel(resources={"limits": dict(LIMITS)}))

    def test_one_size_is_the_size_of_the_only_service(self) -> None:
        assert service_sizes("nano", ["web"]) == {"web": "nano"}

    def test_one_size_does_not_fit_several_services(self) -> None:
        with pytest.raises(ValueError, match="2 services .*web, redis"):
            service_sizes("nano", ["web", "redis"])

    def test_a_map_names_every_service_and_no_other(self) -> None:
        assert service_sizes({"web": "small", "redis": "nano"}, ["web", "redis"]) == {"web": "small", "redis": "nano"}
        with pytest.raises(ValueError, match="no size for redis"):
            service_sizes({"web": "small"}, ["web", "redis"])
        with pytest.raises(ValueError, match="ghost, which is not a service"):
            service_sizes({"web": "small", "ghost": "nano"}, ["web"])

    def test_an_entry_needs_a_size_when_sizes_are_a_map(self) -> None:
        entry = {"name": "api", "service": "worker", "port": 80, "auth": "key"}
        with pytest.raises(ValidationError, match="service 'worker' has no size"):
            ContainerChannel.model_validate(_channel(resources={"web": "small"}, entries=[entry]))
        ContainerChannel.model_validate(_channel(resources="nano", entries=[entry]))

    @pytest.mark.parametrize(
        ("field", "value"),
        [("interval", "10m"), ("timeout", "10m"), ("retries", 31), ("start_period", "3m")],
    )
    def test_health_timings_stay_within_the_agents_bounds(self, field: str, value: Any) -> None:
        health = {"test": ["CMD", "x"], field: value}
        report = validate_instance_spec(_compose(healthcheck=health))
        assert not report.ok
        ok = {"interval": "5m", "timeout": "5m", "retries": 30, "start_period": "2m"}[field]
        assert validate_instance_spec(_compose(healthcheck={"test": ["CMD", "x"], field: ok})).ok


class TestParameterReferences:
    """``{{ params.NAME }}`` belongs in the channel's ``environment``, not in the Compose file (unitysvc#3023)."""

    @pytest.mark.parametrize(
        "service",
        [
            {"environment": {"A": "{{ params.X }}"}},
            {"environment": ["A={{params.X}}"]},
            {"image": "{{ params.X }}"},
            {"command": ["run", "{{ params.X }}"]},
        ],
    )
    def test_the_file_may_not_carry_one(self, service: dict[str, Any]) -> None:
        assert "bad_template" in _codes(validate_instance_spec(_compose(**service)))

    def test_other_braces_are_left_alone(self) -> None:
        assert validate_instance_spec(_compose(healthcheck={"test": ["CMD", "echo", "{{.State.Status}}"]})).ok

    def test_a_channel_may_set_environment_from_its_params(self) -> None:
        channel = {**_channel(), "environment": {"web": {"API_KEY": "{{ params.KEY }}", "MODE": "x-{{params.M}}"}}}
        assert ContainerChannel.model_validate(channel).environment == channel["environment"]

    @pytest.mark.parametrize(
        "environment",
        [
            {"web": {"A": "{{ other }}"}},
            {"web": {"A": "{{ params.X | upper }}"}},
            {"Web!": {"A": "1"}},
            {"web": {"1A": "1"}},
            {"web": {}},
        ],
    )
    def test_a_bad_channel_environment_is_refused(self, environment: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ContainerChannel.model_validate({**_channel(), "environment": environment})


# --- the channel ----------------------------------------------------------------------------------


class TestChannel:
    def test_is_in_the_union(self) -> None:
        config = UpstreamAccessConfig.model_validate({"base": _channel()})
        channel = config.root["base"]
        assert isinstance(channel, ContainerChannel)
        assert channel.servers == []

    def test_requires_resources(self) -> None:
        data = _channel()
        del data["resources"]
        with pytest.raises(ValidationError, match="resources"):
            ContainerChannel.model_validate(data)

    def test_a_container_channel_needs_no_access_request(self) -> None:
        """The customer runs their own instance and grants the seller nothing (unitysvc#3023)."""
        channel = ContainerChannel.model_validate(_channel())
        assert not channel.customer_access_requests

    def test_the_request_passes_the_grants_checks(self, tmp_path: Path) -> None:
        (tmp_path / "compose.yaml").write_text(_compose())
        errors = DataValidator(EXAMPLES, SCHEMA_DIR).validate_service(
            offering=_offering({"base": _channel(customer_access_requests=[REQUEST])}), service_dir=tmp_path
        )
        assert errors["offering"] == []

    def test_a_resource_request_takes_no_options_or_limits(self, tmp_path: Path) -> None:
        (tmp_path / "compose.yaml").write_text(_compose())
        request = {**REQUEST, "options": "kind=chat"}
        offering = _offering({"base": _channel(customer_access_requests=[request])})
        errors = DataValidator(EXAMPLES, SCHEMA_DIR).validate_service(offering=offering, service_dir=tmp_path)
        assert any("resource:container" in e for e in errors["offering"])

    def test_always_enrollable(self) -> None:
        ContainerChannel.model_validate(_channel(type="enrollable"))
        with pytest.raises(ValidationError, match="always enrollable"):
            ContainerChannel.model_validate(_channel(type="unbound"))

    def test_servers_are_plain_http_servers(self) -> None:
        channel = ContainerChannel.model_validate(
            _channel(servers=[{"base_url": "https://{{ enrollment.code }}.unitysvc.app/"}])
        )
        assert channel.servers[0].base_url == "https://{{ enrollment.code }}.unitysvc.app/"
        direct = ContainerChannel.model_validate(_channel(base_url="https://x.example/"))
        assert direct.servers[0].base_url == "https://x.example/"

    def test_entries(self) -> None:
        entry = {"name": "web", "service": "web", "port": 8080, "auth": "key"}
        channel = ContainerChannel.model_validate(_channel(entries=[entry]))
        assert channel.entries and channel.entries[0].auth == "key"
        for auth in ("gateway", "none", ""):
            with pytest.raises(ValidationError):
                ContainerChannel.model_validate(_channel(entries=[{**entry, "auth": auth}]))
        with pytest.raises(ValidationError, match="unique"):
            ContainerChannel.model_validate(_channel(entries=[entry, entry]))
        with pytest.raises(ValidationError, match="DNS label"):
            ContainerChannel.model_validate(_channel(entries=[{**entry, "name": "Web_1"}]))
        with pytest.raises(ValidationError):
            ContainerChannel.model_validate(_channel(entries=[{**entry, "port": 0}]))

    def test_params_are_as_on_other_channels(self) -> None:
        params = {"schema": {"type": "object", "properties": {"n": {"type": "string"}}}, "testing_defaults": {"n": "a"}}
        assert ContainerChannel.model_validate(_channel(params=params)).params is not None

    def test_unknown_keys_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            ContainerChannel.model_validate(_channel(image="x"))


# --- documents ------------------------------------------------------------------------------------


class TestDocument:
    def doc(self, **meta: Any) -> dict[str, Any]:
        data: dict[str, Any] = {"category": "instance_spec", "mime_type": "text", "file_path": "compose.yaml"}
        if meta:
            data["meta"] = meta
        return data

    def test_the_format_defaults_to_compose(self) -> None:
        DocumentData.model_validate(self.doc())
        DocumentData.model_validate(self.doc(format="compose"))

    def test_other_formats_are_refused_clearly(self) -> None:
        with pytest.raises(ValidationError, match="'helm' is not supported; the only format accepted is 'compose'"):
            DocumentData.model_validate(self.doc(format="helm"))

    def test_the_category_is_in_the_schemas(self) -> None:
        offering = json.loads((SCHEMA_DIR / "offering_v1.json").read_text())
        assert "instance_spec" in json.dumps(offering)


# --- the Compose policy ---------------------------------------------------------------------------


class TestHonor:
    def test_the_honored_keys_compile(self) -> None:
        text = _compose(
            command=["--serve"],
            entrypoint=["/bin/app"],
            environment={"A": "1", "B": True, "C": 2.5},
            working_dir="/srv",
            user="1000:1000",
            read_only=True,
            healthcheck={
                "test": ["CMD-SHELL", "true"],
                "interval": "1m30s",
                "timeout": "500ms",
                "retries": 4,
                "start_period": "0s",
            },
        )
        compiled = compile_instance_spec(text, _res())
        (service,) = compiled.manifest.services
        assert service.command == ["/bin/app"]
        assert service.args == ["--serve"]
        assert service.env == {"A": "1", "B": "true", "C": "2.5"}
        assert service.ports == [8080]
        health = service.health
        assert health is not None
        assert health.model_dump(exclude_none=True) == {
            "type": "exec",
            "command": ["sh", "-c", "true"],
            "period_seconds": 90,
            "timeout_seconds": 1,
            "failure_threshold": 4,
            "start_period_seconds": 0,
        }
        # working_dir, user and read_only are allowed but the agent has no field for them yet.
        assert {(d.key, d.reason) for d in compiled.dropped} == {
            ("working_dir", "not_carried"),
            ("user", "not_carried"),
            ("read_only", "not_carried"),
        }

    def test_environment_as_a_list_and_a_string_command(self) -> None:
        compiled = compile_instance_spec(
            _compose(environment=["A=1", "B=x=y"], command="run --flag 'two words'"),
            _res(),
        )
        (service,) = compiled.manifest.services
        assert service.env == {"A": "1", "B": "x=y"}
        assert service.args == ["run", "--flag", "two words"]

    @pytest.mark.parametrize(
        ("port", "expected"),
        [
            ("8080", 8080),
            (8080, 8080),
            ("80:8080", 8080),
            ("127.0.0.1:80:8080", 8080),
            ("8080/tcp", 8080),
            ({"target": 8080, "published": 80}, 8080),
        ],
    )
    def test_ports_are_container_side(self, port: Any, expected: int) -> None:
        report = validate_instance_spec(_compose(ports=[port]))
        assert report.ok, report.errors
        compiled = compile_instance_spec(_compose(ports=[port]), _res())
        assert compiled.manifest.services[0].ports == [expected]

    def test_a_host_port_is_a_warning(self) -> None:
        report = validate_instance_spec(_compose(ports=["80:8080"]))
        assert "host_port" in _codes(report, "warnings")
        assert "host_port" not in _codes(validate_instance_spec(_compose(ports=["8080"])), "warnings")

    def test_health_shapes(self) -> None:
        for test in (["CMD", "/x", "-v"], ["CMD-SHELL", "exit 0"], "exit 0"):
            assert validate_instance_spec(_compose(healthcheck={"test": test})).ok
        disabled = validate_instance_spec(_compose(healthcheck={"disable": True}))
        assert disabled.ok and "no_healthcheck" in _codes(disabled, "warnings")
        assert "no_healthcheck" in _codes(validate_instance_spec(_compose(healthcheck={"test": ["NONE"]})), "warnings")

    @pytest.mark.parametrize(
        "healthcheck",
        [
            {"test": ["CMD"]},
            {"test": ["BOGUS", "x"]},
            {"interval": "5s"},
            {"test": ["CMD", "x"], "interval": "soon"},
            {"test": ["CMD", "x"], "retries": 0},
            {"test": ["CMD", "x"], "path": "/"},
        ],
    )
    def test_bad_healthchecks(self, healthcheck: Any) -> None:
        assert not validate_instance_spec(_compose(healthcheck=healthcheck)).ok


class TestIgnore:
    @pytest.mark.parametrize(
        "key",
        ["container_name", "restart", "labels", "logging", "networks", "expose", "profiles", "links", "x-anything"],
    )
    def test_ignored_with_a_warning(self, key: str) -> None:
        values: dict[str, Any] = {
            "container_name": "c",
            "restart": "always",
            "labels": {"a": "b"},
            "logging": {"driver": "json-file"},
            "networks": ["default"],
            "expose": ["9000"],
            "profiles": ["dev"],
            "links": [],
            "x-anything": {"a": 1},
        }
        report = validate_instance_spec(_compose(**{key: values[key]}))
        assert report.ok, report.errors
        assert any(w.path.endswith(key) and not w.loud for w in report.warnings)
        compiled = compile_instance_spec(_compose(**{key: values[key]}), _res())
        assert key in {d.key for d in compiled.dropped}

    def test_deploy_resources_is_ignored_in_favour_of_the_channel(self) -> None:
        report = validate_instance_spec(_compose(deploy={"resources": {"limits": {"memory": "1G"}}}))
        assert report.ok
        assert any("deploy.resources" in str(w) and "channel" in w.message for w in report.warnings)

    def test_other_deploy_keys_are_refused(self) -> None:
        assert "unsupported_key" in _codes(validate_instance_spec(_compose(deploy={"replicas": 3})))

    def test_top_level_extras(self) -> None:
        text = yaml.safe_dump({"name": "p", "version": "3", "x-common": {"a": 1}, **yaml.safe_load(_compose())})
        report = validate_instance_spec(text)
        assert report.ok and len(report.warnings) == 3


class TestLoudWarnings:
    def test_depends_on_has_no_ordering(self) -> None:
        text = yaml.safe_dump(
            {
                "services": {
                    "db": {"image": f"db{DIGEST}", "ports": [5432]},
                    "web": {"image": f"web{DIGEST}", "depends_on": {"db": {"condition": "service_healthy"}}},
                }
            }
        )
        report = validate_instance_spec(text)
        assert "depends_on" in _codes(report, "warnings")
        assert next(w for w in report.warnings if w.code == "depends_on").loud

    def test_depends_on_must_name_a_service(self) -> None:
        assert "unknown_service" in _codes(validate_instance_spec(_compose(depends_on=["ghost"])))

    def test_named_volumes_are_scratch_space(self) -> None:
        text = yaml.safe_dump(
            {
                "services": {"web": {"image": f"web{DIGEST}", "volumes": ["data:/data", "/cache"]}},
                "volumes": {"data": {}},
            }
        )
        report = validate_instance_spec(text)
        assert report.ok, report.errors
        scratch = [w for w in report.warnings if w.code == "scratch_volume"]
        assert len(scratch) == 2 and all(w.loud for w in scratch)
        assert "survives a container restart" in scratch[0].message
        manifest = compile_instance_spec(text, _res()).manifest
        assert [s.path for s in manifest.services[0].scratch or []] == ["/data", "/cache"]
        assert not any(d.key == "volumes" for d in compile_instance_spec(text, _res()).dropped)

    def test_a_path_mounted_twice_is_an_error(self) -> None:
        text = yaml.safe_dump(
            {
                "services": {"web": {"image": f"web{DIGEST}", "volumes": ["a:/data", "b:/data"]}},
                "volumes": {"a": {}, "b": {}},
            }
        )
        assert "duplicate_mount" in _codes(validate_instance_spec(text))


class TestReject:
    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("build", "."),
            ("privileged", True),
            ("cap_add", ["NET_ADMIN"]),
            ("devices", ["/dev/fuse"]),
            ("network_mode", "host"),
            ("pid", "host"),
            ("ipc", "host"),
            ("security_opt", ["seccomp:unconfined"]),
            ("sysctls", {"a": "1"}),
            ("volumes_from", ["x"]),
            ("runtime", "nvidia"),
            ("extra_hosts", ["a:1.1.1.1"]),
            ("extends", {"service": "x"}),
            ("mem_limit", "1g"),
            ("secrets", ["s"]),
            ("hostname", "h"),
            ("stdin_open", True),
        ],
    )
    def test_rejected_service_keys(self, key: str, value: Any) -> None:
        report = validate_instance_spec(_compose(**{key: value}))
        assert not report.ok
        assert any(e.path == f"services.web.{key}" for e in report.errors), report.errors

    def test_unknown_keys_are_rejected_not_ignored(self) -> None:
        report = validate_instance_spec(_compose(a_future_compose_feature=True))
        assert "unsupported_key" in _codes(report)

    @pytest.mark.parametrize("key", ["secrets", "configs", "include", "services_x"])
    def test_rejected_top_level_keys(self, key: str) -> None:
        text = yaml.safe_dump({**yaml.safe_load(_compose()), key: {"a": {}}})
        assert not validate_instance_spec(text).ok

    @pytest.mark.parametrize(
        "image",
        ["nginx@sha256:abc", "nginx@sha256:" + "A" * 64, "nginx${TAG}" + DIGEST, "nginx latest", "nginx:1@sha256:"],
    )
    def test_a_written_digest_must_be_a_literal_valid_one(self, image: str) -> None:
        assert not validate_instance_spec(_compose(image=image)).ok

    @pytest.mark.parametrize("image", ["nginx", "nginx:latest", "nginx:1.27", "ghcr.io/o/app:stable"])
    def test_a_tag_or_no_tag_is_accepted_with_a_note(self, image: str) -> None:
        report = validate_instance_spec(_compose(image=image))
        assert report.ok, report.errors
        note = next(w for w in report.warnings if w.code == "unpinned_image")
        assert not note.loud and "runs it as written" in note.message and "reports the digest" in note.message
        assert compile_instance_spec(_compose(image=image), _res()).manifest.services[0].image == image

    def test_a_digest_written_by_the_seller_is_honored_as_written(self) -> None:
        image = "ghcr.io/o/app:v1" + DIGEST
        report = validate_instance_spec(_compose(image=image))
        assert report.ok and "unpinned_image" not in _codes(report, "warnings")
        manifest = compile_instance_spec(_compose(image=image), _res()).manifest
        assert manifest.services[0].image == image

    def test_a_tag_with_a_digest_is_accepted(self) -> None:
        assert validate_instance_spec(_compose(image="ghcr.io/o/app:v1.2.3" + DIGEST)).ok

    def test_a_missing_image(self) -> None:
        text = yaml.safe_dump({"services": {"web": {"ports": [80]}}})
        assert "no_image" in _codes(validate_instance_spec(text))

    @pytest.mark.parametrize(
        "port", ["8000-8010", "8080/udp", "${PORT}", 0, 70000, "a:b:c:d", True, {"target": 80, "protocol": "udp"}]
    )
    def test_bad_ports(self, port: Any) -> None:
        assert not validate_instance_spec(_compose(ports=[port])).ok

    def test_duplicate_container_ports(self) -> None:
        assert "duplicate_port" in _codes(validate_instance_spec(_compose(ports=["80:8080", "81:8080"])))

    @pytest.mark.parametrize(
        "volume",
        [
            "./data:/data",
            "/srv/data:/data",
            "~/d:/d",
            {"type": "bind", "source": "/x", "target": "/y"},
            {"type": "tmpfs", "target": "/t"},
        ],
    )
    def test_host_paths_and_bind_mounts(self, volume: Any) -> None:
        assert not validate_instance_spec(_compose(volumes=[volume])).ok

    def test_an_undeclared_volume(self) -> None:
        assert "unknown_volume" in _codes(validate_instance_spec(_compose(volumes=["nope:/data"])))

    def test_a_volume_shared_by_two_services(self) -> None:
        text = yaml.safe_dump(
            {
                "services": {
                    "a": {"image": f"a{DIGEST}", "volumes": ["shared:/d"]},
                    "b": {"image": f"b{DIGEST}", "volumes": ["shared:/d"]},
                },
                "volumes": {"shared": {}},
            }
        )
        assert "shared_volume" in _codes(validate_instance_spec(text))

    @pytest.mark.parametrize("volume", [{"external": True}, {"driver": "nfs"}, {"driver_opts": {"a": "b"}}])
    def test_only_plain_top_level_volumes(self, volume: Any) -> None:
        text = yaml.safe_dump(
            {"services": {"web": {"image": f"w{DIGEST}", "volumes": ["v:/d"]}}, "volumes": {"v": volume}}
        )
        assert not validate_instance_spec(text).ok

    def test_services_split_across_networks(self) -> None:
        text = yaml.safe_dump(
            {
                "services": {
                    "front": {"image": f"f{DIGEST}", "networks": ["public"]},
                    "back": {"image": f"b{DIGEST}", "networks": ["private"]},
                },
                "networks": {"public": {}, "private": {}},
            }
        )
        assert "split_networks" in _codes(validate_instance_spec(text))

    def test_a_third_service_does_not_connect_two_networks(self) -> None:
        text = yaml.safe_dump(
            {
                "services": {
                    "web": {"image": f"w{DIGEST}", "networks": ["front"]},
                    "db": {"image": f"d{DIGEST}", "networks": ["back"]},
                    "proxy": {"image": f"p{DIGEST}", "networks": ["front", "back"]},
                },
                "networks": {"front": {}, "back": {}},
            }
        )
        report = validate_instance_spec(text)
        assert "split_networks" in _codes(report)
        assert "db and web" in next(e.message for e in report.errors if e.code == "split_networks")

    def test_networks_that_still_connect_the_services_are_only_ignored(self) -> None:
        text = yaml.safe_dump(
            {
                "services": {
                    "front": {"image": f"f{DIGEST}", "networks": ["public", "private"]},
                    "back": {"image": f"b{DIGEST}", "networks": ["private"]},
                },
                "networks": {"public": {}, "private": {}},
            }
        )
        report = validate_instance_spec(text, channel=_container(sizes=("front", "back")))
        assert report.ok, report.errors

    def test_an_undeclared_network(self) -> None:
        assert "unknown_network" in _codes(validate_instance_spec(_compose(networks=["ghost"])))

    @pytest.mark.parametrize("name", ["My_Service", "-web", "web-", "Web", "a" * 64])
    def test_service_names_are_dns_labels(self, name: str) -> None:
        text = yaml.safe_dump({"services": {name: {"image": f"x{DIGEST}"}}})
        assert "bad_service_name" in _codes(validate_instance_spec(text))

    def test_environment_without_a_value(self) -> None:
        assert not validate_instance_spec(_compose(environment=["FROM_HOST"])).ok
        assert not validate_instance_spec(_compose(environment={"A": None})).ok

    def test_the_file_itself(self) -> None:
        assert "invalid_yaml" in _codes(validate_instance_spec("services: [unclosed"))
        assert "not_a_mapping" in _codes(validate_instance_spec("- a\n- b\n"))
        assert "no_services" in _codes(validate_instance_spec("services: {}\n"))
        assert "invalid_yaml" in _codes(validate_instance_spec("services:\n  web: {image: a}\n  web: {image: b}\n"))

    def test_yaml_is_parsed_without_executing_anything(self) -> None:
        report = validate_instance_spec("services: !!python/object/apply:os.system ['echo hacked']\n")
        assert "invalid_yaml" in _codes(report)

    def test_other_formats(self) -> None:
        report = validate_instance_spec(_compose(), format="helm")
        assert _codes(report) == {"unsupported_format"}

    def test_oversized_specs(self) -> None:
        assert "too_large" in _codes(validate_instance_spec("#" * (300 * 1024)))


class TestMalformedInput:
    """Bad shapes are reported, never raised (the validation API's promise)."""

    @pytest.mark.parametrize(
        "text",
        [
            "services: {web: {image: 'x@sha256:%s', volumes: [{type: volume, source: 123, target: /data}]}}"
            % ("a" * 64),
            "services: {web: {image: 'x@sha256:%s', depends_on: [[]]}}" % ("a" * 64),
            "services: {web: {image: 'x@sha256:%s', depends_on: [{a: 1}]}}" % ("a" * 64),
            "services: {web: {image: 'x@sha256:%s'}}\nvolumes: {v: {1: 2}}" % ("a" * 64),
            "services: {web: {image: 'x@sha256:%s', ports: [[80]]}}" % ("a" * 64),
            "services: {web: {image: 'x@sha256:%s', environment: [1, [2]]}}" % ("a" * 64),
            "services: {web: {image: 'x@sha256:%s', healthcheck: {test: [1, 2]}}}" % ("a" * 64),
            "services: {web: {image: 'x@sha256:%s', networks: [[1]]}}" % ("a" * 64),
            "services: {1: {image: 5}}",
            "services: {web: null}",
            "services: [1]",
            "{1: 2}",
            "42",
            "",
        ],
    )
    def test_reported_not_raised(self, text: str) -> None:
        report = validate_instance_spec(text)
        assert not report.ok or text == ""

    def test_duration_overflow_and_deep_nesting_are_reported(self) -> None:
        huge = _compose(healthcheck={"test": ["CMD", "x"], "interval": "9" * 400 + "s"})
        assert "bad_value" in _codes(validate_instance_spec(huge))
        deep = "services: " + "[" * 600 + "0" + "]" * 600
        assert not validate_instance_spec(deep).ok

    def test_huge_quantities_do_not_hang(self) -> None:
        for text in ("1e1000000000", "1e-1000000000", "1" * 100000, "1e31"):
            with pytest.raises(ValueError):
                parse_quantity(text)
        assert parse_quantity("1e30") == 10**30


class TestSiblings:
    def two(self) -> str:
        return yaml.safe_dump(
            {
                "services": {
                    "web": {"image": f"web{DIGEST}", "ports": [8080]},
                    "redis": {"image": f"redis{DIGEST}", "ports": [6379]},
                }
            }
        )

    def test_siblings_are_allowed_and_need_a_size_each(self) -> None:
        assert validate_instance_spec(self.two()).ok
        report = validate_instance_spec(self.two(), channel=_container())
        assert "size_needs_map" in _codes(report)
        assert "web" in report.errors[0].message and "redis" in report.errors[0].message
        report = validate_instance_spec(self.two(), channel=_container(sizes=("web", "redis")))
        assert report.ok, report.errors

    def test_the_sizes_must_name_the_services(self) -> None:
        report = validate_instance_spec(self.two(), channel=_container(sizes=("web", "ghost")))
        assert {"size_service", "size_missing"} <= _codes(report)

    def test_a_one_service_spec_takes_a_size_or_a_map(self) -> None:
        text = _compose()
        assert validate_instance_spec(text, channel=_container()).ok
        assert validate_instance_spec(text, channel=_container(sizes=("web",))).ok
        assert "size_service" in _codes(validate_instance_spec(text, channel=_container(sizes=("ghost",))))

    def test_compile_with_a_size_each(self) -> None:
        resources = {
            "web": ResourceSet.model_validate(SIZES["small"]),
            "redis": ResourceSet.model_validate(SIZES["nano"]),
        }
        manifest = compile_instance_spec(self.two(), resources).manifest
        by_name = {s.name: s for s in manifest.services}
        assert by_name["web"].resources is not None
        assert by_name["web"].resources.limits == SIZES["small"]["limits"]
        assert by_name["redis"].resources is not None
        assert by_name["redis"].resources.limits == SIZES["nano"]["limits"]

    def test_compile_needs_resources_for_every_service_and_no_other(self) -> None:
        with pytest.raises(InstanceSpecError, match="no resources for redis"):
            compile_instance_spec(self.two(), _res())
        with pytest.raises(InstanceSpecError, match="not a service of the spec"):
            compile_instance_spec(_compose(), {**_res(), "ghost": _resolved()})

    def test_compile_checks_what_the_platform_resolved(self) -> None:
        with pytest.raises(InstanceSpecError, match="must set ephemeral-storage"):
            compile_instance_spec(
                _compose(), {"web": ResourceSet.model_validate({"limits": {"cpu": "1", "memory": "1Gi"}})}
            )


class TestEntries:
    def entry(self, **changes: Any) -> Any:
        return _container(entries=[{"name": "web", "service": "web", "port": 8080, "auth": "key", **changes}])

    def test_an_entry_targets_a_declared_service_and_port(self) -> None:
        assert validate_instance_spec(_compose(), channel=self.entry()).ok
        assert "entry_service" in _codes(validate_instance_spec(_compose(), channel=self.entry(service="api")))
        assert "entry_port" in _codes(validate_instance_spec(_compose(), channel=self.entry(port=9090)))


def _container(*, sizes: tuple[str, ...] = (), **changes: Any) -> ContainerChannel:
    """A container channel: one size, or (with ``sizes``) a size for each named service."""
    resources: Any = {name: "nano" for name in sizes} if sizes else "nano"
    return ContainerChannel.model_validate(_channel(resources=resources, **changes))


# --- the manifest --------------------------------------------------------------------------------


MANIFEST_SCHEMA = json.loads((SCHEMA_DIR / "normalized_manifest_v1.json").read_text())


class TestManifest:
    def test_the_schema_file_is_the_models(self) -> None:
        assert MANIFEST_SCHEMA == json.loads(json.dumps(NormalizedManifest.model_json_schema()))

    @pytest.mark.parametrize("fixture", sorted((FIXTURES / "manifest" / "valid").glob("*.json")), ids=lambda p: p.name)
    def test_the_agents_valid_manifests_are_valid(self, fixture: Path) -> None:
        data = json.loads(fixture.read_text())
        assert list(Draft7Validator(MANIFEST_SCHEMA).iter_errors(data)) == []
        NormalizedManifest.model_validate(data)

    @pytest.mark.parametrize(
        "fixture", sorted((FIXTURES / "manifest" / "invalid").glob("*.json")), ids=lambda p: p.name
    )
    def test_what_the_agent_refuses_is_refused(self, fixture: Path) -> None:
        data = json.loads(fixture.read_text())
        assert list(Draft7Validator(MANIFEST_SCHEMA).iter_errors(data))
        with pytest.raises(ValidationError):
            NormalizedManifest.model_validate(data)

    @pytest.mark.parametrize(
        "health",
        [
            {"type": "http"},
            {"type": "http", "path": "x"},
            {"type": "tcp", "path": "/"},
            {"type": "exec"},
            {"type": "exec", "command": ["a"], "port": 80},
            {"type": "http", "path": "/", "port": 9999},
        ],
    )
    def test_health_shapes_are_checked(self, health: dict[str, Any]) -> None:
        service = {"name": "a", "image": "i", "ports": [80], "health": health}
        with pytest.raises(ValidationError):
            NormalizedManifest.model_validate({"services": [service]})

    def test_duplicate_services(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            NormalizedManifest.model_validate({"services": [{"name": "a", "image": "i"}, {"name": "a", "image": "i"}]})


class TestCompileGolden:
    @pytest.mark.parametrize(
        ("service_dir", "golden"),
        [(ECHO, "echo.manifest.json"), (WEIXIN, "weixin.manifest.json")],
        ids=["echo", "weixin"],
    )
    def test_compile_output(self, service_dir: Path, golden: str) -> None:
        offering = json.loads((service_dir / "offering.json").read_text())
        channel = ContainerChannel.model_validate(offering["upstream_access_config"]["base"])
        compiled = compile_instance_spec(
            (service_dir / "compose.yaml").read_text(),
            _resolve(channel.resources, (service_dir / "compose.yaml").read_text()),
        )
        expected = json.loads((FIXTURES / "manifest" / golden).read_text())
        assert compiled.manifest.model_dump(exclude_none=True) == expected
        assert list(Draft7Validator(MANIFEST_SCHEMA).iter_errors(expected)) == []

    def test_dropped_keys_are_recorded(self) -> None:
        offering = json.loads((WEIXIN / "offering.json").read_text())
        channel = ContainerChannel.model_validate(offering["upstream_access_config"]["base"])
        compiled = compile_instance_spec(
            (WEIXIN / "compose.yaml").read_text(), _resolve(channel.resources, (WEIXIN / "compose.yaml").read_text())
        )
        assert {(d.service, d.key, d.reason) for d in compiled.dropped} == {
            ("gateway", "deploy.resources", "ignored"),
        }
        assert any(w.loud for w in compiled.warnings)

    def test_both_tiers_share_one_file(self) -> None:
        offering = json.loads((ECHO / "offering.json").read_text())
        text = (ECHO / "compose.yaml").read_text()
        limits = {}
        for name, raw in offering["upstream_access_config"].items():
            sizes = ContainerChannel.model_validate(raw).resources
            manifest = compile_instance_spec(text, _resolve(sizes, text)).manifest
            resources = manifest.services[0].resources
            assert resources is not None
            limits[name] = resources.limits
        assert limits["base"]["cpu"] == "250m" and limits["medium"]["cpu"] == "1"

    def test_requests_are_carried(self) -> None:
        manifest = compile_instance_spec(_compose(), _res(requests={"cpu": "100m"})).manifest
        assert manifest.services[0].resources is not None
        assert manifest.services[0].resources.requests == {"cpu": "100m"}

    def test_an_invalid_spec_does_not_compile(self) -> None:
        with pytest.raises(InstanceSpecError) as caught:
            compile_instance_spec(_compose(image="nginx@sha256:abc"), _res())
        assert "bad_digest" in {e.code for e in caught.value.report.errors}


# --- the examples through the validator --------------------------------------------------------


@pytest.fixture
def validator() -> DataValidator:
    return DataValidator(EXAMPLES, SCHEMA_DIR)


def _offering(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "svc",
        "description": "d",
        "summary": "s",
        "time_created": "2026-01-01T00:00:00Z",
        "instance_spec": "compose.yaml",
        "upstream_access_config": config,
    }


class TestExamples:
    @pytest.mark.parametrize("service_dir", [ECHO, WEIXIN], ids=["echo", "weixin"])
    def test_the_example_service_validates(self, validator: DataValidator, service_dir: Path) -> None:
        for name in ("offering", "listing"):
            ok, errors = validator.validate_data_file(
                service_dir / f"{name}.json", f"{name}_v1", check_name_consistency=False
            )
            assert ok, errors

    def test_the_weixin_digest_is_a_marked_placeholder(self) -> None:
        text = (WEIXIN / "compose.yaml").read_text()
        assert "PLACEHOLDER DIGEST" in text and "sha256:" + "0" * 64 in text

    def test_weixin_warns_loudly_about_its_state(self, validator: DataValidator) -> None:
        offering = json.loads((WEIXIN / "offering.json").read_text())
        warnings = validator.service_warnings(offering, WEIXIN)
        assert any(w.code == "scratch_volume" and w.loud for w in warnings)
        assert any(w.code == "interpolation" for w in warnings)

    def test_the_upstream_weixin_compose_is_accepted_with_loud_warnings(self) -> None:
        report = validate_instance_spec((WEIXIN_UPSTREAM / "upstream-compose.yaml").read_text())
        assert report.ok, report.errors
        loud = {w.code for w in report.warnings if w.loud}
        assert {"ignored_key", "scratch_volume"} <= loud
        env_file = next(w for w in report.warnings if w.path == "services.gateway.env_file")
        assert env_file.loud and "environment" in env_file.message and "missing" in env_file.message
        assert "unpinned_image" in _codes(report, "warnings")

    def test_a_bad_spec_fails_the_offering(self, validator: DataValidator, tmp_path: Path) -> None:
        (tmp_path / "compose.yaml").write_text(_compose(privileged=True))
        offering = json.loads((ECHO / "offering.json").read_text())
        errors = validator.validate_service(offering=offering, service_dir=tmp_path)["offering"]
        assert any("upstream_access_config.base.instance_spec" in e and "privileged" in e for e in errors)

    def test_entry_and_split_errors_surface_in_the_offering(self, validator: DataValidator) -> None:
        offering = json.loads((ECHO / "offering.json").read_text())
        offering["upstream_access_config"]["base"]["entries"][0]["port"] = 9999
        errors = validator.validate_service(offering=offering, service_dir=ECHO)["offering"]
        assert any("targets port 9999" in e for e in errors)


class TestServersRule:
    """Decided 2 of #105, as far as core can hold it (see the PR: ``/e/CODE`` is platform-provided here)."""

    def offering(self, with_servers: bool) -> dict[str, Any]:
        offering = json.loads((ECHO / "offering.json").read_text())
        if with_servers:
            offering["upstream_access_config"]["base"]["servers"] = [
                {"base_url": "https://{{ enrollment.code }}.unitysvc.app/"}
            ]
        return offering

    def listing(self, interfaces: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.loads((ECHO / "listing.json").read_text())
        if interfaces is not None:
            data["user_access_interfaces"] = interfaces
        return data

    def test_a_container_only_service_needs_neither(self, validator: DataValidator) -> None:
        results = validator.validate_service(offering=self.offering(False), listing=self.listing(), service_dir=ECHO)
        assert results["offering"] == [] and results["listing"] == []

    def test_servers_are_accepted_and_need_no_declared_interface(self, validator: DataValidator) -> None:
        results = validator.validate_service(offering=self.offering(True), listing=self.listing(), service_dir=ECHO)
        assert results["offering"] == [] and results["listing"] == []

    def test_the_channel_with_servers_is_enrollable(self) -> None:
        channel = self.offering(True)["upstream_access_config"]["base"]
        assert ContainerChannel.model_validate(channel).type in (None, "enrollable")
        with pytest.raises(ValidationError, match="always enrollable"):
            ContainerChannel.model_validate({**channel, "type": "secret_bound"})

    def test_the_gateway_path_is_not_declared_by_the_listing(self, validator: DataValidator) -> None:
        # The existing canonical-interface rule: /e/<CODE> is provided by the platform, never declared.
        gateway = {
            "enrollment": {"access_method": "http", "base_url": "${API_GATEWAY_BASE_URL}/e/{{ enrollment.code }}"}
        }
        errors = validator.validate_service(
            offering=self.offering(True), listing=self.listing(gateway), service_dir=ECHO
        )["listing"]
        assert any("which the platform provides" in e for e in errors)


# --- the connectivity test -------------------------------------------------------------------


class TestConnectivityTest:
    def listing(self, documents: dict[str, Any] | None) -> dict[str, Any]:
        data = json.loads((ECHO / "listing.json").read_text())
        if documents is None:
            data.pop("documents")
        else:
            data["documents"] = documents
        return data

    def test_a_container_service_needs_no_script(self, validator: DataValidator) -> None:
        offering = json.loads((ECHO / "offering.json").read_text())
        for documents in (
            None,
            {"Getting started": {"category": "getting_started", "mime_type": "markdown", "file_path": "README.md"}},
        ):
            errors = validator.validate_connectivity_test_exists(
                self.listing(documents), "listing_v1", offering=offering
            )
            assert errors == []

    def test_other_services_still_do(self, validator: DataValidator) -> None:
        offering = json.loads((ECHO / "offering.json").read_text())
        offering["upstream_access_config"]["extra"] = {"access_method": "http", "base_url": "https://u.example"}
        assert validator.validate_connectivity_test_exists(self.listing(None), "listing_v1", offering=offering)

    def test_declarative_probes_are_checked_against_the_entries(self, validator: DataValidator, tmp_path: Path) -> None:
        offering = json.loads((ECHO / "offering.json").read_text())
        listing = self.listing(
            {"Probes": {"category": "connectivity_test", "mime_type": "text", "file_path": "probes.json"}}
        )
        # The file on disk is the valid one.
        assert validator.validate_service(offering=offering, listing=listing, service_dir=ECHO)["listing"] == []
        (tmp_path / "probes.json").write_text('{"probes": [{"entry": "nope"}]}')
        errors = validator.validate_service(offering=offering, listing=listing, service_dir=tmp_path)["listing"]
        assert any("'nope' is not an entry" in e for e in errors)

    def test_a_script_is_not_read_as_probes(self, validator: DataValidator, tmp_path: Path) -> None:
        (tmp_path / "test.sh").write_text("exit 0")
        listing = self.listing({"T": {"category": "connectivity_test", "mime_type": "bash", "file_path": "test.sh"}})
        offering = json.loads((ECHO / "offering.json").read_text())
        assert validator.validate_service(offering=offering, listing=listing, service_dir=tmp_path)["listing"] == []


class TestProbes:
    PROBES_SCHEMA = json.loads((SCHEMA_DIR / "probes_v1.json").read_text())

    @pytest.mark.parametrize("fixture", sorted((FIXTURES / "probes").glob("valid*.json")), ids=lambda p: p.name)
    def test_valid(self, fixture: Path) -> None:
        data = json.loads(fixture.read_text())
        assert list(Draft7Validator(self.PROBES_SCHEMA).iter_errors(data)) == []
        assert validate_probes_text(fixture.read_text(), {"web"}) == []

    @pytest.mark.parametrize("fixture", sorted((FIXTURES / "probes").glob("invalid*.json")), ids=lambda p: p.name)
    def test_invalid(self, fixture: Path) -> None:
        data = json.loads(fixture.read_text())
        assert list(Draft7Validator(self.PROBES_SCHEMA).iter_errors(data))
        assert validate_probes_text(fixture.read_text(), {"web"})

    def test_defaults(self) -> None:
        assert validate_probes_text('{"probes": [{"entry": "web"}]}', {"web"}) == []

    def test_unknown_entry_and_bad_json(self) -> None:
        assert validate_probes_text('{"probes": [{"entry": "x"}]}', {"web"})
        assert validate_probes_text("not json", {"web"})

    def test_the_schema_file_is_the_model(self) -> None:
        from unitysvc_core.models.probes import ProbesV1

        assert self.PROBES_SCHEMA == json.loads(json.dumps(ProbesV1.model_json_schema()))


def test_the_channel_schema_lists_the_container_channel() -> None:
    schema = json.loads((SCHEMA_DIR / "offering_v1.json").read_text())
    assert "ContainerChannel" in schema["$defs"]
    assert copy.deepcopy(schema["$defs"]["ContainerChannel"]["properties"]["access_method"])["const"] == "container"


# --- one instance spec per offering (unitysvc-core#107) ------------------------------------------


def _errors(validator: DataValidator, offering: dict[str, Any], service_dir: Path = ECHO) -> list[str]:
    return validator.validate_service(offering=offering, service_dir=service_dir)["offering"]


def _echo() -> dict[str, Any]:
    return json.loads((ECHO / "offering.json").read_text())


def _document(**changes: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {"category": "instance_spec", "mime_type": "text", "file_path": "compose.yaml"}
    doc.update(changes)
    return doc


class TestOneInstanceSpec:
    def test_the_shortcut_validates(self, validator: DataValidator) -> None:
        assert _errors(validator, _echo()) == []

    def test_the_document_form_validates(self, validator: DataValidator) -> None:
        offering = expand_instance_spec(_echo())
        assert list(offering["documents"]) == [INSTANCE_SPEC_TITLE] and "instance_spec" not in offering
        assert _errors(validator, offering) == []

    def test_a_container_offering_needs_one(self, validator: DataValidator) -> None:
        offering = _echo()
        del offering["instance_spec"]
        assert any("needs its instance spec" in e for e in _errors(validator, offering))

    def test_two_are_an_error_from_either_form(self, validator: DataValidator) -> None:
        both = _echo()
        both["documents"] = {"Application": _document()}
        assert any("one instance spec, found 2" in e for e in _errors(validator, both))
        two = expand_instance_spec(_echo())
        two["documents"]["Second"] = _document()
        assert any("one instance spec, found 2" in e for e in _errors(validator, two))

    def test_another_offering_has_none(self, validator: DataValidator) -> None:
        offering = {k: v for k, v in _echo().items() if k not in ("upstream_access_config",)}
        offering["service_type"] = "llm"
        offering["details"] = {"links": {"homepage": "https://example.com"}}
        assert any("only an offering with a container channel" in e for e in _errors(validator, offering))
        offering.pop("instance_spec")
        offering["documents"] = {"Application": _document()}
        assert any("only an offering with a container channel" in e for e in _errors(validator, offering))

    def test_every_tier_runs_the_one_file(self, validator: DataValidator) -> None:
        offering = _echo()
        offering["upstream_access_config"]["medium"]["resources"] = {"web": "small", "ghost": "nano"}
        errors = _errors(validator, offering)
        assert any("medium.instance_spec" in e and "ghost" in e for e in errors)
        assert not any("base." in e for e in errors)

    def test_the_shortcut_is_a_path_or_an_https_url(self, validator: DataValidator) -> None:
        offering = _echo()
        offering["instance_spec"] = "https://raw.githubusercontent.com/o/r/" + "a" * 40 + "/compose.yaml"
        assert _errors(validator, offering) == []
        for bad in (
            "http://example.com/c.yaml",
            "https://localhost/c.yaml",
            "https://10.0.0.1/c.yaml",
            "ftp://x.com/c",
        ):
            offering["instance_spec"] = bad
            assert any("instance_spec" in e for e in _errors(validator, offering)), bad

    def test_a_missing_shortcut_file_is_reported(self, validator: DataValidator) -> None:
        offering = _echo()
        offering["instance_spec"] = "nope.yaml"
        assert any("nope.yaml" in e and "does not exist" in e for e in _errors(validator, offering))


class TestExpand:
    def test_a_path_becomes_a_file_document(self) -> None:
        out = expand_instance_spec({"name": "x", "instance_spec": "compose.yaml"})
        assert out == {
            "name": "x",
            "documents": {
                INSTANCE_SPEC_TITLE: {
                    "category": "instance_spec",
                    "mime_type": "text",
                    "meta": {"format": "compose"},
                    "is_public": False,
                    "file_path": "compose.yaml",
                }
            },
        }

    def test_a_url_becomes_an_external_url_document(self) -> None:
        url = "https://raw.githubusercontent.com/o/r/main/compose.yaml"
        doc = expand_instance_spec({"instance_spec": url})["documents"][INSTANCE_SPEC_TITLE]
        assert doc["external_url"] == url and "file_path" not in doc
        DocumentData.model_validate(doc)

    def test_it_keeps_other_documents_and_does_not_mutate(self) -> None:
        offering = {"instance_spec": "c.yaml", "documents": {"Guide": {"category": "other"}}}
        out = expand_instance_spec(offering)
        assert set(out["documents"]) == {"Guide", INSTANCE_SPEC_TITLE}
        assert "instance_spec" in offering and set(offering["documents"]) == {"Guide"}

    def test_no_shortcut_is_a_copy(self) -> None:
        offering = {"name": "x"}
        assert expand_instance_spec(offering) == offering and expand_instance_spec(offering) is not offering

    def test_conflicts_are_refused(self) -> None:
        with pytest.raises(ValueError, match="one instance spec"):
            expand_instance_spec({"instance_spec": "c.yaml", "documents": {"A": _document()}})
        with pytest.raises(ValueError, match="already has an entry"):
            expand_instance_spec({"instance_spec": "c.yaml", "documents": {INSTANCE_SPEC_TITLE: {"category": "other"}}})
        with pytest.raises(ValueError, match="relative path or an https URL"):
            expand_instance_spec({"instance_spec": 3})

    def test_the_expanded_offering_validates_like_the_shortcut(self, validator: DataValidator) -> None:
        shortcut = _echo()
        assert _errors(validator, expand_instance_spec(shortcut)) == _errors(validator, shortcut) == []


class TestSpecRemoved:
    def test_a_channel_with_spec_is_rejected_as_an_unknown_field(self, validator: DataValidator) -> None:
        offering = _echo()
        offering["upstream_access_config"]["base"]["spec"] = INSTANCE_SPEC_TITLE
        assert any("spec" in e and "Extra inputs are not permitted" in e for e in _errors(validator, offering))
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            ContainerChannel.model_validate(_channel(spec="Application"))

    def test_the_schema_has_no_spec(self) -> None:
        schema = json.loads((SCHEMA_DIR / "offering_v1.json").read_text())
        assert "spec" not in schema["$defs"]["ContainerChannel"]["properties"]
        assert "instance_spec" in schema["properties"]


class TestSnapshotDocument:
    URL = "https://raw.githubusercontent.com/o/r/" + "a" * 40 + "/compose.yaml"

    def doc(self, **changes: Any) -> dict[str, Any]:
        data = _document(external_url=self.URL)
        del data["file_path"]
        data.update(changes)
        return data

    def test_an_https_url_with_source_and_sha256(self) -> None:
        DocumentData.model_validate(self.doc(meta={"source": self.URL, "sha256": "ab" * 32}))

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/c.yaml",
            "https://localhost/c.yaml",
            "https://foo.localhost/c.yaml",
            "https://127.0.0.1/c.yaml",
            "https://169.254.169.254/latest",
            "https://192.168.1.5/c.yaml",
            "https://[::1]/c.yaml",
            "https://2130706433/c.yaml",
            "https://0x7f000001/c.yaml",
            "https://intranet/c.yaml",
            "https://db.internal/c.yaml",
            "https://user:pw@example.com/c.yaml",
            "https:///c.yaml",
            "file:///etc/passwd",
        ],
    )
    def test_private_and_non_https_urls_are_refused(self, url: str) -> None:
        with pytest.raises(ValidationError, match="external_url"):
            DocumentData.model_validate(self.doc(external_url=url))

    def test_bad_source_and_sha256_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="meta.source"):
            DocumentData.model_validate(self.doc(meta={"source": "http://example.com/c"}))
        for bad in ("abc", "A" * 64, 5):
            with pytest.raises(ValidationError, match="meta.sha256"):
                DocumentData.model_validate(self.doc(meta={"sha256": bad}))

    def test_exactly_one_of_file_path_and_external_url(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            DocumentData.model_validate(_document(external_url=self.URL))
        with pytest.raises(ValidationError, match="exactly one"):
            DocumentData.model_validate({"category": "instance_spec", "mime_type": "text"})

    def test_other_categories_keep_their_external_url(self) -> None:
        DocumentData.model_validate({"category": "other", "mime_type": "text", "external_url": "http://example.com/x"})

    def test_a_url_spec_is_not_fetched_or_checked_locally(self, validator: DataValidator) -> None:
        offering = _echo()
        offering["instance_spec"] = self.URL
        assert _errors(validator, offering, service_dir=Path("/nonexistent")) == []

    def test_sha256_pins_a_local_file(self, validator: DataValidator) -> None:
        import hashlib

        offering = expand_instance_spec(_echo())
        doc = offering["documents"][INSTANCE_SPEC_TITLE]
        doc["meta"]["sha256"] = hashlib.sha256((ECHO / "compose.yaml").read_bytes()).hexdigest()
        assert _errors(validator, offering) == []
        doc["meta"]["sha256"] = "0" * 64
        assert any("does not match meta.sha256" in e for e in _errors(validator, offering))


# --- addenda of #107: env_file, meta.environment, scratch ---------------------------------------------


def _resources() -> dict[str, ResourceSet]:
    return _res()


class TestEnvFile:
    def test_env_file_is_ignored_loudly_not_rejected(self) -> None:
        report = validate_instance_spec(_compose(env_file=".env"))
        assert report.ok, report.errors
        warning = next(w for w in report.warnings if w.path == "services.web.env_file")
        assert warning.code == "ignored_key" and warning.loud
        assert "missing" in warning.message and "environment" in warning.message
        compiled = compile_instance_spec(_compose(env_file=".env"), _resources())
        assert ("web", "env_file", "ignored") in {(d.service, d.key, d.reason) for d in compiled.dropped}


class TestEnvironmentOverlay:
    def compose(self) -> str:
        return _compose(environment={"A": "file", "KEEP": "k"})

    def test_the_overlay_overrides_and_adds(self) -> None:
        overlay = {"web": {"A": "overlay", "B": "new"}}
        env = compile_instance_spec(self.compose(), _resources(), environment=overlay).manifest.services[0].env
        assert env == {"A": "overlay", "KEEP": "k", "B": "new"}

    def test_the_file_is_not_edited_and_no_overlay_is_the_file(self) -> None:
        text = self.compose()
        assert compile_instance_spec(text, _resources()).manifest.services[0].env == {"A": "file", "KEEP": "k"}
        assert text == self.compose()

    def test_it_supplies_what_env_file_would_have(self) -> None:
        overlay = {"web": {"API_KEY": "${SECRET_KEY}"}}
        text = _compose(env_file=".env")
        env = compile_instance_spec(text, _resources(), environment=overlay).manifest.services[0].env
        assert env == {"API_KEY": "${SECRET_KEY}"}
        report = validate_instance_spec(text, channel=ContainerChannel.model_validate(_channel(environment=overlay)))
        assert report.ok and "interpolation" in _codes(report, "warnings")

    def test_a_service_the_file_lacks_is_an_error(self) -> None:
        ghost = ContainerChannel.model_validate(_channel(environment={"ghost": {"A": "1"}}))
        report = validate_instance_spec(self.compose(), channel=ghost)
        assert "overlay_service" in _codes(report)
        with pytest.raises(InstanceSpecError):
            compile_instance_spec(self.compose(), _resources(), environment={"ghost": {"A": "1"}})

    def test_the_document_no_longer_takes_an_overlay(self) -> None:
        doc = {
            "category": "instance_spec",
            "mime_type": "text",
            "file_path": "c.yaml",
            "meta": {"environment": {"web": {"A": "1"}}},
        }
        with pytest.raises(ValidationError, match="channel's `environment`"):
            DocumentData.model_validate(doc)

    def test_the_schema_describes_meta_as_open(self) -> None:
        schema = json.loads((SCHEMA_DIR / "offering_v1.json").read_text())
        assert "instance_spec" in json.dumps(schema)


class TestScratch:
    def test_the_manifest_schema_has_scratch(self) -> None:
        props = MANIFEST_SCHEMA["$defs"]["ManifestService"]["properties"]
        assert "scratch" in props
        manifest = {"services": [{"name": "a", "image": "i", "scratch": [{"path": "/data"}]}]}
        assert list(Draft7Validator(MANIFEST_SCHEMA).iter_errors(manifest)) == []
        bad = {"services": [{"name": "a", "image": "i", "scratch": [{"path": "data"}]}]}
        assert list(Draft7Validator(MANIFEST_SCHEMA).iter_errors(bad)) != []
        with pytest.raises(ValidationError):
            NormalizedManifest.model_validate(bad)
        with pytest.raises(ValidationError, match="distinct"):
            NormalizedManifest.model_validate(
                {"services": [{"name": "a", "image": "i", "scratch": [{"path": "/d"}, {"path": "/d"}]}]}
            )
        with pytest.raises(ValidationError):
            NormalizedManifest.model_validate({"services": [{"name": "a", "image": "i", "scratch": [{"path": "/"}]}]})

    def test_the_golden_weixin_manifest_carries_scratch(self) -> None:
        golden = json.loads((FIXTURES / "manifest" / "weixin.manifest.json").read_text())
        assert golden["services"][0]["scratch"] == [{"path": "/app/data"}]


def _with_volumes(volumes: list[Any]) -> str:
    base = {"image": f"example/app{DIGEST}", "ports": ["8080"], "volumes": volumes}
    return yaml.safe_dump({"services": {"web": base}, "volumes": {"v": {}}})


class TestReviewFixes:
    def test_scratch_paths_are_normalized(self) -> None:
        for bad in ("/data/..", "/data/../..", "/."):
            text = _with_volumes([{"type": "volume", "source": "v", "target": bad}])
            assert "bad_value" in _codes(validate_instance_spec(text)), bad
        text = _with_volumes([{"type": "volume", "source": "v", "target": "/data"}, "/data/../data"])
        assert "duplicate_mount" in _codes(validate_instance_spec(text))
        for bad in ("/data/..", "/a//b", "/a/", "/", "//", "//data"):
            with pytest.raises(ValidationError):
                NormalizedManifest.model_validate(
                    {"services": [{"name": "a", "image": "i", "scratch": [{"path": bad}]}]}
                )

    def test_an_overlay_resolves_an_inherited_variable(self) -> None:
        for env in (["KEY"], {"KEY": None}):
            text = _compose(environment=env)
            assert "bad_env" in _codes(validate_instance_spec(text))
            overlay = {"web": {"KEY": "v"}}
            channel = ContainerChannel.model_validate(_channel(environment=overlay))
            assert validate_instance_spec(text, channel=channel).ok
            assert compile_instance_spec(text, _resources(), environment=overlay).manifest.services[0].env == {
                "KEY": "v"
            }


class TestUpstreamExample:
    """The Kadxy URL-plus-overlay case: the project's own Compose file, a URL, a pinned hash and an overlay."""

    def offering(self) -> dict[str, Any]:
        return json.loads((WEIXIN_UPSTREAM / "offering.json").read_text())

    def test_it_validates(self, validator: DataValidator) -> None:
        for name in ("offering", "listing"):
            ok, errors = validator.validate_data_file(
                WEIXIN_UPSTREAM / f"{name}.json", f"{name}_v1", check_name_consistency=False
            )
            assert ok, errors

    def test_the_url_is_the_pinned_commit_and_the_hash_matches_the_fixture(self) -> None:
        import hashlib

        doc = self.offering()["documents"][INSTANCE_SPEC_TITLE]
        assert "ad6097361b834e263247764368c15529b5cf2a39" in doc["external_url"]
        assert (
            doc["meta"]["sha256"]
            == hashlib.sha256((WEIXIN_UPSTREAM / "upstream-compose.yaml").read_bytes()).hexdigest()
        )

    def test_the_channel_environment_and_scratch_reach_the_manifest(self) -> None:
        offering = self.offering()
        channel = ContainerChannel.model_validate(offering["upstream_access_config"]["base"])
        text = (WEIXIN_UPSTREAM / "upstream-compose.yaml").read_text()
        compiled = compile_instance_spec(
            text,
            _resolve(channel.resources, text),
            environment=channel.environment,
        )
        service = compiled.manifest.services[0]
        assert service.env == {"DATA_DIR": "/app/data", "API_KEY": "change-me-at-least-12-chars"}
        assert service.image == "ghcr.io/kadxy/weixin-ilink-gateway:latest"
        assert [s.path for s in service.scratch or []] == ["/app/data"]


class TestVersion0311Shapes:
    """What 0.3.11 accepted keeps validating."""

    def test_digest_pinned_images_and_the_two_examples(self, validator: DataValidator) -> None:
        for service_dir in (ECHO, WEIXIN):
            offering = json.loads((service_dir / "offering.json").read_text())
            assert _errors(validator, offering, service_dir) == []
            assert "unpinned_image" not in {w.code for w in validator.service_warnings(offering, service_dir)}

    def test_the_document_form_with_a_url_and_no_overlay(self, validator: DataValidator) -> None:
        offering = expand_instance_spec(_echo())
        doc = offering["documents"][INSTANCE_SPEC_TITLE]
        del doc["file_path"]
        doc["external_url"] = "https://raw.githubusercontent.com/o/r/" + "a" * 40 + "/compose.yaml"
        assert _errors(validator, offering) == []


# --- persisted volumes (unitysvc/unitysvc#3098) ----------------------------------------------------------
# The file only mounts a plain named volume; the channel's `persist` says which of its volumes outlive the instance.


def _persisting(volumes: list[Any] | None = None, *, extra: dict[str, Any] | None = None) -> str:
    """A one-service file mounting the plain volume ``home`` (as it would run under Docker)."""
    base = {"image": f"example/app{DIGEST}", "ports": ["8080"], "volumes": volumes or ["home:/home/app"]}
    base.update(extra or {})
    return yaml.safe_dump({"services": {"web": base}, "volumes": {"home": {}}})


STORAGE_REQUEST = {"object": "service:storage", "permissions": ["read", "write"], "required": True}
HOME_REQUEST = {
    "object": "service:home",
    "permissions": ["read", "write"],
    "options": "path=projects/",
    "required": True,
}
GRANTS = [STORAGE_REQUEST, {**HOME_REQUEST, "options": "path=*"}]


def _vol(options: dict[str, Any] | None = None) -> dict[str, Any]:
    """A channel's ``volumes`` entry that persists the volume with these options."""
    return {"type": "persist", **(options or {})}


def _persist_channel(volumes: Any, requests: list[dict[str, Any]] | None = None) -> ContainerChannel:
    return ContainerChannel.model_validate(
        _channel(volumes=volumes, customer_access_requests=GRANTS if requests is None else requests)
    )


def _persisted(text: str, volumes: Any) -> list[dict[str, Any]]:
    """The manifest's ``persist`` of the one service, compiled with the channel's ``volumes``."""
    manifest = compile_instance_spec(text, _res(), volumes=volumes).manifest
    return [p.model_dump(exclude_none=True) for p in manifest.services[0].persist or []]


def _persisted_errors(declaration: Any, requests: list[dict[str, Any]] | None) -> list[str]:
    channel = _persist_channel({"home": _vol(declaration)}, requests)
    return [e.code for e in validate_instance_spec(_persisting(), channel=channel).errors]


def _compile_errors(text: str, volumes: Any) -> list[Any]:
    with pytest.raises(InstanceSpecError) as raised:
        compile_instance_spec(text, _res(), volumes=volumes)
    return raised.value.report.errors


class TestPersistedVolumesAreRequested:
    def test_the_channel_must_ask_for_the_enrollments_storage(self) -> None:
        assert _persisted_errors({}, [REQUEST]) == ["persist_not_requested"]
        assert _persisted_errors({}, [STORAGE_REQUEST]) == []

    def test_write_is_needed_unless_the_directory_is_read_only(self) -> None:
        read = {**STORAGE_REQUEST, "permissions": ["read"]}
        assert _persisted_errors({}, [read]) == ["persist_not_requested"]
        assert _persisted_errors({"mode": "ro"}, [read]) == []

    def test_an_optional_request_does_not_count(self) -> None:
        assert _persisted_errors({}, [{**STORAGE_REQUEST, "required": False}]) == ["persist_not_requested"]

    def test_a_home_folder_needs_a_request_whose_path_covers_it(self) -> None:
        source = {"source": "{{ home.path }}/projects/a"}
        assert _persisted_errors(source, [HOME_REQUEST]) == []
        assert _persisted_errors(source, [{**HOME_REQUEST, "options": "path=*"}]) == []
        assert _persisted_errors(source, [{**HOME_REQUEST, "options": "path=other/"}]) == ["persist_not_requested"]
        assert _persisted_errors(source, [{**HOME_REQUEST, "options": ""}]) == ["persist_not_requested"]
        assert _persisted_errors(source, [STORAGE_REQUEST]) == ["persist_not_requested"]

    def test_a_channel_that_persists_nothing_asks_for_nothing(self) -> None:
        channel = ContainerChannel.model_validate(_channel())
        assert validate_instance_spec(_persisting(), channel=channel).ok

    def test_without_a_channel_nothing_is_checked(self) -> None:
        assert validate_instance_spec(_persisting()).ok


class TestPersistedVolumes:
    def test_the_same_file_is_scratch_for_one_channel_and_persistent_for_another(self) -> None:
        text = _persisting()
        demo = _persist_channel(None)
        production = _persist_channel({"home": _vol({})})
        demo_report = validate_instance_spec(text, channel=demo)
        assert demo_report.ok and "scratch_volume" in _codes(demo_report, "warnings")
        assert "persistent_volume" not in _codes(demo_report, "warnings")
        prod_report = validate_instance_spec(text, channel=production)
        assert prod_report.ok, prod_report.errors
        assert "persistent_volume" in _codes(prod_report, "warnings")
        assert "scratch_volume" not in _codes(prod_report, "warnings")

        scratch = compile_instance_spec(text, _res()).manifest.services[0]
        assert scratch.persist is None and [s.path for s in scratch.scratch or []] == ["/home/app"]
        persisted = compile_instance_spec(text, _res(), volumes=production.volumes).manifest.services[0]
        assert persisted.scratch is None
        assert [p.path for p in persisted.persist or []] == ["/home/app"]

    def test_the_declaration_defaults_to_the_enrollments_folder_named_like_the_volume(self) -> None:
        assert _persisted(_persisting(), {"home": _vol({})}) == [
            {"path": "/home/app", "store": "storage", "folder": "home", "mode": "rw"}
        ]

    def test_a_persisted_volume_is_not_scratch(self) -> None:
        text = _persisting()
        report = validate_instance_spec(text, channel=_persist_channel({"home": _vol({})}))
        assert not [w for w in report.warnings if w.code == "scratch_volume"]
        warning = next(w for w in report.warnings if w.code == "persistent_volume")
        assert not warning.loud and "outlive the instance" in warning.message

    def test_every_field(self) -> None:
        persist = {
            "source": "{{ home.path }}/projects/a",
            "mode": "ro",
            "exclude": ["cache/**", "*.tmp"],
            "uid": 1000,
            "gid": 1000,
        }
        assert _persisted(_persisting(), {"home": _vol(persist)}) == [
            {
                "path": "/home/app",
                "store": "home",
                "folder": "projects/a",
                "mode": "ro",
                "exclude": ["cache/**", "*.tmp"],
                "uid": 1000,
                "gid": 1000,
            }
        ]

    def test_the_source_may_be_spaced_and_names_a_subfolder_of_the_enrollment(self) -> None:
        volumes = {"home": _vol({"source": "{{storage.path}}/state/db"})}
        assert _persisted(_persisting(), volumes)[0]["folder"] == "state/db"

    def test_the_other_volumes_of_a_service_stay_scratch(self) -> None:
        text = _persisting(["home:/home/app", "/cache"])
        manifest = compile_instance_spec(text, _res(), volumes={"home": _vol({})}).manifest
        assert [p.path for p in manifest.services[0].persist or []] == ["/home/app"]
        assert [s.path for s in manifest.services[0].scratch or []] == ["/cache"]

    BAD = [
        ({"source": "s3://bucket/x"}, "source"),
        ({"source": "{{ storage.path }}"}, "source"),  # a folder is required
        ({"source": "{{ storage.path }}/"}, "source"),
        ({"source": "{{ storage.path }}/../other"}, "source"),
        ({"source": "{{ storage.path }}/a//b"}, "source"),
        ({"source": "{{ storage.path }}/a b"}, "source"),
        ({"source": "{{ storage.path }}/.hidden"}, "source"),
        ({"source": "{{ storage.path }}/a..b"}, "source"),
        ({"source": "{{ enrollment.path }}/x"}, "source"),  # it is `storage`, as the service:storage grant
        ({"source": "{{ service_name }}/x"}, "source"),
        ({"source": "{{ params.X }}/x"}, "source"),
        ({"source": 5}, "source"),
        ({"mode": "wo"}, "mode"),
        ({"exclude": "cache"}, "exclude"),
        ({"exclude": ["/etc"]}, "exclude"),
        ({"exclude": ["../x"]}, "exclude"),
        ({"exclude": ["[a"]}, "exclude"),
        ({"exclude": [f"p{i}" for i in range(33)]}, "exclude"),
        ({"uid": 1000}, ""),
        ({"uid": -1, "gid": 0}, ""),
        ({"uid": True, "gid": 1}, ""),
        ({"colour": "red"}, "colour"),
    ]

    @pytest.mark.parametrize(("declaration", "path"), BAD)
    def test_a_bad_declaration_is_refused_by_the_channel(self, declaration: dict[str, Any], path: str) -> None:
        with pytest.raises(ValidationError):
            ContainerChannel.model_validate(_channel(volumes={"home": _vol(declaration)}))

    @pytest.mark.parametrize(("declaration", "path"), BAD)
    def test_a_bad_declaration_is_an_error_when_compiling(self, declaration: dict[str, Any], path: str) -> None:
        errors = _compile_errors(_persisting(), {"home": _vol(declaration)})
        assert all(i.path == "volumes.home" for i in errors), errors
        assert any(path in i.message for i in errors), errors

    def test_the_overlay_must_be_a_mapping_of_volumes(self) -> None:
        assert [e.code for e in _compile_errors(_persisting(), ["home"])] == ["bad_volumes"]

    # -- the channel must match the file's volumes

    def test_a_volume_without_a_kind_is_scratch(self) -> None:
        text = _persisting()
        for volumes in ({"home": {}}, {"home": None}, {"home": {"type": "scratch"}}, {}):
            manifest = compile_instance_spec(text, _res(), volumes=volumes).manifest.services[0]
            assert manifest.persist is None and [s.path for s in manifest.scratch or []] == ["/home/app"]

    def test_the_channel_refuses_a_kind_it_does_not_know(self) -> None:
        with pytest.raises(ValidationError):
            ContainerChannel.model_validate(_channel(volumes={"home": {"type": "block", "size": "10Gi"}}))
        errors = _compile_errors(_persisting(), {"home": {"type": "block", "size": "10Gi"}})
        assert errors and all(e.path == "volumes.home" for e in errors)
        with pytest.raises(ValidationError):  # persist options on a scratch volume
            ContainerChannel.model_validate(_channel(volumes={"home": {"type": "scratch", "mode": "ro"}}))
        with pytest.raises(ValidationError):  # the old nested form
            ContainerChannel.model_validate(_channel(volumes={"home": {"persist": {}}}))

    def test_a_persisted_name_must_be_a_volume_of_the_file(self) -> None:
        errors = _compile_errors(_persisting(), {"data": _vol({})})
        assert [e.code for e in errors] == ["overlay_volume"] and "home" in errors[0].message
        channel = _persist_channel({"data": _vol({})})
        assert "overlay_volume" in _codes(validate_instance_spec(_persisting(), channel=channel))

    def test_a_persisted_volume_must_be_mounted_by_a_service(self) -> None:
        text = yaml.safe_dump(
            {"services": {"web": {"image": f"example/app{DIGEST}"}}, "volumes": {"home": {}, "unused": {}}}
        )
        errors = _compile_errors(text, {"unused": _vol({})})
        assert [e.code for e in errors] == ["persist_unmounted"] and errors[0].path == "volumes.unused"

    def test_only_a_named_volume_can_be_persisted(self) -> None:
        for name in ("/cache", "./data", "a b", ""):
            with pytest.raises(ValidationError):
                ContainerChannel.model_validate(_channel(volumes={name: {}}))

    def test_a_file_volume_the_channel_does_not_name_is_untouched(self) -> None:
        # `home` is declared and mounted in the file; the channel persists another volume only.
        text = yaml.safe_dump(
            {
                "services": {"web": {"image": f"example/app{DIGEST}", "volumes": ["home:/home/app", "db:/var/db"]}},
                "volumes": {"home": {}, "db": {}},
            }
        )
        manifest = compile_instance_spec(text, _res(), volumes={"db": _vol({})}).manifest.services[0]
        assert [p.path for p in manifest.persist or []] == ["/var/db"]
        assert [s.path for s in manifest.scratch or []] == ["/home/app"]

    def test_the_old_extension_in_the_file_is_refused_with_a_pointer(self) -> None:
        text = yaml.safe_dump(
            {
                "services": {"web": {"image": f"example/app{DIGEST}", "volumes": ["home:/home/app"]}},
                "volumes": {"home": {"x-unitysvc-persist": {}}},
            }
        )
        report = validate_instance_spec(text)
        assert [e.code for e in report.errors] == ["persist_in_file"]
        assert "volumes.home" in report.errors[0].message and "type: persist" in report.errors[0].message

    # -- mounts

    def test_a_persistent_volume_cannot_be_mounted_twice(self) -> None:
        text = _persisting(["home:/a", "home:/b"])
        assert "duplicate_mount" in {e.code for e in _compile_errors(text, {"home": _vol({})})}

    def test_a_persisted_path_cannot_be_mounted_twice(self) -> None:
        text = _persisting(["home:/data", "/data"])
        assert "duplicate_mount" in {e.code for e in _compile_errors(text, {"home": _vol({})})}

    def test_two_volumes_cannot_share_a_folder(self) -> None:
        text = yaml.safe_dump(
            {
                "services": {"web": {"image": f"example/app{DIGEST}", "volumes": ["a:/a", "b:/b"]}},
                "volumes": {"a": {}, "b": {}},
            }
        )
        same = {"source": "{{ storage.path }}/same"}
        errors = _compile_errors(text, {"a": _vol(same), "b": _vol(same)})
        assert [e.code for e in errors] == ["duplicate_mount"] and "same folder" in errors[0].message

    def test_other_extensions_are_still_ignored_and_other_keys_still_rejected(self) -> None:
        ok = yaml.safe_dump({"services": {"web": {"image": f"example/app{DIGEST}"}}, "volumes": {"v": {"x-other": 1}}})
        assert validate_instance_spec(ok).ok
        bad = yaml.safe_dump(
            {"services": {"web": {"image": f"example/app{DIGEST}"}}, "volumes": {"v": {"driver": "nfs"}}}
        )
        report = validate_instance_spec(bad)
        assert not report.ok and "only a plain volume" in report.errors[0].message

    def test_the_manifest_schema_has_persist(self) -> None:
        volumes = {
            "home": _vol({"source": "{{ home.path }}/a", "mode": "ro", "exclude": ["*.tmp"], "uid": 1, "gid": 2})
        }
        manifest = compile_instance_spec(_persisting(), _res(), volumes=volumes).manifest.model_dump(
            mode="json", exclude_none=True
        )
        assert list(Draft7Validator(MANIFEST_SCHEMA).iter_errors(manifest)) == []
        assert manifest["services"][0]["persist"][0]["store"] == "home"
        bad = {"services": [{"name": "a", "image": "i", "persist": [{"path": "/d", "store": "s3", "folder": "x"}]}]}
        assert list(Draft7Validator(MANIFEST_SCHEMA).iter_errors(bad)) != []
        with pytest.raises(ValidationError):
            NormalizedManifest.model_validate(bad)

    def test_the_model_refuses_what_the_agent_would(self) -> None:
        def service(**persist: Any) -> dict[str, Any]:
            entry = {"path": "/d", "store": "storage", "folder": "x", **persist}
            return {"services": [{"name": "a", "image": "i", "persist": [entry]}]}

        NormalizedManifest.model_validate(service())
        for change in (
            {"path": "d"},
            {"path": "/"},
            {"folder": "/x"},
            {"folder": "../x"},
            {"folder": ""},
            {"mode": "wo"},
            {"uid": 1},
            {"exclude": ["/x"]},
            {"colour": "red"},
        ):
            with pytest.raises(ValidationError):
                NormalizedManifest.model_validate(service(**change))
        with pytest.raises(ValidationError, match="distinct"):
            NormalizedManifest.model_validate(
                {
                    "services": [
                        {
                            "name": "a",
                            "image": "i",
                            "persist": [
                                {"path": "/d", "store": "storage", "folder": "x"},
                                {"path": "/d", "store": "storage", "folder": "y"},
                            ],
                        }
                    ]
                }
            )
        with pytest.raises(ValidationError, match="distinct"):
            NormalizedManifest.model_validate(
                {
                    "services": [
                        {
                            "name": "a",
                            "image": "i",
                            "scratch": [{"path": "/d"}],
                            "persist": [{"path": "/d", "store": "storage", "folder": "x"}],
                        }
                    ]
                }
            )
        with pytest.raises(ValidationError, match="share a folder"):
            NormalizedManifest.model_validate(
                {
                    "services": [
                        {
                            "name": "a",
                            "image": "i",
                            "persist": [
                                {"path": "/d", "store": "storage", "folder": "x"},
                                {"path": "/e", "store": "storage", "folder": "x"},
                            ],
                        }
                    ]
                }
            )

    def test_the_agents_wire_shape(self) -> None:
        """The agent decodes strictly, with exactly these keys (unitysvc-compute-k8s intent.Persist)."""
        entry = _persisted(_persisting(), {"home": _vol({"mode": "ro", "exclude": ["a"], "uid": 0, "gid": 0})})[0]
        assert set(entry) == {"path", "store", "folder", "mode", "exclude", "uid", "gid"}
