#!/usr/bin/env python3
"""Small secret-safe CLI for operator witness operations."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys

from lease_agent import CONTROL_PATH, atomic_json, post, read_config


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    automatic = commands.add_parser("automatic")
    automatic.add_argument("state", choices=("enabled", "disabled"))
    handoff = commands.add_parser("handoff")
    handoff.add_argument("target_node_id")
    commands.add_parser("permit")
    commands.add_parser("ready")
    authorize = commands.add_parser("authorize-transfer")
    authorize.add_argument("source_node_id")
    authorize.add_argument("bundle_id")
    authorize.add_argument("bundle_sha256")
    authorize.add_argument("generation", type=int)
    complete = commands.add_parser("complete-transfer")
    complete.add_argument("bundle_id")
    complete.add_argument("bundle_sha256")
    args = parser.parse_args()
    config = read_config()
    payload = {"node_id": config["HA_NODE_ID"]}
    action = args.command
    if action == "automatic":
        payload["enabled"] = args.state == "enabled"
    elif action == "handoff":
        payload["target_node_id"] = args.target_node_id
    elif action == "permit":
        action = "write-permit"
        try:
            payload["generation"] = int(json.loads(CONTROL_PATH.read_text(encoding="utf-8"))["generation"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            print("The local lease generation is unavailable", file=sys.stderr)
            return 1
    elif action == "ready":
        pass
    elif action == "authorize-transfer":
        action = "transfer-authorize"
        payload.update({
            "source_node_id": args.source_node_id,
            "target_node_id": config["HA_NODE_ID"],
            "bundle_id": args.bundle_id,
            "bundle_sha256": args.bundle_sha256,
            "generation": args.generation,
        })
    else:
        action = "transfer-complete"
        payload.update({
            "bundle_id": args.bundle_id,
            "bundle_sha256": args.bundle_sha256,
        })
    try:
        result = post(config, action, payload)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    # Full witness responses (not the narrow transfer authorization replies)
    # are authoritative local control observations. Persist them immediately
    # so the node that initiated a planned handoff exposes the transition to
    # users instead of waiting up to one heartbeat interval.
    if {
        "holder_node_id", "generation", "routing_ready", "automatic_failover",
    }.issubset(result):
        result.update({
            "node_id": config["HA_NODE_ID"],
            "observed_at": datetime.now(timezone.utc).isoformat(),
        })
        atomic_json(CONTROL_PATH, result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
