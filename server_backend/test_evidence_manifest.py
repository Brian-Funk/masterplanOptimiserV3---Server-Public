"""Tests for canonical, signed, hash-chained accountability evidence."""

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from deploy.evidence.evidence_manifest import (
    EvidenceError,
    PAYLOAD_FIELDS,
    RECORD_TYPES,
    append_record,
    canonical_json,
    key_id,
    load_json_bytes,
    verify_chain,
)


def _keypair(tmp_path):
    private_key = tmp_path / "instance-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "ignored-comment", "-f", str(private_key)],
        check=True,
    )
    return private_key, private_key.with_suffix(".pub")


def test_canonical_json_rejects_duplicates_floats_and_control_characters():
    with pytest.raises(EvidenceError, match="duplicate"):
        load_json_bytes(b'{"format":"one","format":"two"}\n')
    with pytest.raises(EvidenceError, match="floating-point"):
        load_json_bytes(b'{"value":1.2}\n')
    with pytest.raises(EvidenceError, match="control"):
        canonical_json({"value": "line\nbreak"})


def test_key_id_ignores_public_key_comment(tmp_path):
    _private, public = _keypair(tmp_path)
    raw = public.read_text(encoding="ascii")

    assert key_id(raw) == key_id(" ".join(raw.split()[:2]) + " another-comment")


def test_signed_chain_round_trip_and_tamper_detection(tmp_path):
    private, public = _keypair(tmp_path)
    ledger = tmp_path / "ledger"
    instance_id = str(uuid.uuid4())
    chain_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    event_ref = str(uuid.uuid4())
    subject_ref = str(uuid.uuid4())

    first = append_record(
        ledger,
        instance_id=instance_id,
        chain_id=chain_id,
        record_type="instance.initialised",
        payload={},
        private_key=private,
        public_key=public,
    )
    append_record(
        ledger,
        instance_id=instance_id,
        chain_id=chain_id,
        record_type="data_subject.deletion.requested",
        payload={
            "request_id": request_id,
            "event_ref": event_ref,
            "subject_ref": subject_ref,
            "request_type": "full_erasure",
            "identity_verification": "recent_passkey_reauthentication",
            "status": "submitted",
        },
        private_key=private,
        public_key=public,
    )

    result = verify_chain(ledger, public)
    assert result["valid"] is True
    assert result["records"] == 2
    if os.name != "nt":
        assert stat_mode(first) == 0o600
        assert stat_mode(first.with_suffix(first.suffix + ".sig")) == 0o600

    record = json.loads(first.read_text(encoding="utf-8"))
    record["record_type"] = "key.rotated"
    first.write_bytes(canonical_json(record))
    with pytest.raises(EvidenceError, match="signature"):
        verify_chain(ledger, public)


def test_payload_rejects_unknown_or_free_text_fields(tmp_path):
    private, public = _keypair(tmp_path)
    common = {
        "ledger": tmp_path / "ledger",
        "instance_id": str(uuid.uuid4()),
        "chain_id": str(uuid.uuid4()),
        "record_type": "deletion.completed",
        "private_key": private,
        "public_key": public,
    }
    with pytest.raises(EvidenceError, match="unknown"):
        append_record(payload={"person_name": "Alice"}, **common)
    with pytest.raises(EvidenceError, match="enumerated"):
        append_record(payload={"error_code": "Alice said this failed"}, **common)


