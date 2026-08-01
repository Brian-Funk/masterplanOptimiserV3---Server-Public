"""Typed controller and processor public-key trust primitives.

Root authority is WebAuthn and the instance key signs ledger records. This
module handles only external controller and Desktop processor signatures.
Their private keys are never accepted by the Server.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


REGISTRATION_FORMAT = "mp-opt-trust-key-registration-v1"
DECLARATION_FORMAT = "mp-opt-controller-trust-declaration-v1"
PROCESSOR_STATEMENT_FORMAT = "mp-opt-processor-statement-v1"
SIGNATURE_FORMAT = "mp-opt-ed25519-signature-v1"
TRUST_NAMESPACE = "mp-opt-role-trust-v1"
TRUST_ROLES = frozenset({"controller", "processor"})
ROTATION_REASONS = frozenset({"routine", "lost", "compromised"})
REVOCATION_REASONS = frozenset({"retired", "lost", "compromised", "role_changed"})
KEY_ID_RE = re.compile(r"^ek-[0-9a-f]{16}$")
ENTITY_ID_RE = {
    "controller": re.compile(r"^ctl-[a-z0-9]{8,48}$"),
    "processor": re.compile(r"^prc-[a-z0-9]{8,48}$"),
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
PRIVATE_MARKERS = tuple(
    f"-----BEGIN {label}-----"
    for label in ("OPENSSH PRIVATE KEY", "PRIVATE KEY", "ENCRYPTED PRIVATE KEY")
)


class TrustEvidenceError(ValueError):
    """Raised when role-separated trust evidence is ambiguous or invalid."""


def _reject_values(value: Any) -> None:
    if isinstance(value, float):
        raise TrustEvidenceError("floating-point values are forbidden")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        if len(value) > 4096 or any(ord(character) < 0x20 for character in value):
            raise TrustEvidenceError("trust evidence contains an unsafe string")
        if any(marker in value for marker in PRIVATE_MARKERS):
            raise TrustEvidenceError("private-key material is forbidden")
        return
    if isinstance(value, list):
        if len(value) > 64:
            raise TrustEvidenceError("trust evidence array is too large")
        for item in value: _reject_values(item)
        return
    if isinstance(value, dict):
        if len(value) > 32 or any(not isinstance(key, str) for key in value):
            raise TrustEvidenceError("trust evidence object is invalid")
        for key, item in value.items():
            _reject_values(key); _reject_values(item)
        return
    raise TrustEvidenceError("trust evidence contains an unsupported value")


def canonical_json(value: dict[str, Any]) -> bytes:
    if not isinstance(value, dict):
        raise TrustEvidenceError("trust evidence must be a JSON object")
    _reject_values(value)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(raw) > 64 * 1024:
        raise TrustEvidenceError("trust evidence exceeds 64 KiB")
    return raw


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not TIMESTAMP_RE.fullmatch(value):
        raise TrustEvidenceError(f"{field} must be a canonical UTC timestamp")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def canonical_public_key(value: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 2048:
        raise TrustEvidenceError("the public key is invalid")
    if any(marker in value for marker in PRIVATE_MARKERS):
        raise TrustEvidenceError("private-key material is forbidden")
    try:
        key = serialization.load_ssh_public_key(value.strip().encode("ascii"))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise TrustEvidenceError("the public key is not valid OpenSSH data") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise TrustEvidenceError("trust keys must use Ed25519")
    return key.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode("ascii")


def key_id(public_key: str) -> str:
    return "ek-" + hashlib.sha256(canonical_public_key(public_key).encode("ascii")).hexdigest()[:16]


def public_key_sha256(public_key: str) -> str:
    return hashlib.sha256(canonical_public_key(public_key).encode("ascii")).hexdigest()


def _uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TrustEvidenceError(f"{field} must be a UUID")
    try: parsed = uuid.UUID(value)
    except ValueError as exc: raise TrustEvidenceError(f"{field} must be a UUID") from exc
    if str(parsed) != value: raise TrustEvidenceError(f"{field} must use canonical UUID form")
    return value


def validate_entity(role: str, entity_id: Any) -> str:
    if role not in TRUST_ROLES or not isinstance(entity_id, str) or not ENTITY_ID_RE[role].fullmatch(entity_id):
        raise TrustEvidenceError("the entity identity does not match the trust role")
    return entity_id


def action_payload(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "mp-opt-trust-action-v1",
        "action": document.get("action"),
        "instance_id": document.get("instance_id"),
        "entity_id": document.get("entity_id"),
        "key_id": document.get("key_id"),
        "role": document.get("role"),
        "algorithm": document.get("algorithm"),
        "public_key_sha256": document.get("public_key_sha256"),
        "supersedes_key_id": document.get("supersedes_key_id"),
        "reason": document.get("reason"),
    }


def action_sha256(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(action_payload(document))).hexdigest()


def validate_registration_document(document: dict[str, Any]) -> None:
    fields = {
        "format", "challenge_id", "action", "instance_id", "entity_id", "key_id",
        "role", "algorithm", "public_key_sha256", "supersedes_key_id", "reason",
        "action_sha256", "nonce", "created_at", "expires_at",
    }
    if not isinstance(document, dict) or set(document) != fields or document.get("format") != REGISTRATION_FORMAT:
        raise TrustEvidenceError("registration challenge fields are invalid")
    _uuid(document["challenge_id"], "challenge_id"); _uuid(document["instance_id"], "instance_id")
    validate_entity(document["role"], document["entity_id"])
    if document["action"] not in {"register", "rotate"} or document["algorithm"] != "Ed25519":
        raise TrustEvidenceError("registration action or algorithm is invalid")
    if not isinstance(document["key_id"], str) or not KEY_ID_RE.fullmatch(document["key_id"]):
        raise TrustEvidenceError("registration key ID is invalid")
    if not isinstance(document["public_key_sha256"], str) or not SHA256_RE.fullmatch(document["public_key_sha256"]):
        raise TrustEvidenceError("registration fingerprint is invalid")
    if document["action_sha256"] != action_sha256(document):
        raise TrustEvidenceError("registration exact action digest is invalid")
    if document["action"] == "register":
        if document["supersedes_key_id"] is not None or document["reason"] is not None:
            raise TrustEvidenceError("new registration cannot contain rotation metadata")
    elif (
        not isinstance(document["supersedes_key_id"], str)
        or not KEY_ID_RE.fullmatch(document["supersedes_key_id"])
        or document["reason"] not in ROTATION_REASONS
    ):
        raise TrustEvidenceError("rotation metadata is invalid")
    try: nonce = base64.b64decode(document["nonce"], validate=True)
    except (ValueError, TypeError) as exc: raise TrustEvidenceError("registration nonce is invalid") from exc
    if len(nonce) != 32: raise TrustEvidenceError("registration nonce must contain 32 bytes")
    created = parse_timestamp(document["created_at"], "created_at")
    expires = parse_timestamp(document["expires_at"], "expires_at")
    if expires <= created or expires > created + timedelta(minutes=15):
        raise TrustEvidenceError("registration challenge lifetime is invalid")
    canonical_json(document)


def signature_envelope(*, key_identifier: str, signature: bytes) -> dict[str, str]:
    if not KEY_ID_RE.fullmatch(key_identifier) or len(signature) != 64:
        raise TrustEvidenceError("Ed25519 signature material is invalid")
    return {"format": SIGNATURE_FORMAT, "key_id": key_identifier, "namespace": TRUST_NAMESPACE, "signature": base64.b64encode(signature).decode("ascii")}


def signing_bytes(document: dict[str, Any]) -> bytes:
    return TRUST_NAMESPACE.encode("ascii") + b"\0" + canonical_json(document)


def verify_signature(document: dict[str, Any], envelope: dict[str, Any], public_key: str) -> str:
    if not isinstance(envelope, dict) or set(envelope) != {"format", "key_id", "namespace", "signature"}:
        raise TrustEvidenceError("signature envelope is invalid")
    canonical = canonical_public_key(public_key)
    if envelope["format"] != SIGNATURE_FORMAT or envelope["namespace"] != TRUST_NAMESPACE or envelope["key_id"] != key_id(canonical):
        raise TrustEvidenceError("signature identity is invalid")
    try: signature = base64.b64decode(envelope["signature"], validate=True)
    except (ValueError, TypeError) as exc: raise TrustEvidenceError("signature encoding is invalid") from exc
    if len(signature) != 64: raise TrustEvidenceError("signature length is invalid")
    key = serialization.load_ssh_public_key(canonical.encode("ascii"))
    assert isinstance(key, Ed25519PublicKey)
    try: key.verify(signature, signing_bytes(document))
    except Exception as exc: raise TrustEvidenceError("signature verification failed") from exc
    return hashlib.sha256(canonical_json(envelope)).hexdigest()


def validate_role_statement(document: dict[str, Any], *, role: str, entity_id: str, instance_id: str, row_key_id: str, fingerprint: str) -> None:
    expected_format = DECLARATION_FORMAT if role == "controller" else PROCESSOR_STATEMENT_FORMAT
    fields = {"format", "instance_id", "entity_id", "key_id", "role", "algorithm", "public_key_sha256", "statement_type", "statement_sha256", "created_at"}
    if set(document) != fields or document.get("format") != expected_format:
        raise TrustEvidenceError("role statement fields are invalid")
    if document.get("instance_id") != instance_id or document.get("entity_id") != entity_id or document.get("key_id") != row_key_id:
        raise TrustEvidenceError("role statement targets another instance, entity, or key")
    if document.get("role") != role or document.get("algorithm") != "Ed25519" or document.get("public_key_sha256") != fingerprint:
        raise TrustEvidenceError("role statement has the wrong trust role or fingerprint")
    if role == "controller" and document.get("statement_type") != "initial_trust_declaration":
        raise TrustEvidenceError("controller key may sign only controller trust declarations here")
    if role == "processor" and document.get("statement_type") not in {"publication", "conversion", "erasure", "receipt"}:
        raise TrustEvidenceError("processor statement type is unsupported")
    if not isinstance(document.get("statement_sha256"), str) or not SHA256_RE.fullmatch(document["statement_sha256"]):
        raise TrustEvidenceError("exact statement digest is invalid")
    if parse_timestamp(document.get("created_at"), "created_at") > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise TrustEvidenceError("role statement time is too far in the future")
    canonical_json(document)


# Compatibility exception names are intentionally absent: callers must use
# the typed trust vocabulary rather than treating all identities as operators.
