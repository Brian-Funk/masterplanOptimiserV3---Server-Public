"""WebAuthn registration, authentication, and credential management."""
import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, options_to_json
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core import runtime_settings as rt
from app.core.activation import (
    ADDITIONAL_PASSKEY,
    CREDENTIAL_RESET,
    INITIAL_SETUP,
    validate_activation_token,
)
from app.core.activation_consent import (
    ActivationConsentError,
    STATEMENT_VERSION,
    resolve_activation_consent,
)
from app.core.audit import audit
from app.core.config import settings
from app.core.governance import (
    BOOTSTRAP_POLICY_SHA256,
    BOOTSTRAP_POLICY_TEXT,
    BOOTSTRAP_POLICY_VERSION,
    stable_instance_id,
)
from app.core.passkey_ceremonies import (
    ACCOUNT_REGISTRATION,
    ACTIVATION_REGISTRATION,
    AUTHENTICATION,
    BOOTSTRAP_REGISTRATION,
    consume_ceremony,
    create_ceremony,
)
from app.core.rate_limit import (
    PASSKEY_COARSE_IP_LIMIT,
    client_ip_rate_key,
    limiter,
    passkey_registration_rate_key,
    runtime_limit,
)
from app.core.security import (
    ensure_recent_reauth,
    get_current_user,
)
from app.core.commissioning import commissioning_stage
from app.core.evidence import EvidenceUnavailable, append_record
from app.core.sessions import revoke_all_user_sessions, validate_session
from app.db.database import get_db
from app.models.user import (
    ActivationLink,
    AuthSession,
    ExchangeCode,
    User,
    WebAuthnCredential,
)
from app.models.governance import AccountProcessingConsent
from app.models.server_setting import ServerSetting

logger = logging.getLogger(__name__)
router = APIRouter()


class BootstrapStatusResponse(BaseModel):
    """Whether root passkey bootstrap is required and configured."""

    needs_bootstrap: bool
    bootstrap_configured: bool
    bootstrap_disabled: bool
    stage: Literal["passkey", "setup", "complete"]
    policy_version: str
    policy_sha256: str
    policy_text: str


class PasskeyCredentialInfo(BaseModel):
    """Public metadata for a registered passkey credential."""

    id: int
    friendly_name: Optional[str]
    created_at: datetime
    last_used_at: Optional[datetime]


class CeremonyCompletion(BaseModel):
    """Opaque ceremony identifier and browser WebAuthn response."""

    ceremony_id: str = Field(..., min_length=20, max_length=128)
    credential: dict
    policy_version: Optional[str] = Field(None, max_length=64)
    policy_sha256: Optional[str] = Field(None, pattern=r"^[0-9a-f]{64}$")


class ProcessingConsentConfirmation(BaseModel):
    """Exact unchecked confirmation submitted before first WebAuthn activation."""

    # Optional at request parsing so existing authenticated and non-initial
    # activation clients may continue sending an empty object. Initial setup
    # validates every value below before a ceremony can be created.
    confirmed: Literal[True] | None = None
    statement_version: str | None = Field(None, min_length=1, max_length=64)
    statement_sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    policy_version: int | None = Field(None, ge=1)
    policy_sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")


class CredentialRename(BaseModel):
    """New user-visible name for a passkey."""

    friendly_name: str = Field(..., min_length=1, max_length=100)


def _root_needs_bootstrap(db: Session) -> bool:
    root = db.query(User).filter(User.is_root_admin == True).first()  # noqa: E712
    if root is None:
        return True
    credential = db.query(WebAuthnCredential).filter(
        WebAuthnCredential.user_id == root.id
    ).first()
    return credential is None


def _bootstrap_stage(db: Session) -> Literal["passkey", "setup", "complete"]:
    root = db.query(User).filter(User.is_root_admin == True).first()  # noqa: E712
    if root is None:
        return "passkey"
    credential = db.query(WebAuthnCredential).filter(
        WebAuthnCredential.user_id == root.id
    ).first()
    if credential is None:
        return "passkey"
    return "complete" if commissioning_stage(db) == "complete" else "setup"


def _bootstrap_is_disabled(db: Session) -> bool:
    setting = db.query(ServerSetting).filter(ServerSetting.key == "root_bootstrap_disabled").first()
    return bool(setting and setting.value == "true")


