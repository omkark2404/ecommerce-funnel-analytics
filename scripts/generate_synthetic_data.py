#!/usr/bin/env python3
"""Generate a deterministic synthetic funnel dataset.

This is the *only* source of the CSVs in data/ on the
fix/synthetic-data-provenance branch. It replaces the previous
bundled sample, whose original source and redistribution rights
could not be verified (see data/LINEAGE.md before this change).

Licence: the generated CSVs are released under CC0 1.0 — no rights
reserved. The generator itself is MIT-licensed, like the rest of
the code in this repository.

Determinism: run with --seed 42 (the default) to reproduce the
committed files byte-for-byte. The generator uses only the Python
standard library, so no extra dependencies are needed.

Design (deliberately close to, but not a copy of, the previous
sample, so the analysis stays comparable):
- 6,000 users, signup dates 2023-01-01 to 2023-06-29, countries
  in the same proportions as before.
- Five stages: signup -> login -> view_product -> add_to_cart
  -> purchase, with step rates close to the previous sample
  (login ~90%, view ~80%, cart ~60%, purchase ~50%).
- Unlike the previous sample, this data is *not* perfectly clean
  in structure: about 2% of users skip a stage, about 5% repeat
  a login on a later date, and some purchases fall after 7 days.
  That is intentional — the funnel code is written to handle
  skipped stages, repeated events and late purchases, and the
  tests pin that behaviour. The ordered-funnel and any-purchase
  definitions therefore give *different* purchase counts here,
  which is the correct, documented behaviour (see
  docs/KPI_DEFINITIONS.md).
- Event lags are 0-2 days after the previous stage for most
  users, with a small share taking 3-10 days.
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

COUNTRIES = [
    ("Germany", 0.167),
    ("India", 0.175),
    ("Australia", 0.159),
    ("Canada", 0.158),
    ("USA", 0.180),
    ("UK", 0.161),
]

# Step rates, in order login, view, cart, purchase.
STEP_RATES = [0.90, 0.80, 0.60, 0.50]
STAGES = ["signup", "login", "view_product", "add_to_cart", "purchase"]

START = date(2023, 1, 1)
END = date(2023, 6, 29)


def _pick_country(rng: random.Random) -> str:
    roll = rng.random()
    cumulative = 0.0
    for name, weight in COUNTRIES:
        cumulative += weight
        if roll < cumulative:
            return name
    return COUNTRIES[-1][0]


def _lag(rng: random.Random) -> int:
    # 0-2 days for most users, 3-5 days for a small share.
    if rng.random() < 0.03:
        return rng.randint(3, 5)
    return rng.randint(0, 2)


def generate(seed: int = 42, n_users: int = 6000) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    users: list[dict] = []
    events: list[dict] = []
    span = (END - START).days
    for user_id in range(1, n_users + 1):
        signup = START + timedelta(days=rng.randint(0, span))
        country = _pick_country(rng)
        users.append(
            {"user_id": user_id, "signup_date": signup.isoformat(), "country": country}
        )
        events.append(
            {
                "user_id": user_id,
                "event_type": "signup",
                "event_date": signup.isoformat(),
            }
        )
        # About 2% of users skip one intermediate stage.
        skip_stage = None
        if rng.random() < 0.02:
            skip_stage = rng.choice(["login", "view_product", "add_to_cart"])
        current_date = signup
        reached_previous = True
        for stage, rate in zip(STAGES[1:], STEP_RATES):
            if stage == skip_stage:
                # Skipped: no event for this stage, but later stages
                # can still occur (any-purchase counts them; the
                # ordered funnel does not).
                reached_previous = False
                continue
            if not reached_previous:
                # After a skip, a later stage occurs only with the
                # same step rate, counted from the last reached stage.
                if rng.random() >= rate:
                    break
            else:
                if rng.random() >= rate:
                    break
            current_date = current_date + timedelta(days=_lag(rng))
            events.append(
                {
                    "user_id": user_id,
                    "event_type": stage,
                    "event_date": current_date.isoformat(),
                }
            )
            reached_previous = True
        # About 5% of users log in again on a later date.
        if rng.random() < 0.05:
            second_login = signup + timedelta(days=rng.randint(1, 14))
            # Avoid an exact duplicate login record for this user.
            existing_logins = {
                e["event_date"]
                for e in events
                if e["user_id"] == user_id and e["event_type"] == "login"
            }
            while second_login.isoformat() in existing_logins:
                second_login += timedelta(days=1)
            events.append(
                {
                    "user_id": user_id,
                    "event_type": "login",
                    "event_date": second_login.isoformat(),
                }
            )
    # Sort events by user, then date, then stage order, for readability.
    stage_order = {stage: i for i, stage in enumerate(STAGES)}
    events.sort(
        key=lambda e: (e["user_id"], e["event_date"], stage_order[e["event_type"]])
    )
    return users, events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--users", type=int, default=6000)
    args = parser.parse_args()
    users, events = generate(seed=args.seed, n_users=args.users)
    with open(
        DATA_DIR / "product_users_clean.csv", "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=["user_id", "signup_date", "country"], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(users)
    with open(
        DATA_DIR / "product_events_clean.csv", "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=["user_id", "event_type", "event_date"], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(events)
    print(f"Wrote {len(users):,} users and {len(events):,} events (seed {args.seed}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
