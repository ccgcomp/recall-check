"""Tests for the purchase record store, matcher, review decisions, worklist and review screen.

Expected results were worked out by hand from the notice text and the
purchase rows below, never taken from the code under test (report Section 8.3).
"""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from recall_check import audit, db, extract, intake, matcher, purchases, review, web, worklist

HEADER = ",".join(purchases.COLUMNS)


class FakeExtractor:
    version = "fake/p1"

    def __init__(self, items):
        self.items = items

    def run(self, notice_id, original_text):
        return json.dumps({"items": self.items, "uninterpreted": []})


def item(kind, value, source):
    return {"type": kind, "value": value, "source_text": source}


# Notice fixtures. The code fields copy real openFDA records.
COOKIES = ("Carley's Soft Baked Iced Lemon Cookies, 5.34oz Plastic Tray",
           "UPC 740235500110 Lot 091025-111424, 112425-012825",
           [item("upc", "740235500110", "740235500110"),
            item("lot", "091025-111424", "091025-111424"),
            item("lot", "112425-012825", "112425-012825"),
            item("brand", "Carley's", "Carley's"),
            item("product_name", "Soft Baked Iced Lemon Cookies", "Soft Baked Iced Lemon Cookies")])
CREMA = ("Crema GuateLinda (Guatemalan Style Cream) 1 LB",
         "UPC 635349 000390 Best By dates: 07/01/14 thru 12/31/14",
         [item("upc", "635349000390", "635349 000390"),
          item("date_range", "2014-07-01/2014-12-31", "07/01/14 thru 12/31/14")])
MOONCAKE = ("Koi Palace Mini Moon Cake: Single Box - Mini Oolong Tea Paste 1/2 lb.",
            "FG-M1MOT-UW  Best by Nov 1, 2016.",
            [item("brand", "Koi Palace", "Koi Palace"),
             item("product_name", "Mini Moon Cake: Single Box - Mini Oolong Tea Paste",
                  "Mini Moon Cake: Single Box - Mini Oolong Tea Paste"),
             item("other_code", "FG-M1MOT-UW", "FG-M1MOT-UW"),
             item("date", "2016-11-01", "Nov 1, 2016")])
SEASONING = ("Ellsworth Sour Cream + Onion Seasoning, Part #: 180000113, 25.00 lbs",
             "Lot 2026-51127",
             [item("brand", "Ellsworth", "Ellsworth"),
              item("product_name", "Ellsworth Sour Cream + Onion Seasoning", "Ellsworth Sour Cream + Onion Seasoning"),
              item("other_code", "180000113", "180000113"),
              item("lot", "2026-51127", "2026-51127")])
YOGURT = ("Nick The Greek Spicy Yogurt Pail, 42lbs pail, UPC 10075365177756",
          "Lot 4305",
          [item("upc", "10075365177756", "10075365177756"), item("lot", "4305", "4305")])

