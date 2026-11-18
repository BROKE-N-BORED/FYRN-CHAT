"""Theme tests: every hex colour lives in theme.py, the
stylesheet builds with no leftover placeholders, and the
text/background pairs read clearly."""

import re
import unittest
from pathlib import Path

from fyrnchattest.theme import (DARK, build_stylesheet,
                                contrast_ratio, fit_badges,
                                verdict_badges)

ROOT = Path(__file__).resolve().parent.parent
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")


class ThemeTests(unittest.TestCase):
    def test_no_hex_literals_outside_theme(self):
        offenders = []
        for path in sorted((ROOT / "fyrnchattest").rglob("*.py")):
            if path.name == "theme.py":
                continue
            for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(),
                    start=1):
                for match in HEX_RE.finditer(line):
                    offenders.append("%s:%d: %s" % (
                        path.name, number, match.group(0)))
        self.assertEqual(offenders, [])

    def test_stylesheet_builds_clean(self):
        sheet = build_stylesheet(DARK)
        self.assertNotIn("$", sheet)
        self.assertIn(DARK["bg"], sheet)
        self.assertIn(DARK["accent"], sheet)
        self.assertIn("QPushButton", sheet)
        self.assertIn("QTableWidget", sheet)

    def test_text_pairs_read_clearly(self):
        # Body text on the background, and muted text too.
        self.assertGreaterEqual(
            contrast_ratio(DARK["text"], DARK["bg"]), 4.5)
        self.assertGreaterEqual(
            contrast_ratio(DARK["text_dim"], DARK["bg"]), 4.5)
        self.assertGreaterEqual(
            contrast_ratio(DARK["text_muted"], DARK["bg"]), 4.5)
        # Text inside the deep panels (chat view, list).
        self.assertGreaterEqual(
            contrast_ratio(DARK["text"], DARK["bg_deep"]), 4.5)

    def test_badge_text_reads_on_badges(self):
        for _label, color in list(fit_badges(DARK).values()) \
                + list(verdict_badges(DARK).values()):
            self.assertGreaterEqual(
                contrast_ratio(DARK["badge_text"], color), 4.5,
                "white text on %s is too dim" % color)

    def test_accent_visible_on_background(self):
        self.assertGreaterEqual(
            contrast_ratio(DARK["accent"], DARK["bg"]), 3.0)


if __name__ == "__main__":
    unittest.main()
