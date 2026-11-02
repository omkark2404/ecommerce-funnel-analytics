#!/usr/bin/env python3
"""Check that the committed chart figures are present and valid PNGs.

This is a freshness check that does *not* compare PNG bytes: matplotlib
renders slightly different bytes across versions/platforms, so a byte
comparison would fail spuriously. Instead, after the notebook runs in
CI, this checks that every expected figure exists, is a real PNG,
has non-zero dimensions, and is non-empty. A missing or corrupted
figure — the actual regression — fails the check.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIGURES_DIR = ROOT / "outputs" / "figures"

EXPECTED = [
    "funnel.png",
    "step_dropoff.png",
    "days_to_purchase.png",
    "cohort_conversion.png",
    "country_conversion.png",
]


def png_dimensions(path: Path) -> tuple[int, int]:
    """Read width/height from a PNG IHDR chunk (stdlib only)."""
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path.name} is not a PNG file")
    width, height = struct.unpack(">II", data[16:24])
    if width == 0 or height == 0:
        raise ValueError(f"{path.name} has zero dimensions")
    return width, height


def main() -> int:
    problems: list[str] = []
    for name in EXPECTED:
        path = FIGURES_DIR / name
        if not path.is_file():
            problems.append(f"{name}: missing")
            continue
        if path.stat().st_size == 0:
            problems.append(f"{name}: empty file")
            continue
        try:
            width, height = png_dimensions(path)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        print(f"{name}: {width}x{height}, {path.stat().st_size:,} bytes")
    if problems:
        print("Figure check failed:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print(f"All {len(EXPECTED)} figures present and valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
