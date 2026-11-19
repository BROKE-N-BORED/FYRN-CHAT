"""Report tests: the Markdown and JSON reports carry the run,
and write_report puts both files in the run folder."""

import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from helpers import ensure_fake_fyrn

ensure_fake_fyrn()

from fyrnchattest.reports import (format_report_json,  # noqa: E402
                                  format_report_md, new_run_dir,
                                  reports_dir, write_report)


def _results():
    return [
        {"name": "a.gguf", "path": "/m/a.gguf", "source": "folder",
         "size": 2048, "load_ok": True, "load_time_s": 1.2,
         "load_error": None,
         "prompts": [
             {"prompt": "Say hello in one sentence.",
              "replied": True, "ttft_s": 0.4,
              "tokens_per_sec": 12.5, "tokens": 8,
              "elapsed_s": 0.6, "excerpt": "Hello there.",
              "error": None},
             {"prompt": "Write a Python function...",
              "replied": True, "ttft_s": 0.5,
              "tokens_per_sec": 11.0, "tokens": 20,
              "elapsed_s": 1.8, "excerpt": "def add(a, b):",
              "error": None}],
         "verdict": "pass", "error": None},
        {"name": "b.gguf", "path": "/m/b.gguf", "source": "ollama",
         "size": 4096, "load_ok": False, "load_time_s": None,
         "load_error": "The model could not be loaded (boom).",
         "prompts": [], "verdict": "fail",
         "error": "The model could not be loaded (boom)."},
        {"name": "c.gguf", "path": "/m/c.gguf", "source": "folder",
         "size": 1024, "load_ok": False, "load_time_s": None,
         "load_error": None, "prompts": [],
         "verdict": "not tested",
         "error": "Not tested: the run stopped before this model."},
    ]


_META = {"when": "2026-11-17 07:50:00",
         "fyrn_dir": "/home/joshua/fyrn",
         "fyrn_version": "0.5"}


class ReportFormatTests(unittest.TestCase):
    def test_markdown_has_summary_table_and_details(self):
        text = format_report_md(_results(), _META)
        self.assertIn("3 models tested: 1 passed, 1 failed, "
                      "1 not tested.", text)
        self.assertIn("| a.gguf | ok (1.2 s) | replied | replied |",
                      text)
        self.assertIn("| b.gguf | failed |", text)
        self.assertIn("Fyrn version: 0.5", text)
        self.assertIn("Hello there.", text)
        self.assertIn("def add(a, b):", text)
        self.assertIn("Load failed: The model could not be "
                      "loaded (boom).", text)

    def test_not_tested_reason_shows_in_markdown(self):
        results = _results() + [
            {"name": "embed.gguf", "path": "/m/embed.gguf",
             "source": "ollama", "size": 1024,
             "load_ok": False, "load_time_s": None,
             "load_error": None, "prompts": [],
             "verdict": "not tested",
             "error": "Embedding only: this model can't write "
                      "replies, so the smoke test doesn't "
                      "apply."},
        ]
        text = format_report_md(results, _META)
        self.assertIn("4 models tested: 1 passed, 1 failed, "
                      "2 not tested.", text)
        # No load was attempted for either not-tested model,
        # so the Load cell is a dash, not "failed".
        self.assertIn("| embed.gguf | - |", text)
        self.assertIn("| c.gguf | - |", text)
        self.assertIn("Embedding only: this model can't write "
                      "replies, so the smoke test doesn't "
                      "apply.", text)
        self.assertIn("Not tested: the run stopped before this "
                      "model.", text)

    def test_json_round_trips(self):
        text = format_report_json(_results(), _META)
        payload = json.loads(text)
        self.assertEqual(payload["app"], "fyrn-chat-test")
        self.assertEqual(payload["summary"],
                         {"tested": 3, "passed": 1, "failed": 1,
                          "not_tested": 1})
        self.assertEqual(len(payload["prompts"]), 2)
        self.assertEqual(payload["results"][0]["verdict"], "pass")
        self.assertEqual(payload["results"][1]["load_error"],
                         "The model could not be loaded (boom).")


class ReportFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fyrnct-reports-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_run_dir_sorts_by_time(self):
        first = Path(new_run_dir(
            self.tmp, datetime(2026, 11, 17, 7, 50, 0)))
        second = Path(new_run_dir(
            self.tmp, datetime(2026, 11, 17, 8, 5, 30)))
        self.assertEqual(first.name, "2026-11-17_07-50-00")
        self.assertEqual(second.name, "2026-11-17_08-05-30")
        self.assertTrue(first.is_dir())
        self.assertTrue(second.is_dir())
        self.assertLess(str(first), str(second))
        self.assertEqual(reports_dir(self.tmp),
                         str(self.tmp / "reports"))

    def test_write_report_writes_both_files(self):
        run_dir = new_run_dir(self.tmp)
        md_path, json_path = write_report(_results(), run_dir,
                                          _META)
        self.assertTrue(Path(md_path).is_file())
        self.assertTrue(Path(json_path).is_file())
        self.assertEqual(Path(md_path).name, "report.md")
        self.assertEqual(Path(json_path).name, "report.json")
        self.assertIn("smoke test",
                      Path(md_path).read_text(encoding="utf-8"))
        payload = json.loads(
            Path(json_path).read_text(encoding="utf-8"))
        self.assertEqual(payload["summary"]["passed"], 1)


if __name__ == "__main__":
    unittest.main()
