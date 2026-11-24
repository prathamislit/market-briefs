"""Bounded public market observations with explicit missing/stale states.

FRED is a Federal Reserve Bank aggregator. Each series identifies its original
agency. This module makes no claim of live quotes or market causation.
"""
import csv
import io
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config/markets.json'
MAX_BYTES = 160_000


def read_public_csv(url, timeout=10):
    request = urllib.request.Request(url, headers={'User-Agent': 'market-briefs/1.1 (public FRED series reader)', 'Accept': 'text/csv'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError('FRED response exceeded byte limit')
    return payload.decode('utf-8-sig')


def parse_series_csv(raw, series_id, cutoff):
    """Keep the last two numeric observations at or before the report cutoff."""
    rows = csv.DictReader(io.StringIO(raw))
    fields = set(rows.fieldnames or ())
    date_field = 'observation_date' if 'observation_date' in fields else 'DATE'
    if date_field not in fields or series_id not in fields:
        raise ValueError('Unexpected FRED CSV columns')
    values = []
    for row in rows:
        try:
            observed = date.fromisoformat(row[date_field])
            if observed > cutoff:
                continue
            value = float(row[series_id])
            if value != value or abs(value) == float('inf'):
                continue
            values.append((observed, value))
        except (KeyError, TypeError, ValueError):
            continue
    values.sort(key=lambda pair: pair[0])
    return values[-2:]


def collect_market_data(now, fetcher=None, config_path=CONFIG):
    """Return JSON-ready assets; failed requests never supply invented levels."""
    if now.tzinfo is None:
        raise ValueError('Report time must be timezone-aware')
    now = now.astimezone(timezone.utc)
    cfg = json.loads(Path(config_path).read_text())
    if cfg.get('schema_version') != 1:
        raise ValueError('Unsupported markets schema')
    fetcher = fetcher or read_public_csv
    cutoff = now.date()

    def collect(spec):
        series_id = spec['id']
        if not re.fullmatch(r'[A-Z0-9]{3,24}', series_id):
            raise ValueError('Unsafe FRED series ID')
        url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={(cutoff - timedelta(days=21)).isoformat()}&coed={cutoff.isoformat()}'
        result = {
            'name': spec['name'], 'region': spec['region'], 'unit': spec['unit'],
            'source': cfg['provider'], 'original_source': spec['source'],
            'source_url': f'https://fred.stlouisfed.org/series/{series_id}',
            'data_url': url,
            'retrieved_at': now.isoformat(), 'level': None, 'change': None,
            'change_percent': None, 'as_of': None, 'status': 'unavailable',
            'note': 'No verified observation was available.'
        }
        try:
            observations = parse_series_csv(fetcher(url), series_id, cutoff)
            if not observations:
                return result
            observed, value = observations[-1]
            age = (cutoff - observed).days
            result.update({'level': value, 'as_of': observed.isoformat(),
                           'status': 'ok' if age <= spec['max_age_days'] else 'stale',
                           'note': f'{age} calendar days old; delayed agency observation.'})
            if len(observations) == 2:
                previous_date, previous = observations[0]
                result['previous_as_of'] = previous_date.isoformat()
                result['change'] = round(value - previous, 6)
                if previous:
                    result['change_percent'] = round((value / previous - 1) * 100, 4)
            return result
        except (OSError, TimeoutError, ValueError, UnicodeError) as exc:
            result['note'] = f'Data unavailable ({type(exc).__name__}); inspect original source.'
            return result

    with ThreadPoolExecutor(max_workers=4) as pool:
        assets = list(pool.map(collect, cfg['series']))
    for missing in cfg['unavailable']:
        assets.append({**missing, 'unit': None, 'source': None, 'original_source': None,
                       'source_url': None, 'data_url': None, 'retrieved_at': now.isoformat(),
                       'level': None, 'change': None, 'change_percent': None,
                       'as_of': None, 'status': 'unavailable'})
    return {'retrieved_at': now.isoformat(), 'assets': assets,
            'limitations': ['FRED values may lag their original agencies. Observation dates, not retrieval time, determine freshness.',
                            'Percent changes compare the two most recent valid observations, which may span holidays.',
                            'US noon FX rates are neither current Asian closes nor executable quotes.']}
