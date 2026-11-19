"""Review workbench, element E8 (FR-6, FR-7).

A small local web page served by Python's standard library. It listens on
127.0.0.1 only, so nothing outside this computer can reach it. Every form that
changes something asks for the person's name, and the name is written with
the decision. Access roles (E10) come later; until then this page is for one
manager on one computer.

    python -m recall_check review          then open http://127.0.0.1:8000
"""

import html
import sqlite3
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import audit, db, matcher, review, worklist

STYLE = """
:root { --ink:#1d2433; --muted:#5b6475; --line:#d9dee7; --bg:#f6f7f9; --card:#fff;
        --accent:#1f5fbf; --warn:#a15c00; --warnbg:#fff4e0; --bad:#a32020; --badbg:#fdeaea;
        --ok:#1e7a3c; --okbg:#e7f5ec; --mark:#fff0a8; --cand:#ffd36b; }
* { box-sizing:border-box; }
body { margin:0; font:15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; color:var(--ink); background:var(--bg); }
header { background:#14213d; color:#fff; padding:12px 24px; display:flex; gap:24px; align-items:center; flex-wrap:wrap; }
header b { font-size:16px; margin-right:12px; }
header a { color:#cfd8ea; text-decoration:none; } header a:hover { color:#fff; }
main { max-width:1100px; margin:0 auto; padding:20px 16px 48px; }
h1 { font-size:22px; margin:8px 0 4px; } h2 { font-size:17px; margin:28px 0 8px; }
.sub { color:var(--muted); margin:0 0 16px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin:10px 0; }
table { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:8px; overflow:hidden; }
th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
th { background:#eef1f6; font-size:13px; color:var(--muted); font-weight:600; }
tr:last-child td { border-bottom:none; }
a { color:var(--accent); }
.pill { display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px; font-weight:600; background:#eef1f6; color:var(--muted); white-space:nowrap; }
.c1 { background:var(--badbg); color:var(--bad); }
.warn { background:var(--warnbg); color:var(--warn); }
.ok { background:var(--okbg); color:var(--ok); }
.notice-text { white-space:pre-wrap; font-family:ui-monospace, Consolas, monospace; font-size:13px; background:#fbfbfc; border:1px solid var(--line); border-radius:6px; padding:12px; }
mark.v { background:var(--mark); } mark.c { background:var(--cand); outline:1px solid #d79b00; } mark.u { background:var(--badbg); text-decoration:underline wavy var(--bad); }
.legend span { margin-right:14px; font-size:13px; color:var(--muted); }
.grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:6px 18px; }
.k { color:var(--muted); font-size:13px; }
.basis { background:#f3f6fb; border-left:3px solid var(--accent); padding:6px 10px; margin:8px 0; font-size:14px; }
.src { font-family:ui-monospace, Consolas, monospace; font-size:13px; background:var(--cand); padding:0 3px; border-radius:3px; }
form.inline { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:10px; }
input[type=text] { padding:6px 8px; border:1px solid var(--line); border-radius:6px; font:inherit; min-width:0; }
button { padding:6px 12px; border-radius:6px; border:1px solid var(--line); background:#fff; font:inherit; cursor:pointer; }
button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
button.danger { color:var(--bad); }
.banner { padding:10px 14px; border-radius:8px; margin:10px 0; }
.codes li { margin:2px 0; }
.small { font-size:13px; color:var(--muted); }
@media print { header, form.inline { display:none; } body { background:#fff; } .card { break-inside:avoid; } }
"""


ON_INVOICE = ' <span class="pill ok">on the invoice</span>'


def esc(value):
    return html.escape("" if value is None else str(value))


def class_pill(classification):
    css = "pill c1" if classification == "Class I" else "pill"
    return f'<span class="{css}">{esc(classification)}</span>'


def status_pill(status):
    css = {"Routed to manager": "warn", "Worklist issued": "ok", "Confirmed": "ok", "Rejected": "",
           "Needs-check-on-shelf": "warn", "Pending": "warn", "In review": "warn", "Open": "warn",
           "Found and pulled": "ok", "Could not verify": "c1"}.get(status, "")
    return f'<span class="pill {css}">{esc(status)}</span>'


