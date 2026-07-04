#!/usr/bin/env python3
"""Fail if a release tag does not match ``agenteval.__version__``.

Used by the release workflow so a ``vX.Y.Z`` tag can never publish artifacts
built from a different in-code version.

Usage:
    python scripts/check_version.py <tag>

where ``<tag>`` is like ``v0.4.0`` or ``refs/tags/v0.4.0``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the repo root importable so `import agenteval` works when run from CI.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def normalize_tag(tag: str) -> str:
    """Strip a ``refs/tags/`` prefix and a leading ``v`` from a git tag."""
    tag = tag.strip()
    if tag.startswith("refs/tags/"):
        tag = tag[len("refs/tags/"):]
    if tag.startswith("v"):
        tag = tag[1:]
    return tag


def check_tag_matches_version(tag: str, version: str) -> bool:
    return normalize_tag(tag) == version.strip()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("usage: check_version.py <tag>", file=sys.stderr)
        return 2
    from agenteval import __version__

    tag = argv[0]
    if check_tag_matches_version(tag, __version__):
        print(f"OK: tag {tag} matches agenteval.__version__ {__version__}")
        return 0
    print(
        f"MISMATCH: tag {tag!r} does not match agenteval.__version__ "
        f"{__version__!r}. Update __version__ or retag.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
