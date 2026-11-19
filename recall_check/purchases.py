"""Purchase record store, element E4 (FR-4, NFR-7).

Loads the store's invoice lines from a CSV file in the format of report
Section 7.3. Rows that fail a check are listed for the manager and the rest
load. The same file cannot be loaded twice.
"""

import csv
import hashlib
import os
import re
from datetime import date

from . import audit

COLUMNS = [
    "invoice_date", "supplier", "invoice_number", "product_code", "description", "brand",
    "pack_size", "quantity", "lot_code", "best_by", "shelf_location",
]
REQUIRED = ["invoice_date", "description", "quantity"]


class ImportError_(ValueError):
    """The whole file was refused."""


def _iso_date(text):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def check_row(row):
    """Return the reason a row is rejected, or None if it is acceptable."""
    for name in REQUIRED:
        if not (row.get(name) or "").strip():
            return f"{name} is missing"
    if not _iso_date(row["invoice_date"].strip()):
        return f"invoice_date '{row['invoice_date']}' is not a date in YYYY-MM-DD form"
    best_by = (row.get("best_by") or "").strip()
    if best_by and not _iso_date(best_by):
        return f"best_by '{best_by}' is not a date in YYYY-MM-DD form"
    quantity = row["quantity"].strip()
    if not quantity.isdigit() or int(quantity) <= 0:
        return f"quantity '{quantity}' is not a whole number above zero"
    return None


def import_csv(conn, path, imported_by):
    """Load a purchase file. Returns (import_id, accepted, rejected rows)."""
    if not (imported_by or "").strip():
        raise ImportError_("a named person must load the purchase file")
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    if conn.execute("SELECT 1 FROM purchase_imports WHERE file_sha256 = ?", (digest,)).fetchone():
        raise ImportError_("this exact file has already been loaded")

    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = [h.strip() for h in (reader.fieldnames or [])]
        missing = [c for c in COLUMNS if c not in header]
        unknown = [c for c in header if c not in COLUMNS]
        if missing or unknown:
            raise ImportError_(f"header does not match Section 7.3: missing {missing}, unknown {unknown}")
        rows = list(reader)

    cursor = conn.execute(
        """INSERT INTO purchase_imports (file_name, file_sha256, imported_by, imported_at, accepted, rejected)
           VALUES (?, ?, ?, ?, 0, 0)""",
        (os.path.basename(path), digest, imported_by.strip(), audit.now()))
    import_id = cursor.lastrowid
    accepted, rejected = 0, []
    for number, row in enumerate(rows, start=2):  # row 1 is the header
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        reason = check_row(row)
        if reason:
            raw = ",".join(row.get(c, "") for c in COLUMNS)
            conn.execute("INSERT INTO rejected_rows VALUES (?, ?, ?, ?)", (import_id, number, reason, raw))
            rejected.append({"row_number": number, "reason": reason})
            continue
        conn.execute(
            f"""INSERT INTO purchase_lines (import_id, row_number, {", ".join(COLUMNS)})
                VALUES (?, ?, {", ".join("?" for _ in COLUMNS)})""",
            [import_id, number] + [row[c] or None if c != "quantity" else int(row[c]) for c in COLUMNS])
        accepted += 1
    conn.execute("UPDATE purchase_imports SET accepted = ?, rejected = ? WHERE import_id = ?",
                 (accepted, len(rejected), import_id))
    audit.log(conn, imported_by.strip(), "import_purchases", f"import-{import_id}",
              f"{os.path.basename(path)}: {accepted} lines loaded, {len(rejected)} rejected"
              + "".join(f"; row {r['row_number']}: {r['reason']}" for r in rejected))
    conn.commit()
    return import_id, accepted, rejected


def lines(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM purchase_lines ORDER BY line_id")]


def line(conn, line_id):
    row = conn.execute("SELECT * FROM purchase_lines WHERE line_id = ?", (line_id,)).fetchone()
    return dict(row) if row else None
