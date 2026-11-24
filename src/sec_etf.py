"""Delayed, public N-PORT fund flows and holdings for IVV.

SEC series search narrows the trust's many funds to S000004310. These are
reported fund transactions, not daily exchange ETF creations/redemptions.
"""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


SERIES_ID = "S000004310"
CIK = "1100663"
FEED_URL = ("https://www.sec.gov/cgi-bin/browse-edgar?"
            "action=getcompany&CIK=S000004310&type=NPORT-P&output=atom&count=10")
NPORT_NS = "http://www.sec.gov/edgar/nport"
ATOM_NS = "http://www.w3.org/2005/Atom"
MAX_BYTES = 2_000_000
MAX_CANDIDATES = 4
MAX_HOLDINGS = 1_000
MAX_TOP_HOLDINGS = 5
USER_AGENT = "Market Briefs research (https://github.com/prathamislit/market-briefs)"
LIMITATION = ("N-PORT reports delayed fund-level sales, reinvestments, and "
              "redemptions. These are not current daily ETF exchange flows, "
              "investor positioning, or new external cash when reinvestments are included.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise ValueError("SEC redirect refused")


def _fetch(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "www.sec.gov" or (
        url != FEED_URL and not re.fullmatch(
            rf"/Archives/edgar/data/{CIK}/\d{{18}}/primary_doc\.xml", parsed.path)
    ):
        raise ValueError("Unrecognized SEC endpoint")
    if url != FEED_URL and (parsed.query or parsed.fragment):
        raise ValueError("SEC filing URL has unexpected parameters")
    agent = os.environ.get("SEC_USER_AGENT") or USER_AGENT
    request = urllib.request.Request(url, headers={
        "User-Agent": agent, "Accept": "application/xml, text/xml",
    })
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=10) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("SEC response exceeds byte limit")
    return raw


def _xml(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES or not raw:
        raise ValueError("Invalid SEC response size or type")
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Unexpected XML document definition")
    return ET.fromstring(raw)


def _filings(raw, cutoff):
    root = _xml(raw)
    if root.tag != f"{{{ATOM_NS}}}feed":
        raise ValueError("Unexpected SEC Atom feed")
    candidates = []
    for entry in root.findall(f"{{{ATOM_NS}}}entry")[:10]:
        title = entry.findtext(f"{{{ATOM_NS}}}title", default="")
        if not title.startswith(("NPORT-P ", "NPORT-P/A ")):
            continue
        updated = entry.findtext(f"{{{ATOM_NS}}}updated", default="")
        try:
            filed = datetime.fromisoformat(updated).date()
        except ValueError:
            continue
        if filed > cutoff:
            continue
        link = entry.find(f"{{{ATOM_NS}}}link")
        href = link.get("href", "") if link is not None else ""
        pattern = rf"https://www\.sec\.gov/Archives/edgar/data/{CIK}/(\d{{18}})/[\d-]+-index\.htm"
        match = re.fullmatch(pattern, href)
        if match:
            candidates.append((f"https://www.sec.gov/Archives/edgar/data/{CIK}/"
                               f"{match.group(1)}/primary_doc.xml", filed))
    if not candidates:
        raise ValueError("No public IVV N-PORT filing before report cutoff")
    return candidates[:MAX_CANDIDATES]


def _decimal(value):
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError("Invalid SEC numeric field") from None
    if not number.is_finite():
        raise ValueError("Nonfinite SEC numeric field")
    return number


def _parse_filing(raw, source_url, filing_date, now):
    root = _xml(raw)
    if root.tag != f"{{{NPORT_NS}}}edgarSubmission":
        raise ValueError("Unexpected N-PORT XML root")
    n = {"n": NPORT_NS}
    gen = root.find("n:formData/n:genInfo", n)
    fund = root.find("n:formData/n:fundInfo", n)
    if gen is None or fund is None or gen.findtext("n:seriesId", namespaces=n) != SERIES_ID:
        raise ValueError("SEC filing does not belong to IVV series")
    report_date = date.fromisoformat(gen.findtext("n:repPdDate", default="", namespaces=n))
    if report_date > now.date() or filing_date > now.date():
        raise ValueError("SEC filing is after report cutoff")
    flows = []
    for month in (1, 2, 3):
        element = fund.find(f"n:mon{month}Flow", n)
        if element is None:
            continue
        sales = _decimal(element.get("sales"))
        reinvestment = _decimal(element.get("reinvestment"))
        redemption = _decimal(element.get("redemption"))
        flows.append({"reported_month": month, "sales_usd": float(sales),
                      "reinvestment_usd": float(reinvestment),
                      "redemption_usd": float(redemption),
                      "reported_net_share_flow_usd": float(round(sales + reinvestment - redemption, 2))})
    if not flows:
        raise ValueError("SEC filing has no monthly share flow fields")
    positions = root.findall("n:formData/n:invstOrSecs/n:invstOrSec", n)
    if not positions or len(positions) > MAX_HOLDINGS:
        raise ValueError("Missing or excessive N-PORT holdings")
    holdings = []
    for position in positions:
        if position.findtext("n:assetCat", namespaces=n) != "EC":
            continue
        name = position.findtext("n:name", default="", namespaces=n).strip()
        if not name or len(name) > 160:
            continue
        value = _decimal(position.findtext("n:valUSD", namespaces=n))
        percentage = _decimal(position.findtext("n:pctVal", namespaces=n))
        if value > 0:
            holdings.append({"name": name, "value_usd": float(value),
                             "percent_of_net_assets": float(percentage)})
    if not holdings:
        raise ValueError("No verified equity positions in filing")
    holdings.sort(key=lambda item: item["value_usd"], reverse=True)
    age = (now.date() - report_date).days
    return {"kind": "sec_etf", "ticker": "IVV", "series_id": SERIES_ID,
            "name": gen.findtext("n:seriesName", default="iShares Core S&P 500 ETF", namespaces=n),
            "source": "SEC EDGAR Form N-PORT", "source_url": source_url,
            "retrieved_at": now.isoformat(), "report_date": report_date.isoformat(),
            "filing_date": filing_date.isoformat(), "report_lag_days": age,
            "status": "latest_available_lagged", "currency": "USD",
            "flow_basis": "Fund share sales + reinvestments - redemptions; reported months 1–3",
            "net_flows": flows, "top_holdings": holdings[:MAX_TOP_HOLDINGS],
            "note": f"SEC fund filing for {report_date.isoformat()}, {age} calendar days before report. {LIMITATION}"}


def collect_etf_data(now, fetcher=None):
    """Return IVV filing facts, or a failure for archived-data fallback.

    Fetcher receives an allowlisted SEC URL and returns bounded XML bytes.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("ETF collection requires a timezone-aware datetime")
    now = now.astimezone(timezone.utc)
    fetcher = fetcher or _fetch
    result = {"entries": [], "failures": [], "retrieved_at": now.isoformat(),
              "limitations": [LIMITATION]}
    try:
        for index, (url, filed) in enumerate(_filings(fetcher(FEED_URL), now.date())):
            try:
                entry = _parse_filing(fetcher(url), url, filed, now)
                if index:
                    entry["status"] = "older_filing_lagged"
                    entry["note"] += " A newer listed filing could not be verified."
                result["entries"].append(entry)
                break
            except (OSError, TimeoutError, ValueError, ET.ParseError, TypeError) as exc:
                result["failures"].append({"source": "SEC EDGAR", "url": url,
                                           "source_url": url,
                                           "error": f"{type(exc).__name__}: {exc}"[:250]})
        if not result["entries"]:
            result["failures"].append({"source": "SEC EDGAR", "url": FEED_URL,
                                       "source_url": FEED_URL,
                                       "error": "No verified IVV filing within bounded search"})
    except (OSError, TimeoutError, ValueError, ET.ParseError, TypeError) as exc:
        result["failures"].append({"source": "SEC EDGAR", "url": FEED_URL,
                                   "source_url": FEED_URL,
                                   "error": f"{type(exc).__name__}: {exc}"[:250]})
    return result
