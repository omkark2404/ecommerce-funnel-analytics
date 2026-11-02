"""Funnel metrics in pandas.

The MySQL queries in sql/ and these functions implement the same metric
definitions. scripts/validate_results.py runs the queries and checks that MySQL
returns exactly the tables produced here, and the notebook builds its charts and
extra analysis on top of these functions.

KPI definitions, including the difference between ordered-funnel
conversion and any-purchase conversion, and the seven-day window
boundary, are in docs/KPI_DEFINITIONS.md.
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


def pct(numerator: int, denominator: int, places: int = 2) -> float | None:
    """Percentage rounded half-up to the given decimals, matching MySQL ROUND().

    Policy for undefined percentages (see docs/KPI_DEFINITIONS.md):
    a zero numerator with a positive denominator is legitimately 0.0,
    but a zero denominator makes the rate *undefined* — this returns
    None, which the SQL queries express as NULL and the CSV files
    serialise as an empty cell. Returning 0.0 for 0 / 0 would claim
    a conversion rate that does not exist.
    """
    if denominator == 0:
        return None
    value = Decimal(int(numerator) * 100) / Decimal(int(denominator))
    return float(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def load_data(
    data_dir: Path, strict_duplicates: bool = True
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the user and event CSVs and stop on any integrity problem.

    strict_duplicates (default True): exact duplicate event records
    (same user, type and date) fail the load, as before. With
    strict_duplicates=False, duplicates are dropped (keeping the
    first) and a warning is issued instead — see integrity_problems()
    and docs/KPI_DEFINITIONS.md for why the same-day grain is
    ambiguous without event IDs or timestamps.
    """
    for name in (USERS_FILE, EVENTS_FILE):
        if b"\r" in (data_dir / name).read_bytes():
            raise ValueError(
                f"{name} must use LF line endings; MySQL LOAD DATA expects '\\n'."
            )
    users = pd.read_csv(data_dir / USERS_FILE, parse_dates=["signup_date"])
    events = pd.read_csv(data_dir / EVENTS_FILE, parse_dates=["event_date"])
    problems = integrity_problems(users, events, strict_duplicates=strict_duplicates)
    if problems:
        raise ValueError("Data integrity check failed:\n- " + "\n- ".join(problems))
    if not strict_duplicates:
        import warnings

        dup_mask = events.duplicated(subset=["user_id", "event_type", "event_date"])
        if dup_mask.any():
            warnings.warn(
                f"Dropped {int(dup_mask.sum())} duplicate event record(s) "
                "(same user, type and date); the funnel uses the first "
                "occurrence. Without event IDs or timestamps, a repeated "
                "same-day action cannot be distinguished from a duplicated "
                "row — see docs/KPI_DEFINITIONS.md.",
                UserWarning,
                stacklevel=2,
            )
            events = events.loc[~dup_mask].reset_index(drop=True)
    return users, events


def integrity_problems(
    users: pd.DataFrame,
    events: pd.DataFrame,
    strict_duplicates: bool = True,
) -> list[str]:
    """Describe every integrity rule the inputs break; empty when they are clean."""
    problems = []
    if users.isna().any().any() or events.isna().any().any():
        problems.append("missing values")
    if users["user_id"].duplicated().any():
        problems.append("duplicate user_id values in users")
    # Exact duplicate event records (same user, type and date). Repeated
    # events of the same type on *different* dates are legitimate in
    # clickstream data and are not flagged here; only the first
    # qualifying event counts for a stage.
    #
    # The grain is ambiguous: with dates but no event IDs or timestamps,
    # two identical rows could be one action recorded twice *or* two
    # real actions on the same day. Strict mode (the default) treats
    # them as errors, because silently dropping rows would hide a data
    # problem in the demonstration files; lenient mode
    # (strict_duplicates=False) drops them with a warning instead.
    # See docs/KPI_DEFINITIONS.md.
    if events.duplicated(subset=["user_id", "event_type", "event_date"]).any():
        n_dup = int(
            events.duplicated(subset=["user_id", "event_type", "event_date"]).sum()
        )
        if strict_duplicates:
            problems.append(
                f"{n_dup} duplicate event records (same user, type and date)"
            )
    unknown_types = set(events["event_type"]) - set(STAGES)
    if unknown_types:
        problems.append(f"unknown event types: {sorted(unknown_types)}")
    orphans = ~events["user_id"].isin(users["user_id"])
    if orphans.any():
        problems.append(f"{orphans.sum()} events reference unknown users")

    # Drop duplicate accounts here so the remaining rules can still be checked and reported.
    signup_by_user = users.drop_duplicates("user_id").set_index("user_id")[
        "signup_date"
    ]
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
        dates[stage] = (
            qualifying.reset_index(drop=True).groupby("user_id")["event_date"].min()
        )
    return dates