def test_account_consent_record_accepts_uuid5_and_remains_pseudonymous(tmp_path):
    private, public = _keypair(tmp_path)
    ledger = tmp_path / "ledger"
    instance_id = str(uuid.uuid4())
    chain_id = str(uuid.uuid4())
    subject_ref = str(uuid.uuid5(uuid.NAMESPACE_URL, "mp-opt:test:subject"))
    event_ref = str(uuid.uuid5(uuid.NAMESPACE_URL, "mp-opt:test:event"))

    append_record(
        ledger,
        instance_id=instance_id,
        chain_id=chain_id,
        record_type="instance.initialised",
        payload={},
        private_key=private,
        public_key=public,
    )
    consent_record = append_record(
        ledger,
        instance_id=instance_id,
        chain_id=chain_id,
        record_type="account.processing_consent_recorded",
        payload={
            "subject_ref": subject_ref,
            "event_ref": event_ref,
            "policy_version": 1,
            "policy_sha256": "a" * 64,
            "statement_sha256": "b" * 64,
            "document_sha256": "c" * 64,
            "signed_at": "2026-08-16T11:00:00Z",
        },
        private_key=private,
        public_key=public,
    )

    assert verify_chain(ledger, public)["records"] == 2
    payload = json.loads(consent_record.read_text(encoding="utf-8"))["payload"]
    assert payload["subject_ref"] == subject_ref
    assert payload["event_ref"] == event_ref
    assert not {"controller_identity", "email", "display_name"} & set(payload)

    with pytest.raises(EvidenceError, match="unknown"):
        append_record(
            ledger,
            instance_id=instance_id,
            chain_id=chain_id,
            record_type="account.processing_consent_recorded",
            payload={"controller_identity": "Brian Funk"},
            private_key=private,
            public_key=public,
        )


def test_every_declared_record_type_round_trips_through_the_signed_ledger(tmp_path):
    """A manifest entry is not supported until the real writer can sign it."""

    private, public = _keypair(tmp_path)
    ledger = tmp_path / "ledger"
    instance_id = str(uuid.uuid4())
    chain_id = str(uuid.uuid4())

    for record_type in sorted(RECORD_TYPES):
        append_record(
            ledger,
            instance_id=instance_id,
            chain_id=chain_id,
            record_type=record_type,
            payload={},
            private_key=private,
            public_key=public,
        )

    result = verify_chain(ledger, public)
    assert result["valid"] is True
    assert result["records"] == len(RECORD_TYPES)

    with pytest.raises(EvidenceError, match="unknown evidence record type"):
        append_record(
            ledger,
            instance_id=instance_id,
            chain_id=chain_id,
            record_type="account.unsupported_record",
            payload={},
            private_key=private,
            public_key=public,
        )


