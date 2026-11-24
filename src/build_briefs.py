#!/usr/bin/env python3
import argparse
import ipaddress
import json
import os
import re
import socket
import sys
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import pandas_market_calendars as mcal
except ImportError:
    mcal = None  # Offline fixture replay needs only Python.

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
REPORTS = DOCS / "reports"
USER_AGENT = "market-briefs/1.0 (public-source financial-news collector)"

def load_config(path):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    data = json.loads(path.read_text())
    if data.get("config_version") != 1:
        raise ValueError("Unsupported config_version")
    if not isinstance(data.get("sources"), list) or not data["sources"]:
        raise ValueError("sources must be a non-empty list")
    bounds = {
        "limits": {"timeout_seconds": (1, 120), "retries": (0, 5), "max_redirects": (0, 10),
                   "max_feed_bytes": (1, 10_000_000), "max_entries": (1, 500),
                   "max_summary_chars": (0, 10_000), "max_concurrent_fetches": (1, 16)},
        "cache": {"entry_max_bytes": (1, 10_000_000), "entry_ttl_hours": (1, 168),
                  "max_entries": (1, 1000), "max_fallback_age_days": (1, 30)},
    }
    for group, fields in bounds.items():
        if not isinstance(data.get(group), dict):
            raise ValueError(f"{group} must be an object")
        for field, (minimum, maximum) in fields.items():
            value = data[group].get(field)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{group}.{field} must be an integer from {minimum} to {maximum}")
    if not isinstance(data["cache"].get("directory"), str) or not data["cache"]["directory"]:
        raise ValueError("cache.directory must be a non-empty string")
    if not isinstance(data["cache"].get("parser_version"), str) or not data["cache"]["parser_version"]:
        raise ValueError("cache.parser_version must be a non-empty string")
    allowed_sections = {"overview", "india", "us", "macro", "rates", "fx", "energy", "commodities", "geopolitics", "earnings"}
    sections = data.get("sections")
    if not isinstance(sections, list) or not sections or any(not isinstance(value, str) or value not in allowed_sections for value in sections):
        raise ValueError("sections must contain supported section names")
    if len(set(sections)) != len(sections):
        raise ValueError("sections must not contain duplicates")
    source_by_id = {}
    for source in data["sources"]:
        required = ("id", "name", "home", "scope")
        if not isinstance(source, dict) or any(not isinstance(source.get(key), str) or not source[key] for key in required):
            raise ValueError("Each source requires id, name, home, and scope")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source["id"]):
            raise ValueError("Source ids must contain only letters, numbers, underscores, or hyphens")
        if source.get("redistribution", "headline_only") not in ("primary", "headline_only"):
            raise ValueError("redistribution must be primary or headline_only")
        if source.get("feed") is not None and not isinstance(source["feed"], str):
            raise ValueError("Source feed must be a URL string or null")
        source.setdefault("feed", None)
        source.setdefault("redistribution", "headline_only")
        if source["scope"] not in ("India", "US", "both"):
            raise ValueError(f"Invalid source scope: {source['scope']}")
        if source["id"] in source_by_id:
            raise ValueError(f"Duplicate source id: {source['id']}")
        validate_public_url(source["home"], resolve=False)
        if source.get("feed"):
            validate_public_url(source["feed"], resolve=False)
        source_by_id[source["id"]] = source
    return data

def validate_public_url(url, resolve=True):
    try:
        parsed = urlsplit(str(url))
    except Exception as exc:
        raise ValueError(f"Invalid URL: {exc}") from exc
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("Only http and https source URLs are allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid source URL port") from exc
    if any(ord(character) <= 32 for character in str(url)) or "\\" in str(url):
        raise ValueError("Source URLs must not contain whitespace or backslashes")
    if port is not None and port not in (80, 443):
        raise ValueError("Source URLs must use standard HTTP(S) ports")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Source URLs must not contain credentials or fragments")
    host = parsed.hostname.lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        raise ValueError("Loopback and local source hosts are not allowed")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise ValueError("Non-public destination rejected")
    if not resolve:
        return str(url)
    try:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Source hostname could not be resolved: {host}") from exc
    if not addresses:
        raise ValueError(f"Source hostname resolved without addresses: {host}")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        if not ip.is_global:
            raise ValueError(f"Private, loopback, or metadata destination rejected: {host}")
    return str(url)

