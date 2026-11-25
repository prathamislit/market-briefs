# Market Briefs

Public-source market briefs for India and the United States.

- [Repository](https://github.com/prathamislit/market-briefs)
- [Dashboard address](https://prathamislit.github.io/market-briefs/)
- [Workflows](https://github.com/prathamislit/market-briefs/actions)

The dashboard address requires successful GitHub Pages deployment.
Workflow results establish current deployment and collection status.

## What it produces

Each edition creates timestamped JSON and Markdown reports.
Daily Markets combines the latest available editions and public observations.
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

| Output | Target | Calendar behavior |
| --- | --- | --- |
| Daily Markets | Daily, 08:15 Asia/Kolkata and 08:15 America/New_York | Uses latest available observations and editions |
| India edition | Trading days, 08:15 Asia/Kolkata | Official NSE equity holiday API |
| United States edition | Trading days, 08:15 America/New_York | NYSE calendar through pandas-market-calendars |

New York schedules follow daylight-saving transitions.
These are execution targets, not guaranteed publication times.
GitHub can delay scheduled workflow execution.
Daily Markets runs on weekends and exchange holidays too.
It preserves underlying edition dates and observation dates.
Each 08:15 run composes a timestamped Daily Markets report.

Edition collectors accept starts between 08:15 and 09:45 locally.
They never publish scheduled editions before the target time.
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

Compose and archive Daily Markets separately:

```sh
python src/orchestrate_daily.py
```

This uses the latest saved editions and current public inputs.
Missing inputs remain explicit; the composer does not invent evidence.

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

## Daily Markets evidence

Daily Markets separates observations, headline leads, and unavailable evidence.
Every observation includes its source and observation date.
Retrieval time never substitutes for the underlying data date.

| Desk | Included evidence |
| --- | --- |
| Cross-asset dashboard | Configured dated public market observations |
| ETF flows and positioning | Delayed SEC fund disclosures; daily gaps remain explicit |
| Macro and policy | Relevant public-source headline leads |
| Geopolitics | Conditional mechanisms, attributed events, and unresolved causality |
| Companies and earnings | Selected SEC actuals and narrowly matched issuer expectations |
| United States policy and currency | Available observations and policy leads |
| India markets | Available observations, relevant leads, and explicit gaps |
| What matters next | Dated official events when collection succeeds |
| Source register | Public source status, provenance, and diagnostics |

`config/markets.json` configures ten public FRED CSV series.
Original agencies remain identified alongside FRED distribution links.
These are dated observations, not real-time market quotes.

The dashboard also embeds a TradingView-hosted market-data table.
It displays six US/global index references and five BSE indices.
Four indicative currency pairs and six commodity CFD proxies follow.
SPY, GLD, SLV, and USO show ETF share prices separately.
The US 500 CFD moves outside regular exchange hours.
It is not the official S&P 500 closing level.
The dashboard links directly to FRED's dated official close.
CFD quotes differ from official indices and agency spot observations.
BSE indices are end of day; ETF quotes may be delayed.
Nifty and VIX link to their official source pages.
The widget updates through TradingView; no 30-second guarantee exists.
TradingView attribution remains visible.
Its values cannot enter the reproducible report JSON or historical archives.

| Observation group | Configured series |
| --- | --- |
| US Treasury yields | 2-year, 10-year, and 30-year |
| Exchange rates | USD/INR, USD/CNY, USD/JPY, and EUR/USD |
| Energy spot prices | WTI, Brent, and Henry Hub natural gas |

Rates and FX become stale after five calendar days.
Energy observations become stale after ten calendar days.
Age is measured against the UTC report date.
Changes compare the latest two numeric observations.
Those observations can span holidays and missing-data intervals.
Yield changes use percentage points, not percentage returns.

Unavailable groups remain visible in the report.
These include equity closes, credit, VIX, crypto, and metals.
Daily ETF flows and positioning need another verified source.
India indices, breadth, movers, and FII/DII remain explicitly unavailable.
Headlines cannot establish actual levels, consensus, or market reactions.
A relevant lead does not establish a verified causal mechanism.
No private email content or metadata enters these public reports.

### Delayed SEC fund flows and holdings

`src/sec_etf.py` collects public N-PORT disclosures for IVV.
The configured fund series is `S000004310`.
It checks at most four series-matched filing candidates.
A matching disclosure can report three monthly fund-activity periods.
Net activity equals share sales plus reinvestments minus redemptions.
These values describe reported fund activity, not daily exchange flows.
Five largest disclosed equity holdings show USD and NAV percentages.
Report dates, filing dates, and reporting lag remain visible.

Coverage is deliberately limited to the configured fund.
It does not measure the whole ETF universe or positioning.
Delayed filings cannot establish today's fund demand.
Missing disclosures and SEC access failures remain explicit.
Last-good archived evidence can remain visibly dated when retrieval fails.
No ICI statistical table is republished.

`SEC_USER_AGENT` supplies an identifying SEC request header.
Set the repository secret to a real project contact for GitHub Actions.
The default repository URL may be rejected by SEC access controls.
The contact identifies research requests; it grants no market-data rights.

### Earnings actuals and expectations

`src/earnings_data.py` reads SEC submissions and company facts.
Tracked issuers are AAPL, MSFT, NVDA, AMZN, JPM, XOM, TSLA.
Coverage uses recent 10-Q or amended 10-Q filings.
The latest 10-Q filing window spans at most 120 calendar days.
Historical filings show their filing age and may predate newer results.
Quarterly GAAP revenue and diluted EPS require matching filing evidence.
Facts must match the filing's accession and reporting period.
Revenue displays USD millions; diluted EPS displays USD/share.
Annual reports and comprehensive earnings-release coverage are not included.

Tesla GAAP EPS comparisons require matched issuer-published consensus.
Its publication must precede the verified issuer results release.
The reporting quarter and GAAP basis must match.
One audited Q3 2026 Tesla EPS snapshot supports reproducible fallback.
It links the October 19 consensus and October 21 SEC 8-K.
The fallback requires matching SEC 8-K and 10-Q identifiers.
Unreachable live issuer pages remain visible retrieval failures.
Unverified comparisons remain actual-only, with expectations unavailable.
Revenue expectations are currently omitted because units remain unverified.
Broad analyst consensus, company guidance, and price reactions remain unavailable.
Each actual and accepted expectation retains its primary-source link.

### Conditional market-transmission hypotheses

`src/transmission.py` applies narrow rules to primary-source evidence.
Supported channels cover FOMC policy and Hormuz oil-transit disruption.
The event must have a recent, dated primary-source excerpt.
The source host and relevant action evidence must match.
Commercial or generic headlines cannot activate these rules.

Each hypothesis links the event and documented transmission mechanism.
Potential affected assets retain their observation dates and sources.
Daily observations can show temporal overlap with an event.
They cannot isolate the event's price effect.
The report identifies rival explanations and unresolved event attribution.
No directional forecast or verified causal effect is implied.
Unmatched events leave the mechanism section explicitly incomplete.

### India data reuse blockers

India index levels, breadth, movers, and FII/DII remain unavailable.
A permitted source and its redistribution terms must be established.
[NSE's data policy](https://www.nseindia.com/static/market-data/nse-data-policy) governs relevant usage and redistribution.
Subscriber redistribution depends on the relevant agreement.
Public website access alone does not establish redistribution permission.
This project has not established the required reuse rights.
BSE/Sensex coverage also needs a separately verified reusable source.
RBI news and public FX use separate configured sources.
No unofficial scraped index values fill these missing fields.

### Official release watchlist

The watchlist reads official BEA and BLS release calendars.
It selects upcoming events within the next 72 hours.
Timed events retain their exact UTC timestamps.
Date-only events retain dates without fabricated release times.
Each calendar failure remains explicit; other calendars can still contribute.
Earnings calendars and other agencies are not comprehensively covered.
No calendar configuration or authentication is required.

### Offline component checks

Run the added components' offline regression tests:

```sh
python3 -m unittest discover -s tests -p 'test_market_data.py' -v
python3 -m unittest discover -s tests -p 'test_earnings_data.py' -v
python3 -m unittest discover -s tests -p 'test_sec_etf.py' -v
python3 -m unittest discover -s tests -p 'test_transmission.py' -v
python3 -m unittest discover -s tests -p 'test_daily_report.py' -v
python3 -m unittest discover -s tests -p 'test_watchlist.py' -v
python3 -m unittest discover -s tests -p 'test_orchestrate_daily.py' -v
```

Tests inject public-data fixtures instead of calling live providers.
The existing reproducibility command still checks fixed-input edition output.

## Archives

| Path | Contents |
| --- | --- |
| `docs/reports/` | Latest edition files for each market date |
| `docs/reports/manifest.json` | Bounded edition-date dashboard index |
| `docs/runs/` | Separate live-edition JSON snapshots for generated reports |
| `docs/runs/manifest.json` | Edition-run archive index |
| `docs/runs/workflow-*.json` | Scheduled, manual, and push-attempt step outcomes |
| `docs/runs/workflow-manifest.json` | Workflow-attempt archive index |
| `docs/daily/` | Timestamped Daily Markets JSON and Markdown reports |
| `docs/daily/manifest.json` | Daily Markets archive index |

Repeated same-day reports keep separate archived versions.
Archive filenames include timestamps, run identifiers, and attempt numbers.
Skipped edition steps appear in archived workflow-attempt records.
Early runner failures can only appear in GitHub Actions logs.
Fixture replay does not enter the live-edition archive.
Published JSON preserves the report and its source register.
It does not preserve every upstream response verbatim.
Exact historical live recomposition is therefore not promised.
Fixture replay verifies deterministic behavior for its fixed inputs.

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
It targets 08:15 India and Eastern daily, including weekends.
Edition collectors still require a verified local trading session.
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

`config/default.json` controls news sources, retrieval limits, and caching.
`config/markets.json` controls public market-observation series.
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
| Edition-date dashboard manifest entries | 366 |

The edition-date manifest bound limits that dashboard index.
Per-run and daily archive manifests remain separate.
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
Public market series can lag their underlying observation dates.
Headline deduplication does not identify every overlapping story.
Calendar data can fail or omit exceptional exchange sessions.
Manual forced runs can occur outside eligible trading sessions.
Always inspect report dates, provenance, and source-health indicators.
