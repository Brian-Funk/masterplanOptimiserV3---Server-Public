"""Authoritative three-step root commissioning state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models.evidence import EvidenceKey
from app.models.governance import GovernancePublication
from app.models.server_setting import ServerSetting
from app.models.user import User, WebAuthnCredential


CommissioningStage = Literal["recovery", "controller", "governance", "complete"]


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def setting(db: Session, key: str) -> str | None:
    row = db.query(ServerSetting).filter(ServerSetting.key == key).first()
    return row.value if row else None


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(ServerSetting).filter(ServerSetting.key == key).first()
    if row is None:
        db.add(ServerSetting(key=key, value=value))
    else:
        row.value = value


def active_controller_key(db: Session) -> EvidenceKey | None:
    return db.query(EvidenceKey).filter(
        EvidenceKey.role == "controller",
        EvidenceKey.activated_at.isnot(None),
        EvidenceKey.revoked_at.is_(None),
        EvidenceKey.trust_establishment_sha256.isnot(None),
    ).order_by(EvidenceKey.activated_at.desc()).first()


def latest_governance(db: Session) -> GovernancePublication | None:
    return db.query(GovernancePublication).order_by(
        GovernancePublication.version.desc()
    ).first()


def root_passkey_ready(db: Session) -> bool:
    root = db.query(User).filter(User.is_root_admin == True).first()  # noqa: E712
    return bool(root and db.query(WebAuthnCredential).filter(
        WebAuthnCredential.user_id == root.id
    ).first())


def commissioning_stage(db: Session) -> CommissioningStage:
    if not setting(db, "root_recovery_download_acknowledged_at"):
        return "recovery"
    if active_controller_key(db) is None:
        return "controller"
    if latest_governance(db) is None:
        return "governance"
    receipt_sha256 = setting(db, "root_commissioning_receipt_sha256") or ""
    if (
        not setting(db, "root_commissioning_completed_at")
        or len(receipt_sha256) != 64
        or any(character not in "0123456789abcdef" for character in receipt_sha256)
    ):
        return "governance"
    return "complete"


def commissioning_required(db: Session) -> bool:
    return root_passkey_ready(db) and commissioning_stage(db) != "complete"


def _completed_at(db: Session, step: str) -> str | None:
    if step == "recovery":
        return setting(db, "root_recovery_download_acknowledged_at")
    if step == "controller":
        key = active_controller_key(db)
        return _iso_utc(key.activated_at) if key else None
    if step == "governance":
        publication = latest_governance(db)
        return _iso_utc(publication.published_at) if publication else None
    return None


def status_payload(db: Session) -> dict[str, Any]:
    stage = commissioning_stage(db)
    order = ["recovery", "controller", "governance"]
    current_index = 3 if stage == "complete" else order.index(stage)
    titles = {
        "recovery": "Recovery key",
        "controller": "Controller identity",
        "governance": "Governance baseline",
    }
    actions = {
        "recovery": ("verify_recovery_download", "Generate, download and locally verify the recovery key."),
        "controller": ("establish_controller_identity", "Generate or import a controller key, verify custody and approve it with the root passkey."),
        "governance": ("publish_governance_v1", "Complete the governance draft, resolve preflight items and publish version 1."),
        "complete": ("enter_administration", "Commissioning is complete. Normal administration is available."),
    }
    steps = []
    for index, step in enumerate(order):
        state = "complete" if index < current_index or stage == "complete" else ("current" if index == current_index else "locked")
        steps.append({
            "id": step,
            "title": titles[step],
            "number": index + 1,
            "status": state,
            "completed_at": _completed_at(db, step) if state == "complete" else None,
        })
    controller = active_controller_key(db)
    publication = latest_governance(db)
    action_code, action_message = actions[stage]
    return {
        "required": stage != "complete",
        "current_step": stage,
        "current_step_number": 3 if stage in {"governance", "complete"} else current_index + 1,
        "total_steps": 3,
        "percent_complete": 100 if stage == "complete" else current_index * 33,
        "steps": steps,
        "next_action": {"code": action_code, "message": action_message},
        "can_enter_administration": stage == "complete",
        "recovery": {
            "acknowledged": bool(setting(db, "root_recovery_download_acknowledged_at")),
            "recipient_sha256": setting(db, "root_recovery_recipient_sha256"),
        },
        "controller": {
            "ready": controller is not None,
            "entity_id": controller.entity_id if controller else None,
            "key_id": controller.key_id if controller else None,
            "public_key_sha256": controller.public_key_sha256 if controller else None,
            "trust_establishment_sha256": controller.trust_establishment_sha256 if controller else None,
        },
        "governance": {
            "published": publication is not None,
            "version": publication.version if publication else None,
            "content_sha256": publication.content_sha256 if publication else None,
        },
        "commissioning": {
            "completed_at": setting(db, "root_commissioning_completed_at"),
            "receipt_sha256": setting(db, "root_commissioning_receipt_sha256"),
        },
    }


def mark_commissioning_complete(db: Session, receipt_sha256: str) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    set_setting(db, "root_commissioning_completed_at", now)
    set_setting(db, "root_commissioning_receipt_sha256", receipt_sha256)
