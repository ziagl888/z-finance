-- 100 levy reserve (UC-ZF-015, task -019): income tax and SVS that the business income of a year
-- triggers, due as a back-payment in the following year.
--
-- levy_settings   one row (id = 1), absent until the first save (no rate = nothing reserved).
--   levy_rate_bp   personal levy rate in basis points of the base (3500 = 35 %), 0 to 90 %.
--   levy_base      income (business income of the year) | profit (income minus business expenses).
--   due_month_day  'MM-DD' of the back-payment in the following year (day 31 = end of the month).
--   levy_pot_id    the pot that holds the reserve; NULL = the open pot flagged is_levy_pot (080).
--   account_id     account the back-payment is planned on; NULL = the tax plan's business account,
--                  else the levy pot's account.
-- levy_backpayment  one row per year: the planned item "Levy back-payment <year>" the engine keeps
--                   in line (at most one per year).
--   transaction_id  the planned row; NULL while the estimate is <= 0, or once the row is gone.
--   estimate_cents  the last planned amount (positive); kept when the year is archived.
--   settled_at      the planned row was confirmed, skipped or deleted outside the engine: done.
--   archived_at     the estimate was replaced by the real assessment (UC 3a): done, estimate kept.
CREATE TABLE levy_settings (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    levy_rate_bp  INTEGER NOT NULL CHECK (levy_rate_bp BETWEEN 0 AND 9000),
    levy_base     TEXT    NOT NULL DEFAULT 'income' CHECK (levy_base IN ('income', 'profit')),
    due_month_day TEXT    NOT NULL DEFAULT '07-01' CHECK (due_month_day GLOB '[0-1][0-9]-[0-3][0-9]'),
    levy_pot_id   INTEGER REFERENCES pot (id) ON DELETE SET NULL,
    account_id    INTEGER REFERENCES account (id) ON DELETE SET NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE levy_backpayment (
    year           INTEGER PRIMARY KEY,
    transaction_id INTEGER REFERENCES "transaction" (id) ON DELETE SET NULL,
    estimate_cents INTEGER NOT NULL DEFAULT 0 CHECK (estimate_cents >= 0),
    settled_at     TEXT,
    archived_at    TEXT,
    updated_at     TEXT    NOT NULL
);
CREATE INDEX levy_backpayment_transaction ON levy_backpayment (transaction_id);
