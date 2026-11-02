# PR #7 review — synthetic-data replacement

**PR:** https://github.com/omkark2404/ecommerce-funnel-analytics/pull/7
**Branch:** `fix/synthetic-data-provenance`, head `bbb8e05`
**Status at review (2026-11-03):** open, not merged. CI green on its head
(runs 41529570903 / 41529448211, success).
**This review does not merge, close or modify PR #7.** The dataset
decision stays with the owner.

## What was verified

- **Generator determinism — verified independently.** Running
  `scripts/generate_synthetic_data.py` (seed 42) in a clean checkout
  of the PR branch reproduces both committed CSVs byte-for-byte
  (6,000 users, 19,822 events).
- **Metrics — verified independently.** Loading the PR's data with the
  current `src/funnel.py` reproduces exactly the numbers the PR
  claims: funnel 6,000 / 5,382 / 4,256 / 2,541 / 1,275 (21.25%
  ordered), 1,305 any-purchase, seven-day conversion 20.72%, median
  days to first purchase 4 (max 12). The differences from the current
  `main` numbers are explainable: the generator uses step rates close
  to the old sample's (login ~90%, view ~80%, cart ~60%, purchase
  ~50%) with random variation, so the story is unchanged — weakest
  step still add to cart → purchase, biggest loss still product view
  → add to cart, no segment differs significantly.
- **Edge-case coverage — verified.** The new data genuinely exercises
  the definitions the code documents: 30 users skip a stage (so
  ordered 1,275 vs any-purchase 1,305 differ), 62 first purchases
  fall after day 7, and about 5% of users log in again on a later
  date. It loads cleanly under the current integrity checks
  (no duplicate event records, no orphans, no events before signup).
- **References — verified.** Every README, SQL comment, KPI-definition,
  notebook and test reference was updated to the new numbers. The
  only remaining old numbers are the historical record in
  `data/LINEAGE.md` (intentional) and two arbitrary values in
  `pct()` unit tests (not sample data).
- **Licensing — appropriate.** The data is genuinely self-generated
  in-repo, so releasing it under CC0 1.0 is appropriate, and the
  MIT licence correctly continues to cover only the code. The MIT
  badge on the README refers to the code licence.

## Remaining concerns

1. **It changes the published numbers.** That is the point of the PR,
   but it is an owner decision, not a technical one: the funnel,
   seven-day rate, median days and country rates all move (see the
   metric table in the PR description).
2. **It predates this remediation, so it would regress it if merged
   afterwards.** The PR branch's `src/funnel.py` still returns `0.0`
   for a zero denominator, its notebook still has the day-7 boundary
   bug (`max_days < 7` in cell 16), its notebook has no figure
   manifest (so `scripts/check_figures.py` would fail after the
   merge until the notebook is re-run), and its narrative cells are
   the older static versions. If PR #7 is merged, the fixes in this
   remediation must be re-applied on top of it and the notebook
   re-run before `make check` passes again.
3. **Merge interaction.** Both PRs touch `docs/KPI_DEFINITIONS.md`,
   `notebooks/funnel_analysis.ipynb`, the five chart figures,
   `src/funnel.py` and `tests/test_funnel.py`. Whichever merges
   second will need manual conflict resolution, and the regression
   test values in `tests/test_funnel.py` can only match one dataset
   at a time — they must be updated to whichever dataset is on
   `main` after the merge.
4. **No real-data claim.** The synthetic data is clearly labelled
   as such in the PR, which is correct. It must not be described
   as real customer behaviour in interviews.

## Recommendation

Technically ready for owner review: the generator, licence,
regenerated analytics and references all check out. The decision
to merge is the owner's, because it replaces the dataset and
changes the published metrics — and because of concern (2) above,
it should be merged **before** this remediation PR if both are to
land, or this remediation must be rebased onto it afterwards.
