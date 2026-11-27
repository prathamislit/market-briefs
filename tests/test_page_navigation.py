import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import build_briefs as brief


ROUTES = {
    "index.html", "markets.html", "india.html", "macro.html",
    "geopolitics.html", "earnings.html", "etfs.html", "watchlist.html",
    "sources.html", "archives.html",
}


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = set()
        self.has_nav = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.has_nav |= tag == "nav"
        if tag == "a" and attrs.get("href"):
            self.links.add(attrs["href"])


class PageNavigationTests(unittest.TestCase):
    def test_index_and_markets_link_to_every_route(self):
        with tempfile.TemporaryDirectory() as temp:
            docs = Path(temp)
            with patch.object(brief, "DOCS", docs), patch.object(brief, "REPORTS", docs / "reports"):
                brief.build_dashboard()

            for filename in ("index.html", "markets.html"):
                with self.subTest(page=filename):
                    parser = PageParser()
                    parser.feed((docs / filename).read_text())
                    self.assertTrue(parser.has_nav)
                    self.assertTrue(ROUTES <= parser.links, ROUTES - parser.links)


if __name__ == "__main__":
    unittest.main()
