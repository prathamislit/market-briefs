"""Evidence ledger for the requested Daily Markets desks.

This module deliberately treats an unverified field as unavailable. A feed
headline is a lead to inspect, not a measured price or established catalyst.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

DESKS = (
    ("cross_asset", "Cross-asset dashboard"),
    ("etf_flows", "ETF flows and positioning"),
    ("macro_policy", "Macro and policy"),
    ("geopolitics", "Geopolitics and market transmission"),
    ("earnings", "Companies and earnings"),
    ("us_policy_currency", "United States policy and currency"),
    ("india_markets", "India indices, breadth, movers, and FII/DII flows"),
    ("watchlist", "What matters next (24–72 hours)"),
    ("source_register", "Source register"),
)

MISSING = {
    "cross_asset": ["Missing assets or daily changes require a verified quote provider."],
    "etf_flows": ["Dated ETF net flows and positioning remain unavailable."],
    "macro_policy": ["Policy mechanism and market causality require primary-source review."],
    "geopolitics": ["Affected assets, transmission mechanism, and uncertainty require verified analysis."],
    "earnings": ["Consensus comparisons, guidance, and price reactions remain unavailable."],
    "us_policy_currency": ["Comprehensive federal-policy tracking, yields, and currency changes remain unavailable."],
    "india_markets": ["Sensex, Nifty, breadth, movers, and dated FII/DII flows require verified observations."],
    "watchlist": ["Verified event schedules and upcoming releases remain unavailable."],
    "source_register": [],
}


def _url(value):
    try:
        parsed = urlsplit(str(value or ""))
        if parsed.scheme in ("https", "http") and parsed.hostname and not parsed.username and not parsed.password:
            return parsed.geturl()
    except ValueError:
        pass
    return ""


def _unique_items(briefs):
    grouped = {}
    for edition in ("india", "us"):
        brief = briefs.get(edition) or {}
        for section, cards in (brief.get("sections") or {}).items():
            for card in cards:
                if not isinstance(card, dict):
                    continue
                key = (card.get("source"), card.get("link"), card.get("title"))
                if not card.get("title"):
                    continue
                if key not in grouped:
                    grouped[key] = (edition, set(), card)
                grouped[key][1].add(section)
    yield from grouped.values()


def _lead(card, edition, generated_at):
    freshness = card.get("publication_freshness") or "unknown"
    try:
        published = datetime.fromisoformat(str(card.get("publication_timestamp")).replace("Z", "+00:00"))
        generated = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        if published.tzinfo and generated.tzinfo:
            age = (generated - published).total_seconds()
            if age > 7 * 86400:
                freshness = "stale"
            elif age < 0:
                freshness = "future_unverified"
    except ValueError:
        freshness = "unknown"
    if card.get("retained_from_last_good") and freshness not in ("stale", "future_unverified"):
        freshness = "retained_last_good"
    return {
        "kind": "headline_lead",
        "title": card["title"],
        "detail": card.get("summary") or "Headline only; inspect the original source.",
        "as_of": card.get("publication_timestamp"),
        "source": card.get("source_name") or card.get("source"),
        "source_url": _url(card.get("link")),
        "edition": edition,
        "claim_type": "source_headline",
        "freshness": freshness,
        "retained": bool(card.get("retained_from_last_good")),
    }


def _add_leads(sections, briefs, generated_at):
    counts = {key: 0 for key, _ in DESKS}
    for edition, categories, item in _unique_items(briefs):
        lead = _lead(item, edition, generated_at)
        if lead["freshness"] in ("stale", "future_unverified"):
            continue
        title = str(item["title"]).casefold()
        kinds = set()
        if categories.intersection(("macro", "rates")):
            kinds.add("macro_policy")
        if "geopolitics" in categories:
            kinds.add("geopolitics")
        if "earnings" in categories:
            kinds.add("earnings")
        if "fx" in categories or (categories.intersection(("us", "rates")) and edition == "us"):
            kinds.add("us_policy_currency")
        if edition == "india" and categories.intersection(("india", "fx", "macro", "rates")):
            kinds.add("india_markets")
        if re.search(r"\b(etf|exchange.traded fund)\b", title):
            kinds.add("etf_flows")
        # Headlines can guide investigation. They never establish measured flows,
        # expectations, price reactions, policy transmission, or future schedules.
        for key in kinds:
            if counts[key] < 12:
                sections[key]["entries"].append(dict(lead))
                counts[key] += 1


def compose_daily_report(briefs, *, market_data=None, watchlist=None, generated_at=None):
    """Compose a public, JSON-safe report from source-backed inputs.

    Feed summaries remain the extracts permitted by the feed collector.
    """
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()
    sections = {key: {"title": label, "entries": [], "missing": list(MISSING[key]), "status": "unavailable"}
                for key, label in DESKS}
    _add_leads(sections, briefs, generated_at)

    assets = (market_data or {}).get("assets") or []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        entry = {k: asset.get(k) for k in ("name", "region", "level", "change", "change_percent", "unit", "as_of", "status", "note", "source", "original_source", "retrieved_at")}
        entry.update(kind="market_observation", source_url=_url(asset.get("source_url")))
        if not entry["name"]:
            continue
        sections["cross_asset"]["entries"].append(entry)
        region = str(entry["region"] or "").lower()
        name = str(entry["name"]).lower()
        if region == "india":
            sections["india_markets"]["entries"].append(dict(entry))
        if "etf" in name or "exchange-traded fund" in name:
            sections["etf_flows"]["entries"].append(dict(entry))
        if any(token in name for token in ("treasury", "yield", "policy rate")):
            sections["macro_policy"]["entries"].append(dict(entry))
        if any(token in name for token in ("usd", "dollar", "treasury", "yield")):
            sections["us_policy_currency"]["entries"].append(dict(entry))

    for edition in ("india", "us"):
        brief = briefs.get(edition) or {}
        for source in brief.get("sources") or []:
            sections["source_register"]["entries"].append({
                "kind": "public_feed", "edition": edition, "source": source.get("name"),
                "status": source.get("status"), "as_of": source.get("attempt_timestamp"),
                "last_success": source.get("last_success_timestamp"),
                "source_url": _url(source.get("home")), "error": source.get("error"),
            })

    for event in (watchlist or {}).get("events") or []:
        if not isinstance(event, dict):
            continue
        title = event.get("title") or event.get("name")
        when = event.get("starts_at") or event.get("date")
        if title and when:
            sections["watchlist"]["entries"].append({
                "kind": "scheduled_event", "title": str(title), "as_of": str(when),
                "source": event.get("source"), "source_url": _url(event.get("source_url")),
                "detail": str(event.get("detail") or "Officially scheduled release; outcome unknown."),
                "claim_type": "primary_schedule", "all_day": not bool(event.get("starts_at")),
            })
    if sections["watchlist"]["entries"]:
        sections["watchlist"]["missing"].remove(MISSING["watchlist"][0])
    for failure in (watchlist or {}).get("failures") or []:
        if isinstance(failure, dict):
            detail = f"{failure.get('source') or 'unknown source'}: {failure.get('error') or 'unavailable'}"
        else:
            detail = str(failure)
        sections["watchlist"]["missing"].append("Schedule source unavailable: " + detail)

    for key, desk in sections.items():
        if key == "source_register":
            healthy = [e for e in desk["entries"] if e.get("status") == "ok"]
            desk["status"] = ("partial" if len(healthy) < len(desk["entries"]) else "available") if healthy else "unavailable"
            continue
        usable = any((e.get("status") in ("ok", "stale") and e.get("level") is not None)
                     if e.get("kind") == "market_observation" else
                     e.get("freshness") not in ("stale", "future_unverified")
                     for e in desk["entries"])
        desk["status"] = "partial" if usable and desk["missing"] else "available" if usable else "unavailable"

    return {
        "title": "Daily Markets", "generated_at": generated_at,
        "edition_dates": {key: (briefs.get(key) or {}).get("date") for key in ("india", "us")},
        "sections": sections,
        "limitations": [
            "Source headlines are investigation leads, not verified causal explanations.",
            "Missing values remain unavailable; no prices or comparisons are inferred from headlines.",
            "Not financial advice.",
            "Personal academic research portfolio. Not investment advice. Not a solicitation. No services offered. Frameworks are informational research outputs.",
        ],
    }


def render_markdown(report):
    """Render the same evidence and gaps in a downloadable public document."""
    lines = ["# Daily Markets", "", f"Published: {html.escape(str(report['generated_at']))}", "",
             "**Not financial advice.**", "",
             "Personal academic research portfolio. Not investment advice. Not a solicitation. No services offered. Frameworks are informational research outputs.", ""]
    for key, _ in DESKS:
        desk = report["sections"][key]
        lines.extend(("## " + desk["title"], "", "Status: " + desk["status"], ""))
        for entry in desk["entries"]:
            if entry.get("kind") == "market_observation":
                name = str(entry["name"])
                if entry.get("status") in ("ok", "stale") and entry.get("level") is not None:
                    change_unit = "percentage points" if entry.get("unit") == "percent" else (entry.get("unit") or "units")
                    change = (f"{entry['change']} {change_unit}" if entry.get("change") is not None else "unavailable")
                    detail = f"{entry.get('status')}: {entry['level']} {entry.get('unit') or ''}; change {change}; as of {entry.get('as_of') or 'unavailable'}"
                else:
                    detail = "Unavailable: " + str(entry.get("note") or entry.get("status") or "unverified")
            elif entry.get("kind") == "public_feed":
                name = str(entry.get("source") or "Source unavailable")
                detail = f"{entry.get('status') or 'unknown'}; checked {entry.get('as_of') or 'unknown'}"
                if entry.get("error"):
                    detail += "; " + str(entry["error"])
            else:
                name = str(entry.get("title") or "Source lead")
                detail = f"{entry.get('claim_type')}: {entry.get('detail')} (published {entry.get('as_of') or 'unknown'})"
                if entry.get("freshness"):
                    detail += "; freshness " + str(entry["freshness"])
            name = html.escape(name)
            detail = html.escape(detail)
            url = _url(entry.get("source_url"))
            if url:
                url = url.replace("(", "%28").replace(")", "%29").replace("<", "%3C").replace(">", "%3E")
                name = f"[{name}]({url})"
            lines.extend((f"- **{name}** — {detail}",))
        for gap in desk["missing"]:
            lines.append("- **Unavailable:** " + html.escape(str(gap)))
        lines.append("")
    lines.extend(("## Limitations", ""))
    lines.extend("- " + html.escape(item) for item in report["limitations"])
    return "\n".join(lines) + "\n"
