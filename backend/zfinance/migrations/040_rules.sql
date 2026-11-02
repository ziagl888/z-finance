-- 040 categorisation rules (UC-ZF-007, task -010).
-- A rule assigns a category to bookings whose text or counterparty matches its pattern.
--   match_kind      contains = case-insensitive substring; regex = regular expression (IGNORECASE).
--   priority        the highest wins among matching rules; equal priority -> the lowest id (created first).
--   counterparty    optional: filled into a matched booking that has none.
--   invalid_reason  syntax = the pattern does not compile; timeout = a match exceeded the time limit.
--                   An invalid rule is kept, shown and skipped; it is never applied and never deleted.
-- A category that is deleted takes its rules with it (ON DELETE CASCADE).
CREATE TABLE rule (
    id             INTEGER PRIMARY KEY,
    match_kind     TEXT    NOT NULL CHECK (match_kind IN ('contains', 'regex')),
    pattern        TEXT    NOT NULL CHECK (length(trim(pattern)) > 0),
    category_id    INTEGER NOT NULL REFERENCES category (id) ON DELETE CASCADE,
    counterparty   TEXT,
    priority       INTEGER NOT NULL DEFAULT 0,
    invalid_reason TEXT             CHECK (invalid_reason IS NULL OR invalid_reason IN ('syntax', 'timeout')),
    created_at     TEXT    NOT NULL
);
CREATE UNIQUE INDEX rule_unique ON rule (match_kind, pattern COLLATE NOCASE, category_id);
CREATE INDEX rule_category ON rule (category_id);

-- The uncategorised list and the rule engine filter on category IS NULL.
CREATE INDEX transaction_uncategorized ON "transaction" (date) WHERE category_id IS NULL;