ROWS = [
    # 1 cookies, lot on notice: tier 1
    "2026-03-02,Bakery Dist,INV-1,740235500110,Soft Baked Iced Lemon Cookies,Carley's,5.34 oz,24,091025-111424,,Aisle 7",
    # 2 cookies, lot not on notice: excluded
    "2026-04-06,Bakery Dist,INV-2,740235500110,Soft Baked Iced Lemon Cookies,Carley's,5.34 oz,18,999999-000000,,Aisle 7",
    # 3 cookies, no lot or date: tier 2
    "2026-04-10,Bakery Dist,INV-3,7402 3550 0110,Soft Baked Iced Lemon Cookies,Carley's,5.34 oz,12,,,Aisle 7",
    # 4 crema, best-by late in the six-month range: tier 1
    "2014-08-11,Oasis,OB-1,635349000390,Crema GuateLinda,Lacteos,1 LB,12,,2014-12-20,Dairy 2",
    # 5 crema, best-by one day after the range ends: excluded
    "2015-01-02,Oasis,OB-2,635349000390,Crema GuateLinda,Lacteos,1 LB,12,,2015-01-01,Dairy 2",
    # 6 moon cake, no product code on invoice: tier 3
    "2016-10-20,Magic Gourmet,MG-1,,Koi Palace Mini Moon Cake Oolong Tea Paste,Koi Palace,1/2 lb,8,,2016-11-01,Bakery 1",
    # 7 different chips, different GTIN, similar words to the seasoning: no candidate
    "2026-02-14,Crisp Valley,CV-1,200000001025,Sour Cream and Onion Potato Chips,Crisp Valley,8 oz,30,,,Aisle 4",
    # 8 yogurt, unit UPC-12 for the case GTIN-14 on the notice: tier 1
    "2026-01-12,Grecian,GD-1,075365177759,Nick The Greek Spicy Yogurt Sauce,Nick The Greek,42 lb,2,4305,,Deli",
    # 9 bad row: quantity missing
    "2026-03-20,Pantry,PW-1,,Baby Spinach,,5 oz,,,,Produce",
    # 10 bad row: date not ISO
    "03/22/2026,Pantry,PW-2,200000001100,Coffee,,12 oz,18,,,Aisle 3",
]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(":memory:")
        for notice_id, (product, codes, items) in {
            "T-COOKIES": COOKIES, "T-CREMA": CREMA, "T-MOONCAKE": MOONCAKE,
            "T-SEASONING": SEASONING, "T-YOGURT": YOGURT,
        }.items():
            text = intake.build_original_text(product, codes)
            intake.register_upload(self.conn, notice_id, text, "Test Manager", classification="Class II")
            extract.extract_notice(self.conn, notice_id, FakeExtractor(items))
        self.csv = self.write_csv(ROWS)
        purchases.import_csv(self.conn, self.csv, "Test Manager")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def write_csv(self, rows, name="purchases.csv"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(HEADER + "\n" + "\n".join(rows) + "\n")
        return path

    def candidate(self, notice_id, row_number):
        """Candidate for the purchase row at this position in ROWS (1-based)."""
        return self.conn.execute(
            """SELECT c.* FROM candidates c JOIN purchase_lines p ON p.line_id = c.line_id
               WHERE c.notice_id = ? AND p.row_number = ?""", (notice_id, row_number + 1)).fetchone()

    def excluded(self, notice_id, row_number):
        return self.conn.execute(
            """SELECT e.* FROM exclusions e JOIN purchase_lines p ON p.line_id = e.line_id
               WHERE e.notice_id = ? AND p.row_number = ?""", (notice_id, row_number + 1)).fetchone()


class PurchaseImportTests(Base):
    def test_bad_rows_listed_and_rest_loaded(self):
        imp = self.conn.execute("SELECT * FROM purchase_imports").fetchone()
        self.assertEqual((imp["accepted"], imp["rejected"]), (8, 2))
        reasons = [r["reason"] for r in self.conn.execute("SELECT reason FROM rejected_rows ORDER BY row_number")]
        self.assertIn("quantity is missing", reasons[0])
        self.assertIn("not a date", reasons[1])

    def test_same_file_cannot_load_twice(self):
        with self.assertRaises(purchases.ImportError_):
            purchases.import_csv(self.conn, self.csv, "Test Manager")

    def test_named_person_required(self):
        other = self.write_csv(ROWS[:1], "other.csv")
        with self.assertRaises(purchases.ImportError_):
            purchases.import_csv(self.conn, other, "  ")

    def test_wrong_header_refused(self):
        path = os.path.join(self.tmp.name, "bad.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("date,item,qty\n2026-01-01,Milk,4\n")
        with self.assertRaises(purchases.ImportError_):
            purchases.import_csv(self.conn, path, "Test Manager")


class CodeRuleTests(unittest.TestCase):
    def test_check_digit(self):
        self.assertTrue(matcher.gtin_check_ok("740235500110"))
        self.assertTrue(matcher.gtin_check_ok("10075365177756"))
        self.assertFalse(matcher.gtin_check_ok("740235500111"))

    def test_unit_upc_and_case_gtin_are_one_product(self):
        self.assertEqual(matcher.product_number("075365177759"), matcher.product_number("10075365177756"))

    def test_spaces_and_dashes_ignored(self):
        self.assertEqual(matcher.product_number("7 41643 05576 6"), matcher.product_number("741643055766"))

    def test_range_is_closed_and_not_its_first_date(self):
        self.assertTrue(matcher.in_range("2014-12-31", "2014-07-01/2014-12-31"))
        self.assertTrue(matcher.in_range("2014-10-15", "2014-07-01/2014-12-31"))
        self.assertFalse(matcher.in_range("2015-01-01", "2014-07-01/2014-12-31"))
        self.assertFalse(matcher.in_range("2014-06-30", "2014-07-01/2014-12-31"))

    def test_month_precision_date(self):
        self.assertTrue(matcher.date_matches("2018-05-17", "2018-05"))
        self.assertFalse(matcher.date_matches("2018-06-01", "2018-05"))


class MatcherTests(Base):
    def setUp(self):
        super().setUp()
        self.results = {r["notice_id"]: r for r in matcher.match_all(self.conn)}

    def test_tier1_code_and_lot(self):
        c = self.candidate("T-COOKIES", 1)
        self.assertEqual((c["tier"], c["certainty"], c["status"]), (1, "standard", "Pending"))
        self.assertIn("lot 091025-111424", c["basis"])

    def test_code_matches_but_lot_outside_notice_is_set_aside_with_reason(self):
        self.assertIsNone(self.candidate("T-COOKIES", 2))
        self.assertIn("not among", self.excluded("T-COOKIES", 2)["reason"])

    def test_tier2_code_only_is_proposed_needs_check_but_stays_pending(self):
        c = self.candidate("T-COOKIES", 3)
        self.assertEqual(c["tier"], 2)
        self.assertEqual(c["proposed_status"], "Needs-check-on-shelf")
        self.assertEqual(c["status"], "Pending")

    def test_six_month_range(self):
        c = self.candidate("T-CREMA", 4)
        self.assertEqual(c["tier"], 1)
        self.assertIn("2014-07-01/2014-12-31", c["basis"])
        self.assertIsNotNone(self.excluded("T-CREMA", 5))

    def test_tier3_no_upc_lower_certainty(self):
        c = self.candidate("T-MOONCAKE", 6)
        self.assertEqual((c["tier"], c["certainty"]), (3, "lower"))
        self.assertIn("Koi Palace", c["basis"])

    def test_case_gtin_matches_unit_upc(self):
        self.assertEqual(self.candidate("T-YOGURT", 8)["tier"], 1)

    def test_no_candidate_recorded_never_clear(self):
        self.assertEqual(self.results["T-SEASONING"]["result"], matcher.NO_CANDIDATE)
        status = self.conn.execute("SELECT case_status FROM notices WHERE notice_id = 'T-SEASONING'").fetchone()[0]
        self.assertEqual(status, matcher.NO_CANDIDATE)
        text = " ".join(e["basis"] for e in audit.entries(self.conn)).lower()
        for word in ("clear", "unaffected", "not affected", "safe"):
            self.assertNotIn(word, text)

    def test_different_gtin_with_similar_words_is_not_a_candidate(self):
        self.assertIsNone(self.candidate("T-SEASONING", 7))
        self.assertIsNone(self.candidate("T-COOKIES", 7))

    def test_every_candidate_points_to_notice_text(self):
        for c in self.conn.execute("SELECT * FROM candidates"):
            ids = json.loads(c["matched_values"])
            self.assertTrue(ids)
            for row in self.conn.execute(
                    f"SELECT * FROM extracted_values WHERE value_id IN ({','.join('?' * len(ids))})", ids):
                original = self.conn.execute("SELECT original_text FROM notices WHERE notice_id = ?",
                                             (row["notice_id"],)).fetchone()[0]
                self.assertEqual(original[row["span_start"]:row["span_end"]], row["source_text"])

    def test_rerun_adds_nothing_new(self):
        before = self.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        matcher.match_all(self.conn)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], before)

    def test_authority_nothing_takes_effect_without_a_person(self):
        """Test 1 of report Section 8.2: a run with no reviewer present changes nothing."""
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM worklist_lines").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 0)
        statuses = {r[0] for r in self.conn.execute("SELECT status FROM candidates")}
        self.assertEqual(statuses, {"Pending"})