def page(title, body):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(title)}</title>
<style>{STYLE}</style></head><body>
<header><b>Recall shelf check</b><a href="/">Notices</a><a href="/worklist">Worklist</a>
<a href="/purchases">Purchase records</a><a href="/audit">Audit log</a></header>
<main>{body}</main></body></html>"""


def highlight(text, values, candidate_ids, unread):
    """Mark extracted values, values a candidate matched on, and unread text in the notice."""
    marks = [None] * len(text)
    for start, end in ((u["span_start"], u["span_end"]) for u in unread if u["span_start"] is not None):
        for i in range(start, end):
            marks[i] = "u"
    for value in values:
        kind = "c" if value["value_id"] in candidate_ids else "v"
        for i in range(value["span_start"], value["span_end"]):
            if marks[i] != "c":
                marks[i] = kind
    out, i = [], 0
    while i < len(text):
        j = i
        while j < len(text) and marks[j] == marks[i]:
            j += 1
        chunk = esc(text[i:j])
        out.append(f'<mark class="{marks[i]}">{chunk}</mark>' if marks[i] else chunk)
        i = j
    return "".join(out)


def name_field(person):
    return (f'<input type="text" name="person" placeholder="Your name" required value="{esc(person)}" '
            f'aria-label="Your name">')


# ---- pages ------------------------------------------------------------------

def notices_page(conn):
    rows = conn.execute("""
        SELECT n.*,
          (SELECT COUNT(*) FROM candidates c WHERE c.notice_id = n.notice_id) AS cands,
          (SELECT COUNT(*) FROM candidates c WHERE c.notice_id = n.notice_id AND c.status = 'Pending') AS pending,
          (SELECT COUNT(*) FROM worklist_lines w WHERE w.notice_id = n.notice_id AND w.status = 'Open') AS open_lines
        FROM notices n
        ORDER BY CASE n.classification WHEN 'Class I' THEN 0 WHEN 'Class II' THEN 1 ELSE 2 END,
                 pending DESC, n.notice_id""").fetchall()
    body = ['<h1>Recall notices</h1><p class="sub">Class I notices are listed first. '
            'Every match below is a candidate until a named person decides it.</p>',
            "<table><tr><th>Notice</th><th>Class</th><th>Firm and product</th><th>Status</th>"
            "<th>Candidates</th><th>Open lines</th></tr>"]
    for r in rows:
        product = (r["product_description"] or "")[:90]
        pending = f' <span class="pill warn">{r["pending"]} to decide</span>' if r["pending"] else ""
        body.append(
            f'<tr><td><a href="/notice/{quote(r["notice_id"])}">{esc(r["notice_id"])}</a></td>'
            f'<td>{class_pill(r["classification"])}</td>'
            f'<td><b>{esc(r["recalling_firm"])}</b><br><span class="small">{esc(product)}</span></td>'
            f'<td>{status_pill(r["case_status"])}</td><td>{r["cands"]}{pending}</td><td>{r["open_lines"]}</td></tr>')
    body.append("</table>")
    return page("Recall notices", "".join(body))


def notice_page(conn, notice_id, person):
    n = conn.execute("SELECT * FROM notices WHERE notice_id = ?", (notice_id,)).fetchone()
    if n is None:
        return None
    values = matcher.values_for(conn, notice_id)
    unread = [dict(r) for r in conn.execute("SELECT * FROM uninterpreted_text WHERE notice_id = ?", (notice_id,))]
    cands = review.candidates_for(conn, notice_id)
    matched_ids = {v["value_id"] for c in cands for v in c["matched"]}
    run = conn.execute("SELECT * FROM match_runs WHERE notice_id = ? ORDER BY run_id DESC LIMIT 1",
                       (notice_id,)).fetchone()

    b = [f'<h1>{esc(n["notice_id"])} {class_pill(n["classification"])} {status_pill(n["case_status"])}</h1>',
         f'<p class="sub">{esc(n["recalling_firm"])} &middot; initiated {esc(n["initiation_date"])} &middot; '
         f'{esc(n["source"])}, received {esc(n["received_at"])}</p>']
    if n["case_status"] == "Routed to manager":
        reason = conn.execute("SELECT basis FROM audit_log WHERE object_id = ? AND action = 'route_to_manager' "
                              "ORDER BY entry_id DESC LIMIT 1", (notice_id,)).fetchone()
        b.append(f'<div class="banner warn">Routed to the manager: {esc(reason[0] if reason else "")}. '
                 'Read the original text below.</div>')

    b.append('<h2>Notice text</h2><p class="legend"><span><mark class="v">extracted value</mark></span>'
             '<span><mark class="c">value a candidate matched on</mark></span>'
             '<span><mark class="u">text the extractor could not read</mark></span></p>')
    b.append(f'<div class="notice-text">{highlight(n["original_text"], values, matched_ids, unread)}</div>')
    if unread:
        b.append('<p class="small">Unread text, shown as written: '
                 + " &middot; ".join(f'"{esc(u["source_text"])}"' for u in unread) + "</p>")

    b.append("<h2>Candidates</h2>")
    if run is None:
        b.append('<p class="sub">Not matched yet. Run <code>python -m recall_check match</code>.</p>')
    elif not cands:
        b.append(f'<div class="banner warn">{matcher.NO_CANDIDATE} among the {run["lines_compared"]} purchase '
                 'lines on file. Product bought outside these records would not appear here.</div>')
    for c in cands:
        tier_text = {1: "Tier 1: code and lot or date", 2: "Tier 2: code only",
                     3: "Tier 3: described match"}[c["tier"]]
        certainty = '<span class="pill warn">lower certainty</span>' if c["certainty"] == "lower" else ""
        proposed = (f'<span class="pill warn">matcher suggests needs-check-on-shelf</span>'
                    if c["proposed_status"] and c["status"] == "Pending" else "")
        b.append(f'<div class="card" id="c{c["candidate_id"]}"><b>Candidate {c["candidate_id"]}</b> '
                 f'{status_pill(c["status"])} <span class="pill">{tier_text}</span> {certainty} {proposed}')
        b.append('<div class="grid" style="margin-top:8px">'
                 f'<div><span class="k">Store item</span><br>{esc(c["description"])} '
                 f'{esc(c["brand"] or "")} {esc(c["pack_size"] or "")}</div>'
                 f'<div><span class="k">Where stocked</span><br>{esc(c["shelf_location"] or "not on invoice")}</div>'
                 f'<div><span class="k">Invoice</span><br>{esc(c["invoice_number"] or "")} from '
                 f'{esc(c["supplier"] or "")}, {esc(c["invoice_date"])}, qty {c["quantity"]}</div>'
                 f'<div><span class="k">On the invoice</span><br>code {esc(c["product_code"] or "none")}, '
                 f'lot {esc(c["lot_code"] or "none")}, best-by {esc(c["best_by"] or "none")}</div></div>')
        b.append(f'<div class="basis"><span class="k">Why it matched</span><br>{esc(c["basis"])}</div>')
        b.append('<div><span class="k">Matched on, with the notice text each value came from</span><ul class="codes">'
                 + "".join(f'<li>{esc(v["type"])} <b>{esc(v["value"])}</b> from <span class="src">'
                           f'{esc(v["source_text"])}</span> <span class="small">at characters '
                           f'{v["span_start"]}-{v["span_end"]}</span></li>' for v in c["matched"])
                 + "</ul></div>")
        if c["status"] == "Pending":
            b.append(f'<form class="inline" method="post" action="/decide">'
                     f'<input type="hidden" name="candidate_id" value="{c["candidate_id"]}">'
                     f'<input type="hidden" name="back" value="/notice/{quote(notice_id)}#c{c["candidate_id"]}">'
                     f'{name_field(person)}<input type="text" name="note" placeholder="Note (optional)">'
                     '<button class="primary" name="outcome" value="Confirmed">Confirm</button>'
                     '<button name="outcome" value="Needs-check-on-shelf">Needs check on shelf</button>'
                     '<button class="danger" name="outcome" value="Rejected">Reject</button></form>')
        else:
            d = conn.execute("SELECT * FROM decisions WHERE candidate_id = ? ORDER BY decision_id DESC LIMIT 1",
                             (c["candidate_id"],)).fetchone()
            if d:
                b.append(f'<p class="small">Decided {esc(d["outcome"])} by <b>{esc(d["person"])}</b> at '
                         f'{esc(d["decided_at"])}{" &middot; " + esc(d["note"]) if d["note"] else ""}</p>')
        b.append("</div>")

    excl = review.exclusions_for(conn, notice_id)
    if excl:
        b.append('<h2>Compared and set aside</h2><p class="sub">These purchase lines share a code or '
                 'description with the notice, but what the invoice shows falls outside it.</p><table>'
                 "<tr><th>Invoice</th><th>Item</th><th>Reason</th></tr>")
        for e in excl:
            b.append(f'<tr><td>{esc(e["invoice_number"])}<br><span class="small">{esc(e["invoice_date"])}</span></td>'
                     f'<td>{esc(e["description"])}</td><td>{esc(e["reason"])}</td></tr>')
        b.append("</table>")

    lines = worklist.lines(conn, notice_id=notice_id)
    if lines:
        b.append(f'<h2>Worklist lines</h2><p class="sub"><a href="/worklist?notice={quote(notice_id)}">'
                 f'Open the worklist for this notice</a></p>')
    return page(f"{notice_id} review", "".join(b))


def worklist_page(conn, person, notice_id=None):
    lines = worklist.lines(conn, notice_id=notice_id)
    title = f"Shelf-check worklist{' for ' + notice_id if notice_id else ''}"
    b = [f"<h1>{esc(title)}</h1><p class='sub'>Every line comes from a decision a named person made. "
         "Record what the shelf held on each line. This page prints cleanly.</p>"]
    if not lines:
        b.append('<p class="sub">No worklist lines yet. Lines appear when a candidate is confirmed or '
                 'marked needs-check-on-shelf.</p>')
    for w in lines:
        b.append(f'<div class="card"><b>{esc(w["item"])}</b> '
                 f'<span class="pill {"ok" if w["kind"] == "Pull" else "warn"}">{esc(w["kind"])}</span> '
                 f'{status_pill(w["status"])} {class_pill(w["classification"])} '
                 f'<span class="small">line {w["worklist_line_id"]} &middot; '
                 f'<a href="/notice/{quote(w["notice_id"])}">{esc(w["notice_id"])}</a></span>')
        b.append(f'<div class="grid" style="margin-top:8px"><div><span class="k">Where</span><br>'
                 f'<b>{esc(w["location"])}</b></div><div><span class="k">Quantity received</span><br>'
                 f'{esc(w["last_received"])}</div></div>')
        b.append(f'<div class="basis">{esc(w["instruction"])}</div><span class="k">Codes to look for</span>'
                 '<ul class="codes">' + "".join(
                     f'<li>{esc(code["label"])} <b>{esc(code["value"])}</b> <span class="small">notice text: '
                     f'<span class="src">{esc(code["source_text"])}</span></span>'
                     f'{ON_INVOICE if code.get("on_invoice") else ""}</li>'
                     for code in w["look_for"])
                 + "</ul>")
        if w["status"] == "Open":
            b.append(f'<form class="inline" method="post" action="/outcome">'
                     f'<input type="hidden" name="worklist_line_id" value="{w["worklist_line_id"]}">'
                     f'<input type="hidden" name="back" value="/worklist{"?notice=" + quote(notice_id) if notice_id else ""}">'
                     f'{name_field(person)}<input type="text" name="note" placeholder="Note, e.g. units pulled">'
                     '<button class="primary" name="outcome" value="Found and pulled">Found and pulled</button>'
                     '<button name="outcome" value="None found">None found</button>'
                     '<button class="danger" name="outcome" value="Could not verify">Could not verify</button></form>')
        else:
            b.append(f'<p class="small">{esc(w["status"])}, recorded by <b>{esc(w["outcome_by"])}</b> at '
                     f'{esc(w["outcome_at"])}{" &middot; " + esc(w["outcome_note"]) if w["outcome_note"] else ""}</p>')
        b.append("</div>")
    return page(title, "".join(b))


def purchases_page(conn):
    imports = conn.execute("SELECT * FROM purchase_imports ORDER BY import_id").fetchall()
    b = ['<h1>Purchase records</h1><p class="sub">Invoice lines loaded into the purchase record store (E4). '
         'They stay on this computer.</p>']
    for imp in imports:
        b.append(f'<div class="card"><b>{esc(imp["file_name"])}</b> loaded by {esc(imp["imported_by"])} at '
                 f'{esc(imp["imported_at"])}: {imp["accepted"]} lines loaded, {imp["rejected"]} rejected.')
        rejected = conn.execute("SELECT * FROM rejected_rows WHERE import_id = ?", (imp["import_id"],)).fetchall()
        if rejected:
            b.append('<ul>' + "".join(f'<li>Row {r["row_number"]}: {esc(r["reason"])}</li>' for r in rejected) + "</ul>")
        b.append("</div>")
    b.append("<table><tr><th>#</th><th>Invoice</th><th>Item</th><th>Code</th><th>Lot</th><th>Best by</th>"
             "<th>Qty</th><th>Location</th></tr>")
    for p in conn.execute("SELECT * FROM purchase_lines ORDER BY line_id"):
        b.append(f'<tr><td>{p["line_id"]}</td><td>{esc(p["invoice_date"])}<br><span class="small">'
                 f'{esc(p["invoice_number"])}</span></td><td>{esc(p["description"])}<br><span class="small">'
                 f'{esc(p["brand"] or "")} {esc(p["pack_size"] or "")}</span></td><td>{esc(p["product_code"] or "")}</td>'
                 f'<td>{esc(p["lot_code"] or "")}</td><td>{esc(p["best_by"] or "")}</td><td>{p["quantity"]}</td>'
                 f'<td>{esc(p["shelf_location"] or "")}</td></tr>')
    b.append("</table>")
    return page("Purchase records", "".join(b))


def audit_page(conn):
    b = ['<h1>Audit log</h1><p class="sub">Append-only. The database refuses any edit or deletion.</p>'
         "<table><tr><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Object</th><th>Basis</th></tr>"]
    for e in reversed(audit.entries(conn)):
        b.append(f'<tr><td class="small">{esc(e["ts"])}</td><td>{esc(e["actor"])}</td><td>{esc(e["action"])}</td>'
                 f'<td>{esc(e["object_id"])}</td><td class="small">{esc(e["basis"])}</td></tr>')
    b.append("</table>")
    return page("Audit log", "".join(b))


# ---- server -----------------------------------------------------------------

def make_handler(db_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        def _person(self):
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            return unquote(cookie["person"].value) if "person" in cookie else ""

        def _send(self, status, body, extra=None):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status, message, back="/"):
            self._send(status, page("Not recorded", f'<div class="banner warn"><b>Nothing was recorded.</b> '
                                    f'{esc(message)}</div><p><a href="{esc(back)}">Go back</a></p>'))

        def do_GET(self):
            url = urlparse(self.path)
            conn = db.connect(db_path)
            try:
                if url.path == "/":
                    self._send(200, notices_page(conn))
                elif url.path.startswith("/notice/"):
                    body = notice_page(conn, unquote(url.path[len("/notice/"):]), self._person())
                    self._send(200, body) if body else self._error(404, "No such notice.")
                elif url.path == "/worklist":
                    notice = parse_qs(url.query).get("notice", [None])[0]
                    self._send(200, worklist_page(conn, self._person(), notice))
                elif url.path == "/purchases":
                    self._send(200, purchases_page(conn))
                elif url.path == "/audit":
                    self._send(200, audit_page(conn))
                else:
                    self._error(404, "No such page.")
            finally:
                conn.close()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
            back = form.get("back", "/")
            if not back.startswith("/"):
                back = "/"
            conn = db.connect(db_path)
            try:
                if self.path == "/decide":
                    review.decide(conn, int(form.get("candidate_id", 0)), form.get("outcome", ""),
                                  form.get("person", ""), form.get("note", ""))
                elif self.path == "/outcome":
                    worklist.record_outcome(conn, int(form.get("worklist_line_id", 0)), form.get("outcome", ""),
                                            form.get("person", ""), form.get("note", ""))
                else:
                    return self._error(404, "No such action.")
            except (review.DecisionError, worklist.WorklistError, ValueError, sqlite3.Error) as problem:
                conn.rollback()
                return self._error(400, str(problem), back)
            finally:
                conn.close()
            person = quote(form.get("person", "").strip())
            self.send_response(303)
            self.send_header("Location", back)
            self.send_header("Set-Cookie", f"person={person}; Path=/; SameSite=Strict")
            self.end_headers()

    return Handler


def serve(db_path, port=8000):
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(db_path))
    return server
