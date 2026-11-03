# Data lineage and limitations

## What is known

- **Files in this repository:** `data/product_users_clean.csv` (6,000 rows + header) and `data/product_events_clean.csv` (19,822 rows + header).
- **Period covered:** signups 2023-01-01 to 2023-06-29; events 2023-01-01 to 2023-07-10.
- **Grain:** one row per account in the users file; one row per funnel event in the events file. About 5% of accounts have a repeated login on a later date, and 30 accounts skip an intermediate stage — those are properties of this sample, not rules imposed by the schema.
- **Stages:** signup, login, view_product, add_to_cart, purchase.
- **Countries:** Australia, Canada, Germany, India, UK, USA.
- **Integrity checks run on load:** missing values, duplicate `user_id` in users, duplicate event records (same user, type and date), unknown event types, events for unknown users, events before signup, and signup events disagreeing with `users.signup_date`. See `src/funnel.py:integrity_problems` and `data/README.md`. The shipped files pass all of these checks.

## Provenance — verified (replaced 2026-11-03)

- **Original source:** the files in `data/` are generated in this repository by `scripts/generate_synthetic_data.py` (seed 42, Python standard library only). Run it to reproduce the committed CSVs byte-for-byte.
- **Licence:** the generated CSVs are released under **CC0 1.0** (no rights reserved). The MIT licence in `LICENSE` covers the code. There is no redistribution risk for the data.
- **What was replaced, and why:** the previous bundled sample (6,000 users, 19,591 events, funnel 6,000 / 5,396 / 4,320 / 2,577 / 1,298, overall conversion 21.63%) had no recorded source and its redistribution rights could not be verified — no matching public dataset was found by web search. It was replaced, with the owner's approval, by this deterministic synthetic dataset. The published metrics changed as a result: funnel 6,000 / 5,382 / 4,256 / 2,541 / 1,275, overall ordered conversion 21.25% (1,305 any-purchase), seven-day conversion 20.72%, median days to first purchase 4 (max 12). The analysis code, SQL, tests and notebook were all re-run and updated for the new data; see the commit history on branch `fix/synthetic-data-provenance`.
- **Cleaning history:** there is no raw-to-clean transformation — the generator writes the final CSVs directly. The files are labelled “clean” because they pass every integrity check in `src/funnel.py` on load; that is validation, not a claim of a cleaning process.

## What this means in practice

- Treat every result as a demonstration of the method on this sample, not as a measurement from a real commercial platform.
- Do not cite these numbers as evidence about any real e-commerce business.
- Do not assume this dataset is the same as any other public funnel dataset with similar sizes. The only claims made here are about the files in `data/` and the checks run on them.
- The dataset in `data/` now has clear redistribution rights (CC0 1.0) and a reproducible generator, so the provenance issue that prompted this change is resolved. Do not relabel these files as real commercial data: they are synthetic, and every result is a demonstration of the method on synthetic data.

## Schema

### `product_users_clean.csv`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `user_id` | integer | no | Unique account ID; primary key in MySQL |
| `signup_date` | date (`YYYY-MM-DD`) | no | Account creation date; must match the first `signup` event |
| `country` | string | no | One of the six countries listed above in this sample |

### `product_events_clean.csv`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `user_id` | integer | no | References `product_users_clean.csv`; foreign key in MySQL |
| `event_type` | string | no | One of `signup`, `login`, `view_product`, `add_to_cart`, `purchase` |
| `event_date` | date (`YYYY-MM-DD`) | no | Date only — no time of day. Same-day stage order is assumed, not observed |

## Limitations that follow from the data

- Date-only events cannot prove the order of multiple stages on the same day.
- There is no revenue, order, session, or product data, so impact is measured in purchases, not money.
- There is no checkout-start event, so cart abandonment and checkout abandonment cannot be separated.
- The observation period ends at the latest event date (2023-07-10). Seven-day conversion is therefore defined against that date; see `docs/KPI_DEFINITIONS.md`.
