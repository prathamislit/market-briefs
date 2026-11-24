import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sec_etf


NOW = datetime(2026, 11, 24, 13, tzinfo=timezone.utc)
BASE = "https://www.sec.gov/Archives/edgar/data/1100663/"
NEW_URL = BASE + "000207169126019760/primary_doc.xml"
OLD_URL = BASE + "000207169126012459/primary_doc.xml"


def feed(*items):
    entries = []
    for accession, filed in items:
        unhyphenated = accession.replace("-", "")
        entries.append(f'<entry><title>NPORT-P  - Monthly Portfolio Investments Report</title>'
                       f'<updated>{filed}T10:10:51-04:00</updated>'
                       f'<link href="{BASE}{unhyphenated}/{accession}-index.htm"/></entry>')
    return ('<feed xmlns="http://www.w3.org/2005/Atom">' + ''.join(entries) + '</feed>').encode()


def filing(series="S000004310", period="2026-06-30"):
    return (f'<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">'
            f'<formData><genInfo><seriesName>iShares Core S&amp;P 500 ETF</seriesName>'
            f'<seriesId>{series}</seriesId><repPdDate>{period}</repPdDate></genInfo>'
            f'<fundInfo>'
            f'<mon1Flow sales="100" redemption="20" reinvestment="5"/>'
            f'<mon2Flow sales="30" redemption="60" reinvestment="0"/>'
            f'<mon3Flow sales="80" redemption="10" reinvestment="2"/>'
            f'</fundInfo><invstOrSecs>'
            f'<invstOrSec><name>Small Co</name><assetCat>EC</assetCat><valUSD>50</valUSD><pctVal>1.5</pctVal></invstOrSec>'
            f'<invstOrSec><name>Large Co</name><assetCat>EC</assetCat><valUSD>100</valUSD><pctVal>3.0</pctVal></invstOrSec>'
            f'<invstOrSec><name>Cash</name><assetCat>STIV</assetCat><valUSD>1000</valUSD><pctVal>30</pctVal></invstOrSec>'
            f'</invstOrSecs></formData></edgarSubmission>').encode()


class SECETFTests(unittest.TestCase):
    def test_series_feed_and_real_nport_field_shape(self):
        raw = {sec_etf.FEED_URL: feed(("0002071691-26-019760", "2026-08-25")),
               NEW_URL: filing()}
        report = sec_etf.collect_etf_data(NOW, raw.__getitem__)
        self.assertEqual(report["failures"], [])
        entry = report["entries"][0]
        self.assertEqual(entry["report_date"], "2026-06-30")
        self.assertEqual(entry["filing_date"], "2026-08-25")
        self.assertEqual(entry["report_lag_days"], 147)
        self.assertEqual(entry["source_url"], NEW_URL)
        self.assertEqual(entry["net_flows"][0]["reported_net_share_flow_usd"], 85.0)
        self.assertEqual(entry["net_flows"][1]["reported_net_share_flow_usd"], -30.0)
        self.assertEqual(entry["top_holdings"][0]["name"], "Large Co")
        self.assertEqual(len(entry["top_holdings"]), 2)
        self.assertIn("not current daily", entry["note"])

    def test_other_series_never_misidentified_as_ivv(self):
        raw = {sec_etf.FEED_URL: feed(("0002071691-26-019760", "2026-08-25")),
               NEW_URL: filing(series="S999999999")}
        report = sec_etf.collect_etf_data(NOW, raw.__getitem__)
        self.assertEqual(report["entries"], [])
        self.assertIn("does not belong", report["failures"][0]["error"])

    def test_newest_failure_falls_back_to_prior_verified_filing(self):
        raw = {sec_etf.FEED_URL: feed(("0002071691-26-019760", "2026-08-25"),
                                      ("0002071691-26-012459", "2026-05-28")),
               OLD_URL: filing(period="2026-03-31")}
        def fetch(url):
            if url == NEW_URL:
                raise OSError("SEC blocked")
            return raw[url]
        report = sec_etf.collect_etf_data(NOW, fetch)
        self.assertEqual(report["entries"][0]["status"], "older_filing_lagged")
        self.assertEqual(report["entries"][0]["report_date"], "2026-03-31")
        self.assertEqual(report["entries"][0]["filing_date"], "2026-05-28")
        self.assertEqual(len(report["failures"]), 1)

    def test_blocked_sec_and_future_filing_are_explicit(self):
        def blocked(url):
            raise OSError("SEC blocked")
        self.assertEqual(sec_etf.collect_etf_data(NOW, blocked)["entries"], [])
        self.assertEqual(len(sec_etf.collect_etf_data(NOW, blocked)["failures"]), 1)
        future = feed(("0002071691-26-019760", "2026-12-25"))
        report = sec_etf.collect_etf_data(NOW, {sec_etf.FEED_URL: future}.__getitem__)
        self.assertEqual(report["entries"], [])
        self.assertIn("before report cutoff", report["failures"][0]["error"])

    def test_bounded_xml_and_clock(self):
        with self.assertRaises(ValueError):
            sec_etf.collect_etf_data(datetime(2026, 11, 24))
        with self.assertRaises(ValueError):
            sec_etf._xml(b"x" * (sec_etf.MAX_BYTES + 1))
        with self.assertRaises(ValueError):
            sec_etf._xml(b'<!DOCTYPE a [<!ENTITY x "x">]><a>&x;</a>')

    def test_reported_net_currency_uses_cents(self):
        raw = filing().replace(b'sales="100" redemption="20" reinvestment="5"',
                               b'sales="100.10" redemption="20.05" reinvestment="5.00"')
        entry = sec_etf._parse_filing(raw, NEW_URL, NOW.date(), NOW)
        self.assertEqual(entry["net_flows"][0]["reported_net_share_flow_usd"], 85.05)


if __name__ == "__main__":
    unittest.main()
