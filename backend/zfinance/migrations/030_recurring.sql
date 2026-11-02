-- 030 recurring items (UC-ZF-003, task -009): templates that the generation engine turns into
-- planned rows of "transaction" (source 'recurring') for a 12-month horizon.
--   amount_cents                signed like a booking (income +, expense -); positive for a transfer.
--   transfer_target_account_id  set = the item is a transfer (loan or savings instalment) from
--                               account_id to this account; each due date becomes a transfer pair.
--   interval / day_of_month / weekday
--                               weekly items name a weekday (0 = Monday), all others a day of the
--                               month (1-31; 29-31 fall on the last day of shorter months).
--                               Quarterly and yearly items repeat in the month of start_date.
--   pot_id                      savings pot the instalment feeds; the pot table arrives with -015,
--                               so there is no foreign key yet.
--   active                      0 = paused: no planned rows exist and none are generated.
-- Deleting an account (only possible without bookings) deletes its recurring items; deleting a
-- category leaves them uncategorized.
CREATE TABLE recurring (
    id                         INTEGER PRIMARY KEY,
    description                TEXT    NOT NULL CHECK (trim(description) != ''),
    amount_cents               INTEGER NOT NULL CHECK (amount_cents != 0),
    account_id                 INTEGER NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    category_id                INTEGER          REFERENCES category (id) ON DELETE SET NULL,
    transfer_target_account_id INTEGER          REFERENCES account (id) ON DELETE CASCADE,
    pot_id                     INTEGER,
    interval                   TEXT    NOT NULL CHECK (interval IN ('weekly', 'monthly', 'quarterly', 'yearly')),
    day_of_month               INTEGER CHECK (day_of_month BETWEEN 1 AND 31),
    weekday                    INTEGER CHECK (weekday BETWEEN 0 AND 6),
    start_date                 TEXT    NOT NULL,
    end_date                   TEXT,
    active                     INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at                 TEXT    NOT NULL,
    CHECK ((interval = 'weekly') = (weekday IS NOT NULL)),
    CHECK ((interval != 'weekly') = (day_of_month IS NOT NULL)),
    CHECK (end_date IS NULL OR end_date >= start_date),
    CHECK (transfer_target_account_id IS NULL OR (amount_cents > 0 AND transfer_target_account_id != account_id))
);
CREATE INDEX recurring_account ON recurring (account_id);

-- Columns of "transaction" that belong to recurring generation (020 is not edited):
--   due_date        the schedule date a generated row stands for; it stays when the row is moved,
--                   booked or replaced by an imported booking, so the date counts as done.
--   manually_moved  1 = the user changed date or amount of this planned row on the recurring
--                   screen; regeneration never touches it.
-- recurring_id keeps no foreign key: adding one needs a rebuild of the booking table, so the
-- recurring service unlinks rows itself when an item is deleted.
ALTER TABLE "transaction" ADD COLUMN due_date TEXT;
ALTER TABLE "transaction" ADD COLUMN manually_moved INTEGER NOT NULL DEFAULT 0 CHECK (manually_moved IN (0, 1));

-- Idempotency key of the generation: one row per recurring item and due date. A transfer has
-- two legs with the same key, so only its outgoing leg (negative amount) takes part.
CREATE UNIQUE INDEX transaction_recurring_due ON "transaction" (recurring_id, due_date)
    WHERE recurring_id IS NOT NULL AND due_date IS NOT NULL AND (transfer_group_id IS NULL OR amount_cents < 0);
