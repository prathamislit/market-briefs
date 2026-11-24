import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from transmission import build_transmission


NOW = datetime(2026, 11, 24, 13, tzinfo=timezone.utc)


def card(source, title, link, published="2026-11-23T18:00:00+00:00", summary=""):
    return {"source": source, "title": title, "link": link,
            "publication_timestamp": published, "summary": summary}


class TransmissionTests(unittest.TestCase):
    def test_policy_mechanism_and_daily_overlap_never_claim_cause(self):
        briefs = {"us": {"sections": {"rates": [card(
            "fed", "Federal Reserve issues FOMC statement",
            "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261123a.htm",
            summary="The Committee decided to maintain the target range for the federal funds rate.")]}}}
        market = {"assets": [{"name": "US Treasury 2-year yield", "status": "ok",
                              "as_of": "2026-11-24", "previous_as_of": "2026-11-20",
                              "change": -0.12, "source_url": "https://fred.stlouisfed.org/series/DGS2"}]}
        result = build_transmission(briefs, market, NOW)
        self.assertEqual(len(result["entries"]), 1)
        entry = result["entries"][0]
        self.assertIn("federalreserve.gov", entry["mechanism_url"])
        self.assertEqual(entry["confidence"], "unverified_event_market_attribution")
        self.assertIn("target range", entry["event"]["evidence_excerpt"])
        self.assertEqual(entry["affected_assets"][0]["temporal_relation"], "brackets_event_at_daily_resolution")
        self.assertEqual(entry["affected_assets"][0]["observed_change"], -0.12)
        self.assertIn("unverified", entry["conclusion"])

    def test_generic_sanction_or_commercial_headline_never_maps_to_oil(self):
        briefs = {"us": {"sections": {"geopolitics": [
            card("treasury", "Treasury announces sanctions on officials",
                 "https://home.treasury.gov/news/press-releases/jy1234"),
            card("bbc", "Strait of Hormuz oil transit blocked",
                 "https://www.bbc.com/news/business/123"),
        ]}}}
        result = build_transmission(briefs, {"assets": []}, NOW)
        self.assertEqual(result["entries"], [])
        self.assertTrue(result["missing"])

    def test_eia_specific_disruption_is_only_potential_channel(self):
        item = card("eia", "Strait of Hormuz oil transit disrupted",
                    "https://www.eia.gov/todayinenergy/detail.php?id=123",
                    summary="Oil transit through the Strait of Hormuz was disrupted, delaying shipments.")
        briefs = {"us": {"sections": {"energy": [item], "geopolitics": [item]}}}
        market = {"assets": [{"name": "Brent crude oil spot", "status": "stale",
                              "as_of": "2026-11-14", "previous_as_of": "2026-11-13",
                              "change": 2.4}]}
        result = build_transmission(briefs, market, NOW)
        self.assertEqual(len(result["entries"]), 1)
        entry = result["entries"][0]
        self.assertIn("eia.gov", entry["mechanism_url"])
        self.assertEqual(entry["affected_assets"][0]["status"], "stale")
        self.assertIsNone(entry["affected_assets"][0]["observed_change"])
        self.assertEqual(entry["affected_assets"][1]["status"], "unavailable")

    def test_future_old_and_off_domain_events_are_rejected(self):
        briefs = {"us": {"sections": {"macro": [
            card("fed", "Federal Reserve issues FOMC statement",
                 "https://example.com/monetary", "2026-11-23T18:00:00Z"),
            card("fed", "Federal Reserve issues FOMC statement",
                 "https://www.federalreserve.gov/old", "2026-11-01T18:00:00Z"),
            card("fed", "Federal Reserve issues FOMC statement",
                 "https://www.federalreserve.gov/future", "2026-11-25T18:00:00Z"),
        ]}}}
        self.assertEqual(build_transmission(briefs, {}, NOW)["entries"], [])

    def test_primary_headline_without_action_evidence_is_not_enough(self):
        briefs = {"us": {"sections": {"rates": [card(
            "fed", "Federal Reserve issues FOMC statement",
            "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261123a.htm")]}}}
        self.assertEqual(build_transmission(briefs, {}, NOW)["entries"], [])

    def test_negated_oil_disruption_does_not_trigger(self):
        briefs = {"us": {"sections": {"energy": [card(
            "eia", "Strait of Hormuz oil transit disruption risk",
            "https://www.eia.gov/todayinenergy/detail.php?id=123",
            summary="Oil transit through the Strait of Hormuz has not been disrupted.")]}}}
        self.assertEqual(build_transmission(briefs, {}, NOW)["entries"], [])

    def test_unrelated_disruption_elsewhere_does_not_trigger(self):
        briefs = {"us": {"sections": {"energy": [card(
            "eia", "Strait of Hormuz oil transit disruption risk",
            "https://www.eia.gov/todayinenergy/detail.php?id=123",
            summary="Oil transit through the Strait of Hormuz remains open. Oil exports elsewhere were disrupted.")]}}}
        self.assertEqual(build_transmission(briefs, {}, NOW)["entries"], [])

    def test_negated_hormuz_then_positive_elsewhere_is_rejected(self):
        briefs = {"us": {"sections": {"energy": [card(
            "eia", "Strait of Hormuz oil transit disruption risk",
            "https://www.eia.gov/todayinenergy/detail.php?id=123",
            summary="Oil transit through the Strait of Hormuz has not been disrupted; another port was closed.")]}}}
        self.assertEqual(build_transmission(briefs, {}, NOW)["entries"], [])

    def test_naive_clock_is_rejected(self):
        with self.assertRaises(ValueError):
            build_transmission({}, {}, datetime(2026, 11, 24))


if __name__ == "__main__":
    unittest.main()
