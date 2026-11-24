import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from daily_report import DESKS, compose_daily_report, render_markdown


class DailyReportTests(unittest.TestCase):
    def setUp(self):
        earnings = {"title": "Company earnings rise", "summary": "", "source": "bbc",
                    "source_name": "BBC Business", "link": "https://example.org/report",
                    "publication_timestamp": "2026-11-23T12:00:00+00:00", "publication_freshness": "recent_window"}
        self.briefs = {
            "us": {"date": "2026-11-23", "sections": {"overview": [earnings], "earnings": [earnings]},
                   "sources": [{"name": "BBC Business", "status": "ok", "home": "https://www.bbc.com/news/business", "attempt_timestamp": "2026-11-23T13:00:00+00:00"}]},
            "india": {"date": "2026-11-24", "sections": {}, "sources": []},
        }

    def test_all_requested_desks_have_explicit_gaps(self):
        result = compose_daily_report(self.briefs, generated_at="2026-11-24T04:00:00+00:00")
        self.assertEqual(list(result["sections"]), [key for key, _ in DESKS])
        self.assertTrue(result["sections"]["etf_flows"]["missing"])
        self.assertEqual(result["sections"]["watchlist"]["status"], "unavailable")
        self.assertEqual(result["sections"]["earnings"]["status"], "partial")
        self.assertEqual(len(result["sections"]["earnings"]["entries"]), 1)

    def test_no_consensus_or_reaction_inferred_from_earnings_headline(self):
        result = compose_daily_report(self.briefs)
        lead = result["sections"]["earnings"]["entries"][0]
        self.assertEqual(lead["claim_type"], "source_headline")
        self.assertNotIn("expectation", lead)
        self.assertNotIn("price_reaction", lead)
        self.assertIn("Consensus comparisons", result["sections"]["earnings"]["missing"][0])

    def test_retained_edition_headline_ages_against_this_run(self):
        self.briefs["us"]["sections"]["earnings"][0]["publication_freshness"] = "recent_window"
        result = compose_daily_report(self.briefs, generated_at="2026-12-10T04:00:00+00:00")
        self.assertEqual(result["sections"]["earnings"]["entries"], [])
        self.assertEqual(result["sections"]["earnings"]["status"], "unavailable")
        self.assertNotIn("Company earnings rise", render_markdown(result))

    def test_sourced_asset_and_unavailable_asset_preserve_distinction(self):
        market_data = {"assets": [
            {"name": "Nifty 50", "region": "india", "level": 23500, "change": 18,
             "unit": "points", "as_of": "2026-11-23", "status": "ok", "source_url": "https://www.nseindia.com/"},
            {"name": "India VIX", "region": "india", "level": None, "change": None,
             "status": "unavailable", "note": "Source inaccessible"},
            {"name": "ETF flows and positioning", "region": "us", "level": None,
             "status": "unavailable", "note": "No verified feed"},
            {"name": "US Treasury 10-year yield", "region": "us", "level": 4.25,
             "status": "ok", "unit": "percent", "as_of": "2026-11-23"},
            {"name": "Brent crude oil spot", "region": "global", "level": 80.1,
             "status": "stale", "unit": "USD per barrel", "as_of": "2026-11-14"},
        ]}
        result = compose_daily_report(self.briefs, market_data=market_data)
        self.assertEqual(result["sections"]["cross_asset"]["status"], "partial")
        self.assertEqual(len(result["sections"]["india_markets"]["entries"]), 2)
        self.assertEqual(result["sections"]["etf_flows"]["status"], "unavailable")
        self.assertEqual(result["sections"]["etf_flows"]["entries"][0]["status"], "unavailable")
        self.assertEqual(result["sections"]["macro_policy"]["entries"][0]["name"], "US Treasury 10-year yield")
        self.assertEqual(result["sections"]["us_policy_currency"]["entries"][0]["name"], "US Treasury 10-year yield")
        md = render_markdown(result)
        self.assertIn("23500", md)
        self.assertIn("Unavailable: Source inaccessible", md)
        self.assertIn("stale: 80.1 USD per barrel", md)
        self.assertIn("**Not financial advice.**", md)

    def test_only_public_collector_inputs_are_accepted(self):
        with self.assertRaises(TypeError):
            compose_daily_report(self.briefs, newsletters={"issues": [{"body_text": "private"}]})

    def test_source_status_and_links_survive_json_and_markdown(self):
        result = compose_daily_report(self.briefs)
        self.assertEqual(result["sections"]["source_register"]["entries"][0]["status"], "ok")
        self.assertIn("BBC Business", render_markdown(result))
        self.assertIn("https://example.org/report", render_markdown(result))
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_failed_sources_do_not_make_register_available(self):
        self.briefs["us"]["sources"][0]["status"] = "fetch_failed"
        result = compose_daily_report(self.briefs)
        self.assertEqual(result["sections"]["source_register"]["status"], "unavailable")

    def test_watchlist_has_dated_primary_event_without_forecasting(self):
        report = compose_daily_report(self.briefs, watchlist={"events": [
            {"title": "GDP release", "starts_at": "2026-11-25T13:30:00+00:00",
             "source": "BEA", "source_url": "https://www.bea.gov/news/schedule"},
            {"title": "BLS release", "starts_at": None, "date": "2026-11-26",
             "source": "BLS", "source_url": "https://www.bls.gov/schedule/news_release/"}]})
        events = report["sections"]["watchlist"]["entries"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["claim_type"], "primary_schedule")
        self.assertIn("outcome unknown", events[0]["detail"])
        self.assertEqual(events[1]["as_of"], "2026-11-26")
        self.assertTrue(events[1]["all_day"])
        self.assertFalse(report["sections"]["watchlist"]["missing"])


if __name__ == "__main__":
    unittest.main()
