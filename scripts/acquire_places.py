#!/usr/bin/env python3
"""Download and index real Nepal populated places for map search."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.sources.places import acquire_places


if __name__ == "__main__":
    print(acquire_places(ROOT), flush=True)
