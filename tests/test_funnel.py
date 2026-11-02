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

from src.funnel import (
    STAGES,
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

    def test_zero_denominator_returns_zero(self):
        assert pct(100, 0) == 0.0

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
            (user_id, "signup",       "2024-01-01"),
            (user_id, "login",        "2024-01-02"),
            (user_id, "view_product", "2024-01-03"),
            (user_id, "add_to_cart",  "2024-01-04"),
            (user_id, "purchase",     "2024-01-05"),
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
            (1, "signup",       "2024-01-01"),
            # no login for user 1
            (1, "view_product", "2024-01-03"),
            (1, "add_to_cart",  "2024-01-04"),
            (1, "purchase",     "2024-01-05"),
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
            (1, "login",  "2023-12-31"),  # before signup – should be ignored
            (1, "login",  "2024-01-02"),  # valid login
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
            (1, "signup",       "2024-01-01"),
            (1, "login",        "2024-01-01"),
            (1, "view_product", "2024-01-01"),
            (1, "add_to_cart",  "2024-01-01"),
            (1, "purchase",     "2024-01-01"),
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
            (1, "signup",   "2024-01-01"),
            (1, "purchase", "2024-01-05"),   # day 4 within 7-day window -> converted
            # user 2
            (2, "signup",   "2024-01-06"),
            (2, "purchase", "2024-01-20"),   # day 14, outside 7-day window -> not converted
            # user 3 (ineligible: signup 2024-01-15 > cutoff 2024-01-13)
            (3, "signup",   "2024-01-15"),
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

