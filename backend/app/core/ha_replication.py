"""Request and observe host-managed HA replication without exposing SSH to the API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid

from app.core.config import settings


@dataclass(frozen=True)
class HAProtectionResult:
    protected: bool
    job_id: str | None = None
    error_code: str | None = None
    bundle_id: str | None = None
    bundle_sha256: str | None = None
    generation: int | None = None
    accepted_at: datetime | None = None


def request_ha_replication(
    reason: str,
    *,
    critical: bool = False,
    privacy_assertion: dict | None = None,
) -> str | None:
    if settings.HA_MODE != "ha":
        return None
    job_id = str(uuid.uuid4())
    request_dir = Path(settings.HA_REPLICATION_REQUEST_DIR)
    try:
        request_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = request_dir / f".{job_id}.tmp"
        target = request_dir / f"{job_id}.json"
        document = {
            "format": "mp-opt-replication-request-v1",
            "job_id": job_id,
            "reason": reason,
            "critical": critical,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if privacy_assertion is not None:
            document["privacy_assertion"] = privacy_assertion
        temporary.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
        temporary.chmod(0o644)
        temporary.replace(target)
    except OSError:
        return None
    return job_id


def observe_ha_replication(
    job_id: str,
    *,
    privacy_assertion: dict | None = None,
) -> HAProtectionResult | None:
    try:
        parsed_job_id = uuid.UUID(job_id)
    except (TypeError, ValueError, AttributeError):
        return HAProtectionResult(False, error_code="invalid_replication_job_id")
    if str(parsed_job_id) != job_id:
        return HAProtectionResult(False, error_code="invalid_replication_job_id")
    receipt = Path(settings.HA_REPLICATION_REQUEST_DIR).parent / "ha-jobs" / f"{job_id}.json"
    try:
        document = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if document.get("job_id") != job_id:
        return HAProtectionResult(
            False,
            job_id=job_id,
            error_code="replication_receipt_job_mismatch",
        )
    state = document.get("job_state")
    if state == "succeeded":
        if privacy_assertion is not None and document.get("privacy_assertion") != privacy_assertion:
            return HAProtectionResult(
                False,
                job_id=job_id,
                error_code="replication_receipt_scope_mismatch",
            )
        accepted_at = None
        try:
            accepted_at = datetime.fromisoformat(
                str(document.get("accepted_at", "")).replace("Z", "+00:00")
            )
        except ValueError:
            pass
        generation = document.get("bundle_generation")
        return HAProtectionResult(
            True,
            job_id=job_id,
            bundle_id=str(document.get("bundle_id") or job_id),
            bundle_sha256=str(document.get("bundle_sha256") or "") or None,
            generation=generation if isinstance(generation, int) else None,
            accepted_at=accepted_at,
        )
    if state == "failed":
        return HAProtectionResult(
            False,
            job_id=job_id,
            error_code=str(document.get("error_code") or "replication_failed"),
        )
    return None


def protect_current_state(
    reason: str,
    timeout_seconds: float = 90.0,
    *,
    critical: bool = True,
    privacy_assertion: dict | None = None,
) -> HAProtectionResult:
    """Wait until the peer has accepted the just-committed application state."""

    if settings.HA_MODE != "ha":
        return HAProtectionResult(protected=True)
    job_id = request_ha_replication(
        reason,
        critical=critical,
        privacy_assertion=privacy_assertion,
    )
    if job_id is None:
        return HAProtectionResult(False, error_code="replication_agent_unavailable")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        observed = observe_ha_replication(job_id, privacy_assertion=privacy_assertion)
        if observed is not None:
            return observed
        time.sleep(0.25)
    return HAProtectionResult(False, job_id=job_id, error_code="replication_timeout")
