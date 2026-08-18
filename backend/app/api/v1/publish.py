"""
Publish endpoint  -  receives masterplan data from the desktop app.

Authentication: Bearer token matching an event's publish_secret_hash.
Strategy: full publish replaces the event schedule; date-scoped publish replaces only
the requested published days.
"""
import base64
import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.core.rate_limit import limiter, runtime_limit
from app.core.config import settings
from app.core.retention import materialise_event_purge_deadline
from app.core.audit import audit
from app.core.evidence import EvidenceUnavailable, append_record, initialise
from app.core.evidence_identity import CanonicalEvidenceIdentity
from app.core.governance import current_policy_identity
from app.core.operator_evidence import (
    DESKTOP_EVIDENCE_NAMESPACE,
    PROCESSOR_EVENT_REGISTRATION_FORMAT,
    ROTATION_REASONS,
    TrustEvidenceError,
    canonical_json,
    canonical_public_key,
    key_id,
    processor_event_action_sha256,
    public_key_sha256,
    signed_desktop_evidence_package,
    validate_desktop_evidence_document,
    validate_entity,
    validate_processor_event_registration,
    verify_signature,
)
from app.core.schedule_days import (
    event_schedule_day_range,
    merge_schedule_day_range,
    normalise_schedule_day_range,
    working_date_for_datetime,
)
from app.db.database import get_db
from app.models.event import Event
from app.models.ha import HAProtectionOperation
from app.models.published import (
    PublishedPerson,
    PublishedPersonUnavailability,
    PublishedTask,
    TaskEdit,
)
from app.models.user import User
from app.models.deletion import DeletionCase, DesktopDeletionWorkOrder
from app.models.evidence import (
    EvidenceKey,
    EvidenceKeyRegistrationChallenge,
    ProcessorIdentity,
    ProcessorPolicyAcknowledgement,
)
from app.core.deletion_cases import (
    apply_desktop_copy_resolution,
    apply_desktop_report,
    claim_work_order,
)
from app.core.publish_contract import (
    FieldPurpose,
    FieldType,
    FieldVisibility,
    PUBLISH_CONTRACT_VERSION,
    validate_published_field_value,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class AttendeeIn(BaseModel):
    """Published attendee received from the desktop app."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., max_length=256)
    person_id: int = Field(..., gt=0)


class PublishedFieldDefinitionIn(BaseModel):
    """Reviewed purpose and audience for one bounded published field."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str = Field(..., min_length=1, max_length=256)
    type: FieldType
    purpose: FieldPurpose
    visibility: FieldVisibility


class TaskIn(BaseModel):
    """Published task received from the desktop app."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., gt=0)
    name: str = Field(..., max_length=512)
    summary: Optional[str] = Field(None, max_length=2000)
    description: Optional[str] = Field(None, max_length=10000)
    start: str = Field(..., max_length=64)  # ISO datetime
    end: str = Field(..., max_length=64)    # ISO datetime
    location_name: Optional[str] = Field(None, max_length=512)
    location_address: Optional[str] = Field(None, max_length=1024)
    task_type_code: Optional[str] = Field(None, max_length=64)
    task_type_name: Optional[str] = Field(None, max_length=256)
    color: Optional[str] = Field(None, max_length=32)
    attendees: List[AttendeeIn] = Field(default_factory=list)
    field_assignments: Optional[Dict[str, List[AttendeeIn]]] = None
    field_values: Optional[Dict[str, Any]] = Field(None, max_length=100)
    field_definitions: Optional[List[PublishedFieldDefinitionIn]] = Field(None, max_length=100)
    sort_order: Optional[float] = 0

    @model_validator(mode="after")
    def reject_private_profiling(self):
        """Reject structured fields that the operational service must not hold."""

        _reject_prohibited_profile_fields(
            field_values=self.field_values,
            field_definitions=self.field_definitions,
            additional=None,
        )
        definitions = self.field_definitions or []
        definition_by_id = {definition.id: definition for definition in definitions}
        if len(definition_by_id) != len(definitions):
            raise ValueError("Published field identifiers must be unique")
        values = self.field_values or {}
        assignments = self.field_assignments or {}
        unknown = (set(values) | set(assignments)) - set(definition_by_id)
        if unknown:
            raise ValueError("Published values contain an unclassified field")
        for field_id, definition in definition_by_id.items():
            if definition.visibility == "never_publish" and (
                field_id in values or field_id in assignments
            ):
                raise ValueError("Fields marked never_publish must not cross the publish boundary")
            if field_id in values and not validate_published_field_value(
                definition.type, values[field_id]
            ):
                raise ValueError(f"Published field {field_id} does not match its declared type")
            if definition.type == "persons_list" and field_id in values:
                raise ValueError("persons_list data must use the structured assignment contract")
            if field_id in assignments and definition.type != "persons_list":
                raise ValueError("Only persons_list fields may contain published assignments")
        assigned_people: set[int] = set()
        ordered_assignments: list[AttendeeIn] = []
        for definition in definitions:
            if definition.type != "persons_list":
                continue
            for attendee in assignments.get(definition.id, []):
                if attendee.person_id in assigned_people:
                    raise ValueError(
                        "A person may be allocated only once across a published task's assignment fields"
                    )
                assigned_people.add(attendee.person_id)
                ordered_assignments.append(attendee)
        attendee_ids = [attendee.person_id for attendee in self.attendees]
        if len(attendee_ids) != len(set(attendee_ids)):
            raise ValueError("A person may appear only once in a published task's attendee list")
        if any(definition.type == "persons_list" for definition in definitions):
            if self.attendees != ordered_assignments:
                raise ValueError(
                    "Published flat attendees must exactly match the ordered assignment fields"
                )
        return self


class PersonIn(BaseModel):
    """Published person received from the desktop app."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., gt=0)
    first_name: str = Field(..., max_length=256)
    last_name: str = Field(..., max_length=256)
    email: Optional[str] = Field(None, max_length=512)
    evidence_subject_id: CanonicalEvidenceIdentity


class EventMetaIn(BaseModel):
    """Published event metadata supplied by the desktop app."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, max_length=256)
    start_date: Optional[str] = Field(None, max_length=16)
    end_date: Optional[str] = Field(None, max_length=16)
    day_aliases: Optional[Dict[str, str]] = None  # {"2026-08-28": "Arrival Day"}
    schedule_day_range: Optional[Dict[str, int]] = None


class PersonUnavailabilityIn(BaseModel):
    """Published person-unavailability interval for one working day."""

    model_config = ConfigDict(extra="forbid")

    person_id: int = Field(..., gt=0)
    working_date: str = Field(..., max_length=10)
    start: str = Field(..., max_length=32)
    end: str = Field(..., max_length=32)


class PublishPayload(BaseModel):
    """Published schedule payload from the desktop app."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[PUBLISH_CONTRACT_VERSION]
    event: Optional[EventMetaIn] = None
    tasks: List[TaskIn]
    persons: List[PersonIn] = Field(default_factory=list)
    unavailabilities: List[PersonUnavailabilityIn] = Field(default_factory=list)
    publish_scope: Optional[Literal["full", "dates"]] = "full"
    dates: Optional[List[str]] = None


class ProcessorPublicPackageIn(BaseModel):
    """Public-only event processor enrolment material."""

    model_config = ConfigDict(extra="forbid")
    format: Literal["mp-opt-processor-public-key-v1"]
    instance_id: None = None
    entity_id: str = Field(pattern=r"^prc-[a-z0-9]{8,48}$")
    key_id: str = Field(pattern=r"^ek-[0-9a-f]{16}$")
    role: Literal["processor"]
    algorithm: Literal["Ed25519"]
    public_key: str = Field(min_length=32, max_length=2048)
    public_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    supersedes_key_id: str | None = Field(default=None, pattern=r"^ek-[0-9a-f]{16}$")
    rotation_reason: Literal["routine", "lost", "compromised"] | None = None
    display_label: str | None = Field(default=None, max_length=128)
    created_at: str = Field(max_length=32)
    signature_namespace: Literal["mp-opt-role-trust-v1"]


class ProcessorPossessionProofIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge: dict[str, Any]
    proof: dict[str, Any]
    previous_proof: dict[str, Any] | None = None


class SignedDesktopDocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: dict[str, Any]
    proof: dict[str, Any]


class PublishResponse(BaseModel):
    """Summary of rows created and edits cleared by a publish."""

    status: str
    tasks_created: int
    persons_created: int
    edits_cleared: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROHIBITED_PROFILE_TOKENS = {
    "allergies",
    "allergy",
    "criminal",
    "diagnosis",
    "diet",
    "dietary",
    "disciplinary",
    "disability",
    "ethnicity",
    "health",
    "medical",
    "political",
    "private_note",
    "private_profile",
    "religion",
    "religious",
    "safeguarding",
    "sexual_orientation",
    "trade_union",
}


def _normalised_field_tokens(value: str) -> set[str]:
    normalised = _normalised_field_name(value)
    return {token for token in normalised.split("_") if token}


def _normalised_field_name(value: str) -> str:
    return "".join(
        character.lower() if character.isalnum() else "_" for character in value
    )


def _prohibited_field_name(value: str) -> bool:
    normalised = _normalised_field_name(value)
    tokens = _normalised_field_tokens(value)
    return bool(tokens & _PROHIBITED_PROFILE_TOKENS) or any(
        phrase in normalised for phrase in _PROHIBITED_PROFILE_TOKENS if "_" in phrase
    )


def _walk_structured_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk_structured_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_structured_keys(child)


def _reject_prohibited_profile_fields(
    *,
    field_values: Optional[Dict[str, Any]],
    field_definitions: Optional[List[Dict[str, str]]],
    additional: Optional[Dict[str, Any]],
) -> None:
    candidates = list(_walk_structured_keys(field_values or {}))
    candidates.extend(_walk_structured_keys(additional or {}))
    for definition in field_definitions or []:
        if isinstance(definition, BaseModel):
            definition = definition.model_dump()
        for key in ("id", "key", "name", "label", "code"):
            value = definition.get(key)
            if value:
                candidates.append(value)
    if any(_prohibited_field_name(candidate) for candidate in candidates):
        raise ValueError(
            "Sensitive or unrelated private profiling fields are not supported"
        )

def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _ensure_aware_utc(value: datetime | None) -> datetime | None:
    """Return a timezone-aware UTC datetime for database values."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalise_scope_dates(dates: Optional[List[str]]) -> set[str]:
    """Validate and normalise date-scoped publish day ids."""
    if not dates:
        raise HTTPException(
            status_code=400,
            detail="Date-scoped publish requires at least one date.",
        )

    normalised: set[str] = set()
    for raw_date in dates:
        if not isinstance(raw_date, str):
            raise HTTPException(status_code=400, detail="Invalid publish date.")
        value = raw_date.strip()
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid publish date: {raw_date}",
            ) from None
        normalised.add(parsed.isoformat())
    return normalised


def _additional_date(additional_json: str | None) -> str | None:
    """Return the task date stored in additional_json if it is a valid ISO date."""
    if not additional_json:
        return None
    try:
        additional = json.loads(additional_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(additional, dict):
        return None
    raw_date = additional.get("date")
    if not isinstance(raw_date, str):
        return None
    try:
        return datetime.strptime(raw_date, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _published_task_day(
    task: PublishedTask,
    schedule_day_range: dict[str, int],
) -> str:
    """Resolve the published day for an existing task."""
    return working_date_for_datetime(task.start_datetime, schedule_day_range)


def _payload_task_day(task: TaskIn, schedule_day_range: dict[str, int]) -> str:
    """Resolve the published day for an incoming task payload."""
    try:
        return working_date_for_datetime(
            datetime.fromisoformat(task.start),
            schedule_day_range,
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid task start datetime for task {task.id}",
        ) from None


def _insert_person(
    person_in: PersonIn,
    event_id: int,
    db: Session,
) -> None:
    """Insert one published person."""
    db.add(PublishedPerson(
        event_id=event_id,
        external_person_id=person_in.id,
        evidence_subject_id=person_in.evidence_subject_id,
        first_name=person_in.first_name,
        last_name=person_in.last_name,
        email=person_in.email,
    ))


def _upsert_person(person_in: PersonIn, event_id: int, db: Session) -> None:
    """Insert or update one published person without deleting unrelated people."""
    existing = (
        db.query(PublishedPerson)
        .filter(
            PublishedPerson.event_id == event_id,
            PublishedPerson.external_person_id == person_in.id,
        )
        .first()
    )
    if existing is None:
        _insert_person(person_in, event_id, db)
        return
    if person_in.evidence_subject_id and person_in.evidence_subject_id != existing.evidence_subject_id:
        raise HTTPException(status_code=409, detail="A person's evidence identifier is immutable.")
    existing.first_name = person_in.first_name
    existing.last_name = person_in.last_name
    existing.email = person_in.email


def _insert_task(task_in: TaskIn, event_id: int, db: Session) -> None:
    """Insert one published task from a desktop publish payload."""
    attendees_data = [a.model_dump() for a in task_in.attendees]
    field_assignments_data = None
    if task_in.field_assignments:
        field_assignments_data = {
            k: [a.model_dump() for a in v]
            for k, v in task_in.field_assignments.items()
        }

    try:
        start_datetime = datetime.fromisoformat(task_in.start)
        end_datetime = datetime.fromisoformat(task_in.end)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid task datetime for task {task_in.id}",
        ) from None

    db.add(PublishedTask(
        event_id=event_id,
        external_task_id=task_in.id,
        name=task_in.name,
        summary=task_in.summary,
        description=task_in.description,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        location_name=task_in.location_name,
        location_address=task_in.location_address,
        task_type_code=task_in.task_type_code,
        task_type_name=task_in.task_type_name,
        color=task_in.color,
        attendees_json=json.dumps(attendees_data) if attendees_data else None,
        field_assignments_json=json.dumps(field_assignments_data) if field_assignments_data else None,
        field_values_json=json.dumps(task_in.field_values) if task_in.field_values else None,
        field_definitions_json=json.dumps([
            definition.model_dump() for definition in task_in.field_definitions
        ]) if task_in.field_definitions else None,
        additional_json=None,
        sort_order=task_in.sort_order,
    ))


def _authenticate_event(request: Request, db: Session) -> Event:
    """Authenticate via Bearer token matching an event's publish_secret_hash."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    secret = auth_header[7:].strip()
    if not secret:
        raise HTTPException(status_code=401, detail="Empty Bearer token")

    secret_hash = _hash_secret(secret)
    event = (
        db.query(Event)
        .filter(Event.publish_secret_hash == secret_hash)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=401, detail="Invalid publish secret")
    if settings.HA_MODE == "ha":
        pending_protection = db.query(HAProtectionOperation).filter(
            HAProtectionOperation.resource_type == "event",
            HAProtectionOperation.resource_id == str(event.id),
            HAProtectionOperation.operation_type.in_([
                "publisher-secret-create", "publisher-secret-rotation", "publisher-secret-import",
            ]),
            HAProtectionOperation.state.in_(["pending", "indeterminate"]),
        ).first()
        if pending_protection is not None:
            raise HTTPException(
                status_code=423,
                detail={
                    "code": "STANDBY_PROTECTION_PENDING",
                    "operation_id": pending_protection.id,
                    "message": "This publisher credential is still being secured on the standby.",
                },
            )

    # Check secret rotation policy
    from app.core import runtime_settings
    max_age = runtime_settings.get_int("secret_max_age_days", db)
    if max_age > 0 and event.secret_created_at:
        created_at = _ensure_aware_utc(event.secret_created_at)
        age_days = (datetime.now(timezone.utc) - created_at).days
        if age_days > max_age:
            raise HTTPException(
                status_code=401,
                detail="Publish secret has expired - please regenerate in the admin panel",
            )

    return event


def _require_publishing_allowed(event: Event) -> None:
    """Keep the credential usable for deletion sync but block new live data."""

    if event.purge_case_request_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EVENT_PURGE_IN_PROGRESS",
                "message": "Publishing is disabled because the event deletion workflow has started.",
            },
        )


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _processor_key_for_event(
    db: Session, event: Event, *, entity_id: str, requested_key_id: str,
) -> tuple[ProcessorIdentity, EvidenceKey]:
    identity = db.query(ProcessorIdentity).filter(
        ProcessorIdentity.event_evidence_id == event.evidence_id,
        ProcessorIdentity.entity_id == entity_id,
        ProcessorIdentity.status == "active",
    ).first()
    if identity is None:
        raise TrustEvidenceError("the processor identity is not active for this event")
    key = db.query(EvidenceKey).filter(
        EvidenceKey.key_id == requested_key_id,
        EvidenceKey.entity_id == identity.entity_id,
        EvidenceKey.role == "processor",
        EvidenceKey.activated_at.isnot(None),
        EvidenceKey.revoked_at.is_(None),
    ).first()
    if key is None or identity.active_key_id != key.key_id:
        raise TrustEvidenceError("the processor key is not the active key for this event")
    return identity, key