class DecisionAndWorklistTests(Base):
    def setUp(self):
        super().setUp()
        matcher.match_all(self.conn)

    def test_decision_requires_a_name(self):
        c = self.candidate("T-COOKIES", 1)
        with self.assertRaises(review.DecisionError):
            review.decide(self.conn, c["candidate_id"], "Confirmed", " ")
        self.assertEqual(self.candidate("T-COOKIES", 1)["status"], "Pending")

    def test_decision_must_be_a_known_outcome(self):
        with self.assertRaises(review.DecisionError):
            review.decide(self.conn, self.candidate("T-COOKIES", 1)["candidate_id"], "Clear", "Ana")

    def test_decided_once(self):
        cid = self.candidate("T-COOKIES", 1)["candidate_id"]
        review.decide(self.conn, cid, "Rejected", "Ana")
        with self.assertRaises(review.DecisionError):
            review.decide(self.conn, cid, "Confirmed", "Ana")

    def test_confirmed_line_has_all_four_fr8_elements(self):
        cid = self.candidate("T-COOKIES", 1)["candidate_id"]
        line_id = review.decide(self.conn, cid, "Confirmed", "Ana")
        w = worklist.lines(self.conn, notice_id="T-COOKIES")[0]
        self.assertEqual(w["worklist_line_id"], line_id)
        self.assertEqual(w["kind"], "Pull")
        self.assertIn("Soft Baked Iced Lemon Cookies", w["item"])
        self.assertEqual(w["location"], "Aisle 7")
        self.assertIn("This delivery: 24 on 2026-03-02", w["last_received"])
        self.assertIn("Most recent delivery of this item: 12 on 2026-04-10", w["last_received"])
        values = {code["raw"] for code in w["look_for"]}
        self.assertEqual(values, {"740235500110", "091025-111424", "112425-012825"})
        on_invoice = {code["raw"] for code in w["look_for"] if code["on_invoice"]}
        self.assertEqual(on_invoice, {"740235500110", "091025-111424"})
        status = self.conn.execute("SELECT case_status FROM notices WHERE notice_id = 'T-COOKIES'").fetchone()[0]
        self.assertEqual(status, "Worklist issued")

    def test_needs_check_on_shelf_reaches_the_worklist(self):
        """The Assignment 2 gap: needs-check-on-shelf must have a path to the shelf."""
        cid = self.candidate("T-COOKIES", 3)["candidate_id"]
        review.decide(self.conn, cid, "Needs-check-on-shelf", "Ana")
        w = worklist.lines(self.conn, notice_id="T-COOKIES")[0]
        self.assertEqual(w["kind"], "Check on shelf")
        self.assertIn("Read the lot and date on every unit", w["instruction"])
        self.assertTrue(any(code["label"] == "Lot" for code in w["look_for"]))

    def test_rejected_gets_no_line(self):
        review.decide(self.conn, self.candidate("T-COOKIES", 1)["candidate_id"], "Rejected", "Ana")
        self.assertEqual(worklist.lines(self.conn, notice_id="T-COOKIES"), [])

    def test_range_shown_as_full_range_on_the_line(self):
        review.decide(self.conn, self.candidate("T-CREMA", 4)["candidate_id"], "Confirmed", "Ana")
        codes = [code["value"] for code in worklist.lines(self.conn, notice_id="T-CREMA")[0]["look_for"]]
        self.assertIn("2014-07-01 through 2014-12-31", codes)

    def test_outcome_requires_name_and_known_value(self):
        line_id = review.decide(self.conn, self.candidate("T-COOKIES", 1)["candidate_id"], "Confirmed", "Ana")
        with self.assertRaises(worklist.WorklistError):
            worklist.record_outcome(self.conn, line_id, "None found", "")
        with self.assertRaises(worklist.WorklistError):
            worklist.record_outcome(self.conn, line_id, "Clear", "Ben")
        worklist.record_outcome(self.conn, line_id, "Found and pulled", "Ben", "6 units")
        with self.assertRaises(worklist.WorklistError):
            worklist.record_outcome(self.conn, line_id, "None found", "Ben")

    def test_could_not_verify_routes_to_manager(self):
        line_id = review.decide(self.conn, self.candidate("T-COOKIES", 3)["candidate_id"],
                                "Needs-check-on-shelf", "Ana")
        worklist.record_outcome(self.conn, line_id, "Could not verify", "Ben", "label torn")
        actions = [(e["actor"], e["action"]) for e in audit.entries(self.conn, "T-COOKIES")]
        self.assertIn(("E7 coordinator", "route_to_manager"), actions)

    def test_audit_names_each_step(self):
        line_id = review.decide(self.conn, self.candidate("T-COOKIES", 1)["candidate_id"], "Confirmed", "Ana")
        worklist.record_outcome(self.conn, line_id, "None found", "Ben")
        actions = [e["action"] for e in audit.entries(self.conn)]
        for expected in ("register_notice", "extract", "import_purchases", "propose_candidate",
                         "match", "decide", "issue_worklist_line", "record_outcome"):
            self.assertIn(expected, actions)
        people = {e["actor"] for e in audit.entries(self.conn) if e["action"] in ("decide", "record_outcome")}
        self.assertEqual(people, {"Ana", "Ben"})


