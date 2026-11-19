"""Command line for the recall shelf-check prototype.

Run from the repository root, for example:
    python -m recall_check fetch-samples
    python -m recall_check register
    python -m recall_check extract
    python -m recall_check show F-0865-2017
    python -m recall_check import-purchases --by "Your Name"
    python -m recall_check match
    python -m recall_check review
"""

import argparse
import os
import sys

from . import audit, db, extract, intake, matcher, purchases, review, web, worklist

NOTICE_FOLDER = os.path.join("data", "notices")
EXTRACTION_FOLDER = os.path.join("data", "extractions")
SAMPLE_PURCHASES = os.path.join("data", "store", "purchases_sample.csv")

# Sample notices: three whose code fields are quoted in the report, plus recent ones.
SAMPLE_SEARCHES = [
    ('code_info:"FG-M1MOT-UW"', 1),
    ('code_info:"632687615989"', 1),
    ('code_info:"635349 000390"', 1),
    ("report_date:[20250101 TO 20261231]", 20),
]


def cmd_fetch_samples(args):
    total = 0
    for search, limit in SAMPLE_SEARCHES:
        records = intake.fetch_openfda(search, limit)
        total += len(intake.save_records(records, NOTICE_FOLDER))
    print(f"Saved {total} notices to {NOTICE_FOLDER}")


def cmd_fetch(args):
    saved = intake.save_records(intake.fetch_openfda(args.search, args.limit), NOTICE_FOLDER)
    print(f"Saved {len(saved)} notices to {NOTICE_FOLDER}")


def cmd_register(args):
    conn = db.connect(args.db)
    count = 0
    for record in intake.load_records(args.folder):
        try:
            intake.register_openfda(conn, record)
            count += 1
        except intake.IntakeError as problem:
            print(f"Not registered: {problem}")
    print(f"{count} notices registered")


def cmd_upload(args):
    conn = db.connect(args.db)
    with open(args.file, encoding="utf-8") as handle:
        text = handle.read()
    intake.register_upload(conn, args.id, text, args.by, recalling_firm=args.firm,
                           classification=args.classification, expands_notice_id=args.expands)
    print(f"Registered {args.id}")


def cmd_extract(args):
    conn = db.connect(args.db)
    if args.mode == "claude":
        extractor = extract.ClaudeExtractor()
    else:
        extractor = extract.ReplayExtractor(EXTRACTION_FOLDER)
    record_to = EXTRACTION_FOLDER if args.record else None
    ids = [args.id] if args.id else [r["notice_id"] for r in
                                     conn.execute("SELECT notice_id FROM notices ORDER BY notice_id")]
    for notice_id in ids:
        result = extract.extract_notice(conn, notice_id, extractor, record_to)
        if result.get("skipped"):
            print(f"{notice_id}: already extracted")
        elif result["status"] == "Extracted":
            print(f"{notice_id}: {result['kept']} values, {result['uninterpreted_share']:.0%} unread")
        else:
            print(f"{notice_id}: ROUTED TO MANAGER ({result['reason']})")


def cmd_show(args):
    conn = db.connect(args.db)
    row = conn.execute("SELECT * FROM notices WHERE notice_id = ?", (args.id,)).fetchone()
    if row is None:
        sys.exit(f"{args.id} is not registered")
    print(f"{row['notice_id']}  {row['classification']}  {row['recalling_firm']}  [{row['case_status']}]")
    print(row["original_text"])
    print()
    for value in extract.values_for(conn, args.id):
        print(f"  {value['type']:<12} {value['value']:<28} from \"{value['source_text']}\" "
              f"at {value['span_start']}-{value['span_end']}")
    for unread in conn.execute("SELECT source_text FROM uninterpreted_text WHERE notice_id = ?", (args.id,)):
        print(f"  UNREAD       \"{unread['source_text']}\"")


def cmd_import_purchases(args):
    conn = db.connect(args.db)
    try:
        _, accepted, rejected = purchases.import_csv(conn, args.file, args.by)
    except purchases.ImportError_ as problem:
        sys.exit(f"Not loaded: {problem}")
    print(f"{accepted} purchase lines loaded, {len(rejected)} rejected")
    for row in rejected:
        print(f"  row {row['row_number']}: {row['reason']}")


def cmd_match(args):
    conn = db.connect(args.db)
    results = [matcher.match_notice(conn, args.id)] if args.id else matcher.match_all(conn)
    for r in results:
        if "skipped" in r:
            print(f"{r['notice_id']}: not matched, {r['skipped']}")
        else:
            print(f"{r['notice_id']}: {r['result']}")