@router.post("/processor-keys/enrolments", status_code=202)
@limiter.limit("10/minute")
def begin_processor_event_enrolment(
    body: ProcessorPublicPackageIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create an event-bound proof challenge from public Desktop material."""

    event = _authenticate_event(request, db)
    try:
        validate_entity("processor", body.entity_id)
        state = initialise(db)
        if state is None:
            raise EvidenceUnavailable("required evidence is unavailable")
        public = canonical_public_key(body.public_key)
        identifier = key_id(public)
        fingerprint = public_key_sha256(public)
        if identifier != body.key_id or fingerprint != body.public_key_sha256:
            raise TrustEvidenceError("the processor public package fingerprint is inconsistent")
        identity = db.query(ProcessorIdentity).filter(
            ProcessorIdentity.instance_id == state.instance_id,
            ProcessorIdentity.entity_id == body.entity_id,
        ).first()
        if identity is not None and identity.event_evidence_id != event.evidence_id:
            raise TrustEvidenceError("this processor identity is immutably assigned to another event")
        existing_key = db.query(EvidenceKey).filter(EvidenceKey.key_id == identifier).first()
        if existing_key is not None and identity is not None and identity.active_key_id == identifier:
            return {"status": "active", "key_id": identifier, "entity_id": body.entity_id, "event_ref": event.evidence_id}
        purpose = "rotate" if body.supersedes_key_id else "register"
        previous = None
        if purpose == "rotate":
            if body.rotation_reason not in ROTATION_REASONS:
                raise TrustEvidenceError("processor rotation requires a bounded reason")
            previous = db.query(EvidenceKey).filter(
                EvidenceKey.key_id == body.supersedes_key_id,
                EvidenceKey.entity_id == body.entity_id,
                EvidenceKey.role == "processor",
                EvidenceKey.revoked_at.is_(None),
            ).first()
            if previous is None or identity is None or identity.active_key_id != previous.key_id:
                raise TrustEvidenceError("the superseded key is not active for this event processor")
        elif body.rotation_reason is not None:
            raise TrustEvidenceError("new processor enrolment cannot include a rotation reason")
        duplicate = db.query(EvidenceKeyRegistrationChallenge).filter(
            EvidenceKeyRegistrationChallenge.key_id == identifier,
            EvidenceKeyRegistrationChallenge.event_evidence_id == event.evidence_id,
            EvidenceKeyRegistrationChallenge.used_at.is_(None),
        ).first()
        if duplicate is not None and _utc(duplicate.expires_at) >= datetime.now(timezone.utc):
            return {"status": "challenge", "challenge": json.loads(duplicate.challenge_json), "challenge_sha256": duplicate.challenge_sha256}
        now = datetime.now(timezone.utc).replace(microsecond=0)
        expires = now + timedelta(minutes=10)
        document = {
            "format": PROCESSOR_EVENT_REGISTRATION_FORMAT,
            "challenge_id": str(uuid.uuid4()),
            "action": purpose,
            "instance_id": state.instance_id,
            "event_ref": event.evidence_id,
            "entity_id": body.entity_id,
            "key_id": identifier,
            "role": "processor",
            "algorithm": "Ed25519",
            "public_key_sha256": fingerprint,
            "supersedes_key_id": previous.key_id if previous else None,
            "reason": body.rotation_reason if previous else None,
            "action_sha256": "",
            "nonce": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
            "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        document["action_sha256"] = processor_event_action_sha256(document)
        validate_processor_event_registration(document)
        rendered = canonical_json(document)
        challenge = EvidenceKeyRegistrationChallenge(
            challenge_id=document["challenge_id"], purpose=purpose,
            instance_id=state.instance_id, entity_id=body.entity_id,
            event_id=event.id, event_evidence_id=event.evidence_id,
            event_display_name=event.name, display_label=body.display_label,
            public_key=public, public_key_sha256=fingerprint, key_id=identifier,
            role="processor", supersedes_key_id=previous.key_id if previous else None,
            rotation_reason=body.rotation_reason if previous else None,
            challenge_json=rendered.decode("utf-8"),
            challenge_sha256=hashlib.sha256(rendered).hexdigest(),
            action_sha256=document["action_sha256"], expires_at=expires,
        )
        db.add(challenge)
        db.commit()
        return {"status": "challenge", "challenge": document, "challenge_sha256": challenge.challenge_sha256}
    except (EvidenceUnavailable, TrustEvidenceError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "PROCESSOR_ENROLMENT_REJECTED", "message": str(exc)}) from exc


@router.post("/processor-keys/enrolments/{challenge_id}/proof", status_code=202)
@limiter.limit("10/minute")
def submit_processor_event_proof(
    challenge_id: str,
    body: ProcessorPossessionProofIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """Verify Desktop possession and leave the assignment pending root approval."""

    event = _authenticate_event(request, db)
    try:
        validate_processor_event_registration(body.challenge)
        challenge = db.query(EvidenceKeyRegistrationChallenge).filter(
            EvidenceKeyRegistrationChallenge.challenge_id == challenge_id,
            EvidenceKeyRegistrationChallenge.event_evidence_id == event.evidence_id,
            EvidenceKeyRegistrationChallenge.used_at.is_(None),
        ).first()
        rendered = canonical_json(body.challenge)
        if (
            challenge is None
            or challenge.challenge_json.encode("utf-8") != rendered
            or challenge.challenge_sha256 != hashlib.sha256(rendered).hexdigest()
            or _utc(challenge.expires_at) < datetime.now(timezone.utc)
        ):
            raise TrustEvidenceError("the processor enrolment challenge is unavailable or changed")
        if challenge.possession_proof_sha256 is not None:
            return {"status": "pending_root_approval", "challenge_id": challenge.challenge_id, "key_id": challenge.key_id}
        challenge.possession_proof_sha256 = verify_signature(body.challenge, body.proof, challenge.public_key)
        if challenge.purpose == "rotate":
            previous = db.query(EvidenceKey).filter(EvidenceKey.key_id == challenge.supersedes_key_id).first()
            if previous is None:
                raise TrustEvidenceError("the superseded processor key is unavailable")
            if challenge.rotation_reason == "routine" and body.previous_proof is None:
                raise TrustEvidenceError("routine rotation requires proof from the old key")
            if body.previous_proof is not None:
                challenge.previous_proof_sha256 = verify_signature(body.challenge, body.previous_proof, previous.public_key)
        db.commit()
        return {"status": "pending_root_approval", "challenge_id": challenge.challenge_id, "key_id": challenge.key_id, "event_ref": event.evidence_id}
    except (TrustEvidenceError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "PROCESSOR_PROOF_REJECTED", "message": str(exc)}) from exc


@router.get("/processor-keys/status")
@limiter.limit("30/minute")
def processor_event_key_status(request: Request, db: Session = Depends(get_db)):
    event = _authenticate_event(request, db)
    identities = db.query(ProcessorIdentity).filter(
        ProcessorIdentity.event_evidence_id == event.evidence_id,
    ).order_by(ProcessorIdentity.created_at).all()
    pending = db.query(EvidenceKeyRegistrationChallenge).filter(
        EvidenceKeyRegistrationChallenge.event_evidence_id == event.evidence_id,
        EvidenceKeyRegistrationChallenge.possession_proof_sha256.isnot(None),
        EvidenceKeyRegistrationChallenge.used_at.is_(None),
    ).all()
    return {
        "event_ref": event.evidence_id,
        "processors": [{
            "entity_id": row.entity_id,
            "display_label": row.display_label,
            "status": row.status,
            "active_key_id": row.active_key_id,
        } for row in identities],
        "pending": [{"challenge_id": row.challenge_id, "entity_id": row.entity_id, "key_id": row.key_id} for row in pending],
    }


@router.post("/processor-policy-acknowledgements", status_code=201)
@limiter.limit("20/minute")
def record_processor_policy_acknowledgement(
    body: SignedDesktopDocumentIn,
    request: Request,
    db: Session = Depends(get_db),
):
    event = _authenticate_event(request, db)
    try:
        document = body.document
        exact_fields = {
            "format", "instance_id", "event_ref", "entity_id", "key_id", "role",
            "algorithm", "public_key_sha256", "policy_version", "policy_sha256",
            "acknowledged_at",
        }
        if set(document) != exact_fields:
            raise TrustEvidenceError("Desktop policy acknowledgement fields are invalid")
        identity, key = _processor_key_for_event(
            db, event, entity_id=str(document.get("entity_id")), requested_key_id=str(document.get("key_id")),
        )
        validate_desktop_evidence_document(
            document, instance_id=key.instance_id, event_ref=event.evidence_id,
            entity_id=identity.entity_id, row_key_id=key.key_id, fingerprint=key.public_key_sha256,
        )
        current = current_policy_identity(db)
        if current is None or document.get("policy_version") != current[0] or document.get("policy_sha256") != current[1]:
            raise TrustEvidenceError("the permitted-data policy changed; review its current version")
        acknowledged_at = datetime.fromisoformat(str(document["acknowledged_at"]).replace("Z", "+00:00"))
        package_json, package_digest, document_digest, signature_digest = (
            signed_desktop_evidence_package(document, body.proof, key.public_key)
        )
        rendered = canonical_json(document)
        existing = db.query(ProcessorPolicyAcknowledgement).filter(
            ProcessorPolicyAcknowledgement.event_evidence_id == event.evidence_id,
            ProcessorPolicyAcknowledgement.entity_id == identity.entity_id,
            ProcessorPolicyAcknowledgement.policy_version == current[0],
            ProcessorPolicyAcknowledgement.policy_sha256 == current[1],
        ).first()
        if existing is not None:
            if existing.evidence_package_sha256 != package_digest:
                raise TrustEvidenceError("a different signed acknowledgement is already recorded")
            return {
                "status": "acknowledged",
                "document_sha256": existing.document_sha256,
                "instance_record_sha256": existing.instance_record_sha256,
                "evidence_package_sha256": existing.evidence_package_sha256,
            }
        row = ProcessorPolicyAcknowledgement(
            instance_id=key.instance_id, event_evidence_id=event.evidence_id,
            entity_id=identity.entity_id, key_id=key.key_id,
            policy_version=current[0], policy_sha256=current[1],
            document_json=rendered.decode("utf-8"), document_sha256=document_digest,
            signature_sha256=signature_digest, acknowledged_at=acknowledged_at,
            evidence_package_json=package_json,
            evidence_package_sha256=package_digest,
        )
        db.add(row)
        db.flush()
        row.instance_record_sha256 = append_record(
            db, workflow_type="desktop_policy", workflow_id=row.acknowledgement_id,
            operation_type="acknowledged", record_type="desktop.policy_acknowledged",
            payload={
                "event_ref": event.evidence_id, "entity_id": identity.entity_id,
                "key_id": key.key_id, "policy_version": current[0],
                "policy_sha256": current[1], "document_sha256": document_digest,
                "signature_sha256": signature_digest, "status": "verified",
                "evidence_package_sha256": package_digest,
                "public_key_sha256": key.public_key_sha256,
            },
        )
        db.commit()
        return {
            "status": "acknowledged",
            "document_sha256": document_digest,
            "instance_record_sha256": row.instance_record_sha256,
            "evidence_package_sha256": package_digest,
        }
    except (EvidenceUnavailable, TrustEvidenceError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "PROCESSOR_POLICY_REJECTED", "message": str(exc)}) from exc


@router.get("/processor-policy-acknowledgements/current")
@limiter.limit("60/minute")
def current_processor_policy_acknowledgement(
    request: Request,
    db: Session = Depends(get_db),
):
    """Return the Server-authoritative acknowledgement for this event and policy."""

    event = _authenticate_event(request, db)
    current = current_policy_identity(db)
    if current is None:
        return {"acknowledged": False, "policy_version": None, "policy_sha256": None}
    row = db.query(ProcessorPolicyAcknowledgement).filter(
        ProcessorPolicyAcknowledgement.event_evidence_id == event.evidence_id,
        ProcessorPolicyAcknowledgement.policy_version == current[0],
        ProcessorPolicyAcknowledgement.policy_sha256 == current[1],
        ProcessorPolicyAcknowledgement.evidence_package_sha256.isnot(None),
    ).order_by(ProcessorPolicyAcknowledgement.id.desc()).first()
    if row is None:
        return {
            "acknowledged": False,
            "policy_version": current[0],
            "policy_sha256": current[1],
        }
    return {
        "acknowledged": True,
        "policy_version": row.policy_version,
        "policy_sha256": row.policy_sha256,
        "entity_id": row.entity_id,
        "key_id": row.key_id,
        "document_sha256": row.document_sha256,
        "instance_record_sha256": row.instance_record_sha256,
        "evidence_package_sha256": row.evidence_package_sha256,
    }


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/publish", response_model=PublishResponse)
@limiter.limit(runtime_limit("masterplan_pushes_per_minute"))
def publish(
    payload: PublishPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    """Receive published masterplan data from the desktop app.

    Full publish replaces the event's published data.
    Date-scoped publish replaces only the requested published days.
    """
    event = _authenticate_event(request, db)
    _require_publishing_allowed(event)
    incoming_schedule_day_range = (
        payload.event.schedule_day_range
        if payload.event and payload.event.schedule_day_range is not None
        else event_schedule_day_range(event.metadata_json)
    )
    schedule_day_range = normalise_schedule_day_range(incoming_schedule_day_range)
    if (
        incoming_schedule_day_range is not None
        and schedule_day_range != incoming_schedule_day_range
    ):
        raise HTTPException(status_code=400, detail="Invalid schedule day range.")
    publish_scope = payload.publish_scope or "full"
    scoped_dates = (
        _normalise_scope_dates(payload.dates)
        if publish_scope == "dates"
        else None
    )

    if scoped_dates is not None:
        for task_in in payload.tasks:
            if _payload_task_day(task_in, schedule_day_range) not in scoped_dates:
                raise HTTPException(
                    status_code=400,
                    detail=f"Task {task_in.id} is outside the requested publish dates.",
                )

    # Update event metadata if provided
    if payload.event:
        if payload.event.name:
            event.name = payload.event.name
        if payload.event.start_date:
            event.start_date = datetime.strptime(payload.event.start_date, "%Y-%m-%d").date()
        if payload.event.end_date:
            new_end_date = datetime.strptime(payload.event.end_date, "%Y-%m-%d").date()
            end_date_changed = event.end_date != new_end_date
            event.end_date = new_end_date
            materialise_event_purge_deadline(
                event,
                db,
                force=end_date_changed,
            )
        if payload.event.day_aliases is not None:
            # Store day_aliases in event metadata_json
            existing_meta = json.loads(event.metadata_json) if event.metadata_json else {}
            existing_meta["day_aliases"] = payload.event.day_aliases
            event.metadata_json = json.dumps(existing_meta)
        if payload.event.schedule_day_range is not None:
            event.metadata_json = merge_schedule_day_range(
                event.metadata_json,
                schedule_day_range,
            )
        event.status = "published"

    # -----------------------------------------------------------------------
    # Capture old state for per-person diff (before delete-and-replace)
    # -----------------------------------------------------------------------
    old_tasks = (
        db.query(PublishedTask)
        .filter(PublishedTask.event_id == event.id)
        .all()
    )
    old_edits_map = {}
    if old_tasks:
        old_task_ids = [t.id for t in old_tasks]
        old_edits = db.query(TaskEdit).filter(TaskEdit.task_id.in_(old_task_ids)).all()
        old_edits_map = {e.task_id: e for e in old_edits}
        # Detach old tasks from session so they survive the delete below
        for t in old_tasks:
            db.expunge(t)
        for e in old_edits:
            db.expunge(e)

    # Delete existing published data + edits for this event/scope.
    existing_tasks_query = (
        db.query(PublishedTask)
        .filter(PublishedTask.event_id == event.id)
    )
    if scoped_dates is None:
        existing_task_ids = [task.id for task in existing_tasks_query.all()]
    else:
        existing_task_ids = [
            task.id
            for task in existing_tasks_query.all()
            if _published_task_day(task, schedule_day_range) in scoped_dates
        ]
    if existing_task_ids:
        edits_cleared = db.query(TaskEdit).filter(
            TaskEdit.task_id.in_(existing_task_ids),
        ).delete(synchronize_session=False)
    else:
        edits_cleared = 0

    if existing_task_ids:
        db.query(PublishedTask).filter(
            PublishedTask.id.in_(existing_task_ids),
        ).delete(synchronize_session=False)

    if scoped_dates is None:
        db.query(PublishedPerson).filter(
            PublishedPerson.event_id == event.id,
        ).delete(synchronize_session=False)

    # Insert persons
    for person_in in payload.persons:
        if scoped_dates is None:
            _insert_person(person_in, event.id, db)
        else:
            _upsert_person(person_in, event.id, db)

    availability_query = db.query(PublishedPersonUnavailability).filter(
        PublishedPersonUnavailability.event_id == event.id,
    )
    if scoped_dates is None:
        availability_query.delete(synchronize_session=False)
    else:
        availability_query.filter(
            PublishedPersonUnavailability.working_date.in_(scoped_dates),
        ).delete(synchronize_session=False)

    valid_person_ids = {person.id for person in payload.persons}
    seen_intervals: set[tuple[int, str, str, str]] = set()
    for interval in payload.unavailabilities:
        try:
            working_date = datetime.strptime(interval.working_date, "%Y-%m-%d").date().isoformat()
            start = datetime.fromisoformat(interval.start)
            end = datetime.fromisoformat(interval.end)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid unavailability interval.") from None
        if interval.person_id not in valid_person_ids:
            raise HTTPException(status_code=400, detail="Unavailable person is not part of this event.")
        if scoped_dates is not None and working_date not in scoped_dates:
            raise HTTPException(status_code=400, detail="Unavailability is outside the requested publish dates.")
        if end <= start:
            raise HTTPException(status_code=400, detail="Unavailability end must be after its start.")
        key = (interval.person_id, working_date, start.isoformat(), end.isoformat())
        if key in seen_intervals:
            continue
        seen_intervals.add(key)
        db.add(PublishedPersonUnavailability(
            event_id=event.id,
            external_person_id=interval.person_id,
            working_date=working_date,
            start_datetime=start.isoformat(),
            end_datetime=end.isoformat(),
        ))

    # Insert tasks
    for task_in in payload.tasks:
        _insert_task(task_in, event.id, db)

    db.flush()

    # Auto-link users to persons by matching email
    _auto_link_users_by_email(event.id, db)

    # -----------------------------------------------------------------------
    # Compute per-person diffs and store change records
    # -----------------------------------------------------------------------
    try:
        from app.core.diff import compute_per_person_diffs, store_schedule_changes
        new_tasks = (
            db.query(PublishedTask)
            .filter(PublishedTask.event_id == event.id)
            .all()
        )
        new_task_ids = [task.id for task in new_tasks]
        new_edits_map = {}
        if new_task_ids:
            new_edits = db.query(TaskEdit).filter(TaskEdit.task_id.in_(new_task_ids)).all()
            new_edits_map = {edit.task_id: edit for edit in new_edits}
        diffs = compute_per_person_diffs(
            old_tasks,
            old_edits_map,
            new_tasks,
            new_edits_map,
        )
        store_schedule_changes(event.id, diffs, db)
    except Exception as exc:
        logger.warning("Schedule diff generation failed (%s)", type(exc).__name__)

    # Snapshot the full published state after applying this publish.
    from app.core.snapshots import create_snapshot
    create_snapshot(event, db, source="Publish Secret")

    db.commit()

    audit(db, user=None, action="publish.data", resource_type="event",
          resource_id=event.id, detail=json.dumps({
              "scope": publish_scope,
              "tasks": len(payload.tasks),
          }), request=request)
    db.commit()

    # Send push notification to all subscribers of this event
    try:
        from app.core.push import send_push_to_event
        send_push_to_event(
            event_id=event.id,
            title="Schedule Updated",
            body=f"{event.name} schedule has been republished.",
            url=f"/calendar?event={event.id}",
            db=db,
            notification_type="schedule",
        )
    except Exception as exc:
        logger.warning("Publish push delivery failed (%s)", type(exc).__name__)

    return PublishResponse(
        status="ok",
        tasks_created=len(payload.tasks),
        persons_created=len(payload.persons),
        edits_cleared=edits_cleared,
    )


def _auto_link_users_by_email(event_id: int, db: Session) -> None:
    """Match users to published persons by email within the same event."""
    users = (
        db.query(User)
        .filter(User.event_id == event_id, User.email.isnot(None), User.email != "")
        .all()
    )
    if not users:
        return

    persons = (
        db.query(PublishedPerson)
        .filter(PublishedPerson.event_id == event_id, PublishedPerson.email.isnot(None))
        .all()
    )
    email_to_person = {p.email.lower(): p.external_person_id for p in persons if p.email}

    for user in users:
        person_id = email_to_person.get(user.email.lower())
        if person_id is not None:
            user.linked_person_id = person_id


# ---------------------------------------------------------------------------
# Ping endpoint
# ---------------------------------------------------------------------------

class PingResponse(BaseModel):
    """Publish credential health-check response."""

    status: str
    event_name: str
    event_id: int
    event_ref: str
    supports_scoped_publish: bool = True
    supports_deletion_work_orders: bool = True


@router.get("/ping", response_model=PingResponse)
@limiter.limit("20/minute")
def ping(
    request: Request,
    db: Session = Depends(get_db),
):
    """Health check for the desktop app. Validates the Bearer token."""
    event = _authenticate_event(request, db)
    return PingResponse(
        status="ok",
        event_name=event.name,
        event_id=event.id,
        event_ref=event.evidence_id,
        supports_scoped_publish=True,
        supports_deletion_work_orders=True,
    )


# ---------------------------------------------------------------------------
# Strict desktop deletion work orders
# ---------------------------------------------------------------------------

class DesktopDeletionCounts(BaseModel):
    """Bounded deletion counters that contain no personal values."""

    model_config = ConfigDict(extra="forbid")

    persons: int = Field(ge=0)
    assignments: int = Field(ge=0)
    capability_links: int = Field(ge=0)
    group_memberships: int = Field(ge=0)
    unavailability_intervals: int = Field(ge=0)
    task_references: int = Field(ge=0)
    optimisation_records: int = Field(ge=0)
    publish_records: int = Field(ge=0)
    cached_records: int = Field(ge=0)
    tracked_exports: int = Field(ge=0)
    integration_references: int = Field(ge=0)


class DesktopDeletionReportIn(BaseModel):
    """Processor-signed Desktop deletion receipt document."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["mp-opt-desktop-deletion-receipt-v2"]
    instance_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    entity_id: str = Field(pattern=r"^prc-[a-z0-9]{8,48}$")
    key_id: str = Field(pattern=r"^ek-[0-9a-f]{16}$")
    role: Literal["processor"]
    algorithm: Literal["Ed25519"]
    public_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    work_order_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    event_ref: str = Field(pattern=r"^[0-9a-f-]{36}$")
    subject_ref: Optional[str] = Field(None, pattern=r"^[0-9a-f-]{36}$")
    operation: Literal["delete_subject", "delete_event"]
    outcome: Literal["deleted"]
    deleted_counts: DesktopDeletionCounts
    outstanding_actions: List[
        Literal["untracked_external_export", "external_integration_copy"]
    ] = Field(default_factory=list)
    completed_at: str = Field(max_length=40)


class DesktopWorkOrderClaimIn(BaseModel):
    """Processor-signed request to claim its own event work order."""

    model_config = ConfigDict(extra="forbid")
    format: Literal["mp-opt-desktop-work-order-claim-v1"]
    instance_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    event_ref: str = Field(pattern=r"^[0-9a-f-]{36}$")
    entity_id: str = Field(pattern=r"^prc-[a-z0-9]{8,48}$")
    key_id: str = Field(pattern=r"^ek-[0-9a-f]{16}$")
    role: Literal["processor"]
    algorithm: Literal["Ed25519"]
    public_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    work_order_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    requested_at: str = Field(max_length=40)


class DesktopCopyResolutionIn(BaseModel):
    """Processor statement about Desktop-local backups and exports."""

    model_config = ConfigDict(extra="forbid")
    format: Literal["mp-opt-desktop-copy-resolution-v1"]
    instance_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    event_ref: str = Field(pattern=r"^[0-9a-f-]{36}$")
    entity_id: str = Field(pattern=r"^prc-[a-z0-9]{8,48}$")
    key_id: str = Field(pattern=r"^ek-[0-9a-f]{16}$")
    role: Literal["processor"]
    algorithm: Literal["Ed25519"]
    public_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    work_order_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    disposition: Literal["no_known_local_copies", "relevant_local_copies_deleted"]
    software_inventory_complete: bool
    operator_confirmation: Literal["LOCAL COPIES RESOLVED"]
    completed_at: str = Field(max_length=40)


def _desktop_work_order_detail(work_order: DesktopDeletionWorkOrder) -> dict:
    """Return the pseudonymous fields required by the paired desktop."""

    return {
        "version": 1,
        "work_order_id": work_order.work_order_id,
        "event_ref": work_order.event_ref,
        "subject_ref": work_order.subject_ref,
        "operation": work_order.operation,
        "processor_entity_id": work_order.processor_entity_id,
        "processor_key_id": work_order.processor_key_id,
        "state": work_order.state,
        "created_at": work_order.created_at,
        "claimed_at": work_order.claimed_at,
        "claim_expires_at": work_order.claim_expires_at,
        "reported_at": work_order.reported_at,
        "report_sha256": work_order.report_sha256,
        "report_signature_sha256": work_order.report_signature_sha256,
        "copy_resolution_sha256": work_order.copy_resolution_sha256,
    }


@router.get("/deletion-work-orders")
@limiter.limit("20/minute")
def list_desktop_deletion_work_orders(
    request: Request,
    db: Session = Depends(get_db),
):
    """List current deletion work orders for the authenticated event."""

    event = _authenticate_event(request, db)
    work_orders = db.query(DesktopDeletionWorkOrder).filter(
        DesktopDeletionWorkOrder.event_id == event.id,
        DesktopDeletionWorkOrder.state.in_({"open", "claimed", "report_received"}),
    ).order_by(DesktopDeletionWorkOrder.id).all()
    return [_desktop_work_order_detail(work_order) for work_order in work_orders]


@router.post("/deletion-work-orders/{work_order_id}/claim")
@limiter.limit("10/minute")
def claim_desktop_deletion_work_order(
    work_order_id: str,
    body: SignedDesktopDocumentIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """Claim one work order and reveal a short-lived report capability once."""

    event = _authenticate_event(request, db)
    work_order = db.query(DesktopDeletionWorkOrder).filter(
        DesktopDeletionWorkOrder.work_order_id == work_order_id,
        DesktopDeletionWorkOrder.event_id == event.id,
    ).first()
    if work_order is None:
        raise HTTPException(status_code=404, detail="Deletion work order not found")
    try:
        document = DesktopWorkOrderClaimIn.model_validate(body.document).model_dump(mode="json")
        identity, key = _processor_key_for_event(
            db, event, entity_id=document["entity_id"], requested_key_id=document["key_id"],
        )
        if document["work_order_id"] != work_order.work_order_id or work_order.processor_entity_id != identity.entity_id:
            raise TrustEvidenceError("the work-order claim belongs to another processor assignment")
        validate_desktop_evidence_document(
            document, instance_id=key.instance_id, event_ref=event.evidence_id,
            entity_id=identity.entity_id, row_key_id=key.key_id, fingerprint=key.public_key_sha256,
        )
        requested_at = datetime.fromisoformat(document["requested_at"].replace("Z", "+00:00"))
        if requested_at.tzinfo is None or abs((datetime.now(timezone.utc) - requested_at.astimezone(timezone.utc)).total_seconds()) > 300:
            raise TrustEvidenceError("the work-order claim time is outside the allowed window")
        verify_signature(document, body.proof, key.public_key, namespace=DESKTOP_EVIDENCE_NAMESPACE)
        capability = claim_work_order(work_order)
    except (TrustEvidenceError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return {
        **_desktop_work_order_detail(work_order),
        "claim_capability": capability,
    }


@router.post("/deletion-work-orders/{work_order_id}/report")
@limiter.limit("20/minute")
def report_desktop_deletion_work_order(
    work_order_id: str,
    body: SignedDesktopDocumentIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """Record an idempotent deletion report from the authenticated desktop."""

    event = _authenticate_event(request, db)
    work_order = db.query(DesktopDeletionWorkOrder).filter(
        DesktopDeletionWorkOrder.work_order_id == work_order_id,
        DesktopDeletionWorkOrder.event_id == event.id,
    ).first()
    if work_order is None:
        raise HTTPException(status_code=404, detail="Deletion work order not found")
    case = db.query(DeletionCase).filter(
        DeletionCase.id == work_order.case_id,
    ).first()
    if case is None:
        raise HTTPException(status_code=409, detail="Deletion case no longer exists")
    capability = request.headers.get("x-deletion-claim", "")
    try:
        document = DesktopDeletionReportIn.model_validate(body.document).model_dump(mode="json")
        if document["work_order_id"] != work_order.work_order_id:
            raise TrustEvidenceError("the Desktop deletion receipt targets another work order")
        identity, key = _processor_key_for_event(
            db, event, entity_id=document["entity_id"], requested_key_id=document["key_id"],
        )
        if work_order.processor_entity_id != identity.entity_id:
            raise TrustEvidenceError("the Desktop deletion receipt belongs to another processor assignment")
        validate_desktop_evidence_document(
            document, instance_id=key.instance_id, event_ref=event.evidence_id,
            entity_id=identity.entity_id, row_key_id=key.key_id, fingerprint=key.public_key_sha256,
        )
        package_json, package_digest, _, signature_digest = (
            signed_desktop_evidence_package(document, body.proof, key.public_key)
        )
        digest = apply_desktop_report(
            db,
            case,
            work_order,
            claim_capability=capability,
            report=document,
            signature_sha256=signature_digest,
            evidence_package_json=package_json,
            evidence_package_sha256=package_digest,
            completed_key_id=key.key_id,
            completed_public_key_sha256=key.public_key_sha256,
        )
    except (TrustEvidenceError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return {
        "status": "recorded",
        "work_order_id": work_order.work_order_id,
        "report_sha256": digest,
        "case_state": case.state,
    }


@router.post("/deletion-work-orders/{work_order_id}/copy-resolution")
@limiter.limit("20/minute")
def report_desktop_copy_resolution(
    work_order_id: str,
    body: SignedDesktopDocumentIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """Verify the processor's Desktop-local backup/export disposition."""

    event = _authenticate_event(request, db)
    work_order = db.query(DesktopDeletionWorkOrder).filter(
        DesktopDeletionWorkOrder.work_order_id == work_order_id,
        DesktopDeletionWorkOrder.event_id == event.id,
    ).first()
    if work_order is None:
        raise HTTPException(status_code=404, detail="Deletion work order not found")
    case = db.query(DeletionCase).filter(DeletionCase.id == work_order.case_id).first()
    if case is None:
        raise HTTPException(status_code=409, detail="Deletion case no longer exists")
    try:
        document = DesktopCopyResolutionIn.model_validate(body.document).model_dump(mode="json")
        if document["work_order_id"] != work_order.work_order_id:
            raise TrustEvidenceError("the local-copy resolution targets another work order")
        identity, key = _processor_key_for_event(
            db, event, entity_id=document["entity_id"], requested_key_id=document["key_id"],
        )
        if work_order.processor_entity_id != identity.entity_id:
            raise TrustEvidenceError("the local-copy resolution belongs to another processor assignment")
        validate_desktop_evidence_document(
            document, instance_id=key.instance_id, event_ref=event.evidence_id,
            entity_id=identity.entity_id, row_key_id=key.key_id, fingerprint=key.public_key_sha256,
        )
        package_json, package_digest, _, signature_digest = (
            signed_desktop_evidence_package(document, body.proof, key.public_key)
        )
        digest = apply_desktop_copy_resolution(
            db, case, work_order, document=document,
            signature_sha256=signature_digest, completed_key_id=key.key_id,
            completed_public_key_sha256=key.public_key_sha256,
            evidence_package_json=package_json,
            evidence_package_sha256=package_digest,
        )
        db.commit()
        return {"status": "recorded", "work_order_id": work_order.work_order_id, "copy_resolution_sha256": digest, "case_state": case.state}
    except (TrustEvidenceError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
