import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import build_briefs as brief


class PageWidgetScopeTests(unittest.TestCase):
    def test_market_quotes_widget_is_scoped_to_markets_page(self):
        page_names = {
            "index.html",
            "markets.html",
            "macro.html",
            "india.html",
            "earnings.html",
            "etfs.html",
            "watchlist.html",
            "sources.html",
            "geopolitics.html",
            "archives.html",
        }
        widget_marker = "embed-widget-market-quotes.js"

        with tempfile.TemporaryDirectory() as directory:
            docs = Path(directory)
            reports = docs / "reports"
            reports.mkdir()
            (reports / "manifest.json").write_text(json.dumps([]))

            with patch.object(brief, "DOCS", docs), patch.object(brief, "REPORTS", reports):
                brief.build_dashboard()

            index_html = (docs / "index.html").read_text()
            self.assertNotIn(widget_marker, index_html)

            markets_html = (docs / "markets.html").read_text()
            self.assertIn(widget_marker, markets_html)
            self.assertIn("US 500 CFD can move overnight", markets_html)
            self.assertIn("not the official S&amp;P 500 close", markets_html)

            for page_name in page_names - {"markets.html"}:
                with self.subTest(page=page_name):
                    page_html = (docs / page_name).read_text()
                    self.assertNotIn(widget_marker, page_html)


if __name__ == "__main__":
    unittest.main()
