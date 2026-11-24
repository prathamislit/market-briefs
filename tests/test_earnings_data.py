import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import earnings_data as earnings


NOW = datetime(2026, 10, 23, 13, tzinfo=timezone.utc)
ACCESSION = "0001628280-26-066775"


def fixture(*, consensus=True, same_period=True, consensus_date="Oct 19, 2026", results=True, curated=False):
    ticker_index = {str(i): {"cik_str": 1318605 if curated and ticker == "TSLA" else i + 1,
                             "ticker": ticker, "title": ticker}
                    for i, ticker in enumerate(earnings.TICKERS)}
    responses = {earnings.TICKERS_URL: json.dumps(ticker_index).encode()}
    for i, ticker in enumerate(earnings.TICKERS):
        cik = 1318605 if curated and ticker == "TSLA" else i + 1
        recent = {"form": [], "filingDate": [], "reportDate": [], "accessionNumber": [], "primaryDocument": []}
        if ticker == "TSLA":
            recent = {"form": ["10-Q"], "filingDate": ["2026-10-22"],
                      "reportDate": ["2026-09-30"],
                      "accessionNumber": [ACCESSION], "primaryDocument": ["tsla-20260930.htm"]}
            if curated:
                for key, value in {"form": "8-K", "filingDate": "2026-10-21",
                                   "reportDate": "2026-10-21", "accessionNumber": "0001628280-26-066738",
                                   "primaryDocument": "tsla-20261021.htm"}.items():
                    recent[key].append(value)
        responses[f"https://data.sec.gov/submissions/CIK{cik:010d}.json"] = json.dumps(
            {"filings": {"recent": recent}}).encode()
    gaap = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            {"accn": ACCESSION, "form": "10-Q", "start": "2026-01-01", "end": "2026-09-30", "val": 80000000000},
            {"accn": ACCESSION, "form": "10-Q", "start": "2025-07-01", "end": "2025-09-30", "val": 28095000000},
            {"accn": ACCESSION, "form": "10-Q", "start": "2026-07-01", "end": "2026-09-30", "val": 28046000000}]}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [
            {"accn": ACCESSION, "form": "10-Q", "start": "2026-01-01", "end": "2026-09-30", "val": 0.67},
            {"accn": ACCESSION, "form": "10-Q", "start": "2026-07-01" if same_period else "2026-04-01",
             "end": "2026-09-30", "val": 0.22}]}}
    }
    facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{1318605 if curated else 7:010d}.json"
    responses[facts_url] = json.dumps({"facts": {"us-gaap": gaap}}).encode()
    if results:
        responses[earnings.TESLA_ROOT + "/press-release/tesla-releases-third-quarter-2026-financial-results"] = (
            '<h1>Tesla Releases Third Quarter 2026 Financial Results</h1>'
            '<p>Business Wire</p><time>Oct 21, 2026</time>'
            '<p>Tesla has released financial results.</p>').encode()
    if consensus:
        responses[earnings.TESLA_ROOT + "/press-release/earnings-consensus-third-quarter-2026"] = (
            f'<h1>Q3 2026 Earnings Consensus</h1><time>{consensus_date}</time>'
            '<table><tr><th>P&amp;L</th><th>Average</th><th>Median</th></tr>'
            '<tr><td>Total revenues</td><td>28,341</td><td>28,264</td></tr>'
            '<tr><td>EPS - GAAP</td><td>0.23</td><td>0.21</td></tr></table>').encode()
    return responses


