import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from market_data import collect_market_data, parse_series_csv, parse_treasury_xml, business_days_since


class MarketDataTests(unittest.TestCase):
    def test_direct_treasury_supersedes_lagging_fred(self):
        config = {'schema_version': 1, 'provider': 'FRED', 'series': [
            {'id': 'DGS10', 'name': 'US Treasury 10-year yield', 'region': 'us',
             'unit': 'percent', 'source': 'Federal Reserve Board, H.15',
             'treasury_field': 'BC_10YEAR', 'max_age_business_days': 1}], 'unavailable': []}
        xml = '''<feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
          xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
          <entry><content><m:properties><d:NEW_DATE>2026-11-23T00:00:00</d:NEW_DATE>
            <d:BC_10YEAR>4.92</d:BC_10YEAR></m:properties></content></entry>
          <entry><content><m:properties><d:NEW_DATE>2026-11-24T00:00:00</d:NEW_DATE>
            <d:BC_10YEAR>4.91</d:BC_10YEAR></m:properties></content></entry></feed>'''
        self.assertEqual(parse_treasury_xml(xml, datetime(2026,11,25).date())['BC_10YEAR'][-1][1], 4.91)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'markets.json'; p.write_text(json.dumps(config))
            result = collect_market_data(datetime(2026,11,25,13,15,tzinfo=timezone.utc),
                                         lambda url: xml if 'treasury.gov' in url else 'DATE,DGS10\n2026-11-23,4.92\n', p)
        asset = result['assets'][0]
        self.assertEqual((asset['level'], asset['change'], asset['as_of'], asset['status']),
                         (4.91, -0.01, '2026-11-24', 'ok'))
        self.assertEqual(asset['source'], 'U.S. Department of the Treasury')

    def test_treasury_fallback_identifies_methodology(self):
        config = {'schema_version': 1, 'provider': 'FRED', 'series': [
            {'id': 'DGS10', 'name': 'US Treasury 10-year yield', 'region': 'us',
             'unit': 'percent', 'source': 'Federal Reserve Board, H.15 (fallback)',
             'treasury_field': 'BC_10YEAR', 'max_age_business_days': 1}], 'unavailable': []}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'markets.json'; p.write_text(json.dumps(config))
            result = collect_market_data(datetime(2026,11,25,13,15,tzinfo=timezone.utc),
                                         lambda url: '<invalid' if 'treasury.gov' in url else
                                         'DATE,DGS10\n2026-11-23,4.92\n', p)
        asset = result['assets'][0]
        self.assertEqual(asset['status'], 'stale')
        self.assertEqual(asset['source'], 'FRED')
        self.assertIn('Federal Reserve Board, H.15', asset['original_source'])
        self.assertIn('constant-maturity series via FRED', asset['note'])
        self.assertEqual(asset['as_of'], '2026-11-23')

    def test_us_federal_holiday_is_excluded_from_business_age(self):
        self.assertEqual(business_days_since(datetime(2026,11,25).date(),
                                             datetime(2026,11,27).date()), 1)

    def test_weekly_fx_reference_and_lagging_oil(self):
        config = {'schema_version': 1, 'provider': 'FRED', 'series': [
            {'id': 'DEXINUS', 'name': 'USD/INR', 'region': 'india', 'unit': 'INR per USD',
             'source': 'Federal Reserve Board, H.10', 'cadence': 'weekly_reference',
             'max_age_business_days': 6},
            {'id': 'DCOILWTICO', 'name': 'WTI', 'region': 'global', 'unit': 'USD per barrel',
             'source': 'EIA', 'reference_only': True, 'max_age_business_days': 2}], 'unavailable': []}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'markets.json'; p.write_text(json.dumps(config))
            def fetch(url):
                return ('DATE,DEXINUS\n2026-11-20,92.67\n' if 'DEXINUS' in url else
                        'DATE,DCOILWTICO\n2026-11-17,89.53\n')
            result = collect_market_data(datetime(2026,11,25,13,15,tzinfo=timezone.utc), fetch, p)
        fx, oil = result['assets']
        self.assertEqual(fx['status'], 'reference')
        self.assertIn('weekly H.10', fx['note'])
        self.assertEqual(oil['status'], 'stale')
        self.assertIn('exceeds expected publication cadence', oil['note'])

    def test_skips_future_missing_and_non_numeric_values(self):
        for header in ('observation_date', 'DATE'):
            with self.subTest(header=header):
                sample = f'{header},DGS10\n2026-11-20,4.0\n2026-11-21,.\n2026-11-23,4.1\n2026-11-25,999\n'
                self.assertEqual(parse_series_csv(sample, 'DGS10', datetime(2026,11,24).date()), [(datetime(2026,11,20).date(),4.0),(datetime(2026,11,23).date(),4.1)])

    def test_rejects_wrong_column(self):
        with self.assertRaises(ValueError):
            parse_series_csv('DATE,OTHER\n2026-11-23,4\n', 'DGS10', datetime(2026,11,24).date())

    def test_observation_change_and_unavailable_are_explicit(self):
        config={'schema_version':1,'provider':'FRED','series':[{'id':'DGS10','name':'10-year','region':'us','unit':'percent','source':'Federal Reserve','max_age_days':5},{'id':'DGS2','name':'2-year','region':'us','unit':'percent','source':'Federal Reserve','max_age_days':5}], 'unavailable':[{'name':'Sensex','region':'india','note':'Licensing unverified'}]}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'markets.json'; p.write_text(json.dumps(config))
            def fetcher(url):
                if 'DGS2' in url: raise OSError('offline')
                return 'observation_date,DGS10\n2026-11-20,4.00\n2026-11-23,4.05\n'
            result=collect_market_data(datetime(2026,11,24,tzinfo=timezone.utc),fetcher,p)
        ten,two,sensex=result['assets']
        self.assertEqual((ten['level'],ten['change'],ten['as_of'],ten['status']),(4.05,0.05,'2026-11-23','ok'))
        self.assertIsNone(two['level'])
        self.assertIsNone(sensex['level'])
        self.assertEqual(sensex['status'],'unavailable')

    def test_old_data_labeled_stale(self):
        config={'schema_version':1,'provider':'FRED','series':[{'id':'DGS10','name':'10-year','region':'us','unit':'percent','source':'Federal Reserve','max_age_days':2}], 'unavailable':[]}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'markets.json'; p.write_text(json.dumps(config))
            result=collect_market_data(datetime(2026,11,24,tzinfo=timezone.utc),lambda url:'DATE,DGS10\n2026-11-18,4.0\n',p)
        self.assertEqual(result['assets'][0]['status'],'stale')
        self.assertEqual(result['assets'][0]['as_of'],'2026-11-18')

    def test_unsafe_id_fails_closed(self):
        config={'schema_version':1,'provider':'FRED','series':[{'id':'DGS10&other=x','name':'10-year','region':'us','unit':'percent','source':'Federal Reserve','max_age_days':2}], 'unavailable':[]}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'markets.json'; p.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                collect_market_data(datetime(2026,11,24,tzinfo=timezone.utc),lambda url:'',p)

if __name__ == '__main__': unittest.main()
