"""Tests for scripts/check_figures.py.

Each case builds a small outputs/ tree in a temp directory from the
real committed figures, so the PNGs under test are genuine.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from scripts.check_figures import DATA_FILES, EXPECTED, check_figures, data_hash

ROOT = Path(__file__).resolve().parents[1]
REAL_FIGURES = ROOT / "outputs" / "figures"
REAL_OUTPUTS = ROOT / "outputs"


def _build_tree(tmp_path: Path) -> tuple[Path, Path]:
    outputs = tmp_path / "outputs"
    figures = outputs / "figures"
    figures.mkdir(parents=True)
    for name in DATA_FILES:
        shutil.copy2(REAL_OUTPUTS / name, outputs / name)
    for name in EXPECTED:
        shutil.copy2(REAL_FIGURES / name, figures / name)
    return outputs, figures


def _write_manifest(figures: Path, outputs: Path) -> None:
    from PIL import Image

    manifest = {
        "generated_by": "notebooks/funnel_analysis.ipynb",
        "data_sha256": data_hash(outputs),
        "figures": {},
    }
    for name in EXPECTED:
        with Image.open(figures / name) as image:
            manifest["figures"][name] = {
                "width": image.size[0],
                "height": image.size[1],
            }
    (figures / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_valid_tree_passes(tmp_path):
    outputs, figures = _build_tree(tmp_path)
    _write_manifest(figures, outputs)
    assert check_figures(figures, outputs) == []


def test_missing_figure_fails(tmp_path):
    outputs, figures = _build_tree(tmp_path)
    _write_manifest(figures, outputs)
    (figures / "funnel.png").unlink()
    problems = check_figures(figures, outputs)
    assert any("funnel.png" in p and "missing" in p for p in problems)


def test_empty_figure_fails(tmp_path):
    outputs, figures = _build_tree(tmp_path)
    _write_manifest(figures, outputs)
    (figures / "funnel.png").write_bytes(b"")
    problems = check_figures(figures, outputs)
    assert any("funnel.png" in p and "empty" in p for p in problems)


def test_invalid_signature_fails(tmp_path):
    outputs, figures = _build_tree(tmp_path)
    _write_manifest(figures, outputs)
    (figures / "funnel.png").write_bytes(b"not a png at all" * 100)
    problems = check_figures(figures, outputs)
    assert any("funnel.png" in p for p in problems)


def test_truncated_png_fails(tmp_path):
    outputs, figures = _build_tree(tmp_path)
    _write_manifest(figures, outputs)
    data = (figures / "funnel.png").read_bytes()
    (figures / "funnel.png").write_bytes(data[: len(data) // 2])
    problems = check_figures(figures, outputs)
    assert any("funnel.png" in p for p in problems)


def test_corrupted_image_data_fails(tmp_path):
    outputs, figures = _build_tree(tmp_path)
    _write_manifest(figures, outputs)
    # A plausible header with corrupted image data: flip bytes in
    # the middle of the file, past the IHDR chunk.
    path = figures / "funnel.png"
    data = bytearray(path.read_bytes())
    for i in range(len(data) // 2, len(data) // 2 + 200):
        data[i] ^= 0xFF
    path.write_bytes(bytes(data))
    problems = check_figures(figures, outputs)
    assert any("funnel.png" in p for p in problems)


def test_stale_data_fails(tmp_path):
    outputs, figures = _build_tree(tmp_path)
    _write_manifest(figures, outputs)
    # Change the data after the figures were generated.
    csv_path = outputs / "02_funnel_metrics.csv"
    csv_path.write_text(csv_path.read_text().replace("21.63", "21.64"))
    problems = check_figures(figures, outputs)
    assert any("stale" in p for p in problems)


def test_missing_manifest_fails(tmp_path):
    outputs, figures = _build_tree(tmp_path)
    problems = check_figures(figures, outputs)
    assert any("manifest.json" in p and "missing" in p for p in problems)


def test_dimension_mismatch_fails(tmp_path):
    outputs, figures = _build_tree(tmp_path)
    _write_manifest(figures, outputs)
    manifest_path = figures / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["figures"]["funnel.png"]["width"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    problems = check_figures(figures, outputs)
    assert any("funnel.png" in p and "manifest" in p for p in problems)


def test_data_hash_is_stable_and_content_sensitive(tmp_path):
    outputs, _ = _build_tree(tmp_path)
    first = data_hash(outputs)
    assert first == data_hash(outputs)
    assert (
        first
        == hashlib.sha256(
            b"".join((outputs / name).read_bytes() for name in DATA_FILES)
        ).hexdigest()
    )
    (outputs / "02_funnel_metrics.csv").write_bytes(b"changed")
    assert data_hash(outputs) != first
