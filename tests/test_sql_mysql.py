"""MySQL integration tests for the SQL queries.

These run only when a MySQL server is reachable (CI provides one via
the service container; locally, set MYSQL_HOST / MYSQL_PWD and run
`make validate` or start the Docker Compose service first).

Unlike scripts/validate_results.py, which checks the bundled clean
sample, these tests load a small *synthetic edge-case* dataset — a
user who skips stages, a purchase after seven days, a purchase on
the exact seven-day boundary — and check that the SQL queries and
the pandas functions agree on it. That is the point: agreement on
the clean sample alone does not prove the SQL handles edge cases.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from src.funnel import (
    country_conversion,
    first_purchase_after_signup,
    funnel_metrics,
    load_data,
    seven_day_conversion,
    stage_dates,
)

ROOT = Path(__file__).resolve().parents[1]

# Synthetic edge-case data, written as CSVs so both the SQL load and
# the pandas load use exactly the same files.
USERS_CSV = """user_id,signup_date,country
1,2024-01-01,US
2,2024-01-01,UK
3,2024-01-01,US
4,2024-01-15,US
"""

EVENTS_CSV = """user_id,event_type,event_date
1,signup,2024-01-01
1,login,2024-01-02
1,view_product,2024-01-03
1,add_to_cart,2024-01-04
1,purchase,2024-01-05
2,signup,2024-01-01
2,purchase,2024-01-08
3,signup,2024-01-01
3,login,2024-01-02
3,purchase,2024-01-20
4,signup,2024-01-15
4,purchase,2024-01-22
"""

DB = "ecommerce_analytics_test"


def _mysql_available() -> bool:
    if shutil.which("mysql") is None:
        return False
    if not os.environ.get("MYSQL_PWD") and not os.environ.get("MYSQL_ROOT_PASSWORD"):
        return False
    return True


def _mysql_args() -> list[str]:
    return [
        "mysql",
        "--batch",
        "--raw",
        "--host",
        os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "--port",
        os.environ.get("MYSQL_PORT", "3306"),
        "--user",
        os.environ.get("MYSQL_USER", "root"),
    ]


def _run(sql: str) -> list[list[str]]:
    env = os.environ.copy()
    env["MYSQL_PWD"] = env.get("MYSQL_PWD", env.get("MYSQL_ROOT_PASSWORD", ""))
    result = subprocess.run(
        _mysql_args(),
        input=sql,
        text=True,
        capture_output=True,
        env=env,
        timeout=90,
    )
    if result.returncode != 0:
        raise RuntimeError(f"MySQL failed: {result.stderr or result.stdout}")
    return list(csv.reader(io.StringIO(result.stdout), delimiter="\t"))


def _load_edge_case(tmp_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "product_users_clean.csv").write_text(USERS_CSV)
    (data_dir / "product_events_clean.csv").write_text(EVENTS_CSV)
    users, events = load_data(data_dir)
    # Load the same CSVs into a fresh test database
    _run(f"DROP DATABASE IF EXISTS {DB}; CREATE DATABASE {DB};")
    schema = (ROOT / "sql" / "00_schema.sql").read_text()
    schema = schema.replace("ecommerce_analytics", DB)
    _run(schema)
    # LOAD DATA INFILE needs the files in the server's secure-file dir;
    # instead, insert the rows directly — the SQL under test is the
    # analysis queries (02-05), not the loader.
    for _, row in users.iterrows():
        _run(
            f"USE {DB}; INSERT INTO users VALUES ({int(row['user_id'])}, "
            f"'{row['signup_date'].date()}', '{row['country']}');"
        )
    for _, row in events.iterrows():
        _run(
            f"USE {DB}; INSERT INTO events VALUES ({int(row['user_id'])}, "
            f"'{row['event_type']}', '{row['event_date'].date()}');"
        )
    return users, events


@pytest.mark.integration
@pytest.mark.skipif(not _mysql_available(), reason="MySQL not available")
def test_sql_agrees_with_pandas_on_edge_cases(tmp_path):
    users, events = _load_edge_case(tmp_path)
    try:
        # Ordered funnel: user 1 completes, users 2 and 3 skip, user 4 only signs up
        expected_funnel = funnel_metrics(stage_dates(events))
        sql = (
            (ROOT / "sql" / "02_funnel_metrics.sql")
            .read_text()
            .replace("ecommerce_analytics", DB)
        )
        actual = _run(sql)
        assert [row[1] for row in actual[1:]] == [
            str(n) for n in expected_funnel["users"]
        ]

        # Seven-day conversion: observed_through is 2024-01-22, cutoff 2024-01-15.
        # All four users are eligible (signup <= 2024-01-15). User 1 buys day 4,
        # user 2 buys day 7 (boundary, counts), user 3 buys day 19 (does not),
        # user 4 buys day 7 (boundary, counts). Expected: 4 eligible, 3 converted.
        expected_seven = seven_day_conversion(users, events)
        sql = (
            (ROOT / "sql" / "05_seven_day_conversion.sql")
            .read_text()
            .replace("ecommerce_analytics", DB)
        )
        actual = _run(sql)
        assert actual[1][3] == str(int(expected_seven.loc[0, "eligible_users"]))
        assert actual[1][4] == str(
            int(expected_seven.loc[0, "purchasers_within_7_days"])
        )

        # Country conversion (any-purchase): US users 1, 3, 4 all bought,
        # UK user 2 bought — 3/3 US, 1/1 UK.
        expected_country = country_conversion(
            users, first_purchase_after_signup(users, events)
        )
        sql = (
            (ROOT / "sql" / "04_country_segmentation.sql")
            .read_text()
            .replace("ecommerce_analytics", DB)
        )
        actual = _run(sql)
        by_country = {row[0]: row[2] for row in actual[1:]}
        assert by_country["US"] == "3"
        assert by_country["UK"] == "1"
        assert expected_country.set_index("country").loc["US", "valid_purchases"] == 3

        # Data-quality checks must all be 0 on this clean edge-case data.
        sql = (
            (ROOT / "sql" / "06_data_quality.sql")
            .read_text()
            .replace("ecommerce_analytics", DB)
        )
        quality = _run(sql)
        assert all(row[1] == "0" for row in quality[1:]), quality
    finally:
        _run(f"DROP DATABASE IF EXISTS {DB};")
