# Data lineage and limitations

## What is known

- **Files in this repository:** `data/product_users_clean.csv` (6,000 rows + header) and `data/product_events_clean.csv` (19,591 rows + header).
- **Period covered:** signups 2023-01-01 to 2023-06-29; events 2023-01-01 to 2023-07-03.
- **Grain:** one row per account in the users file; one row per funnel event in the events file. In this sample, no account has more than one event of the same type, and no account skips a stage — that is a property of this sample, not a rule imposed by the schema.
- **Stages:** signup, login, view_product, add_to_cart, purchase.
- **Countries:** Australia, Canada, Germany, India, UK, USA.
- **Integrity checks run on load:** missing values, duplicate `user_id` in users, duplicate event records (same user, type and date), unknown event types, events for unknown users, events before signup, and signup events disagreeing with `users.signup_date`. See `src/funnel.py:integrity_problems` and `data/README.md`. The shipped files pass all of these checks.

## What is not known — provenance is unverified

- **Original source:** not recorded. No source URL, dataset name, author, collection method, or licence for the data is present in this repository.
- **Cleaning history:** the files are labelled “clean”, but no raw files and no cleaning script are included. No raw-to-clean transformation can be reproduced from the files provided here. Do not claim otherwise.
- **Simulation inference:** the structure strongly suggests the sample was generated rather than collected from a real store — at most one event per stage per user, no skipped stages, every step 0–2 days after the previous one. That is an inference from the data, not a confirmed fact about its origin.
- **Redistribution rights:** **not verified.** The MIT licence in `LICENSE` covers the code in this repository. It must not be read as a licence for the data files, and it does not establish permission to redistribute them.

## What this means in practice

- Treat every result as a demonstration of the method on this sample, not as a measurement from a real commercial platform.
- Do not cite these numbers as evidence about any real e-commerce business.
- Do not assume this dataset is the same as any other public funnel dataset with similar sizes. The only claims made here are about the files in `data/` and the checks run on them.
- If you need a dataset with clear redistribution rights, do **not** silently relabel this one. Either (a) retain these files pending verification of their source and rights, or (b) replace them — with explicit owner approval — with a newly generated, deterministic synthetic dataset, clearly licensed, with regenerated results and a documented change in the published metrics. No replacement is included in this change.

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
- The observation period ends at the latest event date (2023-07-03). Seven-day conversion is therefore defined against that date; see `docs/KPI_DEFINITIONS.md`.
