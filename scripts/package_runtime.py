#!/usr/bin/env python3
"""Export an allowlisted, checksummed runtime archive; exclude raw reports and secrets."""
from __future__ import annotations

import hashlib
import fcntl
import io
import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.api import PUBLIC_EVENT_FIELDS


def package() -> None:
    data = ROOT / "data"
    required = ["sources.json", "model/model.joblib", "model/model_card.json", "model/xgboost.ubj",
                "processed/grid.geojson", "processed/nepal_boundary.geojson", "processed/places.json",
                "processed/events.json", "processed/rainfall_daily.npz", "published/current.json"]
    missing = [name for name in required if not (data / name).is_file()]
    if missing:
        raise SystemExit(f"Missing runtime inputs: {missing}")
    names = set(required)
    current = json.loads((data / "published/current.json").read_text())
    run_id = current.get("run_id", "")
    if not run_id or Path(run_id).name != run_id or run_id.startswith("."):
        raise ValueError("Current pointer has an invalid run ID")
    for filename in ("manifest.json", "forecast.geojson"):
        relative = f"published/runs/{run_id}/{filename}"
        if not (data / relative).is_file():
            raise ValueError(f"Current pointer references an incomplete run: {run_id}")
        names.add(relative)
    names.update(str(p.relative_to(data)) for p in (data / "processed").glob("*provenance.json"))
    names.update(str(p.relative_to(data)) for p in (data / "published/runs").glob("*/manifest.json") if not p.parent.name.startswith("."))
    names.update(str(p.relative_to(data)) for p in (data / "published/runs").glob("*/forecast.geojson") if not p.parent.name.startswith("."))
    for optional in ["processed/gfs_forecast.json", "processed/rainfall_recent_halfhour.npz", "processed/label_audit.json"]:
        if (data / optional).is_file():
            names.add(optional)
    out = ROOT / "artifacts/runtime"
    out.mkdir(parents=True, exist_ok=True)
    archive = out / "nepal-runtime.tar.gz"
    checksums = {}
    with tarfile.open(archive, "w:gz") as tar:
        for name in sorted(names):
            path = data / name
            if path.is_symlink():
                raise ValueError(f"Refusing symlink: {path}")
            payload = path.read_bytes()
            if name == "processed/events.json":
                # Keep model-required scientific fields but drop names, contacts,
                # report titles and free-form notes before leaving the workstation.
                records = json.loads(payload)
                payload = json.dumps([{k: v for k, v in item.items() if k in PUBLIC_EVENT_FIELDS}
                                      for item in records], ensure_ascii=False).encode()
            checksums[name] = hashlib.sha256(payload).hexdigest()
            entry = tarfile.TarInfo(f"data/{name}")
            entry.size, entry.mode = len(payload), 0o644
            tar.addfile(entry, io.BytesIO(payload))
        payload = json.dumps({"files": checksums, "note": "Events are redacted for runtime distribution; training source hashes refer to original local snapshots."}, indent=2).encode()
        entry = tarfile.TarInfo("data/runtime_checksums.json")
        entry.size, entry.mode = len(payload), 0o644
        tar.addfile(entry, io.BytesIO(payload))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out / "nepal-runtime.tar.gz.sha256").write_text(f"{digest}  nepal-runtime.tar.gz\n")
    print(f"{archive}\n{archive.stat().st_size:,} bytes; {len(checksums)} files\nSHA256 {digest}")


def main() -> None:
    data = ROOT / "data"
    if not data.is_dir():
        raise SystemExit("No data directory; acquire and train the real inputs first.")
    # Match the scheduler's lock order, then exclude standalone publishers.
    # This protects the pointer/run pair and stops source refresh during export.
    with (data / "worker.lock").open("a") as worker, (data / "publish.lock").open("a") as publisher:
        fcntl.flock(worker.fileno(), fcntl.LOCK_EX)
        fcntl.flock(publisher.fileno(), fcntl.LOCK_EX)
        package()


if __name__ == "__main__":
    main()
