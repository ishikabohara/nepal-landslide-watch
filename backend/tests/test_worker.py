"""Scheduling, outage and supervisor checks using only isolated test inputs."""

import fcntl
import json
import subprocess
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app import worker
from backend.app.features import issue_time
from backend.app.main import create_app
from backend.app.sources.common import save_json
from backend.tests.test_features import change_rain_archive, feature_data


@pytest.fixture
def scheduled(feature_data, monkeypatch):
    from backend.app import modeling
    from backend.app.sources import rainfall

    root = feature_data.parent
    clock = [datetime(2024, 1, 14, 18, 5, tzinfo=timezone.utc)]  # 23:50 Nepal
    calls = []
    monkeypatch.setattr(worker, "_now", lambda: clock[0])
    save_json(feature_data / "model/model_card.json", {"model_version": "fixture-v1"})

    def publish(directory, target, operational=False):
        assert operational
        calls.append(target)
        version = json.loads((directory / "model/model_card.json").read_text())["model_version"]
        identifier = f"{target.isoformat()}-forecast-{version}"
        collection = json.loads((directory / "processed/grid.geojson").read_text())
        start = issue_time(target)
        manifest = {"run_id": identifier, "mode": "operational", "model_version": version,
            "issued_at": clock[0].isoformat(), "generated_at": clock[0].isoformat(),
            "valid_from": start.isoformat(), "valid_to": (start + timedelta(days=1)).isoformat(),
            "cell_count": len(collection["features"])}
        save_json(directory / "published/runs" / identifier / "forecast.geojson", collection)
        save_json(directory / "published/runs" / identifier / "manifest.json", manifest)
        save_json(directory / "published/current.json", manifest)
        return manifest

    def forbidden_network(*args, **kwargs):
        raise AssertionError("This test must not use network acquisition.")

    monkeypatch.setattr(modeling, "predict", publish)
    monkeypatch.setattr(rainfall, "acquire_recent_rainfall", forbidden_network)
    monkeypatch.setattr(worker, "_refresh_gfs", forbidden_network)
    return root, feature_data, clock, calls


def test_2350_nepal_schedule_publishes_next_day_once_and_survives_restart(scheduled):
    root, directory, clock, calls = scheduled
    clock[0] -= timedelta(seconds=1)
    before = worker.tick(root, refresh=False)
    assert calls == []
    assert before["next_forecast_attempt_at"] == "2024-01-14T18:05:00Z"
    clock[0] += timedelta(seconds=1)
    first = worker.tick(root, refresh=False)
    assert calls == [date(2024, 1, 15)]
    assert first["forecast"]["status"] == "published"
    # Delete worker-local state to simulate a fresh process after publication.
    (directory / "worker_health.json").unlink()
    clock[0] += timedelta(minutes=2)
    repeated = worker.tick(root, refresh=False)
    assert repeated["published_run_id"] == first["published_run_id"]
    assert len(calls) == 1
    # A new model revision receives its own immutable publication identifier.
    save_json(directory / "model/model_card.json", {"model_version": "fixture-v2"})
    changed = worker.tick(root, refresh=False)
    assert changed["published_run_id"].endswith("fixture-v2")
    assert len(calls) == 2


def test_starting_at_or_after_midnight_never_backdates(scheduled):
    root, _, clock, calls = scheduled
    for minutes in (10, 20):
        clock[0] = datetime(2024, 1, 14, 18, 5, tzinfo=timezone.utc) + timedelta(minutes=minutes)
        state = worker.tick(root, refresh=False)
        assert state["next_forecast_target"] == "2024-01-15T18:15:00Z"
    assert calls == []


def test_cached_complete_inputs_publish_with_visible_persistent_refresh_failure(scheduled):
    root, directory, _, calls = scheduled
    save_json(directory / "worker_health.json", {"rainfall": {"status": "failed", "error": "Upstream unavailable"}})
    state = worker.tick(root, refresh=False)
    assert len(calls) == 1
    assert state["forecast_input_readiness"]["status"] == "complete"
    assert state["forecast"]["status"] == "published"
    assert state["status"] == "degraded"
    assert state["errors"] == [{"source": "rainfall", "message": "Upstream unavailable"}]