class ReviewScreenTests(Base):
    def setUp(self):
        super().setUp()
        self.conn.close()
        self.path = os.path.join(self.tmp.name, "screen.db")
        self.conn = db.connect(self.path)
        text = intake.build_original_text(*COOKIES[:2])
        intake.register_upload(self.conn, "T-COOKIES", text, "Test Manager", classification="Class I")
        extract.extract_notice(self.conn, "T-COOKIES", FakeExtractor(COOKIES[2]))
        purchases.import_csv(self.conn, self.csv, "Test Manager")
        matcher.match_all(self.conn)
        self.server = web.serve(self.path, port=0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as response:
            return response.read().decode("utf-8")

    def post(self, path, fields):
        data = urllib.parse.urlencode(fields).encode()
        try:
            with urllib.request.urlopen(self.base + path, data=data) as response:
                return response.status
        except urllib.error.HTTPError as problem:
            return problem.code

    def test_pages_load(self):
        for path in ("/", "/worklist", "/purchases", "/audit"):
            self.assertIn("Recall shelf check", self.get(path))

    def test_notice_page_shows_item_matched_value_and_source_text(self):
        """FR-6: store item, matched identifier and notice span on one screen."""
        body = self.get("/notice/T-COOKIES")
        self.assertIn("Soft Baked Iced Lemon Cookies", body)
        self.assertIn("<b>740235500110</b>", body)
        self.assertIn('<mark class="c">740235500110</mark>', body)
        self.assertIn("Compared and set aside", body)

    def test_decision_without_name_is_refused(self):
        cid = self.candidate("T-COOKIES", 1)["candidate_id"]
        status = self.post("/decide", {"candidate_id": cid, "outcome": "Confirmed", "person": ""})
        self.assertEqual(status, 400)
        self.assertEqual(self.candidate("T-COOKIES", 1)["status"], "Pending")

    def test_decision_with_name_is_recorded_and_line_appears(self):
        cid = self.candidate("T-COOKIES", 1)["candidate_id"]
        status = self.post("/decide", {"candidate_id": cid, "outcome": "Confirmed", "person": "Ana",
                                       "back": "/worklist"})
        self.assertEqual(status, 200)  # followed the redirect to the worklist
        self.assertEqual(self.candidate("T-COOKIES", 1)["status"], "Confirmed")
        self.assertIn("Aisle 7", self.get("/worklist"))

    def test_page_text_is_escaped(self):
        self.conn.execute("UPDATE purchase_lines SET description = '<script>x</script>' WHERE row_number = 2")
        self.conn.commit()
        body = self.get("/notice/T-COOKIES")
        self.assertNotIn("<script>x</script>", body)
        self.assertIn("&lt;script&gt;", body)


if __name__ == "__main__":
    unittest.main()
