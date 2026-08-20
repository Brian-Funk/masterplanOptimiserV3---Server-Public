"""Four-domain trust-key registration, rotation and revocation ceremonies."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import secrets
from typing import Any, Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from webauthn import generate_authentication_options, verify_authentication_response
from webauthn.helpers import base64url_to_bytes, options_to_json
from webauthn.helpers.structs import UserVerificationRequirement

from app.api.v1.passkey import CeremonyCompletion, _credential_id, _verify_user_handle
from app.core.audit import audit
from app.core.config import settings
from app.core.evidence import EvidenceUnavailable, _atomic_write, append_record, evidence_home, initialise
from app.core.operator_evidence import (
    ARCHIVE_TRUST_FORMAT,
    ARCHIVE_TRUST_SCOPE,
    REVOCATION_REASONS,
    ROTATION_REASONS,
    SIGNED_ARCHIVE_TRUST_PACKAGE_FORMAT,
    TRUST_NAMESPACE,
    TrustEvidenceError,
    action_payload,
    action_sha256,
    canonical_json,
    canonical_public_key,
    key_id,
    public_key_sha256,
    validate_entity,
    validate_archive_trust_document,
    validate_processor_event_registration,
    validate_registration_document,
    verify_signature,
)
from app.core.passkey_ceremonies import TRUST_KEY_ACTIVATION, consume_ceremony, create_ceremony
from app.core.security import require_root_admin, require_root_recent_reauth
from app.core.tenancy import TENANCY_HOSTED, TENANCY_SINGLE, tenancy_mode
from app.db.database import get_db
from app.models.evidence import (
    EvidenceKey,
    EvidenceKeyRegistrationChallenge,
    ProcessorIdentity,
    RootActionAuthorisation,
)
from app.models.tenancy import Controller
from app.models.user import User, WebAuthnCredential


logger = logging.getLogger(__name__)
router = APIRouter()


class BeginTrustKeyChallenge(BaseModel):
    """Public material for one controller or processor registration."""
    model_config = ConfigDict(extra="forbid")
    public_key: str = Field(min_length=32, max_length=2048)
    role: Literal["controller"]
    entity_id: str = Field(min_length=12, max_length=64)
    controller_public_id: str | None = Field(default=None, min_length=36, max_length=36)
    supersedes_key_id: str | None = Field(default=None, pattern=r"^ek-[0-9a-f]{16}$")
    reason: Literal["routine", "lost", "compromised"] | None = None


class SubmitPossessionProof(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge: dict[str, Any]
    proof: dict[str, Any]
    previous_proof: dict[str, Any] | None = None


class RevokeTrustKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_code: Literal["retired", "lost", "compromised", "role_changed"]
    confirmation: Literal["ROOT PASSKEY AUTHORISED"]


class CompleteArchiveTrust(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: dict[str, Any]
    proof: dict[str, Any]


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _reject(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "TRUST_EVIDENCE_REJECTED", "message": str(exc)})


def _active_role_key(
    db: Session, role: str, *, controller_id: int | None = None
) -> EvidenceKey:
    query = db.query(EvidenceKey).filter(
        EvidenceKey.role == role,
        EvidenceKey.activated_at.isnot(None),
        EvidenceKey.revoked_at.is_(None),
    )
    if controller_id is not None:
        query = query.filter(EvidenceKey.controller_id == controller_id)
    row = query.order_by(EvidenceKey.activated_at.desc(), EvidenceKey.id.desc()).first()
    if row is None:
        raise TrustEvidenceError(f"an active {role} evidence key is required")
    return row


def _challenge_controller(
    db: Session, *, controller_public_id: str | None, entity_id: str
) -> Controller:
    """Resolve and bind a controller trust action to one first-class tenant."""

    mode = tenancy_mode(db)
    if controller_public_id is None:
        if mode == TENANCY_HOSTED:
            raise TrustEvidenceError("controller_public_id is required in hosted mode")
        controller = db.get(Controller, 1)
    else:
        controller = (
            db.query(Controller)
            .filter(Controller.public_id == controller_public_id)
            .first()
        )
    if controller is None or controller.status == "retired":
        raise TrustEvidenceError("the controller is unavailable")
    if controller.trust_entity_id != entity_id:
        existing = db.query(EvidenceKey).filter(
            EvidenceKey.controller_id == controller.id,
            EvidenceKey.role == "controller",
        ).first()
        pending = db.query(EvidenceKeyRegistrationChallenge).filter(
            EvidenceKeyRegistrationChallenge.controller_id == controller.id,
            EvidenceKeyRegistrationChallenge.role == "controller",
            EvidenceKeyRegistrationChallenge.used_at.is_(None),
        ).first()
        if mode == TENANCY_SINGLE and controller.id == 1 and existing is None and pending is None:
            # A fresh single-controller installation historically let the
            # controller-key package choose this identity during commissioning.
            controller._allow_initial_trust_identity_binding = True
            try:
                controller.trust_entity_id = entity_id
                db.flush()
            finally:
                controller.__dict__.pop(
                    "_allow_initial_trust_identity_binding",
                    None,
                )
        else:
            raise TrustEvidenceError("the trust identity belongs to another controller")
    return controller


def _archive_trust_directory(controller: Controller | None = None) -> Path:
    if controller is None:
        return evidence_home() / "archive-trust"
    try:
        trust_entity_id = validate_entity("controller", controller.trust_entity_id)
    except TrustEvidenceError as exc:
        raise TrustEvidenceError("the controller trust identity is invalid") from exc
    return evidence_home() / "controllers" / trust_entity_id / "archive-trust"


def _archive_controller(db: Session, public_id: str | None) -> Controller:
    if public_id is None:
        if tenancy_mode(db) == TENANCY_HOSTED:
            raise TrustEvidenceError("controller_public_id is required in hosted mode")
        controller = db.get(Controller, 1)
    else:
        controller = db.query(Controller).filter(Controller.public_id == public_id).first()
    if controller is None or controller.status not in {"active", "draft"}:
        raise TrustEvidenceError("the controller is unavailable")
    return controller


def _archive_trust_package(
    db: Session, *, controller_id: int | None = None
) -> tuple[dict[str, Any], str] | None:
    try:
        controller = _active_role_key(db, "controller", controller_id=controller_id)
        instance = _active_role_key(db, "instance")
    except TrustEvidenceError:
        return None
    controller_tenant = db.get(Controller, controller.controller_id)
    directory = _archive_trust_directory(
        controller_tenant if tenancy_mode(db) == TENANCY_HOSTED else None
    )
    if not directory.is_dir() or directory.is_symlink():
        return None
    for path in sorted(directory.glob("[0-9a-f]" * 64 + ".json"), reverse=True):
        try:
            raw = path.read_bytes()
            package = json.loads(raw.decode("utf-8"))
            if raw != canonical_json(package) or hashlib.sha256(raw).hexdigest() != path.stem:
                continue
            if not isinstance(package, dict) or set(package) != {
                "format", "namespace", "document", "proof",
                "controller_public_key", "instance_public_key",
            }:
                continue
            document = package["document"]
            validate_archive_trust_document(
                document,
                instance_id=instance.instance_id,
                controller_entity_id=str(controller.entity_id),
                controller_key_id=controller.key_id,
                controller_fingerprint=controller.public_key_sha256,
                instance_key_id=instance.key_id,
                instance_fingerprint=instance.public_key_sha256,
                require_fresh=False,
            )
            if (
                package["format"] != SIGNED_ARCHIVE_TRUST_PACKAGE_FORMAT
                or package["namespace"] != TRUST_NAMESPACE
                or canonical_public_key(package["controller_public_key"]) != controller.public_key
                or canonical_public_key(package["instance_public_key"]) != instance.public_key
            ):
                continue
            verify_signature(document, package["proof"], controller.public_key)
            bound = False
            for record_path in sorted((evidence_home() / "ledger").glob("[0-9]" * 12 + "_*.json")):
                record = json.loads(record_path.read_text(encoding="utf-8"))
                if (
                    record.get("record_type") == "evidence.archive_trust_bound"
                    and record.get("payload", {}).get("statement_sha256") == path.stem
                ):
                    bound = True
                    break
            if not bound:
                continue
            return package, path.stem
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TrustEvidenceError, TypeError, ValueError):
            continue
    return None


def _public_key_row(row: EvidenceKey, identity: ProcessorIdentity | None = None) -> dict[str, Any]:
    return {
        "instance_id": row.instance_id,
        "entity_id": row.entity_id,
        "key_id": row.key_id,
        "credential_id": row.key_id,
        "role": row.role,
        "algorithm": row.algorithm,
        "public_key": row.public_key,
        "public_key_sha256": row.public_key_sha256,
        "validity_status": "revoked" if row.revoked_at else ("active" if row.activated_at else "pending"),
        "created_at": row.registered_at,
        "activated_at": row.activated_at,
        "expires_at": row.expires_at,
        "supersedes_key_id": row.supersedes_key_id,
        "superseded_by_key_id": row.superseded_by_key_id,
        "revoked_at": row.revoked_at,
        "revocation_reason": row.revocation_reason,
        "root_action_sha256": row.root_action_sha256,
        "trust_establishment_sha256": row.trust_establishment_sha256,
        "event_ref": identity.event_evidence_id if identity else None,
        "event_name": identity.event_display_name if identity else None,
        "display_label": identity.display_label if identity else None,
    }


def _load_challenge(db: Session, document: dict[str, Any]) -> EvidenceKeyRegistrationChallenge:
    if document.get("format") == "mp-opt-processor-event-registration-v1":
        validate_processor_event_registration(document)
    else:
        validate_registration_document(document)
    rendered = canonical_json(document)
    row = db.query(EvidenceKeyRegistrationChallenge).filter(
        EvidenceKeyRegistrationChallenge.challenge_id == document["challenge_id"],
    ).first()
    if row is None or row.used_at is not None:
        raise TrustEvidenceError("the trust-key challenge is unavailable or already used")
    if _as_utc(row.expires_at) < datetime.now(timezone.utc):
        raise TrustEvidenceError("the trust-key challenge has expired")
    if (
        row.challenge_json.encode("utf-8") != rendered
        or row.challenge_sha256 != hashlib.sha256(rendered).hexdigest()
        or row.action_sha256 != document["action_sha256"]
        or row.instance_id != document["instance_id"]
        or row.entity_id != document["entity_id"]
        or row.key_id != document["key_id"]
        or row.role != document["role"]
    ):
        raise TrustEvidenceError("the trust-key challenge was changed")
    return row


@router.get("/trust-keys")
def list_trust_keys(_root: User = Depends(require_root_admin), db: Session = Depends(get_db)):
    identities = {row.entity_id: row for row in db.query(ProcessorIdentity).all()}
    return [_public_key_row(row, identities.get(row.entity_id)) for row in db.query(EvidenceKey).order_by(EvidenceKey.registered_at, EvidenceKey.id).all()]


@router.get("/trust-keys/archive-trust")
def archive_trust_status(
    controller_public_id: str | None = None,
    _root: User = Depends(require_root_admin),
    db: Session = Depends(get_db),
):
    try:
        controller = _archive_controller(db, controller_public_id)
    except TrustEvidenceError as exc:
        raise _reject(exc) from exc
    binding = _archive_trust_package(db, controller_id=controller.id)
    if binding is None:
        return {"ready": False, "message": "Select the active controller key once to authorise portable evidence archives."}
    package, digest = binding
    document = package["document"]
    return {
        "ready": True,
        "statement_sha256": digest,
        "controller_id": document["controller_id"],
        "controller_key_id": document["controller_key_id"],
        "instance_key_id": document["instance_key_id"],
        "signed_at": document["signed_at"],
    }


@router.post("/trust-keys/archive-trust/prepare")
def prepare_archive_trust(
    request: Request,
    controller_public_id: str | None = None,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    del request
    controller_tenant = _archive_controller(db, controller_public_id)
    controller = _active_role_key(
        db, "controller", controller_id=controller_tenant.id
    )
    instance = _active_role_key(db, "instance")
    existing = _archive_trust_package(db, controller_id=controller_tenant.id)
    if existing is not None:
        return {"ready": True, "document": existing[0]["document"]}
    document = {
        "format": ARCHIVE_TRUST_FORMAT,
        "instance_id": instance.instance_id,
        "controller_id": controller.entity_id,
        "controller_key_id": controller.key_id,
        "controller_public_key_sha256": controller.public_key_sha256,
        "instance_key_id": instance.key_id,
        "instance_public_key_sha256": instance.public_key_sha256,
        "scope": ARCHIVE_TRUST_SCOPE,
        "signed_at": datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    validate_archive_trust_document(
        document,
        instance_id=instance.instance_id,
        controller_entity_id=str(controller.entity_id),
        controller_key_id=controller.key_id,
        controller_fingerprint=controller.public_key_sha256,
        instance_key_id=instance.key_id,
        instance_fingerprint=instance.public_key_sha256,
    )
    return {"ready": False, "document": document}


@router.post("/trust-keys/archive-trust/complete")
def complete_archive_trust(
    body: CompleteArchiveTrust,
    request: Request,
    controller_public_id: str | None = None,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    try:
        controller_tenant = _archive_controller(db, controller_public_id)
        controller = _active_role_key(
            db, "controller", controller_id=controller_tenant.id
        )
        instance = _active_role_key(db, "instance")
        validate_archive_trust_document(
            body.document,
            instance_id=instance.instance_id,
            controller_entity_id=str(controller.entity_id),
            controller_key_id=controller.key_id,
            controller_fingerprint=controller.public_key_sha256,
            instance_key_id=instance.key_id,
            instance_fingerprint=instance.public_key_sha256,
        )
        proof_sha256 = verify_signature(body.document, body.proof, controller.public_key)
        package = {
            "format": SIGNED_ARCHIVE_TRUST_PACKAGE_FORMAT,
            "namespace": TRUST_NAMESPACE,
            "document": body.document,
            "proof": body.proof,
            "controller_public_key": controller.public_key,
            "instance_public_key": instance.public_key,
        }
        raw = canonical_json(package)
        statement_sha256 = hashlib.sha256(raw).hexdigest()
        path = _archive_trust_directory(
            controller_tenant if tenancy_mode(db) == TENANCY_HOSTED else None
        ) / f"{statement_sha256}.json"
        created = False
        if path.exists():
            if path.is_symlink() or path.read_bytes() != raw:
                raise TrustEvidenceError("the archive trust identity conflicts with retained evidence")
        else:
            _atomic_write(path, raw)
            created = True
        try:
            record_sha256 = append_record(
                db,
                workflow_type="evidence_archive_trust",
                workflow_id=instance.key_id,
                operation_type=f"bound_{controller.key_id}",
                record_type="evidence.archive_trust_bound",
                payload={
                    "controller_id": controller.entity_id,
                    "controller_key_id": controller.key_id,
                    "key_id": instance.key_id,
                    "public_key_sha256": instance.public_key_sha256,
                    "statement_sha256": statement_sha256,
                    "proof_sha256": proof_sha256,
                    "status": "verified",
                },
                controller_id=controller.controller_id,
            )
        except Exception:
            if created:
                path.unlink(missing_ok=True)
            raise
        audit(
            db, user=root, action="evidence.archive_trust.bound",
            resource_type="evidence", request=request,
            detail=json.dumps({"schema_version": 1, "statement_sha256": statement_sha256}),
        )
        db.commit()
        return {"ready": True, "statement_sha256": statement_sha256, "record_sha256": record_sha256}
    except (EvidenceUnavailable, TrustEvidenceError, OSError, TypeError, ValueError) as exc:
        db.rollback()
        raise _reject(exc) from exc


@router.get("/trust-keys/pending-enrolments")
def list_pending_processor_enrolments(
    _root: User = Depends(require_root_admin), db: Session = Depends(get_db),
):
    rows = db.query(EvidenceKeyRegistrationChallenge).filter(
        EvidenceKeyRegistrationChallenge.role == "processor",
        EvidenceKeyRegistrationChallenge.event_evidence_id.isnot(None),
        EvidenceKeyRegistrationChallenge.possession_proof_sha256.isnot(None),
        EvidenceKeyRegistrationChallenge.used_at.is_(None),
        EvidenceKeyRegistrationChallenge.expires_at >= datetime.now(timezone.utc),
    ).order_by(EvidenceKeyRegistrationChallenge.created_at).all()
    return [{
        "challenge_id": row.challenge_id,
        "event_ref": row.event_evidence_id,
        "event_name": row.event_display_name,
        "entity_id": row.entity_id,
        "display_label": row.display_label,
        "key_id": row.key_id,
        "public_key_sha256": row.public_key_sha256,
        "purpose": row.purpose,
        "expires_at": row.expires_at,
    } for row in rows]


@router.post("/trust-keys/challenges")
def begin_trust_key_challenge(
    body: BeginTrustKeyChallenge,
    request: Request,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    """Begin public proof of possession after a recent root passkey reauthentication."""
    try:
        validate_entity(body.role, body.entity_id)
        controller = _challenge_controller(
            db,
            controller_public_id=body.controller_public_id,
            entity_id=body.entity_id,
        )
        state = initialise(db)
        if state is None: raise EvidenceUnavailable("required evidence is unavailable")
        public = canonical_public_key(body.public_key)
        identifier = key_id(public)
        fingerprint = public_key_sha256(public)
        if db.query(EvidenceKey).filter((EvidenceKey.key_id == identifier) | (EvidenceKey.public_key_sha256 == fingerprint)).first():
            raise TrustEvidenceError("this public key is already registered")
        purpose = "rotate" if body.supersedes_key_id else "register"
        previous = None
        if purpose == "register":
            if body.reason is not None: raise TrustEvidenceError("new registration cannot specify a rotation reason")
        else:
            if body.reason not in ROTATION_REASONS: raise TrustEvidenceError("rotation requires a bounded reason")
            previous = db.query(EvidenceKey).filter(
                EvidenceKey.key_id == body.supersedes_key_id,
                EvidenceKey.role == body.role,
                EvidenceKey.entity_id == body.entity_id,
                EvidenceKey.controller_id == controller.id,
                EvidenceKey.instance_id == state.instance_id,
                EvidenceKey.revoked_at.is_(None),
            ).first()
            if previous is None: raise TrustEvidenceError("the superseded key is not active for this role, entity, and instance")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        # A browser retry or an explicit restart must leave only one usable
        # ceremony for an entity. Preserve the abandoned rows for audit, but
        # make their signed challenges permanently unavailable before issuing
        # the replacement. Any associated WebAuthn ceremony then fails closed
        # when it attempts to reload the superseded trust challenge.
        superseded_pending = db.query(EvidenceKeyRegistrationChallenge).filter(
            EvidenceKeyRegistrationChallenge.instance_id == state.instance_id,
            EvidenceKeyRegistrationChallenge.entity_id == body.entity_id,
            EvidenceKeyRegistrationChallenge.role == body.role,
            EvidenceKeyRegistrationChallenge.controller_id == controller.id,
            EvidenceKeyRegistrationChallenge.used_at.is_(None),
        ).all()
        for pending in superseded_pending:
            pending.used_at = now
        expires = now + timedelta(minutes=10)
        document = {
            "format": "mp-opt-controller-trust-registration-v2",
            "challenge_id": str(uuid.uuid4()),
            "action": purpose,
            "instance_id": state.instance_id,
            "entity_id": body.entity_id,
            "key_id": identifier,
            "role": body.role,
            "algorithm": "Ed25519",
            "public_key_sha256": fingerprint,
            "trust_scope": "controller_governance_authority",
            "governance_authorisation": "root_passkey_per_publication",
            "supersedes_key_id": previous.key_id if previous else None,
            "reason": body.reason if previous else None,
            "action_sha256": "",
            "nonce": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
            "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        document["action_sha256"] = action_sha256(document)
        validate_registration_document(document)
        rendered = canonical_json(document)
        challenge = EvidenceKeyRegistrationChallenge(
            challenge_id=document["challenge_id"], purpose=purpose,
            instance_id=state.instance_id, entity_id=body.entity_id,
            controller_id=controller.id,
            public_key=public, public_key_sha256=fingerprint, key_id=identifier,
            role=body.role, supersedes_key_id=previous.key_id if previous else None,
            rotation_reason=body.reason if previous else None,
            challenge_json=rendered.decode("utf-8"),
            challenge_sha256=hashlib.sha256(rendered).hexdigest(),
            action_sha256=document["action_sha256"], expires_at=expires,
        )
        db.add(challenge)
        audit(db, user=root, action="evidence.trust_key.challenge", resource_type="evidence", request=request,
              detail=json.dumps({
                  "schema_version": 1,
                  "purpose": purpose,
                  "role": body.role,
                  "controller_public_id": controller.public_id,
                  "superseded_pending_challenges": len(superseded_pending),
              }))
        db.commit()
        return {"challenge": document, "challenge_sha256": challenge.challenge_sha256}
    except (EvidenceUnavailable, TrustEvidenceError, ValueError) as exc:
        db.rollback(); raise _reject(exc) from exc


@router.post("/trust-keys/proofs")
def submit_possession_proof(
    body: SubmitPossessionProof,
    request: Request,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    """Verify the external controller or Desktop processor proof before root activation."""
    try:
        challenge = _load_challenge(db, body.challenge)
        if challenge.possession_proof_sha256 is not None:
            raise TrustEvidenceError("proof of possession was already verified")
        proof_digest = verify_signature(body.challenge, body.proof, challenge.public_key)
        previous_digest = None
        if challenge.purpose == "rotate":
            previous = db.query(EvidenceKey).filter(
                EvidenceKey.key_id == challenge.supersedes_key_id,
                EvidenceKey.instance_id == challenge.instance_id,
                EvidenceKey.entity_id == challenge.entity_id,
                EvidenceKey.role == challenge.role,
                EvidenceKey.revoked_at.is_(None),
            ).first()
            if previous is None: raise TrustEvidenceError("the superseded key is no longer active")
            if challenge.rotation_reason == "routine" and body.previous_proof is None:
                raise TrustEvidenceError("routine rotation requires proof from old and new keys")
            if body.previous_proof is not None:
                previous_digest = verify_signature(body.challenge, body.previous_proof, previous.public_key)
        challenge.possession_proof_sha256 = proof_digest
        challenge.previous_proof_sha256 = previous_digest
        audit(db, user=root, action="evidence.trust_key.proof_verified", resource_type="evidence", request=request,
              detail=json.dumps({"schema_version": 1, "purpose": challenge.purpose, "role": challenge.role}))
        db.commit()
        return {"challenge_id": challenge.challenge_id, "proof_sha256": proof_digest, "root_authorisation_required": True}
    except (TrustEvidenceError, ValueError) as exc:
        db.rollback(); raise _reject(exc) from exc


@router.post("/trust-keys/{challenge_id}/root-authorisation/begin")
def begin_root_authorisation(
    challenge_id: str,
    request: Request,
    root: User = Depends(require_root_admin),
    db: Session = Depends(get_db),
):
    """Create a WebAuthn challenge bound to the exact verified key action."""
    del request
    challenge = db.query(EvidenceKeyRegistrationChallenge).filter(
        EvidenceKeyRegistrationChallenge.challenge_id == challenge_id,
        EvidenceKeyRegistrationChallenge.used_at.is_(None),
    ).first()
    if challenge is None or challenge.possession_proof_sha256 is None:
        raise _reject(TrustEvidenceError("verified proof of possession is required before root authorisation"))
    if _as_utc(challenge.expires_at) < datetime.now(timezone.utc):
        raise _reject(TrustEvidenceError("the trust-key challenge has expired"))
    if challenge.root_ceremony_id is not None:
        raise _reject(TrustEvidenceError("root authorisation was already started"))
    auth_session = getattr(root, "_auth_session", None)
    if auth_session is None: raise HTTPException(status_code=401, detail="Session expired or invalid")
    options = generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    authorisation_id = str(uuid.uuid4())
    action = {
        "format": "mp-opt-root-action-v1",
        "authorisation_id": authorisation_id,
        "action": f"{challenge.purpose}_trust_key",
        "challenge_id": challenge.challenge_id,
        "challenge_sha256": challenge.challenge_sha256,
        "instance_id": challenge.instance_id,
        "entity_id": challenge.entity_id,
        "controller_id": challenge.controller_id,
        "event_ref": challenge.event_evidence_id,
        "key_id": challenge.key_id,
        "role": challenge.role,
        "algorithm": "Ed25519",
        "public_key_sha256": challenge.public_key_sha256,
        "possession_proof_sha256": challenge.possession_proof_sha256,
        "previous_proof_sha256": challenge.previous_proof_sha256,
        "expires_at": challenge.expires_at.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ") if challenge.expires_at.tzinfo else challenge.expires_at.replace(tzinfo=timezone.utc, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nonce": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
    }
    if challenge.role == "controller":
        action.update({
            "trust_scope": "controller_governance_authority",
            "governance_authorisation": "root_passkey_per_publication",
        })
    rendered = canonical_json(action)
    digest = hashlib.sha256(rendered).hexdigest()
    ceremony = create_ceremony(
        options.challenge, TRUST_KEY_ACTIVATION, db,
        user_id=root.id, session_id=auth_session.id,
        ttl_minutes=5, action_json=rendered.decode("utf-8"), action_sha256=digest,
    )
    challenge.root_ceremony_id = ceremony.id
    db.commit()
    return {"options": options_to_json(options), "ceremony_id": ceremony.id, "action": action, "action_sha256": digest}


@router.post("/trust-keys/{challenge_id}/root-authorisation/complete")
def complete_root_authorisation(
    challenge_id: str,
    body: CeremonyCompletion,
    request: Request,
    root: User = Depends(require_root_admin),
    db: Session = Depends(get_db),
):
    """Verify the root passkey and activate the external public key."""
    auth_session = getattr(root, "_auth_session", None)
    if auth_session is None: raise HTTPException(status_code=401, detail="Session expired or invalid")
    challenge = db.query(EvidenceKeyRegistrationChallenge).filter(
        EvidenceKeyRegistrationChallenge.challenge_id == challenge_id,
        EvidenceKeyRegistrationChallenge.root_ceremony_id == body.ceremony_id,
        EvidenceKeyRegistrationChallenge.used_at.is_(None),
    ).first()
    if challenge is None or challenge.possession_proof_sha256 is None:
        raise _reject(TrustEvidenceError("the root-authorised trust-key action is unavailable"))
    ceremony = consume_ceremony(
        body.ceremony_id, TRUST_KEY_ACTIVATION, db,
        user_id=root.id, session_id=auth_session.id,
    )
    try:
        credential_id = _credential_id(body.credential)
        stored = db.query(WebAuthnCredential).filter(
            WebAuthnCredential.credential_id == credential_id,
            WebAuthnCredential.user_id == root.id,
        ).with_for_update().first()
        if stored is None: raise TrustEvidenceError("the root passkey credential is unavailable")
        _verify_user_handle(body.credential, root.id)
        verified = verify_authentication_response(
            credential=body.credential,
            expected_challenge=base64url_to_bytes(ceremony.challenge),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            credential_public_key=stored.public_key,
            credential_current_sign_count=stored.sign_count,
            require_user_verification=True,
        )
        action = json.loads(ceremony.action_json or "null")
        if not isinstance(action, dict) or action.get("challenge_id") != challenge.challenge_id:
            raise TrustEvidenceError("the root passkey ceremony is bound to another action")
        rendered = canonical_json(action)
        if ceremony.action_sha256 != hashlib.sha256(rendered).hexdigest():
            raise TrustEvidenceError("the root passkey action digest changed")
    except Exception as exc:
        audit(db, user=root, action="auth.reauth_failed", request=request, outcome="denied")
        db.commit()
        if isinstance(exc, (TrustEvidenceError, ValueError)): raise _reject(exc) from exc
        logger.warning("Trust-key root authorisation failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=400, detail="Root passkey authorisation failed") from exc

    now = datetime.now(timezone.utc)
    stored.sign_count = verified.new_sign_count
    stored.last_used_at = now
    credential_digest = hashlib.sha256(stored.credential_id).hexdigest()
    root_action = RootActionAuthorisation(
        authorisation_id=action["authorisation_id"], instance_id=challenge.instance_id,
        root_user_id=root.id, credential_id_sha256=credential_digest,
        action_sha256=ceremony.action_sha256, action_json=ceremony.action_json,
        server_verified_at=now,
    )
    db.add(root_action)
    row = EvidenceKey(
        key_id=challenge.key_id, public_key=challenge.public_key,
        public_key_sha256=challenge.public_key_sha256,
        instance_id=challenge.instance_id, entity_id=challenge.entity_id,
        controller_id=challenge.controller_id,
        event_id=challenge.event_id,
        algorithm="Ed25519", role=challenge.role,
        supersedes_key_id=challenge.supersedes_key_id,
        registration_proof_sha256=challenge.possession_proof_sha256,
        root_credential_id_sha256=credential_digest,
        root_action_sha256=ceremony.action_sha256,
        activated_at=now,
    )
    db.add(row)
    processor_identity = None
    if challenge.role == "processor" and challenge.event_evidence_id:
        processor_identity = db.query(ProcessorIdentity).filter(
            ProcessorIdentity.instance_id == challenge.instance_id,
            ProcessorIdentity.entity_id == challenge.entity_id,
        ).first()
        if processor_identity is None:
            processor_identity = ProcessorIdentity(
                instance_id=challenge.instance_id,
                entity_id=challenge.entity_id,
                controller_id=challenge.controller_id,
                event_id=challenge.event_id,
                event_evidence_id=challenge.event_evidence_id,
                event_display_name=challenge.event_display_name,
                display_label=challenge.display_label,
            )
            db.add(processor_identity)
        elif processor_identity.event_evidence_id != challenge.event_evidence_id:
            raise _reject(TrustEvidenceError("the processor identity is assigned to another event"))
    previous = None
    record_type = "trust_key.registered"
    operation = "registered"
    if challenge.purpose == "rotate":
        previous = db.query(EvidenceKey).filter(
            EvidenceKey.key_id == challenge.supersedes_key_id,
            EvidenceKey.instance_id == challenge.instance_id,
            EvidenceKey.entity_id == challenge.entity_id,
            EvidenceKey.role == challenge.role,
            EvidenceKey.controller_id == challenge.controller_id,
            EvidenceKey.revoked_at.is_(None),
        ).first()
        if previous is None: raise _reject(TrustEvidenceError("the superseded key is no longer active"))
        previous.revoked_at = now
        previous.revocation_reason = "retired" if challenge.rotation_reason == "routine" else challenge.rotation_reason
        previous.superseded_by_key_id = row.key_id
        record_type = "trust_key.rotated"; operation = "rotated"
    if processor_identity is not None:
        processor_identity.status = "active"
        processor_identity.active_key_id = row.key_id
        processor_identity.activated_at = now
    db.flush()
    challenge.used_at = now
    controller = db.get(Controller, row.controller_id)
    if controller is None:
        raise _reject(TrustEvidenceError("the controller trust scope is unavailable"))
    payload: dict[str, Any] = {
        "instance_id": row.instance_id, "entity_id": row.entity_id,
        "controller_id": controller.trust_entity_id,
        "key_id": row.key_id, "key_role": row.role, "algorithm": "ed25519",
        "public_key_sha256": row.public_key_sha256,
        "challenge_sha256": challenge.challenge_sha256,
        "proof_sha256": challenge.possession_proof_sha256,
        "root_credential_id_sha256": credential_digest,
        "root_action_sha256": ceremony.action_sha256,
        "root_authorisation": "root_passkey",
        "server_verification": "verified",
        "ledger_signer_role": "instance",
        "status": operation,
    }
    if row.role == "controller":
        payload.update({
            "trust_scope": "controller_governance_authority",
            "governance_authorisation": "root_passkey_per_publication",
        })
    if processor_identity is not None:
        payload.update({
            "event_ref": processor_identity.event_evidence_id,
            "processor_assignment_id": processor_identity.assignment_id,
        })
    if previous:
        payload.update({"previous_key_id": previous.key_id, "new_key_id": row.key_id, "reason_code": challenge.rotation_reason})
        if challenge.previous_proof_sha256: payload["previous_proof_sha256"] = challenge.previous_proof_sha256
    digest = append_record(
        db, workflow_type="trust_key", workflow_id=row.key_id,
        operation_type=operation, record_type=record_type, payload=payload,
        controller_id=row.controller_id, event_id=row.event_id,
    )
    if row.role == "controller":
        row.trust_establishment_sha256 = digest
    root_action.instance_record_sha256 = digest
    audit(db, user=root, action="evidence.trust_key.root_authorised", resource_type="evidence", request=request,
          detail=json.dumps({"schema_version": 1, "purpose": challenge.purpose, "role": challenge.role}))
    lifecycle_action = "evidence.trust_key.rotate" if previous else "evidence.trust_key.register"
    audit(db, user=root, action=lifecycle_action, resource_type="evidence", request=request,
          detail=json.dumps({"schema_version": 1, "role": challenge.role}))
    db.commit()
    return {"key": _public_key_row(row, processor_identity), "root_authorisation": {"role": "root_passkey", "credential_id_sha256": credential_digest, "action_sha256": ceremony.action_sha256, "server_verified_at": now}, "instance_record_sha256": digest}


@router.post("/trust-keys/{trust_key_id}/revoke")
def revoke_trust_key(
    trust_key_id: str,
    body: RevokeTrustKeyRequest,
    request: Request,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    """Revoke future signatures, preserving public historical verification."""
    try:
        if body.reason_code not in REVOCATION_REASONS: raise TrustEvidenceError("the revocation reason is invalid")
        row = db.query(EvidenceKey).filter(EvidenceKey.key_id == trust_key_id, EvidenceKey.role.in_(("controller", "processor")), EvidenceKey.revoked_at.is_(None)).first()
        if row is None: raise TrustEvidenceError("the trust key is unavailable or already revoked")
        controller = db.get(Controller, row.controller_id)
        if controller is None: raise TrustEvidenceError("the controller trust scope is unavailable")
        row.revoked_at = datetime.now(timezone.utc); row.revocation_reason = body.reason_code
        if row.role == "processor":
            identity = db.query(ProcessorIdentity).filter(
                ProcessorIdentity.entity_id == row.entity_id,
                ProcessorIdentity.active_key_id == row.key_id,
            ).first()
            if identity is not None:
                identity.status = "revoked"
                identity.revoked_at = row.revoked_at
        digest = append_record(
            db, workflow_type="trust_key", workflow_id=row.key_id,
            operation_type="revoked", record_type="trust_key.revoked",
            payload={"instance_id": row.instance_id, "entity_id": row.entity_id,
                     "controller_id": controller.trust_entity_id, "key_id": row.key_id,
                     "key_role": row.role, "algorithm": "ed25519",
                     "public_key_sha256": row.public_key_sha256,
                     "reason_code": body.reason_code, "root_authorisation": "recent_root_passkey",
                     "ledger_signer_role": "instance", "status": "revoked"},
            controller_id=row.controller_id, event_id=row.event_id,
        )
        audit(db, user=root, action="evidence.trust_key.revoke", resource_type="evidence", request=request,
              detail=json.dumps({"schema_version": 1, "role": row.role, "reason_code": body.reason_code}))
        db.commit(); return {"key": _public_key_row(row), "instance_record_sha256": digest}
    except (EvidenceUnavailable, TrustEvidenceError, ValueError) as exc:
        db.rollback(); raise _reject(exc) from exc
