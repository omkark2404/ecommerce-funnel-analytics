"""MySQL integration tests for the SQL queries.

These run only when a MySQL server is reachable (CI provides one via
the service container; locally, set MYSQL_HOST / MYSQL_PWD and run
`make validate` or start the Docker Compose service first).

Unlike scripts/validate_results.py, which checks the bundled clean
sample, these tests load small *synthetic edge-case* datasets and
compare every field of every result table — 02 funnel metrics, 03
days to purchase, 04 country segmentation and 05 seven-day
conversion — between MySQL and the pandas implementation, plus
hand-calculated expectations for the fixtures (so a bug shared by
both implementations is still caught).

Fixtures:
- EDGE: full funnel, skipped stages, purchase without add-to-cart,
  repeated login, same-day events, day-7 and day-8 purchases, a
  long-delay purchase, signup-only users, several countries.
- ZERO: signup-only users — zero denominators, so SQL NULL and
  pandas None must agree, and 0% must not be confused with undefined.
- EMPTY: no users and no events at all.

A separate test exercises the reload guard in sql/01_load_data.sql:
bad staging data must not replace the live tables.
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
    days_to_purchase,
    first_purchase_after_signup,
    funnel_metrics,
    load_data,
    seven_day_conversion,
    sql_result_tables,
    stage_dates,
    to_rows,
)

ROOT = Path(__file__).resolve().parents[1]

# --- Fixture EDGE -------------------------------------------------------
# Users: 1 US full funnel (purchase day 4)
#        2 UK signup + purchase day 7 (skips every stage)
#        3 US login, then purchase day 19 (skips view/cart)
#        4 US signup + purchase day 7
#        5 DE same-day login/view, cart and purchase day 1
#        6 UK repeated login, view, purchase day 8 (skips cart)
#        7 DE signup only
EDGE_USERS_CSV = """user_id,signup_date,country
1,2024-01-01,US
2,2024-01-01,UK
3,2024-01-01,US
4,2024-01-15,US
5,2024-01-01,DE
6,2024-01-01,UK
7,2024-01-10,DE
"""

EDGE_EVENTS_CSV = """user_id,event_type,event_date
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
5,signup,2024-01-01
5,login,2024-01-01
5,view_product,2024-01-01
5,add_to_cart,2024-01-02
5,purchase,2024-01-02
6,signup,2024-01-01
6,login,2024-01-03
6,login,2024-01-05
6,view_product,2024-01-06
6,purchase,2024-01-09
7,signup,2024-01-10
"""

# Hand-calculated expectations for EDGE (independent of both
# implementations — see the fixture comment above):
# Ordered funnel: 7 / 4 / 3 / 2 / 2
# Seven-day: observed_through 2024-01-22, cutoff 2024-01-15,
#   7 eligible, 4 converted (users 1, 2, 4, 5), 57.14%
# Days to purchase (any-purchase, 6 buyers): 1d:1, 4d:1, 7d:2, 8d:1, 19d:1
# Country (any-purchase): UK 2/2, US 3/3, DE 1/2
EDGE_FUNNEL_USERS = [7, 4, 3, 2, 2]
EDGE_SEVEN_DAY = (7, 4, 57.14)
EDGE_DAYS = {1: 1, 4: 1, 7: 2, 8: 1, 19: 1}

# --- Fixture ZERO -------------------------------------------------------
ZERO_USERS_CSV = """user_id,signup_date,country
1,2024-01-01,US
2,2024-01-01,UK
"""

ZERO_EVENTS_CSV = """user_id,event_type,event_date
1,signup,2024-01-01
2,signup,2024-01-01
"""

# --- Fixture EMPTY ------------------------------------------------------
EMPTY_USERS_CSV = "user_id,signup_date,country\n"
EMPTY_EVENTS_CSV = "user_id,event_type,event_date\n"

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
    rows = list(csv.reader(io.StringIO(result.stdout), delimiter="\t"))
    # MySQL prints NULL for null values; the pandas CSV serialisation
    # uses an empty cell for the same value (see funnel.to_csv_text).
    return [
        [("" if value in ("NULL", r"\N") else value) for value in row] for row in rows
    ]


def _load_fixture(
    tmp_path: Path, users_csv: str, events_csv: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "product_users_clean.csv").write_text(users_csv)
    (data_dir / "product_events_clean.csv").write_text(events_csv)
    users, events = load_data(data_dir)
    # Load the same CSVs into a fresh test database
    _run(f"DROP DATABASE IF EXISTS {DB}; CREATE DATABASE {DB};")
    schema = (ROOT / "sql" / "00_schema.sql").read_text()
    schema = schema.replace("ecommerce_analytics", DB)
    _run(schema)
    # LOAD DATA INFILE needs the files in the server's secure-file dir;
    # instead, insert the rows directly — the SQL under test is the
    # analysis queries (02-05) and the reload guard, not the loader.
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


def _sql_for(query_file: str) -> str:
    return (ROOT / "sql" / query_file).read_text().replace("ecommerce_analytics", DB)


def _assert_full_table(sql_file: str, expected_table: pd.DataFrame) -> None:
    """Compare every row and column of one SQL result with pandas.

    On mismatch, report the exact field (row, column) and both values
    instead of a bare 'tables differ'.
    """
    actual = _run(_sql_for(sql_file))
    expected = to_rows(expected_table)
    # mysql --batch prints nothing at all — not even the column
    # header — for a query that returns zero rows, while pandas
    # still produces the header row. An empty result is a match
    # only when the expected table is header-only.
    if not actual and len(expected) == 1:
        actual = [expected[0]]
    if actual == expected:
        return
    if len(actual) != len(expected):
        raise AssertionError(
            f"{sql_file}: row count differs — MySQL returned "
            f"{len(actual)} rows (incl. header), pandas expected "
            f"{len(expected)}.\nMySQL: {actual}\nPandas: {expected}"
        )
    for row_index, (actual_row, expected_row) in enumerate(zip(actual, expected)):
        if actual_row != expected_row:
            for col_index, (a, e) in enumerate(zip(actual_row, expected_row)):
                if a != e:
                    header = actual[0][col_index] if col_index < len(actual[0]) else "?"
                    raise AssertionError(
                        f"{sql_file}: mismatch at row {row_index} "
                        f"(header '{header}'), column {col_index}: "
                        f"MySQL returned {a!r}, pandas expected {e!r}.\n"
                        f"MySQL row: {actual_row}\nPandas row: {expected_row}"
                    )
            raise AssertionError(
                f"{sql_file}: row {row_index} differs in length: "
                f"MySQL {actual_row}, pandas {expected_row}"
            )


def _assert_all_tables(users: pd.DataFrame, events: pd.DataFrame) -> None:
    expected = sql_result_tables(users, events)
    for output_name, table in expected.items():
        _assert_full_table(output_name.replace(".csv", ".sql"), table)


@pytest.mark.integration
@pytest.mark.skipif(not _mysql_available(), reason="MySQL not available")
def test_sql_agrees_with_pandas_on_edge_cases(tmp_path):
    users, events = _load_fixture(tmp_path, EDGE_USERS_CSV, EDGE_EVENTS_CSV)
    try:
        # Hand-calculated expectations first (independent of pandas):
        dates = stage_dates(events)
        funnel_table = funnel_metrics(dates)
        assert funnel_table["users"].tolist() == EDGE_FUNNEL_USERS
        seven = seven_day_conversion(users, events)
        assert (
            int(seven.loc[0, "eligible_users"]),
            int(seven.loc[0, "purchasers_within_7_days"]),
            float(seven.loc[0, "conversion_rate_pct"]),
        ) == EDGE_SEVEN_DAY
        days = days_to_purchase(first_purchase_after_signup(users, events))
        assert dict(zip(days["days_to_purchase"], days["observed_purchasers"])) == (
            EDGE_DAYS
        )
        # Then the full comparison of every analytical query:
        _assert_all_tables(users, events)

        # Data-quality checks must all be 0 on this clean edge-case data.
        quality = _run(_sql_for("06_data_quality.sql"))
        assert all(row[1] == "0" for row in quality[1:]), quality
    finally:
        _run(f"DROP DATABASE IF EXISTS {DB};")


@pytest.mark.integration
@pytest.mark.skipif(not _mysql_available(), reason="MySQL not available")
def test_sql_zero_denominators_are_null_not_zero(tmp_path):
    users, events = _load_fixture(tmp_path, ZERO_USERS_CSV, ZERO_EVENTS_CSV)
    try:
        # Hand-calculated: funnel 2 / 0 / 0 / 0 / 0; step rates after
        # login are undefined (NULL), cumulative rates are 0.00;
        # seven-day has 0 eligible users and an undefined rate.
        funnel_table = funnel_metrics(stage_dates(events))
        assert funnel_table["users"].tolist() == [2, 0, 0, 0, 0]
        assert funnel_table["cumulative_conversion_pct"].tolist()[-1] == 0.0
        assert funnel_table["step_conversion_pct"].isna().sum() == 3
        seven = seven_day_conversion(users, events)
        assert int(seven.loc[0, "eligible_users"]) == 0
        assert pd.isna(seven.loc[0, "conversion_rate_pct"])
        # Full comparison: MySQL NULL must match pandas None (empty cell).
        _assert_all_tables(users, events)
    finally:
        _run(f"DROP DATABASE IF EXISTS {DB};")


@pytest.mark.integration
@pytest.mark.skipif(not _mysql_available(), reason="MySQL not available")
def test_sql_empty_tables(tmp_path):
    users, events = _load_fixture(tmp_path, EMPTY_USERS_CSV, EMPTY_EVENTS_CSV)
    try:
        funnel_table = funnel_metrics(stage_dates(events))
        assert funnel_table["users"].tolist() == [0, 0, 0, 0, 0]
        assert funnel_table["step_conversion_pct"].isna().all()
        _assert_all_tables(users, events)
    finally:
        _run(f"DROP DATABASE IF EXISTS {DB};")


def _extract_section(sql_text: str, begin: str, end: str) -> str:
    start = sql_text.index(begin)
    stop = sql_text.index(end) + len(end)
    return sql_text[start:stop]


@pytest.mark.integration
@pytest.mark.skipif(not _mysql_available(), reason="MySQL not available")
def test_reload_guard_keeps_old_data_on_bad_staging(tmp_path):
    """A failed reload must not silently leave valid-looking,
    incomplete data: bad staging aborts before the swap, and the
    live tables keep their previous contents."""
    users, events = _load_fixture(tmp_path, EDGE_USERS_CSV, EDGE_EVENTS_CSV)
    try:
        loader_sql = (ROOT / "sql" / "01_load_data.sql").read_text()
        # The extracted sections contain no USE statement (it sits
        # at the top of sql/01), and every _run() call is a fresh
        # mysql connection with no default database — so select the
        # test database explicitly before running them.
        guard = f"USE {DB};\n" + _extract_section(
            loader_sql,
            "-- BEGIN staging validation guard",
            "-- END staging validation guard",
        ).replace("ecommerce_analytics", DB)
        swap = f"USE {DB};\n" + _extract_section(
            loader_sql, "-- BEGIN swap", "-- END swap"
        ).replace("ecommerce_analytics", DB)
        # Staging tables as defined by sql/01 (without the LOAD DATA
        # step, which needs the server's secure-file dir): populate
        # them from the fixture, plus one bad row — an event for an
        # unknown user.
        _run(
            f"USE {DB};"
            "DROP TABLE IF EXISTS users_staging; DROP TABLE IF EXISTS events_staging;"
            "CREATE TABLE users_staging (user_id INT NOT NULL, signup_date DATE NOT NULL, country VARCHAR(100) NOT NULL);"
            "CREATE TABLE events_staging (user_id INT NOT NULL, event_type VARCHAR(50) NOT NULL, event_date DATE NOT NULL);"
            "INSERT INTO users_staging SELECT * FROM users;"
            "INSERT INTO events_staging SELECT * FROM events;"
            "INSERT INTO events_staging VALUES (999, 'purchase', '2024-01-02');"
        )
        live_before = _run(
            f"USE {DB}; SELECT COUNT(*) FROM users; SELECT COUNT(*) FROM events;"
        )
        # The guard must fail (CHECK violation) on the bad staging.
        with pytest.raises(RuntimeError):
            _run(guard)
        # Live tables are unchanged by the failed reload.
        live_after = _run(
            f"USE {DB}; SELECT COUNT(*) FROM users; SELECT COUNT(*) FROM events;"
        )
        assert live_after == live_before
        # Remove the bad row and reload: the guard passes and the
        # swap replaces the live tables with the staging contents.
        _run(
            f"USE {DB}; DELETE FROM events_staging WHERE user_id = 999;"
            "INSERT INTO users_staging VALUES (8, '2024-01-01', 'FR');"
            "INSERT INTO events_staging VALUES (8, 'signup', '2024-01-01');"
        )
        _run(guard)
        _run(swap)
        counts = _run(
            f"USE {DB}; SELECT COUNT(*) FROM users; SELECT COUNT(*) FROM events;"
        )
        assert counts[1] == [str(len(users) + 1)]
        assert counts[3] == [str(len(events) + 1)]
        assert _run(f"USE {DB}; SELECT country FROM users WHERE user_id = 8;")[1] == [
            "FR"
        ]
    finally:
        _run(f"DROP DATABASE IF EXISTS {DB};")
