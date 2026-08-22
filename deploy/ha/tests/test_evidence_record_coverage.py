"""Keep every production evidence record classified for the staging laboratory."""

from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "backend" / "app"
MANIFEST = Path(__file__).with_name("evidence_record_coverage.json")
APPEND_NAMES = {"EvidenceOperation", "append_record", "append_evidence_record"}
ALLOWED_PACKS = {
    "accounts.activation-legal-bases",
    "commissioning.all-interruptions",
    "deletion.retention",
    "evidence.git",
    "evidence.ledger",
    "publishing.desktop",
    "tenancy.isolation",
}


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _assigned_string_values(tree: ast.AST) -> dict[str, set[str]]:
    values: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                values.setdefault(target.id, set()).add(value.value)
    return values


def production_record_types() -> set[str]:
    discovered: set[str] = set()
    unresolved: list[str] = []
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        assigned = _assigned_string_values(tree)
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            if _call_name(call.func) not in APPEND_NAMES:
                continue
            keyword = next((item for item in call.keywords if item.arg == "record_type"), None)
            if keyword is None:
                continue
            value = keyword.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                discovered.add(value.value)
            elif isinstance(value, ast.Name):
                # core.evidence.append_record is the shared sink. Its dynamic
                # parameter is classified at every concrete producer instead.
                if path.name == "evidence.py" and value.id == "record_type":
                    continue
                candidates = assigned.get(value.id, set())
                if candidates:
                    discovered.update(candidates)
                else:
                    unresolved.append(f"{path.relative_to(ROOT)}:{call.lineno}:{value.id}")
            else:
                # Recovery reconstructs the two initialisation outbox rows
                # from an already verified first ledger record. The concrete
                # producer constants above remain the coverage source.
                if path.name == "evidence.py" and _call_name(call.func) == "EvidenceOperation":
                    continue
                unresolved.append(f"{path.relative_to(ROOT)}:{call.lineno}:expression")
    assert not unresolved, "unclassified dynamic evidence record producers: " + ", ".join(unresolved)
    return discovered


def test_every_production_evidence_record_has_a_real_laboratory_case() -> None:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert document["format"] == "mp-opt-evidence-laboratory-coverage-v1"
    records = document["records"]
    assert set(records) == production_record_types()
    for record_type, coverage in records.items():
        assert record_type and len(record_type) <= 64
        assert coverage["pack"] in ALLOWED_PACKS
        assert isinstance(coverage["case"], str) and coverage["case"]
        assert isinstance(coverage["assertions"], list) and coverage["assertions"]
        assert len(coverage["assertions"]) == len(set(coverage["assertions"]))
