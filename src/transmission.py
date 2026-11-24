"""Source-backed transmission hypotheses, without causal attribution.

Only narrowly matched primary-source announcements can activate a documented
mechanism. Daily market observations can show temporal overlap, never prove
that the announcement caused a price change.
"""

from datetime import date, datetime, timedelta, timezone
import re
from urllib.parse import urlsplit


RULES = (
    {
        "id": "fomc_financial_conditions",
        "source": "fed",
        "host": "www.federalreserve.gov",
        "tokens": (("fomc", "federal open market committee"), ("statement", "policy decision")),
        "evidence_tokens": (("target range", "federal funds rate"),
                            ("decided to", "maintain", "reduce", "lower", "raise", "increase")),
        "channel": "Interest-rate and exchange-rate transmission",
        "mechanism": "Federal Reserve policy and communications can affect market interest rates and exchange rates; the direction and magnitude of this event's effect are unverified.",
        "mechanism_url": "https://www.federalreserve.gov/monetarypolicy/monetary-policy-what-are-its-goals-how-does-it-work.htm",
        "assets": ("US Treasury 2-year yield", "US Treasury 10-year yield", "EUR/USD (New York noon buying rate)"),
    },
    {
        "id": "hormuz_oil_transit",
        "source": "eia",
        "host": "www.eia.gov",
        "tokens": (("strait of hormuz",), ("disrupt", "clos", "block", "halt", "attack")),
        "evidence_tokens": (("strait of hormuz",), ("oil", "petroleum", "crude"),
                            ("disrupt", "clos", "block", "halt")),
        "evidence_pattern": r"\bstrait of hormuz\b[^.;!?]{0,80}\b(?:was|were|is|are|has been|have been)\s+(?:temporarily\s+)?(?:disrupted|blocked|closed|halted)\b",
        "channel": "Oil transit and supply-risk transmission",
        "mechanism": "A disruption at the Strait of Hormuz can delay oil transit and raise shipping costs; any observed price attribution remains unverified.",
        "mechanism_url": "https://www.eia.gov/international/analysis/special-topics/World_Oil_Transit_Chokepoints",
        "assets": ("Brent crude oil spot", "WTI crude oil spot"),
    },
)

RIVAL_FACTORS = (
    "Other contemporaneous releases and market events were not isolated.",
    "Demand changes, inventory, positioning, and liquidity were not isolated.",
)


def _publication(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _primary_url(url, host):
    try:
        parsed = urlsplit(str(url))
        if parsed.scheme == "https" and parsed.hostname == host and not parsed.username and not parsed.password:
            return parsed.geturl()
    except ValueError:
        pass
    return None


def _matching_rule(card):
    title = str(card.get("title") or "").casefold()
    source_text = str(card.get("summary") or "")
    sentences = re.split(r"(?<=[.!?])\s+", source_text)
    for rule in RULES:
        if card.get("source") != rule["source"]:
            continue
        if not _primary_url(card.get("link"), rule["host"]):
            continue
        verified_sentence = next((sentence for sentence in sentences
            if all(any(token in sentence.casefold() for token in alternatives)
                   for alternatives in rule["evidence_tokens"])
            and ("evidence_pattern" not in rule or re.search(rule["evidence_pattern"], sentence.casefold()))), None)
        if (all(any(token in title for token in alternatives) for alternatives in rule["tokens"])
                and verified_sentence):
            return rule, verified_sentence
    return None


def _exposure(name, assets, event_date):
    asset = assets.get(name)
    if not asset:
        return {"name": name, "status": "unavailable", "as_of": None,
                "previous_as_of": None, "observed_change": None,
                "temporal_relation": "unavailable", "exposure_status": "potential_only",
                "change_unit": None}
    status = asset.get("status") or "unavailable"
    as_of = asset.get("as_of")
    previous = asset.get("previous_as_of")
    relation = "not_comparable"
    change = None
    try:
        observed_date = date.fromisoformat(as_of)
        previous_date = date.fromisoformat(previous)
        if status == "ok" and previous_date <= event_date < observed_date:
            relation = "brackets_event_at_daily_resolution"
            change = asset.get("change")
    except (TypeError, ValueError):
        pass
    return {"name": name, "status": status, "as_of": as_of,
            "previous_as_of": previous, "observed_change": change,
            "temporal_relation": relation, "source_url": asset.get("source_url"),
            "exposure_status": "potential_only",
            "change_unit": "percentage points" if asset.get("unit") == "percent" else asset.get("unit")}


def build_transmission(briefs, market_data, now):
    """Return deterministic, attributed hypotheses from supported primary events."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Transmission report requires a timezone-aware clock")
    now = now.astimezone(timezone.utc)
    assets = {asset.get("name"): asset for asset in (market_data or {}).get("assets", [])
              if isinstance(asset, dict) and asset.get("name")}
    entries = []
    seen = set()
    for edition in ("india", "us"):
        for cards in ((briefs or {}).get(edition) or {}).get("sections", {}).values():
            for card in cards:
                if not isinstance(card, dict):
                    continue
                matched = _matching_rule(card)
                published = _publication(card.get("publication_timestamp"))
                if not matched or not published or not now - timedelta(days=7) <= published <= now:
                    continue
                rule, evidence_sentence = matched
                event_url = _primary_url(card.get("link"), rule["host"])
                key = (rule["id"], event_url)
                if key in seen:
                    continue
                seen.add(key)
                entries.append({
                    "kind": "transmission_hypothesis",
                    "rule": rule["id"],
                    "event": {"title": str(card["title"])[:300], "source": card.get("source_name") or rule["source"],
                              "source_url": event_url, "published_at": published.isoformat(),
                              "evidence_type": "primary_feed_excerpt",
                              "evidence_excerpt": evidence_sentence[:240]},
                    "channel": rule["channel"], "mechanism": rule["mechanism"],
                    "mechanism_url": rule["mechanism_url"],
                    "affected_assets": [_exposure(name, assets, published.date()) for name in rule["assets"]],
                    "rival_factors": list(RIVAL_FACTORS),
                    "confidence": "unverified_event_market_attribution",
                    "conclusion": "Documented potential mechanism; this event's market effect is unverified.",
                })
                if len(entries) >= 12:
                    break
            if len(entries) >= 12:
                break
    entries.sort(key=lambda entry: (entry["event"]["published_at"], entry["event"]["source_url"]), reverse=True)
    return {"generated_at": now.isoformat(), "entries": entries,
            "missing": [] if entries else ["No recent primary-source event matched a supported transmission rule."],
            "scope": "Mechanisms are documented; event-specific market causality is unverified."}
