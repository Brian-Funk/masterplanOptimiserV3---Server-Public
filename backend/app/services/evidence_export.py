"""Create a short-lived, verified accountability evidence download."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from sqlalchemy.orm import Session

from app.core.evidence import EvidenceUnavailable, evidence_home
from app.models.deletion import DesktopDeletionWorkOrder
from app.models.evidence import ProcessorPolicyAcknowledgement


EXPORT_TOOL = Path("/app/evidence/evidence_bundle.py")


def _stage_evidence(db: Session, destination: Path) -> None:
    source = evidence_home()
    destination.mkdir(mode=0o700)
    for name in ("ledger", "public", "anchors"):
        path = source / name
        if path.exists():
            shutil.copytree(path, destination / name, symlinks=True)
    artifacts = destination / "artifacts"
    artifacts.mkdir(mode=0o700)
    packages: list[tuple[str | None, str | None]] = [
        (row.evidence_package_sha256, row.evidence_package_json)
        for row in db.query(ProcessorPolicyAcknowledgement).all()
    ]
    for row in db.query(DesktopDeletionWorkOrder).all():
        packages.extend((
            (row.report_evidence_package_sha256, row.report_evidence_package_json),
            (row.copy_resolution_evidence_package_sha256, row.copy_resolution_evidence_package_json),
        ))
    for digest, rendered in packages:
        if digest is None and rendered is None:
            continue
        if (
            digest is None
            or rendered is None
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise EvidenceUnavailable("A retained Desktop evidence package is incomplete")
        raw = rendered.encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise EvidenceUnavailable("A retained Desktop evidence package failed its digest check")
        target = artifacts / f"{digest}.json"
        if target.exists() and target.read_bytes() != raw:
            raise EvidenceUnavailable("Conflicting Desktop evidence packages were retained")
        target.write_bytes(raw)


def create_complete_evidence_export(db: Session, instance_id: str) -> tuple[Path, dict]:
    """Return a verified ZIP in a private temporary directory."""

    directory = Path(tempfile.mkdtemp(prefix="mp-opt-evidence-download."))
    output = directory / "accountability-evidence.zip"
    staged_home = directory / "evidence"
    command = [
        sys.executable,
        str(EXPORT_TOOL),
        "create-zip",
        "--evidence-home",
        str(staged_home),
        "--output",
        str(output),
    ]
    try:
        _stage_evidence(db, staged_home)
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise EvidenceUnavailable("The complete evidence ZIP could not be created from the verified chain")
        metadata = json.loads(result.stdout)
        if (
            not output.is_file()
            or output.is_symlink()
            or metadata.get("valid") is not True
            or metadata.get("valid_zip") is not True
            or metadata.get("instance_id") != instance_id
        ):
            raise EvidenceUnavailable("The complete evidence ZIP failed final verification")
        return output, metadata
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, EvidenceUnavailable):
        shutil.rmtree(directory, ignore_errors=True)
        raise


def remove_complete_evidence_export(output: Path) -> None:
    """Remove only the temporary directory created by this module."""

    directory = output.parent
    if directory.name.startswith("mp-opt-evidence-download."):
        shutil.rmtree(directory, ignore_errors=True)
