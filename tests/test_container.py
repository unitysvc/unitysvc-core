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
    InstanceSpecError,
    compile_instance_spec,
    validate_instance_spec,
)
from unitysvc_core.models.channel import ContainerChannel, UpstreamAccessConfig
from unitysvc_core.models.container import ContainerResources, parse_quantity
from unitysvc_core.models.documents import DocumentData
from unitysvc_core.models.manifest import NormalizedManifest
from unitysvc_core.models.probes import validate_probes_text
from unitysvc_core.validator import DataValidator

ROOT = Path(__file__).parent
EXAMPLES = ROOT / "example_data"
ECHO = EXAMPLES / "hashicorp" / "services" / "echo"
WEIXIN = EXAMPLES / "kadxy" / "services" / "weixin-ilink-gateway"
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
        "spec": "Application",
        "resources": {"limits": dict(LIMITS)},
        "customer_access_requests": [dict(REQUEST)],
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


class TestResources:
    def test_the_tier_needs_cpu_memory_and_storage(self) -> None:
        with pytest.raises(ValidationError, match="must set ephemeral-storage"):
            ContainerResources.model_validate({"limits": {"cpu": "1", "memory": "1Gi"}})

    def test_only_the_reserved_names(self) -> None:
        with pytest.raises(ValidationError, match="unknown resource"):
            ContainerResources.model_validate({"limits": {**LIMITS, "hugepages-2Mi": "1Gi"}})

    def test_gpu_is_whole_and_its_request_equals_the_limit(self) -> None:
        ContainerResources.model_validate({"limits": {**LIMITS, "nvidia.com/gpu": "1"}})
        with pytest.raises(ValidationError, match="whole number"):
            ContainerResources.model_validate({"limits": {**LIMITS, "nvidia.com/gpu": "500m"}})
        with pytest.raises(ValidationError, match="must equal the limit"):
            ContainerResources.model_validate(
                {"limits": {**LIMITS, "nvidia.com/gpu": "2"}, "requests": {"nvidia.com/gpu": "1"}}
            )

    def test_a_request_is_at_most_its_limit(self) -> None:
        with pytest.raises(ValidationError, match="above the limit"):
            ContainerResources.model_validate({"limits": LIMITS, "requests": {"cpu": "1"}})
        ContainerResources.model_validate({"limits": LIMITS, "requests": {"cpu": "100m"}})

    def test_quantities_must_be_positive_and_valid(self) -> None:
        with pytest.raises(ValidationError, match="not a Kubernetes quantity"):
            ContainerResources.model_validate({"limits": {**LIMITS, "memory": "1KB"}})
        with pytest.raises(ValidationError, match="positive"):
            ContainerResources.model_validate({"limits": {**LIMITS, "cpu": "0"}})

    def test_the_split_sums_within_the_tier(self) -> None:
        split = {
            "a": {"limits": {"cpu": "100m", "memory": "128Mi", "ephemeral-storage": "512Mi"}},
            "b": {"limits": {"cpu": "150m", "memory": "128Mi", "ephemeral-storage": "512Mi"}},
        }
        ContainerResources.model_validate({"limits": LIMITS, "services": split})
        split["b"]["limits"]["cpu"] = "200m"
        with pytest.raises(ValidationError, match="limits of cpu add up to more than the tier"):
            ContainerResources.model_validate({"limits": LIMITS, "services": split})

    def test_the_split_requests_count_too(self) -> None:
        split = {
            "a": {"limits": {"cpu": "1", "memory": "100Mi", "ephemeral-storage": "100Mi"}, "requests": {"cpu": "200m"}},
            "b": {"limits": {"cpu": "1", "memory": "100Mi", "ephemeral-storage": "100Mi"}, "requests": {"cpu": "100m"}},
        }
        tier = {"limits": {"cpu": "2", "memory": "1Gi", "ephemeral-storage": "1Gi"}}
        with pytest.raises(ValidationError, match="requests of cpu add up"):
            ContainerResources.model_validate({**tier, "requests": {"cpu": "250m"}, "services": split})
        ContainerResources.model_validate({**tier, "requests": {"cpu": "300m"}, "services": split})


# --- the channel ----------------------------------------------------------------------------------


