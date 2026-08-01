#!/usr/bin/env python3
"""Validate and render the non-secret deployment cryptographic inventory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any


CATALOGUE = Path(__file__).with_name("cryptographic_inventory.json")
FORMAT = "mp-opt-cryptographic-inventory-v1"
REQUIRED_FIELDS = {
    "id",
    "key_or_secret_type",
    "purpose",
    "algorithm_or_format",
    "owner",
    "generation_method",
    "storage_locations",
    "systems_that_receive_it",
    "access_roles",
    "backup_method",
    "rotation_trigger",
    "normal_rotation_policy",
    "revocation_method",
    "compromise_procedure",
    "destruction_condition",
    "current_key_id_or_fingerprint",
    "deployment_probe",
}
REQUIRED_IDS = {
    "activation_reset_tokens",
    "application_secret",
    "controller_evidence_signing_private_keys",
    "root_passkey_private_keys",
    "processor_desktop_signing_private_keys",
    "csrf_tokens",
    "database_password",
    "desktop_database_encryption_key",
    "desktop_manifest_signing_key",
    "dns_failover_api_token",
    "event_desktop_publish_secrets",
    "evidence_instance_signing_key",
    "evidence_github_fine_grained_token",
    "exchange_codes",
    "git_signing_keys",
    "ha_node_tokens",
    "human_ssh_keys",
    "ip_pseudonymisation_hmac_key",
    "node_replication_age_identities",
    "peer_ssh_keys",
    "provider_recovery_codes",
    "recovery_age_private_identity",
    "root_bootstrap_token",
    "session_bearer_tokens",
    "smtp_token",
    "tls_private_keys",
    "user_passkey_private_keys",
    "vapid_private_key",
    "webauthn_credential_public_keys",
}
_PEM_BEGIN = "-----BEGIN "
PRIVATE_MARKERS = (
    f"{_PEM_BEGIN}PRIVATE KEY-----",
    f"{_PEM_BEGIN}OPENSSH PRIVATE KEY-----",
    "AGE-SECRET-KEY-",
)


class InventoryError(ValueError):
    """Raised when the inventory is incomplete or unsafe."""


def load_catalogue(path: Path = CATALOGUE) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("format") != FORMAT or set(document) != {"format", "items"}:
        raise InventoryError("inventory document has an unsupported shape")
    items = document["items"]
    if not isinstance(items, list):
        raise InventoryError("inventory items must be a list")
    identifiers: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != REQUIRED_FIELDS:
            raise InventoryError("every inventory item must contain exactly the required fields")
        if any(not isinstance(value, str) or not value.strip() for value in item.values()):
            raise InventoryError("inventory fields must be non-empty strings")
        identifier = item["id"]
        if identifier in identifiers:
            raise InventoryError(f"duplicate inventory id: {identifier}")
        identifiers.add(identifier)
    if identifiers != REQUIRED_IDS:
        missing = sorted(REQUIRED_IDS - identifiers)
        extra = sorted(identifiers - REQUIRED_IDS)
        raise InventoryError(f"inventory coverage mismatch; missing={missing}; extra={extra}")
    serialised = json.dumps(document)
    if any(marker in serialised for marker in PRIVATE_MARKERS):
        raise InventoryError("inventory contains private-key material")
    return document


def _safe_file_status(path: Path) -> tuple[str, str | None]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "not_configured", None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return "unsafe_path", None
    if os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o600:
        return "unsafe_mode", None
    return ("configured" if info.st_size else "disabled"), None


def _public_fingerprint(path: Path, prefix: str) -> tuple[str, str | None]:
    status, _ = _safe_file_status(path)
    if status not in {"configured", "unsafe_mode"}:
        return status, None
    raw = path.read_bytes()
    return status, f"{prefix}-{hashlib.sha256(raw).hexdigest()[:16]}"


def _probe(item: dict[str, str], root: Path, home: Path, recovery_recipient: Path | None) -> tuple[str, str | None]:
    probe = item["deployment_probe"]
    if probe in {"external", "database", "runtime", "caddy"}:
        return {
            "external": "operator_record_required",
            "database": "database_managed",
            "runtime": "generated_per_use",
            "caddy": "caddy_managed",
        }[probe], None
    if probe == "recovery_recipient":
        if recovery_recipient is None:
            return "operator_record_required", None
        return _public_fingerprint(recovery_recipient, "rk")
    kind, value = probe.split(":", 1)
    if kind == "secret":
        return _safe_file_status(root / value)
    if kind == "system_secret":
        return _safe_file_status(Path(value))
    if kind == "public":
        return _public_fingerprint(root / value, "pub")
    if kind == "public_home":
        return _public_fingerprint(home / value, "ssh")
    if kind == "key_id":
        relative, prefix = value.rsplit(":", 1)
        path = root / relative
        status, _ = _safe_file_status(path)
        if status != "configured":
            return status, None
        # This probe is restricted to generated high-entropy keys whose public
        # operational key ID already uses the same one-way derivation.
        return status, f"{prefix}-{hashlib.sha256(path.read_bytes()).hexdigest()[:16]}"
    raise InventoryError(f"unsupported deployment probe: {probe}")


def deployment_report(document: dict[str, Any], root: Path, home: Path, recovery_recipient: Path | None) -> dict[str, Any]:
    items = []
    for catalogue_item in document["items"]:
        status, current_id = _probe(catalogue_item, root, home, recovery_recipient)
        item = dict(catalogue_item)
        item.pop("deployment_probe")
        item["deployment_status"] = status
        item["observed_key_id_or_fingerprint"] = current_id
        items.append(item)
    return {
        "format": "mp-opt-deployment-cryptographic-inventory-v1",
        "private_values_included": False,
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "report"))
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--recovery-recipient", type=Path)
    args = parser.parse_args()
    try:
        document = load_catalogue(args.catalogue)
        if args.command == "validate":
            print("valid")
        else:
            print(json.dumps(
                deployment_report(
                    document,
                    args.root.resolve(),
                    args.home.resolve(),
                    args.recovery_recipient.resolve() if args.recovery_recipient else None,
                ),
                indent=2,
                sort_keys=True,
            ))
    except (InventoryError, OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"inventory error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
