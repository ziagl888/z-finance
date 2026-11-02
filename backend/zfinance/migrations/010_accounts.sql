-- 010 accounts and categories (UC-ZF-001, task -007).
-- Amounts are integer cents, business dates YYYY-MM-DD, audit timestamps ISO-8601 UTC.
-- Settings of this feature (total reserve floor, first-run flag) live in app_setting.
CREATE TABLE account (
    id                    INTEGER PRIMARY KEY,
    name                  TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    area                  TEXT    NOT NULL CHECK (area IN ('private', 'business')),
    type                  TEXT    NOT NULL CHECK (type IN ('giro', 'savings', 'cash', 'card', 'liability')),
    opening_balance_cents INTEGER NOT NULL,
    opening_date          TEXT    NOT NULL,
    reserve_floor_cents   INTEGER NOT NULL DEFAULT 0 CHECK (reserve_floor_cents >= 0),
    archived_at           TEXT,
    created_at            TEXT    NOT NULL
);
CREATE UNIQUE INDEX account_name_unique ON account (name COLLATE NOCASE);

-- One level of nesting: a parent is a top-level category of the same kind (enforced in the
-- service). ON DELETE RESTRICT keeps a parent from vanishing under its subcategories.
CREATE TABLE category (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    kind       TEXT    NOT NULL CHECK (kind IN ('income', 'expense', 'transfer')),
    area       TEXT             CHECK (area IS NULL OR area IN ('private', 'business')),
    parent_id  INTEGER REFERENCES category (id) ON DELETE RESTRICT,
    created_at TEXT    NOT NULL
);
-- The same name may exist once per parent and area ("Insurance" private and business).
CREATE UNIQUE INDEX category_name_unique ON category (ifnull(parent_id, 0), ifnull(area, ''), name COLLATE NOCASE);
CREATE INDEX category_parent ON category (parent_id);