def _require_bootstrap_token(request: Request, db: Session) -> None:
    """Require the operator-provided bootstrap secret without logging it."""
    if _bootstrap_is_disabled(db):
        raise HTTPException(status_code=403, detail="Root bootstrap is permanently disabled")
    configured = settings.ROOT_BOOTSTRAP_TOKEN
    supplied = request.headers.get("x-bootstrap-token", "")
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="Root bootstrap is not configured on the server",
        )
    if not supplied or not secrets.compare_digest(configured, supplied):
        raise HTTPException(status_code=403, detail="Invalid root bootstrap code")


def _active_user(
    user_id: int,
    db: Session,
    *,
    require_activated: bool = True,
    allow_root_recovery: bool = False,
) -> User:
    """Load an account that is currently permitted to authenticate."""
    user = db.query(User).filter(User.id == user_id).first()
    if (
        user is None
        or not user.is_active
        or (
            require_activated
            and not user.is_activated
            and not (allow_root_recovery and user.is_root_admin)
        )
    ):
        raise HTTPException(status_code=401, detail="Authentication failed")
    return user


def _session_registration_context(request: Request, db: Session) -> tuple[User, AuthSession]:
    """Resolve and re-authorise a session-based credential registration."""
    raw_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    auth_session = validate_session(
        raw_token,
        db,
        user_agent=request.headers.get("user-agent"),
        accept_language=request.headers.get("accept-language"),
    )
    if auth_session is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    user = _active_user(auth_session.user_id, db)
    if not (user.is_root_admin or user.is_admin or user.is_issuer):
        raise HTTPException(status_code=403, detail="Passkey management access required")
    user._auth_session = auth_session  # type: ignore[attr-defined]
    ensure_recent_reauth(user, db)
    return user, auth_session


def _activation_context(
    request: Request,
    db: Session,
    *,
    for_update: bool = False,
) -> tuple[User, ActivationLink] | None:
    """Resolve an activation-token registration request, if present."""
    token = request.headers.get("x-activation-token", "")
    if not token:
        return None
    link = validate_activation_token(token, db, for_update=for_update)
    if link is None:
        raise HTTPException(status_code=401, detail="Invalid or expired activation token")
    user = _active_user(link.user_id, db, require_activated=False)
    return user, link


def _registration_options(user: User, db: Session):
    existing = db.query(WebAuthnCredential).filter(
        WebAuthnCredential.user_id == user.id
    ).all()
    return generate_registration_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        rp_name=settings.WEBAUTHN_RP_NAME,
        user_id=str(user.id).encode(),
        user_name=user.username,
        user_display_name=user.display_name,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=credential.credential_id)
            for credential in existing
        ],
    )


def _verify_user_handle(credential: dict, user_id: int) -> None:
    """Bind a discoverable authentication response to its stored account."""
    response = credential.get("response")
    encoded = response.get("userHandle") if isinstance(response, dict) else None
    if not isinstance(encoded, str) or not encoded:
        raise HTTPException(status_code=400, detail="Authentication failed")
    try:
        user_handle = base64url_to_bytes(encoded)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Authentication failed") from exc
    if not secrets.compare_digest(user_handle, str(user_id).encode()):
        raise HTTPException(status_code=400, detail="Authentication failed")


def _credential_id(credential: dict) -> bytes:
    raw = credential.get("rawId") or credential.get("id")
    if not isinstance(raw, str) or not raw:
        raise HTTPException(status_code=400, detail="Authentication failed")
    try:
        return base64url_to_bytes(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Authentication failed") from exc


def _create_exchange_code(user_id: int, db: Session) -> str:
    """Create a raw exchange code while persisting only its digest."""
    raw_code = secrets.token_urlsafe(32)
    db.add(
        ExchangeCode(
            code=hashlib.sha256(raw_code.encode()).hexdigest(),
            user_id=user_id,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=rt.get_int("exchange_code_ttl_seconds", db)),
        )
    )
    return raw_code


def _record_verification_failure(
    db: Session,
    request: Request,
    *,
    action: str,
    user: User | None = None,
) -> None:
    """Persist a safe denial record and the ceremony's consumed state."""
    audit(db, user=user, action=action, request=request, outcome="denied")
    db.commit()


