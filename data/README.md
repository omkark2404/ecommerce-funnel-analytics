# Data

Two CSV files, already cleaned. The raw files and the cleaning steps are not part of this repository.

## product_users_clean.csv

One row per account: 6,000 accounts that signed up between 2023-01-01 and 2023-06-29.

| Column | Description |
| --- | --- |
| user_id | Account ID (unique) |
| signup_date | Date the account was created |
| country | One of Australia, Canada, Germany, India, UK, USA |

## product_events_clean.csv

One row per funnel event: 19,591 events dated 2023-01-01 to 2023-07-03.

| Column | Description |
| --- | --- |
| user_id | Account ID, references product_users_clean.csv |
| event_type | signup, login, view_product, add_to_cart or purchase |
| event_date | Date of the event (no time of day) |

## Checks

`src/funnel.py` refuses to load the files if there are missing values, duplicate accounts, unknown event types, events for unknown accounts, events dated before signup, or signup events that disagree with `signup_date`. The files pass all of these checks.

No rows were dropped or imputed by the code in this repository: the checks above are validation, not cleaning. If a future data drop fails a check, the load fails loudly instead of silently producing a funnel from bad data.

## Provenance

The original source of this sample was not recorded. Its structure suggests it was generated rather than collected: every account has at most one event per stage, no account skips a stage, and each step follows the previous one within zero to two days. Section 1 of the notebook shows these checks. Treat the results as a demonstration of the method, not as facts about a real store.

Because the source is unknown, do not cite these numbers as evidence about any real e-commerce business, and do not assume the dataset is the same as any other public funnel dataset with similar sizes — the only claims made here are about the files in `data/` and the checks run on them.

The files use LF line endings (enforced by `.gitattributes`) because the MySQL load script expects them.
