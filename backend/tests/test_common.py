"""Persistence checks for concurrent real-source provenance writers."""

import json
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.app.sources.common import save_json, update_source


def source_writer(root: str, identifier: int):
    update_source(Path(root), {"id": str(identifier), "status": "fixture"})


def test_source_updates_preserve_all_concurrent_threads_and_processes(tmp_path):
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda number: source_writer(str(tmp_path), number), range(20)))
    with ProcessPoolExecutor(max_workers=3) as pool:
        results = [pool.submit(source_writer, str(tmp_path), number) for number in range(20, 32)]
        for result in results:
            result.result(timeout=30)
    records = json.loads((tmp_path / "data/sources.json").read_text())
    assert {item["id"] for item in records} == {str(number) for number in range(32)}
    assert len(records) == 32
    update_source(tmp_path, {"id": "1", "status": "replacement"})
    updated = json.loads((tmp_path / "data/sources.json").read_text())
    assert len(updated) == 32
    assert next(item for item in updated if item["id"] == "1")["status"] == "replacement"


def test_rejecting_nonfinite_json_preserves_previous_snapshot_and_cleans_temp(tmp_path):
    path = tmp_path / "snapshot.json"
    save_json(path, {"value": None})
    with pytest.raises(ValueError):
        save_json(path, {"value": float("nan")})
    assert json.loads(path.read_text()) == {"value": None}
    assert list(tmp_path.iterdir()) == [path]
