"""Coverage and disclosure safety for the cryptographic inventory."""
import json
import subprocess
import sys
from pathlib import Path

from deploy.security.cryptographic_inventory import REQUIRED_IDS, load_catalogue


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "security" / "cryptographic_inventory.py"


def test_catalogue_covers_every_required_key_and_secret():
    document = load_catalogue()
    assert {item["id"] for item in document["items"]} == REQUIRED_IDS


def test_deployment_report_contains_status_not_private_values(tmp_path: Path):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "ip_hmac_key").write_text("a" * 64, encoding="utf-8")
    (secrets / "root_bootstrap_token").write_text("", encoding="utf-8")
    evidence_public = secrets / "evidence_signing_key.pub"
    evidence_public.write_text("ssh-ed25519 AAAATEST inventory-test\n", encoding="utf-8")
    if sys.platform != "win32":
        for path in secrets.iterdir():
            path.chmod(0o600)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "report", "--root", str(tmp_path), "--home", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["private_values_included"] is False
    rendered = json.dumps(report)
    assert "a" * 64 not in rendered
    items = {item["id"]: item for item in report["items"]}
    assert items["ip_pseudonymisation_hmac_key"]["observed_key_id_or_fingerprint"].startswith("iphmac-")
    assert items["root_bootstrap_token"]["deployment_status"] == "disabled"
    assert items["evidence_instance_signing_key"]["observed_key_id_or_fingerprint"].startswith("pub-")


def test_catalogue_validation_cli():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "validate"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "valid"


def test_management_tui_exposes_only_the_safe_inventory_report():
    menu = (ROOT / "manage.sh").read_text(encoding="utf-8")
    actions = (ROOT / "deploy" / "management" / "actions.sh").read_text(encoding="utf-8")
    assert '"crypto-inventory" "View non-secret key and credential inventory status"' in menu
    assert "crypto-inventory) mp_run_action mp_cryptographic_inventory" in menu
    assert "mp_cryptographic_inventory()" in actions
    assert 'cryptographic_inventory.py" report' in actions