def _consent_action(
    user: User,
    link: ActivationLink,
    confirmation: ProcessingConsentConfirmation | None,
    db: Session,
) -> tuple[str | None, str | None]:
    """Validate and canonicalise consent only for an account's first activation."""

    if link.purpose != INITIAL_SETUP:
        return None, None
    if confirmation is None or confirmation.confirmed is not True:
        raise HTTPException(
            status_code=428,
            detail={
                "code": "processing_consent_required",
                "message": "Review and confirm the processing information before registering a passkey.",
            },
        )
    try:
        disclosure = resolve_activation_consent(user, db)
    except ActivationConsentError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": exc.safe_message},
        ) from exc
    expected = disclosure.document
    if (
        confirmation.statement_version != STATEMENT_VERSION
        or not secrets.compare_digest(
            str(confirmation.statement_sha256 or "").lower(),
            disclosure.statement_sha256,
        )
        or confirmation.policy_version != expected["policy_version"]
        or not secrets.compare_digest(
            str(confirmation.policy_sha256 or "").lower(),
            str(expected["policy_sha256"]),
        )
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "processing_consent_identity_mismatch",
                "message": "The processing information changed. Review the current exact notice before continuing.",
            },
        )
    action = json.dumps(
        {
            "format": "mp-opt-activation-registration-action-v1",
            "consent_document": expected,
            "consent_statement_sha256": disclosure.statement_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return action, hashlib.sha256(action.encode("utf-8")).hexdigest()


@router.get("/bootstrap-status", response_model=BootstrapStatusResponse)
def bootstrap_status(db: Session = Depends(get_db)):
    """Return whether root bootstrap is required and operator-enabled."""
    disabled = _bootstrap_is_disabled(db)
    return BootstrapStatusResponse(
        needs_bootstrap=_root_needs_bootstrap(db),
        bootstrap_configured=bool(settings.ROOT_BOOTSTRAP_TOKEN) and not disabled,
        bootstrap_disabled=disabled,
        stage=_bootstrap_stage(db),
        policy_version=BOOTSTRAP_POLICY_VERSION,
        policy_sha256=BOOTSTRAP_POLICY_SHA256,
        policy_text=BOOTSTRAP_POLICY_TEXT,
    )


@router.post("/bootstrap/begin")
@limiter.limit("5/minute")
def bootstrap_begin(request: Request, db: Session = Depends(get_db)):
    """Start root registration after verifying the operator bootstrap code."""
    _require_bootstrap_token(request, db)
    if _bootstrap_stage(db) != "passkey":
        raise HTTPException(status_code=403, detail="Root passkey registration is already complete")
    root = db.query(User).filter(User.is_root_admin == True).first()  # noqa: E712
    if root is None:
        raise HTTPException(status_code=500, detail="Root admin user not found")
    options = _registration_options(root, db)
    ceremony = create_ceremony(
        options.challenge,
        BOOTSTRAP_REGISTRATION,
        db,
        user_id=root.id,
    )
    return {"options": options_to_json(options), "ceremony_id": ceremony.id}


@router.post("/bootstrap/complete")
@limiter.limit("5/minute")
def bootstrap_complete(
    body: CeremonyCompletion,
    request: Request,
    db: Session = Depends(get_db),
):
    """Complete the one-time root passkey registration."""
    _require_bootstrap_token(request, db)
    if (
        body.policy_version != BOOTSTRAP_POLICY_VERSION
        or body.policy_sha256 != BOOTSTRAP_POLICY_SHA256
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "bootstrap_policy_identity_mismatch",
                "policy_version": BOOTSTRAP_POLICY_VERSION,
                "policy_sha256": BOOTSTRAP_POLICY_SHA256,
                "message": "The setup policy changed. Review the exact policy shown by the server.",
            },
        )
    root = (
        db.query(User)
        .filter(User.is_root_admin == True)  # noqa: E712
        .with_for_update()
        .first()
    )
    if root is None:
        raise HTTPException(status_code=500, detail="Root admin user not found")
    if db.query(WebAuthnCredential).filter(WebAuthnCredential.user_id == root.id).first():
        raise HTTPException(status_code=403, detail="Bootstrap already completed")
    ceremony = consume_ceremony(
        body.ceremony_id,
        BOOTSTRAP_REGISTRATION,
        db,
        user_id=root.id,
    )
    try:
        verification = verify_registration_response(
            credential=body.credential,
            expected_challenge=base64url_to_bytes(ceremony.challenge),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            require_user_verification=True,
        )
    except Exception as exc:
        logger.warning("Bootstrap registration verification failed (%s)", type(exc).__name__)
        _record_verification_failure(
            db,
            request,
            action="passkey.bootstrap_failed",
            user=root,
        )
        raise HTTPException(status_code=400, detail="Registration verification failed")

    credential = WebAuthnCredential(
        user_id=root.id,
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        aaguid=str(verification.aaguid) if verification.aaguid else None,
        friendly_name="Root Passkey (bootstrap)",
    )
    try:
        with db.begin_nested():
            db.add(credential)
            db.flush()
    except IntegrityError as exc:
        _record_verification_failure(
            db,
            request,
            action="passkey.duplicate_denied",
            user=root,
        )
        raise HTTPException(status_code=409, detail="Passkey is already registered") from exc
    # Authentication is available immediately; commissioning is enforced by
    # authoritative deployment facts rather than the account activation flag.
    root.is_activated = True
    setup_acknowledgement = {
        "governance_setup_ack_instance_id": stable_instance_id(db),
        "governance_setup_ack_root_user_id": str(root.id),
        "governance_setup_ack_version": body.policy_version,
        "governance_setup_ack_sha256": body.policy_sha256,
        "governance_setup_acknowledged_at": datetime.now(timezone.utc).isoformat(),
    }
    for key, value in setup_acknowledgement.items():
        row = db.query(ServerSetting).filter(ServerSetting.key == key).first()
        if row is None:
            db.add(ServerSetting(key=key, value=value))
        else:
            row.value = value
    disabled = db.query(ServerSetting).filter(ServerSetting.key == "root_bootstrap_disabled").first()
    if disabled is None:
        db.add(ServerSetting(key="root_bootstrap_disabled", value="true"))
    else:
        disabled.value = "true"
    raw_code = _create_exchange_code(root.id, db)
    audit(
        db,
        user=root,
        action="passkey.bootstrap",
        resource_type="credential",
        request=request,
    )
    db.commit()
    return {
        "status": "commissioning_required",
        "exchange_code": raw_code,
        "setup_url": "/setup",
        "message": "Root passkey registered. Continue the three-step commissioning wizard.",
    }


@router.post("/register/begin")
@limiter.limit(PASSKEY_COARSE_IP_LIMIT, key_func=client_ip_rate_key)
@limiter.limit(
    runtime_limit("passkey_requests_per_minute"),
    key_func=passkey_registration_rate_key,
)
def register_begin(
    request: Request,
    body: ProcessingConsentConfirmation | None = None,
    db: Session = Depends(get_db),
):
    """Start activation-based or recently re-authenticated registration."""
    activation = _activation_context(request, db)
    if activation:
        user, link = activation
        purpose = ACTIVATION_REGISTRATION
        session_id = None
        activation_link_id = link.id
        action_json, action_sha256 = _consent_action(user, link, body, db)
    else:
        user, auth_session = _session_registration_context(request, db)
        purpose = ACCOUNT_REGISTRATION
        session_id = auth_session.id
        activation_link_id = None
        action_json = None
        action_sha256 = None

    options = _registration_options(user, db)
    ceremony = create_ceremony(
        options.challenge,
        purpose,
        db,
        user_id=user.id,
        session_id=session_id,
        activation_link_id=activation_link_id,
        action_json=action_json,
        action_sha256=action_sha256,
    )
    return {"options": options_to_json(options), "ceremony_id": ceremony.id}


def _verified_ceremony_consent(
    user: User,
    link: ActivationLink | None,
    ceremony,
    db: Session,
):
    """Return the still-current disclosure bound to an initial ceremony."""

    if link is None or link.purpose != INITIAL_SETUP:
        return None
    if not ceremony.action_json or not ceremony.action_sha256:
        raise HTTPException(
            status_code=428,
            detail={
                "code": "processing_consent_required",
                "message": "Review and confirm the processing information before registering a passkey.",
            },
        )
    if not secrets.compare_digest(
        hashlib.sha256(ceremony.action_json.encode("utf-8")).hexdigest(),
        ceremony.action_sha256,
    ):
        raise HTTPException(status_code=409, detail="The activation ceremony is invalid")
    try:
        action = json.loads(ceremony.action_json)
        disclosure = resolve_activation_consent(user, db)
    except (TypeError, ValueError, json.JSONDecodeError, ActivationConsentError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "processing_consent_unavailable",
                "message": "The processing information could not be verified. Review the activation page again.",
            },
        ) from exc
    if (
        action.get("format") != "mp-opt-activation-registration-action-v1"
        or action.get("consent_document") != disclosure.document
        or not secrets.compare_digest(
            str(action.get("consent_statement_sha256") or ""),
            disclosure.statement_sha256,
        )
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "processing_consent_identity_mismatch",
                "message": "The processing information changed. Review the current exact notice before continuing.",
            },
        )
    return disclosure


