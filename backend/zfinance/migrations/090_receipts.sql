-- 090 receipts (UC-ZF-013, task -016): receipt photos sent in by the receipt agent.
-- One run of the agent is an agent_run; each photo it hands over is a receipt.
--   counts_json   what the agent reports for the run (JSON object), shown under "Agent runs".
CREATE TABLE agent_run (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    counts_json TEXT NOT NULL DEFAULT '{}',
    note        TEXT
);

--   transaction_id  the booking the photo belongs to: created for it, or an existing card booking
--                   it was matched to. NULL while the receipt waits on the review list (and after
--                   its booking was deleted).
--   file_path       photo location relative to the data directory: receipts/<year>/<id>.<ext>,
--                   built from server values only.
--   image_hash      sha256 of the photo bytes, computed by the server: the same photo twice is a
--                   duplicate.
--   extracted_json  the fields the agent sent (date, amount, merchant, payment method, hints, ...).
--   confidence      the agent's score 0..1 (a score, not money).
--   duplicate_key   "<date>|<amount_cents>|<casefolded merchant>" of the extracted fields when all
--                   three are known; a second receipt with the same key is a duplicate.
--   status          booked = has its booking; review = waits for a correction (reason says why);
--                   duplicate = same date, amount and merchant as an earlier receipt (duplicate_of).
CREATE TABLE receipt (
    id             INTEGER PRIMARY KEY,
    transaction_id INTEGER REFERENCES "transaction" (id) ON DELETE SET NULL,
    file_path      TEXT    NOT NULL,
    media_type     TEXT    NOT NULL CHECK (media_type IN ('image/jpeg', 'image/png', 'image/webp')),
    image_hash     TEXT    NOT NULL UNIQUE,
    extracted_json TEXT    NOT NULL DEFAULT '{}',
    confidence     REAL,
    duplicate_key  TEXT,
    status         TEXT    NOT NULL CHECK (status IN ('booked', 'review', 'duplicate')),
    reason         TEXT,
    duplicate_of   INTEGER REFERENCES receipt (id) ON DELETE SET NULL,
    message_ref    TEXT,
    run_id         INTEGER REFERENCES agent_run (id) ON DELETE SET NULL,
    created_at     TEXT    NOT NULL,
    booked_at      TEXT
);
CREATE INDEX receipt_status ON receipt (status);
CREATE INDEX receipt_transaction ON receipt (transaction_id);
CREATE INDEX receipt_run ON receipt (run_id);
CREATE INDEX receipt_duplicate_key ON receipt (duplicate_key);
