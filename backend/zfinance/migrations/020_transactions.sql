-- 020 transactions (UC-ZF-002, task -008): the booking table every later feature writes into.
-- Amounts are signed integer cents (income positive, expense negative, never 0), business dates
-- YYYY-MM-DD, created_at ISO-8601 UTC. Column names are a contract for tasks -009 ... -025.
--   status  booked = counts towards the balance; planned = expected item (future date, recurring);
--           skipped = a planned item that did not happen (-011).
--   source  who wrote the row: manual entry, CSV import, recurring generation, receipt agent,
--           allocation proposal.
--   transfer_group_id  both legs of a transfer share it (the legs cancel out in the total).
--   recurring_id       the recurring item a row came from; its foreign key arrives with -009.
--   import_hash        duplicate guard of the CSV import (-012); unique when set.
CREATE TABLE "transaction" (
    id                INTEGER PRIMARY KEY,
    date              TEXT    NOT NULL,
    amount_cents      INTEGER NOT NULL CHECK (amount_cents != 0),
    account_id        INTEGER NOT NULL REFERENCES account (id) ON DELETE RESTRICT,
    category_id       INTEGER          REFERENCES category (id) ON DELETE RESTRICT,
    text              TEXT    NOT NULL DEFAULT '',
    counterparty      TEXT,
    memo              TEXT,
    status            TEXT    NOT NULL DEFAULT 'booked' CHECK (status IN ('booked', 'planned', 'skipped')),
    source            TEXT    NOT NULL DEFAULT 'manual'
                              CHECK (source IN ('manual', 'import', 'recurring', 'receipt', 'allocation')),
    transfer_group_id TEXT,
    recurring_id      INTEGER,
    import_hash       TEXT    UNIQUE,
    created_at        TEXT    NOT NULL
);
-- Balance sums per account up to a day; list order by date.
CREATE INDEX transaction_account_date ON "transaction" (account_id, date);
CREATE INDEX transaction_date ON "transaction" (date);
CREATE INDEX transaction_category ON "transaction" (category_id);
CREATE INDEX transaction_transfer_group ON "transaction" (transfer_group_id);
CREATE INDEX transaction_recurring ON "transaction" (recurring_id);
