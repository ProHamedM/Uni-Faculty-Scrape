import csv
import io
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT  # noqa: F401
from unifaculty.logs import close_logging, get_logger, log_event, redact, setup_logging
from unifaculty.models import CSV_COLUMNS, FacultyRecord
from unifaculty.output import merge_records, safe_cell, write_records_csv
from unifaculty.tracing import span


class OutputTests(unittest.TestCase):
    def test_formula_injection_neutralized(self):
        self.assertEqual(safe_cell("=HYPERLINK(\"http://x\")"), "'=HYPERLINK(\"http://x\")")
        self.assertEqual(safe_cell("-0.5"), "-0.5")
        self.assertEqual(safe_cell("Jane"), "Jane")

    def test_merge_by_email_and_by_name(self):
        a = FacultyRecord(university="U", name="Prof. Dr. Jane Example", email="jane@u.edu", funding_status="partial",
                          source_urls=["https://u.edu/a"], field_match_score=0.7)
        b = FacultyRecord(university="U", name="Jane Example", email="jane@u.edu", funding_status="full",
                          source_urls=["https://u.edu/b"], field_match_score=0.9, github=["https://github.com/j"])
        c = FacultyRecord(university="U", name="Jane Example", source_urls=["https://u.edu/c"])
        merged = merge_records([a, b, c])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].funding_status, "full")
        self.assertEqual(merged[0].field_match_score, 0.9)
        self.assertEqual(merged[0].source_urls, ["https://u.edu/a", "https://u.edu/b", "https://u.edu/c"])
        self.assertEqual(merged[0].github, ["https://github.com/j"])

    def test_csv_columns_and_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_records_csv(Path(tmp) / "u" / "x.csv",
                                     [FacultyRecord(university="Universität Wien", name="Jürgen Beispiel")])
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
            self.assertEqual(list(rows[0].keys()), CSV_COLUMNS)
            self.assertEqual(rows[0]["name"], "Jürgen Beispiel")


class LoggingTests(unittest.TestCase):
    def tearDown(self):
        close_logging()
        os.environ.pop("GEMINI_API_KEY", None)

    def test_redaction_patterns(self):
        os.environ["GEMINI_API_KEY"] = "my-secret-gemini-key-123"
        text = redact("key=my-secret-gemini-key-123 auth: Bearer abcdefghijklmnopqrstuvwxyz sk-or-v1-" + "a" * 40)
        self.assertNotIn("my-secret-gemini-key-123", text)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", text)
        self.assertNotIn("a" * 40, text)

    def test_run_folder_jsonl_spans_and_redaction(self):
        os.environ["GEMINI_API_KEY"] = "AIzaSyTESTTESTTESTTESTTESTTESTTEST12345"
        with tempfile.TemporaryDirectory() as tmp:
            ctx = setup_logging(Path(tmp), "unit", verbosity=0, quiet=True, console_stream=io.StringIO())
            log = get_logger("test")
            with span("outer", log, url="https://x.edu") as s:
                with span("inner", log):
                    log_event(log, logging.INFO, "secret.logged", header=f"x-goog-api-key: {os.environ['GEMINI_API_KEY']}")
                s.set(status=200)
            close_logging()
            events = [json.loads(line) for line in (ctx.run_dir / "events.jsonl").read_text().splitlines()]
            names = [e["event"] for e in events]
            self.assertIn("outer.end", names)
            self.assertIn("inner.end", names)
            inner = next(e for e in events if e["event"] == "inner.end")
            outer = next(e for e in events if e["event"] == "outer.end")
            self.assertEqual(inner["parent_span_id"], outer["span_id"])
            self.assertIn("elapsed_ms", outer)
            joined = (ctx.run_dir / "events.jsonl").read_text() + (ctx.run_dir / "run.log").read_text()
            self.assertNotIn("AIzaSyTESTTESTTESTTESTTESTTESTTEST12345", joined)
            self.assertIn("redacted", joined)


if __name__ == "__main__":
    unittest.main()
