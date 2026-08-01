#!/usr/bin/env python3
"""Fail closed on unsupported legal, deletion and provider claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable


DEFAULT_POLICY = Path(__file__).with_name("legal_claim_rules.json")


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("format") != "masterplan-public-claim-policy-v1":
        raise ValueError("unsupported legal-claim policy format")
    return policy


def _flags(value: str) -> int:
    result = 0
    if "i" in value:
        result |= re.IGNORECASE
    if "m" in value:
        result |= re.MULTILINE
    return result


def scan_text(text: str, policy: dict[str, Any], source: str = "<text>") -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rule in policy["rules"]:
        expression = re.compile(rule["pattern"], _flags(rule.get("flags", "")))
        for match in expression.finditer(text):
            prefix = text[max(0, match.start() - 120):match.start()]
            if any(
                re.search(pattern, prefix, re.IGNORECASE)
                for pattern in rule.get("safe_prefix_patterns", [])
            ):
                continue
            line = text.count("\n", 0, match.start()) + 1
            excerpt = " ".join(match.group(0).split())[:160]
            findings.append({
                "rule_id": rule["id"],
                "source": source,
                "line": line,
                "excerpt": excerpt,
                "guidance": rule["guidance"],
            })
    return findings


def _normalise(relative: str) -> str:
    return relative.replace("\\", "/").removeprefix("./")


def public_files(root: Path, profile: str, policy: dict[str, Any]) -> Iterable[Path]:
    configuration = policy["profiles"].get(profile)
    if configuration is None:
        raise ValueError(f"unknown public-output profile: {profile}")
    excluded = tuple(_normalise(value).rstrip("/") + "/" for value in configuration.get("exclude_prefixes", []))
    paths: set[Path] = set()
    for relative in configuration.get("root_files", []):
        path = root / relative
        if path.is_file():
            paths.add(path)
    for tree in configuration.get("trees", []):
        tree_root = root / tree["path"]
        if not tree_root.is_dir():
            continue
        extensions = set(tree["extensions"])
        for path in tree_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            relative = _normalise(path.relative_to(root).as_posix())
            if any(relative.startswith(prefix) for prefix in excluded):
                continue
            paths.add(path)
    return sorted(paths)


def audit_public_claims(
    root: Path,
    profile: str,
    policy_path: Path = DEFAULT_POLICY,
) -> list[str]:
    policy = load_policy(policy_path)
    failures: list[str] = []
    for path in public_files(root.resolve(), profile, policy):
        relative = path.relative_to(root.resolve()).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for finding in scan_text(content, policy, relative):
            failures.append(
                f"unsupported public claim {finding['rule_id']}: "
                f"{relative}:{finding['line']} ({finding['excerpt']})"
            )
    return failures


def verify_fixture(path: Path, policy: dict[str, Any]) -> list[str]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    failures: list[str] = []
    for index, text in enumerate(fixture.get("safe", [])):
        findings = scan_text(text, policy, f"safe[{index}]")
        if findings:
            failures.append(f"safe fixture {index} produced {findings[0]['rule_id']}")
    for item in fixture.get("unsafe", []):
        observed = {finding["rule_id"] for finding in scan_text(item["text"], policy, item["rule_id"])}
        if item["rule_id"] not in observed:
            failures.append(f"unsafe fixture did not produce {item['rule_id']}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--profile", choices=("server", "docs", "app", "testing"), default="server")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args()
    policy = load_policy(args.policy)
    failures = (
        verify_fixture(args.fixture, policy)
        if args.fixture
        else audit_public_claims(args.root, args.profile, args.policy)
    )
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    if args.fixture:
        print("Legal-claim fixture corpus verified.")
    else:
        print(f"Legal-claim audit passed for {args.profile} public outputs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
