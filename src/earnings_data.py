"""Narrow, source-matched earnings actuals and issuer-published expectations.

SEC XBRL facts are reusable public filings. Analyst consensus is included only
when an issuer publishes a dated, pre-results snapshot for the same period.
"""

import json
import math
import os
import re
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit


TICKERS = ("AAPL", "MSFT", "NVDA", "AMZN", "JPM", "XOM", "TSLA")
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_ROOT = "https://data.sec.gov"
TESLA_ROOT = "https://ir.tesla.com"
MAX_BYTES = 24_000_000
REVENUE_TAGS = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax")
METRICS = (("Revenue", REVENUE_TAGS, "USD", "USD millions", 1_000_000),
           ("Diluted EPS", ("EarningsPerShareDiluted",), "USD/shares", "USD/share", 1))
QUARTERS = {3: ("first", "Q1"), 6: ("second", "Q2"),
            9: ("third", "Q3"), 12: ("fourth", "Q4")}
# Dated, issuer-published Q3 2026 GAAP EPS average. Verified against Tesla IR
# and its 2026-10-21 SEC Item 2.02 filing. No extrapolation to other quarters.
TESLA_Q3_2026 = {
    "cik": 1318605, "period_start": "2026-07-01", "period_end": "2026-09-30",
    "filed": "2026-10-22",
    "quarterly_accession": "0001628280-26-066775",
    "results_accession": "0001628280-26-066738", "results_date": "2026-10-21",
    "results_url": "https://www.sec.gov/Archives/edgar/data/1318605/000162828026066738/tsla-20261021.htm",
    "consensus_date": "2026-10-19", "gaap_eps_mean": 0.23,
    "consensus_url": "https://ir.tesla.com/press-release/earnings-consensus-third-quarter-2026",
}
_last_sec_request = 0.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise ValueError("Unexpected earnings-source redirect")


def fetch_public(url):
    global _last_sec_request
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ("www.sec.gov", "data.sec.gov", "ir.tesla.com"):
        raise ValueError("Unexpected earnings-source endpoint")
    agent = os.environ.get("SEC_USER_AGENT") or "Market Briefs research (https://github.com/prathamislit/market-briefs)"
    request = urllib.request.Request(url, headers={"User-Agent": agent, "Accept": "application/json,text/html"})
    if parsed.hostname in ("www.sec.gov", "data.sec.gov"):
        time.sleep(max(0.0, 0.12 - (time.monotonic() - _last_sec_request)))
        _last_sec_request = time.monotonic()
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=10) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("Earnings source exceeds size limit")
    return payload


def _payload(fetcher, url):
    raw = fetcher(url)
    if isinstance(raw, str):
        raw = raw.encode()
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError("Invalid earnings response")
    return raw


def _tickers(raw):
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Unexpected SEC ticker index")
    found = {}
    for row in data.values():
        if not isinstance(row, dict):
            continue
        ticker = row.get("ticker")
        cik = row.get("cik_str")
        if ticker in TICKERS and isinstance(cik, int) and cik > 0:
            found[ticker] = cik
    return found


def _recent_filing(raw, now, days=120):
    data = json.loads(raw)
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    for idx, form in enumerate(forms):
        if form not in ("10-Q", "10-Q/A"):
            continue
        try:
            filed = date.fromisoformat(recent["filingDate"][idx])
            report_date = date.fromisoformat(recent["reportDate"][idx])
            accession = recent["accessionNumber"][idx]
            document = recent["primaryDocument"][idx]
            if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", document):
                continue
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if now.date() - timedelta(days=days) <= filed <= now.date():
            return {"filed": filed.isoformat(), "accession": accession,
                    "report_date": report_date.isoformat(), "document": document}
    return None


