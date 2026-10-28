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
            raise RuntimeError("Docker Compose is not installed or not available on PATH.")
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
        raise RuntimeError("The MySQL client is not installed or not available on PATH.")
    env["MYSQL_PWD"] = env.get("MYSQL_PWD", env.get("MYSQL_ROOT_PASSWORD", ""))
    if not env["MYSQL_PWD"]:
        raise ValueError("Set MYSQL_PWD (or MYSQL_ROOT_PASSWORD) before connecting to MySQL.")
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
    return [[("" if value in ("NULL", r"\N") else value) for value in row] for row in rows]


def check_loaded_tables(command: list[str], env: dict[str, str], users, events) -> None:
    """Confirm MySQL holds the same rows as the CSVs before comparing results."""
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
    print(f"MySQL tables match the CSVs ({len(users):,} users, {len(events):,} events).")


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
    except (OSError, ValueError, RuntimeError, csv.Error, subprocess.SubprocessError) as exc:
        print(f"Result validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
