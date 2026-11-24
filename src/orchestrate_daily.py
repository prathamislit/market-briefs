#!/usr/bin/env python3
"""Publish one timestamped Daily Markets report per invocation."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from build_briefs import ROOT, atomic_write, build_dashboard
from daily_report import compose_daily_report, render_markdown
from market_data import collect_market_data
from watchlist import collect_watchlist


def latest_briefs(docs):
    reports = docs / "reports"
    manifest_path = reports / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    briefs = {}
    for entry in manifest:
        edition = entry.get("edition")
        filename = entry.get("json")
        if edition not in ("india", "us") or edition in briefs:
            continue
        if not isinstance(filename, str) or not filename.startswith(edition + "-") or "/" in filename or "\\" in filename or not filename.endswith(".json"):
            continue
        try:
            brief = json.loads((reports / filename).read_text())
            if brief.get("edition") == edition and not brief.get("synthetic_fixture"):
                briefs[edition] = brief
        except (OSError, ValueError):
            continue
    return briefs


def publish(now=None, *, docs=None, market_collector=collect_market_data,
            watchlist_collector=collect_watchlist):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Report time must include a timezone")
    now = now.astimezone(timezone.utc)
    docs = Path(docs) if docs is not None else ROOT / "docs"
    market_data = market_collector(now)
    watchlist = watchlist_collector(now)
    report = compose_daily_report(latest_briefs(docs), market_data=market_data,
                                  watchlist=watchlist, generated_at=now.isoformat())
    daily = docs / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    if not run_id.isdigit() and run_id != "local" or not attempt.isdigit():
        raise ValueError("Invalid run identity")
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    basename = f"{stamp}-{run_id}-{attempt}"
    json_path = daily / f"{basename}.json"
    markdown_path = daily / f"{basename}.md"
    if json_path.exists() or markdown_path.exists():
        raise FileExistsError(f"Daily report already archived: {basename}")
    atomic_write(json_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    atomic_write(markdown_path, render_markdown(report))
    manifest_path = daily / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    manifest.insert(0, {"date": now.astimezone(ZoneInfo("America/New_York")).date().isoformat(),
                        "generated_at": now.isoformat(),
                        "run_id": run_id, "attempt": attempt,
                        "json": json_path.name, "markdown": markdown_path.name})
    atomic_write(manifest_path, json.dumps(manifest, indent=2) + "\n")
    if docs.resolve() == (ROOT / "docs").resolve():
        build_dashboard()
    return report, json_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, help="Separate output directory for inspection")
    args = parser.parse_args()
    report, path = publish(docs=args.output_dir)
    print(f"Published Daily Markets to {path}; {len(report['sections'])} desks")


if __name__ == "__main__":
    main()