CONFIG = load_config(ROOT / "config/default.json")
SOURCES = {source["id"]: source for source in CONFIG["sources"]}

SECTION_KEYWORDS = {
    "overview": ("market", "economy", "economic", "central bank", "policy"),
    "india": ("india", "rbi", "sebi", "nse", "bse", "rupee", "rupees"),
    "us": ("united states", "u.s.", "us ", "fed", "federal reserve", "treasury", "sec"),
    "macro": ("gdp", "inflation", "growth", "employment", "jobs", "retail", "manufacturing"),
    "rates": ("interest", "yield", "bond", "treasury", "rate"),
    "fx": ("currency", "dollar", "rupee", "yen", "euro", "fx", "exchange"),
    "energy": ("oil", "gas", "energy", "opec", "lng", "crude"),
    "commodities": ("gold", "silver", "copper", "commodity", "metals", "crop"),
    "geopolitics": ("war", "trade", "tariff", "sanction", "election", "geopolit", "conflict"),
    "earnings": ("earnings", "revenue", "profit", "guidance", "quarter", "filing"),
}

def utcnow():
    return datetime.now(timezone.utc)

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--edition", choices=("india", "us"), required=True)
    p.add_argument("--force", action="store_true", help="Run outside the schedule; calendar status remains visible")
    p.add_argument("--config", default="config/default.json")
    p.add_argument("--fixture", type=Path, help="Replay an offline, synthetic feed snapshot")
    p.add_argument("--at", help="Fixed ISO-8601 clock; requires --fixture")
    p.add_argument("--output-dir", type=Path, help="Separate output directory, required for fixtures")
    return p.parse_args()

def local_and_timezone(edition):
    if edition == "india":
        return ZoneInfo("Asia/Kolkata"), "NSE (official holiday API)", "India"
    return ZoneInfo("America/New_York"), "XNYS", "United States"

def nse_payload_status(payload, session_date):
    if not isinstance(payload, dict) or not payload:
        return {"status": "unknown", "source": "NSE equity segment", "diagnostic": "Empty or unexpected holiday payload"}
    # NSE returns multiple trading segments. Currency and derivatives holidays
    # must never close an otherwise-open cash-equity session.
    rows = payload.get("CM")
    if not isinstance(rows, list):
        return {"status": "unknown", "source": "NSE equity segment", "diagnostic": "NSE CM equity holiday segment missing"}
    holidays = set()
    parsed_rows = 0
    years = set()
    for row in rows:
        if not isinstance(row, dict) or not row.get("tradingDate"):
            continue
        try:
            parsed = datetime.strptime(str(row["tradingDate"]), "%d-%b-%Y").date()
        except ValueError:
            continue
        parsed_rows += 1
        years.add(parsed.year)
        if row.get("morning_session") is None and row.get("evening_session") is None:
            holidays.add(parsed)
    if parsed_rows == 0:
        return {"status": "unknown", "source": "NSE equity segment", "diagnostic": "No expected tradingDate rows returned"}
    if session_date.year not in years:
        return {"status": "unknown", "source": "NSE equity segment", "diagnostic": f"Payload did not include requested year {session_date.year}"}
    if session_date in holidays:
        return {"status": "holiday", "source": "NSE equity segment", "diagnostic": "Official NSE equity holiday"}
    return {"status": "trading_day", "source": "NSE equity segment", "diagnostic": f"Verified against official NSE CM equity holiday payload ({len(holidays)} dates)"}

