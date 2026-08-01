#!/usr/bin/env python3
"""Reject unsafe content by verifying the closed portable-bundle archive."""

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "deploy" / "evidence"
sys.path.insert(0, str(SOURCE if SOURCE.is_dir() else HERE))

import evidence_archive_repository  # noqa: E402

raise SystemExit(evidence_archive_repository.cli(["verify", *sys.argv[1:]]))
