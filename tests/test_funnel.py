"""Unit tests for src/funnel.py.

Covers:
- stage ordering (stage_dates follows sequence; a user who skips a stage
  does not advance past it)
- every rule in integrity_problems():
    - duplicate user_ids in users
    - events that reference unknown users
    - events that occur before the user's signup date
    - signup mismatch (users.signup_date != first signup event date)
    - unknown event types
- pct() half-up rounding
- the seven-day window (mature_users / seven_day_conversion)
- the core output tables (funnel_metrics, days_to_purchase,
  country_conversion, sql_result_tables) — these were previously
  checked only by running the notebook / CI, not by unit tests
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.funnel import (
    STAGES,
    canonical_source_hash,
    country_conversion,
    days_to_purchase,
    first_purchase_after_signup,
    funnel_metrics,
    integrity_problems,
    mature_users,
    pct,
    seven_day_conversion,
    sql_result_tables,
    stage_dates,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_users(*rows: tuple) -> pd.DataFrame:
    """Create a minimal users DataFrame from (user_id, signup_date, country) tuples."""
    return pd.DataFrame(rows, columns=["user_id", "signup_date", "country"]).assign(
        signup_date=lambda df: pd.to_datetime(df["signup_date"])
    )


def _make_events(*rows: tuple) -> pd.DataFrame:
    """Create a minimal events DataFrame from (user_id, event_type, event_date) tuples."""
    return pd.DataFrame(rows, columns=["user_id", "event_type", "event_date"]).assign(
        event_date=lambda df: pd.to_datetime(df["event_date"])
    )


# ---------------------------------------------------------------------------
# pct() – half-up rounding
# ---------------------------------------------------------------------------


class TestPct:
    def test_example_43_0(self):
        assert pct(2577, 6000, places=1) == 43.0

    def test_example_59_7(self):
        assert pct(2577, 4320, places=1) == 59.7

    def test_zero_denominator_is_undefined(self):
        # A rate with no denominator is undefined, not 0%: 0 / 0 has
        # no value, and reporting 0% would claim a conversion rate
        # that does not exist. SQL expresses this as NULL (see
        # sql/02_funnel_metrics.sql, which uses NULLIF).
        assert pct(100, 0) is None
        assert pct(0, 0) is None

    def test_zero_numerator_with_positive_denominator_is_zero(self):
        # The opposite case is a real measurement: nobody converted.
        assert pct(0, 100) == 0.0

    def test_100_pct(self):
        assert pct(5, 5) == 100.0

    def test_two_decimal_places(self):
        # 1/3 ≈ 33.33...% rounds half-up to 33.33
        assert pct(1, 3, places=2) == 33.33

    def test_half_up_not_half_even(self):
        # 2.5 must round to 3, not 2 (banker's rounding would give 2)
        assert pct(5, 200, places=0) == 3.0  # 2.5 -> 3


# ---------------------------------------------------------------------------
# integrity_problems()
# ---------------------------------------------------------------------------


class TestIntegrityProblems:
    """Each sub-test injects exactly one violation and checks the right rule fires."""

    def _clean_pair(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """A perfectly valid pair of frames with one user and two events."""
        users = _make_users((1, "2024-01-01", "US"))
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "login", "2024-01-02"),
        )
        return users, events

    def test_clean_data_has_no_problems(self):
        users, events = self._clean_pair()
        assert integrity_problems(users, events) == []

    def test_duplicate_user_id(self):
        users = _make_users(
            (1, "2024-01-01", "US"),
            (1, "2024-01-01", "US"),  # duplicate
        )
        events = _make_events(
            (1, "signup", "2024-01-01"),
        )
        problems = integrity_problems(users, events)
        assert any("duplicate" in p for p in problems)

    def test_unknown_user_in_events(self):
        users = _make_users((1, "2024-01-01", "US"))
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (99, "login", "2024-01-02"),  # user 99 not in users
        )
        problems = integrity_problems(users, events)
        assert any("unknown users" in p for p in problems)

    def test_event_before_signup(self):
        users = _make_users((1, "2024-01-05", "US"))
        events = _make_events(
            (1, "signup", "2024-01-05"),
            (1, "login", "2024-01-01"),  # before signup date
        )
        problems = integrity_problems(users, events)
        assert any("before" in p for p in problems)

    def test_signup_mismatch(self):
        # users.signup_date is Jan 1, but the signup event is Jan 2
        users = _make_users((1, "2024-01-01", "US"))
        events = _make_events(
            (1, "signup", "2024-01-02"),  # date mismatch
        )
        problems = integrity_problems(users, events)
        assert any("signup event" in p for p in problems)

    def test_unknown_event_type(self):
        users = _make_users((1, "2024-01-01", "US"))
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "refund", "2024-01-03"),  # not in STAGES
        )
        problems = integrity_problems(users, events)
        assert any("unknown event types" in p for p in problems)

    def test_multiple_violations_reported(self):
        # Duplicate user + unknown event type both appear
        users = _make_users(
            (1, "2024-01-01", "US"),
            (1, "2024-01-01", "US"),
        )
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "refund", "2024-01-02"),
        )
        problems = integrity_problems(users, events)
        assert any("duplicate" in p for p in problems)
        assert any("unknown event types" in p for p in problems)


# ---------------------------------------------------------------------------
# stage_dates() – ordering rules
# ---------------------------------------------------------------------------


class TestStageDates:
    def _full_funnel_events(self, user_id: int = 1) -> pd.DataFrame:
        """One user who completes every stage in order."""
        rows = [
            (user_id, "signup", "2024-01-01"),
            (user_id, "login", "2024-01-02"),
            (user_id, "view_product", "2024-01-03"),
            (user_id, "add_to_cart", "2024-01-04"),
            (user_id, "purchase", "2024-01-05"),
        ]
        return _make_events(*rows)

    def test_all_stages_populated_for_complete_user(self):
        events = self._full_funnel_events()
        dates = stage_dates(events)
        for stage in STAGES:
            assert pd.notna(dates.loc[1, stage])

    def test_stage_order_is_non_decreasing(self):
        events = self._full_funnel_events()
        dates = stage_dates(events)
        row = dates.loc[1]
        for prev, curr in zip(STAGES, STAGES[1:]):
            assert row[curr] >= row[prev]

    def test_user_who_skips_login_does_not_reach_view_product(self):
        """
        stage_dates only processes users who appear in signup events.
        A user who has a signup event but no login event must stop at signup.
        """
        events = _make_events(
            (1, "signup", "2024-01-01"),
            # no login for user 1
            (1, "view_product", "2024-01-03"),
            (1, "add_to_cart", "2024-01-04"),
            (1, "purchase", "2024-01-05"),
        )
        dates = stage_dates(events)
        # signup is present
        assert pd.notna(dates.loc[1, "signup"])
        # without login, everything from login onwards must be NaT
        assert pd.isna(dates.loc[1, "login"])
        assert pd.isna(dates.loc[1, "view_product"])
        assert pd.isna(dates.loc[1, "add_to_cart"])
        assert pd.isna(dates.loc[1, "purchase"])

    def test_out_of_order_event_before_previous_stage_not_counted(self):
        """
        A login event that occurs before the signup date must not count as the
        qualifying login. Only the login on or after signup should be used.
        """
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "login", "2023-12-31"),  # before signup – should be ignored
            (1, "login", "2024-01-02"),  # valid login
        )
        dates = stage_dates(events)
        # login should be 2024-01-02 (qualifying date), not 2023-12-31
        assert dates.loc[1, "login"] == pd.Timestamp("2024-01-02")

    def test_multiple_users_independent(self):
        events = pd.concat(
            [self._full_funnel_events(user_id=1), self._full_funnel_events(user_id=2)],
            ignore_index=True,
        )
        dates = stage_dates(events)
        assert len(dates) == 2
        for uid in (1, 2):
            for stage in STAGES:
                assert pd.notna(dates.loc[uid, stage])

    def test_same_day_events_advance_the_funnel(self):
        # All events on the same day – same-day is allowed by the >= comparison
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "login", "2024-01-01"),
            (1, "view_product", "2024-01-01"),
            (1, "add_to_cart", "2024-01-01"),
            (1, "purchase", "2024-01-01"),
        )
        dates = stage_dates(events)
        for stage in STAGES:
            assert pd.notna(dates.loc[1, stage])


# ---------------------------------------------------------------------------
# Seven-day window (mature_users / seven_day_conversion)
# ---------------------------------------------------------------------------


class TestSevenDayWindow:
    """
    Dataset designed so that:
      observed_through = events["event_date"].max()

    We use three users and anchor observed_through explicitly so the cutoff
    (observed_through - 7 days) lands where we need it.

    Plan:
      observed_through = 2024-01-20  (user 2's purchase, the latest event)
      cutoff           = 2024-01-13  (= 2024-01-20 - 7 days)

      user 1: signup 2024-01-01 (<= cutoff -> eligible)
              purchase 2024-01-05 (within [signup, signup+7] -> converted)
      user 2: signup 2024-01-06 (<= cutoff -> eligible)
              purchase 2024-01-20 (> signup+7 = 2024-01-13 -> NOT converted)
      user 3: signup 2024-01-15 (> cutoff -> ineligible)
    """

    def _users(self) -> pd.DataFrame:
        return _make_users(
            (1, "2024-01-01", "US"),
            (2, "2024-01-06", "UK"),
            (3, "2024-01-15", "DE"),
        )

    def _events(self) -> pd.DataFrame:
        return _make_events(
            # user 1
            (1, "signup", "2024-01-01"),
            (1, "purchase", "2024-01-05"),  # day 4 within 7-day window -> converted
            # user 2
            (2, "signup", "2024-01-06"),
            (
                2,
                "purchase",
                "2024-01-20",
            ),  # day 14, outside 7-day window -> not converted
            # user 3 (ineligible: signup 2024-01-15 > cutoff 2024-01-13)
            (3, "signup", "2024-01-15"),
        )

    def test_observed_through_is_latest_event(self):
        _, _, observed_through = mature_users(self._users(), self._events())
        assert observed_through == pd.Timestamp("2024-01-20")

    def test_cutoff_is_seven_days_before_observed_through(self):
        _, cutoff, observed_through = mature_users(self._users(), self._events())
        assert (observed_through - cutoff).days == 7

    def test_eligible_users_excludes_late_signups(self):
        eligible, cutoff, _ = mature_users(self._users(), self._events())
        # user 3 signed up after cutoff – must not appear
        assert set(eligible["user_id"]) == {1, 2}

    def test_user_within_window_is_converted(self):
        eligible, _, _ = mature_users(self._users(), self._events())
        row = eligible.set_index("user_id")
        assert bool(row.loc[1, "converted"]) is True

    def test_user_outside_window_is_not_converted(self):
        eligible, _, _ = mature_users(self._users(), self._events())
        row = eligible.set_index("user_id")
        assert bool(row.loc[2, "converted"]) is False

    def test_seven_day_conversion_counts(self):
        result = seven_day_conversion(self._users(), self._events())
        assert result.loc[0, "eligible_users"] == 2
        assert result.loc[0, "purchasers_within_7_days"] == 1

    def test_seven_day_conversion_rate(self):
        result = seven_day_conversion(self._users(), self._events())
        assert result.loc[0, "conversion_rate_pct"] == 50.0

    def test_explicit_observation_end_moves_the_cutoff(self):
        # Same data, but the observation is cut at 2024-01-14 instead
        # of the latest event (2024-01-20): cutoff becomes 2024-01-07,
        # so user 2 (signup 2024-01-06) is still eligible but user 1's
        # purchase is unchanged — and the reported observed_through
        # is the explicit date, not the max event date.
        result = seven_day_conversion(
            self._users(),
            self._events(),
            observation_end=pd.Timestamp("2024-01-14"),
        )
        assert result.loc[0, "observed_through"] == "2024-01-14"
        assert result.loc[0, "signup_cutoff"] == "2024-01-07"
        assert result.loc[0, "eligible_users"] == 2
        assert result.loc[0, "purchasers_within_7_days"] == 1

    def test_default_observation_end_is_max_event_date(self):
        # No explicit end: behaviour is unchanged from before.
        result = seven_day_conversion(self._users(), self._events())
        assert result.loc[0, "observed_through"] == "2024-01-20"
        assert result.loc[0, "signup_cutoff"] == "2024-01-13"


# ---------------------------------------------------------------------------
# Core output tables (funnel_metrics, days_to_purchase, country_conversion)
# ---------------------------------------------------------------------------


class TestCoreOutputTables:
    """Three users: one completes the funnel, one stops at login,
    one stops at product view.

    Expected funnel: signup 3, login 3, view 2, cart 1, purchase 1.
    """

    def _data(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        users = _make_users(
            (1, "2024-01-01", "US"),
            (2, "2024-01-01", "US"),
            (3, "2024-01-02", "UK"),
        )
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "login", "2024-01-02"),
            (1, "view_product", "2024-01-03"),
            (1, "add_to_cart", "2024-01-04"),
            (1, "purchase", "2024-01-05"),
            (2, "signup", "2024-01-01"),
            (2, "login", "2024-01-02"),
            (3, "signup", "2024-01-02"),
            (3, "login", "2024-01-03"),
            (3, "view_product", "2024-01-04"),
        )
        return users, events

    def test_funnel_metrics_counts(self):
        _, events = self._data()
        table = funnel_metrics(stage_dates(events))
        assert table["users"].tolist() == [3, 3, 2, 1, 1]
        assert table["stage"].tolist() == [
            "1_signup",
            "2_login",
            "3_view_product",
            "4_add_to_cart",
            "5_purchase",
        ]

    def test_funnel_metrics_rates(self):
        _, events = self._data()
        table = funnel_metrics(stage_dates(events))
        # view: 2/3, cart: 1/2, purchase: 1/1; cumulative purchase: 1/3
        assert table["step_conversion_pct"].tolist() == [
            100.0,
            100.0,
            66.67,
            50.0,
            100.0,
        ]
        assert table["cumulative_conversion_pct"].tolist() == [
            100.0,
            100.0,
            66.67,
            33.33,
            33.33,
        ]

    def test_days_to_purchase(self):
        users, events = self._data()
        first = first_purchase_after_signup(users, events)
        table = days_to_purchase(first)
        assert len(table) == 1
        assert table.loc[0, "days_to_purchase"] == 4
        assert table.loc[0, "observed_purchasers"] == 1
        assert table.loc[0, "pct_of_observed_purchasers"] == 100.0

    def test_country_conversion(self):
        users, events = self._data()
        first = first_purchase_after_signup(users, events)
        table = country_conversion(users, first)
        by_country = table.set_index("country")
        assert by_country.loc["US", "total_users"] == 2
        assert by_country.loc["US", "valid_purchases"] == 1
        assert by_country.loc["US", "conversion_rate_pct"] == 50.0
        assert by_country.loc["UK", "valid_purchases"] == 0
        # Sorted by conversion rate, highest first.
        assert table["country"].tolist() == ["US", "UK"]

    def test_country_conversion_counts_any_purchase_after_signup(self):
        """country_conversion / days_to_purchase count a purchase on or
        after signup even if the user skipped an intermediate stage,
        while funnel_metrics counts only ordered purchases.

        In the shipped sample no user skips a stage, so both
        definitions give the same 1,298 purchasers — but the
        difference is a real definition, not an accident, so it is
        pinned here and documented in src/funnel.py.
        """
        users = _make_users((1, "2024-01-01", "US"))
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "purchase", "2024-01-02"),  # no login / view / cart
        )
        first = first_purchase_after_signup(users, events)
        assert country_conversion(users, first).loc[0, "valid_purchases"] == 1
        assert funnel_metrics(stage_dates(events))["users"].tolist()[-1] == 0

    def test_sql_result_tables_keys_and_shapes(self):
        users, events = self._data()
        tables = sql_result_tables(users, events)
        assert set(tables) == {
            "02_funnel_metrics.csv",
            "03_days_to_purchase.csv",
            "04_country_segmentation.csv",
            "05_seven_day_conversion.csv",
        }
        assert len(tables["02_funnel_metrics.csv"]) == 5
        assert len(tables["03_days_to_purchase.csv"]) == 1
        assert len(tables["04_country_segmentation.csv"]) == 2
        assert len(tables["05_seven_day_conversion.csv"]) == 1


# ---------------------------------------------------------------------------
# Edge cases added in the analytics-quality remediation
# ---------------------------------------------------------------------------

from src.funnel import (  # noqa: E402
    country_conversion_mature,
    country_conversion_ordered,
    load_data,
    ordered_first_purchase,
)


class TestDuplicateEvents:
    def test_duplicate_event_record_is_a_problem(self):
        users = _make_users((1, "2024-01-01", "US"))
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "login", "2024-01-02"),
            (1, "login", "2024-01-02"),  # exact duplicate record
        )
        problems = integrity_problems(users, events)
        assert any("duplicate event" in p for p in problems)

    def test_repeat_event_on_different_date_is_not_a_duplicate(self):
        # Repeated logins on different days are legitimate clickstream data.
        users = _make_users((1, "2024-01-01", "US"))
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "login", "2024-01-02"),
            (1, "login", "2024-01-03"),
        )
        problems = integrity_problems(users, events)
        assert not any("duplicate event" in p for p in problems)
        # stage_dates uses the first qualifying login
        assert stage_dates(events).loc[1, "login"] == pd.Timestamp("2024-01-02")


class TestMultipleEventsSameStage:
    def test_first_qualifying_event_wins(self):
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "login", "2024-01-05"),
            (1, "login", "2024-01-03"),
            (1, "login", "2024-01-02"),
        )
        assert stage_dates(events).loc[1, "login"] == pd.Timestamp("2024-01-02")


class TestSevenDayBoundary:
    """Purchase exactly 7 days after signup counts; day 8 does not."""

    def _pair(self, purchase_day: int):
        users = _make_users((1, "2024-01-01", "US"))
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "purchase", f"2024-01-{1 + purchase_day:02d}"),
            # latest event anchors observed_through so user 1 is mature
            (1, "login", "2024-01-20"),
        )
        return users, events

    def test_day_7_counts(self):
        users, events = self._pair(7)
        eligible, _, _ = mature_users(users, events)
        assert bool(eligible.set_index("user_id").loc[1, "converted"]) is True

    def test_day_8_does_not_count(self):
        users, events = self._pair(8)
        eligible, _, _ = mature_users(users, events)
        assert bool(eligible.set_index("user_id").loc[1, "converted"]) is False

    def test_purchase_after_seven_days_still_in_days_table(self):
        # >7-day purchases are not a failure: they appear in days_to_purchase
        # (any-purchase) even though they are excluded from seven-day conversion.
        users = _make_users((1, "2024-01-01", "US"))
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "purchase", "2024-01-20"),
        )
        first = first_purchase_after_signup(users, events)
        assert first.loc[1, "days_to_purchase"] == 19
        assert days_to_purchase(first).loc[0, "days_to_purchase"] == 19


class TestZeroConversionAndEmpty:
    def test_signup_only_user(self):
        events = _make_events((1, "signup", "2024-01-01"))
        table = funnel_metrics(stage_dates(events))
        assert table["users"].tolist() == [1, 0, 0, 0, 0]
        # Cumulative conversion is 0% (0 of 1 signup) — a real rate.
        assert table["cumulative_conversion_pct"].tolist()[-1] == 0.0
        # Step rates after login are undefined, not 0%: their
        # denominators (users at the previous stage) are zero.
        assert table.loc[1, "step_conversion_pct"] == 0.0  # login: 0 of 1
        assert pd.isna(table.loc[2, "step_conversion_pct"])  # view: 0 of 0
        assert pd.isna(table.loc[3, "step_conversion_pct"])  # cart: 0 of 0
        assert pd.isna(table.loc[4, "step_conversion_pct"])  # purchase: 0 of 0

    def test_empty_funnel_is_undefined_not_100_pct(self):
        # No users at all: even the signup row is undefined, not 100%.
        dates = stage_dates(_make_events())
        table = funnel_metrics(dates)
        assert table["users"].tolist() == [0, 0, 0, 0, 0]
        assert table["step_conversion_pct"].isna().all()
        assert table["cumulative_conversion_pct"].isna().all()

    def test_seven_day_conversion_with_no_eligible_users_is_undefined(self):
        users = _make_users((1, "2024-01-01", "US"))
        events = _make_events((1, "signup", "2024-01-01"))
        # Explicit observation end before any user is mature: no one
        # has a complete seven-day window.
        table = seven_day_conversion(
            users, events, observation_end=pd.Timestamp("2024-01-02")
        )
        assert table.loc[0, "eligible_users"] == 0
        assert pd.isna(table.loc[0, "conversion_rate_pct"])

    def test_empty_tables_have_no_rows_and_no_errors(self):
        users = _make_users()
        events = _make_events()
        tables = sql_result_tables(users, events)
        assert tables["02_funnel_metrics.csv"]["users"].tolist() == [0, 0, 0, 0, 0]
        assert len(tables["03_days_to_purchase.csv"]) == 0
        assert len(tables["04_country_segmentation.csv"]) == 0
        seven = tables["05_seven_day_conversion.csv"]
        assert seven.loc[0, "eligible_users"] == 0
        assert seven.loc[0, "purchasers_within_7_days"] == 0
        assert seven.loc[0, "signup_cutoff"] == ""
        assert seven.loc[0, "observed_through"] == ""
        assert pd.isna(seven.loc[0, "conversion_rate_pct"])

    def test_zero_conversion_group(self):
        users = _make_users((1, "2024-01-01", "US"), (2, "2024-01-01", "UK"))
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (2, "signup", "2024-01-01"),
        )
        first = first_purchase_after_signup(users, events)
        assert len(first) == 0
        table = country_conversion(users, first)
        assert table["valid_purchases"].tolist() == [0, 0]
        assert table["conversion_rate_pct"].tolist() == [0.0, 0.0]

    def test_pct_zero_denominator(self):
        # See TestPct: 0 / 0 is undefined (None), not 0%.
        assert pct(0, 0) is None


class TestOrderedVsAnyPurchase:
    def test_ordered_first_purchase_excludes_skip(self):
        users = _make_users((1, "2024-01-01", "US"))
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "purchase", "2024-01-02"),
        )
        dates = stage_dates(events)
        assert len(ordered_first_purchase(dates)) == 0
        assert len(first_purchase_after_signup(users, events)) == 1

    def test_ordered_and_any_agree_on_full_funnel(self):
        users = _make_users((1, "2024-01-01", "US"))
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "login", "2024-01-02"),
            (1, "view_product", "2024-01-03"),
            (1, "add_to_cart", "2024-01-04"),
            (1, "purchase", "2024-01-05"),
        )
        dates = stage_dates(events)
        ordered = ordered_first_purchase(dates)
        any_purchase = first_purchase_after_signup(users, events)
        assert len(ordered) == len(any_purchase) == 1
        assert (
            ordered.loc[1, "days_to_purchase"]
            == any_purchase.loc[1, "days_to_purchase"]
        )

    def test_country_conversion_ordered(self):
        users = _make_users((1, "2024-01-01", "US"), (2, "2024-01-01", "UK"))
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "purchase", "2024-01-02"),  # US skips stages
            (2, "signup", "2024-01-01"),
            (2, "login", "2024-01-02"),
            (2, "view_product", "2024-01-03"),
            (2, "add_to_cart", "2024-01-04"),
            (2, "purchase", "2024-01-05"),  # UK completes the funnel
        )
        dates = stage_dates(events)
        ordered = country_conversion_ordered(users, dates)
        by_country = ordered.set_index("country")
        assert by_country.loc["US", "valid_purchases"] == 0
        assert by_country.loc["UK", "valid_purchases"] == 1

    def test_country_conversion_mature_excludes_recent_signups(self):
        users = _make_users((1, "2024-01-01", "US"), (2, "2024-01-19", "US"))
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "purchase", "2024-01-03"),
            (2, "signup", "2024-01-19"),
            (2, "purchase", "2024-01-20"),
        )
        table = country_conversion_mature(users, events)
        # Only user 1 is mature (observed_through 2024-01-20, cutoff 2024-01-13)
        assert table.loc[0, "total_users"] == 1
        assert table.loc[0, "valid_purchases"] == 1


class TestCanonicalSourceHash:
    """canonical_source_hash() must change when any record changes,
    even when every aggregate count stays the same."""

    def _data(self):
        users = _make_users((1, "2024-01-01", "US"), (2, "2024-01-01", "UK"))
        events = _make_events(
            (1, "signup", "2024-01-01"),
            (1, "login", "2024-01-02"),
            (2, "signup", "2024-01-01"),
        )
        return users, events

    def test_same_data_same_hash(self):
        users, events = self._data()
        assert canonical_source_hash(users, events) == canonical_source_hash(
            users, events
        )

    def test_row_order_does_not_change_hash(self):
        users, events = self._data()
        shuffled = events.iloc[::-1].reset_index(drop=True)
        assert canonical_source_hash(users, events) == canonical_source_hash(
            users, shuffled
        )

    def test_swapped_countries_change_hash_but_not_counts(self):
        users, events = self._data()
        swapped = users.copy()
        swapped["country"] = ["UK", "US"]  # same counts per country
        assert canonical_source_hash(users, events) != canonical_source_hash(
            swapped, events
        )

    def test_shifted_date_changes_hash(self):
        users, events = self._data()
        shifted = events.copy()
        shifted.loc[1, "event_date"] = pd.Timestamp("2024-01-03")
        assert canonical_source_hash(users, events) != canonical_source_hash(
            users, shifted
        )

    def test_changed_event_type_changes_hash(self):
        users, events = self._data()
        changed = events.copy()
        changed.loc[1, "event_type"] = "view_product"
        assert canonical_source_hash(users, events) != canonical_source_hash(
            users, changed
        )

    def test_missing_or_extra_record_changes_hash(self):
        users, events = self._data()
        assert canonical_source_hash(users, events) != canonical_source_hash(
            users, events.iloc[:-1]
        )
        assert canonical_source_hash(users, events) != canonical_source_hash(
            users,
            pd.concat(
                [events, _make_events((2, "login", "2024-01-02"))],
                ignore_index=True,
            ),
        )

    def test_duplicated_record_changes_hash(self):
        users, events = self._data()
        doubled = pd.concat([events, events.iloc[[0]]], ignore_index=True)
        assert canonical_source_hash(users, events) != canonical_source_hash(
            users, doubled
        )


class TestDuplicateEventModes:
    """Exact duplicates fail in strict mode (default) and are dropped
    with a warning in lenient mode; the funnel result is the same
    either way, because stage_dates() uses the first occurrence."""

    def _write_duplicate_data(self, tmp_path):
        (tmp_path / "product_users_clean.csv").write_text(
            "user_id,signup_date,country\n1,2024-01-01,US\n"
        )
        (tmp_path / "product_events_clean.csv").write_text(
            "user_id,event_type,event_date\n"
            "1,signup,2024-01-01\n"
            "1,login,2024-01-02\n"
            "1,login,2024-01-02\n"
        )

    def test_strict_mode_fails_on_duplicate(self, tmp_path):
        self._write_duplicate_data(tmp_path)
        with pytest.raises(ValueError, match="duplicate event records"):
            load_data(tmp_path)

    def test_lenient_mode_drops_duplicate_with_warning(self, tmp_path):
        self._write_duplicate_data(tmp_path)
        with pytest.warns(UserWarning, match="Dropped 1 duplicate"):
            users, events = load_data(tmp_path, strict_duplicates=False)
        assert len(events) == 2
        table = funnel_metrics(stage_dates(events))
        assert table["users"].tolist() == [1, 1, 0, 0, 0]

    def test_lenient_mode_same_result_as_clean_data(self, tmp_path):
        self._write_duplicate_data(tmp_path)
        with pytest.warns(UserWarning):
            _, events_lenient = load_data(tmp_path, strict_duplicates=False)
        clean = _make_events((1, "signup", "2024-01-01"), (1, "login", "2024-01-02"))
        assert stage_dates(events_lenient).equals(stage_dates(clean))


class TestLoadDataInvalid:
    def test_missing_value_fails_load(self, tmp_path):
        (tmp_path / "product_users_clean.csv").write_text(
            "user_id,signup_date,country\n1,2024-01-01,\n"
        )
        (tmp_path / "product_events_clean.csv").write_text(
            "user_id,event_type,event_date\n1,signup,2024-01-01\n"
        )
        try:
            load_data(tmp_path)
        except ValueError as exc:
            assert "missing values" in str(exc)
        else:
            raise AssertionError("load_data should have failed on missing values")


class TestRegressionSample:
    """Sample-specific regression values for the shipped dataset only.

    These are NOT universal requirements — they pin the published
    numbers for the bundled sample so an accidental change is
    caught. If the dataset is ever replaced, these values must be
    updated with the new dataset, not treated as a failure.
    """

    def test_shipped_sample_headline_numbers(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        users, events = load_data(root / "data")
        tables = sql_result_tables(users, events)
        funnel_table = tables["02_funnel_metrics.csv"]
        assert funnel_table["users"].tolist() == [6000, 5396, 4320, 2577, 1298]
        assert funnel_table["cumulative_conversion_pct"].tolist()[-1] == 21.63
        assert funnel_table["step_conversion_pct"].tolist()[-1] == 50.37
        # Ordered and any-purchase agree on this sample (no skipped stages)
        dates = stage_dates(events)
        assert len(ordered_first_purchase(dates)) == 1298
        assert len(first_purchase_after_signup(users, events)) == 1298
