"""The bundled data is generated, not downloaded — this test pins that.

scripts/generate_synthetic_data.py (seed 42) must reproduce the
committed CSVs exactly, so the data's provenance is verifiable
from the repository alone. If the generator changes, the committed
CSVs must be regenerated in the same commit.
"""

import importlib.util
import pathlib

import pandas as pd

from src.funnel import load_data, sql_result_tables

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_synthetic_data", ROOT / "scripts" / "generate_synthetic_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generator_reproduces_committed_data():
    generator = _load_generator()
    users, events = generator.generate(seed=42, n_users=6000)
    committed_users = pd.read_csv(ROOT / "data" / "product_users_clean.csv")
    committed_events = pd.read_csv(ROOT / "data" / "product_events_clean.csv")
    assert len(users) == len(committed_users) == 6000
    assert len(events) == len(committed_events) == 19822
    generated_users = pd.DataFrame(users)
    generated_events = pd.DataFrame(events)
    pd.testing.assert_frame_equal(generated_users, committed_users, check_dtype=False)
    pd.testing.assert_frame_equal(generated_events, committed_events, check_dtype=False)


def test_generated_data_gives_published_funnel():
    users, events = load_data(ROOT / "data")
    funnel_table = sql_result_tables(users, events)["02_funnel_metrics.csv"]
    assert funnel_table["users"].tolist() == [6000, 5382, 4256, 2541, 1275]