def nse_calendar_status(session_date):
    if session_date.weekday() >= 5:
        return {"status": "non_trading_day", "source": "NSE equity segment", "diagnostic": "Weekend"}
    req = urllib.request.Request(
        "https://www.nseindia.com/api/holiday-master?type=trading&segment=equity",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            payload = json.loads(response.read(2_000_000))
        return nse_payload_status(payload, session_date)
    except Exception as exc:
        return {"status": "unknown", "source": "NSE equity segment", "diagnostic": f"{type(exc).__name__}: holiday calendar unavailable"}

def is_official_nse_session(session_date):
    return nse_calendar_status(session_date)["status"] == "trading_day"

def is_schedule_window(now, edition):
    tz, _, _ = local_and_timezone(edition)
    local = now.astimezone(tz)
    minutes = local.hour * 60 + local.minute
    target = 8 * 60 + 15
    if not target <= minutes <= target + 90:
        return False
    return calendar_status(now, edition)["status"] == "trading_day"

def calendar_status(now, edition):
    tz, _, _ = local_and_timezone(edition)
    local = now.astimezone(tz)
    if edition == "india":
        return nse_calendar_status(local.date())
    try:
        if mcal is None:
            raise RuntimeError("Install requirements.txt for live NYSE calendars")
        cal = mcal.get_calendar("XNYS")
        valid = cal.valid_days(local.date().isoformat(), local.date().isoformat())
        if len(valid):
            return {"status": "trading_day", "source": "NYSE (pandas-market-calendars XNYS)", "diagnostic": "Verified against XNYS session calendar"}
        return {"status": "non_trading_day", "source": "NYSE (pandas-market-calendars XNYS)", "diagnostic": "Weekend or NYSE holiday"}
    except Exception as exc:
        return {"status": "unknown", "source": "NYSE (pandas-market-calendars XNYS)", "diagnostic": f"{type(exc).__name__}: calendar unavailable"}

def publication_freshness(published, now, prior_publication=None):
    if published > now:
        return "future_rejected"
    if now - published > timedelta(days=7):
        return "outside_retention_window"
    if prior_publication and published >= prior_publication:
        return "since_previous_edition"
    if now - published <= timedelta(days=7):
        return "recent_window"
    return "outside_retention_window"

def parse_publication_timestamp(raw):
    raw = " ".join(str(raw or "").split())
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            parsed = None
    if parsed is None:
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    return None

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)

def strip_feed_markup(value):
    extractor = TextExtractor()
    extractor.feed(str(value or ""))
    return " ".join(" ".join(extractor.parts).replace("\n", " ").split())