@router.post("/register/complete")
@limiter.limit(PASSKEY_COARSE_IP_LIMIT, key_func=client_ip_rate_key)
@limiter.limit(
    runtime_limit("passkey_requests_per_minute"),
    key_func=passkey_registration_rate_key,
)
def register_complete(
    body: CeremonyCompletion,
    request: Request,
    db: Session = Depends(get_db),
):
    """Verify and store one passkey without losing activation structure."""
    activation = _activation_context(request, db, for_update=True)
    if activation:
        user, link = activation
        purpose = ACTIVATION_REGISTRATION
        session_id = None
        activation_link_id = link.id
    else:
        user, auth_session = _session_registration_context(request, db)
        link = None
        purpose = ACCOUNT_REGISTRATION
        session_id = auth_session.id
        activation_link_id = None

    ceremony = consume_ceremony(
        body.ceremony_id,
        purpose,
        db,
        user_id=user.id,
        session_id=session_id,
        activation_link_id=activation_link_id,
    )
    consent_disclosure = _verified_ceremony_consent(user, link, ceremony, db)
    try:
        verification = verify_registration_response(
            credential=body.credential,
            expected_challenge=base64url_to_bytes(ceremony.challenge),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            require_user_verification=True,
        )
    except Exception as exc:
        logger.warning(
            "Passkey registration verification failed for uid=%s (%s)",
            user.id,
            type(exc).__name__,
        )
        _record_verification_failure(
            db,
            request,
            action="passkey.registration_failed",
            user=user,
        )
        raise HTTPException(status_code=400, detail="Registration verification failed")

    if db.query(WebAuthnCredential).filter(
        WebAuthnCredential.credential_id == verification.credential_id
    ).first():
        _record_verification_failure(
            db,
            request,
            action="passkey.duplicate_denied",
            user=user,
        )
        raise HTTPException(status_code=409, detail="Passkey is already registered")

    credential = WebAuthnCredential(
        user_id=user.id,
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        aaguid=str(verification.aaguid) if verification.aaguid else None,
        friendly_name="Passkey",
    )
    try:
        with db.begin_nested():
            db.add(credential)
            db.flush()
    except IntegrityError as exc:
        _record_verification_failure(
            db,
            request,
            action="passkey.duplicate_denied",
            user=user,
        )
        raise HTTPException(status_code=409, detail="Passkey is already registered") from exc

    consent_row = None
    if consent_disclosure is not None and link is not None:
        document = consent_disclosure.document
        consented_at = datetime.now(timezone.utc)
        consent_row = AccountProcessingConsent(
            user_id=user.id,
            user_subject_id=user.evidence_subject_id,
            event_id=user.event_id,
            event_evidence_id=document.get("event_ref"),
            activation_link_id=link.id,
            policy_version=int(document["policy_version"]),
            policy_sha256=str(document["policy_sha256"]),
            statement_version=STATEMENT_VERSION,
            statement_sha256=consent_disclosure.statement_sha256,
            controller_identity=str(document["controller_identity"]),
            document_json=consent_disclosure.document_json,
            consented_at=consented_at,
        )
        try:
            with db.begin_nested():
                db.add(consent_row)
                db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "processing_consent_already_recorded",
                    "message": "This account activation consent is already recorded.",
                },
            ) from exc
        try:
            consent_row.instance_record_sha256 = append_record(
                db,
                workflow_type="account_consent",
                workflow_id=consent_row.consent_id,
                operation_type="recorded",
                record_type="account.processing_consent_recorded",
                payload={
                    "subject_ref": user.evidence_subject_id,
                    **(
                        {"event_ref": document["event_ref"]}
                        if document.get("event_ref")
                        else {}
                    ),
                    "policy_version": consent_row.policy_version,
                    "policy_sha256": consent_row.policy_sha256,
                    "statement_sha256": consent_row.statement_sha256,
                    "document_sha256": hashlib.sha256(
                        consent_row.document_json.encode("utf-8")
                    ).hexdigest(),
                    "signed_at": consented_at.replace(microsecond=0).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                },
            )
        except EvidenceUnavailable as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "processing_consent_evidence_unavailable",
                    "message": "The consent record could not be sealed. No passkey was registered; try again.",
                },
            ) from exc
    replaced_passkeys = _apply_activation_credential_policy(
        user_id=user.id,
        new_credential=credential,
        activation_purpose=link.purpose if link is not None else None,
        db=db,
    )
    if link is not None:
        link.used_at = datetime.now(timezone.utc)
        user.is_activated = True
    audit(
        db,
        user=user,
        action="passkey.register",
        resource_type="credential",
        detail=json.dumps({
            "purpose": link.purpose if link is not None else "account_registration",
            "credentials_replaced": replaced_passkeys,
            "processing_consent_sha256": (
                consent_row.statement_sha256 if consent_row is not None else None
            ),
        }),
        request=request,
    )
    db.commit()

    sessions_revoked = 0
    if not _registration_preserves_sessions(
        link.purpose if link is not None else None
    ):
        sessions_revoked = revoke_all_user_sessions(user.id, db)
    return {
        "status": "ok",
        "message": "Passkey registered",
        "replaced_passkeys": replaced_passkeys,
        "sessions_revoked": sessions_revoked,
    }


