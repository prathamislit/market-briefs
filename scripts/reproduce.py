#!/usr/bin/env python3
"""Verify byte-identical offline builds across separate output directories."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOCK = '2026-11-24T02:45:00+00:00'
EXPECTED_DATES = {'india': '2026-11-24', 'us': '2026-11-23'}


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def validate_output(output):
    """Reject matching builds that omit an edition or mislabel samples."""
    manifest = json.loads((output / 'reports/manifest.json').read_text())
    if {(entry['edition'], entry['date']) for entry in manifest} != set(EXPECTED_DATES.items()):
        raise SystemExit('FAIL: manifest editions or local dates differ')
    for edition, session in EXPECTED_DATES.items():
        report = json.loads((output / f'reports/{edition}-{session}.json').read_text())
        if not report.get('synthetic_fixture') or report['publication_timestamp'] != CLOCK:
            raise SystemExit('FAIL: synthetic provenance or fixed clock missing')
        cards = [card for section in report['sections'].values() for card in section]
        if not cards or any(not card['title'].startswith('SYNTHETIC:') for card in cards):
            raise SystemExit('FAIL: fixture content missing or incorrectly labeled')
        pages = ('index', 'markets', 'macro', 'india', 'etfs', 'geopolitics',
                 'earnings', 'watchlist', 'archives', 'sources')
        for page in pages:
            html = (output / f'{page}.html').read_text()
            if any(token in html for token in ('__MANIFEST__', '__PAGE__', '__PAGE_CONTENT__')):
                raise SystemExit(f'FAIL: {page} contains an unresolved template')


def main():
    with tempfile.TemporaryDirectory() as directory:
        outputs = [Path(directory) / name for name in ('first', 'second')]
        for seed, output in enumerate(outputs, start=1):
            environment = dict(os.environ, PYTHONHASHSEED=str(seed))
            for edition in EXPECTED_DATES:
                subprocess.run([sys.executable, str(ROOT / 'src/build_briefs.py'),
                    '--edition', edition, '--fixture', str(ROOT / 'fixtures/public-feeds.json'),
                    '--at', CLOCK, '--output-dir', str(output)],
                    env=environment, check=True, timeout=30)
            validate_output(output)
        first, second = map(snapshot, outputs)
        if first != second or len(first) != 15:
            raise SystemExit('FAIL: offline builds differ or outputs are missing')
        print('PASS: fifteen output files reproduced byte-for-byte')
        for path, digest in sorted(first.items()):
            print(f'{digest}  {path}')


if __name__ == '__main__':
    main()
