"""Bounded, attributed upcoming economic-release calendar collection."""

from datetime import date, datetime, timedelta, timezone
import json
import re
import urllib.request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BEA_URL = "https://apps.bea.gov/API/signup/release_dates.json"
BLS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
SOURCES = (("BEA", BEA_URL), ("BLS", BLS_URL))
MAX_BYTES = 1_000_000
MAX_EVENTS = 5000
MAX_FIELD = 500


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise ValueError("Calendar redirect refused")


def _fetch(url):
    if url not in (BEA_URL, BLS_URL):
        raise ValueError("Unknown calendar endpoint")
    request = urllib.request.Request(url, headers={
        "User-Agent": "market-briefs/1.0 (public-source calendar collector)",
        "Accept": "application/json,text/calendar",
    })
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=8) as response:
        content = response.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError("Calendar exceeds size limit")
    return content


def _event(title, when, source, url, all_day=False):
    title = str(title).strip()
    if not title or len(title) > MAX_FIELD:
        raise ValueError("Invalid event title")
    if all_day:
        return {"title": title, "starts_at": None, "date": when.isoformat(),
                "source": source, "source_url": url}
    if when.tzinfo is None:
        raise ValueError("Event timestamp lacks timezone")
    utc = when.astimezone(timezone.utc)
    return {"title": title, "starts_at": utc.isoformat(), "date": utc.date().isoformat(),
            "source": source, "source_url": url}


def _bea(raw):
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Invalid BEA calendar")
    events = []
    release_count = 0
    for title, entry in payload.items():
        if title == "file_last_updated" and isinstance(entry, str):
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("release_dates"), list):
            raise ValueError("Invalid BEA release dates")
        release_count += 1
        for timestamp in entry["release_dates"]:
            if len(events) >= MAX_EVENTS:
                raise ValueError("Too many BEA events")
            if not isinstance(timestamp, str) or len(timestamp) > 50:
                raise ValueError("Invalid BEA timestamp")
            events.append(_event(title, datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
                                 "BEA", BEA_URL))
    if not release_count:
        raise ValueError("BEA calendar has no releases")
    return events


def _ics_lines(raw):
    lines = []
    for line in raw.decode("utf-8-sig").splitlines():
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _ics_time(key, value):
    if re.fullmatch(r"\d{8}", value) and ("VALUE=DATE" in key or key == "DTSTART"):
        return datetime.strptime(value, "%Y%m%d").date(), True
    if not re.fullmatch(r"\d{8}T\d{6}Z?", value):
        raise ValueError("Invalid BLS event timestamp")
    parsed = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return parsed.replace(tzinfo=timezone.utc), False
    match = re.search(r"(?:^|;)TZID=([^;:]+)", key)
    if match:
        try:
            zone = match.group(1)
            # BLS labels its Eastern calendar with the legacy US-Eastern TZID.
            # Resolve that documented regional alias to an IANA zone available
            # on the GitHub runner, preserving daylight-saving transitions.
            if zone == "US-Eastern":
                zone = "America/New_York"
            return parsed.replace(tzinfo=ZoneInfo(zone)), False
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown BLS event timezone") from exc
    raise ValueError("BLS event timestamp lacks timezone")


def _bls(raw):
    lines = _ics_lines(raw)
    if not lines or lines[0] != "BEGIN:VCALENDAR" or "END:VCALENDAR" not in lines:
        raise ValueError("Invalid BLS calendar")
    events = []
    current = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise ValueError("Nested BLS events")
            current = {}
        elif line == "END:VEVENT":
            if current is None or "SUMMARY" not in current or "DTSTART" not in current:
                raise ValueError("Incomplete BLS event")
            key, value = current["DTSTART"]
            when, all_day = _ics_time(key, value)
            events.append(_event(current["SUMMARY"], when, "BLS", BLS_URL, all_day))
            if len(events) > MAX_EVENTS:
                raise ValueError("Too many BLS events")
            current = None
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            if len(value) > MAX_FIELD:
                raise ValueError("BLS event field exceeds size limit")
            if key.startswith("DTSTART"):
                current["DTSTART"] = (key, value)
            elif key == "SUMMARY":
                current["SUMMARY"] = value.replace("\\,", ",").replace("\\n", " ").replace("\\N", " ")
    if current is not None:
        raise ValueError("Unclosed BLS event")
    return events


def collect_watchlist(now, fetcher=None):
    """Return verified events starting within the next 72 hours.

    Fetcher receives one allowlisted URL and returns bytes. Failures are per-source.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Watchlist requires a timezone-aware datetime")
    now = now.astimezone(timezone.utc)
    end = now + timedelta(hours=72)
    fetcher = fetcher or _fetch
    result = {"events": [], "failures": [], "retrieved_at": now.isoformat()}
    for source, url in SOURCES:
        try:
            raw = fetcher(url)
            if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
                raise ValueError("Invalid calendar response size or type")
            parsed = _bea(raw) if source == "BEA" else _bls(raw)
            for event in parsed:
                if event["starts_at"]:
                    event_time = datetime.fromisoformat(event["starts_at"])
                    eligible = now < event_time <= end
                else:
                    event_date = date.fromisoformat(event["date"])
                    eligible = now.date() <= event_date <= end.date()
                if eligible:
                    result["events"].append(event)
        except (OSError, ValueError, UnicodeError, TypeError, KeyError, OverflowError) as exc:
            result["failures"].append({"source": source, "source_url": url,
                                       "error": f"{type(exc).__name__}: {exc}"[:250]})
    unique = {(event["source"], event["title"], event["starts_at"], event["date"]): event
              for event in result["events"]}
    result["events"] = sorted(unique.values(), key=lambda event: (event["starts_at"] or event["date"], event["source"], event["title"]))
    return result
