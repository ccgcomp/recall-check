"""Tests for notice intake, storage, extraction and the audit log.

The notice fixtures copy code fields from real openFDA records quoted in the
Solution Design Report. Claude's answers are simulated so the tests run with
no Claude account and no network.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from recall_check import audit, db, extract, intake

RECORD_NO_UPC = {
    "recall_number": "F-0865-2017",
    "recalling_firm": "Koi Palace",
    "classification": "Class II",
    "status": "Terminated",
    "recall_initiation_date": "20161201",
    "product_description": "Koi Palace Mini Moon Cake: Single Box - Mini Oolong Tea Paste",
    "code_info": "FG-M1MOT-UW  Best by Nov 1, 2016.",
}
RECORD_RANGE = {
    "recall_number": "TEST-RANGE",
    "recalling_firm": "Test Firm",
    "classification": "Class I",
    "status": "Ongoing",
    "recall_initiation_date": "20140801",
    "product_description": "Test product",
    "code_info": "UPC 635349 000390  Best By dates: 07/01/14 thru 12/31/14",
}


class FakeExtractor:
    """Stands in for Claude and returns a fixed answer."""
    version = "fake/p1"

    def __init__(self, answer):
        self.answer = answer

    def run(self, notice_id, original_text):
        return self.answer


class UnavailableExtractor:
    version = "fake/p1"

    def run(self, notice_id, original_text):
        raise extract.ExtractionUnavailable("Claude is offline")


def answer(items, unread=()):
    return "```json\n" + json.dumps({"items": items, "uninterpreted": list(unread)}) + "\n```"


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)

    def test_registers_all_eight_elements(self):
        intake.register_openfda(self.conn, RECORD_NO_UPC)
        row = self.conn.execute("SELECT * FROM notices").fetchone()
        for field in ("source", "channel", "received_at", "recalling_firm", "classification",
                      "status", "initiation_date", "original_text"):
            self.assertTrue(row[field], field)
        self.assertIn("FG-M1MOT-UW", row["original_text"])

    def test_notice_without_text_is_refused(self):
        empty = dict(RECORD_NO_UPC, product_description="", code_info="")
        with self.assertRaises(intake.IntakeError):
            intake.register_openfda(self.conn, empty)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0], 0)

    def test_registering_twice_changes_nothing(self):
        intake.register_openfda(self.conn, RECORD_NO_UPC)
        intake.register_openfda(self.conn, RECORD_NO_UPC)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0], 1)

    def test_upload_must_name_a_person(self):
        with self.assertRaises(intake.IntakeError):
            intake.register_upload(self.conn, "U-1", "Lot 123", uploaded_by="")


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)
        intake.register_openfda(self.conn, RECORD_NO_UPC)

    def test_registration_is_logged(self):
        actions = [e["action"] for e in audit.entries(self.conn, "F-0865-2017")]
        self.assertEqual(actions, ["register_notice"])

    def test_log_cannot_be_edited_or_deleted(self):
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("UPDATE audit_log SET basis = 'changed'")
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("DELETE FROM audit_log")


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)
        intake.register_openfda(self.conn, RECORD_NO_UPC)
        intake.register_openfda(self.conn, RECORD_RANGE)

    def test_code_fence_is_removed(self):
        data = extract.parse_response('```json\n{"items": []}\n```')
        self.assertEqual(data["items"], [])

    def test_value_without_source_text_is_dropped(self):
        fake = FakeExtractor(answer([
            {"type": "other_code", "value": "FG-M1MOT-UW", "source_text": "FG-M1MOT-UW"},
            {"type": "lot", "value": "999", "source_text": "Lot 999"},
        ]))
        result = extract.extract_notice(self.conn, "F-0865-2017", fake)
        self.assertEqual((result["kept"], result["dropped"]), (1, 1))
        for value in extract.values_for(self.conn, "F-0865-2017"):
            original = self.conn.execute("SELECT original_text FROM notices").fetchone()[0]
            self.assertEqual(original[value["span_start"]:value["span_end"]], value["source_text"])

    def test_date_range_stays_a_closed_range(self):
        fake = FakeExtractor(answer([
            {"type": "upc", "value": "635349000390", "source_text": "635349 000390"},
            {"type": "date_range", "value": "2014-07-01/2014-12-31", "source_text": "07/01/14 thru 12/31/14"},
        ]))
        extract.extract_notice(self.conn, "TEST-RANGE", fake)
        ranges = [v for v in extract.values_for(self.conn, "TEST-RANGE") if v["type"] == "date_range"]
        self.assertEqual(ranges[0]["value"], "2014-07-01/2014-12-31")

    def test_notice_without_upc_is_marked_lower_certainty(self):
        fake = FakeExtractor(answer([
            {"type": "date", "value": "2016-11-01", "source_text": "Best by Nov 1, 2016"},
            {"type": "other_code", "value": "FG-M1MOT-UW", "source_text": "FG-M1MOT-UW"},
        ]))
        extract.extract_notice(self.conn, "F-0865-2017", fake)
        self.assertEqual({v["certainty"] for v in extract.values_for(self.conn, "F-0865-2017")}, {"standard"})
        fake_brand_only = FakeExtractor(answer([
            {"type": "brand", "value": "Test", "source_text": "Test"},
            {"type": "date_range", "value": "2014-07-01/2014-12-31", "source_text": "07/01/14 thru 12/31/14"},
        ]))
        extract.extract_notice(self.conn, "TEST-RANGE", fake_brand_only)
        self.assertEqual({v["certainty"] for v in extract.values_for(self.conn, "TEST-RANGE")}, {"lower"})

    def test_largely_uninterpreted_notice_goes_to_manager(self):
        fake = FakeExtractor(answer([{"type": "date", "value": "2016-11-01", "source_text": "2016"}],
                                    unread=["FG-M1MOT-UW  Best by Nov 1, 2016."]))
        result = extract.extract_notice(self.conn, "F-0865-2017", fake)
        self.assertEqual(result["status"], "Routed to manager")
        self.assertGreater(result["uninterpreted_share"], extract.LARGELY_UNINTERPRETED)

    def test_extraction_failure_routes_notice_and_keeps_it(self):
        result = extract.extract_notice(self.conn, "F-0865-2017", UnavailableExtractor())
        self.assertEqual(result["status"], "Routed to manager")
        row = self.conn.execute("SELECT original_text FROM notices WHERE notice_id='F-0865-2017'").fetchone()
        self.assertIn("FG-M1MOT-UW", row[0])
        self.assertIn("route_to_manager", [e["action"] for e in audit.entries(self.conn, "F-0865-2017")])

    def test_claude_missing_means_unavailable(self):
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(extract.ExtractionUnavailable):
                extract.ClaudeExtractor().run("F-0865-2017", "text")

    def test_replay_reads_saved_answer(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = {"extractor_version": "claude-code-cli/p1",
                     "raw_response": answer([{"type": "other_code", "value": "FG-M1MOT-UW",
                                              "source_text": "FG-M1MOT-UW"}])}
            with open(os.path.join(folder, "F-0865-2017.json"), "w", encoding="utf-8") as handle:
                json.dump(saved, handle)
            result = extract.extract_notice(self.conn, "F-0865-2017", extract.ReplayExtractor(folder))
            self.assertEqual(result["kept"], 1)
            self.assertTrue(extract.values_for(self.conn, "F-0865-2017")[0]["extractor_version"].startswith("replay of"))


class CoverageTests(unittest.TestCase):
    """Regression tests from the first run on real notices, 2026-11-12."""

    def test_code_found_in_product_line_points_at_code_line(self):
        # H-0417-2026 repeats its codes in the product description. The first
        # run located them there and counted the whole code line as unread.
        original = intake.build_original_text(
            "SOMA KITCHEN ASAFOETIDA Lot : NATL/ASFTP/G/24 UPC : 4 973993 173586",
            "Lot : NATL/ASFTP/G/24 UPC : 4 973993 173586")
        kept, _, _ = extract.validate(original, {"items": [
            {"type": "lot", "value": "NATL/ASFTP/G/24", "source_text": "NATL/ASFTP/G/24"},
            {"type": "upc", "value": "4973993173586", "source_text": "4 973993 173586"},
        ]})
        start, end = extract.code_section(original)
        self.assertTrue(all(start <= v["span_start"] < end for v in kept))
        self.assertEqual(extract.uninterpreted_share(original, kept), 0.0)

    def test_repeated_product_name_counts_as_read(self):
        # F-0399-2025 repeats the product name on every line of its code text.
        original = intake.build_original_text(
            "Cucumber Select 5#", "LOT CUCUMBER SELECT 5# X1 CUCUMBER SELECT 5# X2")
        kept, _, _ = extract.validate(original, {"items": [
            {"type": "product_name", "value": "Cucumber Select", "source_text": "Cucumber Select"},
            {"type": "pack_size", "value": "5#", "source_text": "5#"},
            {"type": "lot", "value": "X1", "source_text": "X1"},
            {"type": "lot", "value": "X2", "source_text": "X2"},
        ]})
        self.assertEqual(extract.uninterpreted_share(original, kept), 0.0)

    def test_label_words_do_not_count_as_unread(self):
        original = intake.build_original_text("Product", "UPC No. 632687615989; Lot No. 30661601")
        kept, _, _ = extract.validate(original, {"items": [
            {"type": "upc", "value": "632687615989", "source_text": "632687615989"},
            {"type": "lot", "value": "30661601", "source_text": "30661601"},
        ]})
        self.assertEqual(extract.uninterpreted_share(original, kept), 0.0)

    def test_unread_code_still_counts(self):
        original = intake.build_original_text("Product", "Lot 4305 and code QZX99")
        kept, _, _ = extract.validate(original, {"items": [
            {"type": "lot", "value": "4305", "source_text": "4305"},
        ]})
        self.assertGreater(extract.uninterpreted_share(original, kept), 0.0)


if __name__ == "__main__":
    unittest.main()
