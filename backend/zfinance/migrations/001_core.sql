-- 001 core: app-wide key/value settings (UI language, planning budgets, ...).
-- Features own their tables in their own number block; see ARCHITECTURE.md "Migrations".
-- Values are JSON text so a setting can hold a number, string or small object.
CREATE TABLE app_setting (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
