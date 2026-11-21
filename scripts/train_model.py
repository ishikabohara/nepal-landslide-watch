"""Train the research model exclusively on acquired real source data."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.app.modeling import train

parser=argparse.ArgumentParser()
parser.add_argument("--data-dir",type=Path,default=ROOT/"data")
args=parser.parse_args()
card=train(args.data_dir)
print(json.dumps(card["metrics"],indent=2))
