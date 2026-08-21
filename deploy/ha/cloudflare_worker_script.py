#!/usr/bin/env python3
"""Observe or delete one exact Cloudflare Worker script without exposing its token."""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request


def worker_api_url(action: str, account_id: str, worker_name: str) -> str:
    base = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/workers/scripts/{worker_name}"
    )
    # Module Workers can return 404 from the content endpoint even while the
    # script exists. Settings is the same exact-resource existence probe used
    # by the commissioning controller; deletion still targets the script.
    return f"{base}/settings" if action == "observe" else base


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] not in {"observe", "delete"}:
        return 64
    action, account_id, worker_name = sys.argv[1:]
    if re.fullmatch(r"[0-9a-f]{32}", account_id) is None:
        return 64
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", worker_name) is None:
        return 64
    token = sys.stdin.read(4097)
    if not 32 <= len(token) <= 4096 or "\n" in token or "\r" in token:
        return 64
    url = worker_api_url(action, account_id, worker_name)
    request = urllib.request.Request(
        url,
        method="GET" if action == "observe" else "DELETE",
        headers={"Authorization": f"Bearer {token}"},
    )
    token = ""
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print("absent")
            return 4
        print(f"Cloudflare Worker API returned HTTP {exc.code}", file=sys.stderr)
        return 1
    except (OSError, urllib.error.URLError):
        print("Cloudflare Worker API was unavailable", file=sys.stderr)
        return 1
    if action == "observe" and status == 200:
        print("present")
        return 0
    if action == "delete" and status in {200, 204}:
        print("deleted")
        return 0
    print(f"Cloudflare Worker API returned unexpected HTTP {status}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