class EarningsTests(unittest.TestCase):
    def test_matches_same_quarter_actual_to_issuer_pre_release_mean(self):
        responses = fixture()
        result = earnings.collect_earnings(NOW, responses.__getitem__)
        self.assertEqual(len(result["entries"]), 2)
        revenue = next(e for e in result["entries"] if e["metric"] == "Revenue")
        self.assertEqual(revenue["actual"], 28046)
        self.assertIsNone(revenue["expectation"])
        self.assertEqual(revenue["period_start"], "2026-07-01")
        self.assertIn("Archives/edgar/data/7/", revenue["source_url"])
        eps = next(e for e in result["entries"] if e["metric"] == "Diluted EPS")
        self.assertAlmostEqual(eps["difference"], -0.01)
        self.assertEqual(eps["expectation"], 0.23)
        self.assertEqual(eps["expectation_published"], "2026-10-19")
        self.assertTrue(eps["results_source_url"].endswith("third-quarter-2026-financial-results"))
        self.assertEqual(eps["basis"], "GAAP")
        self.assertIsNone(eps["reaction"])

    def test_missing_consensus_preserves_actual_only(self):
        responses = fixture(consensus=False)
        result = earnings.collect_earnings(NOW, responses.__getitem__)
        self.assertEqual(len(result["entries"]), 2)
        self.assertTrue(all(e["status"] == "actual_only" and e["expectation"] is None for e in result["entries"]))
        self.assertEqual(result["failures"][0]["source"], "Tesla consensus")

    def test_ytd_and_prior_year_do_not_replace_current_quarter(self):
        facts = fixture()["https://data.sec.gov/api/xbrl/companyfacts/CIK0000000007.json"]
        result = earnings._facts(facts, ACCESSION, "2026-09-30")
        self.assertEqual(result["Diluted EPS"]["actual"], 0.22)
        self.assertEqual(result["Revenue"]["period_end"], "2026-09-30")

    def test_mismatched_periods_fail_explicitly(self):
        responses = fixture(same_period=False)
        result = earnings.collect_earnings(NOW, responses.__getitem__)
        # The 9-month EPS datum is excluded; revenue remains source-backed.
        self.assertEqual(len(result["entries"]), 1)
        self.assertEqual(result["entries"][0]["metric"], "Revenue")

    def test_stale_filing_not_reported_as_current(self):
        result = earnings.collect_earnings(datetime(2027, 2, 22, tzinfo=timezone.utc), fixture().__getitem__)
        self.assertEqual(result["entries"], [])

    def test_older_filing_is_explicitly_historical(self):
        result = earnings.collect_earnings(datetime(2026, 11, 24, tzinfo=timezone.utc), fixture().__getitem__)
        self.assertEqual(len(result["entries"]), 2)
        self.assertTrue(all(row["historical"] and row["filing_age_days"] == 33 for row in result["entries"]))

    def test_post_results_consensus_rejected(self):
        result = earnings.collect_earnings(NOW, fixture(consensus_date="Oct 22, 2026").__getitem__)
        self.assertTrue(all(entry["status"] == "actual_only" for entry in result["entries"]))

    def test_same_day_consensus_rejected(self):
        result = earnings.collect_earnings(NOW, fixture(consensus_date="Oct 21, 2026").__getitem__)
        self.assertTrue(all(entry["status"] == "actual_only" for entry in result["entries"]))

    def test_missing_results_release_rejects_consensus(self):
        result = earnings.collect_earnings(NOW, fixture(results=False).__getitem__)
        self.assertTrue(all(entry["status"] == "actual_only" for entry in result["entries"]))

    def test_curated_snapshot_survives_issuer_http_failure(self):
        result = earnings.collect_earnings(NOW, fixture(consensus=False, results=False, curated=True).__getitem__)
        eps = next(e for e in result["entries"] if e["metric"] == "Diluted EPS")
        self.assertEqual(eps["expectation"], 0.23)
        self.assertAlmostEqual(eps["difference"], -0.01)
        self.assertEqual(eps["expectation_evidence_basis"], "curated_primary_snapshot")
        self.assertEqual(eps["results_published"], "2026-10-21")
        self.assertIn("000162828026066738", eps["results_source_url"])
        revenue = next(e for e in result["entries"] if e["metric"] == "Revenue")
        self.assertIsNone(revenue["expectation"])

    def test_curated_snapshot_requires_matching_sec_8k(self):
        responses = fixture(consensus=False, results=False, curated=True)
        url = "https://data.sec.gov/submissions/CIK0001318605.json"
        data = json.loads(responses[url])
        data["filings"]["recent"]["accessionNumber"][1] = "0001628280-26-000000"
        responses[url] = json.dumps(data).encode()
        result = earnings.collect_earnings(NOW, responses.__getitem__)
        self.assertTrue(all(e["status"] == "actual_only" for e in result["entries"]))

    def test_curated_snapshot_requires_exact_quarter_and_company(self):
        responses = fixture(consensus=False, results=False, curated=True)
        submissions = responses["https://data.sec.gov/submissions/CIK0001318605.json"]
        filing = earnings._recent_filing(submissions, NOW)
        actual = {"period_start": "2026-07-01", "period_end": "2026-09-30",
                  "unit": "USD/share", "basis": "GAAP"}
        self.assertIsNotNone(earnings._tesla_curated_snapshot(submissions, 1318605, filing, actual))
        for mismatch in ({"period_start": "2026-07-02"}, {"period_end": "2026-06-30"},
                         {"unit": "USD millions"}, {"basis": "non-GAAP"}):
            self.assertIsNone(earnings._tesla_curated_snapshot(submissions, 1318605, filing,
                                                               {**actual, **mismatch}))
        self.assertIsNone(earnings._tesla_curated_snapshot(submissions, 7, filing, actual))
        self.assertIsNone(earnings._tesla_curated_snapshot(submissions, 1318605,
                                                            {**filing, "accession": "0001628280-26-000000"}, actual))

    def test_curated_snapshot_does_not_override_conflicting_live_page(self):
        result = earnings.collect_earnings(NOW, fixture(curated=True,
                                                         consensus_date="Oct 22, 2026").__getitem__)
        self.assertTrue(all(e["status"] == "actual_only" for e in result["entries"]))


if __name__ == "__main__":
    unittest.main()
