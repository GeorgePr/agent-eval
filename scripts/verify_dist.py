#!/usr/bin/env python3
"""Sanity-check built distributions before release.

Confirms the wheel and sdist contain the files an install needs and do not carry
obvious repo junk (VCS/CI/cache/state dirs). Conservative on purpose — it checks
by path segment, not fragile exact metadata paths. Stdlib only.

Usage:
    python scripts/verify_dist.py --dist-dir dist
"""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path

# Directory names / files that must never appear inside a distribution.
JUNK_DIRS = {
    ".git",
    ".github",
    ".agenteval",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".relenv",
}
JUNK_FILES = {".env"}


def _junk_hits(members: list[str]) -> list[str]:
    hits = []
    for name in members:
        parts = [p for p in name.split("/") if p]
        if any(part in JUNK_DIRS for part in parts):
            hits.append(name)
        elif parts and parts[-1] in JUNK_FILES:
            hits.append(name)
    return hits


def _wheel_members(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def _sdist_members(path: Path) -> list[str]:
    with tarfile.open(path, "r:gz") as tf:
        return tf.getnames()


def _has_top_level(members: list[str], filename: str) -> bool:
    """True if any member's basename matches (sdists nest under a top dir)."""
    return any([p for p in name.split("/") if p][-1:] == [filename] for name in members)


def verify_dist(dist_dir: Path) -> list[str]:
    """Return a list of problems (empty means the distributions look good)."""
    problems: list[str] = []
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))

    if not wheels:
        problems.append(f"no wheel (*.whl) found in {dist_dir}")
    if not sdists:
        problems.append(f"no sdist (*.tar.gz) found in {dist_dir}")

    for wheel in wheels:
        members = _wheel_members(wheel)
        if "agenteval.py" not in members:
            problems.append(f"{wheel.name}: missing top-level agenteval.py")
        if not any(".dist-info/" in m for m in members):
            problems.append(f"{wheel.name}: missing .dist-info metadata")
        for hit in _junk_hits(members):
            problems.append(f"{wheel.name}: contains unwanted path {hit!r}")

    for sdist in sdists:
        members = _sdist_members(sdist)
        for expected in ("agenteval.py", "README.md", "pyproject.toml"):
            if not _has_top_level(members, expected):
                problems.append(f"{sdist.name}: missing {expected}")
        for hit in _junk_hits(members):
            problems.append(f"{sdist.name}: contains unwanted path {hit!r}")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify built distributions.")
    parser.add_argument("--dist-dir", default="dist", help="directory holding wheel/sdist")
    args = parser.parse_args(argv)
    problems = verify_dist(Path(args.dist_dir))
    if problems:
        print("dist verification FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    print("dist verification OK: wheel + sdist contain expected files, no junk")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
