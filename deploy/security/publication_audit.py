#!/usr/bin/env python3
"""Fail closed when a public source tree contains private deployment material."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

try:
    from .legal_claim_lint import audit_public_claims
except ImportError:  # Direct script execution keeps the security directory on sys.path.
    from legal_claim_lint import audit_public_claims


FORBIDDEN_NAMES = {
    ".codex-cloudflare-prod.ps1",
    ".env",
    "root_bootstrap_token",
    "smtp_token",
    "secret_key",
    "vapid_private_key",
    "codex_progress.md",
    "codex_handoff.md",
    "final_validation_report.md",
    "gdpr_technical_report.md",
    "migration_report.md",
    "security_report.md",
}
FORBIDDEN_PATH_PREFIXES = (".agents/", ".codex/", ".codex-temp/", "notes/")
FORBIDDEN_SUFFIXES = {
    ".7z", ".age", ".bak", ".db", ".dump", ".gz", ".p12", ".pem",
    ".pfx", ".rar", ".sqlite", ".sqlite3", ".tar", ".tgz", ".zip",
}
APPROVED_LICENSE_SHA256 = "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"
SPDX_LICENSE = "AGPL-3.0-only"
REQUIRED_PUBLIC_FILES = (
    "BRANDING.md",
    "CONTRIBUTING.md",
    "COPYRIGHT-AND-CONTRIBUTION-PROVENANCE.md",
    "LICENSE",
    "SECURITY.md",
    "SUPPORTED-VERSIONS.md",
    "THIRD-PARTY-NOTICES.md",
    "SELF-HOSTING-DATA-PROTECTION.md",
    "PERMITTED-DATA-AND-ACCEPTABLE-USE.md",
    "CONTROLLER-AND-OPERATOR-CHECKLIST.md",
    "SUPPORT-DATA-POLICY.md",
)
SECRET_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----")),
    ("age identity", re.compile(r"AGE-SECRET-KEY-1[0-9A-Z]+")),
    ("GitHub token", re.compile(r"(?:github_pat_|ghp_)[A-Za-z0-9_]{20,}")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("service token", re.compile(r"\b(?:xox[baprs]-[A-Za-z0-9-]{20,})\b")),
    ("secret assignment", re.compile(r"(?m)^(?:CLOUDFLARE_API_TOKEN|SMTP_TOKEN|ROOT_BOOTSTRAP_TOKEN|SECRET_KEY)=[^\s#]{16,}$")),
)
EVIDENCE_PATH_PARTS = {"attestations", "evidence", "records", "trust"}
EVIDENCE_PERSONAL_DATA_PATTERNS = (
    ("email address", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    ("forbidden evidence field", re.compile(
        r'(?i)["\'](?:email|full_name|ip_address|location|name|passkey_id|private_key|session_id)["\']\s*:'
    )),
)
TEXT_LIMIT = 5 * 1024 * 1024
ACTION_REFERENCE = re.compile(
    r"^\s*(?:-\s*)?uses:\s*([^\s#]+)",
)
IMMUTABLE_ACTION_COMMIT = re.compile(r"^[0-9a-f]{40}$")
IMMUTABLE_IMAGE_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
RELEASE_DOCKERFILES = (
    "infra/Dockerfile",
    "infra/Dockerfile.caddy",
    "infra/Dockerfile.postgres",
    "infra/Dockerfile.tools",
)


def forbidden_path_reason(relative: str) -> str | None:
    normalised = relative.replace("\\", "/").removeprefix("./")
    lower = normalised.lower()
    if any(lower.startswith(prefix) for prefix in FORBIDDEN_PATH_PREFIXES):
        return "internal path"
    path = Path(normalised)
    if path.name.lower() in FORBIDDEN_NAMES:
        return "forbidden name"
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return f"forbidden {path.suffix.lower()} artefact"
    return None


def normalised_license_sha256(path: Path) -> str:
    content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def audit_text(
    relative: str,
    content: str,
    ignored_secret_labels: frozenset[str] = frozenset(),
) -> list[str]:
    """Scan textual source without ever returning the matched value."""

    failures: list[str] = []
    normalised = relative.replace("\\", "/").removeprefix("./")
    for label, pattern in SECRET_PATTERNS:
        if label in ignored_secret_labels:
            continue
        if pattern.search(content):
            failures.append(f"secret-like {label}: {normalised}")
            break
    parts = set(Path(normalised).parts)
    if parts & EVIDENCE_PATH_PARTS and Path(normalised).suffix.lower() in {".json", ".jsonl"}:
        for label, pattern in EVIDENCE_PERSONAL_DATA_PATTERNS:
            if pattern.search(content):
                failures.append(f"evidence contains {label}: {normalised}")
                break
    return failures


def verify_scanner_fixture(path: Path) -> list[str]:
    """Verify the shared safe/unsafe adversarial corpus."""

    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("format") != "masterplan-security-scanner-fixture-v1":
        return ["unsupported scanner fixture format"]
    failures: list[str] = []
    for item in document.get("safe", []):
        observed = []
        reason = forbidden_path_reason(item["path"])
        if reason:
            observed.append(f"{reason}: {item['path']}")
        observed.extend(audit_text(item["path"], item.get("content", "")))
        if observed:
            failures.append(f"safe fixture {item['id']} was rejected: {observed[0]}")
    for item in document.get("unsafe", []):
        observed = []
        if item.get("scope") == "history":
            reason = forbidden_path_reason(item["path"])
            if reason:
                observed.append(f"forbidden historical path requires clean export: {item['path']}")
        else:
            reason = forbidden_path_reason(item["path"])
            if reason:
                observed.append(f"{reason}: {item['path']}")
            observed.extend(audit_text(item["path"], item.get("content", "")))
        if not any(item["expected"] in finding for finding in observed):
            failures.append(f"unsafe fixture {item['id']} did not produce {item['expected']}")
    return failures


def audit_license_metadata(root: Path) -> list[str]:
    failures: list[str] = []
    licence = root / "LICENSE"
    if not licence.is_file() or normalised_license_sha256(licence) != APPROVED_LICENSE_SHA256:
        failures.append("LICENSE does not match the approved GNU AGPLv3 text")
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        if project.get("project", {}).get("license") != SPDX_LICENSE:
            failures.append(f"pyproject.toml licence must be {SPDX_LICENSE}")
    except (OSError, tomllib.TOMLDecodeError) as error:
        failures.append(f"invalid pyproject.toml: {error}")
    for relative in (
        "web/package.json",
        "web/package-lock.json",
        "infra/cloudflare-ha-witness/package.json",
        "infra/cloudflare-ha-witness/package-lock.json",
    ):
        try:
            document = json.loads((root / relative).read_text(encoding="utf-8"))
            value = document.get("license") if relative.endswith("package.json") else document.get("packages", {}).get("", {}).get("license")
            if value != SPDX_LICENSE:
                failures.append(f"{relative} licence must be {SPDX_LICENSE}")
        except (OSError, json.JSONDecodeError) as error:
            failures.append(f"invalid {relative}: {error}")
    legal_copy = root / "web" / "legal-artifacts" / "LICENSE"
    if not legal_copy.is_file() or normalised_license_sha256(legal_copy) != APPROVED_LICENSE_SHA256:
        failures.append("web/legal-artifacts/LICENSE must be an exact copy of LICENSE")
    notice = root / "THIRD-PARTY-NOTICES.md"
    notice_copy = root / "web" / "legal-artifacts" / "THIRD-PARTY-NOTICES.md"
    if not notice.is_file() or not notice_copy.is_file() or notice.read_bytes() != notice_copy.read_bytes():
        failures.append("web/legal-artifacts/THIRD-PARTY-NOTICES.md must exactly match the root notice")
    return failures


def audit_immutable_build_inputs(root: Path) -> list[str]:
    """Require reviewed immutable identities for release-chain dependencies."""

    failures: list[str] = []
    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        for path in sorted((*workflows.glob("*.yml"), *workflows.glob("*.yaml"))):
            relative = path.relative_to(root).as_posix()
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1,
            ):
                match = ACTION_REFERENCE.match(line)
                if match is None:
                    continue
                reference = match.group(1)
                if reference.startswith("./"):
                    continue
                if reference.startswith("docker://"):
                    immutable = bool(IMMUTABLE_IMAGE_DIGEST.search(reference))
                else:
                    action, separator, revision = reference.rpartition("@")
                    immutable = bool(
                        action and separator and IMMUTABLE_ACTION_COMMIT.fullmatch(revision)
                    )
                if not immutable:
                    failures.append(
                        f"GitHub Action is not pinned to a full commit SHA: "
                        f"{relative}:{line_number}"
                    )

    for relative in RELEASE_DOCKERFILES:
        path = root / relative
        if not path.is_file():
            continue
        stage_aliases: set[str] = set()
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1,
        ):
            parts = line.strip().split()
            if not parts or parts[0].upper() != "FROM":
                continue
            index = 1
            while index < len(parts) and parts[index].startswith("--"):
                index += 1
            if index >= len(parts):
                failures.append(f"invalid FROM instruction: {relative}:{line_number}")
                continue
            image = parts[index]
            if (
                image != "scratch"
                and image not in stage_aliases
                and not IMMUTABLE_IMAGE_DIGEST.search(image)
            ):
                failures.append(
                    f"release base image is not pinned to a SHA-256 digest: "
                    f"{relative}:{line_number}"
                )
            if index + 2 < len(parts) and parts[index + 1].upper() == "AS":
                stage_aliases.add(parts[index + 2])
    return failures


def tracked_files(root: Path) -> list[Path]:
    output = subprocess.run(
        [
            "git", "-C", str(root), "ls-files", "--cached", "--others",
            "--exclude-standard",
        ],
        check=True, capture_output=True, text=True,
    ).stdout
    return [root / item for item in output.splitlines() if item and (root / item).is_file()]


def audit_tree(root: Path) -> list[str]:
    failures: list[str] = []
    for path in tracked_files(root):
        relative = path.relative_to(root).as_posix()
        reason = forbidden_path_reason(relative)
        if reason:
            failures.append(f"{reason}: {relative}")
            continue
        if path.stat().st_size > TEXT_LIMIT:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        ignored = frozenset({"age identity"}) if (
            "/tests/" in f"/{relative}" or relative.startswith("server_backend/")
        ) else frozenset()
        failures.extend(audit_text(relative, content, ignored))
        if os.name != "nt" and path.suffix == ".sh" and not path.stat().st_mode & 0o100:
            failures.append(f"tracked shell script is not owner-executable: {relative}")
    for required in REQUIRED_PUBLIC_FILES:
        if not (root / required).is_file():
            failures.append(f"required public file is missing: {required}")
    failures.extend(audit_license_metadata(root))
    failures.extend(audit_immutable_build_inputs(root))
    failures.extend(audit_public_claims(root, "server"))
    return failures


def audit_history(root: Path) -> list[str]:
    """Identify forbidden paths in history before a visibility change."""

    output = subprocess.run(
        ["git", "-C", str(root), "log", "--all", "--format=", "--name-only"],
        check=True, capture_output=True, text=True,
    ).stdout
    failures = []
    for item in sorted(set(output.splitlines())):
        if item and forbidden_path_reason(item):
            failures.append(f"forbidden historical path requires clean export: {item}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args()
    if args.fixture:
        failures = verify_scanner_fixture(args.fixture)
        if failures:
            for failure in failures:
                print(f"ERROR: {failure}", file=sys.stderr)
            return 1
        print("Security scanner fixture corpus verified.")
        return 0
    root = args.root.resolve()
    failures = audit_tree(root)
    if args.history:
        failures.extend(audit_history(root))
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    print("Publication audit passed for the current publishable tree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