def _apply_activation_credential_policy(
    *,
    user_id: int,
    new_credential: WebAuthnCredential,
    activation_purpose: str | None,
    db: Session,
) -> int:
    """Apply the selected invitation outcome to a verified new credential.

    Additional-passkey invitations keep all previous credentials. Reset
    invitations replace them. Initial and signed-in registrations retain the
    established additive behaviour.
    """

    if activation_purpose == CREDENTIAL_RESET:
        return _replace_previous_credentials(
            user_id=user_id,
            new_credential=new_credential,
            db=db,
        )
    if activation_purpose == ADDITIONAL_PASSKEY:
        new_credential.friendly_name = "Additional passkey"
    return 0


def _registration_preserves_sessions(activation_purpose: str | None) -> bool:
    """Return whether registration must leave current sessions untouched."""

    return activation_purpose == ADDITIONAL_PASSKEY


def _replace_previous_credentials(
    *,
    user_id: int,
    new_credential: WebAuthnCredential,
    db: Session,
) -> int:
    """Replace every existing passkey after a verified reset registration.

    The new credential must already have been flushed so its database identifier
    can be excluded. The deletion remains in the caller's transaction.
    """

    replaced = (
        db.query(WebAuthnCredential)
        .filter(
            WebAuthnCredential.user_id == user_id,
            WebAuthnCredential.id != new_credential.id,
        )
        .delete(synchronize_session=False)
    )
    new_credential.friendly_name = "Replacement passkey"
    return replaced


