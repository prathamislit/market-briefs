# Market Briefs

Public-source market briefs for India and the United States.

- [Repository](https://github.com/prathamislit/market-briefs)
- [Dashboard address](https://prathamislit.github.io/market-briefs/)
- [Workflows](https://github.com/prathamislit/market-briefs/actions)

The dashboard address requires successful GitHub Pages deployment.
Workflow results establish current deployment and collection status.

## What it produces

Each edition creates timestamped JSON and Markdown reports.
A static dashboard displays reports, source health, and archives.
Sections cover macro, rates, FX, energy, commodities, and earnings.
India, US, overview, and geopolitics filters are also available.
Keyword rules classify items; no model generates investment analysis.
Identical normalized headlines retain their supporting source links.

Every report identifies coverage gaps and source retrieval failures.
Commercial feeds supply headlines and original-source links only.
Configured primary sources may include bounded feed excerpts.
The collector does not download full articles or bypass paywalls.

## Editions and schedules

| Edition | Weekday target | Trading calendar |
| --- | --- | --- |
| India | 08:15 Asia/Kolkata | Official NSE equity holiday API |
| United States | 08:15 America/New_York | NYSE calendar through pandas-market-calendars |

US scheduling follows New York daylight-saving transitions.
GitHub can delay scheduled workflow execution.
The collector accepts starts between 08:15 and 09:45 locally.
It never publishes scheduled editions before the target time.
Weekends and confirmed holidays are skipped.
An unavailable calendar cannot establish an eligible trading session.
In-window calendar failure exits unsuccessfully without creating an edition.
Its diagnostic appears in the workflow logs.
Manual recovery can bypass the scheduling and trading-day guards.
Reports still display their calendar status.

Scheduling runs from the default branch, `main`.
Public-repository inactivity can disable GitHub's scheduled workflows.
Check Actions periodically and re-enable disabled schedules when needed.

## Local setup

Use Python 3.12 for live collection and exchange calendars.
The direct calendar dependency is pinned in `requirements.txt`.
Transitive dependencies are resolved during installation.

```sh
git clone https://github.com/prathamislit/market-briefs.git
cd market-briefs
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/reproduce.py
```

Normal collection obeys the same schedule guards:

```sh
python src/build_briefs.py --edition india
python src/build_briefs.py --edition us
```

Manual collection intentionally bypasses those guards:

```sh
python src/build_briefs.py --edition india --force
python src/build_briefs.py --edition us --force
```

Both commands write into `docs/` by default.
Successful collection requires reachable configured public feed endpoints.
The calendar dependency is required for live US calendar verification.

Serve the dashboard over HTTP; opening files directly prevents fetching:

```sh
python -m http.server 8000 --directory docs
```

Then open <http://localhost:8000/>.

## Reproduce without network access

Fixture replay uses Python's standard library and system timezone data.
It requires no package installation, credentials, or network requests.

```sh
python3 scripts/reproduce.py
```

This command builds both editions twice in fresh temporary directories.
It compares SHA-256 hashes for six generated files.
A successful comparison demonstrates deterministic fixture output.
It does not establish live feed availability or publication success.
Changing live feeds cannot reproduce historical live reports exactly.

Generate an inspectable synthetic edition with a fixed clock:

```sh
python3 src/build_briefs.py \
  --edition india \
  --fixture fixtures/public-feeds.json \
  --at 2026-11-24T02:45:00+00:00 \
  --output-dir replay-output
python3 -m http.server 8001 --directory replay-output
```

Fixture reports carry `synthetic_fixture: true`.
Synthetic output must use a separate directory.
The collector rejects fixture writes into the published `docs/` directory.
`--at` requires fixture mode and a timezone offset.

## GitHub automation setup

The repository includes ordinary GitHub Actions workflows.
No Copilot automation, premium model call, or paid API runs.
Live publication uses GitHub's standard Ubuntu runner.
The source repository must remain public for public-repository runner allowances.
GitHub's account limits and policies still apply.

1. Push these files to the repository's default branch.
2. Open **Settings → Pages**.
3. Set **Source** to **GitHub Actions**.
4. Open **Actions → Publish market briefs**.
5. Choose **Run workflow**, then select an edition.
6. Verify both build and deployment jobs succeed.
7. Open the dashboard after successful deployment.

The publishing workflow installs dependencies and runs regression checks.
It verifies fixture reproducibility before collecting live reports.
Action references use verified, immutable upstream commit SHAs.
A shared concurrency group serializes publishing runs.
Publishing refuses runs from a non-default branch.
Holiday or unchanged runs skip unnecessary dashboard deployment.
Generated reports are committed back into `docs/`.
Pages deploys the corresponding generated dashboard artifact.
A separate verification workflow checks pushes and pull requests.

`GITHUB_TOKEN` provides narrowly declared repository and Pages permissions.
No personal access token or market-data key is required.
Forks require their own Actions and Pages configuration.

## Configuration and data boundaries

`config/default.json` controls sources, retrieval limits, and caching.
Use `--config PATH` to supply another complete configuration.
Edition timezone presets document the intended schedules.
Workflow cron entries remain in `.github/workflows/build.yml`.
Changing a preset alone does not change GitHub's schedule.

| Default bound | Value |
| --- | --- |
| Concurrent source fetches | 4 |
| Timeout per fetch attempt | 12 seconds |
| Retries after initial attempt | 2 |
| Redirects | 3 |
| Feed or cache entry size | 2,000,000 bytes |
| Feed entries per source | 40 |
| Primary-source summary length | 4,000 characters |
| Fresh cache lifetime | 24 hours |
| Last-good cache fallback lifetime | 14 days |
| Publication retention window | 7 days |
| Cache entries | 366 |
| Dashboard manifest entries | 366 |

The manifest bound limits the displayed archive.
Previously committed report files remain in repository history.
The local cache is excluded from Git commits.
Fresh GitHub runners do not inherit earlier local cache files.
Previously committed reports provide an additional bounded fallback.
Retained items are explicitly labeled; their retrieval is not fresh.
Future-dated and out-of-window publications are rejected.
Unknown publication dates are shown explicitly.

## Source health and failures

| Status | Meaning |
| --- | --- |
| `ok` | Endpoint returned usable entries |
| `no_items` | Endpoint returned no usable entries |
| `failed` | Collection failed after bounded attempts |
| `unconfigured` | No public feed endpoint is configured |

SEBI currently has no configured public feed endpoint.
Other endpoints can block requests, change, or become unavailable.
Each report records attempt time, last success, and diagnostics.
Source success does not guarantee recent, comprehensive article coverage.
`no_recent_items` means successful collection found no in-window items.
That condition alone does not mark collection unsuccessful.
A stale source can retain previously collected, labeled material.

An edition without fresh successful sources exits unsuccessfully.
This includes editions containing only retained last-good material.
Its report still records explicit degradation and source diagnostics.
The publisher continues collecting the other selected edition.
It publishes successful editions alongside available degraded diagnostic reports.
After deployment, any collection failure marks the overall workflow unsuccessful.
Tests or reproducibility failures stop publication before live collection.
Report commits precede deployment.
Failed deployment leaves committed reports and the prior live dashboard.
Inspect Actions logs and source diagnostics before retrying manually.

## Limits

This is a feed-based briefing and archive tool.
It cannot establish every catalyst or explain every market move.
It does not promise complete policy, earnings, or paywalled coverage.
It includes no guaranteed real-time prices or consensus estimates.
Headline deduplication does not identify every overlapping story.
Calendar data can fail or omit exceptional exchange sessions.
Manual forced runs can occur outside eligible trading sessions.
Always inspect report dates, provenance, and source-health indicators.
