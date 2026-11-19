"""Worklist composer, element E9 (FR-8, FR-10).

Builds one shelf-check line for each candidate a person has confirmed or
marked needs-check-on-shelf. Every field is copied from the purchase line or
from values extracted from the notice, each with the notice text it came
from. Nothing on a line is written by AI, and a line with no codes to look for
is not issued.

Two kinds of line:
  Pull            confirmed: the invoice already ties this delivery to the recall.
  Check on shelf  the invoice could not settle it, so the person at the shelf
                  reads the lot and date on each unit and pulls only matches.
                  This is the route from needs-check-on-shelf to the floor that
                  the Assignment 2 design was missing.
"""

import json

from . import audit, matcher

OUTCOMES = ("Found and pulled", "None found", "Could not verify")
CODE_TYPES = ("upc", "other_code", "lot", "date", "date_range")
LABELS = {"upc": "UPC", "other_code": "Item code", "lot": "Lot", "date": "Date", "date_range": "Date range"}


class WorklistError(ValueError):
    pass


def _look_for(conn, notice_id, matched_ids=()):
    rows = conn.execute(
        f"""SELECT * FROM extracted_values WHERE notice_id = ? AND type IN ({','.join('?' * len(CODE_TYPES))})
            ORDER BY span_start""", (notice_id, *CODE_TYPES))
    out, seen = [], set()
    for row in rows:
        key = (row["type"], row["value"])
        if key in seen:
            if row["value_id"] in matched_ids:
                next(c for c in out if (c["type"], c["raw"]) == key)["on_invoice"] = True
            continue
        seen.add(key)
        value = row["value"]
        if row["type"] == "date_range":
            start, _, end = value.partition("/")
            value = f"{start} through {end}"
        out.append({"label": LABELS[row["type"]], "value": value, "source_text": row["source_text"],
                    "span": [row["span_start"], row["span_end"]], "type": row["type"], "raw": row["value"],
                    "on_invoice": row["value_id"] in matched_ids})
    return out


def _delivery(row):
    text = f"{row['quantity']} on {row['invoice_date']}"
    if row["invoice_number"]:
        text += f", invoice {row['invoice_number']}"
    if row["supplier"]:
        text += f" from {row['supplier']}"
    return text


def _item_key(code):
    return matcher.product_number(code) if matcher.is_gtin(code) else matcher.norm_code(code)


def _last_received(conn, line):
    """The matched delivery, plus the most recent delivery of the same item if that is a later one.

    The same item means the same product code, or the same description when
    the invoice has no code.
    """
    if line["product_code"]:
        # Compare normalized codes, so "7402 3550 0110" and "740235500110" are one item.
        key = _item_key(line["product_code"])
        same = [r for r in conn.execute("SELECT * FROM purchase_lines WHERE product_code IS NOT NULL")
                if _item_key(r["product_code"]) == key]
        row = max(same, key=lambda r: (r["invoice_date"], r["line_id"]))
    else:
        row = conn.execute("""SELECT * FROM purchase_lines WHERE product_code IS NULL AND description = ?
                              ORDER BY invoice_date DESC, line_id DESC LIMIT 1""", (line["description"],)).fetchone()
    text = "This delivery: " + _delivery(line)
    if row and row["line_id"] != line["line_id"]:
        text += ". Most recent delivery of this item: " + _delivery(row)
    return text