@router.get("/credentials", response_model=List[PasskeyCredentialInfo])
def list_credentials(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List passkeys registered to the current account."""
    return [
        PasskeyCredentialInfo(
            id=credential.id,
            friendly_name=credential.friendly_name,
            created_at=credential.created_at,
            last_used_at=credential.last_used_at,
        )
        for credential in db.query(WebAuthnCredential)
        .filter(WebAuthnCredential.user_id == current_user.id)
        .all()
    ]


@router.delete("/credentials/{credential_id}")
def delete_credential(
    credential_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a passkey after recent re-authentication."""
    ensure_recent_reauth(current_user, db)
    # Serialise credential removal per account so concurrent requests cannot
    # both observe two credentials and delete the final two passkeys.
    db.query(User).filter(User.id == current_user.id).with_for_update().one()
    credential = db.query(WebAuthnCredential).filter(
        WebAuthnCredential.id == credential_id,
        WebAuthnCredential.user_id == current_user.id,
    ).first()
    if credential is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    count = db.query(WebAuthnCredential).filter(
        WebAuthnCredential.user_id == current_user.id
    ).count()
    if count <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last passkey")
    db.delete(credential)
    audit(
        db,
        user=current_user,
        action="passkey.delete",
        resource_type="credential",
        resource_id=credential_id,
        request=request,
    )
    db.commit()
    revoke_all_user_sessions(current_user.id, db)
    return {"status": "ok", "message": "Credential deleted"}


@router.patch("/credentials/{credential_id}")
def rename_credential(
    credential_id: int,
    body: CredentialRename,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the friendly name of one current-account passkey."""
    credential = db.query(WebAuthnCredential).filter(
        WebAuthnCredential.id == credential_id,
        WebAuthnCredential.user_id == current_user.id,
    ).first()
    if credential is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    credential.friendly_name = body.friendly_name.strip()
    if not credential.friendly_name:
        raise HTTPException(status_code=422, detail="Passkey name is required")
    audit(
        db,
        user=current_user,
        action="passkey.rename",
        resource_type="credential",
        resource_id=credential.id,
        request=request,
    )
    db.commit()
    return {"status": "ok"}


@router.post("/auth/begin")
@limiter.limit(
    runtime_limit("passkey_requests_per_minute"),
    key_func=client_ip_rate_key,
)
def auth_begin(request: Request, db: Session = Depends(get_db)):
    """Start discoverable-passkey authentication."""
    options = generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    ceremony = create_ceremony(options.challenge, AUTHENTICATION, db)
    return {"options": options_to_json(options), "ceremony_id": ceremony.id}


@router.post("/auth/complete")
@limiter.limit(
    runtime_limit("passkey_requests_per_minute"),
    key_func=client_ip_rate_key,
)
def auth_complete(
    body: CeremonyCompletion,
    request: Request,
    db: Session = Depends(get_db),
):
    """Verify a discoverable passkey and issue a short-lived exchange code."""
    ceremony = consume_ceremony(body.ceremony_id, AUTHENTICATION, db)
    try:
        credential_id = _credential_id(body.credential)
    except HTTPException as exc:
        _record_verification_failure(db, request, action="passkey.auth_failed")
        raise HTTPException(status_code=400, detail="Authentication failed") from exc
    stored_credential = (
        db.query(WebAuthnCredential)
        .filter(WebAuthnCredential.credential_id == credential_id)
        .with_for_update()
        .first()
    )
    if stored_credential is None:
        _record_verification_failure(db, request, action="passkey.auth_failed")
        raise HTTPException(status_code=400, detail="Authentication failed")
    try:
        user = _active_user(
            stored_credential.user_id,
            db,
            allow_root_recovery=True,
        )
    except HTTPException as exc:
        denied_user = db.query(User).filter(User.id == stored_credential.user_id).first()
        _record_verification_failure(
            db,
            request,
            action="passkey.auth_failed",
            user=denied_user,
        )
        raise HTTPException(status_code=401, detail="Authentication failed") from exc
    try:
        _verify_user_handle(body.credential, user.id)
        verification = verify_authentication_response(
            credential=body.credential,
            expected_challenge=base64url_to_bytes(ceremony.challenge),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            credential_public_key=stored_credential.public_key,
            credential_current_sign_count=stored_credential.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        logger.warning(
            "Passkey authentication failed for uid=%s (%s)",
            user.id,
            type(exc).__name__,
        )
        _record_verification_failure(
            db,
            request,
            action="passkey.auth_failed",
            user=user,
        )
        raise HTTPException(status_code=400, detail="Authentication failed")

    stored_credential.sign_count = verification.new_sign_count
    stored_credential.last_used_at = datetime.now(timezone.utc)
    raw_code = _create_exchange_code(user.id, db)
    db.commit()
    return {"status": "ok", "exchange_code": raw_code}