class TestChannel:
    def test_is_in_the_union(self) -> None:
        config = UpstreamAccessConfig.model_validate({"base": _channel()})
        channel = config.root["base"]
        assert isinstance(channel, ContainerChannel)
        assert channel.servers == []

    def test_requires_spec_and_resources(self) -> None:
        for key in ("spec", "resources"):
            data = _channel()
            del data[key]
            with pytest.raises(ValidationError, match=key):
                ContainerChannel.model_validate(data)

    @pytest.mark.parametrize(
        "requests",
        [
            None,
            [],
            [{**REQUEST, "required": False}],
            [{**REQUEST, "permissions": ["join"]}],
            [{"object": "service:x", "permissions": ["invoke"], "required": True, "justification": "x"}],
        ],
    )
    def test_requires_the_container_create_request(self, requests: Any) -> None:
        data = _channel(customer_access_requests=requests)
        with pytest.raises(ValidationError, match="resource:container"):
            ContainerChannel.model_validate(data)

    def test_the_request_passes_the_grants_checks(self, tmp_path: Path) -> None:
        (tmp_path / "compose.yaml").write_text(_compose())
        errors = DataValidator(EXAMPLES, SCHEMA_DIR).validate_service(
            offering=_offering({"base": _channel()}), service_dir=tmp_path
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
        compiled = compile_instance_spec(text, ContainerResources.model_validate({"limits": LIMITS}))
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
            ContainerResources.model_validate({"limits": LIMITS}),
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
        compiled = compile_instance_spec(_compose(ports=[port]), ContainerResources.model_validate({"limits": LIMITS}))
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
        compiled = compile_instance_spec(
            _compose(**{key: values[key]}), ContainerResources.model_validate({"limits": LIMITS})
        )
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
        assert "does not survive a restart" in scratch[0].message


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
            ("env_file", ".env"),
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
        ["nginx", "nginx:latest", "nginx:1.27", "nginx@sha256:abc", "nginx@sha256:" + "A" * 64, "nginx${TAG}" + DIGEST],
    )
    def test_images_must_be_pinned_by_a_literal_digest(self, image: str) -> None:
        assert not validate_instance_spec(_compose(image=image)).ok

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
        report = validate_instance_spec(text, channel=_container(resources_split=("front", "back")))
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

    def test_siblings_are_allowed_and_need_a_split(self) -> None:
        assert validate_instance_spec(self.two()).ok
        report = validate_instance_spec(self.two(), channel=_container())
        assert "split_required" in _codes(report)
        report = validate_instance_spec(self.two(), channel=_container(resources_split=("web", "redis")))
        assert report.ok, report.errors

    def test_the_split_must_name_the_services(self) -> None:
        report = validate_instance_spec(self.two(), channel=_container(resources_split=("web", "ghost")))
        assert {"split_service", "split_missing"} <= _codes(report)

    def test_compile_with_a_split(self) -> None:
        resources = ContainerResources.model_validate(
            {
                "limits": {"cpu": "1", "memory": "1Gi", "ephemeral-storage": "2Gi"},
                "services": {
                    "web": {"limits": {"cpu": "750m", "memory": "768Mi", "ephemeral-storage": "1Gi"}},
                    "redis": {"limits": {"cpu": "250m", "memory": "256Mi", "ephemeral-storage": "1Gi"}},
                },
            }
        )
        manifest = compile_instance_spec(self.two(), resources).manifest
        by_name = {s.name: s for s in manifest.services}
        assert by_name["web"].resources is not None and by_name["web"].resources.limits == {
            "cpu": "750m",
            "memory": "768Mi",
            "ephemeral-storage": "1Gi",
        }
        assert by_name["redis"].resources is not None and by_name["redis"].resources.limits is not None
        assert by_name["redis"].resources.limits["cpu"] == "250m"

    def test_compile_without_a_split_is_refused(self) -> None:
        with pytest.raises(InstanceSpecError, match="split the tier"):
            compile_instance_spec(self.two(), ContainerResources.model_validate({"limits": LIMITS}))


class TestEntries:
    def entry(self, **changes: Any) -> Any:
        return _container(entries=[{"name": "web", "service": "web", "port": 8080, "auth": "key", **changes}])

    def test_an_entry_targets_a_declared_service_and_port(self) -> None:
        assert validate_instance_spec(_compose(), channel=self.entry()).ok
        assert "entry_service" in _codes(validate_instance_spec(_compose(), channel=self.entry(service="api")))
        assert "entry_port" in _codes(validate_instance_spec(_compose(), channel=self.entry(port=9090)))


