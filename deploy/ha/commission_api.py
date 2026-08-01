#!/usr/bin/env python3
"""Secret-safe Cloudflare and witness API calls used by setup-v2.

Credentials and request documents are read from standard input so they never
appear in the process command line or the management audit log.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


def request_json(url: str, token: str, *, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    request = urllib.request.Request(url, data=data)
    request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
        request.method = "POST"
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1_048_577)
    except urllib.error.HTTPError as exc:
        # Do not echo a provider response which might contain request details.
        raise RuntimeError(f"remote API returned HTTP {exc.code}") from exc
    if len(raw) > 1_048_576:
        raise RuntimeError("remote API response was unexpectedly large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise RuntimeError("remote API returned an invalid response")
    return result


def zone_id(hostname: str) -> int:
    token = sys.stdin.read().strip()
    labels = hostname.lower().rstrip(".").split(".")
    for offset in range(0, len(labels) - 1):
        candidate = ".".join(labels[offset:])
        query = urllib.parse.urlencode({"name": candidate, "status": "active"})
        result = request_json(
            f"https://api.cloudflare.com/client/v4/zones?{query}", token
        )
        zones = result.get("result") if result.get("success") is True else None
        if isinstance(zones, list) and len(zones) == 1:
            value = str(zones[0].get("id", ""))
            if re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value):
                print(value)
                return 0
    raise RuntimeError("the token could not discover one matching Cloudflare zone")


def witness(action: str, url: str, cluster: str) -> int:
    token = sys.stdin.readline().strip()
    body = json.load(sys.stdin)
    if not 32 <= len(token) <= 256 or not isinstance(body, dict):
        raise RuntimeError("invalid local commissioning request")
    endpoint = f"{url.rstrip('/')}/v1/clusters/{cluster}/{action}"
    result = request_json(endpoint, token, body=body)
    json.dump(result, sys.stdout, separators=(",", ":"), sort_keys=True)
    sys.stdout.write("\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    zone = commands.add_parser("zone-id")
    zone.add_argument("hostname")
    call = commands.add_parser("witness")
    call.add_argument("action")
    call.add_argument("url")
    call.add_argument("cluster")
    args = parser.parse_args()
    try:
        if args.command == "zone-id":
            return zone_id(args.hostname)
        return witness(args.action, args.url, args.cluster)
    except (RuntimeError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
