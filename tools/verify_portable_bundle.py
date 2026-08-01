#!/usr/bin/env python3
"""Verify a portable accountability evidence bundle offline."""

from pathlib import Path
import sys

EVIDENCE = Path(__file__).resolve().parents[1] / "deploy" / "evidence"
sys.path.insert(0, str(EVIDENCE))

import portable_bundle  # noqa: E402

raise SystemExit(portable_bundle.cli(["verify", *sys.argv[1:]]))
