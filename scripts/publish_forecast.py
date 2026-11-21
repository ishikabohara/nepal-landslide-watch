"""Publish a reproducible daily replay or a genuinely pre-issued operational forecast."""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.app.modeling import predict

parser=argparse.ArgumentParser()
parser.add_argument("day",type=date.fromisoformat)
parser.add_argument("--data-dir",type=Path,default=ROOT/"data")
parser.add_argument("--operational",action="store_true")
args=parser.parse_args()
print(json.dumps(predict(args.data_dir,args.day,operational=args.operational),indent=2))
