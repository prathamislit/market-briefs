import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from html.parser import HTMLParser

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import build_briefs


class IdCounter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        self.ids.extend(value for name, value in attrs if name == "id")


class PageMountTests(unittest.TestCase):
    def test_generated_research_pages_have_one_mount_each(self):
        pages = (
            "markets", "macro", "india", "etfs", "geopolitics",
            "earnings", "watchlist", "sources",
        )
        with tempfile.TemporaryDirectory() as directory:
            docs = Path(directory)
            with patch.object(build_briefs, "DOCS", docs), patch.object(
                build_briefs, "REPORTS", docs / "reports"
            ):
                build_briefs.build_dashboard()

            for page in pages:
                with self.subTest(page=page):
                    path = docs / f"{page}.html"
                    self.assertTrue(path.exists(), f"Missing generated page: {path}")
                    parser = IdCounter()
                    parser.feed(path.read_text())
                    self.assertEqual(parser.ids.count("daily-report"), 1)
                    self.assertEqual(parser.ids.count("daily-controls"), 1)

            home = docs / "index.html"
            self.assertTrue(home.exists())
            parser = IdCounter()
            parser.feed(home.read_text())
            self.assertIn("home-latest", parser.ids)
            self.assertEqual(parser.ids.count("daily-report"), 0)


if __name__ == "__main__":
    unittest.main()
