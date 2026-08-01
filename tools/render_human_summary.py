#!/usr/bin/env python3
"""Render deterministic Markdown and HTML from verified evidence only."""

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "deploy" / "evidence"
sys.path.insert(0, str(SOURCE if SOURCE.is_dir() else HERE))

import evidence_git  # noqa: E402

raise SystemExit(evidence_git.cli("render"))
