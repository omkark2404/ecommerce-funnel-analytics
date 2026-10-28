"""Funnel metrics in pandas.

The MySQL queries in sql/ and these functions implement the same metric
definitions. scripts/validate_results.py runs the queries and checks that MySQL
returns exactly the tables produced here, and the notebook builds its charts and
extra analysis on top of these functions.
"""

from __future__ import annotations

import csv
import io
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd

STAGES = ["signup", "login", "view_product", "add_to_cart", "purchase"]
STAGE_LABELS = {
    "signup": "Signup",
    "login": "Login",
    "view_product": "Product view",
    "add_to_cart": "Add to cart",
    "purchase": "Purchase",
}
FOLLOW_UP_DAYS = 7
USERS_FILE = "product_users_clean.csv"
EVENTS_FILE = "product_events_clean.csv"


def pct(numerator: int, denominator: int, places: int = 2) -> float:
    """Percentage rounded half-up to the given decimals, matching MySQL ROUND()."""
    if denominator == 0:
        return 0.0
    value = Decimal(int(numerator) * 100) / Decimal(int(denominator))
    return float(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def load_data(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the user and event CSVs and stop on any integrity problem."""
    for name in (USERS_FILE, EVENTS_FILE):
        if b"\r" in (data_dir / name).read_bytes():
            raise ValueError(
                f"{name} must use LF line endings; MySQL LOAD DATA expects '\\n'."
            )
    users = pd.read_csv(data_dir / USERS_FILE, parse_dates=["signup_date"])
    events = pd.read_csv(data_dir / EVENTS_FILE, parse_dates=["event_date"])
    problems = integrity_problems(users, events)
    if problems:
        raise ValueError("Data integrity check failed:\n- " + "\n- ".join(problems))
    return users, events


def integrity_problems(users: pd.DataFrame, events: pd.DataFrame) -> list[str]:
    """Describe every integrity rule the inputs break; empty when they are clean."""
    problems = []
    if users.isna().any().any() or events.isna().any().any():
        problems.append("missing values")
    if users["user_id"].duplicated().any():
        problems.append("duplicate user_id values in users")
    unknown_types = set(events["event_type"]) - set(STAGES)
    if unknown_types:
        problems.append(f"unknown event types: {sorted(unknown_types)}")
    orphans = ~events["user_id"].isin(users["user_id"])
    if orphans.any():
        problems.append(f"{orphans.sum()} events reference unknown users")

    # Drop duplicate accounts here so the remaining rules can still be checked and reported.
    signup_by_user = users.drop_duplicates("user_id").set_index("user_id")["signup_date"]
    before_signup = events["event_date"] < events["user_id"].map(signup_by_user)
    if before_signup.any():
        problems.append(f"{before_signup.sum()} events occur before the user's signup")

    first_signup_event = (
        events[events["event_type"] == "signup"].groupby("user_id")["event_date"].min()
    )
    mismatched = signup_by_user.ne(first_signup_event.reindex(signup_by_user.index))
    if mismatched.any():
        problems.append(
            f"{mismatched.sum()} users lack a signup event matching users.signup_date"
        )
    return problems


def stage_dates(events: pd.DataFrame) -> pd.DataFrame:
    """First qualifying date per user and stage, one row per signed-up user.

    A user reaches a stage only with an event of that type on or after the date
    they reached the previous stage. Dates have no time of day, so same-day
    events are assumed to follow the stage order.
    """
    by_type = {
        stage: events.loc[events["event_type"] == stage, ["user_id", "event_date"]]
        for stage in STAGES
    }
    dates = by_type["signup"].groupby("user_id")["event_date"].min().to_frame("signup")
    for previous, stage in zip(STAGES, STAGES[1:]):
        reached_previous = dates[previous].dropna().rename("previous_date")
        candidates = by_type[stage].join(reached_previous, on="user_id", how="inner")
        qualifying = candidates[candidates["event_date"] >= candidates["previous_date"]]
        dates[stage] = qualifying.reset_index(drop=True).groupby("user_id")["event_date"].min()
    return dates


def first_purchase_after_signup(users: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Each user with a purchase on or after signup, and the days it took."""
    purchases = events.loc[events["event_type"] == "purchase", ["user_id", "event_date"]]
    joined = purchases.merge(users[["user_id", "signup_date"]], on="user_id")
    joined = joined[joined["event_date"] >= joined["signup_date"]]
    first = joined.groupby("user_id").agg(
        signup_date=("signup_date", "first"), purchase_date=("event_date", "min")
    )
    first["days_to_purchase"] = (first["purchase_date"] - first["signup_date"]).dt.days
    return first


def funnel_metrics(dates: pd.DataFrame) -> pd.DataFrame:
    """Users at each stage with step and cumulative conversion (sql/02)."""
    counts = [int(dates[stage].notna().sum()) for stage in STAGES]
    rows = []
    for index, (stage, users_at_stage) in enumerate(zip(STAGES, counts)):
        previous = counts[index - 1] if index else None
        rows.append(
            {
                "stage": f"{index + 1}_{stage}",
                "users": users_at_stage,
                "previous_stage_users": previous,
                "step_conversion_pct": pct(users_at_stage, previous) if index else 100.0,
                "cumulative_conversion_pct": pct(users_at_stage, counts[0]),
            }
        )
    table = pd.DataFrame(rows)
    table["previous_stage_users"] = table["previous_stage_users"].astype("Int64")
    return table


def days_to_purchase(first_purchases: pd.DataFrame) -> pd.DataFrame:
    """Observed purchasers by whole days from signup to first purchase (sql/03)."""
    counts = first_purchases["days_to_purchase"].value_counts().sort_index()
    total = int(counts.sum())
    cumulative = counts.cumsum()
    return pd.DataFrame(
        {
            "days_to_purchase": counts.index.astype(int),
            "observed_purchasers": counts.to_numpy(),
            "pct_of_observed_purchasers": [pct(n, total) for n in counts],
            "cumulative_pct": [pct(n, total) for n in cumulative],
        }
    )


def country_conversion(users: pd.DataFrame, first_purchases: pd.DataFrame) -> pd.DataFrame:
    """Share of each country's users with a purchase on or after signup (sql/04)."""
    purchased = users["user_id"].isin(first_purchases.index)
    grouped = (
        users.assign(purchased=purchased)
        .groupby("country")["purchased"]
        .agg(total_users="size", valid_purchases="sum")
        .reset_index()
    )
    grouped["conversion_rate_pct"] = [
        pct(p, n) for p, n in zip(grouped["valid_purchases"], grouped["total_users"])
    ]
    return grouped.sort_values(
        ["conversion_rate_pct", "country"], ascending=[False, True], ignore_index=True
    )


def mature_users(
    users: pd.DataFrame, events: pd.DataFrame, follow_up_days: int = FOLLOW_UP_DAYS
) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """Users with a complete follow-up window, flagged if they bought within it."""
    observed_through = events["event_date"].max()
    cutoff = observed_through - pd.Timedelta(days=follow_up_days)
    eligible = users[users["signup_date"] <= cutoff].copy()
    purchases = events.loc[events["event_type"] == "purchase", ["user_id", "event_date"]]
    window = purchases.merge(eligible[["user_id", "signup_date"]], on="user_id")
    in_window = window[
        (window["event_date"] >= window["signup_date"])
        & (window["event_date"] <= window["signup_date"] + pd.Timedelta(days=follow_up_days))
    ]
    eligible["converted"] = eligible["user_id"].isin(in_window["user_id"])
    return eligible, cutoff, observed_through


def seven_day_conversion(users: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Conversion within seven days among users with a full window (sql/05)."""
    eligible, cutoff, observed_through = mature_users(users, events)
    converted = int(eligible["converted"].sum())
    return pd.DataFrame(
        [
            {
                "signup_cutoff": cutoff.date().isoformat(),
                "observed_through": observed_through.date().isoformat(),
                "follow_up_days": FOLLOW_UP_DAYS,
                "eligible_users": len(eligible),
                "purchasers_within_7_days": converted,
                "conversion_rate_pct": pct(converted, len(eligible)),
            }
        ]
    )


def sql_result_tables(users: pd.DataFrame, events: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """The tables each SQL query must reproduce, keyed by output file name."""
    first_purchases = first_purchase_after_signup(users, events)
    return {
        "02_funnel_metrics.csv": funnel_metrics(stage_dates(events)),
        "03_days_to_purchase.csv": days_to_purchase(first_purchases),
        "04_country_segmentation.csv": country_conversion(users, first_purchases),
        "05_seven_day_conversion.csv": seven_day_conversion(users, events),
    }


def to_csv_text(table: pd.DataFrame) -> str:
    """CSV text with two-decimal percentages and LF line endings."""
    return table.to_csv(index=False, lineterminator="\n", float_format="%.2f")


def to_rows(table: pd.DataFrame) -> list[list[str]]:
    """Header plus rows as strings, in the same form the CSV files use."""
    return list(csv.reader(io.StringIO(to_csv_text(table))))
