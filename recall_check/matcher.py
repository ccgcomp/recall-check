"""Rule-based matcher, element E5 (FR-3, FR-4, FR-10).

Compares the values extracted from a notice with the store's purchase lines
using fixed rules. No AI is involved, so the same notice and the same purchase
file always give the same candidates. The rules follow report Section 7.4:

  Tier 1  The product code matches, and the invoice lot or best-by date falls
          inside the notice's lots, dates or date ranges, or the notice names
          no lot or date at all, so every unit of the product is in scope.
  Tier 2  The product code matches but the invoice carries nothing to compare
          with the notice's lots or dates. Proposed as needs-check-on-shelf,
          because only the package on the shelf can settle it.
  Tier 3  No code comparison is possible: the notice has no UPC, or the
          invoice line has no comparable code. Brand, product name and pack
          size are compared with the invoice description. Lower certainty.

A line whose code matches but whose printed lot or date falls outside the
notice is set aside as an exclusion with its reason, never dropped silently.
When nothing matches, "No candidate identified" is recorded. The matcher
never records that a store is unaffected or clear.
"""

import json
import re

from . import audit

MATCHER_VERSION = "m1"
ACTOR = f"E5 rule-based matcher {MATCHER_VERSION}"
NO_CANDIDATE = "No candidate identified"

STOP_WORDS = {
    "and", "with", "the", "of", "in", "a", "an", "for", "by", "to", "per", "net", "wt",
    "oz", "lb", "lbs", "g", "kg", "ml", "ct", "count", "pack", "bag", "box", "case",
    "jar", "tray", "units", "unit", "product", "brand", "flavored", "single", "x",
}


# ---- product codes ----------------------------------------------------------

def digits(code):
    return re.sub(r"\D", "", code or "")


