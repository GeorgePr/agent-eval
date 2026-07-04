#!/usr/bin/env python3
"""Write SHA256 checksums for built distributions.

Produces ``<dist-dir>/SHA256SUMS`` in the standard ``sha256sums`` format
(``<hex>  <filename>``), so consumers can verify a downloaded wheel/sdist with
``sha256sum -c SHA256SUMS``. Stdlib only.

Usage:
    python scripts/build_checksums.py --dist-dir dist
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

CHECKSUM_FILE = "SHA256SUMS"


def find_distributions(dist_dir: Path) -> tuple[list[Path], list[Path]]:
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    return wheels, sdists


def build_checksums(dist_dir: Path) -> Path:
    """Write SHA256SUMS covering every wheel/sdist in dist_dir. Returns its path.

    Raises FileNotFoundError if a wheel or sdist is missing (a release must ship
    both), so an empty/half-built dist/ fails loudly.
    """
    wheels, sdists = find_distributions(dist_dir)
    if not wheels:
        raise FileNotFoundError(f"no wheel (*.whl) found in {dist_dir}")
    if not sdists:
        raise FileNotFoundError(f"no sdist (*.tar.gz) found in {dist_dir}")

    lines = []
    for path in sorted(wheels + sdists):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    out = dist_dir / CHECKSUM_FILE
    out.write_text("\n".join(lines) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write SHA256SUMS for built distributions.")
    parser.add_argument("--dist-dir", default="dist", help="directory holding wheel/sdist")
    args = parser.parse_args(argv)
    try:
        out = build_checksums(Path(args.dist_dir))
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {out}:")
    print(out.read_text(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
