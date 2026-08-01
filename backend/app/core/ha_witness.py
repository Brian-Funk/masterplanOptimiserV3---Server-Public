"""Provider-neutral lease and write-permit client for HA nodes."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import json
import secrets
from typing import Any
from urllib import error, request
from pathlib import Path

from app.core.config import settings


class HAWritePermitError(RuntimeError):
    """Raised when the witness cannot prove that this node may commit."""


_permit_deadline: ContextVar[float] = ContextVar("ha_permit_deadline", default=0.0)


def _utc_timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Witness timestamps must include a timezone")
    return parsed.astimezone(timezone.utc).timestamp()


def _witness_call(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.HA_WITNESS_URL or not settings.HA_NODE_TOKEN:
        raise HAWritePermitError("The HA lease authority is not configured")
    req = request.Request(
        f"{settings.HA_WITNESS_URL.rstrip('/')}{path}",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.HA_NODE_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "MP-OPT-HA/1.0",
            "X-MP-OPT-Nonce": secrets.token_hex(16),
        },
    )
    try:
        with request.urlopen(req, timeout=settings.HA_WRITE_PERMIT_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise HAWritePermitError("The HA lease authority refused the request")
            raw = response.read(65537)
    except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
        raise HAWritePermitError("The HA lease authority is unavailable") from exc
    if len(raw) > 65536:
        raise HAWritePermitError("The HA lease response was too large")
    try:
        result = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HAWritePermitError("The HA lease response was invalid") from exc
    if not isinstance(result, dict):
        raise HAWritePermitError("The HA lease response was invalid")
    return result


def require_write_permit(*, force_refresh: bool = False) -> None:
    """Require a short online permit for the current cluster generation."""

    if settings.HA_MODE != "ha":
        return
    now = datetime.now(timezone.utc).timestamp()
    if not force_refresh and _permit_deadline.get() - now >= 1:
        return
    generation = settings.HA_GENERATION
    try:
        local_state = json.loads(Path(settings.HA_LEASE_STATE_PATH).read_text(encoding="utf-8"))
        generation = int(local_state.get("generation", generation))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    result = _witness_call(
        f"/v1/clusters/{settings.HA_CLUSTER_ID}/write-permit",
        {"node_id": settings.HA_NODE_ID, "generation": generation},
    )
    if (
        result.get("allowed") is not True
        or result.get("holder_node_id") != settings.HA_NODE_ID
        or int(result.get("generation", -1)) != generation
    ):
        raise HAWritePermitError("This node does not hold the HA writer lease")
    try:
        deadline = _utc_timestamp(str(result["permit_expires_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HAWritePermitError("The HA lease response omitted its expiry") from exc
    if deadline <= now:
        raise HAWritePermitError("The HA writer permit has expired")
    _permit_deadline.set(deadline)


def clear_cached_write_permit() -> None:
    _permit_deadline.set(0.0)


def witness_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Perform another authenticated witness operation."""

    return _witness_call(path, payload)
