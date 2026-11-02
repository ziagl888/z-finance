-- 120 allocation rules (UC-ZF-014, task -025): fixed shares of an income go to the levy pot, the
-- debt payoff and the pots; every share is proposed and nothing is booked without confirmation.
--
-- The levy share is not stored in a rule: it is derived from the levy rate of -019 for a business
-- income and is the floor of its line (AK7). An open proposal is deliberately *not* a planned
-- booking, so it never lowers free_amount a second time — the levy share is already reserved by
-- levy_gap from the booking on (core rule, AK6).
--
-- allocation_rule   one row per rule the user maintains under "Allocation".
--   area              trigger: income on an account of this area; NULL = any area.
--   category_id       trigger: income with this category; NULL = any category. A deleted category
--                     deletes its rules (like rule.category_id of 040).
--   min_amount_cents  the income must reach this amount (AK2).
--   targets_json      JSON array, in the order they are paid:
--                     [{"kind": "debt_plan"|"pot"|"reserve", "pot_id": <id|null>,
--                       "percent_bp": <0..10000|null>, "fixed_cents": <>0|null>}]
--                     percent_bp and fixed_cents are exclusive; Σ percent_bp <= 10000 (100 %).
--   fallback_kind     where a "debt_plan" share goes without an open debt (AK4, UC 3b): the pot
--                     fallback_pot_id, else the highest-ranked open pot of the income's account
--                     ("pot" with NULL), or the total reserve ("reserve", books nothing).
-- allocation_proposal  one row per matching income, at most one per booking.
--   transaction_id   the income; NULL after it was deleted (UC 2a: the reversal suggestion of a
--                    confirmed proposal reads booked_json, so the row must survive).
--   rule_id          the rule that produced it; NULL after the rule was deleted (the proposal stays).
--   amount_cents     the income's amount, gross (the percentages refer to it, UC assumption).
--   account_id       the income's account: source of the transfers a confirmation books.
--   date             the income's date, used as the booking date of the confirmation.
--   lines_json       JSON array in payment order:
--                    [{"kind": "levy"|"debt_plan"|"pot"|"reserve", "pot_id": <id|null>,
--                      "amount_cents": <>=0>, "floor_cents": <>=0>, "capped": <bool>}]
--                    floor_cents > 0 only for the levy line (it cannot be lowered, AK7).
--   status           open = waiting (highlighted after 7 days, AK5); confirmed = booked;
--                    skipped = rejected, counted by the month review (UC 4a). Never deleted.
--   booked_json      JSON array of the transfer_group_ids the confirmation wrote (UC 2a).
CREATE TABLE allocation_rule (
    id               INTEGER PRIMARY KEY,
    name             TEXT    NOT NULL,
    area             TEXT             CHECK (area IN ('private', 'business')),
    category_id      INTEGER          REFERENCES category (id) ON DELETE CASCADE,
    min_amount_cents INTEGER NOT NULL DEFAULT 0 CHECK (min_amount_cents >= 0),
    targets_json     TEXT    NOT NULL DEFAULT '[]',
    fallback_kind    TEXT    NOT NULL DEFAULT 'reserve' CHECK (fallback_kind IN ('pot', 'reserve')),
    fallback_pot_id  INTEGER          REFERENCES pot (id) ON DELETE SET NULL,
    active           INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);

CREATE TABLE allocation_proposal (
    id             INTEGER PRIMARY KEY,
    transaction_id INTEGER          REFERENCES "transaction" (id) ON DELETE SET NULL,
    rule_id        INTEGER          REFERENCES allocation_rule (id) ON DELETE SET NULL,
    amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
    text           TEXT    NOT NULL DEFAULT '',
    account_id     INTEGER NOT NULL REFERENCES account (id) ON DELETE RESTRICT,
    date           TEXT    NOT NULL,
    lines_json     TEXT    NOT NULL DEFAULT '[]',
    status         TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'confirmed', 'skipped')),
    booked_json    TEXT    NOT NULL DEFAULT '[]',
    created_at     TEXT    NOT NULL,
    confirmed_at   TEXT
);
-- One proposal per income (the sweep is idempotent); several NULLs are allowed in SQLite.
CREATE UNIQUE INDEX allocation_proposal_transaction ON allocation_proposal (transaction_id);
CREATE INDEX allocation_proposal_status ON allocation_proposal (status);
