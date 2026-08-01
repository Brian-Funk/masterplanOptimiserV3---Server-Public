#!/usr/bin/env python3
"""Send a token-free operational HA alert through the configured SMTP relay."""

from __future__ import annotations

import argparse
from email.message import EmailMessage
from email.utils import formataddr
import smtplib
import ssl
from pathlib import Path


ROOT = Path("/opt/masterplan")
HA_HOME = Path("/etc/mp-opt-ha")


def read_key_values(path: Path) -> dict[str, str]:
    """Read an inert dotenv-style file without evaluating its contents."""

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def main() -> int:
    """Validate configuration and submit one plain-text operational message."""

    parser = argparse.ArgumentParser()
    parser.add_argument("subject")
    parser.add_argument("message")
    arguments = parser.parse_args()
    application = read_key_values(ROOT / ".env")
    ha = read_key_values(HA_HOME / "node.env")
    recipient = ha.get("HA_ALERT_EMAIL", "")
    if not recipient:
        return 0
    token = (ROOT / "secrets/smtp_token").read_text(encoding="utf-8").strip()
    required = [
        application.get("SMTP_HOST", ""),
        application.get("SMTP_USERNAME", ""),
        application.get("SMTP_FROM_EMAIL", ""),
        token,
    ]
    if not all(required):
        raise RuntimeError("SMTP alert delivery is not configured")

    message = EmailMessage()
    message["Subject"] = f"MP-OPT HA: {arguments.subject}"
    message["From"] = formataddr(
        (
            application.get("SMTP_FROM_NAME", "Masterplan Access"),
            application["SMTP_FROM_EMAIL"],
        )
    )
    message["To"] = recipient
    message.set_content(arguments.message)
    context = ssl.create_default_context()
    port = int(application.get("SMTP_PORT", "587"))
    timeout = int(application.get("SMTP_TIMEOUT_SECONDS", "15"))
    if application.get("SMTP_SECURITY", "starttls") == "tls":
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            application["SMTP_HOST"], port, timeout=timeout, context=context
        )
    else:
        client = smtplib.SMTP(application["SMTP_HOST"], port, timeout=timeout)
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
    try:
        client.login(application["SMTP_USERNAME"], token)
        client.send_message(message)
    finally:
        client.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
