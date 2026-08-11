"""Activation-token validation for passkey registration."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Literal, Optional

from app.core.activation import INITIAL_SETUP, validate_activation_token
from app.core.activation_consent import ActivationConsentError, resolve_activation_consent
from app.core.rate_limit import client_ip_rate_key, limiter, runtime_limit
from app.db.database import get_db
from app.models.user import User

router = APIRouter()


class ActivationValidateResponse(BaseModel):
    """Response returned when an activation token is checked."""

    valid: bool
    username: Optional[str] = None
    display_name: Optional[str] = None
    purpose: Optional[
        Literal["initial_setup", "additional_passkey", "credential_reset"]
    ] = None
    logo_color_1: Optional[str] = None
    logo_color_2: Optional[str] = None
    processing_consent: Optional[dict] = None


class ActivationTokenRequest(BaseModel):
    """Activation token submitted without exposing it in the request URL."""

    token: str = Field(..., min_length=20, max_length=256)


@router.post("/validate", response_model=ActivationValidateResponse)
@limiter.limit(
    runtime_limit("passkey_requests_per_minute"),
    key_func=client_ip_rate_key,
)
def validate_token(
    body: ActivationTokenRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Check whether an activation token is valid."""
    link = validate_activation_token(body.token, db)
    if link is None:
        return ActivationValidateResponse(valid=False)

    user = db.query(User).filter(User.id == link.user_id).first()
    if not user or not user.is_active:
        return ActivationValidateResponse(valid=False)

    processing_consent = None
    if link.purpose == INITIAL_SETUP:
        try:
            processing_consent = resolve_activation_consent(user, db).public_payload()
        except ActivationConsentError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": exc.safe_message},
            ) from exc

    return ActivationValidateResponse(
        valid=True,
        username=user.username,
        display_name=user.display_name,
        purpose=link.purpose,
        processing_consent=processing_consent,
    )