def _facts(raw, accession, report_date):
    data = json.loads(raw)
    gaap = data.get("facts", {}).get("us-gaap", {})
    results = {}
    for label, tags, unit, display_unit, divisor in METRICS:
        for tag in tags:
            rows = gaap.get(tag, {}).get("units", {}).get(unit, [])
            candidates = []
            for row in rows:
                if row.get("accn") != accession or row.get("form") not in ("10-Q", "10-Q/A"):
                    continue
                try:
                    start, end = date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
                    value = float(row["val"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                if (end.isoformat() != report_date or
                        not 65 <= (end - start).days <= 110 or not math.isfinite(value)):
                    continue
                candidates.append((end, start, value))
            if candidates:
                end, start, value = max(candidates)
                results[label] = {"actual": round(value / divisor, 6),
                                  "period_start": start.isoformat(), "period_end": end.isoformat(),
                                  "unit": display_unit, "basis": "GAAP", "tag": tag}
                break
    return results


class _Table(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None
        self.text = []
        self.headings = []
        self.heading = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag == "h1":
            self.heading = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        self.text.append(data)
        if self.heading is not None:
            self.heading.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "h1" and self.heading is not None:
            self.headings.append((" ".join(" ".join(self.heading).split()), len(self.text)))
            self.heading = None


def _page_date(parser, title):
    for heading, end_index in parser.headings:
        if heading.casefold() != title.casefold():
            continue
        following = " ".join(" ".join(parser.text[end_index:]).split())[:250]
        match = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+20\d{2}\b", following)
        if match:
            return datetime.strptime(match.group(), "%b %d, %Y").date()
    return None


def _tesla_results_date(raw, period_end, filed):
    end = date.fromisoformat(period_end)
    if end.month not in QUARTERS:
        return None
    parser = _Table()
    parser.feed(raw.decode("utf-8-sig"))
    title = f"Tesla Releases {QUARTERS[end.month][0].title()} Quarter {end.year} Financial Results"
    published = _page_date(parser, title)
    if published and end < published <= date.fromisoformat(filed):
        return published
    return None


def _tesla_consensus(raw, period_end, results_date):
    parser = _Table()
    parser.feed(raw.decode("utf-8-sig"))
    end = date.fromisoformat(period_end)
    if end.month not in QUARTERS:
        return None
    quarter = QUARTERS[end.month][1]
    published = _page_date(parser, f"{quarter} {end.year} Earnings Consensus")
    if not published or published >= results_date:
        return None
    result = {"published": published.isoformat()}
    if not any(len(row) > 1 and row[1].casefold() == "average" for row in parser.rows):
        return None
    for row in parser.rows:
        if len(row) < 2:
            continue
        label = row[0].casefold()
        # The issuer's consensus table does not label revenue units.
        metric = "Diluted EPS" if label == "eps - gaap" else None
        if metric:
            try:
                value = float(row[1].replace(",", "").replace("$", ""))
            except ValueError:
                continue
            if math.isfinite(value):
                result[metric] = value
    return result


def _tesla_curated_snapshot(submissions, cik, filing, actual):
    """Match one audited issuer snapshot to its exact SEC filing lineage."""
    snapshot = TESLA_Q3_2026
    if (cik != snapshot["cik"] or filing["accession"] != snapshot["quarterly_accession"] or
            filing["report_date"] != snapshot["period_end"] or filing["filed"] != snapshot["filed"] or
            actual.get("period_start") != snapshot["period_start"] or
            actual.get("period_end") != snapshot["period_end"] or
            actual.get("unit") != "USD/share" or actual.get("basis") != "GAAP" or
            not date.fromisoformat(snapshot["consensus_date"]) < date.fromisoformat(snapshot["results_date"])):
        return None
    recent = json.loads(submissions).get("filings", {}).get("recent", {})
    for idx, form in enumerate(recent.get("form", [])):
        try:
            if (form == "8-K" and recent["accessionNumber"][idx] == snapshot["results_accession"] and
                    recent["filingDate"][idx] == snapshot["results_date"] and
                    recent["reportDate"][idx] == snapshot["results_date"]):
                return {"published": snapshot["consensus_date"], "Diluted EPS": snapshot["gaap_eps_mean"],
                        "consensus_url": snapshot["consensus_url"], "results_url": snapshot["results_url"],
                        "evidence": "curated_primary_snapshot"}
        except (IndexError, KeyError, TypeError):
            continue
    return None


def _failure(exc):
    result = {"error": type(exc).__name__}
    if isinstance(exc, ValueError):
        # Only our short validation messages or JSON parser positions appear.
        result["detail"] = str(exc)[:120]
    return result


def collect_earnings(now, fetcher=None):
    """Report recent SEC quarterly actuals; compare matched Tesla issuer consensus."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Earnings report time needs a timezone")
    now = now.astimezone(timezone.utc)
    fetcher = fetcher or fetch_public
    output = {"retrieved_at": now.isoformat(), "entries": [], "failures": [],
              "limitations": ["Coverage is limited to seven U.S. issuers' latest 10-Q within 120 days; older filings are historical context.",
                              "Company guidance requires verified issuer text; reaction needs licensed prices.",
                              "Analyst consensus is unavailable unless published by the issuer before results.",
                              "One curated Tesla Q3 2026 EPS snapshot is tied to its SEC results filing; unavailable live IR fetches remain failures."]}
    try:
        directory = _tickers(_payload(fetcher, TICKERS_URL))
    except (OSError, ValueError, UnicodeError, TypeError) as exc:
        output["failures"].append({"source": "SEC ticker index", "url": TICKERS_URL, **_failure(exc)})
        return output
    for ticker in TICKERS:
        cik = directory.get(ticker)
        if not cik:
            output["failures"].append({"source": ticker, "error": "Ticker missing from SEC index"})
            continue
        filing_url = f"{SEC_ROOT}/submissions/CIK{cik:010d}.json"
        try:
            submissions = _payload(fetcher, filing_url)
            filing = _recent_filing(submissions, now)
            if not filing:
                continue
            facts_url = f"{SEC_ROOT}/api/xbrl/companyfacts/CIK{cik:010d}.json"
            facts = _facts(_payload(fetcher, facts_url), filing["accession"], filing["report_date"])
            if not facts:
                output["failures"].append({"source": ticker, "url": facts_url, "error": "No matching quarterly GAAP facts"})
                continue
            periods = {(fact["period_start"], fact["period_end"]) for fact in facts.values()}
            if len(periods) != 1:
                output["failures"].append({"source": ticker, "url": facts_url, "error": "GAAP facts cover mismatched periods"})
                continue
            source_url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                          f"{filing['accession'].replace('-', '')}/{filing['document']}")
            consensus = None
            consensus_url = None
            results_url = None
            issuer_unavailable = False
            if ticker == "TSLA":
                period = next(iter(facts.values()))["period_end"]
                end = date.fromisoformat(period)
                if end.month in QUARTERS:
                    consensus_url = (f"{TESLA_ROOT}/press-release/earnings-consensus-"
                                     f"{QUARTERS[end.month][0]}-quarter-{end.year}")
                    results_url = (f"{TESLA_ROOT}/press-release/tesla-releases-"
                                   f"{QUARTERS[end.month][0]}-quarter-{end.year}-financial-results")
                    try:
                        published = _tesla_results_date(_payload(fetcher, results_url), period, filing["filed"])
                        if published:
                            try:
                                consensus = _tesla_consensus(_payload(fetcher, consensus_url), period, published)
                            except (OSError, ValueError, UnicodeError, TypeError, KeyError) as exc:
                                issuer_unavailable = isinstance(exc, (OSError, KeyError))
                                output["failures"].append({"source": "Tesla consensus", "url": consensus_url,
                                                           **_failure(exc)})
                        else:
                            output["failures"].append({"source": "Tesla results", "url": results_url,
                                                       "error": "Release date could not be verified"})
                    except (OSError, ValueError, UnicodeError, TypeError, KeyError) as exc:
                        issuer_unavailable = isinstance(exc, (OSError, KeyError))
                        output["failures"].append({"source": "Tesla results", "url": results_url,
                                                   **_failure(exc)})
                    if issuer_unavailable and not consensus and "Diluted EPS" in facts:
                        consensus = _tesla_curated_snapshot(submissions, cik, filing, facts["Diluted EPS"])
                        if consensus:
                            consensus_url = consensus["consensus_url"]
                            results_url = consensus["results_url"]
            for metric, actual in facts.items():
                expected = consensus.get(metric) if consensus else None
                delta = round(actual["actual"] - expected, 6) if expected is not None else None
                output["entries"].append({
                    "kind": "earnings_comparison" if expected is not None else "earnings_actual",
                    "company": ticker, "metric": metric, **actual, "filed": filing["filed"],
                    "filing_age_days": (now.date() - date.fromisoformat(filing["filed"])).days,
                    "historical": filing["filed"] != now.date().isoformat(),
                    "source_url": source_url, "expectation": expected, "difference": delta,
                    "expectation_source_url": consensus_url if expected is not None else None,
                    "expectation_published": consensus["published"] if expected is not None else None,
                    "results_source_url": results_url if expected is not None else None,
                    "results_published": (TESLA_Q3_2026["results_date"] if expected is not None and
                                          consensus.get("evidence") == "curated_primary_snapshot" else None),
                    "expectation_evidence_basis": consensus.get("evidence", "live_issuer_pages") if expected is not None else None,
                    "guidance": None, "reaction": None,
                    "status": "matched_issuer_consensus" if expected is not None else "actual_only",
                })
        except (OSError, ValueError, UnicodeError, TypeError, KeyError) as exc:
            output["failures"].append({"source": ticker, "url": filing_url, **_failure(exc)})
    return output
