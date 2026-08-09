"""Secure activation email delivery and expiry policy tests."""

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from PIL import Image
from starlette.requests import Request

import app.api.v1.admin as admin_module
from app.core import runtime_settings
from app.core.activation import (
    ADDITIONAL_PASSKEY,
    CREDENTIAL_RESET,
    INITIAL_SETUP,
    ActivationDeliveryInProgressError,
    create_activation_link,
    validate_activation_token,
)
from app.core.activation_email import (
    ActivationMailError,
    activation_url,
    build_activation_message,
    build_test_message,
    recover_stale_deliveries,
    render_activation_qr_png,
)
from app.core.activation_mail_governance import resolve_activation_mail_governance
from app.models.audit import AuditLog
from app.models.governance import EventGovernanceOverride, GovernancePublication
from app.models.server_setting import ServerSetting
from app.models.user import ActivationEmailDelivery, ActivationLink
from server_backend.conftest import create_test_event, create_test_user, _make_client


class FakeMailer:
    """In-memory SMTP stand-in used by API tests."""

    def __init__(self, messages: list, errors: list[ActivationMailError] | None = None):
        self.messages = messages
        self.errors = errors or []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def send(self, message):
        self.messages.append(message)
        if self.errors:
            raise self.errors.pop(0)


def _request() -> Request:
    """Return a minimal request suitable for audit metadata in direct tests."""

    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/admin/users/1/activation-email",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "scheme": "https",
        "server": ("localhost", 443),
    })


MAIL_NOTICE = {
    "instance_name": "Synthetic Access Portal",
    "controller_legal_name": "Synthetic Event Controller",
    "privacy_contact_email": "privacy@synthetic-controller.ch",
    "optional_features": {
        "smtp_enabled": True,
        "smtp_provider_code": "synthetic_mail",
    },
    "processors": [
        {
            "provider_code": "synthetic_mail",
            "display_name": "Synthetic Mail Relay",
            "purpose_codes": ["activation_email"],
            "hosting_countries": ["CH"],
            "support_access_countries": ["DE"],
        }
    ],
}


def _publish_mail_notice(db, *, content: dict | None = None) -> GovernancePublication:
    """Publish synthetic controller facts without using deployment examples."""

    row = GovernancePublication(
        version=4,
        content_json=json.dumps(content or MAIL_NOTICE),
        content_sha256="4" * 64,
        source_json="{}",
        source_sha256="5" * 64,
        material_change=True,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def configured_mail(monkeypatch, db):
    """Configure a safe fake SMTP sender and capture generated messages."""

    from app.core.config import settings

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "token-user")
    monkeypatch.setattr(settings, "SMTP_TOKEN", "provider-token")
    monkeypatch.setattr(settings, "SMTP_SECURITY", "starttls")
    monkeypatch.setattr(settings, "SMTP_FROM_EMAIL", "access@example.com")
    monkeypatch.setattr(settings, "SMTP_FROM_NAME", "Synthetic SMTP Sender")
    _publish_mail_notice(db)
    messages: list = []
    monkeypatch.setattr(
        admin_module,
        "ActivationMailer",
        lambda: FakeMailer(messages),
    )
    return messages