def atomic_write(path, content, binary=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb" if binary else "w") as handle:
            handle.write(content if binary else str(content))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

def cache_path(source_key):
    safe_key = re.sub(r"[^A-Za-z0-9_.-]", "_", source_key)
    directory = ROOT / CONFIG["cache"]["directory"]
    return directory / f"{safe_key}.json"

def read_cache(source_key):
    path = cache_path(source_key)
    try:
        if path.stat().st_size > CONFIG["cache"]["entry_max_bytes"]:
            return None
        data = json.loads(path.read_text())
        if data.get("parser_version") != CONFIG["cache"]["parser_version"] or data.get("feed_url") != SOURCES[source_key].get("feed"):
            return None
        timestamp = datetime.fromisoformat(data["timestamp"])
        age = datetime.now(timezone.utc) - timestamp
        if age < timedelta(0) or age > timedelta(hours=CONFIG["cache"]["entry_ttl_hours"]):
            return None
        if not isinstance(data.get("items"), list) or len(data["items"]) > CONFIG["limits"]["max_entries"]:
            return None
        return data
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None

def write_cache(source_key, items, etag=None):
    data = {
        "parser_version": CONFIG["cache"]["parser_version"],
        "feed_url": SOURCES[source_key].get("feed"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "etag": etag,
        "items": items,
    }
    encoded = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if len(encoded.encode("utf-8")) > CONFIG["cache"]["entry_max_bytes"]:
        return
    atomic_write(cache_path(source_key), encoded)
    entries = sorted(cache_path(source_key).parent.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for obsolete in entries[CONFIG["cache"]["max_entries"]:]:
        obsolete.unlink(missing_ok=True)

def clear_cache():
    directory = ROOT / CONFIG["cache"]["directory"]
    if directory.exists():
        for path in directory.glob("*.json"):
            path.unlink()

def inspect_cache():
    directory = ROOT / CONFIG["cache"]["directory"]
    if not directory.exists():
        print("No cache directory")
        return
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            print(f"{path.name}: parser={data.get('parser_version')} items={len(data.get('items', []))} timestamp={data.get('timestamp')}")
        except (OSError, json.JSONDecodeError):
            print(f"{path.name}: corrupted")

def read_cache_fallback(source_key):
    path = cache_path(source_key)
    try:
        if path.stat().st_size > CONFIG["cache"]["entry_max_bytes"]:
            return None
        data = json.loads(path.read_text())
        if data.get("parser_version") != CONFIG["cache"]["parser_version"] or data.get("feed_url") != SOURCES[source_key].get("feed"):
            return None
        timestamp = datetime.fromisoformat(data["timestamp"])
        age_days = (datetime.now(timezone.utc) - timestamp).total_seconds() / 86400
        if age_days < 0 or age_days > CONFIG["cache"]["max_fallback_age_days"]:
            return None
        if not isinstance(data.get("items"), list) or len(data["items"]) > CONFIG["limits"]["max_entries"]:
            return None
        return data
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None

class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, max_redirects):
        super().__init__()
        self.redirects = 0
        self.max_redirects = max_redirects
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirects += 1
        if self.redirects > self.max_redirects:
            raise urllib.error.URLError("Redirect limit exceeded")
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch_feed(source_key):
    source = SOURCES[source_key]
    if not source.get("feed"):
        return {"source": source_key, "status": "unconfigured", "items": [], "error": "No permitted public feed configured"}
    cached = read_cache(source_key)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"}
    if cached and cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    last_error = "Feed collection failed"
    for attempt in range(CONFIG["limits"]["retries"] + 1):
        try:
            validate_public_url(source["feed"])
            opener = urllib.request.build_opener(SafeRedirectHandler(CONFIG["limits"]["max_redirects"]))
            req = urllib.request.Request(source["feed"], headers=headers)
            with opener.open(req, timeout=CONFIG["limits"]["timeout_seconds"]) as response:
                data = response.read(CONFIG["limits"]["max_feed_bytes"] + 1)
                etag = response.headers.get("ETag")
            if len(data) > CONFIG["limits"]["max_feed_bytes"]:
                raise ValueError("Feed exceeds configured byte limit")
            if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                raise ValueError("DTD and entity declarations are rejected")
            root = ET.fromstring(data)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            entries = root.findall(".//item")
            if not entries:
                entries = root.findall(".//atom:entry", ns)
            items = []
            for entry in entries[:CONFIG["limits"]["max_entries"]]:
                title = entry.findtext("title") or entry.findtext("atom:title", namespaces=ns) or ""
                link = entry.findtext("link") or ""
                if not link:
                    atom_link = entry.find("atom:link", ns)
                    if atom_link is not None:
                        link = atom_link.attrib.get("href", "")
                pub = (
                    entry.findtext("pubDate")
                    or entry.findtext("atom:updated", namespaces=ns)
                    or entry.findtext("atom:published", namespaces=ns)
                    or entry.findtext("dc:date", namespaces={"dc": "http://purl.org/dc/elements/1.1/"})
                    or ""
                )
                summary = strip_feed_markup(entry.findtext("description") or entry.findtext("atom:summary", namespaces=ns) or "")
                summary = summary[:CONFIG["limits"]["max_summary_chars"]]
                redistribution = source.get("redistribution", "headline_only")
                if redistribution == "headline_only":
                    summary = ""
                try:
                    validate_public_url(link)
                    safe_link = str(link)
                except (ValueError, TypeError):
                    safe_link = ""
                if title and safe_link:
                    items.append({
                        "title": strip_feed_markup(title),
                        "link": safe_link,
                        "published": pub,
                        "publication_timestamp": parse_publication_timestamp(pub),
                        "publication_timestamp_raw": pub or None,
                        "summary": summary,
                        "access": "headline_only" if redistribution == "headline_only" or not summary else "extractive_excerpt",
                    })
            if items:
                write_cache(source_key, items, etag)
            return {"source": source_key, "status": "ok" if items else "no_items", "items": items,
                    "error": None if items else "Endpoint returned no usable title/link entries"}
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cached:
                return {"source": source_key, "status": "ok", "items": cached["items"], "error": None}
            last_error = f"HTTP {exc.code}: feed request rejected"
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError, OSError) as exc:
            last_error = f"{type(exc).__name__}: feed collection failed"
    fallback = read_cache_fallback(source_key)
    if fallback:
        return {"source": source_key, "status": "failed", "items": fallback["items"], "error": last_error + "; bounded last-good cache retained",
                "cache_fallback": True, "last_success_timestamp": fallback["timestamp"]}
    return {"source": source_key, "status": "failed", "items": [], "error": last_error}

def relevant_source(source_key, edition):
    scope = SOURCES[source_key]["scope"]
    return scope == "both" or scope == ("India" if edition == "india" else "US")

def classify(text):
    lower = " " + text.casefold() + " "
    matches = []
    for section, keywords in SECTION_KEYWORDS.items():
        if any(re.search(r"(?<![a-z0-9])" + re.escape(keyword.strip()) + r"(?![a-z0-9])", lower) for keyword in keywords):
            matches.append(section)
    return matches or ["overview"]

def dedupe(items):
    seen = set()
    out = []
    for item in items:
        key = " ".join(item["title"].casefold().split())
        if key not in seen:
            seen.add(key)
            merged = dict(item)
            link = item.get("link")
            source_name = item.get("source_name")
            merged["supporting_links"] = [link] if link else []
            merged["supporting_sources"] = [source_name] if source_name else []
            out.append(merged)
        else:
            existing = out[seenpos(key, out)]
            if item.get("link") and item["link"] not in existing["supporting_links"]:
                existing["supporting_links"].append(item["link"])
            if item.get("source_name") and item["source_name"] not in existing["supporting_sources"]:
                existing["supporting_sources"].append(item["source_name"])
    return out

def seenpos(key, items):
    for index, item in enumerate(items):
        if " ".join(item["title"].casefold().split()) == key:
            return index
    return -1

def load_latest_report(edition):
    candidates = sorted(REPORTS.glob(f"{edition}-*.json"), reverse=True)
    for candidate in candidates:
        try:
            return json.loads(candidate.read_text())
        except (json.JSONDecodeError, OSError):
            continue
    return None

def load_last_good_report(edition):
    candidates = sorted(REPORTS.glob(f"{edition}-*.json"), reverse=True)
    for candidate in candidates:
        try:
            report = json.loads(candidate.read_text())
            if any(source["status"] == "ok" for source in report.get("sources", [])):
                return report
        except (json.JSONDecodeError, OSError):
            continue
    return None

def previous_source_items(report, source_key):
    if not report:
        return []
    items = []
    for cards in report.get("sections", {}).values():
        for item in cards:
            if item.get("source") == source_key:
                if item not in items:
                    items.append(dict(item))
    return items

def make_report(edition, now, fixture=None, verified_calendar=None):
    tz, _, market_name = local_and_timezone(edition)
    selected_sources = [key for key in SOURCES if relevant_source(key, edition)]
    if fixture is None:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=CONFIG["limits"]["max_concurrent_fetches"]) as pool:
            feed_results = list(pool.map(fetch_feed, selected_sources))
    else:
        snapshots = {result["source"]: result for result in fixture["feeds"]}
        feed_results = [dict(snapshots.get(key, {"source": key, "status": "no_items", "items": [], "error": "No synthetic fixture items"})) for key in selected_sources]
    prior = load_latest_report(edition) if fixture is None else None
    prior_sources = {source["id"]: source for source in prior.get("sources", [])} if prior else {}
    prior_publication = None
    if prior and prior.get("publication_timestamp"):
        prior_timestamp = parse_publication_timestamp(prior["publication_timestamp"])
        prior_publication = datetime.fromisoformat(prior_timestamp) if prior_timestamp else None
    items = []
    for result in feed_results:
        for item in result["items"]:
            if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not item["title"] or not isinstance(item.get("summary", ""), str):
                continue
            item = dict(item)
            item.setdefault("summary", "")
            item["source"] = result["source"]
            item["source_name"] = SOURCES[result["source"]]["name"]
            if result.get("cache_fallback"):
                item["retained_from_last_good"] = True
            if SOURCES[result["source"]].get("redistribution") == "headline_only":
                item["summary"] = ""
                item["access"] = "headline_only"
            timestamp = parse_publication_timestamp(item.get("publication_timestamp"))
            item["publication_timestamp"] = timestamp
            if timestamp:
                published = datetime.fromisoformat(timestamp)
                item["publication_freshness"] = publication_freshness(published, now, prior_publication)
            else:
                if result.get("cache_fallback"):
                    continue
                item["publication_freshness"] = "unknown"
            if item["publication_freshness"] not in ("future_rejected", "outside_retention_window"):
                items.append(item)
        if result["status"] == "failed":
            retained = previous_source_items(prior, result["source"])
            for item in retained:
                item = dict(item)
                item["retained_from_last_good"] = True
                timestamp = parse_publication_timestamp(item.get("publication_timestamp"))
                if not timestamp:
                    continue
                published = datetime.fromisoformat(timestamp)
                if publication_freshness(published, now) in ("future_rejected", "outside_retention_window"):
                    continue
                if SOURCES[result["source"]].get("redistribution") == "headline_only":
                    item["summary"] = ""
                    item["access"] = "headline_only"
                item["publication_freshness"] = "retained_last_good"
                items.append(item)
    usable_by_source = {key: [item for item in items if item.get("source") == key] for key in selected_sources}
    items = dedupe(items)

    sections = {name: [] for name in CONFIG["sections"]}
    for item in items:
        for section in classify(item["title"] + " " + item["summary"]):
            if section in sections:
                sections[section].append(item)

    sources = []
    for result in feed_results:
        prior_source = prior_sources.get(result["source"])
        last_success = now.isoformat() if result["status"] == "ok" else (
            result.get("last_success_timestamp") or (prior_source.get("last_success_timestamp") if prior_source else None)
        )
        sources.append({
            "id": result["source"],
            "name": SOURCES[result["source"]]["name"],
            "home": SOURCES[result["source"]]["home"],
            "status": result["status"],
            "last_success_timestamp": last_success,
            "attempt_timestamp": now.isoformat(),
            "error": result["error"],
            "item_count": len(usable_by_source[result["source"]]),
            "fetched_item_count": len(result["items"]),
            "retained_last_good": any(item.get("retained_from_last_good") for item in usable_by_source[result["source"]]),
        })

    report_id = now.astimezone(tz).strftime("%Y-%m-%d")
    report = {
        "edition": edition,
        "market": market_name,
        "date": report_id,
        "publication_timestamp": now.isoformat(),
        "retrieval_timestamp": now.isoformat(),
        "timezone": str(tz),
        "calendar": fixture["calendar"][edition] if fixture is not None else (verified_calendar or calendar_status(now, edition)),
        "synthetic_fixture": fixture is not None,
        "unique_item_count": len(items),
        "freshness": ("retrieved" if items else "no_recent_items") if any(source["status"] == "ok" for source in sources) else (
            "prior_good_retained" if any(source["retained_last_good"] for source in sources) else "no_successful_sources"
        ),
        "sections": sections,
        "sources": sources,
        "limitations": [
            "Only permitted public feeds configured for this run are included.",
            "Headlines and concise summaries are not evidence of full article coverage.",
            "This report does not claim comprehensive access to paywalled financial journalism.",
        ],
    }
    return report

