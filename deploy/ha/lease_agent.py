#!/usr/bin/env python3
"""Maintain the provider-neutral writer lease and atomic local witness file."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib import error, request


ROOT = Path(os.getenv("MP_ROOT", "/opt/masterplan"))
HA_HOME = Path(os.getenv("MP_HA_HOME", "/etc/mp-opt-ha"))
DEPLOYMENT_POLICY = Path(os.getenv("MP_DEPLOYMENT_POLICY_FILE", "/etc/mp-opt/deployment-policy"))
CONTROL_PATH = ROOT / "runtime/ha-control.json"
RECEIVER_PATH = ROOT / "runtime/ha-receiver.json"
SMTP_STATUS_PATH = ROOT / "runtime/ha-smtp-status.json"
CONNECTION_DRAIN_PATH = ROOT / "runtime/ha-connection-drain.json"


def read_config() -> dict[str, str]:
    allowed = {
        "HA_MODE", "HA_NODE_ID", "HA_CLUSTER_ID", "HA_PEER_NODE_ID",
        "HA_WITNESS_URL", "HA_AUTOMATIC_FAILOVER",
    }
    result: dict[str, str] = {}
    for line in (HA_HOME / "node.env").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in allowed and "\n" not in value and "\r" not in value:
            result[key] = value
    required = {"HA_NODE_ID", "HA_CLUSTER_ID", "HA_WITNESS_URL"}
    if result.get("HA_MODE") != "ha" or not required.issubset(result):
        raise RuntimeError("The symmetric HA node configuration is incomplete")
    return result


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o644)
    temporary.replace(path)


def post(config: dict[str, str], action: str, payload: dict) -> dict:
    token = (HA_HOME / "secrets/node_token").read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise RuntimeError("The HA node token is missing")
    req = request.Request(
        f"{config['HA_WITNESS_URL'].rstrip('/')}/v1/clusters/{config['HA_CLUSTER_ID']}/{action}",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "MP-OPT-HA/1.0",
        },
    )
    try:
        with request.urlopen(req, timeout=5) as response:
            raw = response.read(65537)
    except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("The HA lease authority is unavailable") from exc
    if len(raw) > 65536:
        raise RuntimeError("The HA lease response was too large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise RuntimeError("The HA lease response was invalid")
    return result


def test_override_enabled() -> bool:
    override = ROOT / ".test-deployment.env"
    if not override.is_file():
        return False
    if not DEPLOYMENT_POLICY.is_file() or DEPLOYMENT_POLICY.read_text(encoding="utf-8").strip() != "test":
        raise RuntimeError("unsigned deployment override exists outside test policy")
    return True


def release_hash() -> str:
    release_environment = ROOT / ".release.env"
    test_environment = ROOT / ".test-deployment.env"
    identity_environment = test_environment if test_override_enabled() else release_environment
    if identity_environment.is_file():
        for line in identity_environment.read_text(encoding="utf-8").splitlines():
            if line.startswith("MP_TEST_COMMIT="):
                value = line.split("=", 1)[1]
                return value if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) else ""
            if line.startswith("MP_RELEASE_COMMIT="):
                value = line.split("=", 1)[1]
                return value if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) else ""
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=False, capture_output=True, text=True, timeout=5,
    )
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) else ""


def receiver_state() -> dict:
    try:
        result = json.loads(RECEIVER_PATH.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def smtp_state() -> dict:
    try:
        result = json.loads(SMTP_STATUS_PATH.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def compose_command() -> list[str]:
    compose = [
        "docker", "compose", "--env-file", str(ROOT / ".env"),
    ]
    if (ROOT / ".release.env").is_file():
        compose.extend(["--env-file", str(ROOT / ".release.env")])
    if test_override_enabled():
        compose.extend(["--env-file", str(ROOT / ".test-deployment.env")])
    compose.extend([
        "-f", str(ROOT / "infra/docker-compose.yml"),
        "-f", str(ROOT / "infra/docker-compose.prod.yml"),
        "-f", str(ROOT / "infra/docker-compose.ha.yml"),
    ])
    override = ROOT / "infra/docker-compose.override.yml"
    if override.is_file():
        compose.extend(["-f", str(override)])
    return compose


def local_healthy(config: dict[str, str]) -> bool:
    compose = compose_command()
    result = subprocess.run(
        [*compose, "exec", "-T", "backend", "python", "-c",
         'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3).read()'],
        cwd=ROOT, env={**os.environ, **config}, timeout=8,
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def drain_standby_client_connections(config: dict[str, str], state: dict) -> bool:
    """Close pre-handoff HTTP pools after DNS has had time to converge."""

    generation = state.get("generation")
    transition = state.get("transition") if isinstance(state.get("transition"), dict) else {}
    recovery = state.get("last_recovery") if isinstance(state.get("last_recovery"), dict) else {}
    routing = state.get("routing") if isinstance(state.get("routing"), dict) else {}
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
        or state.get("holder_node_id") == config["HA_NODE_ID"]
        or state.get("routing_ready") is not True
        or transition.get("phase") != "stable"
        or recovery.get("kind") not in {"planned_handoff", "automatic_failover"}
    ):
        return False
    try:
        completed_at = datetime.fromisoformat(
            str(recovery["completed_at"]).replace("Z", "+00:00")
        )
        if completed_at.tzinfo is None:
            return False
    except (KeyError, TypeError, ValueError):
        return False
    ttl = routing.get("ttl", 60)
    if not isinstance(ttl, int) or isinstance(ttl, bool):
        ttl = 60
    ttl = max(5, min(300, ttl))
    if (datetime.now(timezone.utc) - completed_at.astimezone(timezone.utc)).total_seconds() < ttl + 5:
        return False
    try:
        marker = json.loads(CONNECTION_DRAIN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        marker = {}
    if isinstance(marker, dict) and marker.get("generation") == generation:
        return False
    subprocess.run(
        [*compose_command(), "restart", "caddy"],
        check=True,
        timeout=45,
        env={**os.environ, **config},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    atomic_json(CONNECTION_DRAIN_PATH, {
        "format": "mp-opt-ha-connection-drain-v1",
        "generation": generation,
        "holder_node_id": state.get("holder_node_id"),
        "drained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })
    return True


def promote(config: dict[str, str], generation: int) -> None:
    subprocess.run(
        [str(ROOT / "deploy/ha/promote_local.sh"), str(generation)],
        check=True, timeout=180,
        env={**os.environ, "MP_ROOT": str(ROOT), "MP_HA_HOME": str(HA_HOME)},
    )


def one_iteration(config: dict[str, str]) -> dict:
    received = receiver_state()
    smtp = smtp_state()
    payload = {
        "node_id": config["HA_NODE_ID"],
        "healthy": local_healthy(config),
        "release_hash": release_hash(),
        "bundle_id": received.get("last_bundle_id", ""),
        "bundle_generation": received.get("generation", 0),
        "bundle_created_at": received.get("last_received_at", ""),
        "smtp_configured": smtp.get("configured") is True,
        "smtp_ready": smtp.get("ready") is True,
        "smtp_checked_at": smtp.get("checked_at", ""),
        "smtp_error_code": smtp.get("error_code", ""),
        "smtp_config_fingerprint": smtp.get("config_fingerprint", ""),
        "critical_pending": (
            any((ROOT / "runtime/ha-requests").glob("*.json"))
            or any((ROOT / "runtime/ha-deferred-requests").glob("*.json"))
        ),
    }
    state = post(config, "heartbeat", payload)
    state.update({
        "node_id": config["HA_NODE_ID"],
        "observed_at": datetime.now(timezone.utc).isoformat(),
    })
    atomic_json(CONTROL_PATH, state)
    if state.get("holder_node_id") == config["HA_NODE_ID"] and not state.get("routing_ready"):
        promote(config, int(state["generation"]))
        state = post(config, "ready", {"node_id": config["HA_NODE_ID"]})
        state.update({"node_id": config["HA_NODE_ID"], "observed_at": datetime.now(timezone.utc).isoformat()})
        atomic_json(CONTROL_PATH, state)
    elif state.get("holder_node_id") != config["HA_NODE_ID"]:
        drain_standby_client_connections(config, state)
    return state


def main() -> int:
    once = "--once" in sys.argv
    while True:
        try:
            one_iteration(read_config())
        except Exception as exc:
            # Preserve the last holder/generation for read-only continuity, but
            # never refresh observed_at: readiness and all writes must see the
            # witness observation expire.
            previous: dict = {}
            try:
                loaded = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    previous = loaded
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            previous.update({
                "witness_error_at": datetime.now(timezone.utc).isoformat(),
                "error_type": type(exc).__name__,
            })
            atomic_json(CONTROL_PATH, previous)
            if once:
                print(str(exc), file=sys.stderr)
                return 1
        if once:
            return 0
        time.sleep(15)


if __name__ == "__main__":
    raise SystemExit(main())
