"""SQLite storage for notices, extracted values and the audit log.

One local database file holds everything. SQLite ships with Python, so the
prototype needs nothing installed to store data.
"""

import os
import sqlite3

DEFAULT_DB_PATH = os.path.join("data", "local", "recall.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    notice_id          TEXT PRIMARY KEY,
    source             TEXT NOT NULL,
    channel            TEXT NOT NULL,
    received_at        TEXT NOT NULL,
    recall_number      TEXT,
    recalling_firm     TEXT NOT NULL,
    classification     TEXT NOT NULL,
    status             TEXT NOT NULL,
    initiation_date    TEXT NOT NULL,
    product_description TEXT,
    code_info          TEXT,
    original_text      TEXT NOT NULL,
    expands_notice_id  TEXT,
    case_status        TEXT NOT NULL DEFAULT 'Registered'
);

CREATE TABLE IF NOT EXISTS extracted_values (
    value_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id         TEXT NOT NULL REFERENCES notices(notice_id),
    type              TEXT NOT NULL,
    value             TEXT NOT NULL,
    source_text       TEXT NOT NULL,
    span_start        INTEGER NOT NULL,
    span_end          INTEGER NOT NULL,
    certainty         TEXT NOT NULL,
    extractor_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS uninterpreted_text (
    notice_id   TEXT NOT NULL REFERENCES notices(notice_id),
    source_text TEXT NOT NULL,
    span_start  INTEGER,
    span_end    INTEGER
);

-- E4 purchase record store. One row per invoice line, in the format of
-- report Section 7.3. Only invoice_date, description and quantity are required.
CREATE TABLE IF NOT EXISTS purchase_imports (
    import_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name    TEXT NOT NULL,
    file_sha256  TEXT NOT NULL UNIQUE,
    imported_by  TEXT NOT NULL,
    imported_at  TEXT NOT NULL,
    accepted     INTEGER NOT NULL,
    rejected     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_lines (
    line_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id      INTEGER NOT NULL REFERENCES purchase_imports(import_id),
    row_number     INTEGER NOT NULL,
    invoice_date   TEXT NOT NULL,
    supplier       TEXT,
    invoice_number TEXT,
    product_code   TEXT,
    description    TEXT NOT NULL,
    brand          TEXT,
    pack_size      TEXT,
    quantity       INTEGER NOT NULL,
    lot_code       TEXT,
    best_by        TEXT,
    shelf_location TEXT
);

CREATE TABLE IF NOT EXISTS rejected_rows (
    import_id  INTEGER NOT NULL REFERENCES purchase_imports(import_id),
    row_number INTEGER NOT NULL,
    reason     TEXT NOT NULL,
    raw        TEXT NOT NULL
);

-- E6 candidate and review store. A candidate is only a suggestion; its status
-- changes only through a named person's decision (FR-5, FR-7, NFR-3).
CREATE TABLE IF NOT EXISTS match_runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id       TEXT NOT NULL REFERENCES notices(notice_id),
    matcher_version TEXT NOT NULL,
    run_at          TEXT NOT NULL,
    lines_compared  INTEGER NOT NULL,
    candidates      INTEGER NOT NULL,
    result          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id       TEXT NOT NULL REFERENCES notices(notice_id),
    line_id         INTEGER NOT NULL REFERENCES purchase_lines(line_id),
    tier            INTEGER NOT NULL CHECK (tier IN (1, 2, 3)),
    certainty       TEXT NOT NULL CHECK (certainty IN ('standard', 'lower')),
    basis           TEXT NOT NULL,
    matched_values  TEXT NOT NULL,
    proposed_status TEXT,
    status          TEXT NOT NULL DEFAULT 'Pending'
                    CHECK (status IN ('Pending', 'Confirmed', 'Rejected', 'Needs-check-on-shelf')),
    matcher_version TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (notice_id, line_id)
);

-- Purchase lines the matcher compared and set aside, with the reason, so a
-- reviewer can see why a line with the right product code is not a candidate.
CREATE TABLE IF NOT EXISTS exclusions (
    notice_id TEXT NOT NULL REFERENCES notices(notice_id),
    line_id   INTEGER NOT NULL REFERENCES purchase_lines(line_id),
    reason    TEXT NOT NULL,
    UNIQUE (notice_id, line_id)
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id),
    outcome      TEXT NOT NULL,
    person       TEXT NOT NULL CHECK (length(trim(person)) > 0),
    decided_at   TEXT NOT NULL,
    note         TEXT
);

-- E9 worklist. One line per confirmed or needs-check-on-shelf candidate.
CREATE TABLE IF NOT EXISTS worklist_lines (
    worklist_line_id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id        TEXT NOT NULL REFERENCES notices(notice_id),
    candidate_id     INTEGER NOT NULL UNIQUE REFERENCES candidates(candidate_id),
    kind             TEXT NOT NULL CHECK (kind IN ('Pull', 'Check on shelf')),
    item             TEXT NOT NULL,
    location         TEXT NOT NULL,
    look_for         TEXT NOT NULL,
    last_received    TEXT NOT NULL,
    instruction      TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'Open'
                     CHECK (status IN ('Open', 'Found and pulled', 'None found', 'Could not verify')),
    outcome_by       TEXT,
    outcome_at       TEXT,
    outcome_note     TEXT,
    issued_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    entry_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    object_id TEXT NOT NULL,
    basis     TEXT NOT NULL
);

-- The audit log is append-only (FR-12, NFR-9): no entry can be edited or removed.
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


def connect(path=DEFAULT_DB_PATH):
    """Open the database, creating the folder and tables if needed."""
    if path != ":memory:":
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
