#!/usr/bin/env python3
"""Run due or explicitly requested point-in-time replication jobs."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(os.getenv("MP_ROOT", "/opt/masterplan"))
DEPLOYMENT_POLICY = Path(os.getenv("MP_DEPLOYMENT_POLICY_FILE", "/etc/mp-opt/deployment-policy"))
STATUS = ROOT / "runtime/ha-replication.json"
CONTROL = ROOT / "runtime/ha-control.json"
REQUESTS = ROOT / "runtime/ha-requests"
DEFERRED = ROOT / "runtime/ha-deferred-requests"
JOBS = ROOT / "runtime/ha-jobs"


def now() -> datetime:
    return datetime.now(timezone.utc)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def write_status(value: dict) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o644)
    temporary.replace(STATUS)


def write_job_receipt(job_id: str, value: dict) -> None:
    JOBS.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = JOBS / f".{job_id}.tmp"
    target = JOBS / f"{job_id}.json"
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o644)
    temporary.replace(target)


def prune_job_receipts(current: datetime) -> None:
    """Bound non-secret API job receipts by age and count."""

    JOBS.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipts = sorted(
        JOBS.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True,
    )
    oldest = current.timestamp() - 7 * 24 * 60 * 60
    for index, receipt in enumerate(receipts):
        try:
            if index >= 1000 or receipt.stat().st_mtime < oldest:
                receipt.unlink()
        except OSError:
            continue


def config() -> dict[str, str]:
    result = {}
    for line in Path("/etc/mp-opt-ha/node.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            result[key] = value
    return result


def interval_minutes(cfg: dict[str, str]) -> int:
    command = (
        "SELECT COALESCE((SELECT value FROM server_settings "
        "WHERE key='ha_replication_interval_minutes'),'5');"
    )
    compose = ["docker", "compose", "--env-file", ".env"]
    if (ROOT / ".release.env").is_file():
        compose.extend(["--env-file", ".release.env"])
    test_environment = ROOT / ".test-deployment.env"
    if test_environment.is_file():
        if not DEPLOYMENT_POLICY.is_file() or DEPLOYMENT_POLICY.read_text(encoding="utf-8").strip() != "test":
            raise RuntimeError("unsigned deployment override exists outside test policy")
        compose.extend(["--env-file", ".test-deployment.env"])
    compose.extend(["-f", "infra/docker-compose.yml", "-f", "infra/docker-compose.prod.yml",
                    "-f", "infra/docker-compose.ha.yml"])
    result = subprocess.run(
        [*compose, "exec", "-T", "db", "psql", "-U", "masterplan", "-d", "masterplan", "-Atqc", command],
        cwd=ROOT, check=False, capture_output=True, text=True, timeout=30,
        env={**os.environ, **cfg},
    )
    try:
        value = int(result.stdout.strip())
    except ValueError:
        value = 5
    return min(1440, max(5, value))


def timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def main() -> int:
    cfg = config()
    control = read_json(CONTROL)
    previous = read_json(STATUS)
    if cfg.get("HA_MODE") != "ha" or control.get("holder_node_id") != cfg.get("HA_NODE_ID") or not control.get("routing_ready"):
        return 0
    REQUESTS.mkdir(parents=True, exist_ok=True, mode=0o700)
    DEFERRED.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Newly queued work runs immediately through the path unit. Failed
    # noncritical work lives outside that watched directory and is retried by
    # the minute timer without creating a tight systemd activation loop.
    request_files = sorted(REQUESTS.glob("*.json")) or sorted(DEFERRED.glob("*.json"))
    request_document = read_json(request_files[0]) if request_files else {}
    privacy_assertion = request_document.get("privacy_assertion")
    last_success = timestamp(previous.get("last_success_at"))
    last_attempt = timestamp(previous.get("last_attempt_at"))
    current = now()
    prune_job_receipts(current)
    due = last_success is None or (current - last_success).total_seconds() >= interval_minutes(cfg) * 60
    if previous.get("job_state") == "failed" and last_attempt and (current - last_attempt).total_seconds() < 300 and not request_files:
        due = False
    if not request_files and not due:
        previous["potential_data_loss_seconds"] = int((current - last_success).total_seconds()) if last_success else None
        write_status(previous)
        return 0
    job_id = str(request_document.get("job_id") or current.strftime("auto-%Y%m%dT%H%M%SZ"))
    state = {
        **previous,
        "mode": "ha", "node_id": cfg.get("HA_NODE_ID"),
        "peer_node_id": cfg.get("HA_PEER_NODE_ID"),
        "holder_node_id": control.get("holder_node_id"),
        "generation": control.get("generation"), "job_id": job_id,
        "job_state": "capturing", "state": "replicating",
        "started_at": current.isoformat(), "last_attempt_at": current.isoformat(),
        "peer_reachable": None, "peer_compatible": None,
    }
    write_status(state)
    receipt_state = {
        **state,
        **({"privacy_assertion": privacy_assertion} if privacy_assertion is not None else {}),
    }
    write_job_receipt(job_id, receipt_state)
    result = subprocess.run(
        [
            str(ROOT / "deploy/ha/replicate_now.sh"),
            job_id,
            str(request_files[0]) if request_files and privacy_assertion is not None else "",
        ], cwd=ROOT,
        check=False, capture_output=True, text=True, timeout=3600,
        env={**os.environ, **cfg},
    )
    completed = now()
    if result.returncode == 0:
        parts = result.stdout.strip().split(":")
        state.update({
            "job_state": "succeeded", "state": "healthy", "completed_at": completed.isoformat(),
            "last_success_at": completed.isoformat(), "last_bundle_id": job_id,
            "last_bundle_sha256": parts[-1] if len(parts) == 3 else None,
            "potential_data_loss_seconds": 0, "peer_reachable": True,
            "peer_compatible": True, "error_code": None,
            "message": "The peer accepted and verified the complete application state.",
            "bundle_id": job_id,
            "bundle_sha256": parts[-1] if len(parts) == 3 else None,
            "bundle_generation": control.get("generation"),
            "accepted_at": completed.isoformat(),
        })
    else:
        last_success = timestamp(state.get("last_success_at"))
        potential_loss = int((completed - last_success).total_seconds()) if last_success else None
        if result.returncode == 20:
            error_code = "peer_unreachable"
            message = "The peer is unreachable; its previous verified copy remains unchanged."
            peer_reachable: bool | None = False
            peer_compatible: bool | None = None
        elif result.returncode == 23:
            error_code = "peer_busy"
            message = "The peer is completing a management operation; its previous verified copy remains unchanged."
            peer_reachable = True
            peer_compatible = True
        elif result.returncode == 74:
            error_code = "local_busy"
            message = "A local management operation is running; replication was deferred."
            peer_reachable = None
            peer_compatible = None
        elif result.returncode in {21, 22}:
            error_code = "peer_rejected_copy"
            message = "The peer rejected the copy; its previous verified copy remains unchanged."
            peer_reachable = True
            peer_compatible = False
        else:
            error_code = "capture_failed"
            message = "A complete copy could not be produced; the peer remains unchanged."
            peer_reachable = None
            peer_compatible = None
        state.update({
            "job_state": "failed", "state": "degraded", "completed_at": completed.isoformat(),
            "peer_reachable": peer_reachable, "peer_compatible": peer_compatible,
            "potential_data_loss_seconds": potential_loss, "error_code": error_code,
            "message": message,
        })
    write_status(state)
    receipt_state = {
        **state,
        **({"privacy_assertion": privacy_assertion} if privacy_assertion is not None else {}),
    }
    write_job_receipt(job_id, receipt_state)
    # Synchronous API secret changes are critical: report the failed receipt
    # immediately so the API restores its previous database state. Operator
    # and rollback copies remain pending until the peer accepts them, which
    # also prevents the witness from promoting a stale standby.
    deferred_request = bool(
        request_files and result.returncode != 0
        and request_document.get("critical") is not True
    )
    if request_files:
        request_path = request_files[0]
        try:
            if deferred_request and request_path.parent == REQUESTS:
                request_path.replace(DEFERRED / request_path.name)
            elif not deferred_request:
                request_path.unlink()
        except OSError:
            pass
    if deferred_request and result.returncode in {23, 74}:
        time.sleep(1)
    return 0 if result.returncode == 0 or deferred_request else 1


if __name__ == "__main__":
    raise SystemExit(main())
