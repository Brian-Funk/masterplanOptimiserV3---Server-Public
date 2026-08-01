"""Tests for the non-secret provider/workstation security checklist."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "deploy" / "security" / "storage_security_checklist.py"
SPEC = importlib.util.spec_from_file_location("storage_security_checklist", MODULE_PATH)
assert SPEC and SPEC.loader
CHECKLIST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKLIST)


def _template() -> dict:
    return json.loads(
        (ROOT / "deploy" / "security" / "storage_security_checklist.json").read_text(
            encoding="utf-8"
        )
    )


def test_committed_template_is_complete_valid_and_not_preapproved():
    document = _template()

    assert CHECKLIST.validate_document(document) == []
    assert not CHECKLIST.is_ready(document)
    assert all(item["status"] == "not_checked" for item in document["controls"])
    assert len(document["controls"]) == 16


def test_complete_review_is_ready_without_storing_source_evidence():
    document = copy.deepcopy(_template())
    reviewed_at = "2026-07-30T12:00:00Z"
    values = {
        "provider_snapshot_policy": "disabled",
        "provider_data_centre_country": "CH",
        "workstation_full_disk_encryption": "bitlocker",
        "workstation_cloud_sync": "disabled",
    }
    snapshot_dependents = {
        "provider_snapshot_encryption",
        "provider_snapshot_lifecycle",
    }
    for item in document["controls"]:
        item["status"] = "not_applicable" if item["id"] in snapshot_dependents else "pass"
        item["recorded_value"] = values.get(item["id"])
        item["evidence_reference"] = f"review-{item['id']}"
        item["reviewed_at"] = reviewed_at

    assert CHECKLIST.validate_document(document) == []
    assert CHECKLIST.is_ready(document)
    assert "ready" in CHECKLIST._report(document)


def test_invalid_values_and_secret_markers_are_rejected():
    document = _template()
    item = next(
        control
        for control in document["controls"]
        if control["id"] == "provider_data_centre_country"
    )
    item.update(
        status="pass",
        recorded_value="Switzerland",
        evidence_reference="token=do-not-store",
        reviewed_at="2026-07-30T12:00:00Z",
    )

    errors = CHECKLIST.validate_document(document)

    assert any("recorded_value is invalid" in error for error in errors)
    assert any("evidence_reference is missing or unsafe" in error for error in errors)
    assert any("forbidden secret marker" in error for error in errors)


def test_management_menu_exposes_checklist_without_external_queries():
    actions = (ROOT / "deploy" / "management" / "actions.sh").read_text(encoding="utf-8")
    menu = (ROOT / "manage.sh").read_text(encoding="utf-8")

    assert "mp_storage_security_checklist()" in actions
    assert "storage_security_checklist.py" in actions
    assert '"storage-checklist" "Review provider and workstation storage controls"' in menu
    body = actions[
        actions.index("mp_storage_security_checklist()") : actions.index(
            "# Show database size", actions.index("mp_storage_security_checklist()")
        )
    ]
    assert "curl " not in body