def compose(conn, candidate_id):
    """Issue the worklist line for one decided candidate. Called by review.decide."""
    cand = conn.execute("SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
    if cand is None or cand["status"] not in ("Confirmed", "Needs-check-on-shelf"):
        raise WorklistError("only a confirmed or needs-check-on-shelf candidate gets a worklist line")
    line = conn.execute("SELECT * FROM purchase_lines WHERE line_id = ?", (cand["line_id"],)).fetchone()
    look_for = _look_for(conn, cand["notice_id"], set(json.loads(cand["matched_values"])))
    if not look_for:
        audit.log(conn, "E9 worklist composer", "refuse_worklist_line", f"candidate-{candidate_id}",
                  "no codes to look for, so no line issued; routed to the manager")
        audit.log(conn, "E7 coordinator", "route_to_manager", cand["notice_id"],
                  f"candidate {candidate_id} confirmed but the notice gives no codes to look for")
        return None

    item = " ".join(filter(None, [line["brand"], line["description"], line["pack_size"]]))
    if line["brand"] and line["description"].lower().startswith(line["brand"].lower()):
        item = " ".join(filter(None, [line["description"], line["pack_size"]]))
    location = line["shelf_location"] or "Not on the invoice: ask the manager where it is stocked"
    has_scope = any(c["label"] in ("Lot", "Date", "Date range") for c in look_for)

    if cand["status"] == "Confirmed":
        kind = "Pull"
        instruction = ("Read the lot and date on every unit and pull each unit that carries a code listed here."
                       if has_scope else "The notice names no lot or date. Pull every unit of this item.")
    else:
        kind = "Check on shelf"
        instruction = ("The invoice did not show which lot was delivered. Read the lot and date on every unit "
                       "and pull each unit that carries a code listed here. If the codes on the package "
                       "cannot be read, record Could not verify.")

    cursor = conn.execute(
        """INSERT INTO worklist_lines (notice_id, candidate_id, kind, item, location, look_for,
               last_received, instruction, issued_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (cand["notice_id"], candidate_id, kind, item, location, json.dumps(look_for),
         _last_received(conn, line), instruction, audit.now()))
    line_id = cursor.lastrowid
    audit.log(conn, "E9 worklist composer", "issue_worklist_line", f"worklist-{line_id}",
              f"{kind} line for candidate {candidate_id} ({cand['notice_id']}): {item} at {location}; "
              f"{len(look_for)} code(s) to look for")
    conn.execute("UPDATE notices SET case_status = 'Worklist issued' WHERE notice_id = ? "
                 "AND case_status = 'In review'", (cand["notice_id"],))
    return line_id


def record_outcome(conn, worklist_line_id, outcome, person, note=""):
    """Record what a named person found at the shelf (FR-7)."""
    person = (person or "").strip()
    if not person:
        raise WorklistError("an outcome must name the person who checked the shelf")
    if outcome not in OUTCOMES:
        raise WorklistError(f"outcome must be one of {', '.join(OUTCOMES)}")
    row = conn.execute("SELECT * FROM worklist_lines WHERE worklist_line_id = ?", (worklist_line_id,)).fetchone()
    if row is None:
        raise WorklistError(f"worklist line {worklist_line_id} does not exist")
    if row["status"] != "Open":
        raise WorklistError(f"worklist line {worklist_line_id} is already closed: {row['status']}")
    when = audit.now()
    conn.execute("""UPDATE worklist_lines SET status = ?, outcome_by = ?, outcome_at = ?, outcome_note = ?
                    WHERE worklist_line_id = ?""", (outcome, person, when, note or None, worklist_line_id))
    audit.log(conn, person, "record_outcome", f"worklist-{worklist_line_id}",
              f"{outcome} for {row['notice_id']}" + (f"; note: {note}" if note else ""))
    if outcome == "Could not verify":
        audit.log(conn, "E7 coordinator", "route_to_manager", row["notice_id"],
                  f"worklist line {worklist_line_id}: codes on the shelf could not be verified")
    conn.commit()


def lines(conn, notice_id=None, status=None):
    sql = """SELECT w.*, n.classification, n.recalling_firm FROM worklist_lines w
             JOIN notices n ON n.notice_id = w.notice_id WHERE 1 = 1"""
    params = []
    if notice_id:
        sql += " AND w.notice_id = ?"
        params.append(notice_id)
    if status:
        sql += " AND w.status = ?"
        params.append(status)
    sql += " ORDER BY n.classification, w.location, w.worklist_line_id"
    out = []
    for row in conn.execute(sql, params):
        item = dict(row)
        item["look_for"] = json.loads(item["look_for"])
        out.append(item)
    return out