def cmd_candidates(args):
    conn = db.connect(args.db)
    ids = [args.id] if args.id else [r[0] for r in conn.execute(
        "SELECT DISTINCT notice_id FROM candidates ORDER BY notice_id")]
    for notice_id in ids:
        for c in review.candidates_for(conn, notice_id):
            print(f"[{c['candidate_id']}] {notice_id} tier {c['tier']} {c['status']:<20} "
                  f"{c['description']} ({c['shelf_location'] or 'no location'})")
            print(f"      {c['basis']}")


def cmd_decide(args):
    conn = db.connect(args.db)
    try:
        line_id = review.decide(conn, args.candidate, args.outcome, args.by, args.note)
    except review.DecisionError as problem:
        sys.exit(f"Not recorded: {problem}")
    print(f"Candidate {args.candidate}: {args.outcome}, recorded for {args.by}"
          + (f"; worklist line {line_id} issued" if line_id else ""))


def cmd_worklist(args):
    conn = db.connect(args.db)
    for w in worklist.lines(conn, notice_id=args.id):
        print(f"[{w['worklist_line_id']}] {w['kind'].upper()}  {w['item']}  at {w['location']}  [{w['status']}]")
        print(f"      {w['instruction']}")
        print(f"      {w['last_received']}")
        for code in w["look_for"]:
            print(f"      {code['label']}: {code['value']}" + ("   <- on the invoice" if code.get("on_invoice") else ""))


def cmd_outcome(args):
    conn = db.connect(args.db)
    try:
        worklist.record_outcome(conn, args.line, args.outcome, args.by, args.note)
    except worklist.WorklistError as problem:
        sys.exit(f"Not recorded: {problem}")
    print(f"Worklist line {args.line}: {args.outcome}, recorded for {args.by}")


def cmd_review(args):
    server = web.serve(args.db, args.port)
    print(f"Review workbench running at http://127.0.0.1:{args.port}  (press Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped")


def cmd_audit(args):
    conn = db.connect(args.db)
    for entry in audit.entries(conn, args.id):
        print(f"{entry['ts']}  {entry['actor']:<34} {entry['action']:<18} {entry['object_id']:<14} {entry['basis']}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="recall_check", description="Recall shelf-check prototype")
    parser.add_argument("--db", default=db.DEFAULT_DB_PATH, help="database file")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("fetch-samples", help="download the sample notices from openFDA").set_defaults(func=cmd_fetch_samples)

    p = sub.add_parser("fetch", help="download notices matching an openFDA search")
    p.add_argument("--search", required=True)
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("register", help="register saved notices (FR-1)")
    p.add_argument("--folder", default=NOTICE_FOLDER)
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("upload", help="register a notice from a text file")
    p.add_argument("--id", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--by", required=True, help="name of the person uploading")
    p.add_argument("--firm", default=intake.NOT_STATED)
    p.add_argument("--classification", default=intake.NOT_STATED)
    p.add_argument("--expands", help="notice ID this one expands or corrects")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("extract", help="read codes from registered notices (FR-2, FR-3)")
    p.add_argument("--mode", choices=["replay", "claude"], default="replay",
                   help="replay saved Claude answers (default, no account needed) or call Claude")
    p.add_argument("--record", action="store_true", help="save Claude's answers for replay")
    p.add_argument("--id", help="one notice ID; default is all")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("show", help="show a notice and its extracted values")
    p.add_argument("id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("import-purchases", help="load the store's purchase file (E4)")
    p.add_argument("--file", default=SAMPLE_PURCHASES)
    p.add_argument("--by", required=True, help="name of the person loading the file")
    p.set_defaults(func=cmd_import_purchases)

    p = sub.add_parser("match", help="match notices against purchase records (FR-4)")
    p.add_argument("--id", help="one notice ID; default is all")
    p.set_defaults(func=cmd_match)

    p = sub.add_parser("candidates", help="list candidate matches")
    p.add_argument("id", nargs="?")
    p.set_defaults(func=cmd_candidates)

    p = sub.add_parser("decide", help="record a decision on a candidate (FR-7)")
    p.add_argument("candidate", type=int)
    p.add_argument("outcome", choices=review.OUTCOMES)
    p.add_argument("--by", required=True, help="name of the person deciding")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("worklist", help="print the shelf-check worklist (FR-8)")
    p.add_argument("id", nargs="?")
    p.set_defaults(func=cmd_worklist)

    p = sub.add_parser("outcome", help="record what the shelf held for a worklist line")
    p.add_argument("line", type=int)
    p.add_argument("outcome", choices=worklist.OUTCOMES)
    p.add_argument("--by", required=True, help="name of the person who checked")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_outcome)

    p = sub.add_parser("review", help="open the review workbench in a browser (E8)")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("audit", help="print the audit log")
    p.add_argument("id", nargs="?")
    p.set_defaults(func=cmd_audit)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
