#!/usr/bin/env python3
"""Replace the exported service-worker release placeholder deterministically."""

from __future__ import annotations

import argparse
from pathlib import Path
import re


PLACEHOLDER = "__MP_OPT_RELEASE__"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("worker", type=Path)
    parser.add_argument("release")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{7,64}", args.release):
        parser.error("release must be a lowercase Git hash")
    body = args.worker.read_text(encoding="utf-8")
    if PLACEHOLDER not in body:
        parser.error("service-worker release placeholder is missing")
    args.worker.write_text(body.replace(PLACEHOLDER, args.release), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