def test_application_append_calls_use_the_signed_manifest_contract():
    """Keep every literal application evidence call aligned with the tool."""

    application_root = Path(__file__).resolve().parents[1] / "backend" / "app"
    dynamic_record_types: dict[tuple[str, str], set[str]] = {}
    calls_seen = 0

    def literal_dict_keys(value: ast.AST | None) -> set[str]:
        if isinstance(value, ast.Dict):
            result = {
                key.value
                for key in value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            for key, item in zip(value.keys, value.values, strict=True):
                if key is None:
                    result.update(literal_dict_keys(item))
            return result
        if isinstance(value, ast.IfExp):
            return literal_dict_keys(value.body) | literal_dict_keys(value.orelse)
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
            return literal_dict_keys(value.left) | literal_dict_keys(value.right)
        return set()

    def local_payload_keys(
        scope: ast.FunctionDef | ast.AsyncFunctionDef,
        variable_name: str,
        before_line: int,
    ) -> set[str]:
        result: set[str] = set()
        for item in ast.walk(scope):
            if getattr(item, "lineno", before_line + 1) > before_line:
                continue
            if isinstance(item, (ast.Assign, ast.AnnAssign)):
                targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                value = item.value
                for target in targets:
                    if isinstance(target, ast.Name) and target.id == variable_name:
                        result.update(literal_dict_keys(value))
                    elif (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == variable_name
                        and isinstance(target.slice, ast.Constant)
                        and isinstance(target.slice.value, str)
                    ):
                        result.add(target.slice.value)
            elif (
                isinstance(item, ast.Call)
                and isinstance(item.func, ast.Attribute)
                and item.func.attr == "update"
                and isinstance(item.func.value, ast.Name)
                and item.func.value.id == variable_name
            ):
                for argument in item.args:
                    result.update(literal_dict_keys(argument))
                result.update(keyword.arg for keyword in item.keywords if keyword.arg)
        return result

    for path in application_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if call_name not in {"append_record", "append_evidence_record"}:
                continue
            calls_seen += 1
            keywords = {item.arg: item.value for item in node.keywords if item.arg}
            record_type = keywords.get("record_type")
            assert record_type is not None, f"{path}:{node.lineno} has no record_type"
            if isinstance(record_type, ast.Constant) and isinstance(record_type.value, str):
                assert record_type.value in RECORD_TYPES, (
                    f"{path}:{node.lineno} emits unsupported record type {record_type.value}"
                )
            else:
                scope = parents.get(node)
                while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    scope = parents.get(scope)
                assert scope is not None
                values = {
                    item.value.value
                    for item in ast.walk(scope)
                    if isinstance(item, ast.Assign)
                    for target in item.targets
                    if isinstance(target, ast.Name)
                    and isinstance(record_type, ast.Name)
                    and target.id == record_type.id
                    and isinstance(item.value, ast.Constant)
                    and isinstance(item.value.value, str)
                }
                key = (path.relative_to(application_root).as_posix(), scope.name)
                dynamic_record_types[key] = values
                assert values and values <= RECORD_TYPES, (
                    f"{path}:{node.lineno} has unbounded dynamic record types {values}"
                )

            payload = keywords.get("payload")
            literal_keys = literal_dict_keys(payload)
            if isinstance(payload, ast.Name):
                scope = parents.get(node)
                while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    scope = parents.get(scope)
                assert scope is not None
                literal_keys.update(local_payload_keys(scope, payload.id, node.lineno))
                assert literal_keys, (
                    f"{path}:{node.lineno} has an unbounded payload variable {payload.id}"
                )
            if literal_keys:
                assert literal_keys <= PAYLOAD_FIELDS, (
                    f"{path}:{node.lineno} emits unsupported payload fields "
                    f"{sorted(literal_keys - PAYLOAD_FIELDS)}"
                )

    assert calls_seen >= 30
    assert dynamic_record_types == {
        ("api/v1/evidence_keys.py", "complete_root_authorisation"): {
            "trust_key.registered",
            "trust_key.rotated",
        }
    }


def test_current_deletion_records_are_signed_and_attestation_cli_is_absent(tmp_path):
    private, public = _keypair(tmp_path)
    tool = Path(__file__).resolve().parents[1] / "deploy" / "evidence" / "evidence_manifest.py"
    ledger = tmp_path / "ledger"
    instance_id = str(uuid.uuid4())
    chain_id = str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    work_order_id = str(uuid.uuid4())
    event_ref = str(uuid.uuid4())
    subject_ref = str(uuid.uuid4())

    append_record(
        ledger,
        instance_id=instance_id,
        chain_id=chain_id,
        record_type="deletion.desktop_report_received",
        payload={
            "case_id": case_id,
            "work_order_id": work_order_id,
            "event_ref": event_ref,
            "subject_ref": subject_ref,
            "report_sha256": "a" * 64,
            "outstanding_actions": [],
            "status": "ready_for_live_purge",
        },
        private_key=private,
        public_key=public,
    )
    append_record(
        ledger,
        instance_id=instance_id,
        chain_id=chain_id,
        record_type="deletion.checklist_approved",
        payload={
            "case_id": case_id,
            "checklist_sha256": "b" * 64,
            "role": "controller",
            "approval_sha256": "c" * 64,
            "status": "ready_for_completion",
        },
        private_key=private,
        public_key=public,
    )
    assert verify_chain(ledger, public)["records"] == 2

    result = subprocess.run(
        [sys.executable, str(tool), "validate-attestation"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_clean_backup_evidence_accepts_v3_and_deletion_scoped_v4_inventory(tmp_path):
    private, public = _keypair(tmp_path)
    ledger = tmp_path / "ledger"
    instance_id = str(uuid.uuid4())
    chain_id = str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    event_ref = str(uuid.uuid4())
    replacement_package_id = str(uuid.uuid4())
    superseded_package_id = str(uuid.uuid4())
    payload = {
        "case_id": case_id,
        "event_ref": event_ref,
        "replacement_package_id": replacement_package_id,
        "replacement_package_sha256": "a" * 64,
        "receipt_sha256": "b" * 64,
        "local_snapshot_count": 1,
        "superseded_portable_package_ids": [superseded_package_id],
        "verified_at": "2026-08-05T15:00:00Z",
        "outcome": "verified",
        "status": "clean_backup_verified",
    }

    record = append_record(
        ledger,
        instance_id=instance_id,
        chain_id=chain_id,
        record_type="deletion.clean_backup_verified",
        payload=payload,
        private_key=private,
        public_key=public,
    )

    assert record.is_file()
    assert verify_chain(ledger, public)["records"] == 1

    resolution_id = str(uuid.uuid4())
    v4_payload = {
        key: value for key, value in payload.items() if key != "local_snapshot_count"
    } | {
        "retained_local_snapshot_count": 2,
        "local_resolution_id": resolution_id,
        "local_resolution_sha256": "c" * 64,
        "superseded_local_snapshot_receipt_sha256s": ["d" * 64],
    }
    append_record(
        ledger,
        instance_id=instance_id,
        chain_id=chain_id,
        record_type="deletion.clean_backup_verified",
        payload=v4_payload,
        private_key=private,
        public_key=public,
    )
    assert verify_chain(ledger, public)["records"] == 2

    with pytest.raises(EvidenceError, match="exactly one"):
        append_record(
            ledger,
            instance_id=instance_id,
            chain_id=chain_id,
            record_type="deletion.clean_backup_verified",
            payload={**payload, "local_snapshot_count": 2},
            private_key=private,
            public_key=public,
        )

    with pytest.raises(EvidenceError, match="UUID"):
        append_record(
            ledger,
            instance_id=instance_id,
            chain_id=chain_id,
            record_type="deletion.clean_backup_verified",
            payload={**payload, "superseded_portable_package_ids": ["not-a-package-id"]},
            private_key=private,
            public_key=public,
        )

    with pytest.raises(EvidenceError, match="positive snapshot count"):
        append_record(
            ledger,
            instance_id=instance_id,
            chain_id=chain_id,
            record_type="deletion.clean_backup_verified",
            payload={**v4_payload, "retained_local_snapshot_count": 0},
            private_key=private,
            public_key=public,
        )


def test_archive_completion_receipt_is_bounded_and_signed(tmp_path):
    private, public = _keypair(tmp_path)
    ledger = tmp_path / "ledger"
    record = append_record(
        ledger,
        instance_id=str(uuid.uuid4()),
        chain_id=str(uuid.uuid4()),
        record_type="evidence.git_archive_completed",
        payload={
            "submission_id": "sub-" + "a" * 32,
            "archive_repository_id": "42",
            "controller_id": "ctl-controller000001",
            "bundle_id": str(uuid.uuid4()),
            "bundle_sha256": "b" * 64,
            "chain_head_sha256": "c" * 64,
            "pull_request_number": 7,
            "pull_request_head_sha": "d" * 40,
            "merge_commit_sha": "e" * 40,
            "completed_at": "2026-07-31T12:00:00Z",
            "archive_status": "verified",
        },
        private_key=private,
        public_key=public,
    )
    assert record.is_file()
    assert verify_chain(ledger, public)["records"] == 1
    with pytest.raises(EvidenceError, match="Git object digest"):
        append_record(
            ledger,
            instance_id=json.loads(record.read_text(encoding="utf-8"))["instance_id"],
            chain_id=json.loads(record.read_text(encoding="utf-8"))["chain_id"],
            record_type="evidence.git_archive_completed",
            payload={"merge_commit_sha": "not-a-sha"},
            private_key=private,
            public_key=public,
        )


def stat_mode(path):
    return os.stat(path).st_mode & 0o777
