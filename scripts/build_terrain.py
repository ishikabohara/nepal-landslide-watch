"""Download bounded real terrain tiles and aggregate a Nepal 0.1 degree grid."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.terrain import build_terrain

parser = argparse.ArgumentParser()
parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
parser.add_argument("--boundary", type=Path, default=ROOT / "data/processed/boundary.geojson")
args = parser.parse_args()
build_terrain(args.data_dir, args.boundary)
