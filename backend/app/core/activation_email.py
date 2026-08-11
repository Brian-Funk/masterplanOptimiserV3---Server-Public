"""Secure SMTP delivery and local QR rendering for activation links."""

from __future__ import annotations

import html
import io
import logging
import smtplib
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

import qrcode
from email_validator import EmailNotValidError, validate_email
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from sqlalchemy.orm import Session

from app.core.activation_mail_governance import ActivationMailGovernance
from app.core.config import settings

logger = logging.getLogger("activation.email")

DEFAULT_MAIL_BRAND_NAME = "Masterplan Access"


@dataclass(frozen=True)
class _EmailPresentation:
    """Purpose-specific copy and semantic styling for an access email."""

    subject: str
    label: str
    headline: str
    intro: str
    button_label: str
    outcome_note: str
    notice_label: str
    notice_colour: str
    qr_alt: str


class ActivationMailError(Exception):
    """A sanitised SMTP failure suitable for recording and displaying."""

    def __init__(self, code: str, message: str, *, unknown: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.unknown = unknown


def recover_stale_deliveries(
    db: Session,
    *,
    user_id: int | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> int:
    """Invalidate unusable links left by interrupted SMTP attempts.

    A pending email link is never accepted by token validation. Attempts older
    than the SMTP timeout grace period can therefore be recorded as unknown
    safely before an administrator retries them. ``force`` is reserved for
    single-instance startup, where no previous send can still be running.
    """

    from app.models.user import ActivationEmailDelivery, ActivationLink

    grace_seconds = max(settings.SMTP_TIMEOUT_SECONDS * 4, 120)
    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(seconds=grace_seconds)
    query = db.query(ActivationEmailDelivery).filter(
        ActivationEmailDelivery.status == "sending",
    )
    if not force:
        query = query.filter(ActivationEmailDelivery.started_at < cutoff)
    if user_id is not None:
        query = query.filter(ActivationEmailDelivery.user_id == user_id)
    deliveries = query.with_for_update().all()
    completed_at = current_time
    for delivery in deliveries:
        delivery.status = "unknown"
        delivery.error_code = "delivery_interrupted"
        delivery.error_message = (
            "Delivery was interrupted and could not be confirmed. "
            "The activation link was invalidated; send a fresh email."
        )
        delivery.completed_at = completed_at
        if delivery.activation_link_id is not None:
            link = db.query(ActivationLink).filter(
                ActivationLink.id == delivery.activation_link_id,
            ).first()
            if link is not None:
                link.delivery_pending = False
                if link.used_at is None:
                    link.invalidated_at = completed_at
    return len(deliveries)


def mail_is_configured() -> bool:
    """Return whether every required SMTP setting is available."""

    return all(
        (
            settings.SMTP_HOST,
            settings.SMTP_USERNAME,
            settings.SMTP_TOKEN,
            settings.SMTP_FROM_EMAIL,
        )
    )


def normalise_recipient(value: str | None) -> str:
    """Validate and normalise an email address without external DNS checks."""

    if not value:
        raise ActivationMailError("missing_email", "No email address is available.")
    try:
        return validate_email(value, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ActivationMailError(
            "invalid_email",
            "The email address is not valid. Update it before sending.",
        ) from exc


def safe_mail_settings() -> dict[str, str | bool | int | None]:
    """Return non-secret mail configuration for administration screens."""

    return {
        "configured": mail_is_configured(),
        "from_email": settings.SMTP_FROM_EMAIL or None,
        "from_name": _mail_brand_name() if mail_is_configured() else None,
        "security": settings.SMTP_SECURITY if mail_is_configured() else None,
        "max_batch_size": 50,
    }


def activation_url(raw_token: str) -> str:
    """Build a trusted absolute activation URL using the WebAuthn origin."""

    origin = settings.WEBAUTHN_ORIGIN.rstrip("/")
    return f"{origin}/activate#token={raw_token}"


def _mail_brand_name() -> str:
    """Return the configured email identity with a stable non-empty fallback."""

    return settings.SMTP_FROM_NAME.strip() or DEFAULT_MAIL_BRAND_NAME


def _logo_path() -> Path:
    """Locate the packaged logo, falling back to the source-tree web asset."""

    packaged = Path(__file__).resolve().parent.parent / "assets" / "logo_normal.png"
    source_tree = (
        Path(__file__).resolve().parents[3]
        / "web"
        / "public"
        / "logo_normal.png"
    )
    for candidate in (packaged, source_tree):
        if candidate.is_file():
            return candidate
    raise RuntimeError("The activation email logo asset is unavailable.")


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load the deterministic font installed in development and production."""

    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu") / filename,
        Path("/usr/local/share/fonts") / filename,
    )
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    try:
        return ImageFont.truetype(filename, size=size)
    except OSError as exc:
        raise RuntimeError("The activation QR font is unavailable.") from exc


def _fit_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> str:
    """Ellipsise one line so participant names remain inside the card."""

    text = value.strip() or "Participant"
    if draw.textlength(text, font=font) <= max_width:
        return text
    suffix = "..."
    while text and draw.textlength(text + suffix, font=font) > max_width:
        text = text[:-1]
    return (text.rstrip() or "Participant") + suffix


def _wrap_unbroken_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """Wrap a URL without inserting characters into its displayed value."""

    lines: list[str] = []
    current = ""
    for character in value:
        candidate = current + character
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = character
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _wrap_words(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """Wrap guidance copy at word boundaries within the QR card."""

    lines: list[str] = []
    current = ""
    for word in value.split():
        candidate = word if not current else f"{current} {word}"
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _purpose_presentation(purpose: str, brand: str) -> _EmailPresentation:
    """Return consistent copy and semantic colour for one activation purpose."""

    if purpose == "credential_reset":
        return _EmailPresentation(
            subject=f"Reset your {brand} passkeys",
            label="Passkey reset",
            headline="Reset your passkeys",
            intro="Use this secure link to register a replacement passkey.",
            button_label="Reset passkeys",
            outcome_note=(
                "Previous passkeys and signed-in sessions are revoked only after "
                "the replacement registration succeeds."
            ),
            notice_label="Important",
            notice_colour="#f59e0b",
            qr_alt="QR code for resetting your passkeys",
        )
    if purpose == "additional_passkey":
        return _EmailPresentation(
            subject=f"Add another {brand} passkey",
            label="Additional passkey",
            headline="Add a passkey to your account",
            intro="Use this secure link on the device that should hold the new passkey.",
            button_label="Add another passkey",
            outcome_note=(
                "Your existing passkeys and signed-in sessions will remain valid."
            ),
            notice_label="Existing access remains",
            notice_colour="#10b981",
            qr_alt="QR code for adding another passkey",
        )
    return _EmailPresentation(
        subject=f"Activate your {brand} account",
        label="Account activation",
        headline="Set up your secure access",
        intro="Use this secure link to register your first passkey and activate your account.",
        button_label="Activate account",
        outcome_note="The one-time link creates the first passkey for your account.",
        notice_label="Keep this private",
        notice_colour="#3b82f6",
        qr_alt="QR code for activating your account",
    )


def render_activation_qr_png(
    url: str,
    display_name: str,
    purpose: str,
) -> bytes:
    """Render the canonical purpose-aware dark access QR card as a PNG."""

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=0,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="#000000", back_color="#ffffff").convert("RGB")

    scale = 2
    width = 460
    card_width = 384
    card_x = (width - card_width) // 2
    card_top = 48
    card_padding = 32
    centre_x = width // 2
    text_width = card_width - (card_padding * 2)
    qr_size = 240
    qr_padding = 16
    qr_wrap_size = qr_size + (qr_padding * 2)

    title = {
        "credential_reset": "Scan to Reset Passkeys",
        "additional_passkey": "Scan to Add a Passkey",
    }.get(purpose, "Scan to Activate")
    instruction = {
        "credential_reset": (
            "Scan to register a replacement. Previous access changes only after "
            "registration succeeds."
        ),
        "additional_passkey": (
            "Scan with the device that should hold the additional passkey. "
            "Existing access remains valid."
        ),
    }.get(purpose, "Scan this QR code with your phone to register your passkey.")

    measurement = Image.new("RGB", (1, 1))
    measure_draw = ImageDraw.Draw(measurement)
    title_font = _font(20 * scale, bold=True)
    name_font = _font(18 * scale)
    url_font = _font(12 * scale)
    instruction_font = _font(14 * scale)
    safe_name = _fit_text(measure_draw, display_name, name_font, text_width * scale)
    url_lines = _wrap_unbroken_text(measure_draw, url, url_font, text_width * scale)
    instruction_lines = _wrap_words(
        measure_draw,
        instruction,
        instruction_font,
        text_width * scale,
    )

    y = card_top + card_padding
    logo_y = y
    y += 40
    y += 16
    title_y = y
    y += 28
    y += 4
    name_y = y
    y += 25
    y += 24
    qr_wrap_y = y
    y += qr_wrap_size
    y += 16
    url_y = y
    y += len(url_lines) * 19
    y += 16
    instruction_y = y
    y += len(instruction_lines) * 20
    y += card_padding
    card_height = y - card_top
    height = y + 48

    canvas = Image.new(
        "RGBA",
        (width * scale, height * scale),
        "#22252a",
    )
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (
            card_x * scale,
            (card_top + 10) * scale,
            (card_x + card_width) * scale,
            (card_top + card_height + 10) * scale,
        ),
        radius=16 * scale,
        fill=(0, 0, 0, 150),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(15 * scale))
    canvas = Image.alpha_composite(canvas, shadow)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (
            card_x * scale,
            card_top * scale,
            (card_x + card_width) * scale,
            (card_top + card_height) * scale,
        ),
        radius=16 * scale,
        fill="#282c34",
    )

    with Image.open(_logo_path()) as source_logo:
        logo = source_logo.convert("RGBA")
        logo = logo.resize((40 * scale, 40 * scale), Image.Resampling.LANCZOS)
    canvas.alpha_composite(
        logo,
        ((centre_x - 20) * scale, logo_y * scale),
    )

    draw.text(
        (centre_x * scale, title_y * scale),
        title,
        fill="#f3f4f6",
        font=title_font,
        anchor="mt",
    )
    draw.text(
        (centre_x * scale, name_y * scale),
        safe_name,
        fill="#d1d5db",
        font=name_font,
        anchor="mt",
    )

    qr_wrap_x = centre_x - (qr_wrap_size // 2)
    draw.rounded_rectangle(
        (
            qr_wrap_x * scale,
            qr_wrap_y * scale,
            (qr_wrap_x + qr_wrap_size) * scale,
            (qr_wrap_y + qr_wrap_size) * scale,
        ),
        radius=12 * scale,
        fill="#ffffff",
    )
    qr_image = qr_image.resize(
        (qr_size * scale, qr_size * scale),
        Image.Resampling.NEAREST,
    )
    canvas.paste(
        qr_image,
        ((qr_wrap_x + qr_padding) * scale, (qr_wrap_y + qr_padding) * scale),
    )

    for index, line in enumerate(url_lines):
        draw.text(
            (centre_x * scale, (url_y + index * 19) * scale),
            line,
            fill="#6b7280",
            font=url_font,
            anchor="mt",
        )
    for index, line in enumerate(instruction_lines):
        draw.text(
            (centre_x * scale, (instruction_y + index * 20) * scale),
            line,
            fill="#9ca3af",
            font=instruction_font,
            anchor="mt",
        )

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def _base_message(
    recipient: str,
    subject: str,
    *,
    sender_name: str,
) -> tuple[EmailMessage, str]:
    """Create a message with validated sender and privacy-safe standard headers."""

    sender = validate_email(
        settings.SMTP_FROM_EMAIL,
        check_deliverability=False,
    ).normalized
    sender_domain = sender.rsplit("@", 1)[-1]
    message_id = make_msgid(domain=sender_domain)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = Address(
        display_name=sender_name,
        addr_spec=sender,
    )
    message["To"] = recipient
    if settings.SMTP_REPLY_TO:
        message["Reply-To"] = normalise_recipient(settings.SMTP_REPLY_TO)
    message["Message-ID"] = message_id
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message["Auto-Submitted"] = "auto-generated"
    return message, message_id


def _email_html(
    *,
    brand: str,
    logo_cid: str,
    preheader: str,
    label: str,
    headline: str,
    greeting: str,
    intro: str,
    event_name: str | None = None,
    expiry_label: str | None = None,
    action_url: str | None = None,
    button_label: str | None = None,
    notice_label: str,
    outcome_note: str,
    notice_colour: str,
    qr_cid: str | None = None,
    qr_alt: str | None = None,
    request_explanation: str | None = None,
    security_notice: str | None = None,
    activation_confirmation_notice: str | None = None,
    governance: ActivationMailGovernance | None = None,
) -> str:
    """Render the reusable fixed-dark transactional email shell."""

    safe_brand = html.escape(brand)
    safe_preheader = html.escape(preheader)
    safe_label = html.escape(label)
    safe_headline = html.escape(headline)
    safe_greeting = html.escape(greeting)
    safe_intro = html.escape(intro)
    safe_notice_label = html.escape(notice_label)
    safe_outcome = html.escape(outcome_note)
    safe_logo_cid = html.escape(logo_cid, quote=True)

    detail_rows = ""
    if event_name:
        detail_rows += f"""
          <tr>
            <td style="padding:0 12px 10px 0;color:#9ca3af;font-size:13px;line-height:18px;">Event</td>
            <td align="right" style="padding:0 0 10px 12px;color:#f3f4f6;font-size:14px;line-height:18px;font-weight:600;word-break:break-word;">{html.escape(event_name)}</td>
          </tr>"""
    if expiry_label:
        detail_rows += f"""
          <tr>
            <td style="padding:0 12px 0 0;color:#9ca3af;font-size:13px;line-height:18px;">Valid until</td>
            <td align="right" style="padding:0 0 0 12px;color:#f3f4f6;font-size:14px;line-height:18px;font-weight:600;">{html.escape(expiry_label)}</td>
          </tr>"""
    details = ""
    if detail_rows:
        details = f"""
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#22252a" style="margin:24px 0;background:#22252a;border:1px solid #3d434f;border-radius:10px;">
          <tr><td style="padding:16px 18px;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">{detail_rows}</table>
          </td></tr>
        </table>"""

    action = ""
    fallback = ""
    if action_url and button_label:
        safe_url = html.escape(action_url, quote=True)
        safe_button = html.escape(button_label)
        action = f"""
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:0 0 22px;">
          <tr>
            <td bgcolor="#2563eb" style="background:#2563eb;border-radius:8px;text-align:center;">
              <a href="{safe_url}" style="display:block;padding:13px 22px;color:#ffffff;font-size:16px;line-height:20px;font-weight:700;text-decoration:none;">{safe_button}</a>
            </td>
          </tr>
        </table>"""
        fallback = f"""
        <p style="margin:0 0 8px;color:#9ca3af;font-size:13px;line-height:19px;">If the button does not work, copy this link:</p>
        <p style="margin:0 0 24px;font-size:12px;line-height:18px;word-break:break-all;"><a href="{safe_url}" style="color:#93c5fd;text-decoration:underline;">{safe_url}</a></p>"""

    qr_section = ""
    if qr_cid and qr_alt:
        safe_qr_cid = html.escape(qr_cid, quote=True)
        safe_qr_alt = html.escape(qr_alt, quote=True)
        qr_section = f"""
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-top:1px solid #3d434f;margin-top:26px;">
          <tr><td style="padding-top:26px;">
            <h2 style="margin:0 0 8px;color:#f3f4f6;font-size:18px;line-height:24px;font-weight:700;">Scan instead</h2>
            <p style="margin:0 0 18px;color:#9ca3af;font-size:14px;line-height:21px;">Use this QR code when you want to open the secure action on another device.</p>
            <img src="cid:{safe_qr_cid}" width="460" alt="{safe_qr_alt}" style="display:block;width:100%;max-width:460px;height:auto;margin:0 auto;border:0;" />
          </td></tr>
        </table>"""

    security_section = ""
    if security_notice:
        security_section = (
            '<p style="margin:14px 0 0;color:#d1d5db;font-size:13px;line-height:19px;">'
            f'{html.escape(security_notice)}</p>'
        )

    consent_section = ""
    if activation_confirmation_notice:
        consent_section = (
            '<p style="margin:16px 0 0;color:#d1d5db;font-size:14px;line-height:21px;">'
            f'{html.escape(activation_confirmation_notice)}</p>'
        )

    privacy_section = ""
    if governance and request_explanation:
        safe_privacy = html.escape(governance.privacy_url, quote=True)
        safe_rights = html.escape(governance.rights_url, quote=True)
        event_link = ""
        if governance.event_privacy_url:
            safe_event_privacy = html.escape(governance.event_privacy_url, quote=True)
            event_link = (
                f' · <a href="{safe_event_privacy}" style="color:#93c5fd;text-decoration:underline;">'
                'Event privacy details</a>'
            )
        country_label = ", ".join(governance.smtp_processing_countries)
        privacy_section = f"""
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#22252a" style="margin-top:26px;background:#22252a;border:1px solid #3d434f;border-radius:8px;" data-policy-sha256="{html.escape(governance.policy_sha256, quote=True)}">
          <tr><td style="padding:14px 16px;">
            <p style="margin:0 0 7px;color:#f3f4f6;font-size:14px;line-height:19px;font-weight:700;">Privacy and contact</p>
            <p style="margin:0 0 5px;color:#d1d5db;font-size:13px;line-height:19px;">{html.escape(request_explanation)}</p>
            <p style="margin:0 0 5px;color:#d1d5db;font-size:13px;line-height:19px;"><strong>Controller:</strong> {html.escape(governance.controller_name)}</p>
            <p style="margin:0 0 5px;color:#d1d5db;font-size:13px;line-height:19px;"><strong>Privacy contact:</strong> <a href="mailto:{html.escape(governance.privacy_contact, quote=True)}" style="color:#93c5fd;text-decoration:underline;">{html.escape(governance.privacy_contact)}</a></p>
            <p style="margin:0 0 5px;color:#d1d5db;font-size:13px;line-height:19px;"><strong>Email delivery:</strong> {html.escape(governance.smtp_provider_name)} · {html.escape(country_label)}</p>
            <p style="margin:0;color:#9ca3af;font-size:12px;line-height:18px;"><a href="{safe_privacy}" style="color:#93c5fd;text-decoration:underline;">Privacy notice</a> · <a href="{safe_rights}" style="color:#93c5fd;text-decoration:underline;">Your rights</a>{event_link} · Published policy v{governance.policy_version}</p>
          </td></tr>
        </table>"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <meta name="color-scheme" content="dark" />
  <meta name="supported-color-schemes" content="dark" />
  <style>
    :root {{ color-scheme: dark; supported-color-schemes: dark; }}
    @media only screen and (max-width:620px) {{
      .mail-shell {{ width:100% !important; }}
      .mail-content {{ padding:24px 20px !important; }}
      .mail-header {{ padding:20px !important; }}
    }}
  </style>
</head>
<body bgcolor="#22252a" style="margin:0;padding:0;background:#22252a;color:#f3f4f6;font-family:Arial,'Segoe UI',sans-serif;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{safe_preheader}</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#22252a" style="width:100%;background:#22252a;">
    <tr>
      <td align="center" style="padding:32px 12px;">
        <table role="presentation" class="mail-shell" width="600" cellspacing="0" cellpadding="0" border="0" bgcolor="#282c34" style="width:100%;max-width:600px;background:#282c34;border:1px solid #3d434f;border-top:4px solid #2563eb;border-radius:16px;">
          <tr>
            <td class="mail-header" style="padding:26px 32px 20px;border-bottom:1px solid #3d434f;">
              <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                <tr>
                  <td valign="middle" style="padding-right:12px;"><img src="cid:{safe_logo_cid}" width="40" height="40" alt="{safe_brand}" style="display:block;width:40px;height:40px;border:0;" /></td>
                  <td valign="middle">
                    <p style="margin:0;color:#f3f4f6;font-size:18px;line-height:22px;font-weight:700;">{safe_brand}</p>
                    <p style="margin:3px 0 0;color:#9ca3af;font-size:12px;line-height:16px;">Secure event access</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td class="mail-content" style="padding:32px;">
              <p style="margin:0 0 10px;color:#93c5fd;font-size:12px;line-height:16px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;">{safe_label}</p>
              <h1 style="margin:0 0 20px;color:#f3f4f6;font-size:28px;line-height:36px;font-weight:700;">{safe_headline}</h1>
              <p style="margin:0 0 12px;color:#d1d5db;font-size:16px;line-height:24px;">Hello {safe_greeting},</p>
              <p style="margin:0;color:#d1d5db;font-size:16px;line-height:24px;">{safe_intro}</p>
              {details}
              {action}
              {fallback}
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#22252a" style="background:#22252a;border:1px solid #3d434f;border-left:4px solid {notice_colour};border-radius:8px;">
                <tr><td style="padding:14px 16px;">
                  <p style="margin:0 0 4px;color:#f3f4f6;font-size:13px;line-height:18px;font-weight:700;">{safe_notice_label}</p>
                  <p style="margin:0;color:#d1d5db;font-size:13px;line-height:19px;">{safe_outcome}</p>
                  {security_section}
                </td></tr>
              </table>
              {consent_section}
              {qr_section}
              {privacy_section}
            </td>
          </tr>
        </table>
        <table role="presentation" class="mail-shell" width="600" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;">
          <tr><td align="center" style="padding:18px 24px 0;color:#9ca3af;font-size:12px;line-height:18px;">
            Sent automatically by {safe_brand}. Do not forward secure access links or QR codes.
          </td></tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _attach_logo(html_part: EmailMessage, logo_cid: str) -> None:
    """Attach the local logo as an inline related image without remote loading."""

    html_part.add_related(
        _logo_path().read_bytes(),
        maintype="image",
        subtype="png",
        cid=logo_cid,
        disposition="inline",
    )


def build_activation_message(
    *,
    recipient: str,
    display_name: str,
    url: str,
    expires_at: datetime,
    purpose: str,
    governance: ActivationMailGovernance,
    self_service_requested: bool = False,
) -> tuple[EmailMessage, str]:
    """Build a branded multipart access email and return its message ID."""

    brand = governance.brand
    presentation = _purpose_presentation(purpose, brand)
    message, message_id = _base_message(
        recipient,
        presentation.subject,
        sender_name=brand,
    )
    message_domain = message_id.rsplit("@", 1)[-1].rstrip(">")
    logo_cid = make_msgid(domain=message_domain)
    qr_cid = make_msgid(domain=message_domain)
    qr_png = render_activation_qr_png(url, display_name, purpose)
    expiry = expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    expiry_label = expiry.astimezone(timezone.utc).strftime("%d %B %Y at %H:%M UTC")

    event_name = governance.event_name
    event_line = f" for {event_name}" if event_name else ""
    if self_service_requested and purpose == "additional_passkey":
        request_explanation = "You requested this from your signed-in account."
    elif purpose == "initial_setup":
        request_explanation = "An authorised organiser prepared account access for you."
    elif purpose == "additional_passkey":
        request_explanation = "An authorised organiser requested an additional passkey link."
    else:
        request_explanation = "An authorised administrator requested a credential reset."
    security_notice = (
        "If you did not request or expect this message, do not use the link. "
        "No new passkey has been added yet. Contact the controller below."
    )
    activation_confirmation_notice = (
        "The activation page explains the processing and asks for your confirmation "
        "before a passkey is registered."
        if purpose == "initial_setup"
        else None
    )
    message.set_content(
        f"Hello {display_name},\n\n"
        f"{presentation.headline}\n\n"
        f"{presentation.intro.rstrip('.')}{event_line}.\n\n"
        f"{presentation.button_label}:\n"
        f"{url}\n\n"
        f"Event: {event_name or 'Not specified'}\n"
        f"Valid until: {expiry_label}\n\n"
        f"{presentation.notice_label}: {presentation.outcome_note}\n\n"
        f"{security_notice}\n\n"
        + (
            f"{activation_confirmation_notice}\n\n"
            if activation_confirmation_notice else ""
        )
        +
        "The inline QR code opens the same secure action on another device.\n"
        "Do not forward this one-time link or QR code.\n"
        "\nPrivacy and contact\n"
        f"{request_explanation}\n"
        f"Controller: {governance.controller_name}\n"
        f"Privacy contact: {governance.privacy_contact}\n"
        f"Email delivery: {governance.smtp_provider_name} · {', '.join(governance.smtp_processing_countries)}\n"
        f"Privacy notice: {governance.privacy_url}\n"
        f"Your rights: {governance.rights_url}\n"
        + (
            f"Event privacy details: {governance.event_privacy_url}\n"
            if governance.event_privacy_url else ""
        )
        + f"Published policy: v{governance.policy_version}; SHA-256 {governance.policy_sha256}\n"
    )
    message.add_alternative(
        _email_html(
            brand=brand,
            logo_cid=logo_cid[1:-1],
            preheader=(
                f"{presentation.headline}. This one-time link expires on "
                f"{expiry_label}."
            ),
            label=presentation.label,
            headline=presentation.headline,
            greeting=display_name,
            intro=presentation.intro,
            event_name=event_name,
            expiry_label=expiry_label,
            action_url=url,
            button_label=presentation.button_label,
            notice_label=presentation.notice_label,
            outcome_note=presentation.outcome_note,
            notice_colour=presentation.notice_colour,
            qr_cid=qr_cid[1:-1],
            qr_alt=presentation.qr_alt,
            request_explanation=request_explanation,
            security_notice=security_notice,
            activation_confirmation_notice=activation_confirmation_notice,
            governance=governance,
        ),
        subtype="html",
    )
    html_part = message.get_payload()[1]
    _attach_logo(html_part, logo_cid)
    html_part.add_related(
        qr_png,
        maintype="image",
        subtype="png",
        cid=qr_cid,
        filename="activation-qr.png",
        disposition="inline",
    )
    return message, message_id


def build_test_message(recipient: str) -> EmailMessage:
    """Build a branded token-free SMTP configuration test message."""

    brand = _mail_brand_name()
    message, message_id = _base_message(
        recipient,
        f"{brand} email test",
        sender_name=brand,
    )
    logo_cid = make_msgid(domain=message_id.rsplit("@", 1)[-1].rstrip(">"))
    message.set_content(
        f"{brand} email delivery is ready.\n\n"
        f"{brand} can connect to the configured mail server securely.\n"
        "This test email contains no activation link or account token.\n"
    )
    message.add_alternative(
        _email_html(
            brand=brand,
            logo_cid=logo_cid[1:-1],
            preheader=(
                "Secure email delivery is configured. No activation link is included."
            ),
            label="Configuration test",
            headline="Email delivery is ready",
            greeting="administrator",
            intro=f"{brand} can connect to the configured mail server securely.",
            notice_label="Token-free test",
            outcome_note="No activation link or account token is included.",
            notice_colour="#3b82f6",
        ),
        subtype="html",
    )
    _attach_logo(message.get_payload()[1], logo_cid)
    return message


class ActivationMailer:
    """Authenticated SMTP connection reused for one immediate send operation."""

    def __init__(self) -> None:
        self._smtp: smtplib.SMTP | smtplib.SMTP_SSL | None = None

    def __enter__(self) -> "ActivationMailer":
        if not mail_is_configured():
            raise ActivationMailError(
                "mail_not_configured",
                "Email delivery is not configured on this server.",
            )
        try:
            validate_email(settings.SMTP_FROM_EMAIL, check_deliverability=False)
            if settings.SMTP_REPLY_TO:
                validate_email(settings.SMTP_REPLY_TO, check_deliverability=False)
        except EmailNotValidError as exc:
            raise ActivationMailError(
                "mail_configuration_invalid",
                "The configured sender address is invalid. Ask a root administrator to check the mail settings.",
            ) from exc
        context = ssl.create_default_context()
        smtp: smtplib.SMTP | smtplib.SMTP_SSL | None = None
        try:
            if settings.SMTP_SECURITY == "tls":
                smtp = smtplib.SMTP_SSL(
                    settings.SMTP_HOST,
                    settings.SMTP_PORT,
                    timeout=settings.SMTP_TIMEOUT_SECONDS,
                    context=context,
                )
            else:
                smtp = smtplib.SMTP(
                    settings.SMTP_HOST,
                    settings.SMTP_PORT,
                    timeout=settings.SMTP_TIMEOUT_SECONDS,
                )
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_TOKEN)
            self._smtp = smtp
            return self
        except smtplib.SMTPAuthenticationError as exc:
            if smtp is not None:
                smtp.close()
            logger.warning("SMTP authentication failed (%s)", type(exc).__name__)
            raise ActivationMailError(
                "smtp_authentication_failed",
                "The mail server rejected its credentials. Ask a root administrator to check the SMTP token.",
            ) from exc
        except (smtplib.SMTPException, OSError, socket.timeout) as exc:
            if smtp is not None:
                smtp.close()
            logger.warning("SMTP connection failed (%s)", type(exc).__name__)
            raise ActivationMailError(
                "smtp_unavailable",
                "The mail server could not be reached securely. Try again later.",
            ) from exc

    def send(self, message: EmailMessage) -> None:
        """Submit one message, classifying definite and uncertain failures."""

        if self._smtp is None:
            raise ActivationMailError(
                "smtp_unavailable",
                "The mail connection is not available.",
            )
        try:
            refused = self._smtp.send_message(message)
            if refused:
                raise ActivationMailError(
                    "recipient_rejected",
                    "The mail server rejected this recipient address.",
                )
        except ActivationMailError:
            raise
        except (
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
            smtplib.SMTPDataError,
            smtplib.SMTPResponseException,
        ) as exc:
            logger.info("SMTP rejected an activation message (%s)", type(exc).__name__)
            raise ActivationMailError(
                "recipient_rejected",
                "The mail server rejected this message or recipient address.",
            ) from exc
        except (smtplib.SMTPServerDisconnected, OSError, socket.timeout) as exc:
            logger.warning("SMTP acceptance is unknown (%s)", type(exc).__name__)
            raise ActivationMailError(
                "delivery_unknown",
                "Delivery could not be confirmed. The activation link was invalidated; send a fresh email.",
                unknown=True,
            ) from exc
        except smtplib.SMTPException as exc:
            logger.warning("SMTP send failed (%s)", type(exc).__name__)
            raise ActivationMailError(
                "smtp_send_failed",
                "The email could not be sent. The activation link was invalidated.",
            ) from exc

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._smtp is None:
            return
        try:
            self._smtp.quit()
        except (smtplib.SMTPException, OSError):
            self._smtp.close()
