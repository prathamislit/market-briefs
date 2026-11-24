import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from orchestrate_daily import publish


class DailyArchiveTests(unittest.TestCase):
    def test_every_invocation_is_immutable_and_indexed(self):
        with tempfile.TemporaryDirectory() as temp:
            docs = Path(temp)
            (docs / "reports").mkdir()
            brief = {"edition": "us", "date": "2026-11-23", "sections": {},
                     "sources": [{"name": "Fed", "status": "failed", "home": "https://www.federalreserve.gov/",
                                  "attempt_timestamp": "2026-11-23T13:00:00Z", "error": "Offline"}]}
            (docs / "reports/us-2026-11-23.json").write_text(json.dumps(brief))
            (docs / "reports/manifest.json").write_text(json.dumps([
                {"edition": "us", "date": "2026-11-23", "json": "us-2026-11-23.json"}]))
            market = lambda now: {"assets": [], "retrieved_at": now.isoformat()}
            watch = lambda now: {"events": [], "failures": ["Calendar unavailable"]}
            first = datetime(2026, 11, 24, 13, 0, tzinfo=timezone.utc)
            _, path1 = publish(first, docs=docs, market_collector=market, watchlist_collector=watch)
            _, path2 = publish(first.replace(microsecond=1), docs=docs,
                               market_collector=market, watchlist_collector=watch)
            entries = json.loads((docs / "daily/manifest.json").read_text())
            self.assertEqual(len(entries), 2)
            self.assertNotEqual(path1, path2)
            self.assertEqual(json.loads(path1.read_text())["sections"]["source_register"]["entries"][0]["status"], "failed")
            self.assertTrue(path1.with_suffix(".md").exists())
            with self.assertRaises(FileExistsError):
                publish(first, docs=docs, market_collector=market, watchlist_collector=watch)


if __name__ == "__main__":
    unittest.main()
