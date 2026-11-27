import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import build_briefs as brief


class PageGenerationTests(unittest.TestCase):
    def test_build_dashboard_generates_all_pages_without_template_tokens(self):
        pages = (
            "index", "markets", "macro", "india", "etfs", "geopolitics",
            "earnings", "watchlist", "archives", "sources",
        )
        with tempfile.TemporaryDirectory() as directory:
            docs = Path(directory)
            with patch.object(brief, "DOCS", docs), patch.object(brief, "REPORTS", docs / "reports"):
                brief.build_dashboard()

            for page in pages:
                with self.subTest(page=page):
                    html = (docs / f"{page}.html").read_text()
                    for token in ("__PAGE__", "__PAGE_CONTENT__", "__MANIFEST__"):
                        self.assertNotIn(token, html)


if __name__ == "__main__":
    unittest.main()