def test_cached_complete_inputs_defer_network_during_issuance(scheduled):
    root, _, _, calls = scheduled
    state = worker.tick(root, refresh=True)
    assert len(calls) == 1
    assert state["refresh_attempted"] is False
    assert state["rainfall_refresh_deferred_for_issuance"] is True
    assert state["status"] == "ready"


def test_incomplete_inputs_block_publication_and_retry_is_bounded(scheduled, monkeypatch):
    root, directory, clock, calls = scheduled
    change_rain_archive(directory, lambda arrays: arrays["sample_count"].__setitem__((13, 0, 0), 47))
    state = worker.tick(root, refresh=False)
    assert state["forecast"]["status"] == "failed"
    assert "complete" in state["forecast"]["error"]
    assert calls == []
    clock[0] += timedelta(seconds=30)
    readiness_calls = []
    monkeypatch.setattr(worker, "_input_readiness", lambda *args: readiness_calls.append(args))
    retry = worker.tick(root, refresh=False)
    assert retry["status"] == "degraded"
    assert readiness_calls == []
    assert calls == []


def test_slow_failed_refresh_that_crosses_midnight_is_never_backdated(scheduled, monkeypatch):
    from backend.app.sources import rainfall

    root, directory, clock, calls = scheduled
    change_rain_archive(directory, lambda arrays: arrays["sample_count"].__setitem__((13, 0, 0), 47))

    def slow_failure(*args):
        clock[0] = datetime(2024, 1, 14, 18, 16, tzinfo=timezone.utc)
        raise TimeoutError("Rainfall request timed out")

    monkeypatch.setattr(rainfall, "acquire_recent_rainfall", slow_failure)
    state = worker.tick(root, refresh=True)
    assert calls == []
    assert state["forecast"]["status"] == "missed"
    assert state["last_missed_forecast_target"] == "2024-01-14T18:15:00Z"
    assert state["next_forecast_target"] == "2024-01-15T18:15:00Z"


def test_refresh_failure_does_not_disappear_on_no_refresh_tick(scheduled, monkeypatch):
    from backend.app.sources import rainfall

    root, _, clock, _ = scheduled
    clock[0] = datetime(2024, 1, 14, 11, tzinfo=timezone.utc)

    def failure(*args):
        raise ConnectionError("Rainfall provider unavailable")

    monkeypatch.setattr(rainfall, "acquire_recent_rainfall", failure)
    first = worker.tick(root, refresh=True)
    clock[0] += timedelta(seconds=30)
    second = worker.tick(root, refresh=False)
    assert first["errors"] == second["errors"]
    assert second["status"] == "degraded"
    monkeypatch.setattr(rainfall, "acquire_recent_rainfall", lambda *args: None)
    recovered = worker.tick(root, refresh=True)
    assert recovered["status"] == "ready"
    assert recovered["errors"] == []


def test_gfs_prefetch_runs_once_outside_issuance_window(scheduled, monkeypatch):
    from backend.app.sources import rainfall

    root, directory, clock, _ = scheduled
    clock[0] = datetime(2024, 1, 14, 12, tzinfo=timezone.utc)
    calls = []
    monkeypatch.setattr(rainfall, "acquire_recent_rainfall", lambda *args: None)

    def context(root, target):
        calls.append(target)
        start = issue_time(target)
        save_json(directory / "processed/gfs_forecast.json", {"status": "available", "valid_from": start.isoformat(),
            "valid_to": (start + timedelta(days=1)).isoformat(), "cells": {"fixture": {"forecast_rain_24h_mm": 3}}})

    monkeypatch.setattr(worker, "_refresh_gfs", context)
    worker.tick(root, refresh=True)
    worker.tick(root, refresh=True)
    assert calls == [date(2024, 1, 15)]