def _container(*, resources_split: tuple[str, ...] = (), **changes: Any) -> ContainerChannel:
    resources: dict[str, Any] = {"limits": {"cpu": "1", "memory": "1Gi", "ephemeral-storage": "2Gi"}}
    if resources_split:
        share = {"cpu": "100m", "memory": "100Mi", "ephemeral-storage": "100Mi"}
        resources["services"] = {name: {"limits": dict(share)} for name in resources_split}
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
        compiled = compile_instance_spec((service_dir / "compose.yaml").read_text(), channel.resources)
        expected = json.loads((FIXTURES / "manifest" / golden).read_text())
        assert compiled.manifest.model_dump(exclude_none=True) == expected
        assert list(Draft7Validator(MANIFEST_SCHEMA).iter_errors(expected)) == []

    def test_dropped_keys_are_recorded(self) -> None:
        offering = json.loads((WEIXIN / "offering.json").read_text())
        channel = ContainerChannel.model_validate(offering["upstream_access_config"]["base"])
        compiled = compile_instance_spec((WEIXIN / "compose.yaml").read_text(), channel.resources)
        assert {(d.service, d.key, d.reason) for d in compiled.dropped} == {
            ("gateway", "deploy.resources", "ignored"),
            ("gateway", "volumes", "not_carried"),
        }
        assert any(w.loud for w in compiled.warnings)

    def test_both_tiers_share_one_file(self) -> None:
        offering = json.loads((ECHO / "offering.json").read_text())
        text = (ECHO / "compose.yaml").read_text()
        limits = {}
        for name, raw in offering["upstream_access_config"].items():
            manifest = compile_instance_spec(text, ContainerChannel.model_validate(raw).resources).manifest
            resources = manifest.services[0].resources
            assert resources is not None
            limits[name] = resources.limits
        assert limits["base"]["cpu"] == "250m" and limits["medium"]["cpu"] == "1"

    def test_requests_are_carried(self) -> None:
        resources = ContainerResources.model_validate({"limits": LIMITS, "requests": {"cpu": "100m"}})
        manifest = compile_instance_spec(_compose(), resources).manifest
        assert manifest.services[0].resources is not None
        assert manifest.services[0].resources.requests == {"cpu": "100m"}

    def test_an_invalid_spec_does_not_compile(self) -> None:
        with pytest.raises(InstanceSpecError) as caught:
            compile_instance_spec(_compose(image="nginx:latest"), ContainerResources.model_validate({"limits": LIMITS}))
        assert "unpinned_image" in {e.code for e in caught.value.report.errors}


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
        "documents": {"Application": {"category": "instance_spec", "mime_type": "text", "file_path": "compose.yaml"}},
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

    def test_the_original_weixin_compose_is_rejected(self) -> None:
        report = validate_instance_spec((WEIXIN / "docker-compose.original.yaml").read_text())
        assert {"unpinned_image", "rejected_key"} <= _codes(report)
        assert any(e.path == "services.gateway.env_file" for e in report.errors)
        assert any(e.path == "services.gateway.image" for e in report.errors)
        assert "host_port" in _codes(report, "warnings")

    def test_a_bad_spec_fails_the_offering(self, validator: DataValidator, tmp_path: Path) -> None:
        (tmp_path / "compose.yaml").write_text((WEIXIN / "docker-compose.original.yaml").read_text())
        offering = json.loads((ECHO / "offering.json").read_text())
        errors = validator.validate_service(offering=offering, service_dir=tmp_path)["offering"]
        assert any("env_file" in e for e in errors) and any("upstream_access_config.base.spec" in e for e in errors)

    def test_spec_must_name_a_document_of_the_category(self, validator: DataValidator) -> None:
        offering = json.loads((ECHO / "offering.json").read_text())
        offering["upstream_access_config"]["base"]["spec"] = "Missing"
        assert any(
            "no document titled 'Missing'" in e
            for e in validator.validate_service(offering=offering, service_dir=ECHO)["offering"]
        )
        offering = json.loads((ECHO / "offering.json").read_text())
        offering["documents"]["Application"]["category"] = "other"
        assert any(
            "category 'instance_spec'" in e
            for e in validator.validate_service(offering=offering, service_dir=ECHO)["offering"]
        )

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
