#!/usr/bin/env python3
"""Encode and validate the short-lived, copy/paste HA join document."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from urllib.parse import urlparse

PREFIX = "MPHA1-"
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
HOSTNAME = re.compile(
    r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def validate(document: dict[str, object]) -> dict[str, object]:
    if document.get("format") != "mp-opt-ha-join-v1":
        raise ValueError("unsupported join-code format")
    cluster_id = str(document.get("cluster_id", ""))
    domain = str(document.get("domain", "")).lower()
    witness_url = str(document.get("witness_url", "")).rstrip("/")
    secret = str(document.get("pairing_secret", ""))
    node_id = str(document.get("node_id", "node-b"))
    parsed = urlparse(witness_url)
    if not IDENTIFIER.fullmatch(cluster_id):
        raise ValueError("invalid cluster id")
    if not HOSTNAME.fullmatch(domain):
        raise ValueError("invalid application hostname")
    if parsed.scheme != "https" or not parsed.netloc or parsed.username:
        raise ValueError("invalid witness URL")
    if not 32 <= len(secret) <= 256 or not re.fullmatch(r"[A-Za-z0-9_-]+", secret):
        raise ValueError("invalid pairing secret")
    if node_id not in {"node-a", "node-b"}:
        raise ValueError("invalid joining node id")
    return {
        "format": "mp-opt-ha-join-v1",
        "cluster_id": cluster_id,
        "domain": domain,
        "witness_url": witness_url,
        "pairing_secret": secret,
        "node_id": node_id,
    }


def encode_document(value: dict[str, object]) -> str:
    document = validate(value)
    raw = json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
    payload = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    checksum = hashlib.sha256(raw).hexdigest()[:10].upper()
    return f"{PREFIX}{payload}-{checksum}"


def encode() -> int:
    print(encode_document(json.load(sys.stdin)))
    return 0


def decode_code(value: str) -> dict[str, object]:
    code = "".join(value.split())
    if not code.startswith(PREFIX) or "-" not in code[len(PREFIX) :]:
        raise ValueError("invalid join code")
    payload, checksum = code[len(PREFIX) :].rsplit("-", 1)
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    if hashlib.sha256(raw).hexdigest()[:10].upper() != checksum:
        raise ValueError("join-code checksum mismatch")
    return validate(json.loads(raw))


def decode() -> int:
    document = decode_code(sys.stdin.read())
    json.dump(document, sys.stdout, separators=(",", ":"), sort_keys=True)
    sys.stdout.write("\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("encode", "decode"))
    args = parser.parse_args()
    try:
        return encode() if args.command == "encode" else decode()
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
