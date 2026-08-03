"""Short-lived complete evidence export service tests."""

import hashlib
import json
from pathlib import Path
from subprocess import CompletedProcess

from app.core.config import settings
from app.services import evidence_export


def test_complete_export_uses_fixed_tool_arguments_and_cleans_up(monkeypatch, tmp_path):
    evidence_home = tmp_path / "evidence"
    trust_root = evidence_home / "controller-trust"
    evidence_home.mkdir()
    trust_root.mkdir()
    monkeypatch.setattr(settings, "EVIDENCE_HOME", str(evidence_home))
    monkeypatch.setattr(settings, "EVIDENCE_TRUST_ROOT", str(trust_root))
    monkeypatch.setattr(evidence_export, "EXPORT_TOOL", tmp_path / "portable_bundle.py")
    instance_id = "11111111-1111-4111-8111-111111111111"

    def run(command, **kwargs):
        output = Path(command[command.index("--output") + 1])
        output.write_bytes(b"synthetic zip")
        metadata = {
            "valid": True,
            "valid_zip": True,
            "instance_id": instance_id,
            "bundle_id": "22222222-2222-4222-8222-222222222222",
            "chain_head_sha256": "a" * 64,
            "record_count": 3,
            "zip_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        }
        assert command[2] == "create-local-zip"
        assert command[command.index("--trust-repository") + 1] == str(trust_root)
        assert kwargs == {"check": False, "capture_output": True, "text": True, "timeout": 120}
        return CompletedProcess(command, 0, json.dumps(metadata), "")

    monkeypatch.setattr(evidence_export.subprocess, "run", run)
    output, metadata = evidence_export.create_complete_evidence_export(instance_id)
    directory = output.parent

    assert output.read_bytes() == b"synthetic zip"
    assert metadata["valid_zip"] is True
    evidence_export.remove_complete_evidence_export(output)
    assert not directory.exists()
