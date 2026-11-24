import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from orchestrate_daily import publish


class DailyArchiveTests(unittest.TestCase):
    def test_sec_failure_retains_dated_prior_filing(self):
        with tempfile.TemporaryDirectory() as temp:
            docs = Path(temp)
            (docs / "daily").mkdir()
            name = "20261123T130000000000Z-local-1.json"
            old_entry = {"kind": "sec_etf", "ticker": "IVV", "report_date": "2026-06-30",
                         "filing_date": "2026-08-25", "status": "latest_available_lagged",
                         "source_url": "https://www.sec.gov/Archives/edgar/data/1100663/123/primary_doc.xml"}
            (docs / "daily" / name).write_text(json.dumps({"sections": {"etf_flows": {"entries": [old_entry]}}}))
            (docs / "daily/manifest.json").write_text(json.dumps([{"json": name}]))
            market = lambda now: {"assets": []}
            watch = lambda now: {"events": []}
            etf = lambda now: {"entries": [], "failures": [{"source": "SEC", "error": "Offline"}]}
            earnings = lambda now: {"entries": [], "failures": []}
            report, _ = publish(datetime(2026, 11, 24, 13, tzinfo=timezone.utc), docs=docs,
                                market_collector=market, watchlist_collector=watch,
                                etf_collector=etf, earnings_collector=earnings)
            retained = next(e for e in report["sections"]["etf_flows"]["entries"]
                            if e.get("kind") == "sec_etf")
            self.assertEqual(retained["status"], "archived")
            self.assertEqual(retained["report_date"], old_entry["report_date"])
            self.assertTrue(any("SEC retrieval failed: Offline" in gap
                                for gap in report["sections"]["etf_flows"]["missing"]))

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
            etf = lambda now: {"entries": [], "failures": [], "retrieved_at": now.isoformat()}
            earnings = lambda now: {"entries": [], "failures": [], "retrieved_at": now.isoformat()}
            first = datetime(2026, 11, 24, 13, 0, tzinfo=timezone.utc)
            _, path1 = publish(first, docs=docs, market_collector=market, watchlist_collector=watch,
                               etf_collector=etf, earnings_collector=earnings)
            _, path2 = publish(first.replace(microsecond=1), docs=docs,
                               market_collector=market, watchlist_collector=watch,
                               etf_collector=etf, earnings_collector=earnings)
            entries = json.loads((docs / "daily/manifest.json").read_text())
            self.assertEqual(len(entries), 2)
            self.assertNotEqual(path1, path2)
            self.assertEqual(json.loads(path1.read_text())["sections"]["source_register"]["entries"][0]["status"], "failed")
            self.assertTrue(path1.with_suffix(".md").exists())
            with self.assertRaises(FileExistsError):
                publish(first, docs=docs, market_collector=market, watchlist_collector=watch,
                        etf_collector=etf, earnings_collector=earnings)


if __name__ == "__main__":
    unittest.main()