def test_gfs_failure_is_optional_and_never_retried_inside_issuance(scheduled, monkeypatch):
    from backend.app.sources import rainfall

    root, _, clock, published = scheduled
    clock[0] = datetime(2024, 1, 14, 12, tzinfo=timezone.utc)
    attempts = []
    monkeypatch.setattr(rainfall, "acquire_recent_rainfall", lambda *args: None)

    def failure(*args):
        attempts.append(True)
        raise TimeoutError("GFS temporarily unavailable")

    monkeypatch.setattr(worker, "_refresh_gfs", failure)
    worker.tick(root, refresh=True)
    clock[0] = datetime(2024, 1, 14, 18, 5, tzinfo=timezone.utc)
    state = worker.tick(root, refresh=False)
    assert len(attempts) == 1
    assert len(published) == 1
    assert state["forecast"]["status"] == "published"
    assert state["status"] == "degraded"


def test_worker_lock_contender_does_not_overwrite_owner_health(scheduled):
    root, directory, _, calls = scheduled
    path = directory / "worker_health.json"
    save_json(path, {"status": "refreshing", "owner": "fixture"})
    before = path.read_bytes()
    with (directory / "worker.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert worker.tick(root, refresh=True) == {"status": "another_worker_running"}
        assert path.read_bytes() == before
    assert calls == []


def test_status_exposes_worker_health_without_hiding_forecasts_on_health_corruption(scheduled):
    _, directory, _, _ = scheduled
    client = TestClient(create_app(data_dir=directory))
    assert client.get("/api/status").json()["worker_health"] is None
    save_json(directory / "worker_health.json", {"status": "degraded", "checked_at": "2024-01-14T18:05:00Z", "errors": [{"source": "rainfall", "message": "Outage"}]})
    health = client.get("/api/status").json()["worker_health"]
    assert health["status"] == "degraded"
    assert health["heartbeat_stale"] is True
    (directory / "worker_health.json").write_text('{"invalid"')
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["worker_health"]["status"] == "unavailable"


def test_optional_context_subprocess_has_a_hard_timeout(tmp_path, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(worker.subprocess, "run", run)
    worker._refresh_gfs(tmp_path, date(2024, 1, 15))
    assert calls[0][1]["timeout"] == 120
    assert calls[0][0][-2:] == ["--target-day", "2024-01-15"]


def test_service_supervisor_stops_and_reaps_other_children_on_failure(monkeypatch):
    from scripts import run_service

    class Child:
        def __init__(self, code=None):
            self.code, self.terminated, self.reaped = code, False, False

        def poll(self):
            return self.code

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            self.reaped = True
            return self.code or 0

    children = [Child(4), Child()]
    queue = iter(children)
    monkeypatch.setenv("NEPAL_WORKER_ENABLED", "1")
    monkeypatch.delenv("NEPAL_DATA_DIR", raising=False)
    monkeypatch.setattr(run_service.signal, "signal", lambda *args: None)
    monkeypatch.setattr(run_service.subprocess, "Popen", lambda *args, **kwargs: next(queue))
    assert run_service.main() == 4
    assert children[1].terminated
    assert all(child.reaped for child in children)


def test_service_rejects_divergent_worker_and_api_data_paths(tmp_path, monkeypatch):
    from scripts import run_service

    monkeypatch.setenv("NEPAL_WORKER_ENABLED", "1")
    monkeypatch.setenv("NEPAL_DATA_DIR", str(tmp_path / "unshared"))
    with pytest.raises(ValueError, match="source adapters"):
        run_service.main()


def test_supervisor_kills_and_reaps_an_unresponsive_owned_child():
    from scripts import run_service

    class UnresponsiveChild:
        def __init__(self):
            self.terminated = self.killed = self.reaped = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, timeout):
            if not self.killed:
                raise subprocess.TimeoutExpired("fixture child", timeout)
            self.reaped = True

    child = UnresponsiveChild()
    run_service.stop_children([child], timeout=0)
    assert child.terminated and child.killed and child.reaped
