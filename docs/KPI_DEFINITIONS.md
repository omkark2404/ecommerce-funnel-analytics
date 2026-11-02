# KPI definitions

All percentages in this project are percentages of users, unless a table
says otherwise. “Percentage point” (pp) means an absolute difference in
percentages; “percent” means a relative change. The two are not interchangeable.

## Primary funnel — ordered conversion

**Definition (primary, used in `sql/02_funnel_metrics.sql` and
`src/funnel.py:funnel_metrics`):** a user counts at a stage only if they
reached every preceding stage first, in order, on or after the date they
reached the previous stage.

- Stages: signup → login → view_product → add_to_cart → purchase.
- `users` at a stage = number of signed-up users who reached that stage
  under the ordered rule.
- `step_conversion_pct` = users at this stage / users at the previous stage × 100.
- `cumulative_conversion_pct` = users at this stage / signups × 100.
- Denominator for the first stage is signups; its step and cumulative
  rates are 100% by definition.

**Same-day events:** events carry dates, not timestamps. Events on the
same day are assumed to follow stage order. Date-only data cannot prove
the sequence of multiple same-day events — see `data/LINEAGE.md`.

**Repeated events:** a user may have several events of the same type on
different dates. Only the first qualifying event on or after the previous
stage counts for that stage. Exact duplicate event records (same user,
type and date) are an integrity failure in `src/funnel.py`.

**Skipped stages:** a user who has a purchase event but no add-to-cart
event does **not** count as a purchaser in the ordered funnel.

## Secondary metric — any-purchase conversion

**Definition (secondary, used in `sql/03`, `sql/04`, and
`src/funnel.py:first_purchase_after_signup` / `country_conversion`):**
a user counts as converted if they have *any* purchase event on or after
their signup date, even if an intermediate stage is missing.

- This is a purchase metric, not a funnel metric. It answers “did this
  user ever buy after signing up?”, not “did they complete the funnel?”.
- In the shipped sample the two definitions differ: 1,275 ordered
  purchasers vs 1,305 any-purchase purchasers, because 30 users
  skipped a stage. That difference is a real finding — see the test
  `test_country_conversion_counts_any_purchase_after_signup` in
  `tests/test_funnel.py`.
- Do not mix the two: do not compare an ordered funnel purchase count
  with an any-purchase country rate as if they were the same KPI.

## Days to first purchase

**Definition:** for users with an any-purchase (see above), the whole
days from `signup_date` to the first purchase date on or after signup.
This is a distribution over observed purchasers only, not a conversion
rate. Users who never bought, and users who signed up near the end of
the data, are not in this table.

## Seven-day conversion

**Definition (`sql/05`, `src/funnel.py:seven_day_conversion`):**
among users with a complete seven-day follow-up window, the share who
bought within seven days of signup.

- **Window boundary — inclusive of day 7:** a purchase counts if
  `signup_date <= purchase_date <= signup_date + 7 days`. A purchase
  exactly seven days after signup counts; a purchase on day 8 does not.
  The Python and SQL implementations both use `<= signup + 7 days`.
- **Eligibility:** a user is eligible only if `signup_date <=
  observed_through - 7 days`, where `observed_through` is the latest
  event date in the data (2023-07-03 in the shipped sample, so the
  cutoff is 2023-06-26). Users signing up after the cutoff are excluded,
  not counted as non-converters.
- **Observation end:** `observed_through` is defined as the maximum
  event date, because no separate observation-end date is provided.
  If the data were extended, the cutoff — and therefore the eligible
  set — would move with it. This is an assumption, documented here.
- **Purchases after seven days:** they exist in principle and are
  handled correctly — they simply do not count in this metric. The
  notebook no longer asserts that all purchases fall within seven days.

## Country and cohort comparisons

- **Country conversion** (`sql/04`) uses any-purchase conversion over
  **all** users, including users who signed up in the last seven days
  and therefore had less time to buy. That is a right-censoring
  limitation: recent signups are counted in the denominator before
  they have had a full chance to convert. The notebook also reports
  country conversion among mature users as a sensitivity check; the
  headline country table is the all-users version, and the difference
  is small in this sample.
- **Signup-month cohorts** use seven-day conversion among mature users
  only, so the last cohort is not penalised for having less follow-up.
- **Statistical tests:** chi-square tests compare counts, not rates.
  A non-significant result means “no evidence of a difference larger
  than random variation at this sample size”, not “the groups are
  identical”. With about 1,000 users per country, gaps of roughly
  5 points in overall conversion are reliably detectable; smaller
  real differences cannot be ruled out. Six chi-square tests are run
  in the notebook; the Bonferroni threshold is 0.05 / 6 ≈ 0.008.
  Expected cell counts are checked in the notebook — chi-square is
  unreliable if any expected count is below 5.
- **Causation:** none of these comparisons establish causation.
  Segment differences, if found, would be descriptive only.

## One-percentage-point uplift

The “extra purchases per +1 pp” table in the notebook is a scenario
estimate under stated assumptions, not a measured experimental outcome.
It assumes a one-point gain at one step, with every other step rate
unchanged. Algebraically, the gain is 1% of all purchases divided by
that step’s own rate, so the lowest-rate step always comes out on top
on that scale. As a relative lift, every step is worth the same.
