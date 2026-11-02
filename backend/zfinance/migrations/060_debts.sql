-- 060 debts (UC-ZF-010, task -013): a debt is a liability account plus, with a fixed instalment,
-- a recurring transfer from the paying account to that liability account. The remaining amount
-- is the negated balance of the liability account; it is never stored.
--   total_amount_cents   the debt as agreed (raised by an increase, UC 7a); > 0.
--   installment_cents    NULL = no fixed instalment (UC 1a): no recurring item, only the remaining
--                        amount and the optional target_end. Set together with interval and
--                        first_due; the day of the month is first_due's day.
--   pay_account_id       account the instalments and special payments come from.
--   interest_rate_pct    information only, a decimal string such as '4.5' (no amortisation in v1).
--   contract_end         information only; target_end is the wished end of a debt without instalment.
--   recurring_id         the instalment's recurring item, kept equal to these fields by the debts service.
--   closed_at            UTC timestamp when the remaining amount reached 0; NULL while open.
-- The tax plan (-014) and savings pots (-015) follow the same "recurring item from master data" shape.
-- The payoff budget is the app_setting key 'debts.payoff_budget_cents' (no column here).
CREATE TABLE debt (
    id                 INTEGER PRIMARY KEY,
    creditor           TEXT    NOT NULL CHECK (length(trim(creditor)) > 0),
    account_id         INTEGER NOT NULL UNIQUE REFERENCES account (id) ON DELETE CASCADE,
    area               TEXT    NOT NULL CHECK (area IN ('private', 'business')),
    total_amount_cents INTEGER NOT NULL CHECK (total_amount_cents > 0),
    installment_cents  INTEGER CHECK (installment_cents IS NULL OR installment_cents > 0),
    interval           TEXT    CHECK (interval IS NULL OR interval IN ('monthly', 'quarterly', 'yearly')),
    first_due          TEXT,
    pay_account_id     INTEGER REFERENCES account (id) ON DELETE SET NULL,
    interest_rate_pct  TEXT,
    contract_end       TEXT,
    target_end         TEXT,
    recurring_id       INTEGER REFERENCES recurring (id) ON DELETE SET NULL,
    closed_at          TEXT,
    created_at         TEXT    NOT NULL,
    CHECK ((installment_cents IS NULL) = (interval IS NULL)),
    CHECK ((installment_cents IS NULL) = (first_due IS NULL))
);
