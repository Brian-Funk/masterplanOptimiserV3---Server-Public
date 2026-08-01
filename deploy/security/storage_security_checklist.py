#!/usr/bin/env python3
"""Validate and maintain a non-secret provider/workstation security checklist."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FORMAT = "mp-opt-storage-security-checklist-v1"
STATUSES = {"not_checked", "pass", "fail", "not_applicable"}
REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
REVIEWED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
VALUE_PATTERNS = {
    "country_code": re.compile(r"^[A-Z]{2}$"),
    "snapshot_policy": re.compile(r"^(disabled|used)$"),
    "disk_encryption": re.compile(r"^(bitlocker|filevault|luks|equivalent)$"),
    "cloud_sync": re.compile(r"^(disabled|approved-processor)$"),
}
FORBIDDEN_TEXT = (
    "BEGIN PRIVATE KEY",
    "AGE-SECRET-KEY-",
    "password=",
    "token=",
    "secret=",
)
TEMPLATE_PATH = Path(__file__).with_name("storage_security_checklist.json")


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("Checklist must be a JSON object")
    return value


def _expected_controls() -> dict[str, dict[str, Any]]:
    template = _load(TEMPLATE_PATH)
    return {item["id"]: item for item in template["controls"]}


def validate_document(document: dict[str, Any]) -> list[str]:
    """Return validation errors without reading or emitting protected values."""

    errors: list[str] = []
    expected = _expected_controls()
    if document.get("format") != FORMAT:
        errors.append("format is invalid")
    controls = document.get("controls")
    if not isinstance(controls, list):
        return errors + ["controls must be a list"]
    ids = [item.get("id") for item in controls if isinstance(item, dict)]
    if len(ids) != len(controls) or len(set(ids)) != len(ids):
        errors.append("control IDs must be unique strings")
    if set(ids) != set(expected):
        errors.append("control coverage differs from the required template")

    by_id: dict[str, dict[str, Any]] = {}
    for item in controls:
        if not isinstance(item, dict):
            continue
        control_id = item.get("id")
        if control_id not in expected:
            continue
        by_id[control_id] = item
        baseline = expected[control_id]
        for immutable in ("scope", "label", "allow_not_applicable", "value_format"):
            if item.get(immutable) != baseline[immutable]:
                errors.append(f"{control_id}: {immutable} differs from the template")
        status = item.get("status")
        value = item.get("recorded_value")
        reference = item.get("evidence_reference")
        reviewed_at = item.get("reviewed_at")
        if status not in STATUSES:
            errors.append(f"{control_id}: status is invalid")
            continue
        if status == "not_applicable" and not baseline["allow_not_applicable"]:
            errors.append(f"{control_id}: not_applicable is not allowed")
        if status == "not_checked":
            if any(field is not None for field in (value, reference, reviewed_at)):
                errors.append(f"{control_id}: unchecked controls must not contain evidence fields")
            continue
        if not isinstance(reference, str) or not REFERENCE_RE.fullmatch(reference):
            errors.append(f"{control_id}: evidence_reference is missing or unsafe")
        if not isinstance(reviewed_at, str) or not REVIEWED_AT_RE.fullmatch(reviewed_at):
            errors.append(f"{control_id}: reviewed_at must be UTC to whole seconds")
        value_format = baseline["value_format"]
        if status == "not_applicable":
            if value is not None:
                errors.append(f"{control_id}: not_applicable controls must not record a value")
        elif value_format == "none":
            if value is not None:
                errors.append(f"{control_id}: this control does not accept a recorded value")
        elif not isinstance(value, str) or not VALUE_PATTERNS[value_format].fullmatch(value):
            errors.append(f"{control_id}: recorded_value is invalid")

    encoded = json.dumps(document, sort_keys=True)
    if any(marker.lower() in encoded.lower() for marker in FORBIDDEN_TEXT):
        errors.append("checklist contains a forbidden secret marker")
    return errors


def is_ready(document: dict[str, Any]) -> bool:
    """Return whether all controls have a positive or permitted N/A decision."""

    if validate_document(document):
        return False
    controls = {item["id"]: item for item in document["controls"]}
    if not all(item["status"] in {"pass", "not_applicable"} for item in controls.values()):
        return False
    snapshot_policy = controls["provider_snapshot_policy"]["recorded_value"]
    dependent_statuses = {
        controls[control_id]["status"]
        for control_id in ("provider_snapshot_encryption", "provider_snapshot_lifecycle")
    }
    if snapshot_policy == "disabled":
        return dependent_statuses == {"not_applicable"}
    if snapshot_policy == "used":
        return dependent_statuses == {"pass"}
    return False


def _write_private(path: Path, document: dict[str, Any], *, refuse_existing: bool) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if refuse_existing and path.exists():
        raise FileExistsError(f"Refusing to overwrite {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".storage-checklist-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _report(document: dict[str, Any]) -> str:
    errors = validate_document(document)
    lines = [
        "Provider and workstation storage security",
        "",
        f"Schema: {'valid' if not errors else 'INVALID'}",
        f"Release readiness: {'ready' if not errors and is_ready(document) else 'blocked'}",
        "",
    ]
    for item in document.get("controls", []):
        value = f" ({item['recorded_value']})" if item.get("recorded_value") else ""
        reference = f" [{item['evidence_reference']}]" if item.get("evidence_reference") else ""
        lines.append(f"{item.get('status', 'invalid').upper():14} {item.get('label', 'invalid')}{value}{reference}")
    if errors:
        lines.extend(["", "Validation errors:", *[f"- {error}" for error in errors]])
    lines.extend([
        "",
        "This record contains decisions and non-secret evidence references only.",
        "It does not prove provider behaviour, disk encryption or physical deletion.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "check", "report", "list"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--file", type=Path, required=True)
    initialise = subparsers.add_parser("initialise")
    initialise.add_argument("--output", type=Path, required=True)
    update = subparsers.add_parser("set")
    update.add_argument("--file", type=Path, required=True)
    update.add_argument("--control", required=True)
    update.add_argument("--status", choices=sorted(STATUSES), required=True)
    update.add_argument("--value", default="-")
    update.add_argument("--evidence-reference", default="-")
    args = parser.parse_args()

    if args.command == "initialise":
        _write_private(args.output, copy.deepcopy(_load(TEMPLATE_PATH)), refuse_existing=True)
        return 0

    document = _load(args.file)
    if args.command == "set":
        controls = {item["id"]: item for item in document.get("controls", []) if isinstance(item, dict)}
        if args.control not in controls:
            raise ValueError("Unknown checklist control")
        item = controls[args.control]
        item["status"] = args.status
        if args.status == "not_checked":
            item["recorded_value"] = None
            item["evidence_reference"] = None
            item["reviewed_at"] = None
        else:
            item["recorded_value"] = None if args.value == "-" else args.value
            item["evidence_reference"] = (
                None if args.evidence_reference == "-" else args.evidence_reference
            )
            item["reviewed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        errors = validate_document(document)
        if errors:
            raise ValueError("; ".join(errors))
        _write_private(args.file, document, refuse_existing=False)
        return 0

    errors = validate_document(document)
    if args.command == "validate":
        if errors:
            print("\n".join(errors))
            return 1
        print("valid")
        return 0
    if args.command == "check":
        if errors:
            print("\n".join(errors))
        return 0 if is_ready(document) else 1
    if args.command == "report":
        print(_report(document), end="")
        return 0 if not errors else 1
    if args.command == "list":
        if errors:
            print("\n".join(errors))
            return 1
        for item in document["controls"]:
            print(f"{item['id']}\t{item['label']}\t{item['value_format']}")
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
