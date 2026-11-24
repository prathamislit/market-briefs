import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from market_data import collect_market_data, parse_series_csv


class MarketDataTests(unittest.TestCase):
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