def html_escape(value):
    import html
    return html.escape(str(value), quote=True)

def safe_link(value):
    try:
        value = validate_public_url(value, resolve=False)
    except (TypeError, ValueError):
        return ""
    return value.replace("(", "%28").replace(")", "%29").replace("<", "%3C").replace(">", "%3E")

def report_markdown(report):
    lines = [
        f"# {report['market']} Market Brief",
        "",
        f"**Published:** {report['publication_timestamp']}  ",
        f"**Retrieved:** {report['retrieval_timestamp']}  ",
        f"**Edition:** {report['edition']}  ",
        f"**Freshness:** {report['freshness']}",
        "",
    ]
    if report.get("synthetic_fixture"):
        lines.extend(["**SYNTHETIC FIXTURE: demonstration data, not market news.**", ""])
    for section in ("overview", "india", "us", "macro", "rates", "fx", "energy", "commodities", "geopolitics", "earnings"):
        if section not in report["sections"]:
            continue
        lines.extend(["## " + section.replace("_", " ").title(), ""])
        cards = report["sections"][section]
        if not cards:
            lines += ["_No uncovered public-feed items in this edition._", ""]
            continue
        for item in cards[:12]:
            supporting_links = item.get("supporting_links") or [item["link"]]
            source_links = ", ".join(
                f"[source {index + 1}]({safe_link(link)})"
                for index, link in enumerate(supporting_links)
                if safe_link(link)
            )
            lines.extend([
                f"### {html_escape(item['title'])}",
                "",
                f"- **Supporting sources:** {source_links or 'Unavailable'}",
                f"- **Published:** {html_escape(item.get('publication_timestamp') or 'Unknown date')}",
                f"- **Access:** {html_escape(item.get('access') or 'unknown')}",
                f"- **Detail:** {html_escape(item['summary'] or 'Headline only; full extracted summary unavailable.')}",
                f"- **Classification:** Source-supported headline/detail",
                f"- **Retention:** {'Retained prior good source content' if item.get('retained_from_last_good') else 'Current collection'}",
                "",
            ])
    lines.extend(["## Source status", ""])
    for source in report["sources"]:
        lines.append(f"- **{source['name']}**: `{source['status']}` ({source['item_count']} items)"
                     + (f" - {source['error']}" if source["error"] else ""))
    lines.extend(["", "## Coverage limitations", ""])
    for limitation in report["limitations"]:
        lines.append(f"- {limitation}")
    return "\n".join(lines)

