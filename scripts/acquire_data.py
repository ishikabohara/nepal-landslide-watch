#!/usr/bin/env python3
"""Download bounded, real Nepal inputs. Run from the repository root."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=["all", "events", "boundary", "rain", "recent"], default="all")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--output-stem", default="rainfall_daily")
    parser.add_argument("--snapshot-id", default=None, help="Pin historical rainfall to a recorded Icechunk snapshot")
    args = parser.parse_args()
    if args.only in {"all", "boundary"}:
        from app.sources.boundary import acquire_boundary
        print(acquire_boundary(ROOT), flush=True)
    if args.only in {"all", "events"}:
        from app.sources.events import acquire_events
        print(acquire_events(ROOT), flush=True)
    if args.only in {"all", "rain"}:
        from app.sources.rainfall import acquire_rainfall
        print(acquire_rainfall(ROOT, args.start, args.end, output_stem=args.output_stem, snapshot_id=args.snapshot_id), flush=True)
    if args.only == "recent":
        from app.sources.rainfall import acquire_recent_rainfall
        print(acquire_recent_rainfall(ROOT), flush=True)


if __name__ == "__main__":
    main()
