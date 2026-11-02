-- 070 tax plan (UC-ZF-008, task -014): quarterly income-tax prepayments (ESt-VZ) and SVS as
-- recurring items. There is no VAT column anywhere: small business, no VAT (decision TBD-2).
--
-- tax_settings   one row (id = 1), absent until the first save.
--   business_account_id  account the payments are made from (business by intent, any active one is accepted).
--   est_vz_quarter_cents / svs_quarter_cents  amount per due date in cents; NULL = not entered (UC 1a).
--   est_due_dates / svs_due_dates  'MM-DD' list, comma separated, ascending; day 31 = last day of the month.
-- tax_item       one row per due date and recurring item the plan created (one yearly item per due date).
--   retired_at   set when the item was superseded or its due date left the list; the row stays so the
--                booked payments of the old item still count for the prepayments of the year.
--   recurring_id NULL after the recurring item was deleted on the recurring screen; a save recreates it.
CREATE TABLE tax_settings (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    business_account_id  INTEGER REFERENCES account (id) ON DELETE SET NULL,
    est_vz_quarter_cents INTEGER CHECK (est_vz_quarter_cents IS NULL OR est_vz_quarter_cents > 0),
    svs_quarter_cents    INTEGER CHECK (svs_quarter_cents IS NULL OR svs_quarter_cents > 0),
    est_due_dates        TEXT    NOT NULL DEFAULT '02-15,05-15,08-15,11-15',
    svs_due_dates        TEXT    NOT NULL DEFAULT '02-31,05-31,08-31,11-31',
    updated_at           TEXT    NOT NULL
);

CREATE TABLE tax_item (
    id           INTEGER PRIMARY KEY,
    kind         TEXT    NOT NULL CHECK (kind IN ('est', 'svs')),
    due          TEXT    NOT NULL CHECK (due GLOB '[0-1][0-9]-[0-3][0-9]'),
    recurring_id INTEGER REFERENCES recurring (id) ON DELETE SET NULL,
    created_at   TEXT    NOT NULL,
    retired_at   TEXT
);
-- One current item per kind and due date; superseded ones may repeat.
CREATE UNIQUE INDEX tax_item_current ON tax_item (kind, due) WHERE retired_at IS NULL;
CREATE INDEX tax_item_recurring ON tax_item (recurring_id);
