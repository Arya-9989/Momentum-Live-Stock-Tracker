"""Write a brotli copy (``<file>.br``) beside each compressible static asset.

Run once while building the Docker image, on Streamlit's frontend bundle::

    python scripts/precompress.py /opt/venv/lib/python3.12/site-packages/streamlit/static

``momentum.server`` serves these to browsers that accept ``br`` and falls back to
gzip otherwise. Quality 11 takes seconds per megabyte, which is fine at build
time and far too slow per request (6 s for Plotly's 4.6 MB chunk), and it saves
about a quarter over gzip -9 (Plotly: 1,379 KB -> 1,032 KB). Only the build stage
needs the ``brotli`` package; the runtime image just reads the files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import brotli

SUFFIXES = {".js", ".css", ".svg", ".json", ".html", ".txt", ".map", ".wasm"}
MIN_SIZE = 1024


def precompress(root: Path, quality: int = 11) -> tuple[int, int, int]:
    """Compress every eligible file under ``root``; return (files, raw bytes, br bytes)."""
    files = raw_total = br_total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SUFFIXES or path.stat().st_size < MIN_SIZE:
            continue
        raw = path.read_bytes()
        encoded: bytes = brotli.compress(raw, quality=quality)
        if len(encoded) >= len(raw):
            continue
        path.with_name(path.name + ".br").write_bytes(encoded)
        files += 1
        raw_total += len(raw)
        br_total += len(encoded)
    return files, raw_total, br_total


def main() -> None:
    root = Path(sys.argv[1])
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")
    files, raw, encoded = precompress(root)
    print(f"brotli: {files} files, {raw / 1e6:.1f} MB -> {encoded / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
