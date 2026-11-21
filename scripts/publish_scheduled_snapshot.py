#!/usr/bin/env python3
"""Refresh inputs and attempt one genuinely prospective daily publication."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.features import NEPAL, issue_time
from backend.app.worker import tick

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("scheduled_snapshot")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-only", action="store_true", help="Refresh source data without waiting for the nightly issue window")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    target = datetime.now(timezone.utc).astimezone(NEPAL).date() + timedelta(days=1)
    deadline = issue_time(target)
    first = tick(ROOT, refresh=True)
    LOG.info("Input refresh: %s", first.get("status"))
    if first.get("rainfall", {}).get("status") != "available":
        LOG.warning("Rainfall refresh unavailable; a complete cached window may still permit publication")
    if args.refresh_only:
        return
    # The model's operational mode permits only the final 20 minutes before
    # Nepal midnight. A delayed runner never backdates a missed issuance.
    window = deadline - timedelta(minutes=10)
    while datetime.now(timezone.utc) < window:
        time.sleep(min(60, max(1, (window - datetime.now(timezone.utc)).total_seconds())))
    while datetime.now(timezone.utc) < deadline:
        result = tick(ROOT, refresh=False)
        forecast = result.get("forecast", {})
        if forecast.get("status") == "published" and forecast.get("target_day") == target.isoformat():
            LOG.info("Published run %s", forecast.get("run_id"))
            return
        LOG.warning("Publication attempt: %s", forecast.get("error", forecast.get("status")))
        time.sleep(60)
    LOG.warning("No prospective run was published before the target-day deadline")


if __name__ == "__main__":
    main()
