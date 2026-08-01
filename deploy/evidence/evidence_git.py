#!/usr/bin/env python3
"""Verify and render a controller-owned accountability evidence Git repository.

The repository contains public accountability evidence only. It has no network
client and never pushes. A valid signature proves who signed the exact bytes. It
does not prove physical deletion, absence of other copies, or legal compliance.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

import evidence_manifest


CONTROLLER_FORMAT = "mp-opt-controller-trust-v1"
PROCESSOR_FORMAT = "mp-opt-processor-trust-v1"
INSTANCE_FORMAT = "mp-opt-instance-trust-v1"
CONTROLLER_ID_RE = re.compile(r"^ctl-[a-z0-9]{16}$")
PROCESSOR_ID_RE = re.compile(r"^proc-[a-z0-9]{16}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/+()]{0,127}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
EMAIL_RE = re.compile(rb"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
PRIVATE_KEY_RE = re.compile(rb"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----")
SECRET_RE = re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*[^\s,;]{8,}")
RECORD_FILE_RE = re.compile(r"^[0-9]{12}_[0-9a-f-]{36}\.json$")
ROOT_FILES = {"README.md", "CONTROLLER.md", "PROCESSORS.md", "CODEOWNERS"}
TEMPLATE_SCHEMA_FILES = {
    "schemas/controller.schema.json",
    "schemas/processor.schema.json",
    "schemas/instance.schema.json",
}
TOOL_FILES = {
    "tools/evidence_archive_repository.py",
    "tools/evidence_git.py",
    "tools/evidence_manifest.py",
    "tools/portable_bundle.py",
    "tools/verify_evidence_repo.py",
    "tools/scan_evidence_repo.py",
    "tools/validate_ingestion_paths.py",
    "tools/render_human_summary.py",
}
STATUS_LABELS = {
    "verified": "Verified",
    "missing": "Missing",
    "pending": "Pending",
    "failed": "Failed",
    "blocked": "Blocked",
    "requires_controller_action": "Requires controller action",
    "superseded": "Superseded",
    "revoked": "Revoked",
    "historic": "Historic verification only",
}


class EvidenceGitError(ValueError):
    """Raised when a Git evidence repository is unsafe or unverifiable."""


def canonical_json(value: dict[str, Any]) -> bytes:
    return evidence_manifest.canonical_json(value)


def _load_canonical(path: Path) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise EvidenceGitError(f"Missing or unsafe file: {path}")
    try:
        raw = path.read_bytes()
        value = evidence_manifest.load_json_bytes(raw)
        canonical = canonical_json(value)
    except (OSError, evidence_manifest.EvidenceError) as exc:
        raise EvidenceGitError(f"Invalid JSON file: {path}") from exc
    if raw != canonical:
        raise EvidenceGitError(f"JSON is not canonical: {path}")
    return value, raw


def _exact_fields(value: dict[str, Any], required: set[str], label: str) -> None:
    if set(value) != required:
        raise EvidenceGitError(f"{label} fields are invalid")


def _safe_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SAFE_VALUE_RE.fullmatch(value):
        raise EvidenceGitError(f"{field} is invalid")
    return value


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise EvidenceGitError(f"{field} is not a UTC timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise EvidenceGitError(f"{field} is not a valid UTC timestamp") from exc
    return value


def _verify_signature(path: Path, public_key_path: Path) -> None:
    try:
        evidence_manifest.verify_file(path, Path(str(path) + ".sig"), public_key_path)
    except (OSError, evidence_manifest.EvidenceError) as exc:
        raise EvidenceGitError(f"Signature verification failed: {path}") from exc


def _validate_controller(root: Path) -> dict[str, Any]:
    path = root / "trust" / "controller.json"
    value, _raw = _load_canonical(path)
    required = {
        "format", "controller_id", "display_name", "jurisdiction",
        "signing_key_id", "signing_public_key", "revoked_key_ids", "status", "signed_at",
    }
    _exact_fields(value, required, "controller")
    if value["format"] != CONTROLLER_FORMAT or not CONTROLLER_ID_RE.fullmatch(value.get("controller_id", "")):
        raise EvidenceGitError("Controller format or ID is invalid")
    _safe_text(value["display_name"], "controller display_name")
    if not COUNTRY_RE.fullmatch(value.get("jurisdiction", "")):
        raise EvidenceGitError("Controller jurisdiction must be an ISO 3166-1 alpha-2 code")
    public = evidence_manifest.canonical_public_key(value.get("signing_public_key", ""))
    if value.get("signing_key_id") != evidence_manifest.key_id(public):
        raise EvidenceGitError("Controller signing key ID does not match its public key")
    revoked = value.get("revoked_key_ids")
    if not isinstance(revoked, list) or revoked != sorted(set(revoked)):
        raise EvidenceGitError("Controller revoked key IDs must be sorted and unique")
    if any(not evidence_manifest.KEY_ID_RE.fullmatch(item) for item in revoked):
        raise EvidenceGitError("Controller revoked key ID is invalid")
    if value.get("status") != "active" or value["signing_key_id"] in revoked:
        raise EvidenceGitError("Controller signing key is revoked or inactive")
    _timestamp(value.get("signed_at"), "controller signed_at")
    public_path = root / "trust" / "controller.pub"
    if public_path.read_text(encoding="ascii").strip() != public:
        raise EvidenceGitError("Controller public key file does not match controller.json")
    _verify_signature(path, public_path)
    return value


def _validate_processors(root: Path, controller: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    directory = root / "trust" / "processors"
    if not directory.is_dir() or directory.is_symlink():
        raise EvidenceGitError("Processor trust directory is missing or unsafe")
    for path in sorted(directory.glob("*.json")):
        value, _raw = _load_canonical(path)
        required = {
            "format", "processor_id", "controller_id", "display_name", "service_categories",
            "countries", "transfer_basis", "active_from", "active_until", "status", "signed_at",
        }
        _exact_fields(value, required, "processor")
        processor_id = value.get("processor_id", "")
        if value["format"] != PROCESSOR_FORMAT or not PROCESSOR_ID_RE.fullmatch(processor_id):
            raise EvidenceGitError(f"Processor format or ID is invalid: {path}")
        if processor_id in result:
            raise EvidenceGitError(f"Conflicting processor ID: {processor_id}")
        if path.stem != processor_id:
            raise EvidenceGitError(f"Processor filename does not match its ID: {path}")
        if value.get("controller_id") != controller["controller_id"]:
            raise EvidenceGitError(f"Processor has the wrong controller: {processor_id}")
        _safe_text(value["display_name"], "processor display_name")
        categories = value.get("service_categories")
        countries = value.get("countries")
        if not isinstance(categories, list) or not categories or categories != sorted(set(categories)):
            raise EvidenceGitError(f"Processor categories are invalid: {processor_id}")
        if any(not evidence_manifest.SAFE_ENUM_RE.fullmatch(item) for item in categories):
            raise EvidenceGitError(f"Processor category is invalid: {processor_id}")
        if not isinstance(countries, list) or not countries or countries != sorted(set(countries)):
            raise EvidenceGitError(f"Processor countries are invalid: {processor_id}")
        if any(not COUNTRY_RE.fullmatch(item) for item in countries):
            raise EvidenceGitError(f"Processor country is invalid: {processor_id}")
        if not evidence_manifest.SAFE_ENUM_RE.fullmatch(value.get("transfer_basis", "")):
            raise EvidenceGitError(f"Processor transfer basis is invalid: {processor_id}")
        _timestamp(value.get("active_from"), "processor active_from")
        if value.get("active_until") is not None:
            _timestamp(value["active_until"], "processor active_until")
            if value["active_until"] < value["active_from"]:
                raise EvidenceGitError(f"Processor active interval is invalid: {processor_id}")
        if value.get("status") not in {"active", "revoked", "historic"}:
            raise EvidenceGitError(f"Processor status is invalid: {processor_id}")
        _timestamp(value.get("signed_at"), "processor signed_at")
        _verify_signature(path, root / "trust" / "controller.pub")
        result[processor_id] = value
    return result


def _validate_instance(root: Path, directory: Path, controller: dict[str, Any], processors: dict[str, dict[str, Any]]) -> dict[str, Any]:
    instance_id = directory.name
    if not UUID_RE.fullmatch(instance_id):
        raise EvidenceGitError(f"Instance directory is not a canonical UUID: {directory}")
    trust_path = directory / "trust" / "instance.json"
    value, _raw = _load_canonical(trust_path)
    required = {
        "format", "instance_id", "controller_id", "signing_key_id", "signing_public_key",
        "processor_ids", "status", "signed_at",
    }
    _exact_fields(value, required, "instance trust")
    if value["format"] != INSTANCE_FORMAT or value.get("instance_id") != instance_id:
        raise EvidenceGitError(f"Instance trust identity is invalid: {instance_id}")
    if value.get("controller_id") != controller["controller_id"]:
        raise EvidenceGitError(f"Instance has the wrong controller: {instance_id}")
    public = evidence_manifest.canonical_public_key(value.get("signing_public_key", ""))
    key_id = evidence_manifest.key_id(public)
    if value.get("signing_key_id") != key_id:
        raise EvidenceGitError(f"Instance signing key ID does not match: {instance_id}")
    if key_id in controller["revoked_key_ids"] or value.get("status") != "active":
        raise EvidenceGitError(f"Instance signing key is revoked or inactive: {instance_id}")
    processor_ids = value.get("processor_ids")
    if not isinstance(processor_ids, list) or processor_ids != sorted(set(processor_ids)):
        raise EvidenceGitError(f"Instance processor IDs are invalid: {instance_id}")
    for processor_id in processor_ids:
        processor = processors.get(processor_id)
        if processor is None:
            raise EvidenceGitError(f"Instance references a missing processor: {processor_id}")
        if processor["status"] != "active":
            raise EvidenceGitError(f"Instance references a non-active processor: {processor_id}")
    _timestamp(value.get("signed_at"), "instance signed_at")
    _verify_signature(trust_path, root / "trust" / "controller.pub")
    key_path = directory / "trust" / "instance.pub"
    if key_path.read_text(encoding="ascii").strip() != public:
        raise EvidenceGitError(f"Instance public key file does not match trust record: {instance_id}")
    ledger = directory / "ledger"
    if not ledger.is_dir() or ledger.is_symlink():
        raise EvidenceGitError(f"Instance ledger is missing or unsafe: {instance_id}")
    try:
        chain = evidence_manifest.verify_chain(ledger, key_path)
    except (OSError, evidence_manifest.EvidenceError) as exc:
        raise EvidenceGitError(f"Instance chain verification failed: {instance_id}") from exc
    records: list[dict[str, Any]] = []
    for path in sorted(ledger.glob("*.json")):
        if path.name == "chain-head.json":
            continue
        if not RECORD_FILE_RE.fullmatch(path.name):
            raise EvidenceGitError(f"Unexpected ledger filename: {path}")
        record, _raw = evidence_manifest.load_record(path)
        if record["instance_id"] != instance_id or record["signer"]["key_id"] != key_id:
            raise EvidenceGitError(f"Ledger identity or signer mismatch: {path}")
        records.append(record)
    for category in ("requests", "purges", "attestations", "backups", "anchors"):
        evidence_directory = directory / category
        if not evidence_directory.is_dir() or evidence_directory.is_symlink():
            raise EvidenceGitError(f"Instance evidence directory is missing or unsafe: {instance_id}/{category}")
        documents = sorted(evidence_directory.glob("*.json"))
        signatures = {path.name for path in evidence_directory.glob("*.json.sig")}
        if signatures != {path.name + ".sig" for path in documents}:
            raise EvidenceGitError(f"Evidence signatures are missing or orphaned: {instance_id}/{category}")
        for path in documents:
            if not RECORD_FILE_RE.fullmatch(path.name):
                raise EvidenceGitError(f"Unexpected evidence filename: {path}")
            try:
                record, _raw = evidence_manifest.load_record(path)
                evidence_manifest.verify_file(path, Path(str(path) + ".sig"), key_path)
            except (OSError, evidence_manifest.EvidenceError) as exc:
                raise EvidenceGitError(f"Signed evidence verification failed: {path}") from exc
            if record["instance_id"] != instance_id or record["signer"]["key_id"] != key_id:
                raise EvidenceGitError(f"Signed evidence identity or signer mismatch: {path}")
    return {"trust": value, "chain": chain, "records": records}


def scan_repository(root: Path) -> list[str]:
    """Return deterministic findings for prohibited data and unsafe files."""

    findings: list[str] = []
    exempt_parts = {".git", "tools", "schemas", ".github"}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if any(part in exempt_parts for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            findings.append(f"symlink:{relative}")
            continue
        if not path.is_file():
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            findings.append(f"oversized:{relative}")
            continue
        raw = path.read_bytes()
        if PRIVATE_KEY_RE.search(raw):
            findings.append(f"private-key:{relative}")
        if SECRET_RE.search(raw):
            findings.append(f"secret:{relative}")
        if EMAIL_RE.search(raw):
            findings.append(f"personal-email:{relative}")
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".bak", ".zip", ".tar", ".gz"}:
            findings.append(f"forbidden-artifact:{relative}")
    return sorted(set(findings))


def _allowed_repository_file(relative: str) -> bool:
    if relative in ROOT_FILES | TEMPLATE_SCHEMA_FILES | TOOL_FILES | {
        ".github/workflows/verify-evidence.yml",
        "trust/controller.json",
        "trust/controller.json.sig",
        "trust/controller.pub",
        "trust/processors/README.md",
        "instances/README.md",
    }:
        return True
    if re.fullmatch(r"trust/processors/proc-[a-z0-9]{16}\.json(?:\.sig)?", relative):
        return True
    prefix = r"instances/[0-9a-f-]{36}"
    if re.fullmatch(prefix + r"/trust/(?:instance\.json(?:\.sig)?|instance\.pub)", relative):
        return True
    if re.fullmatch(prefix + r"/ledger/(?:\.append\.lock|chain-head\.json|[0-9]{12}_[0-9a-f-]{36}\.json(?:\.sig)?)", relative):
        return True
    if re.fullmatch(prefix + r"/(?:requests|purges|attestations|backups|anchors)/[0-9]{12}_[0-9a-f-]{36}\.json(?:\.sig)?", relative):
        return True
    if re.fullmatch(prefix + r"/summaries/evidence-summary\.(?:md|html)", relative):
        return True
    return False


def _verify_closed_schema(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        relative_path = path.relative_to(root)
        if ".git" in relative_path.parts or "__pycache__" in relative_path.parts or path.is_dir():
            continue
        relative = relative_path.as_posix()
        if path.is_symlink() or not _allowed_repository_file(relative):
            raise EvidenceGitError(f"Undeclared or unsafe repository file: {relative}")


def _git_controller_ids(root: Path) -> set[str]:
    if not (root / ".git").exists():
        return set()
    history = subprocess.run(
        ["git", "log", "--all", "--format=%H", "--", "trust/controller.json"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if history.returncode != 0:
        raise EvidenceGitError("Git controller history could not be inspected")
    controller_ids: set[str] = set()
    for commit in history.stdout.splitlines():
        shown = subprocess.run(
            ["git", "show", f"{commit}:trust/controller.json"],
            cwd=root, capture_output=True, check=False,
        )
        if shown.returncode != 0:
            raise EvidenceGitError("Controller trust was removed in Git history")
        try:
            controller_ids.add(evidence_manifest.load_json_bytes(shown.stdout)["controller_id"])
        except (KeyError, evidence_manifest.EvidenceError) as exc:
            raise EvidenceGitError("Invalid controller trust in Git history") from exc
    return controller_ids


def verify_repository(root: Path, *, check_summaries: bool = True) -> dict[str, Any]:
    if root.is_symlink() or not root.is_dir():
        raise EvidenceGitError("Evidence repository is unavailable or unsafe")
    root = root.resolve()
    findings = scan_repository(root)
    if findings:
        raise EvidenceGitError("Repository safety scan failed: " + ", ".join(findings))
    _verify_closed_schema(root)
    controller = _validate_controller(root)
    historical_ids = _git_controller_ids(root)
    if historical_ids and historical_ids != {controller["controller_id"]}:
        raise EvidenceGitError("Controller identity changed in Git history; use a new repository")
    processors = _validate_processors(root, controller)
    instances_dir = root / "instances"
    if not instances_dir.is_dir() or instances_dir.is_symlink():
        raise EvidenceGitError("Instances directory is missing or unsafe")
    instances: dict[str, dict[str, Any]] = {}
    for directory in sorted(path for path in instances_dir.iterdir() if path.is_dir()):
        instances[directory.name] = _validate_instance(root, directory, controller, processors)
    result = {
        "valid": True,
        "controller": controller,
        "processors": processors,
        "instances": instances,
        "chain_health": "verified",
    }
    if check_summaries:
        for instance_id in instances:
            summaries = instances_dir / instance_id / "summaries"
            markdown_path = summaries / "evidence-summary.md"
            html_path = summaries / "evidence-summary.html"
            if markdown_path.exists() and markdown_path.read_text(encoding="utf-8") != render_markdown(result, instance_id):
                raise EvidenceGitError(f"Derived Markdown summary is stale: {instance_id}")
            if html_path.exists() and html_path.read_text(encoding="utf-8") != render_html(result, instance_id):
                raise EvidenceGitError(f"Derived HTML summary is stale: {instance_id}")
    return result


def _record_status(record_type: str) -> str:
    if record_type.endswith(("requested", "created")):
        return "Pending"
    if record_type.endswith(("rejected", "withdrawn")):
        return "Blocked"
    if record_type.endswith(("purged", "completed", "confirmed", "verified", "resolved", "accepted", "revoked")):
        return "Verified"
    return "Historic verification only"


def _summary_rows(result: dict[str, Any], instance_id: str) -> list[tuple[str, str, str]]:
    records = result["instances"][instance_id]["records"]
    return [
        (str(record["sequence"]), record["record_type"], _record_status(record["record_type"]))
        for record in records
    ]


def render_markdown(result: dict[str, Any], instance_id: str) -> str:
    controller = result["controller"]
    instance = result["instances"][instance_id]
    processor_ids = instance["trust"]["processor_ids"]
    lines = [
        "# Accountability evidence summary",
        "",
        "Status: Verified",
        "",
        f"Controller ID: `{controller['controller_id']}`",
        f"Instance ID: `{instance_id}`",
        f"Controller key ID: `{controller['signing_key_id']}`",
        f"Instance key ID: `{instance['trust']['signing_key_id']}`",
        f"Chain health: {STATUS_LABELS[result['chain_health']]}",
        "",
        "## Active processors",
        "",
    ]
    if processor_ids:
        for processor_id in processor_ids:
            processor = result["processors"][processor_id]
            lines.append(
                f"- `{processor_id}`: {processor['display_name']} ({', '.join(processor['service_categories'])}; "
                f"countries {', '.join(processor['countries'])}; transfer basis {processor['transfer_basis']})"
            )
    else:
        lines.append("- None declared for this instance.")
    lines += ["", "## Verified timeline", "", "| Sequence | Record | Status |", "| ---: | --- | --- |"]
    for sequence, record_type, status in _summary_rows(result, instance_id):
        lines.append(f"| {sequence} | `{record_type}` | {status} |")
    lines += [
        "",
        "## Verification limits",
        "",
        "A valid signature proves that the identified key signed the exact statement shown. "
        "It does not prove physical deletion, absence of copies outside controlled systems, or legal compliance.",
        "",
    ]
    return "\n".join(lines)


def render_html(result: dict[str, Any], instance_id: str) -> str:
    controller = result["controller"]
    instance = result["instances"][instance_id]
    processors = "".join(
        f"<li><code>{html.escape(processor_id)}</code>: {html.escape(result['processors'][processor_id]['display_name'])}</li>"
        for processor_id in instance["trust"]["processor_ids"]
    ) or "<li>None declared for this instance.</li>"
    rows = "".join(
        f"<tr><td>{html.escape(sequence)}</td><td><code>{html.escape(record_type)}</code></td><td>{html.escape(status)}</td></tr>"
        for sequence, record_type, status in _summary_rows(result, instance_id)
    )
    return (
        "<!doctype html>\n<html lang=\"en-GB\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Accountability evidence summary</title>"
        "<style>body{font:16px/1.5 system-ui;max-width:72rem;margin:auto;padding:2rem;color:#17202a}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #85929e;padding:.5rem;text-align:left}"
        "th{background:#eef2f5}code{overflow-wrap:anywhere}.status{font-weight:700;color:#176b3a}"
        "@media(prefers-color-scheme:dark){body{background:#111827;color:#f3f4f6}th{background:#263244}}</style>"
        "</head><body><main><h1>Accountability evidence summary</h1><p class=\"status\">Status: Verified</p>"
        f"<dl><dt>Controller ID</dt><dd><code>{html.escape(controller['controller_id'])}</code></dd>"
        f"<dt>Instance ID</dt><dd><code>{html.escape(instance_id)}</code></dd>"
        f"<dt>Controller key ID</dt><dd><code>{html.escape(controller['signing_key_id'])}</code></dd>"
        f"<dt>Instance key ID</dt><dd><code>{html.escape(instance['trust']['signing_key_id'])}</code></dd>"
        "<dt>Chain health</dt><dd>Verified</dd></dl><h2>Active processors</h2>"
        f"<ul>{processors}</ul><h2>Verified timeline</h2><table><thead><tr><th scope=\"col\">Sequence</th>"
        f"<th scope=\"col\">Record</th><th scope=\"col\">Status</th></tr></thead><tbody>{rows}</tbody></table>"
        "<h2>Verification limits</h2><p>A valid signature proves that the identified key signed the exact statement shown. "
        "It does not prove physical deletion, absence of copies outside controlled systems, or legal compliance.</p>"
        "</main></body></html>\n"
    )


def write_summaries(root: Path, result: dict[str, Any] | None = None) -> list[Path]:
    result = result or verify_repository(root, check_summaries=False)
    written: list[Path] = []
    for instance_id in result["instances"]:
        directory = root / "instances" / instance_id / "summaries"
        directory.mkdir(parents=True, exist_ok=True)
        markdown_path = directory / "evidence-summary.md"
        html_path = directory / "evidence-summary.html"
        markdown_path.write_text(render_markdown(result, instance_id), encoding="utf-8", newline="\n")
        html_path.write_text(render_html(result, instance_id), encoding="utf-8", newline="\n")
        written.extend((markdown_path, html_path))
    return written


def export_template(source_root: Path, destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise EvidenceGitError("Template destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    template = source_root / "deploy" / "evidence" / "git-template"
    shutil.copytree(template, destination, dirs_exist_ok=True)
    tools = destination / "tools"
    tools.mkdir(exist_ok=True)
    for name in ("verify_evidence_repo.py", "scan_evidence_repo.py", "validate_ingestion_paths.py"):
        shutil.copyfile(source_root / "tools" / name, tools / name)
    for name in (
        "evidence_archive_repository.py", "evidence_git.py", "evidence_manifest.py",
        "portable_bundle.py",
    ):
        shutil.copyfile(source_root / "deploy" / "evidence" / name, tools / name)


def cli(command: str, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    try:
        if command == "scan":
            findings = scan_repository(arguments.repository.resolve())
            if findings:
                raise EvidenceGitError("Repository safety scan failed: " + ", ".join(findings))
            print("safe")
        elif command == "verify":
            result = verify_repository(arguments.repository)
            print(json.dumps({"valid": True, "instances": len(result["instances"]), "controller_id": result["controller"]["controller_id"]}, sort_keys=True))
        elif command == "render":
            result = verify_repository(arguments.repository, check_summaries=False)
            paths = write_summaries(arguments.repository.resolve(), result)
            print("\n".join(str(path) for path in paths))
        else:
            raise EvidenceGitError(f"Unknown command: {command}")
    except (EvidenceGitError, OSError) as exc:
        print(f"evidence {command} failed: {exc}", file=sys.stderr)
        return 1
    return 0
