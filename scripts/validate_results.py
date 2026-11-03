#!/usr/bin/env python3
"""Run the SQL queries in MySQL and check them against the pandas results."""

from __future__ import annotations

import argparse
import csv
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import funnel  # noqa: E402


def mysql_command(mode: str) -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    if mode == "docker":
        if shutil.which("docker") is None:
            raise RuntimeError(
                "Docker Compose is not installed or not available on PATH."
            )
        command = [
            "docker",
            "compose",
            "exec",
            "-T",
            "mysql",
            "sh",
            "-c",
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --batch --raw --user=root ecommerce_analytics',
        ]
        return command, env

    mysql_client = shutil.which("mysql")
    if mysql_client is None:
        raise RuntimeError(
            "The MySQL client is not installed or not available on PATH."
        )
    env["MYSQL_PWD"] = env.get("MYSQL_PWD", env.get("MYSQL_ROOT_PASSWORD", ""))
    if not env["MYSQL_PWD"]:
        raise ValueError(
            "Set MYSQL_PWD (or MYSQL_ROOT_PASSWORD) before connecting to MySQL."
        )
    command = [
        mysql_client,
        "--batch",
        "--raw",
        "--host",
        env.get("MYSQL_HOST", "127.0.0.1"),
        "--port",
        env.get("MYSQL_PORT", "3306"),
        "--user",
        env.get("MYSQL_USER", "root"),
        "ecommerce_analytics",
    ]
    return command, env


def query_rows(command: list[str], env: dict[str, str], sql_text: str, label: str):
    result = subprocess.run(
        command,
        input=sql_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        env=env,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"MySQL failed for {label} (exit {result.returncode}):\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    rows = list(csv.reader(io.StringIO(result.stdout), delimiter="\t"))
    if not rows:
        raise RuntimeError(f"MySQL returned no rows for {label}")
    return [
        [("" if value in ("NULL", r"\N") else value) for value in row] for row in rows
    ]


def check_loaded_tables(command: list[str], env: dict[str, str], users, events) -> None:
    """Confirm MySQL holds the same rows as the CSVs before comparing results.

    Row counts alone would miss a swapped country, a shifted date, or a
    duplicate event record, so this also checks per-stage and per-country
    counts, date ranges, and the data-quality query in sql/06 — and,
    decisively, a canonical SHA-256 hash of every record on both sides,
    which fails if any single field of any record differs.
    """
    sql = """
USE ecommerce_analytics;
SELECT
    (SELECT COUNT(*) FROM users) AS user_rows,
    (SELECT COUNT(*) FROM events) AS event_rows,
    (SELECT COUNT(*) FROM users WHERE RIGHT(country, 1) = CHAR(13))
        AS country_trailing_carriage_returns;
"""
    actual = query_rows(command, env, sql, "loaded tables")
    expected = [
        ["user_rows", "event_rows", "country_trailing_carriage_returns"],
        [str(len(users)), str(len(events)), "0"],
    ]
    if actual != expected:
        raise ValueError(
            "MySQL tables do not match the CSV inputs.\n"
            f"Expected: {expected}\nActual:   {actual}"
        )
    print(
        f"MySQL tables match the CSVs ({len(users):,} users, {len(events):,} events)."
    )

    # Row-level comparison: counts alone cannot catch a swapped
    # country, a shifted date, a changed event type, or a changed
    # user_id when another record compensates. Compare the canonical
    # SHA-256 of every record (see funnel.canonical_source_hash):
    # the MySQL side builds the same canonical lines and Python
    # hashes them, so any single-field difference fails here.
    hash_sql = """
USE ecommerce_analytics;
SELECT CONCAT('u|', user_id, '|', signup_date, '|', country)
FROM users
UNION ALL
SELECT CONCAT('e|', user_id, '|', event_type, '|', event_date)
FROM events;
"""
    import hashlib

    digest = hashlib.sha256()
    for row in sorted(query_rows(command, env, hash_sql, "source hash")[1:]):
        digest.update(row[0].encode("utf-8"))
        digest.update(b"\n")
    mysql_hash = digest.hexdigest()
    csv_hash = funnel.canonical_source_hash(users, events)
    if mysql_hash != csv_hash:
        raise ValueError(
            "MySQL tables do not match the CSV inputs record-for-record "
            "(canonical hash differs), even though the counts agree. "
            "A record's country, date, event type or user_id differs, "
            "or a record is missing/extra.\n"
            f"CSV hash:   {csv_hash}\nMySQL hash: {mysql_hash}"
        )
    print(f"MySQL tables match the CSVs record-for-record (SHA-256 {csv_hash[:12]}…).")

    # Per-stage event counts and per-country user counts, compared to pandas.
    stage_sql = """
USE ecommerce_analytics;
SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY event_type;
"""
    stage_actual = query_rows(command, env, stage_sql, "event counts by stage")
    stage_expected = [["event_type", "COUNT(*)"]] + [
        [stage, str(int((events["event_type"] == stage).sum()))]
        for stage in sorted(events["event_type"].unique())
    ]
    if stage_actual != stage_expected:
        raise ValueError(
            "MySQL event counts by stage do not match the CSVs.\n"
            f"Expected: {stage_expected}\nActual:   {stage_actual}"
        )
    country_sql = """
USE ecommerce_analytics;
SELECT country, COUNT(*) FROM users GROUP BY country ORDER BY country;
"""
    country_actual = query_rows(command, env, country_sql, "user counts by country")
    country_expected = [["country", "COUNT(*)"]] + [
        [country, str(int((users["country"] == country).sum()))]
        for country in sorted(users["country"].unique())
    ]
    if country_actual != country_expected:
        raise ValueError(
            "MySQL user counts by country do not match the CSVs.\n"
            f"Expected: {country_expected}\nActual:   {country_actual}"
        )
    print("MySQL per-stage and per-country counts match the CSVs.")

    # Data-quality query: every check must return 0 problems.
    quality_sql = (ROOT / "sql" / "06_data_quality.sql").read_text(encoding="utf-8")
    quality = query_rows(command, env, quality_sql, "06_data_quality.sql")
    failures = [row for row in quality[1:] if row[1] != "0"]
    if failures:
        raise ValueError(
            "MySQL data-quality checks failed:\n"
            + "\n".join(f"- {name}: {count} problems" for name, count in failures)
        )
    print(f"MySQL data-quality checks passed ({len(quality) - 1} checks, 0 problems).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("host", "docker"),
        default="host",
        help="connect to a host MySQL client or use the local Docker Compose service",
    )
    args = parser.parse_args()
    try:
        users, events = funnel.load_data(ROOT / "data")
        expected_tables = funnel.sql_result_tables(users, events)
        command, env = mysql_command(args.mode)
        check_loaded_tables(command, env, users, events)
        for output_name, table in expected_tables.items():
            sql_name = output_name.replace(".csv", ".sql")
            sql_text = (ROOT / "sql" / sql_name).read_text(encoding="utf-8")
            actual = query_rows(command, env, sql_text, sql_name)
            expected = funnel.to_rows(table)
            if actual != expected:
                raise ValueError(
                    f"{sql_name} does not match the pandas results.\n"
                    f"Expected: {expected}\nActual:   {actual}"
                )
            print(f"{sql_name}: matches pandas ({len(table)} rows).")
    except (
        OSError,
        ValueError,
        RuntimeError,
        csv.Error,
        subprocess.SubprocessError,
    ) as exc:
        print(f"Result validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
