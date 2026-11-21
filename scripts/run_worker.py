import argparse
import logging
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.app.worker import run_forever,tick

parser=argparse.ArgumentParser()
parser.add_argument("--once",action="store_true")
args=parser.parse_args()
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
if args.once:
    print(tick(ROOT))
else:
    run_forever(ROOT)