def test_single_user_email_contains_link_and_one_inline_qr(
    db,
    admin_client,
    configured_mail,
):
    """A per-user send returns no token while producing the complete MIME email."""

    event, _ = create_test_event(db, name="Email Event")
    user = create_test_user(
        db,
        username="email.target",
        display_name="Email Target",
        event_id=event.id,
        is_activated=False,
    )

    response = admin_client.post(
        f"/api/v1/admin/users/{user.id}/activation-email",
        json={},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert "activation_url" not in response.json()
    assert "token" not in str(response.json()).lower()
    assert len(configured_mail) == 1
    message = configured_mail[0]
    assert message["Date"]
    assert "/activate#token=" in message.get_body(preferencelist=("plain",)).get_content()
    image_parts = [part for part in message.walk() if part.get_content_type() == "image/png"]
    qr_parts = [part for part in image_parts if part.get_filename() == "activation-qr.png"]
    logo_parts = [part for part in image_parts if part.get_filename() is None]
    assert len(image_parts) == 2
    assert len(qr_parts) == 1
    assert len(logo_parts) == 1
    assert logo_parts[0].get_content_disposition() == "inline"
    assert qr_parts[0].get_content_disposition() == "inline"
    assert not [part for part in message.walk() if part.get_content_disposition() == "attachment"]

    delivery = db.query(ActivationEmailDelivery).filter_by(user_id=user.id).one()
    link = db.query(ActivationLink).filter_by(user_id=user.id).one()
    assert delivery.status == "accepted"
    assert link.invalidated_at is None
    assert link.delivery_pending is False
    assert len(link.token_hash) == 64


def test_delivery_helper_commits_only_non_secret_metadata(
    db,
    configured_mail,
):
    """The shared single and batch delivery path works without HTTP transport."""

    event, _ = create_test_event(db, name="Direct Event")
    admin = create_test_user(
        db,
        username="direct.admin",
        event_id=event.id,
        is_admin=True,
    )
    user = create_test_user(
        db,
        username="direct.target",
        event_id=event.id,
        is_activated=False,
    )

    result = admin_module._send_user_activation_email(
        user=user,
        admin=admin,
        mailer=FakeMailer(configured_mail),
        request=_request(),
        purpose=INITIAL_SETUP,
        db=db,
    )

    assert result.status == "accepted"
    assert result.expires_at is not None
    assert configured_mail[0]["Date"]
    qr_parts = [
        part
        for part in configured_mail[0].walk()
        if part.get_filename() == "activation-qr.png"
    ]
    assert len(qr_parts) == 1
    with Image.open(BytesIO(qr_parts[0].get_payload(decode=True))) as qr_badge:
        assert qr_badge.format == "PNG"
        assert qr_badge.width == 920
        assert qr_badge.height > 1200
        assert qr_badge.getpixel((0, 0)) == (34, 37, 42)
    delivery = db.query(ActivationEmailDelivery).filter_by(user_id=user.id).one()
    assert delivery.status == "accepted"
    assert not hasattr(delivery, "activation_url")
    assert not hasattr(delivery, "token")


def test_additional_passkey_email_states_non_destructive_outcome(
    db,
    configured_mail,
):
    """Additional-passkey mail is purpose-bound and explains retained access."""

    event, _ = create_test_event(db, name="Additive Event")
    admin = create_test_user(
        db,
        username="additive.admin",
        event_id=event.id,
        is_admin=True,
    )
    user = create_test_user(
        db,
        username="additive.target",
        display_name="Additive Target",
        event_id=event.id,
        is_activated=True,
    )

    result = admin_module._send_user_activation_email(
        user=user,
        admin=admin,
        mailer=FakeMailer(configured_mail),
        request=_request(),
        purpose=ADDITIONAL_PASSKEY,
        db=db,
    )

    assert result.status == "accepted"
    assert result.purpose == ADDITIONAL_PASSKEY
    message = configured_mail[0]
    assert message["Subject"] == "Add another Synthetic Access Portal passkey"
    plain = message.get_body(preferencelist=("plain",)).get_content()
    assert "existing passkeys and signed-in sessions will remain valid" in plain
    assert "An authorised organiser requested an additional passkey link." in plain
    delivery = db.query(ActivationEmailDelivery).filter_by(user_id=user.id).one()
    link = db.query(ActivationLink).filter_by(user_id=user.id).one()
    assert delivery.purpose == ADDITIONAL_PASSKEY
    assert link.purpose == ADDITIONAL_PASSKEY


def test_participant_self_service_is_hidden_by_default(db):
    """The root-controlled participant capability defaults to disabled."""

    event, _ = create_test_event(db, name="Self-service disabled")
    user = create_test_user(db, username="disabled.user", event_id=event.id)
    client = _make_client(db, user)

    response = client.get("/api/v1/account/additional-passkey")

    assert response.status_code == 200
    assert response.json()["mode"] == "email"
    assert response.json()["self_service_enabled"] is False
    assert response.json()["can_request"] is False


def test_participant_self_service_explains_missing_admin_email(db):
    """No recipient can be selected or supplied by the participant."""

    event, _ = create_test_event(db, name="Missing email")
    user = create_test_user(db, username="missing.email", event_id=event.id)
    user.email = None
    db.add(ServerSetting(key="self_service_additional_passkeys_enabled", value="1"))
    db.commit()
    client = _make_client(db, user)

    capability = client.get("/api/v1/account/additional-passkey")
    send = client.post("/api/v1/account/additional-passkey/email", json={})

    assert capability.status_code == 200
    assert capability.json()["email_available"] is False
    assert "not been added by an administrator" in capability.json()["message"]
    assert send.status_code == 409
    assert "not been added by an administrator" in send.json()["detail"]
    assert db.query(ActivationLink).filter_by(user_id=user.id).count() == 0


def test_participant_can_email_only_their_recorded_additional_passkey_link(
    db,
    configured_mail,
):
    """Enabled self-service sends a non-destructive link to the stored address."""

    event, _ = create_test_event(db, name="Self-service enabled")
    user = create_test_user(db, username="enabled.user", event_id=event.id)
    db.add(ServerSetting(key="self_service_additional_passkeys_enabled", value="1"))
    db.commit()
    client = _make_client(db, user)

    response = client.post("/api/v1/account/additional-passkey/email", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["purpose"] == ADDITIONAL_PASSKEY
    assert "activation_url" not in response.json()
    delivery = db.query(ActivationEmailDelivery).filter_by(user_id=user.id).one()
    assert delivery.requested_by_id == user.id
    assert delivery.recipient_email == user.email
    assert delivery.purpose == ADDITIONAL_PASSKEY
    assert len(configured_mail) == 1
    plain = configured_mail[0].get_body(preferencelist=("plain",)).get_content()
    assert "You requested this from your signed-in account." in plain


def test_participant_capability_fails_closed_without_published_mail_governance(
    db,
    configured_mail,
):
    """A participant never receives a token from generic or draft-only facts."""

    db.query(GovernancePublication).delete()
    event, _ = create_test_event(db, name="Governance gated event")
    user = create_test_user(db, username="governance.gated", event_id=event.id)
    db.add(ServerSetting(key="self_service_additional_passkeys_enabled", value="1"))
    db.commit()
    client = _make_client(db, user)

    capability = client.get("/api/v1/account/additional-passkey")
    send = client.post("/api/v1/account/additional-passkey/email", json={})

    assert capability.status_code == 200
    assert capability.json()["governance_ready"] is False
    assert capability.json()["can_request"] is False
    assert "published controller and email-provider notice" in capability.json()["message"]
    assert send.status_code == 200
    assert send.json()["status"] == "not_attempted"
    assert send.json()["error_code"] == "published_mail_governance_unavailable"
    assert db.query(ActivationLink).filter_by(user_id=user.id).count() == 0
    assert configured_mail == []


@pytest.mark.parametrize(
    "mutate_notice",
    [
        lambda notice: notice["optional_features"].update({"smtp_enabled": False}),
        lambda notice: notice["optional_features"].update({"smtp_provider_code": "unknown"}),
        lambda notice: notice["processors"][0].update({"hosting_countries": [], "support_access_countries": []}),
        lambda notice: notice["processors"][0].update({"purpose_codes": ["event_scheduling"]}),
        lambda notice: notice.update({"instance_name": ""}),
    ],
)
def test_participant_mail_rejects_incomplete_published_variables(
    db,
    configured_mail,
    mutate_notice,
):
    """Every visible deployment fact must be supplied by one complete publication."""

    publication = db.query(GovernancePublication).one()
    notice = json.loads(publication.content_json)
    mutate_notice(notice)
    publication.content_json = json.dumps(notice)
    event, _ = create_test_event(db, name="Incomplete governance")
    user = create_test_user(db, username="incomplete.variables", event_id=event.id)
    db.commit()

    result = admin_module._send_user_activation_email(
        user=user,
        admin=user,
        mailer=FakeMailer(configured_mail),
        request=_request(),
        purpose=ADDITIONAL_PASSKEY,
        db=db,
    )

    assert result.status == "not_attempted"
    assert result.error_code == "published_mail_governance_unavailable"
    assert db.query(ActivationLink).filter_by(user_id=user.id).count() == 0
    assert configured_mail == []


def test_event_governance_can_disable_activation_email(
    db,
    configured_mail,
):
    """An event-level feature decision is enforced before token creation."""

    event, _ = create_test_event(db, name="Email-disabled event")
    user = create_test_user(db, username="event.disabled", event_id=event.id)
    db.add(EventGovernanceOverride(
        event_id=event.id,
        enabled_optional_features_json="[]",
        policy_version=4,
    ))
    db.commit()

    result = admin_module._send_user_activation_email(
        user=user,
        admin=user,
        mailer=FakeMailer(configured_mail),
        request=_request(),
        purpose=ADDITIONAL_PASSKEY,
        db=db,
    )

    assert result.status == "not_attempted"
    assert result.error_code == "event_activation_email_disabled"
    assert db.query(ActivationLink).filter_by(user_id=user.id).count() == 0


def test_event_controller_override_drives_mail_and_public_details(
    db,
    configured_mail,
    admin_client,
):
    """Reviewed event facts replace instance identity without exposing drafts."""

    event, _ = create_test_event(db, name="Overridden event")
    user = create_test_user(db, username="override.recipient", event_id=event.id)
    db.add(EventGovernanceOverride(
        event_id=event.id,
        controller_override_enabled=True,
        controller_identity_override="Synthetic Event-Specific Controller",
        privacy_contact_override="event-privacy@synthetic-controller.ch",
        enabled_optional_features_json='["activation_email"]',
        policy_version=4,
    ))
    db.commit()

    result = admin_module._send_user_activation_email(
        user=user,
        admin=user,
        mailer=FakeMailer(configured_mail),
        request=_request(),
        purpose=ADDITIONAL_PASSKEY,
        db=db,
    )

    assert result.status == "accepted"
    html_body = configured_mail[0].get_body(preferencelist=("html",)).get_content()
    assert "Synthetic Event-Specific Controller" in html_body
    assert "event-privacy@synthetic-controller.ch" in html_body
    assert f"/api/v1/governance/public/events/{event.id}/privacy.html" in html_body
    details = admin_client.get(
        f"/api/v1/governance/public/events/{event.id}/privacy.html"
    )
    assert details.status_code == 200
    assert "Synthetic Event-Specific Controller" in details.text
    assert "event-privacy@synthetic-controller.ch" in details.text
    assert "Overridden event" not in details.text
    assert 'name="robots"' not in details.text
    assert details.headers["x-robots-tag"] == "noindex, nofollow"


def test_participant_self_service_enforces_per_user_minute_limit(
    db,
    configured_mail,
):
    """The root-selected minute limit counts only participant self-service attempts."""

    event, _ = create_test_event(db, name="Self-service limited")
    user = create_test_user(db, username="limited.user", event_id=event.id)
    db.add_all([
        ServerSetting(key="self_service_additional_passkeys_enabled", value="1"),
        ServerSetting(key="self_service_passkey_emails_per_minute", value="1"),
        ServerSetting(key="self_service_passkey_emails_per_day", value="10"),
    ])
    db.commit()
    client = _make_client(db, user)

    first = client.post("/api/v1/account/additional-passkey/email", json={})
    second = client.post("/api/v1/account/additional-passkey/email", json={})

    assert first.status_code == 200
    assert second.status_code == 429
    assert "per-minute" in second.json()["detail"]
    assert second.headers["retry-after"] == "60"
    assert len(configured_mail) == 1


def test_participant_self_service_enforces_per_user_daily_limit(
    db,
    configured_mail,
):
    """The independently configurable rolling-day limit is enforced as well."""

    event, _ = create_test_event(db, name="Self-service daily limit")
    user = create_test_user(db, username="daily.user", event_id=event.id)
    db.add_all([
        ServerSetting(key="self_service_additional_passkeys_enabled", value="1"),
        ServerSetting(key="self_service_passkey_emails_per_minute", value="10"),
        ServerSetting(key="self_service_passkey_emails_per_day", value="1"),
    ])
    db.commit()
    client = _make_client(db, user)

    first = client.post("/api/v1/account/additional-passkey/email", json={})
    second = client.post("/api/v1/account/additional-passkey/email", json={})

    assert first.status_code == 200
    assert second.status_code == 429
    assert "daily" in second.json()["detail"]
    assert second.headers["retry-after"] == "86400"
    assert len(configured_mail) == 1


def test_management_accounts_keep_direct_passkey_enrollment(db, configured_mail):
    """Participant policy and limits never replace management re-authentication."""

    event, _ = create_test_event(db, name="Management direct")
    admin = create_test_user(
        db,
        username="direct.manager",
        event_id=event.id,
        is_admin=True,
    )
    client = _make_client(db, admin)

    capability = client.get("/api/v1/account/additional-passkey")
    send = client.post("/api/v1/account/additional-passkey/email", json={})

    assert capability.status_code == 200
    assert capability.json()["mode"] == "direct"
    assert send.status_code == 409
    assert "directly after re-authentication" in send.json()["detail"]
    assert configured_mail == []


@pytest.mark.parametrize(
    ("purpose", "subject", "headline", "notice", "reason"),
    [
        (
            INITIAL_SETUP,
            "Activate your Synthetic Access Portal account",
            "Set up your secure access",
            "Keep this private",
            "An authorised organiser prepared account access for you.",
        ),
        (
            ADDITIONAL_PASSKEY,
            "Add another Synthetic Access Portal passkey",
            "Add a passkey to your account",
            "Existing access remains",
            "An authorised organiser requested an additional passkey link.",
        ),
        (
            CREDENTIAL_RESET,
            "Reset your Synthetic Access Portal passkeys",
            "Reset your passkeys",
            "Important",
            "An authorised administrator requested a credential reset.",
        ),
    ],
)
def test_email_purposes_share_branded_dark_accessible_shell(
    db,
    configured_mail,
    purpose,
    subject,
    headline,
    notice,
    reason,
):
    """Every purpose uses the same accessible dark shell and tailored copy."""

    event, _ = create_test_event(db, name='Event <b>unsafe</b> & "quoted"')
    recipient = create_test_user(
        db,
        username=f"purpose.{purpose}",
        event_id=event.id,
    )
    governance = resolve_activation_mail_governance(user=recipient, db=db)
    message, _message_id = build_activation_message(
        recipient="recipient@example.com",
        display_name="Alex <script>alert(1)</script>",
        url="https://localhost/activate#token=safe-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        purpose=purpose,
        governance=governance,
    )

    assert message["Subject"] == subject
    assert "Synthetic Access Portal <access@example.com>" == str(message["From"])
    html_body = message.get_body(preferencelist=("html",)).get_content()
    plain_body = message.get_body(preferencelist=("plain",)).get_content()
    assert headline in html_body
    assert notice in html_body
    assert reason in html_body
    assert "Synthetic Event Controller" in html_body
    assert "privacy@synthetic-controller.ch" in html_body
    assert "Synthetic Mail Relay" in html_body
    assert "CH, DE" in html_body
    assert 'data-policy-sha256="4444444444444444444444444444444444444444444444444444444444444444"' in html_body
    assert 'bgcolor="#22252a"' in html_body
    assert 'bgcolor="#282c34"' in html_body
    assert 'role="presentation"' in html_body
    assert 'alt="QR code' in html_body
    assert 'width="460"' in html_body
    assert "<script>" not in html_body
    assert "<b>unsafe</b>" not in html_body
    assert "&lt;script&gt;" in html_body
    assert "Event &lt;b&gt;unsafe&lt;/b&gt; &amp; &quot;quoted&quot;" in html_body
    assert "https://localhost/activate#token=safe-token" in plain_body
    assert "Published policy: v4; SHA-256 " + "4" * 64 in plain_body
    action_position = html_body.index('href="https://localhost/activate#token=safe-token"')
    security_position = html_body.index("If you did not request or expect this message")
    qr_position = html_body.index("Scan instead")
    privacy_position = html_body.index("Privacy and contact")
    assert action_position < security_position < qr_position < privacy_position
    assert plain_body.index("https://localhost/activate#token=safe-token") < plain_body.index(
        "If you did not request or expect this message"
    ) < plain_body.index("The inline QR code") < plain_body.index("Privacy and contact")
    assert '<img src="http' not in html_body
    assert len(html_body.encode()) < 80_000


def test_operational_mail_uses_only_escaped_deployment_variables(
    db,
    configured_mail,
    monkeypatch,
):
    """No maintainer, campaign host, or example deployment leaks into mail."""

    from app.core.config import settings

    publication = db.query(GovernancePublication).one()
    notice = json.loads(publication.content_json)
    notice["instance_name"] = "Tenant & <Portal>"
    notice["controller_legal_name"] = "Controller <One> & Partners"
    notice["processors"][0]["display_name"] = "Mail <Relay> & Co"
    publication.content_json = json.dumps(notice)
    monkeypatch.setattr(settings, "WEBAUTHN_ORIGIN", "https://tenant.synthetic-controller.ch")
    event, _ = create_test_event(db, name="Variable event")
    user = create_test_user(db, username="variable.recipient", event_id=event.id)
    db.commit()
    governance = resolve_activation_mail_governance(user=user, db=db)

    message, _ = build_activation_message(
        recipient=user.email,
        display_name=user.display_name,
        url="https://tenant.synthetic-controller.ch/activate#token=variable-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        purpose=ADDITIONAL_PASSKEY,
        governance=governance,
        self_service_requested=True,
    )

    html_body = message.get_body(preferencelist=("html",)).get_content()
    plain_body = message.get_body(preferencelist=("plain",)).get_content()
    assert str(message["Subject"]) == "Add another Tenant & <Portal> passkey"
    assert "Tenant &amp; &lt;Portal&gt;" in html_body
    assert "Controller &lt;One&gt; &amp; Partners" in html_body
    assert "Mail &lt;Relay&gt; &amp; Co" in html_body
    assert "https://tenant.synthetic-controller.ch/api/v1/governance/public/versions/4/privacy.html" in plain_body
    for forbidden in (
        "mp-opt.net",
        "access@mp-opt.net",
        "Brian-Funk",
        "Northstar Assembly Cooperative",
    ):
        assert forbidden not in str(message)


def test_token_free_test_email_previews_brand_without_activation_content(
    configured_mail,
):
    """The SMTP test previews the mail design without any account secret."""

    message = build_test_message("administrator@example.com")

    assert message["Subject"] == "Synthetic SMTP Sender email test"
    plain_body = message.get_body(preferencelist=("plain",)).get_content()
    html_body = message.get_body(preferencelist=("html",)).get_content()
    assert "Email delivery is ready" in html_body
    assert "Configuration test" in html_body
    assert "Synthetic SMTP Sender can connect" in plain_body
    assert "/activate" not in str(message)
    assert "token=" not in str(message)
    assert "href=" not in html_body
    assert not [
        part
        for part in message.walk()
        if part.get_content_disposition() == "attachment"
    ]
    inline_images = [
        part for part in message.walk() if part.get_content_type() == "image/png"
    ]
    assert len(inline_images) == 1
    assert inline_images[0].get_content_disposition() == "inline"


def test_qr_zip_uses_identical_canonical_bytes_and_token_free_audit(
    db,
    admin_client,
    configured_mail,
):
    """A direct ZIP and the inline email QR share exactly one PNG renderer."""

    event, _ = create_test_event(db, name="Canonical Event")
    user = create_test_user(
        db,
        username="canonical.target",
        display_name="Älice / Team",
        event_id=event.id,
        is_activated=False,
    )
    token, link = create_activation_link(
        user_id=user.id,
        created_by_id=None,
        db=db,
    )
    db.commit()

    response = admin_client.post(
        "/api/v1/admin/activation-qr-codes",
        json={"items": [{"user_id": user.id, "token": token}]},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert response.headers["cache-control"] == "no-store"
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == [f"Alice-Team-{user.id}.png"]
        zip_png = archive.read(archive.namelist()[0])

    url = activation_url(token)
    expected_png = render_activation_qr_png(url, user.display_name, link.purpose)
    message, _ = build_activation_message(
        recipient=user.email,
        display_name=user.display_name,
        url=url,
        expires_at=link.expires_at,
        purpose=link.purpose,
        governance=resolve_activation_mail_governance(user=user, db=db),
    )
    inline_qr = next(
        part
        for part in message.walk()
        if part.get_filename() == "activation-qr.png"
    )
    assert inline_qr.get_content_disposition() == "inline"
    assert zip_png == expected_png == inline_qr.get_payload(decode=True)

    audit_entry = db.query(AuditLog).filter_by(action="activation.qr_download").one()
    assert str(user.id) in (audit_entry.detail or "")
    assert token not in (audit_entry.detail or "")
    assert token not in archive.namelist()[0]


@pytest.mark.parametrize("unavailable_state", ["expired", "used", "invalidated"])
def test_qr_zip_rejects_unavailable_links_without_exposing_tokens(
    db,
    admin_client,
    unavailable_state,
):
    """Expired, used, and invalidated links share one safe download failure."""

    event, _ = create_test_event(db, name="Unavailable QR Event")
    user = create_test_user(
        db,
        username=f"{unavailable_state}.target",
        event_id=event.id,
        is_activated=False,
    )
    token, link = create_activation_link(
        user_id=user.id,
        created_by_id=None,
        db=db,
    )
    if unavailable_state == "expired":
        link.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    elif unavailable_state == "used":
        link.used_at = datetime.now(timezone.utc)
    else:
        link.invalidated_at = datetime.now(timezone.utc)
    db.commit()

    response = admin_client.post(
        "/api/v1/admin/activation-qr-codes",
        json={"items": [{"user_id": user.id, "token": token}]},
    )

    assert response.status_code == 400
    assert "no longer available" in response.json()["detail"]
    assert token not in response.text
    assert db.query(AuditLog).filter_by(action="activation.qr_download").count() == 0


def test_qr_zip_rejects_mismatched_duplicate_and_oversized_selections(
    db,
    admin_client,
):
    """QR downloads reject ambiguous or mismatched selections before rendering."""

    event, _ = create_test_event(db, name="Bounded QR Event")
    owner = create_test_user(
        db,
        username="qr.owner",
        event_id=event.id,
        is_activated=False,
    )
    other = create_test_user(
        db,
        username="qr.other",
        event_id=event.id,
        is_activated=False,
    )
    token, _link = create_activation_link(
        user_id=owner.id,
        created_by_id=None,
        db=db,
    )
    db.commit()

    mismatched = admin_client.post(
        "/api/v1/admin/activation-qr-codes",
        json={"items": [{"user_id": other.id, "token": token}]},
    )
    duplicate = admin_client.post(
        "/api/v1/admin/activation-qr-codes",
        json={
            "items": [
                {"user_id": owner.id, "token": token},
                {"user_id": owner.id, "token": token},
            ]
        },
    )
    oversized = admin_client.post(
        "/api/v1/admin/activation-qr-codes",
        json={
            "items": [
                {"user_id": index + 1, "token": f"token-{index}"}
                for index in range(51)
            ]
        },
    )

    assert mismatched.status_code == 400
    assert token not in mismatched.text
    assert duplicate.status_code == 422
    assert oversized.status_code == 422


def test_issuer_cannot_render_another_events_qr(db, issuer_client):
    """Issuer scoping applies equally to manual QR archive generation."""

    client, _issuer, _issuer_event = issuer_client
    other_event, _ = create_test_event(db, name="Other QR Event")
    user = create_test_user(
        db,
        username="cross.event.qr",
        event_id=other_event.id,
        is_activated=False,
    )
    token, _link = create_activation_link(
        user_id=user.id,
        created_by_id=None,
        db=db,
    )
    db.commit()

    response = client.post(
        "/api/v1/admin/activation-qr-codes",
        json={"items": [{"user_id": user.id, "token": token}]},
    )

    assert response.status_code == 403
    assert token not in response.text


def test_qr_rendering_failure_does_not_invalidate_link(
    db,
    admin_client,
    monkeypatch,
):
    """Artwork failures leave the underlying manual activation link untouched."""

    event, _ = create_test_event(db, name="Render Failure Event")
    user = create_test_user(
        db,
        username="render.failure",
        event_id=event.id,
        is_activated=False,
    )
    token, link = create_activation_link(
        user_id=user.id,
        created_by_id=None,
        db=db,
    )
    db.commit()
    monkeypatch.setattr(
        admin_module,
        "render_activation_qr_png",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("render failed")),
    )

    with pytest.raises(RuntimeError, match="render failed"):
        admin_client.post(
            "/api/v1/admin/activation-qr-codes",
            json={"items": [{"user_id": user.id, "token": token}]},
        )

    db.refresh(link)
    assert link.invalidated_at is None
    assert link.used_at is None


def test_unknown_delivery_is_recorded_and_link_is_invalidated(
    db,
    admin_client,
    configured_mail,
    monkeypatch,
):
    """An uncertain SMTP outcome never leaves a usable activation link."""

    event, _ = create_test_event(db, name="Unknown Event")
    user = create_test_user(
        db,
        username="unknown.target",
        event_id=event.id,
        is_activated=False,
    )
    monkeypatch.setattr(
        admin_module,
        "ActivationMailer",
        lambda: FakeMailer(
            configured_mail,
            [ActivationMailError("delivery_unknown", "Delivery unknown.", unknown=True)],
        ),
    )

    response = admin_client.post(
        f"/api/v1/admin/users/{user.id}/activation-email",
        json={},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "unknown"
    delivery = db.query(ActivationEmailDelivery).filter_by(user_id=user.id).one()
    link = db.query(ActivationLink).filter_by(user_id=user.id).one()
    assert delivery.error_code == "delivery_unknown"
    assert link.invalidated_at is not None


def test_retry_links_attempts_and_creates_a_fresh_token(
    db,
    admin_client,
    configured_mail,
    monkeypatch,
):
    """Retry creates a new link and records its relationship to the failed attempt."""

    event, _ = create_test_event(db, name="Retry Event")
    user = create_test_user(
        db,
        username="retry.target",
        event_id=event.id,
        is_activated=False,
    )
    errors = [ActivationMailError("recipient_rejected", "Recipient rejected.")]
    monkeypatch.setattr(
        admin_module,
        "ActivationMailer",
        lambda: FakeMailer(configured_mail, errors),
    )
    first = admin_client.post(
        f"/api/v1/admin/users/{user.id}/activation-email",
        json={},
    ).json()
    second = admin_client.post(
        f"/api/v1/admin/users/{user.id}/activation-email",
        json={"retry_of_delivery_id": first["delivery_id"]},
    ).json()

    assert first["status"] == "failed"
    assert second["status"] == "accepted"
    deliveries = (
        db.query(ActivationEmailDelivery)
        .filter_by(user_id=user.id)
        .order_by(ActivationEmailDelivery.id)
        .all()
    )
    links = (
        db.query(ActivationLink)
        .filter_by(user_id=user.id)
        .order_by(ActivationLink.id)
        .all()
    )
    assert deliveries[1].retry_of_id == deliveries[0].id
    assert links[0].token_hash != links[1].token_hash
    assert links[0].invalidated_at is not None
    assert links[1].invalidated_at is None


def test_batch_uses_only_selected_users_and_reports_missing_email(
    db,
    admin_client,
    configured_mail,
):
    """Batch email is explicit and returns an outcome for every selected user."""

    event, _ = create_test_event(db, name="Selected Event")
    selected = create_test_user(
        db,
        username="selected.target",
        event_id=event.id,
        is_activated=False,
    )
    missing = create_test_user(
        db,
        username="missing.target",
        event_id=event.id,
        is_activated=False,
    )
    unselected = create_test_user(
        db,
        username="unselected.target",
        event_id=event.id,
        is_activated=False,
    )
    missing.email = None
    db.commit()

    response = admin_client.post(
        "/api/v1/admin/batch-activation-emails",
        json={"user_ids": [selected.id, missing.id]},
    )

    assert response.status_code == 200
    assert [result["user_id"] for result in response.json()["results"]] == [
        selected.id,
        missing.id,
    ]
    assert response.json()["results"][0]["status"] == "accepted"
    assert response.json()["results"][1]["error_code"] == "missing_email"
    assert db.query(ActivationLink).filter_by(user_id=unselected.id).count() == 0


def test_activation_expiry_defaults_to_24_hours_and_is_capped(db):
    """The global policy advertises the secure default and seven-day maximum."""

    meta = runtime_settings.get_all(db)["activation_link_expiry_hours"]
    assert meta["default"] == 24
    assert meta["min"] == 1
    assert meta["max"] == 168


def test_root_can_invalidate_all_active_links_after_reauthentication(db):
    """Global invalidation is root-only, confirmed and audited."""

    event, _ = create_test_event(db, name="Invalidate Event")
    root = create_test_user(
        db,
        username="invalidate.root",
        is_root_admin=True,
        is_admin=True,
    )
    target = create_test_user(
        db,
        username="invalidate.target",
        event_id=event.id,
        is_activated=False,
    )
    client = _make_client(db, root, reauth=True)
    client.post(f"/api/v1/admin/users/{target.id}/activation-link", json={})

    response = client.post(
        "/api/v1/admin/activation-links/invalidate-all",
        json={"confirm": True},
    )

    assert response.status_code == 200
    assert response.json()["invalidated_count"] == 1
    link = db.query(ActivationLink).filter_by(user_id=target.id).one()
    assert link.invalidated_at is not None
    assert link.invalidated_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc)


def test_pending_delivery_link_cannot_be_used_until_smtp_acceptance(db):
    """A committed link remains unusable while its SMTP attempt is unresolved."""

    event, _ = create_test_event(db, name="Pending Event")
    admin = create_test_user(db, username="pending.admin", is_admin=True)
    user = create_test_user(
        db,
        username="pending.target",
        event_id=event.id,
        is_activated=False,
    )
    token, link = create_activation_link(
        user_id=user.id,
        created_by_id=admin.id,
        db=db,
        delivery_pending=True,
    )
    db.commit()

    assert validate_activation_token(token, db) is None
    link.delivery_pending = False
    db.commit()
    assert validate_activation_token(token, db).id == link.id


def test_interrupted_delivery_recovery_invalidates_pending_link(db):
    """An interrupted SMTP attempt becomes unknown and cannot leave a live link."""

    event, _ = create_test_event(db, name="Recovery Event")
    admin = create_test_user(db, username="recovery.admin", is_admin=True)
    user = create_test_user(
        db,
        username="recovery.target",
        event_id=event.id,
        is_activated=False,
    )
    _token, link = create_activation_link(
        user_id=user.id,
        created_by_id=admin.id,
        db=db,
        delivery_pending=True,
    )
    delivery = ActivationEmailDelivery(
        activation_link_id=link.id,
        user_id=user.id,
        requested_by_id=admin.id,
        recipient_email="recovery@example.com",
        status="sending",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    db.add(delivery)
    db.commit()

    assert recover_stale_deliveries(db, user_id=user.id) == 1
    db.commit()
    db.refresh(delivery)
    db.refresh(link)
    assert delivery.status == "unknown"
    assert delivery.error_code == "delivery_interrupted"
    assert link.delivery_pending is False
    assert link.invalidated_at is not None


def test_existing_send_is_reported_without_creating_another_link(
    db,
    configured_mail,
):
    """A concurrent request is skipped while the first SMTP hand-off is active."""

    event, _ = create_test_event(db, name="Concurrent Event")
    admin = create_test_user(
        db,
        username="concurrent.admin",
        is_admin=True,
        event_id=event.id,
    )
    user = create_test_user(
        db,
        username="concurrent.target",
        event_id=event.id,
        is_activated=False,
    )
    user.email = "concurrent@example.com"
    db.commit()
    _token, link = create_activation_link(
        user_id=user.id,
        created_by_id=admin.id,
        db=db,
        delivery_pending=True,
    )
    delivery = ActivationEmailDelivery(
        activation_link_id=link.id,
        user_id=user.id,
        requested_by_id=admin.id,
        recipient_email=user.email,
        status="sending",
        started_at=datetime.now(timezone.utc),
    )
    db.add(delivery)
    db.commit()

    result = admin_module._send_user_activation_email(
        user=user,
        admin=admin,
        mailer=FakeMailer(configured_mail),
        request=_request(),
        purpose=INITIAL_SETUP,
        db=db,
    )

    assert result.status == "skipped"
    assert result.error_code == "delivery_in_progress"
    with pytest.raises(ActivationDeliveryInProgressError):
        create_activation_link(
            user_id=user.id,
            created_by_id=admin.id,
            db=db,
        )
    with pytest.raises(HTTPException) as invalidation:
        admin_module.invalidate_activation_link(
            user_id=user.id,
            link_id=link.id,
            request=_request(),
            admin=admin,
            db=db,
        )
    assert invalidation.value.status_code == 409
    assert db.query(ActivationLink).filter_by(user_id=user.id).count() == 1
    assert configured_mail == []