def first_purchase_after_signup(
    users: pd.DataFrame, events: pd.DataFrame
) -> pd.DataFrame:
    """Each user with a purchase on or after signup, and the days it took.

    Note the definition: this counts *any* purchase on or after signup,
    even if the user skipped an intermediate stage. funnel_metrics()
    is stricter — it counts only purchases reached in stage order.
    In the shipped sample the two agree (1,298 purchasers, no user
    skips a stage; see notebook §1), but they are different metrics
    and the difference is pinned by a test in tests/test_funnel.py.
    """
    purchases = events.loc[
        events["event_type"] == "purchase", ["user_id", "event_date"]
    ]
    joined = purchases.merge(users[["user_id", "signup_date"]], on="user_id")
    joined = joined[joined["event_date"] >= joined["signup_date"]]
    if joined.empty:
        # No purchasers: return the same shape with no rows, so
        # days_to_purchase() and country_conversion() still work
        # instead of failing on the .dt accessor below.
        return pd.DataFrame(
            {
                "signup_date": pd.Series(dtype="datetime64[ns]"),
                "purchase_date": pd.Series(dtype="datetime64[ns]"),
                "days_to_purchase": pd.Series(dtype="int64"),
            }
        )
    first = joined.groupby("user_id").agg(
        signup_date=("signup_date", "first"), purchase_date=("event_date", "min")
    )
    first["days_to_purchase"] = (first["purchase_date"] - first["signup_date"]).dt.days
    return first


def ordered_first_purchase(dates: pd.DataFrame) -> pd.DataFrame:
    """First *ordered* purchase per user, from stage_dates().

    Unlike first_purchase_after_signup(), this counts only purchases
    reached in stage order. Returns the same shape (signup_date,
    purchase_date, days_to_purchase) so the two definitions can be
    compared directly. See docs/KPI_DEFINITIONS.md.
    """
    ordered = dates[dates["purchase"].notna()][["signup", "purchase"]].copy()
    ordered = ordered.rename(
        columns={"signup": "signup_date", "purchase": "purchase_date"}
    )
    ordered["days_to_purchase"] = (
        ordered["purchase_date"] - ordered["signup_date"]
    ).dt.days
    return ordered


def country_conversion_ordered(
    users: pd.DataFrame, dates: pd.DataFrame
) -> pd.DataFrame:
    """Country conversion using ordered-funnel purchases only.

    Same shape as country_conversion(), but purchasers are users with
    a non-null ordered purchase date in stage_dates(). In the shipped
    sample this gives the same numbers as country_conversion();
    on data with skipped stages it will be lower. See
    docs/KPI_DEFINITIONS.md.
    """
    purchased = users["user_id"].isin(dates[dates["purchase"].notna()].index)
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


