-- 050 CSV import (UC-ZF-005, task -012): bank profiles, the log of import runs, and the link from
-- an imported booking to its run.
--   import_profile  how one bank's CSV export is read. delimiter/encoding 'auto' = detect; a file
--                   whose delimiter or encoding cannot be detected for sure is refused, never guessed.
--                   column_map (JSON) maps roles to header names: date, text and either amount or
--                   debit + credit; counterparty is optional. skip_rows = records before the header.
--   import_run      one committed import: counts (JSON: lines, new, matched, duplicates, suspects,
--                   skipped, errors, uncategorized) and the line errors that were skipped (JSON list).
-- A deleted profile keeps its runs (profile_id set to NULL); an account can only be deleted without
-- bookings, so its runs go with it.
CREATE TABLE import_profile (
    id           INTEGER PRIMARY KEY,
    bank_name    TEXT    NOT NULL CHECK (trim(bank_name) != ''),
    delimiter    TEXT    NOT NULL DEFAULT 'auto' CHECK (delimiter IN ('auto', 'semicolon', 'comma', 'tab')),
    encoding     TEXT    NOT NULL DEFAULT 'auto' CHECK (encoding IN ('auto', 'utf-8', 'cp1252')),
    date_format  TEXT    NOT NULL DEFAULT '%d.%m.%Y',
    decimal_sep  TEXT    NOT NULL DEFAULT ',' CHECK (decimal_sep IN (',', '.')),
    column_map   TEXT    NOT NULL CHECK (json_valid(column_map)),
    skip_rows    INTEGER NOT NULL DEFAULT 0 CHECK (skip_rows BETWEEN 0 AND 50),
    created_at   TEXT    NOT NULL
);
CREATE UNIQUE INDEX import_profile_bank ON import_profile (bank_name COLLATE NOCASE);

CREATE TABLE import_run (
    id          INTEGER PRIMARY KEY,
    profile_id  INTEGER          REFERENCES import_profile (id) ON DELETE SET NULL,
    account_id  INTEGER NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    file_name   TEXT,
    file_hash   TEXT    NOT NULL,
    counts      TEXT    NOT NULL CHECK (json_valid(counts)),
    errors      TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(errors)),
    created_at  TEXT    NOT NULL
);
CREATE INDEX import_run_account ON import_run (account_id);

-- The run a booking was imported by (020 is not edited); NULL for every other source.
ALTER TABLE "transaction" ADD COLUMN import_run_id INTEGER REFERENCES import_run (id) ON DELETE SET NULL;
CREATE INDEX transaction_import_run ON "transaction" (import_run_id);