def write_report(report):
    REPORTS.mkdir(parents=True, exist_ok=True)
    filename = f"{report['edition']}-{report['date']}.json"
    path = REPORTS / filename
    atomic_write(path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    atomic_write(REPORTS / f"{report['edition']}-{report['date']}.md", report_markdown(report))
    manifest_path = REPORTS / "manifest.json"
    manifest = []
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    manifest = [entry for entry in manifest if not (entry["edition"] == report["edition"] and entry["date"] == report["date"])]
    manifest.insert(0, {
        "edition": report["edition"],
        "date": report["date"],
        "publication_timestamp": report["publication_timestamp"],
        "retrieval_timestamp": report["retrieval_timestamp"],
        "json": f"{report['edition']}-{report['date']}.json",
        "markdown": f"{report['edition']}-{report['date']}.md",
    })
    manifest = sorted(manifest, key=lambda x: (x["date"], x["edition"]), reverse=True)[:366]
    atomic_write(manifest_path, json.dumps(manifest, indent=2) + "\n")

def build_dashboard():
    manifest_path = REPORTS / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    data = json.dumps(manifest)
    index = DOCS / "index.html"
    template = (ROOT / "src/dashboard.html").read_text()
    atomic_write(index, template.replace("__MANIFEST__", data))

def main():
    global CONFIG, SOURCES, DOCS, REPORTS
    args = parse_args()
    CONFIG = load_config(args.config)
    SOURCES = {source["id"]: source for source in CONFIG["sources"]}
    if args.at and not args.fixture:
        raise SystemExit("--at requires --fixture")
    if args.fixture and not args.output_dir:
        raise SystemExit("--fixture requires --output-dir to isolate synthetic data")
    if args.output_dir:
        DOCS = args.output_dir.resolve()
        REPORTS = DOCS / "reports"
    fixture = json.loads(args.fixture.read_text()) if args.fixture else None
    if fixture is not None and DOCS.resolve().is_relative_to((ROOT / "docs").resolve()):
        raise SystemExit("Synthetic fixtures cannot write into published docs")
    now = datetime.fromisoformat(args.at.replace("Z", "+00:00")) if args.at else utcnow()
    if now.tzinfo is None:
        raise SystemExit("--at requires a timezone offset")
    verified_calendar = None
    if fixture is None and not args.force:
        tz, _, _ = local_and_timezone(args.edition)
        local = now.astimezone(tz)
        minutes = local.hour * 60 + local.minute
        if not 8 * 60 + 15 <= minutes <= 9 * 60 + 45:
            print(f"Skipping {args.edition}: outside 08:15-09:45 local publication window")
            return 0
        verified_calendar = calendar_status(now, args.edition)
        if verified_calendar["status"] != "trading_day":
            print(f"Skipping {args.edition}: calendar {verified_calendar['status']}: {verified_calendar.get('diagnostic', '')}")
            return 1 if verified_calendar["status"] == "unknown" else 0
    report = make_report(args.edition, now, fixture=fixture, verified_calendar=verified_calendar)
    write_report(report)
    build_dashboard()
    print(f"Built {args.edition} edition for {report['date']} "
          f"with {sum(len(v) for v in report['sections'].values())} classified items")
    return 1 if report["freshness"] in ("no_successful_sources", "prior_good_retained") else 0

if __name__ == "__main__":
    sys.exit(main())