def country_conversion_mature(
    users: pd.DataFrame,
    events: pd.DataFrame,
    observation_end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Country conversion among mature users only (seven-day window).

    A sensitivity check for right-censoring: country_conversion()
    counts all users, including recent signups with less than seven
    days of follow-up. This version counts only users with a complete
    seven-day window, using mature_users(). In the shipped sample the
    two are close; the headline country table remains the all-users
    version, documented in docs/KPI_DEFINITIONS.md.
    """
    eligible, _, _ = mature_users(users, events, observation_end=observation_end)
    grouped = (
        eligible.groupby("country")["converted"]
        .agg(total_users="size", valid_purchases="sum")
        .reset_index()
    )
    grouped["conversion_rate_pct"] = [
        pct(p, n) for p, n in zip(grouped["valid_purchases"], grouped["total_users"])
    ]
    return grouped.sort_values(
        ["conversion_rate_pct", "country"], ascending=[False, True], ignore_index=True
    )


def funnel_metrics(dates: pd.DataFrame) -> pd.DataFrame:
    """Users at each stage with step and cumulative conversion (sql/02)."""
    counts = [int(dates[stage].notna().sum()) for stage in STAGES]
    rows = []
    for index, (stage, users_at_stage) in enumerate(zip(STAGES, counts)):
        previous = counts[index - 1] if index else None
        # The signup row is 100% by definition — but only when there
        # is at least one signup. With zero signups every rate in the
        # table is undefined, including this one (SQL agrees: see
        # sql/02_funnel_metrics.sql).
        signup_rate = 100.0 if counts[0] > 0 else None
        rows.append(
            {
                "stage": f"{index + 1}_{stage}",
                "users": users_at_stage,
                "previous_stage_users": previous,
                "step_conversion_pct": pct(users_at_stage, previous)
                if index
                else signup_rate,
                "cumulative_conversion_pct": pct(users_at_stage, counts[0])
                if index
                else signup_rate,
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


def country_conversion(
    users: pd.DataFrame, first_purchases: pd.DataFrame
) -> pd.DataFrame:
    """Share of each country's users with a purchase on or after signup (sql/04).

    Uses first_purchase_after_signup(), so — like days_to_purchase() —
    it counts any purchase after signup, not only ordered funnel
    purchases. See first_purchase_after_signup() for why that matters
    and why the shipped sample is unaffected.
    """
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
    users: pd.DataFrame,
    events: pd.DataFrame,
    follow_up_days: int = FOLLOW_UP_DAYS,
    observation_end: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """Users with a complete follow-up window, flagged if they bought within it.

    Window is inclusive of the boundary day: a purchase counts if
    signup_date <= purchase_date <= signup_date + follow_up_days.
    observed_through defaults to the maximum event date in the data —
    that is a proxy, not proof that tracking was complete up to that
    date: the last recorded event only shows when activity was last
    *seen*, not that nothing happened afterwards. Pass an explicit
    observation_end (e.g. the date a data export was cut) when one is
    known; the default preserves the shipped results. See
    docs/KPI_DEFINITIONS.md.
    """
    observed_through = (
        pd.Timestamp(observation_end)
        if observation_end is not None
        else events["event_date"].max()
    )
    cutoff = observed_through - pd.Timedelta(days=follow_up_days)
    eligible = users[users["signup_date"] <= cutoff].copy()
    purchases = events.loc[
        events["event_type"] == "purchase", ["user_id", "event_date"]
    ]
    window = purchases.merge(eligible[["user_id", "signup_date"]], on="user_id")
    in_window = window[
        (window["event_date"] >= window["signup_date"])
        & (
            window["event_date"]
            <= window["signup_date"] + pd.Timedelta(days=follow_up_days)
        )
    ]
    eligible["converted"] = eligible["user_id"].isin(in_window["user_id"])
    return eligible, cutoff, observed_through


def seven_day_conversion(
    users: pd.DataFrame,
    events: pd.DataFrame,
    observation_end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Conversion within seven days among users with a full window (sql/05)."""
    eligible, cutoff, observed_through = mature_users(
        users, events, observation_end=observation_end
    )
    converted = int(eligible["converted"].sum())
    # With no events at all there is no observation end: the SQL
    # query returns NULL for both dates, so use empty cells here too.
    return pd.DataFrame(
        [
            {
                "signup_cutoff": ("" if pd.isna(cutoff) else cutoff.date().isoformat()),
                "observed_through": (
                    ""
                    if pd.isna(observed_through)
                    else observed_through.date().isoformat()
                ),
                "follow_up_days": FOLLOW_UP_DAYS,
                "eligible_users": len(eligible),
                "purchasers_within_7_days": converted,
                "conversion_rate_pct": pct(converted, len(eligible)),
            }
        ]
    )


def sql_result_tables(
    users: pd.DataFrame, events: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """The tables each SQL query must reproduce, keyed by output file name."""
    first_purchases = first_purchase_after_signup(users, events)
    return {
        "02_funnel_metrics.csv": funnel_metrics(stage_dates(events)),
        "03_days_to_purchase.csv": days_to_purchase(first_purchases),
        "04_country_segmentation.csv": country_conversion(users, first_purchases),
        "05_seven_day_conversion.csv": seven_day_conversion(users, events),
    }


def canonical_source_hash(users: pd.DataFrame, events: pd.DataFrame) -> str:
    """SHA-256 over every source record, in a canonical sorted form.

    Aggregate counts can hide a swapped country, a shifted date or a
    changed event type, so scripts/validate_results.py compares this
    hash between the CSVs and the MySQL tables instead of relying on
    counts alone. Each record becomes one line
    ``u|<user_id>|<signup_date>|<country>`` or
    ``e|<user_id>|<event_type>|<event_date>``; lines are sorted, so
    row order in the file does not matter, and duplicate lines are
    kept, so a duplicated record changes the hash. Changing any field,
    deleting a record or adding one all change the hash.
    """
    import hashlib

    user_lines = [
        f"u|{int(row.user_id)}|{row.signup_date:%Y-%m-%d}|{row.country}"
        for row in users.sort_values("user_id").itertuples(index=False)
    ]
    event_lines = [
        f"e|{int(row.user_id)}|{row.event_type}|{row.event_date:%Y-%m-%d}"
        for row in events.sort_values(
            ["user_id", "event_type", "event_date"]
        ).itertuples(index=False)
    ]
    digest = hashlib.sha256()
    for line in sorted(user_lines + event_lines):
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def to_csv_text(table: pd.DataFrame) -> str:
    """CSV text with two-decimal percentages and LF line endings."""
    return table.to_csv(index=False, lineterminator="\n", float_format="%.2f")


def to_rows(table: pd.DataFrame) -> list[list[str]]:
    """Header plus rows as strings, in the same form the CSV files use."""
    return list(csv.reader(io.StringIO(to_csv_text(table))))
