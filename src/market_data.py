"""Bounded public market observations with explicit missing/stale states.

FRED is a Federal Reserve Bank aggregator. Each series identifies its original
agency. This module makes no claim of live quotes or market causation.
"""
import csv
import calendar
import io
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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


def parse_treasury_xml(raw, cutoff):
    """Read dated par yields from Treasury's Atom/OData monthly feed."""
    root = ET.fromstring(raw)
    values = {field: [] for field in ('BC_2YEAR', 'BC_10YEAR', 'BC_30YEAR')}
    for entry in root.iter():
        if entry.tag.rsplit('}', 1)[-1] != 'entry':
            continue
        properties = next((node for node in entry.iter()
                           if node.tag.rsplit('}', 1)[-1] == 'properties'), None)
        if properties is None:
            continue
        fields = {node.tag.rsplit('}', 1)[-1]: node.text for node in properties}
        try:
            observed = date.fromisoformat((fields['NEW_DATE'] or '')[:10])
        except (KeyError, ValueError):
            continue
        if observed > cutoff:
            continue
        for field in values:
            try:
                value = float(fields[field])
                if value == value and abs(value) != float('inf'):
                    values[field].append((observed, value))
            except (KeyError, TypeError, ValueError):
                continue
    return {field: sorted(rows)[-2:] for field, rows in values.items()}


def federal_holidays(year):
    """Observed U.S. federal holidays relevant to agency releases."""
    def observed(day):
        return day - timedelta(days=1) if day.weekday() == 5 else (
            day + timedelta(days=1) if day.weekday() == 6 else day)

    def nth(month, weekday, number):
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (number - 1))

    def last(month, weekday):
        end = date(year, month, calendar.monthrange(year, month)[1])
        return end - timedelta(days=(end.weekday() - weekday) % 7)

    fixed = [date(year, month, day) for month, day in
             ((1, 1), (6, 19), (7, 4), (11, 11), (12, 25))]
    moving = [nth(1, 0, 3), nth(2, 0, 3), last(5, 0), nth(9, 0, 1),
              nth(10, 0, 2), nth(11, 3, 4)]
    return {observed(day) for day in fixed} | set(moving)


def business_days_since(observed, cutoff):
    holidays = set().union(*(federal_holidays(year)
                            for year in range(observed.year - 1, cutoff.year + 2)))
    return sum((day := observed + timedelta(days=offset)).weekday() < 5 and day not in holidays
               for offset in range(1, (cutoff - observed).days + 1))


def collect_market_data(now, fetcher=None, config_path=CONFIG):
    """Return JSON-ready assets; failed requests never supply invented levels."""
    if now.tzinfo is None:
        raise ValueError('Report time must be timezone-aware')
    now = now.astimezone(timezone.utc)
    cfg = json.loads(Path(config_path).read_text())
    if cfg.get('schema_version') != 1:
        raise ValueError('Unsupported markets schema')
    fetcher = fetcher or read_public_csv
    cutoff = now.astimezone(ZoneInfo('America/New_York')).date()

    treasury = {field: [] for field in ('BC_2YEAR', 'BC_10YEAR', 'BC_30YEAR')}
    treasury_urls = []
    if any(spec.get('treasury_field') for spec in cfg['series']):
        for month in (cutoff.replace(day=1) - timedelta(days=1), cutoff):
            url = ('https://home.treasury.gov/resource-center/data-chart-center/'
                   'interest-rates/pages/xml?data=daily_treasury_yield_curve'
                   f'&field_tdr_date_value_month={month:%Y%m}')
            try:
                rows = parse_treasury_xml(fetcher(url), cutoff)
                for field in treasury:
                    treasury[field].extend(rows[field])
                treasury_urls.append(url)
            except (ET.ParseError, OSError, TimeoutError, ValueError, UnicodeError):
                continue
        treasury = {field: sorted(set(rows))[-2:] for field, rows in treasury.items()}

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
            try:
                observations = parse_series_csv(fetcher(url), series_id, cutoff)
            except (OSError, TimeoutError, ValueError, UnicodeError):
                observations = []
            field = spec.get('treasury_field')
            direct = treasury.get(field, []) if field else []
            if direct and (not observations or direct[-1][0] >= observations[-1][0]):
                observations = direct
                result.update(source='U.S. Department of the Treasury',
                              original_source='Daily Treasury par yield curve',
                              source_url='https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve',
                              data_url=treasury_urls[-1] if treasury_urls else url)
            if not observations:
                return result
            observed, value = observations[-1]
            age = (cutoff - observed).days
            weekdays = business_days_since(observed, cutoff)
            cadence = spec.get('cadence', 'daily')
            threshold = spec.get('max_age_business_days', spec.get('max_age_days', 1))
            status = ('stale' if weekdays > threshold else
                      'reference' if cadence == 'weekly_reference' or spec.get('reference_only') else 'ok')
            note = (f'{age} calendar days old ({weekdays} US federal business days); '
                    + ('weekly H.10 release; historical reference, not a current quote.'
                       if cadence == 'weekly_reference' else
                       'agency spot reference, not a current quote.' if spec.get('reference_only') else
                       'source observation, not a live yield.'))
            if status == 'stale':
                note += ' Observation exceeds expected publication cadence; check the linked agency.'
            if field and result['source'] != 'U.S. Department of the Treasury':
                note += ' Treasury direct feed unavailable; using Fed H.15 constant-maturity series via FRED.'
            result.update({'level': value, 'as_of': observed.isoformat(),
                           'status': status, 'note': note})
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
            'limitations': ['Treasury direct daily yields use FRED only as a fallback. Federal Reserve H.10 FX releases weekly; EIA spot series may lag. Observation dates determine freshness.',
                            'Percent changes compare the two most recent valid observations, which may span holidays.',
                            'US noon FX rates are neither current Asian closes nor executable quotes.']}
