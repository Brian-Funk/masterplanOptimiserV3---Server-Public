#!/usr/bin/env python3
"""Perform a token-free, node-local SMTP configuration and authentication probe."""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
from email.message import EmailMessage
import hashlib
import json
import os
from pathlib import Path
import re
import smtplib
import socket
import ssl
import tempfile


SAFE_KEYS = (
    "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_SECURITY",
    "SMTP_FROM_EMAIL", "SMTP_FROM_NAME", "SMTP_REPLY_TO", "SMTP_TIMEOUT_SECONDS",
)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def fingerprint(values: dict[str, str], token: str) -> str:
    canonical = "\n".join(f"{key}={values.get(key, '')}" for key in SAFE_KEYS)
    return hashlib.sha256((canonical + "\nSMTP_TOKEN=" + token).encode()).hexdigest()


def atomic_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True)
            handle.write("\n")
        os.chmod(name, 0o644)
        os.replace(name, path)
    finally:
        try: os.unlink(name)
        except FileNotFoundError: pass


def classify(exc: BaseException) -> str:
    if isinstance(exc, socket.gaierror): return "dns_failed"
    if isinstance(exc, smtplib.SMTPAuthenticationError): return "authentication_failed"
    if isinstance(exc, ssl.SSLError): return "tls_failed"
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError, OSError)): return "connection_failed"
    if isinstance(exc, smtplib.SMTPException): return "provider_rejected"
    return "probe_failed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/opt/masterplan"))
    parser.add_argument("--node-id", default=os.getenv("HA_NODE_ID", "unknown"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--send-to")
    parser.add_argument("--send-to-b64")
    parser.add_argument("--correlation-id")
    args = parser.parse_args()
    if args.send_to and args.send_to_b64:
        parser.error("choose only one SMTP recipient input")
    if args.send_to_b64:
        try:
            raw_recipient = base64.b64decode(args.send_to_b64, validate=True)
            args.send_to = raw_recipient.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            parser.error("the encoded SMTP recipient is invalid")
    observed = datetime.now(timezone.utc).isoformat()
    document: dict[str, object] = {
        "format": "mp-opt-smtp-probe-v1", "node_id": args.node_id,
        "checked_at": observed, "configured": False, "ready": False,
        "error_code": None, "config_fingerprint": None,
    }
    exit_code = 1
    try:
        values = read_env(args.root / ".env")
        token = (args.root / "secrets/smtp_token").read_text(encoding="utf-8").strip()
        required = [values.get("SMTP_HOST", ""), values.get("SMTP_USERNAME", ""),
                    values.get("SMTP_FROM_EMAIL", ""), token]
        if not all(required):
            document["error_code"] = "not_configured"
            raise RuntimeError("SMTP is not fully configured")
        document["configured"] = True
        document["config_fingerprint"] = fingerprint(values, token)
        host = values["SMTP_HOST"]
        port = int(values.get("SMTP_PORT", "587"))
        timeout = max(1, min(60, int(values.get("SMTP_TIMEOUT_SECONDS", "15"))))
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        document["address_family"] = "ipv6" if addresses and addresses[0][0] == socket.AF_INET6 else "ipv4"
        context = ssl.create_default_context()
        smtp: smtplib.SMTP | smtplib.SMTP_SSL
        if values.get("SMTP_SECURITY", "starttls") == "tls":
            smtp = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        else:
            smtp = smtplib.SMTP(host, port, timeout=timeout)
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        try:
            smtp.login(values["SMTP_USERNAME"], token)
            if args.send_to:
                if args.correlation_id is not None and re.fullmatch(r"[0-9a-f]{32}", args.correlation_id) is None:
                    raise ValueError("invalid SMTP test correlation identifier")
                message = EmailMessage()
                message["Subject"] = f"MP-OPT SMTP verification from {args.node_id}"
                message["From"] = values["SMTP_FROM_EMAIL"]
                message["To"] = args.send_to
                if args.correlation_id is not None:
                    message["X-MP-OPT-Test-ID"] = args.correlation_id
                message.set_content("This token-free message verifies SMTP delivery from one HA node.")
                refused = smtp.send_message(message)
                if refused: raise smtplib.SMTPRecipientsRefused(refused)
                document["test_message_sent"] = True
            document["ready"] = True
            exit_code = 0
        finally:
            try: smtp.quit()
            except (OSError, smtplib.SMTPException): smtp.close()
    except Exception as exc:
        if document.get("error_code") is None:
            document["error_code"] = classify(exc)
        document["error_type"] = type(exc).__name__
    if args.output:
        atomic_json(args.output, document)
    print(json.dumps(document, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
