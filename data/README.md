# Data

Two CSV files, generated deterministically by `scripts/generate_synthetic_data.py` (seed 42) and released under CC0 1.0. There are no raw files to clean — the generator writes the final CSVs directly.

Full lineage, schema, and limitations: [LINEAGE.md](LINEAGE.md).

**Licence note:** the data files are released under **CC0 1.0** — no rights reserved. The MIT licence in the repository root covers the code. See [LINEAGE.md](LINEAGE.md).

## product_users_clean.csv

One row per account: 6,000 accounts that signed up between 2023-01-01 and 2023-06-29.

| Column | Description |
| --- | --- |
| user_id | Account ID (unique) |
| signup_date | Date the account was created |
| country | One of Australia, Canada, Germany, India, UK, USA |

## product_events_clean.csv

One row per funnel event: 19,822 events dated 2023-01-01 to 2023-07-10.

| Column | Description |
| --- | --- |
| user_id | Account ID, references product_users_clean.csv |
| event_type | signup, login, view_product, add_to_cart or purchase |
| event_date | Date of the event (no time of day) |

## Checks

`src/funnel.py` refuses to load the files if there are missing values, duplicate accounts, duplicate event records, unknown event types, events for unknown accounts, events dated before signup, or signup events that disagree with `signup_date`. The files pass all of these checks.

No rows were dropped or imputed by the code in this repository: the checks above are validation, not cleaning. If a future data drop fails a check, the load fails loudly instead of silently producing a funnel from bad data.

## Provenance

The files are synthetic, generated in this repository by `scripts/generate_synthetic_data.py` (seed 42). The previous bundled sample's source could not be verified, so it was replaced with this generated dataset — see [LINEAGE.md](LINEAGE.md) for the old numbers and what changed. Section 1 of the notebook shows the checks run on the data (repeated logins, skipped stages, late purchases). Treat the results as a demonstration of the method, not as facts about a real store.

Do not cite these numbers as evidence about any real e-commerce business — the only claims made here are about the files in `data/` and the checks run on them.

The files use LF line endings (enforced by `.gitattributes`) because the MySQL load script expects them.