def gtin_check_ok(code):
    """True when the last digit is the correct GS1 check digit."""
    d = digits(code)
    if len(d) not in (8, 12, 13, 14):
        return False
    body, check = d[:-1], int(d[-1])
    total = sum(int(ch) * (3 if i % 2 == 0 else 1) for i, ch in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def is_gtin(code):
    """A code that looks like a UPC, EAN or GTIN: 12, 13 or 14 digits once spaces and dashes go."""
    d = digits(code)
    return len(d) in (12, 13, 14) and len(d) == len(re.sub(r"[\s\-]", "", code or ""))


def product_number(code):
    """The part of a GTIN that names the product.

    The code is padded to 14 digits, then the packaging indicator (first digit)
    and the check digit (last) are dropped. A 12-digit unit UPC and the 14-digit
    case GTIN built from it therefore compare as the same product, which is
    what a recall needs: a case code on the notice covers the units inside.
    """
    d = digits(code).zfill(14)
    return d[1:13]


def norm_code(code):
    """Normalize a non-GTIN code or lot: upper case, letters and digits only."""
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


# ---- dates ------------------------------------------------------------------

def date_matches(best_by, notice_date):
    """Single notice date, at day or month precision."""
    return best_by[:len(notice_date)] == notice_date


def in_range(best_by, value):
    """Closed range 'start/end'. Never reduced to its first date (FR-2)."""
    start, _, end = value.partition("/")
    if not start or not end:
        return False
    return best_by[:len(start)] >= start and best_by[:len(end)] <= end


# ---- descriptions -----------------------------------------------------------

def tokens(text):
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    out = set()
    for word in words:
        if word in STOP_WORDS or word.isdigit():
            continue
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        out.add(word)
    return out


def description_match(values, line):
    """Tier 3 test. Returns (matched, basis, value ids) for one purchase line."""
    line_words = tokens(" ".join(filter(None, [line["description"], line.get("brand"), line.get("supplier")])))
    brands = [v for v in values if v["type"] == "brand"]
    names = [v for v in values if v["type"] == "product_name"]
    brand_hit = next((b for b in brands if tokens(b["value"]) and tokens(b["value"]) <= line_words), None)
    best, best_share = None, 0.0
    for name in names:
        wanted = tokens(name["value"])
        if wanted:
            share = len(wanted & line_words) / len(wanted)
            if share > best_share:
                best, best_share = name, share
    # With the brand on the invoice, half the product-name words are enough.
    # Without it, every product-name word must appear.
    if not best or not ((brand_hit and best_share >= 0.5) or best_share == 1.0):
        return False, "", []
    used = [best["value_id"]]
    parts = []
    if brand_hit:
        parts.append(f"brand '{brand_hit['value']}' appears on the invoice line")
        used.append(brand_hit["value_id"])
    parts.append(f"{best_share:.0%} of the words in product name '{best['value']}' appear in "
                 f"'{line['description']}'")
    pack_words = tokens(line.get("pack_size") or "") | set(re.findall(r"\d+(?:\.\d+)?", line.get("pack_size") or ""))
    for pack in (v for v in values if v["type"] == "pack_size"):
        numbers = set(re.findall(r"\d+(?:\.\d+)?", pack["value"]))
        if numbers and numbers <= pack_words:
            parts.append(f"pack size '{pack['value']}' agrees")
            used.append(pack["value_id"])
            break
    return True, "; ".join(parts), used


# ---- lot and date check -----------------------------------------------------

def scope_check(values, line):
    """Compare the invoice's lot and best-by date with the notice.

    Returns one of
      ("in", reason, value ids)       the line falls inside the notice
      ("unknown", reason, [])         nothing on the invoice can be compared
      ("out", reason, [])             comparable, and outside the notice
      ("all", reason, [])             the notice names no lot or date
    """
    lots = [v for v in values if v["type"] == "lot"]
    dates = [v for v in values if v["type"] == "date"]
    ranges = [v for v in values if v["type"] == "date_range"]
    if not (lots or dates or ranges):
        return "all", "the notice names no lot or date, so every unit of the product is in scope", []

    lot = norm_code(line.get("lot_code"))
    best_by = line.get("best_by") or ""
    compared = []
    if lot and lots:
        hit = next((v for v in lots if norm_code(v["value"]) == lot), None)
        if hit:
            return "in", f"invoice lot {line['lot_code']} is listed on the notice", [hit["value_id"]]
        compared.append(f"invoice lot {line['lot_code']} is not among the {len(lots)} lots on the notice")
    if best_by and (dates or ranges):
        for v in ranges:
            if in_range(best_by, v["value"]):
                return "in", f"invoice best-by {best_by} falls inside the range {v['value']}", [v["value_id"]]
        for v in dates:
            if date_matches(best_by, v["value"]):
                return "in", f"invoice best-by {best_by} matches notice date {v['value']}", [v["value_id"]]
        compared.append(f"invoice best-by {best_by} is outside every date and range on the notice")
    if compared:
        return "out", "; ".join(compared), []
    return "unknown", "the invoice carries no lot or best-by date that can be compared with the notice", []


# ---- matching one notice ----------------------------------------------------

def compare(values, line):
    """Apply the tiers to one purchase line. Returns a dict, or None if unrelated."""
    upcs = [v for v in values if v["type"] == "upc"]
    others = [v for v in values if v["type"] == "other_code"]
    code = line.get("product_code") or ""

    code_hit = None
    if code and is_gtin(code):
        code_hit = next((v for v in upcs if product_number(v["value"]) == product_number(code)), None)
        if code_hit:
            note = "" if gtin_check_ok(code) and gtin_check_ok(code_hit["value"]) else \
                " (a check digit did not verify, so compare the printed code by eye)"
            code_basis = f"product code {code} matches notice code {code_hit['value']}{note}"
    if not code_hit and code:
        code_hit = next((v for v in others if norm_code(v["value"]) == norm_code(code)), None)
        if code_hit:
            code_basis = f"item code {code} matches notice code {code_hit['value']}"

    if code_hit:
        scope, reason, scope_ids = scope_check(values, line)
        if scope == "out":
            return {"exclude": f"{code_basis}, but {reason}"}
        ids = [code_hit["value_id"]] + scope_ids
        if scope in ("in", "all"):
            return {"tier": 1, "certainty": "standard", "basis": f"{code_basis}; {reason}",
                    "values": ids, "proposed": None}
        return {"tier": 2, "certainty": "standard", "basis": f"{code_basis}; {reason}",
                "values": ids, "proposed": "Needs-check-on-shelf"}

    # A GTIN on the invoice that differs from a UPC on the notice is a different product.
    if code and is_gtin(code) and upcs:
        return None

    matched, basis, ids = description_match(values, line)
    if not matched:
        return None
    why = "the notice gives no UPC" if not upcs else "the invoice line has no comparable product code"
    scope, reason, scope_ids = scope_check(values, line)
    if scope == "out":
        return {"exclude": f"description matched ({basis}), but {reason}"}
    return {"tier": 3, "certainty": "lower",
            "basis": f"described match because {why}: {basis}; {reason}",
            "values": ids + scope_ids,
            "proposed": "Needs-check-on-shelf" if scope == "unknown" else None}


def values_for(conn, notice_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM extracted_values WHERE notice_id = ? ORDER BY span_start", (notice_id,))]


MATCHABLE = ("Extracted", "In review", NO_CANDIDATE)


def match_notice(conn, notice_id):
    """Match one notice against every purchase line. Adds only new candidates."""
    notice = conn.execute("SELECT * FROM notices WHERE notice_id = ?", (notice_id,)).fetchone()
    if notice is None:
        raise KeyError(f"notice {notice_id} is not registered")
    if notice["case_status"] not in MATCHABLE:
        return {"notice_id": notice_id, "skipped": f"case status is {notice['case_status']}"}
    values = values_for(conn, notice_id)
    lines = [dict(r) for r in conn.execute("SELECT * FROM purchase_lines ORDER BY line_id")]
    known = {r[0] for r in conn.execute("SELECT line_id FROM candidates WHERE notice_id = ?", (notice_id,))}
    known |= {r[0] for r in conn.execute("SELECT line_id FROM exclusions WHERE notice_id = ?", (notice_id,))}

    added = 0
    for line in lines:
        if line["line_id"] in known:
            continue
        result = compare(values, line)
        if result is None:
            continue
        if "exclude" in result:
            conn.execute("INSERT INTO exclusions VALUES (?, ?, ?)", (notice_id, line["line_id"], result["exclude"]))
            audit.log(conn, ACTOR, "exclude_line", notice_id, f"purchase line {line['line_id']}: {result['exclude']}")
            continue
        cursor = conn.execute(
            """INSERT INTO candidates (notice_id, line_id, tier, certainty, basis, matched_values,
                   proposed_status, matcher_version, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (notice_id, line["line_id"], result["tier"], result["certainty"], result["basis"],
             json.dumps(result["values"]), result["proposed"], MATCHER_VERSION, audit.now()))
        audit.log(conn, ACTOR, "propose_candidate", f"candidate-{cursor.lastrowid}",
                  f"{notice_id} x purchase line {line['line_id']}, tier {result['tier']}: {result['basis']}")
        added += 1

    total = conn.execute("SELECT COUNT(*) FROM candidates WHERE notice_id = ?", (notice_id,)).fetchone()[0]
    result_text = f"{total} candidate(s)" if total else NO_CANDIDATE
    conn.execute(
        "INSERT INTO match_runs (notice_id, matcher_version, run_at, lines_compared, candidates, result) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (notice_id, MATCHER_VERSION, audit.now(), len(lines), total, result_text))
    if notice["case_status"] in ("Extracted", NO_CANDIDATE):
        conn.execute("UPDATE notices SET case_status = ? WHERE notice_id = ?",
                     ("In review" if total else NO_CANDIDATE, notice_id))
    audit.log(conn, ACTOR, "match", notice_id,
              f"compared {len(lines)} purchase lines: {added} new candidate(s); {result_text}")
    conn.commit()
    return {"notice_id": notice_id, "added": added, "total": total, "result": result_text}


def match_all(conn):
    ids = [r[0] for r in conn.execute("SELECT notice_id FROM notices ORDER BY notice_id")]
    return [match_notice(conn, notice_id) for notice_id in ids]
