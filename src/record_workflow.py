#!/usr/bin/env python3
"""Archive workflow attempt outcomes, including scheduled skips and failures."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from build_briefs import DOCS, atomic_write


def record(directory=DOCS, now=None, environ=None):
    environ = environ or os.environ
    now = now or datetime.now(timezone.utc)
    run_id = environ.get("GITHUB_RUN_ID", "local")
    attempt = environ.get("GITHUB_RUN_ATTEMPT", "1")
    if not run_id.isdigit() and run_id != "local" or not attempt.isdigit():
        raise ValueError("Invalid run identity")
    runs = Path(directory) / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    filename = f"workflow-{run_id}-{attempt}.json"
    destination = runs / filename
    if destination.exists():
        raise FileExistsError(f"Archived workflow attempt already exists: {destination}")
    event = {
        "run_id": run_id, "attempt": attempt, "recorded_at": now.astimezone(timezone.utc).isoformat(),
        "event": environ.get("GITHUB_EVENT_NAME", "local"),
        "schedule": environ.get("EVENT_SCHEDULE") or None,
        "commit": environ.get("GITHUB_SHA") or None,
        "steps": {key: environ.get(f"STEP_{key.upper()}", "unknown")
                  for key in ("tests", "replay", "generate", "daily")},
        "edition_collection_failed": environ.get("COLLECTION_FAILED", "unknown"),
    }
    atomic_write(destination, json.dumps(event, indent=2) + "\n")
    # The edition manifest records only edition artifacts. Workflow attempts
    # receive their own index, leaving existing report links unchanged.
    manifest_path = runs / "workflow-manifest.json"
    history = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    history.insert(0, {"run_id": run_id, "attempt": attempt,
                       "recorded_at": event["recorded_at"], "json": filename})
    atomic_write(manifest_path, json.dumps(history, indent=2) + "\n")
    return destination


if __name__ == "__main__":
    print(record())
