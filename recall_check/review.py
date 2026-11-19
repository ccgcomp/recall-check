"""Candidate and review store, element E6 (FR-5, FR-7, NFR-3).

A candidate is a suggestion until a named person decides it. This module is
the only place a candidate's status changes, and it changes only here, with a
person's name, a time and the outcome written to the decisions table and the
audit log. A confirmed or needs-check-on-shelf decision hands the candidate
to the worklist composer (E9).
"""

import json

from . import audit, worklist

OUTCOMES = ("Confirmed", "Rejected", "Needs-check-on-shelf")


class DecisionError(ValueError):
    pass


def candidates_for(conn, notice_id):
    rows = conn.execute(
        """SELECT c.*, p.invoice_date, p.supplier, p.invoice_number, p.product_code, p.description,
                  p.brand, p.pack_size, p.quantity, p.lot_code, p.best_by, p.shelf_location
           FROM candidates c JOIN purchase_lines p ON p.line_id = c.line_id
           WHERE c.notice_id = ? ORDER BY c.tier, c.candidate_id""", (notice_id,))
    out = []
    for row in rows:
        item = dict(row)
        ids = json.loads(item["matched_values"])
        item["matched"] = [dict(r) for r in conn.execute(
            f"SELECT * FROM extracted_values WHERE value_id IN ({','.join('?' * len(ids))})", ids)] if ids else []
        out.append(item)
    return out


def exclusions_for(conn, notice_id):
    return [dict(r) for r in conn.execute(
        """SELECT e.reason, p.* FROM exclusions e JOIN purchase_lines p ON p.line_id = e.line_id
           WHERE e.notice_id = ? ORDER BY p.line_id""", (notice_id,))]


def decide(conn, candidate_id, outcome, person, note=""):
    """Record a named person's decision on one candidate.

    Each candidate is decided once, so the record cannot be rewritten after
    the fact. Correcting a wrong decision is not built in the prototype.
    """
    person = (person or "").strip()
    if not person:
        raise DecisionError("a decision must name the person making it")
    if outcome not in OUTCOMES:
        raise DecisionError(f"outcome must be one of {', '.join(OUTCOMES)}")
    row = conn.execute("SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise DecisionError(f"candidate {candidate_id} does not exist")
    if row["status"] != "Pending":
        raise DecisionError(f"candidate {candidate_id} was already decided: {row['status']}")

    when = audit.now()
    conn.execute("INSERT INTO decisions (candidate_id, outcome, person, decided_at, note) VALUES (?, ?, ?, ?, ?)",
                 (candidate_id, outcome, person, when, note or None))
    conn.execute("UPDATE candidates SET status = ? WHERE candidate_id = ?", (outcome, candidate_id))
    audit.log(conn, person, "decide", f"candidate-{candidate_id}",
              f"{outcome} for {row['notice_id']}" + (f"; note: {note}" if note else ""))
    line_id = None
    if outcome in ("Confirmed", "Needs-check-on-shelf"):
        line_id = worklist.compose(conn, candidate_id)
    conn.commit()
    return line_id


def pending_count(conn, notice_id):
    return conn.execute("SELECT COUNT(*) FROM candidates WHERE notice_id = ? AND status = 'Pending'",
                        (notice_id,)).fetchone()[0]
