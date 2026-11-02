#!/usr/bin/env python3
"""Check that the committed chart figures are present, valid and current.

Two separate questions, checked separately:

1. **Valid image** — each expected figure exists, is non-empty, and
   fully decodes as a PNG (Pillow loads every scanline, so a
   truncated or corrupted file fails here, not just a bad header).
2. **Matches the current data** — the notebook writes
   ``outputs/figures/manifest.json`` when it generates the figures.
   The manifest records the SHA-256 of the ``outputs/*.csv`` files
   the charts were drawn from, plus each figure's dimensions. This
   check recomputes that hash from the committed CSVs: if the data
   changed without re-running the notebook, the figures are stale
   and the check fails.

This deliberately does *not* compare PNG bytes: matplotlib renders
slightly different bytes across versions/platforms, so a byte
comparison would fail spuriously. The manifest is the strongest
reliable alternative — it proves the figures were generated from
exactly the data now in ``outputs/``, without requiring pixel-perfect
reproducibility.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = ROOT / "outputs"
FIGURES_DIR = OUTPUTS_DIR / "figures"
MANIFEST = FIGURES_DIR / "manifest.json"

EXPECTED = [
    "funnel.png",
    "step_dropoff.png",
    "days_to_purchase.png",
    "cohort_conversion.png",
    "country_conversion.png",
]

# The result tables the figures are drawn from.
DATA_FILES = [
    "02_funnel_metrics.csv",
    "03_days_to_purchase.csv",
    "04_country_segmentation.csv",
    "05_seven_day_conversion.csv",
]


def data_hash(outputs_dir: Path = OUTPUTS_DIR) -> str:
    """SHA-256 of the result CSVs, concatenated in filename order."""
    digest = hashlib.sha256()
    for name in DATA_FILES:
        digest.update((outputs_dir / name).read_bytes())
    return digest.hexdigest()


def decode_png(path: Path) -> tuple[int, int]:
    """Fully decode a PNG and return its dimensions.

    Raises ValueError for anything that is not a readable PNG:
    missing signature, empty file, truncated data, corrupt IDAT.
    """
    if not path.is_file():
        raise ValueError(f"{path.name}: missing")
    if path.stat().st_size == 0:
        raise ValueError(f"{path.name}: empty file")
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise ValueError(f"{path.name}: not a PNG file")
            image.load()  # decodes every scanline; catches truncation
            width, height = image.size
    except ValueError:
        raise
    except Exception as exc:  # Pillow raises several error types
        raise ValueError(f"{path.name}: unreadable PNG ({exc})") from exc
    if width == 0 or height == 0:
        raise ValueError(f"{path.name}: zero dimensions")
    return width, height


def check_figures(
    figures_dir: Path = FIGURES_DIR, outputs_dir: Path = OUTPUTS_DIR
) -> list[str]:
    """Return a list of problems; empty means every check passed."""
    problems: list[str] = []

    # --- Manifest: are the figures current? ---
    manifest_path = figures_dir / "manifest.json"
    manifest = None
    if not manifest_path.is_file():
        problems.append(
            "manifest.json is missing — re-run the notebook "
            "(make notebook) so the figures and their manifest are "
            "regenerated from the current data"
        )
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            problems.append(f"manifest.json is unreadable: {exc}")
        else:
            recorded = manifest.get("data_sha256")
            if recorded is None:
                problems.append("manifest.json has no data_sha256 field")
            else:
                try:
                    current = data_hash(outputs_dir)
                except OSError as exc:
                    problems.append(f"cannot read result CSVs to hash them: {exc}")
                else:
                    if recorded != current:
                        problems.append(
                            "figures are stale: outputs/*.csv changed "
                            "since the figures were generated — re-run "
                            "the notebook (make notebook)"
                        )

    # --- Images: are they valid PNGs of the recorded size? ---
    recorded_sizes = (manifest or {}).get("figures", {})
    for name in EXPECTED:
        path = figures_dir / name
        try:
            width, height = decode_png(path)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        print(f"{name}: {width}x{height}, {path.stat().st_size:,} bytes")
        expected_size = recorded_sizes.get(name)
        if expected_size is not None:
            recorded_width = expected_size.get("width")
            recorded_height = expected_size.get("height")
            if (width, height) != (recorded_width, recorded_height):
                problems.append(
                    f"{name}: dimensions {width}x{height} do not match "
                    f"the manifest ({recorded_width}x{recorded_height})"
                )
    return problems


def main() -> int:
    problems = check_figures()
    if problems:
        print("Figure check failed:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print(f"All {len(EXPECTED)} figures present, valid and current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
