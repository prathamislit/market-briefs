import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import build_briefs as brief


WARNING = "<strong>Not financial advice.</strong>"
DISCLAIMER = (
    "<p>Personal academic research portfolio. Not investment advice. "
    "Not a solicitation. No services offered. Frameworks are informational "
    "research outputs.</p>"
)
PAGE_NAMES = {
    "index.html",
    "archives.html",
    "markets.html",
    "india.html",
    "macro.html",
    "geopolitics.html",
    "earnings.html",
    "etfs.html",
    "watchlist.html",
    "sources.html",
}


class PageDisclaimerTests(unittest.TestCase):
    def test_all_generated_pages_include_exact_disclaimer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            docs = Path(temp_dir)
            reports = docs / "reports"
            reports.mkdir()
            (reports / "manifest.json").write_text("[]")

            with patch.object(brief, "DOCS", docs), patch.object(brief, "REPORTS", reports):
                brief.build_dashboard()

            pages = {path.name: path for path in docs.rglob("*.html")}
            self.assertEqual(set(pages), PAGE_NAMES)
            for name in sorted(PAGE_NAMES):
                with self.subTest(page=name):
                    html = pages[name].read_text()
                    self.assertIn(WARNING, html)
                    self.assertIn(DISCLAIMER, html)


if __name__ == "__main__":
    unittest.main()
