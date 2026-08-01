"""Fail-closed dynamic-writer and database-state checks."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings


@dataclass(frozen=True)
class HAReadiness:
    """Minimal readiness result safe to expose to a load-balancer monitor."""

    ready: bool
    reason: str


def is_ha_enabled() -> bool:
    """Return whether this process belongs to a symmetric HA cluster."""

    return settings.HA_MODE == "ha"


def control_witness_state() -> dict | None:
    """Return a fresh lease observation, or ``None`` when it is unusable."""

    if not settings.HA_CONTROL_WITNESS_REQUIRED:
        return {
            "holder_node_id": settings.HA_NODE_ID,
            "generation": settings.HA_GENERATION,
            "routing_ready": True,
        }
    try:
        witness = json.loads(
            Path(settings.HA_CONTROL_STATE_PATH).read_text(encoding="utf-8")
        )
        observed_at = datetime.fromisoformat(str(witness["observed_at"]).replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            return None
        now = datetime.now(timezone.utc)
        age = (now - observed_at.astimezone(timezone.utc)).total_seconds()
        if not (0 <= age <= settings.HA_CONTROL_WITNESS_MAX_AGE_SECONDS):
            return None
        lease_expires_at = datetime.fromisoformat(
            str(witness["lease_expires_at"]).replace("Z", "+00:00")
        )
        if lease_expires_at.tzinfo is None or lease_expires_at.astimezone(timezone.utc) <= now:
            return None
        holder = witness.get("holder_node_id")
        if not isinstance(holder, str) or not holder:
            return None
        generation = witness.get("generation")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            return None
        witness["holder_node_id"] = holder
        return witness
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def control_witness_ready() -> bool:
    """Return whether this node is the freshly observed routed lease holder."""

    witness = control_witness_state()
    return bool(
        witness
        and witness.get("holder_node_id") == settings.HA_NODE_ID
        and witness.get("routing_ready") is True
    )


def _public_timestamp(value: object) -> str | None:
    """Return a valid UTC-ish ISO timestamp without exposing control details."""

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def public_service_status() -> dict:
    """Return a sanitised, database-independent service availability summary."""

    base = {
        "format": "mp-opt-ha-public-status-v1",
        "mode": "ha" if is_ha_enabled() else "standalone",
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "roles": {"from": None, "to": None, "active": "Primary"},
        "capabilities": {
            "sign_in": True, "live_reads": True, "writes": True, "public_links": True,
        },
        "retry_after_seconds": 0,
        "transition_started_at": None,
        "earliest_failover_at": None,
        "recovery_point_at": None,
        "last_recovery": None,
    }
    if not is_ha_enabled():
        return {**base, "state": "ready", "reason": None}

    try:
        witness = json.loads(Path(settings.HA_CONTROL_STATE_PATH).read_text(encoding="utf-8"))
        observed = datetime.fromisoformat(str(witness["observed_at"]).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("naive observation")
        now = datetime.now(timezone.utc)
        age = (now - observed.astimezone(timezone.utc)).total_seconds()
        if not 0 <= age <= settings.HA_CONTROL_WITNESS_MAX_AGE_SECONDS:
            raise ValueError("stale observation")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return {
            **base,
            "state": "control_unavailable",
            "reason": "control_unavailable",
            "capabilities": {key: False for key in base["capabilities"]},
            "retry_after_seconds": 5,
        }

    transition = witness.get("transition") if isinstance(witness.get("transition"), dict) else {}
    phase = transition.get("phase")
    should_promote = witness.get("should_promote") is True
    routing_ready = witness.get("routing_ready") is True
    local_holder = witness.get("holder_node_id") == settings.HA_NODE_ID
    if phase == "planned_handoff":
        state = "promoting" if should_promote else "planned_handoff"
    elif phase == "failover_wait":
        state = "failover_wait"
    elif phase == "automatic_failover_disabled":
        state = "automatic_failover_disabled"
    elif phase == "routing":
        state = "promoting" if should_promote else "routing"
    elif local_holder and routing_ready:
        state = "ready"
    elif local_holder:
        state = "routing"
    else:
        state = "standby_shell"

    ready = state == "ready"
    earliest = _public_timestamp(transition.get("earliest_failover_at"))
    retry_after = 0
    if not ready:
        retry_after = 5
        if earliest:
            try:
                due = datetime.fromisoformat(earliest.replace("Z", "+00:00"))
                retry_after = max(1, min(60, math.ceil((due - datetime.now(timezone.utc)).total_seconds())))
            except ValueError:
                pass
    last_recovery = witness.get("last_recovery")
    if not isinstance(last_recovery, dict):
        last_recovery = None
    elif last_recovery.get("kind") not in {"planned_handoff", "automatic_failover"}:
        last_recovery = None
    else:
        last_recovery = {
            "kind": last_recovery["kind"],
            "completed_at": _public_timestamp(last_recovery.get("completed_at")),
            "recovery_seconds": last_recovery.get("recovery_seconds")
            if isinstance(last_recovery.get("recovery_seconds"), int) else None,
        }
    return {
        **base,
        "state": state,
        "reason": transition.get("reason") if transition.get("reason") in {
            "planned_handoff", "automatic_failover", "node_unreachable", "application_unhealthy"
        } else None,
        "observed_at": _public_timestamp(witness.get("observed_at")) or base["observed_at"],
        "roles": {
            "from": "Primary" if transition.get("from_node_id") else None,
            "to": "Standby" if transition.get("to_node_id") else None,
            "active": "Primary" if ready else None,
        },
        "capabilities": {
            "sign_in": ready, "live_reads": ready, "writes": ready, "public_links": ready,
        },
        "retry_after_seconds": retry_after,
        "transition_started_at": _public_timestamp(transition.get("started_at")),
        "earliest_failover_at": earliest,
        "recovery_point_at": _public_timestamp(transition.get("recovery_point_at")),
        "last_recovery": last_recovery,
    }


def assess_readiness(db: Session) -> HAReadiness:
    """Verify role, PostgreSQL writability and the durable cluster generation."""

    if not is_ha_enabled():
        db.execute(text("SELECT 1"))
        return HAReadiness(True, "standalone")
    witness = control_witness_state()
    if witness is None:
        return HAReadiness(False, "control-witness-unavailable")
    if witness.get("holder_node_id") != settings.HA_NODE_ID:
        return HAReadiness(False, "not-lease-holder")
    if witness.get("routing_ready") is not True:
        return HAReadiness(False, "routing-not-ready")

    row = db.execute(
        text(
            """
            SELECT pg_is_in_recovery(),
                   NOT current_setting('transaction_read_only')::boolean,
                   cluster_id, generation, active_node_id, maintenance
            FROM ha_cluster_state
            WHERE id = 1
            """
        )
    ).one_or_none()
    if row is None:
        return HAReadiness(False, "cluster-state-missing")
    in_recovery, writable, cluster_id, generation, active_node_id, maintenance = row
    if in_recovery or not writable:
        return HAReadiness(False, "database-read-only")
    if maintenance:
        return HAReadiness(False, "maintenance")
    if cluster_id != settings.HA_CLUSTER_ID:
        return HAReadiness(False, "cluster-mismatch")
    if generation != int(witness.get("generation", -1)):
        return HAReadiness(False, "generation-mismatch")
    if active_node_id != settings.HA_NODE_ID:
        return HAReadiness(False, "active-node-mismatch")
    return HAReadiness(True, "ready")


def record_heartbeat(db: Session) -> None:
    """Dynamic liveness is external; readiness probes never mutate the DB."""

    return
