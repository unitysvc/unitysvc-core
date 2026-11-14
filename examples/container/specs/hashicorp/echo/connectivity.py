"""Connectivity test: pull, start and check a core-normalized container manifest."""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from decimal import ROUND_CEILING
from pathlib import Path
from urllib.parse import urlsplit


def quantity(value):
    from unitysvc_core.models.container import resource_quantity

    return resource_quantity(value)


class DockerTest:
    def __init__(self, manifest, timeout=120):
        self.manifest = manifest
        self.timeout = timeout
        self.deadline = time.monotonic() + timeout
        self.tag = uuid.uuid4().hex
        self.label = f"com.unitysvc.container-test={self.tag}"
        self.network = f"usvc-test-{self.tag}"
        self.containers = []
        self.local_verified = False

    def docker(self, *args, timeout=None, check=True):
        remaining = self.deadline - time.monotonic() if timeout is None else timeout
        if remaining <= 0:
            raise RuntimeError("container test deadline exceeded")
        try:
            result = subprocess.run(
                ["docker", *args],
                capture_output=True,
                text=True,
                timeout=remaining,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"docker {args[0]} timed out") from exc
        if check and result.returncode:
            raise RuntimeError(f"docker {args[0]} failed: {result.stderr.strip()[:1500]}")
        return result

    def launch(self):
        self.docker("info", "--format", "{{.ServerVersion}}")
        host = os.environ.get("DOCKER_HOST")
        if os.environ.get("DOCKER_CONTEXT") or not host:
            host = json.loads(self.docker("context", "inspect", "--format", "{{json .Endpoints.docker.Host}}").stdout)
        parsed = urlsplit(host)
        if parsed.scheme not in {"unix", "npipe"} and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("use a local Docker context; health probes connect to loopback")
        self.local_verified = True
        for image in sorted({service["image"] for service in self.manifest["services"]}):
            self.docker("pull", "--", image)
        self.docker("network", "create", "--label", self.label, self.network)
        for service in self.manifest["services"]:
            limits = service["resources"]["limits"]
            unsupported = set(limits) - {"cpu", "memory", "ephemeral-storage"}
            if unsupported:
                raise RuntimeError(f"local Docker cannot test extended resources {sorted(unsupported)}")
            for replica in range(service.get("replicas", 1)):
                args = [
                    "create",
                    "--name",
                    f"usvc-test-{self.tag}-{service['name']}-{replica}",
                    "--label",
                    self.label,
                    "--network",
                    self.network,
                    "--network-alias",
                    service["name"],
                    "--no-healthcheck",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--cpus",
                    format(quantity(limits["cpu"]), "f"),
                    "--memory",
                    str(int(quantity(limits["memory"]).to_integral_value(rounding=ROUND_CEILING))),
                ]
                for key, value in sorted(service.get("env", {}).items()):
                    args += ["--env", f"{key}={value}"]
                for port in service.get("ports", []):
                    args += ["--publish", f"127.0.0.1::{port}/tcp"]
                command = service.get("command") or []
                if command:
                    args += ["--entrypoint", command[0]]
                args += ["--", service["image"], *command[1:], *(service.get("args") or [])]
                cid = self.docker(*args).stdout.strip()
                self.containers.append((cid, service))
                self.docker("start", cid)

    def probe(self, cid, service, health, inspected):
        timeout = min(health.get("timeout_seconds", 2), self.deadline - time.monotonic())
        if timeout <= 0:
            return False
        if health["type"] == "exec":
            try:
                return self.docker("exec", cid, *health["command"], timeout=timeout, check=False).returncode == 0
            except TimeoutError:
                return False
        port = health.get("port") or service["ports"][0]
        bindings = inspected["NetworkSettings"]["Ports"].get(f"{port}/tcp") or []
        if not bindings:
            return False
        host_port = int(bindings[0]["HostPort"])
        if health["type"] == "tcp":
            try:
                with socket.create_connection(("127.0.0.1", host_port), timeout=timeout):
                    return True
            except OSError:
                return False
        connection = http.client.HTTPConnection("127.0.0.1", host_port, timeout=timeout)
        try:
            connection.request("GET", health["path"])
            return 200 <= connection.getresponse().status < 400
        except (OSError, http.client.HTTPException):
            return False
        finally:
            connection.close()

    def wait_healthy(self):
        states = {}
        for cid, service in self.containers:
            health = service.get("health")
            if health is None and service.get("ports"):
                health = {"type": "tcp", "period_seconds": 2}
            period = health.get("period_seconds", 5) if health else 1
            start = health.get("start_period_seconds", min(60, self.timeout)) if health else 0
            if start > self.timeout:
                raise RuntimeError(f"{service['name']}: start period exceeds test deadline")
            states[cid] = {
                "health": health,
                "period": period,
                "startup": bool(health and start),
                "startup_failures": math.ceil(start / period) if start else 0,
                "failures": 0,
                "successes": 0,
                "ready": False,
                "next": 0,
            }
        while time.monotonic() < self.deadline:
            for cid, service in self.containers:
                state = states[cid]
                if time.monotonic() < state["next"]:
                    continue
                inspected = json.loads(self.docker("inspect", cid).stdout)[0]
                if not inspected["State"].get("Running"):
                    code = inspected["State"].get("ExitCode")
                    raise RuntimeError(f"{service['name']}: exited before readiness (exit {code})")
                health = state["health"]
                if health is None:
                    state["ready"] = True
                    continue
                passed = self.probe(cid, service, health, inspected)
                state["next"] = time.monotonic() + state["period"]
                if passed:
                    state["failures"] = 0
                    if state["startup"]:
                        state.update(startup=False, successes=0, next=0)
                    else:
                        state["successes"] += 1
                        if state["successes"] >= health.get("success_threshold", 1):
                            state["ready"] = True
                else:
                    state["successes"] = 0
                    state["failures"] += 1
                    if state["startup"] and state["failures"] >= state["startup_failures"]:
                        raise RuntimeError(f"{service['name']}: startup health failed")
                    if state["failures"] >= health.get("failure_threshold", 3):
                        state["ready"] = False
            if all(state["ready"] for state in states.values()):
                return
            time.sleep(min(0.1, max(0, self.deadline - time.monotonic())))
        waiting = [service["name"] for cid, service in self.containers if not states[cid]["ready"]]
        raise RuntimeError(f"health deadline exceeded for {sorted(set(waiting))}")

    def cleanup(self):
        if not self.local_verified:
            return
        errors = []
        # The label also finds resources whose create response timed out.
        for listing, removal in (
            (["ps", "-aq"], ["rm", "--force", "--volumes"]),
            (["network", "ls", "-q"], ["network", "rm"]),
        ):
            try:
                ids = self.docker(*listing, "--filter", f"label={self.label}", timeout=5).stdout.split()
                if ids:
                    self.docker(*removal, *ids, timeout=10)
            except (RuntimeError, OSError) as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("cleanup incomplete: " + "; ".join(errors))

    def run(self):
        result = {"success": False, "phase": "failed", "error": None}
        try:
            self.launch()
            self.wait_healthy()
            result.update(success=True, phase="ready")
        except (RuntimeError, OSError, ValueError, KeyboardInterrupt) as exc:
            result["error"] = str(exc) or "test interrupted"
        finally:
            try:
                self.cleanup()
            except RuntimeError as exc:
                result.update(success=False, phase="failed", cleanup_error=str(exc))
        result["limitations"] = (
            "Local Docker only; Kubernetes/gVisor, scheduling, network policy and disk quotas untested"
        )
        return result


def _interrupt(*_args):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("USVC_CONTAINER_TIMEOUT_SECONDS", "120")))
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("timeout must be between 1 and 600 seconds")
    signal.signal(signal.SIGTERM, _interrupt)
    try:
        from unitysvc_core.models.container import ContainerManifest

        content = args.manifest.read_text() if args.manifest else os.environ["USVC_CONTAINER_MANIFEST_JSON"]
        manifest = ContainerManifest.model_validate_json(content).runtime_dict()
        result = DockerTest(manifest, args.timeout).run()
    except (ImportError, KeyError, OSError, ValueError) as exc:
        result = {"success": False, "phase": "failed", "error": str(exc)}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
