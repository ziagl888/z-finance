-- 080 savings pots (UC-ZF-011, task -015): virtual subdivisions of one real account. A pot holds
-- no money of its own; its balance is the sum of its allocations, and the account's free balance
-- is the account balance minus the balances of all its pots.
--   area                 private | business; defaults to the account's area in the service.
--   target_amount_cents  NULL = open saving without a target (UC 1b).
--   target_date          NULL = no date (emergency fund, UC 1a): no monthly rate, no plan status.
--   plan_start           the day the linear plan starts (creation, or the last change of target
--                        amount/date); the expected balance grows linearly from the balance on this
--                        day to the target on target_date.
--   plan_expense         1 = the target amount is a planned expense on target_date (UC step 1).
--   planned_transaction_id  that planned expense row, kept in line by the pots service.
--   emergency_rank       1 = taken first in an emergency; unique per account among open pots.
--                        NULL for a locked or closed pot.
--   locked               1 = never part of an emergency proposal (emergency fund, tax reserve).
--   is_levy_pot          flag for the levy reserve (-019); no logic here.
--   closed_at            UTC timestamp; a closed pot has balance 0 and takes no more allocations.
CREATE TABLE pot (
    id                     INTEGER PRIMARY KEY,
    name                   TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    account_id             INTEGER NOT NULL REFERENCES account (id) ON DELETE RESTRICT,
    area                   TEXT    NOT NULL CHECK (area IN ('private', 'business')),
    target_amount_cents    INTEGER CHECK (target_amount_cents IS NULL OR target_amount_cents > 0),
    target_date            TEXT,
    plan_start             TEXT    NOT NULL,
    plan_expense           INTEGER NOT NULL DEFAULT 0 CHECK (plan_expense IN (0, 1)),
    planned_transaction_id INTEGER REFERENCES "transaction" (id) ON DELETE SET NULL,
    emergency_rank         INTEGER CHECK (emergency_rank IS NULL OR emergency_rank >= 1),
    locked                 INTEGER NOT NULL DEFAULT 0 CHECK (locked IN (0, 1)),
    is_levy_pot            INTEGER NOT NULL DEFAULT 0 CHECK (is_levy_pot IN (0, 1)),
    closed_at              TEXT,
    created_at             TEXT    NOT NULL,
    CHECK (locked = 0 OR emergency_rank IS NULL),
    CHECK (closed_at IS NULL OR emergency_rank IS NULL),
    CHECK (plan_expense = 0 OR (target_amount_cents IS NOT NULL AND target_date IS NOT NULL))
);
CREATE UNIQUE INDEX pot_account_rank ON pot (account_id, emergency_rank) WHERE emergency_rank IS NOT NULL;
CREATE INDEX pot_account ON pot (account_id);

-- One movement of a pot's balance (signed cents, never 0).
--   deposit       + money into the pot: from the free balance, or with transaction_id the incoming
--                   leg of a transfer into the account (hand-entered or the pot's savings plan).
--   withdraw      - money back to the free balance (release, close), or with transaction_id the
--                   part of an expense on the account that the pot covers (UC step 5).
--   transfer_in / transfer_out  moving between two pots of the same account (UC 3b), always as a
--                   pair with one transfer_group_id; the account balance does not change.
--   emergency     - an emergency withdrawal (UC 5b, 5c), logged as such.
-- Deleting the booking behind an allocation deletes the allocation: the pot gets the money back.
CREATE TABLE pot_allocation (
    id                INTEGER PRIMARY KEY,
    pot_id            INTEGER NOT NULL REFERENCES pot (id) ON DELETE CASCADE,
    transaction_id    INTEGER UNIQUE REFERENCES "transaction" (id) ON DELETE CASCADE,
    amount_cents      INTEGER NOT NULL CHECK (amount_cents != 0),
    date              TEXT    NOT NULL,
    kind              TEXT    NOT NULL
                              CHECK (kind IN ('deposit', 'withdraw', 'transfer_in', 'transfer_out', 'emergency')),
    transfer_group_id TEXT,
    memo              TEXT,
    created_at        TEXT    NOT NULL,
    CHECK ((kind IN ('deposit', 'transfer_in')) = (amount_cents > 0)),
    CHECK ((kind IN ('transfer_in', 'transfer_out')) = (transfer_group_id IS NOT NULL))
);
CREATE INDEX pot_allocation_pot_date ON pot_allocation (pot_id, date);

-- recurring.pot_id (030) stays without a foreign key: adding one needs a rebuild of "recurring",
-- and dropping the old table would run its ON DELETE SET NULL on debt.recurring_id. The pots
-- service clears recurring.pot_id itself when a pot's savings plan or the pot goes.
CREATE INDEX recurring_pot ON recurring (pot_id) WHERE pot_id IS NOT NULL;
