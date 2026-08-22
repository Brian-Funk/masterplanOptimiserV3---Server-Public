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


class RemoteApiError(RuntimeError):
    """A bounded remote failure whose HTTP status is safe to classify."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def request_json(url: str, token: str, *, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    request = urllib.request.Request(url, data=data)
    request.add_header("User-Agent", "Masterplan-Optimiser-HA/1")
    request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
        request.method = "POST"
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1_048_577)
    except urllib.error.HTTPError as exc:
        # Do not echo a provider response which might contain request details.
        provider_error = re.search(rb"error code:\s*([0-9]{3,5})", exc.read(512))
        suffix = (
            f" (provider error {provider_error.group(1).decode('ascii')})"
            if provider_error
            else ""
        )
        raise RemoteApiError(
            exc.code, f"remote API returned HTTP {exc.code}{suffix}"
        ) from exc
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
    except RemoteApiError as exc:
        print(str(exc), file=sys.stderr)
        # A replacement standby remains deliberately non-replaceable until
        # its last heartbeat is older than the witness lease.  Keep that
        # expected 409 as a retryable commissioning wait; the local guards
        # have already proved holder identity and disabled automatic failover.
        if (
            args.command == "witness"
            and args.action == "pair-open"
            and exc.status == 409
        ):
            return 10
        return 1
    except (RuntimeError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
