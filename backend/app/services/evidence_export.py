"""Create a short-lived, verified accountability evidence download."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from app.core.config import settings
from app.core.evidence import EvidenceUnavailable, evidence_home


EXPORT_TOOL = Path("/app/evidence/portable_bundle.py")


def create_complete_evidence_export(instance_id: str) -> tuple[Path, dict]:
    """Return a verified ZIP in a private temporary directory."""

    directory = Path(tempfile.mkdtemp(prefix="mp-opt-evidence-download."))
    output = directory / "accountability-evidence.zip"
    command = [
        sys.executable,
        str(EXPORT_TOOL),
        "create-local-zip",
        "--evidence-home",
        str(evidence_home()),
        "--trust-repository",
        settings.EVIDENCE_TRUST_ROOT,
        "--instance-id",
        instance_id,
        "--output",
        str(output),
    ]
    try:
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
