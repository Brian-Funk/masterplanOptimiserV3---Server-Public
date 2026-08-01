#!/usr/bin/env python3
"""Generate deterministic package-by-package third-party notices."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "licensing" / "third-party-notice-config.json"
PYTHON_REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)")
SPDX_TOKEN = re.compile(r"^[A-Za-z0-9.+() -]+$")
CLASSIFIER_LICENSES = {
    "Apache Software License": "Apache-2.0",
    "BSD License": "BSD-3-Clause",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "ISC License (ISCL)": "ISC",
    "MIT License": "MIT",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Python Software Foundation License": "PSF-2.0",
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def python_requirements(root: Path, config: dict) -> dict[str, str]:
    packages: dict[str, str] = {}
    for relative in config["python_locks"]:
        for raw in (root / relative).read_text(encoding="utf-8").splitlines():
            match = PYTHON_REQUIREMENT.match(raw.strip())
            if not match:
                continue
            name, version = match.groups()
            canonical = re.sub(r"[-_.]+", "-", name).lower()
            previous = packages.get(canonical)
            if previous and previous != version:
                raise ValueError(f"conflicting Python versions for {name}: {previous} and {version}")
            packages[canonical] = version
    return packages


def normalise_python_license(info: dict, overrides: dict, key: str) -> str:
    if key in overrides:
        return overrides[key]
    expression = str(info.get("license_expression") or "").strip()
    if expression and expression.upper() != "UNKNOWN":
        return expression
    legacy = " ".join(str(info.get("license") or "").split())
    known = {
        "Apache 2.0": "Apache-2.0",
        "Apache-2.0": "Apache-2.0",
        "BSD": "BSD-3-Clause",
        "BSD-3-Clause": "BSD-3-Clause",
        "ISC": "ISC",
        "MIT": "MIT",
        "MIT License": "MIT",
        "MPL-2.0": "MPL-2.0",
        "PSF-2.0": "PSF-2.0",
    }
    if legacy in known:
        return known[legacy]
    if legacy and len(legacy) <= 120 and SPDX_TOKEN.fullmatch(legacy):
        return legacy
    classifiers = info.get("classifiers") or []
    mapped = sorted(
        {
            CLASSIFIER_LICENSES[item.removeprefix("License :: OSI Approved :: ")]
            for item in classifiers
            if item.startswith("License :: OSI Approved :: ")
            and item.removeprefix("License :: OSI Approved :: ") in CLASSIFIER_LICENSES
        }
    )
    if mapped:
        return " OR ".join(mapped)
    raise ValueError(f"no reviewable licence expression for Python package {key}")


def refresh_python_metadata(root: Path, config: dict, destination: Path) -> dict:
    requirements = python_requirements(root, config)
    overrides = config.get("python_license_overrides", {})
    packages = {}
    for name, version in sorted(requirements.items()):
        url = f"https://pypi.org/pypi/{quote(name)}/{quote(version)}/json"
        request = Request(url, headers={"User-Agent": "Masterplan-licence-audit/1"})
        with urlopen(request, timeout=30) as response:
            info = json.load(response)["info"]
        key = f"{name}=={version}"
        packages[key] = {
            "license": normalise_python_license(info, overrides, key),
            "project_url": info.get("project_url") or f"https://pypi.org/project/{name}/{version}/",
        }
    document = {"schema_version": 1, "packages": packages}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


def npm_packages(root: Path, config: dict) -> list[dict[str, str]]:
    result = []
    for relative in config["npm_locks"]:
        lock = load_json(root / relative)
        for package_path, metadata in (lock.get("packages") or {}).items():
            if not package_path or metadata.get("link"):
                continue
            name = package_path.rsplit("node_modules/", 1)[-1]
            version = str(metadata.get("version") or "").strip()
            license_name = str(metadata.get("license") or "").strip()
            if not name or not version or not license_name:
                raise ValueError(f"incomplete npm licence metadata in {relative}: {package_path}")
            result.append({"name": name, "version": version, "license": license_name, "source": relative})
    return result


def esc(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render(root: Path, config: dict, python_metadata: dict) -> str:
    requirements = python_requirements(root, config)
    recorded = python_metadata.get("packages") or {}
    expected_keys = {f"{name}=={version}" for name, version in requirements.items()}
    if set(recorded) != expected_keys:
        missing = sorted(expected_keys - set(recorded))
        stale = sorted(set(recorded) - expected_keys)
        raise ValueError(f"Python licence inventory mismatch; missing={missing}, stale={stale}")

    lines = [
        "# Third-party notices",
        "",
        f"{config['project']} uses the third-party components listed below.",
        "This generated inventory is tied to the committed dependency locks. It does not change",
        "the project licence, replace upstream licence texts, or make a legal compatibility conclusion.",
        "Release SBOMs remain authoritative for operating-system packages and exact container digests.",
        "",
        "## Python packages",
        "",
        "| Package | Version | Licence | Upstream metadata |",
        "|---|---:|---|---|",
    ]
    for key, metadata in sorted(recorded.items()):
        name, version = key.rsplit("==", 1)
        lines.append(
            f"| {esc(name)} | {esc(version)} | {esc(metadata['license'])} | {esc(metadata['project_url'])} |"
        )

    lines += [
        "",
        "## Node packages",
        "",
        "| Package | Version | Licence | Lock |",
        "|---|---:|---|---|",
    ]
    unique_npm = {
        (item["name"], item["version"], item["license"], item["source"])
        for item in npm_packages(root, config)
    }
    for name, version, license_name, source in sorted(unique_npm):
        lines.append(f"| {esc(name)} | {esc(version)} | {esc(license_name)} | `{esc(source)}` |")

    lines += ["", "## Images, runtimes and bundled assets", "", "| Component | Licence or notice | Evidence |", "|---|---|---|"]
    for item in config.get("other_components", []):
        lines.append(f"| {esc(item['name'])} | {esc(item['license'])} | {esc(item['evidence'])} |")
    lines += [
        "",
        "## Review rule",
        "",
        "Regenerate and review this file whenever a dependency lock, base image, runtime, font, icon or bundled asset changes.",
        "Packages can carry additional copyright or attribution text in their distributions; redistributors must preserve it.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--refresh-python", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    config_path = args.config.resolve()
    root = config_path.parent.parent
    config = load_json(config_path)
    metadata_path = root / config["python_metadata"]
    if args.refresh_python:
        metadata = refresh_python_metadata(root, config, metadata_path)
    else:
        metadata = load_json(metadata_path)
    content = render(root, config, metadata)
    targets = [root / item for item in config["output_files"]]
    if args.check:
        stale = [str(path.relative_to(root)) for path in targets if not path.is_file() or path.read_text(encoding="utf-8").replace("\r\n", "\n") != content]
        if stale:
            print(f"ERROR: stale generated third-party notices: {', '.join(stale)}", file=sys.stderr)
            return 1
        print(f"Third-party notices verified for {len(metadata['packages'])} Python and {len(npm_packages(root, config))} npm package records.")
        return 0
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    print(f"Generated {', '.join(str(path.relative_to(root)) for path in targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
