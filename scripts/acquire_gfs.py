#!/usr/bin/env python3
"""Acquire real NOAA GFS next-day precipitation context for a Nepal calendar day."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.sources.gfs import acquire_gfs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-day", help="Target Nepal calendar day YYYY-MM-DD; defaults to today in Nepal")
    args = parser.parse_args()
    print(acquire_gfs(ROOT, args.target_day), flush=True)
