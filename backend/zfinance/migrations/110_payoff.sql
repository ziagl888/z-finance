-- 110 payoff plan (UC-ZF-012, task -021): order of the debts, the special payments above the
-- instalments and the baseline that "on track / behind plan" is measured against.
--
-- The monthly budget on top of the instalments is not stored here: it is the debts setting
-- debts.payoff_budget_cents (-013), read and written through debts.payoff_budget/set_payoff_budget.
--
-- payoff_plan   one row (id = 1), absent until the plan is saved the first time.
--   strategy                  snowball (smallest first) | avalanche (highest interest first) |
--                             manual (manual_order, e.g. a reminder or the tax office first).
--   manual_order              JSON array of debt ids; open debts missing from it follow snowball.
--   baseline_end_month        'YYYY-MM' the plan was debt-free in when saved; NULL = no plan
--                             (negative available amount).
--   baseline_available_cents  available amount per month when saved (6b: suggest more when it rose).
-- payoff_month  baseline per plan month, written on save for the months after the save.
--   planned_cents  instalments + special payments planned for the month (positive).
--   extra_cents    the special-payment part of it.
--   actual_cents   booked payments into the debts in that month; refreshed by the status read.
-- payoff_transfer  the planned special payments the plan keeps in line (one transfer pair each);
--                  debt_id NULL = the debt was deleted, the next sync removes a still planned pair.
CREATE TABLE payoff_plan (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),
    strategy                 TEXT    NOT NULL DEFAULT 'snowball'
                                     CHECK (strategy IN ('snowball', 'avalanche', 'manual')),
    manual_order             TEXT    NOT NULL DEFAULT '[]',
    baseline_end_month       TEXT,
    baseline_available_cents INTEGER,
    created_at               TEXT    NOT NULL,
    updated_at               TEXT    NOT NULL
);

CREATE TABLE payoff_month (
    month         TEXT    PRIMARY KEY CHECK (month GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'),
    planned_cents INTEGER NOT NULL CHECK (planned_cents >= 0),
    extra_cents   INTEGER NOT NULL CHECK (extra_cents >= 0),
    actual_cents  INTEGER
);

CREATE TABLE payoff_transfer (
    transfer_group_id TEXT    PRIMARY KEY,
    month             TEXT    NOT NULL,
    debt_id           INTEGER REFERENCES debt (id) ON DELETE SET NULL
);
CREATE INDEX payoff_transfer_month ON payoff_transfer (month);
