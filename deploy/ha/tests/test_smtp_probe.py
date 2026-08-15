from __future__ import annotations

import importlib.util
from pathlib import Path
import smtplib


MODULE_PATH = Path(__file__).resolve().parents[1] / "smtp_probe.py"
SPEC = importlib.util.spec_from_file_location("mp_opt_smtp_probe", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
smtp_probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smtp_probe)


def test_recipient_rejection_is_not_reported_as_connection_failure() -> None:
    error = smtplib.SMTPRecipientsRefused(
        {"synthetic@example.invalid": (550, b"recipient rejected")}
    )
    assert smtp_probe.classify(error) == "recipient_rejected"


def test_sender_and_message_rejections_are_distinct() -> None:
    assert smtp_probe.classify(
        smtplib.SMTPSenderRefused(550, b"sender rejected", "synthetic@example.invalid")
    ) == "sender_rejected"
    assert smtp_probe.classify(
        smtplib.SMTPDataError(554, b"message rejected")
    ) == "message_rejected"
