import copy
import json
import io
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import build_briefs as brief

NOW = datetime(2026, 11, 24, 13, 15, tzinfo=timezone.utc)


class BriefTests(unittest.TestCase):
    def test_india_schedule_uses_confirmed_session(self):
        with patch.object(brief, 'nse_calendar_status', return_value={'status': 'trading_day'}):
            self.assertTrue(brief.is_schedule_window(NOW.replace(hour=2, minute=45), 'india'))

    def test_schedule_never_publishes_early(self):
        with patch.object(brief, 'calendar_status', return_value={'status': 'trading_day'}):
            self.assertFalse(brief.is_schedule_window(NOW.replace(hour=2, minute=44), 'india'))
            self.assertTrue(brief.is_schedule_window(NOW.replace(hour=3, minute=45), 'india'))
            self.assertFalse(brief.is_schedule_window(NOW.replace(hour=4, minute=16), 'india'))

    @unittest.skipIf(brief.mcal is None, 'Live calendar dependency absent; verified by GitHub CI')
    def test_us_standard_and_daylight_savings(self):
        self.assertTrue(brief.is_schedule_window(NOW, 'us'))
        self.assertTrue(brief.is_schedule_window(datetime(2026, 7, 1, 12, 15, tzinfo=timezone.utc), 'us'))

    @unittest.skipIf(brief.mcal is None, 'Live calendar dependency absent; verified by GitHub CI')
    def test_us_thanksgiving_closed(self):
        self.assertFalse(brief.is_schedule_window(NOW.replace(day=26), 'us'))

    def test_nse_weekend_never_needs_network(self):
        self.assertEqual(brief.nse_calendar_status(date(2026, 11, 22))['status'], 'non_trading_day')

    def test_nse_holiday_and_year_coverage(self):
        payload = {'CM': [{'tradingDate': '26-Jan-2026'}]}
        self.assertEqual(brief.nse_payload_status(payload, date(2026, 1, 26))['status'], 'holiday')
        self.assertEqual(brief.nse_payload_status(payload, date(2026, 1, 27))['status'], 'trading_day')
        self.assertEqual(brief.nse_payload_status(payload, date(2027, 1, 27))['status'], 'unknown')
        self.assertEqual(brief.nse_payload_status({}, date(2026, 1, 27))['status'], 'unknown')

    def test_unknown_calendar_fails_closed(self):
        with patch.object(brief, 'calendar_status', return_value={'status': 'unknown'}):
            self.assertFalse(brief.is_schedule_window(NOW, 'us'))

    def test_dedupe_retains_supporting_links(self):
        items = [{'title': 'Rates rise', 'link': 'https://a.example/story', 'source_name': 'A'},
                 {'title': 'rates rise', 'link': 'https://b.example/story', 'source_name': 'B'}]
        merged = brief.dedupe(items)
        self.assertEqual(len(merged), 1)
        self.assertEqual(set(merged[0]['supporting_links']), {x['link'] for x in items})
        self.assertEqual(merged[0]['supporting_sources'], ['A', 'B'])

    def test_keyword_boundaries(self):
        self.assertIn('rates', brief.classify('Treasury bond yields rise'))
        self.assertIn('earnings', brief.classify('Company earnings guidance'))
        self.assertNotIn('energy', brief.classify('Boiling water'))
        self.assertNotIn('us', brief.classify('Security review'))

    def test_atom_timestamp(self):
        self.assertEqual(brief.parse_publication_timestamp('2026-11-23T08:15:00Z'), '2026-11-23T08:15:00+00:00')
        self.assertIsNone(brief.parse_publication_timestamp('unknown'))

    def test_future_and_old_publications_rejected(self):
        self.assertEqual(brief.publication_freshness(NOW + timedelta(seconds=1), NOW), 'future_rejected')
        self.assertEqual(brief.publication_freshness(NOW - timedelta(days=8), NOW, NOW - timedelta(days=10)), 'outside_retention_window')

    def test_html_escaped(self):
        self.assertEqual(brief.html_escape('<b>Rate & risk</b>'), '&lt;b&gt;Rate &amp; risk&lt;/b&gt;')

    def test_unsafe_source_urls_rejected(self):
        for url in ['file:///etc/passwd', 'https://localhost/', 'https://host.local/', 'https://user:pass@example.org/', 'http://127.0.0.1/', 'http://169.254.169.254/']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                brief.validate_public_url(url, resolve=False)

    def test_dns_resolved_private_address_rejected(self):
        with patch.object(brief.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('10.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                brief.validate_public_url('https://public.example/')

    def test_redirect_revalidates_destination(self):
        handler = brief.SafeRedirectHandler(3)
        with self.assertRaises(ValueError):
            handler.redirect_request(None, None, 302, '', {}, 'http://localhost/')

    def test_atomic_write_and_cache_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            config = copy.deepcopy(brief.CONFIG)
            config['cache']['directory'] = directory
            with patch.object(brief, 'CONFIG', config):
                brief.write_cache('fed', [{'title': 'Sample'}])
                self.assertEqual(brief.read_cache('fed')['items'][0]['title'], 'Sample')
                brief.cache_path('fed').write_text('{broken')
                self.assertIsNone(brief.read_cache('fed'))
                brief.clear_cache()
                self.assertFalse(brief.cache_path('fed').exists())

    def test_fixture_commercial_sources_are_headline_only(self):
        fixture = json.loads((brief.ROOT / 'fixtures/public-feeds.json').read_text())
        report = brief.make_report('us', NOW, fixture)
        cards = [item for cards in report['sections'].values() for item in cards]
        commercial = [item for item in cards if item['source'] == 'bbc']
        self.assertTrue(report['synthetic_fixture'])
        self.assertTrue(commercial)
        self.assertTrue(all(item['summary'] == '' for item in commercial))
        self.assertTrue(all(item['access'] == 'headline_only' for item in commercial))

    def test_failed_source_cannot_retain_unbounded_old_items(self):
        source = {'id': 'fed', 'last_success_timestamp': NOW.isoformat()}
        item = {'title': 'Old policy', 'source': 'fed', 'summary': '', 'link': 'https://www.federalreserve.gov/',
                'publication_timestamp': (NOW - timedelta(days=30)).isoformat()}
        prior = {'publication_timestamp': NOW.isoformat(), 'sources': [source], 'sections': {'rates': [item]}}
        with patch.object(brief, 'load_latest_report', return_value=prior), patch.object(brief, 'fetch_feed', side_effect=lambda key: {'source': key, 'status': 'failed', 'items': [], 'error': 'Offline'}), patch.object(brief, 'calendar_status', return_value={'status': 'unknown'}):
            report = brief.make_report('us', NOW)
        self.assertFalse(any(report['sections'].values()))
        self.assertEqual(report['freshness'], 'no_successful_sources')
        self.assertFalse(any(source['retained_last_good'] for source in report['sources']))

    def test_nse_ignores_holidays_for_other_segments(self):
        payload = {'CM': [{'tradingDate': '26-Jan-2026'}],
                   'FO': [{'tradingDate': '27-Jan-2026'}]}
        self.assertEqual(brief.nse_payload_status(payload, date(2026, 1, 27))['status'], 'trading_day')
        self.assertEqual(brief.nse_payload_status({'FO': payload['FO']}, date(2026, 1, 27))['status'], 'unknown')

    def test_feed_parser_does_not_publish_commercial_excerpts(self):
        raw = b'<rss><channel><item><title>Oil markets</title><link>https://www.bbc.com/news/business</link><pubDate>Mon, 23 Nov 2026 12:00:00 GMT</pubDate><description>Commercial text that must never be published</description></item></channel></rss>'
        response = io.BytesIO(raw)
        response.headers = {}
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        with patch.object(brief, 'read_cache', return_value=None), patch.object(brief, 'write_cache'), patch.object(brief, 'validate_public_url', side_effect=lambda url: url), patch.object(brief.urllib.request, 'build_opener', return_value=opener):
            result = brief.fetch_feed('bbc')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['items'][0]['summary'], '')
        self.assertEqual(result['items'][0]['access'], 'headline_only')

    def test_xml_entities_are_rejected_without_cached_fallback(self):
        raw = b'<!DOCTYPE rss [<!ENTITY secret SYSTEM "file:///etc/passwd">]><rss><channel/></rss>'
        config = copy.deepcopy(brief.CONFIG)
        config['limits']['retries'] = 0
        response = io.BytesIO(raw)
        response.headers = {}
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        with patch.object(brief, 'CONFIG', config), patch.object(brief, 'read_cache', return_value=None), patch.object(brief, 'read_cache_fallback', return_value=None), patch.object(brief, 'validate_public_url', side_effect=lambda url: url), patch.object(brief.urllib.request, 'build_opener', return_value=opener):
            result = brief.fetch_feed('fed')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['items'], [])

    def test_cache_expiry_and_fallback_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            config = copy.deepcopy(brief.CONFIG)
            config['cache']['directory'] = directory
            with patch.object(brief, 'CONFIG', config):
                for age, allowed_fallback in [(2, True), (15, False)]:
                    payload = {'parser_version': config['cache']['parser_version'],
                               'feed_url': brief.SOURCES['fed']['feed'],
                               'timestamp': (datetime.now(timezone.utc) - timedelta(days=age)).isoformat(),
                               'items': [{'title': 'Cached sample'}]}
                    brief.cache_path('fed').write_text(json.dumps(payload))
                    self.assertIsNone(brief.read_cache('fed'))
                    self.assertEqual(brief.read_cache_fallback('fed') is not None, allowed_fallback)

    def test_fixture_cannot_overwrite_published_docs(self):
        result = subprocess.run([sys.executable, str(brief.ROOT / 'src/build_briefs.py'),
            '--edition', 'us', '--fixture', str(brief.ROOT / 'fixtures/public-feeds.json'),
            '--at', NOW.isoformat(), '--output-dir', str(brief.ROOT / 'docs')],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Synthetic fixtures cannot write into published docs', result.stderr)

    def test_replay_rejects_timezone_naive_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(brief.ROOT / 'src/build_briefs.py'),
                '--edition', 'us', '--fixture', str(brief.ROOT / 'fixtures/public-feeds.json'),
                '--at', '2026-11-24T08:15:00', '--output-dir', directory],
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('timezone', result.stderr)
            self.assertFalse(any(Path(directory).iterdir()))

    def test_legacy_commercial_fallback_cannot_leak_excerpts(self):
        last_success = (NOW - timedelta(hours=1)).isoformat()
        source = {'id': 'bbc', 'last_success_timestamp': last_success}
        item = {'title': 'Economy policy update', 'source': 'bbc', 'source_name': 'BBC Business',
                'summary': 'Legacy commercial excerpt must be removed',
                'link': 'https://www.bbc.com/news/business',
                'access': 'extractive_excerpt', 'publication_timestamp': last_success}
        prior = {'publication_timestamp': last_success, 'sources': [source],
                 'sections': {'overview': [item]}}
        with patch.object(brief, 'load_latest_report', return_value=prior), patch.object(brief, 'fetch_feed', side_effect=lambda key: {'source': key, 'status': 'failed', 'items': [], 'error': 'Offline'}), patch.object(brief, 'calendar_status', return_value={'status': 'unknown'}):
            report = brief.make_report('us', NOW)
        cards = [item for section in report['sections'].values() for item in section]
        self.assertTrue(cards)
        self.assertTrue(all(card['summary'] == '' for card in cards))
        self.assertTrue(all(card['access'] == 'headline_only' for card in cards))
        self.assertEqual(report['freshness'], 'prior_good_retained')
        source_status = next(source for source in report['sources'] if source['id'] == 'bbc')
        self.assertTrue(source_status['retained_last_good'])
        self.assertEqual(source_status['last_success_timestamp'], last_success)

    def test_config_rejects_duplicate_source_ids_without_network(self):
        config = copy.deepcopy(brief.CONFIG)
        config['sources'].append(dict(config['sources'][0]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(config))
            with patch.object(brief.socket, 'getaddrinfo', side_effect=AssertionError('Config validation must remain offline')):
                with self.assertRaisesRegex(ValueError, 'Duplicate source id'):
                    brief.load_config(path)

    def test_changed_endpoint_invalidates_cached_content(self):
        with tempfile.TemporaryDirectory() as directory:
            config = copy.deepcopy(brief.CONFIG)
            config['cache']['directory'] = directory
            with patch.object(brief, 'CONFIG', config):
                brief.write_cache('fed', [{'title': 'Cached sample'}])
                sources = copy.deepcopy(brief.SOURCES)
                sources['fed']['feed'] = 'https://www.federalreserve.gov/feeds/different.xml'
                with patch.object(brief, 'SOURCES', sources):
                    self.assertIsNone(brief.read_cache('fed'))
                    self.assertIsNone(brief.read_cache_fallback('fed'))


if __name__ == '__main__':
    unittest.main()
