"""Canonical identifiers used to bind operational records to evidence."""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import AfterValidator


def validate_canonical_evidence_identity(value: str) -> str:
    """Accept canonical lower-case RFC 4122 UUIDv4 and UUIDv5 identities."""

    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Evidence identity must be a UUID") from exc

    if str(parsed) != value:
        raise ValueError("Evidence identity must use canonical lower-case UUID form")
    if parsed.int == 0:
        raise ValueError("Evidence identity must not be the nil UUID")
    if parsed.variant != uuid.RFC_4122:
        raise ValueError("Evidence identity must use the RFC 4122 variant")
    if parsed.version not in {4, 5}:
        raise ValueError("Evidence identity must be a UUIDv4 or UUIDv5")
    return value


CanonicalEvidenceIdentity = Annotated[
    str,
    AfterValidator(validate_canonical_evidence_identity),
]
