#!/usr/bin/env python3
"""Generate the build-specific CSP header required by the static frontend."""

from __future__ import annotations

import argparse
import base64
import hashlib
from html.parser import HTMLParser
import os
from pathlib import Path
import tempfile


class InlineScriptCollector(HTMLParser):
    """Collect executable inline script bodies exactly as browsers hash them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._collecting = False
        self._parts: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Start collecting a script only when it has no external source."""
        if tag.lower() != "script":
            return
        self._collecting = not any(name.lower() == "src" for name, _ in attrs)
        self._parts = []

    def handle_data(self, data: str) -> None:
        """Preserve inline script text without normalising its bytes."""
        if self._collecting:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        """Store each non-empty inline script when its element closes."""
        if tag.lower() != "script" or not self._collecting:
            return
        script = "".join(self._parts)
        if script:
            self.scripts.append(script)
        self._collecting = False
        self._parts = []


def collect_inline_script_hashes(frontend_dir: Path) -> list[str]:
    """Return sorted SHA-256 CSP sources for all exported inline scripts."""
    html_files = sorted(frontend_dir.rglob("*.html"))
    if not html_files:
        raise ValueError(f"No exported HTML files found in {frontend_dir}")

    hashes: set[str] = set()
    for html_file in html_files:
        parser = InlineScriptCollector()
        parser.feed(html_file.read_text(encoding="utf-8"))
        for script in parser.scripts:
            digest = hashlib.sha256(script.encode("utf-8")).digest()
            hashes.add(f"'sha256-{base64.b64encode(digest).decode('ascii')}'")

    if not hashes:
        raise ValueError(f"No inline scripts found in exported HTML under {frontend_dir}")
    return sorted(hashes)


def render_caddy_header(script_hashes: list[str]) -> str:
    """Render the Caddy header fragment for the supplied trusted scripts."""
    script_sources = " ".join(["'self'", *script_hashes])
    policy = (
        "default-src 'self'; "
        f"script-src {script_sources}; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "img-src 'self' data: blob:; "
        "manifest-src 'self' blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'"
    )
    return f'Content-Security-Policy "{policy}"\n'


def write_header_atomically(output_path: Path, content: str) -> None:
    """Replace the generated header only after a complete protected write."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        temporary_path.chmod(0o644)
        temporary_path.replace(output_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> int:
    """Generate and verify the CSP fragment for one exported frontend build."""
    parser = argparse.ArgumentParser()
    parser.add_argument("frontend_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    frontend_dir = args.frontend_dir.resolve()
    output_path = args.output or frontend_dir / ".csp-header.caddy"
    hashes = collect_inline_script_hashes(frontend_dir)
    write_header_atomically(output_path, render_caddy_header(hashes))
    print(f"Generated CSP allow-list for {len(hashes)} inline scripts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
