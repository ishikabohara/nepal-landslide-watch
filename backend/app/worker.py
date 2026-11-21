"""Single-node scheduler: prepare weather context early, publish before Nepal midnight."""
from __future__ import annotations

import fcntl
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .features import FeatureEngine, NEPAL, issue_time, rainfall_cutoff
from .sources.common import save_json
from .storage import SnapshotStore, parse_time, validate_run_id

LOG = logging.getLogger("nepal.worker")
REFRESH_SECONDS = 1800
REFRESH_RETRY_SECONDS = 300
FORECAST_RETRY_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _load_health(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            return {}
        for component in ("rainfall", "gfs_context", "forecast"):
            if component in value and not isinstance(value[component], dict):
                value.pop(component)
        return value
    except (FileNotFoundError, ValueError):
        return {}


def _record_failure(health: dict, component: str, error: Exception) -> None:
    state = health.setdefault(component, {})
    state.update(status="failed", last_attempt_at=_iso(_now()), error=str(error)[:1200])
    LOG.warning("%s failed: %s", component, error)


def _matching_context(directory: Path, start: datetime) -> bool:
    try:
        context = json.loads((directory / "processed/gfs_forecast.json").read_text())
        return (context.get("status") == "available"
            and parse_time(context.get("valid_from")) == start
            and parse_time(context.get("valid_to")) == start + timedelta(days=1)
            and bool(context.get("cells")))
    except (FileNotFoundError, ValueError, AttributeError):
        return False


def _refresh_gfs(root: Path, target) -> None:
    """Bound optional network work so it cannot monopolize the scheduler."""
    result = subprocess.run([sys.executable, str(root / "scripts/acquire_gfs.py"), "--target-day", target.isoformat()],
        cwd=root, timeout=120, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"GFS context acquisition failed: {(result.stderr or result.stdout)[-1000:]}")


def _published_for_model(directory: Path, target) -> dict | None:
    """Discover completion from immutable files, including after a worker restart."""
    card_path = directory / "model/model_card.json"
    if not card_path.exists():
        return None
    card = json.loads(card_path.read_text())
    version = card.get("model_version")
    if not isinstance(version, str) or not version:
        raise ValueError("The published model card has no model version.")
    identifier = validate_run_id(f"{target.isoformat()}-forecast-{version}")
    run_path = directory / "published/runs" / identifier
    if not (run_path / "manifest.json").exists() or not (run_path / "forecast.geojson").exists():
        return None
    manifest, collection = SnapshotStore(directory).forecast(identifier)
    if (manifest.get("mode") != "operational"
            or parse_time(manifest.get("valid_from")) != issue_time(target)
            or parse_time(manifest.get("valid_to")) != issue_time(target) + timedelta(days=1)
            or manifest.get("model_version") != version
            or manifest.get("cell_count") != len(collection["features"])):
        raise ValueError("An existing operational publication has inconsistent metadata.")
    return manifest


def _input_readiness(directory: Path, target) -> dict:
    engine = FeatureEngine(directory)
    batch = engine.for_day(target)
    valid_cells = int(batch.valid.sum())
    if valid_cells != len(engine.cells) or valid_cells == 0:
        raise ValueError(f"Only {valid_cells}/{len(engine.cells)} cells have complete finite terrain and 14-day rainfall inputs; daily publication is withheld.")
    cutoff = rainfall_cutoff(target)
    if cutoff > _now() - timedelta(hours=4):
        raise ValueError("The required rainfall cutoff is too recent for the declared delivery latency.")
    return {"status": "complete", "valid_cells": valid_cells, "total_cells": len(engine.cells),
        "target_day": target.isoformat(),
        "required_rainfall_cutoff": _iso(cutoff), "first_interval_end_day": batch.rain_days[0],
        "last_interval_end_day": batch.rain_days[-1], "checked_at": _iso(_now())}


def _finish(health_path: Path, health: dict) -> dict:
    now = _now()
    tomorrow = now.astimezone(NEPAL).date() + timedelta(days=1)
    start = issue_time(tomorrow)
    health["errors"] = [
        {"source": component, "message": state["error"]}
        for component in ("rainfall", "gfs_context", "forecast")
        if isinstance((state := health.get(component)), dict) and state.get("error")
    ]
    health.update(status="degraded" if health["errors"] else "ready", checked_at=_iso(now),
        next_forecast_target=_iso(start), next_forecast_attempt_at=_iso(start - timedelta(minutes=10)),
        schedule="Rainfall refresh every 30 minutes; forecast at 23:50 Asia/Kathmandu for the next calendar day. Missed issuance windows are never backdated.")
    if not health["errors"]:
        health["last_success"] = _iso(now)
    save_json(health_path, health)
    return health


def tick(root: Path, *, refresh: bool = True) -> dict:
    directory = root / "data"
    directory.mkdir(parents=True, exist_ok=True)
    health_path = directory / "worker_health.json"
    with (directory / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Only the active owner updates health; contenders cannot erase it.
            return {"status": "another_worker_running"}
        try:
            health = _load_health(health_path)
            health.update(last_attempt=_iso(_now()), status="refreshing" if refresh else "checking")
            scheduled_at = _now()
            target = scheduled_at.astimezone(NEPAL).date() + timedelta(days=1)
            start = issue_time(target)
            window_start = start - timedelta(minutes=10)
            health["refresh_attempted"] = False
            # During issuance, already-complete cached inputs take precedence
            # over periodic network work. Resume refresh after the window closes.
            if refresh and window_start <= scheduled_at < start:
                try:
                    if _published_for_model(directory, target) or _input_readiness(directory, target):
                        refresh = False
                        health["rainfall_refresh_deferred_for_issuance"] = True
                except Exception:
                    pass  # Missing inputs still need a real refresh attempt.
            save_json(health_path, health)
            if refresh:
                health["refresh_attempted"] = True
                health.pop("rainfall_refresh_deferred_for_issuance", None)
                health["rainfall"] = {**health.get("rainfall", {}), "status": "refreshing", "last_attempt_at": _iso(_now())}
                save_json(health_path, health)
                try:
                    from .sources.rainfall import acquire_recent_rainfall
                    acquire_recent_rainfall(root)
                    health["rainfall"] = {"status": "available", "last_attempt_at": health["rainfall"]["last_attempt_at"], "last_success_at": _iso(_now())}
                    health["rainfall_refreshed_at"] = _iso(_now())
                except Exception as exc:
                    _record_failure(health, "rainfall", exc)

            now = _now()
            old_target = parse_time(health.get("forecast", {}).get("valid_from"))
            if old_target is not None and old_target <= now and health["forecast"].get("status") != "published":
                health["last_missed_forecast_target"] = _iso(old_target)

            # Context is optional. Fetch well before issuance, never inside the
            # publication window, and never again after the target is cached.
            if refresh and now < window_start - timedelta(minutes=10):
                cycle = start.replace(hour=6, minute=0, second=0, microsecond=0)
                if _matching_context(directory, start):
                    health["gfs_context"] = {"status": "available", "target_day": target.isoformat(), "model_input": False}
                elif now >= cycle + timedelta(hours=6):
                    retry_at = parse_time(health.get("gfs_context", {}).get("retry_at"))
                    if retry_at is None or now >= retry_at:
                        try:
                            _refresh_gfs(root, target)
                            if not _matching_context(directory, start):
                                raise ValueError("GFS acquisition did not produce context for the required target interval.")
                            health["gfs_context"] = {"status": "available", "target_day": target.isoformat(), "model_input": False, "last_success_at": _iso(_now())}
                        except Exception as exc:
                            _record_failure(health, "gfs_context", exc)
                            health["gfs_context"]["retry_at"] = _iso(_now() + timedelta(seconds=REFRESH_SECONDS))

            # Recheck after source I/O: a slow download can cross midnight.
            now = _now()
            if window_start <= now < start:
                try:
                    existing = _published_for_model(directory, target)
                    if existing:
                        health["forecast"] = {"status": "published", "run_id": existing["run_id"], "valid_from": _iso(start), "target_day": target.isoformat(), "published_at": existing.get("generated_at")}
                        health["published_run_id"] = existing["run_id"]
                        health["forecast_published_at"] = existing.get("generated_at")
                    else:
                        previous = health.get("forecast", {})
                        retry_at = parse_time(previous.get("retry_at")) if previous.get("target_day") == target.isoformat() else None
                        if retry_at is None or now >= retry_at:
                            try:
                                readiness = _input_readiness(directory, target)
                            except Exception as exc:
                                health["forecast_input_readiness"] = {"status": "unavailable", "target_day": target.isoformat(), "checked_at": _iso(_now()), "error": str(exc)[:1200]}
                                raise
                            health["forecast_input_readiness"] = readiness
                            # Complete cached windows remain eligible after a
                            # refresh outage, which remains visible in health.
                            if not window_start <= _now() < start:
                                raise ValueError("The issuance window ended while checking inputs; no forecast was backdated.")
                            from .modeling import predict
                            run = predict(directory, target, operational=True)
                            if run.get("mode") != "operational" or parse_time(run.get("valid_from")) != start:
                                raise ValueError("Publication did not return the requested operational target.")
                            health["forecast"] = {"status": "published", "run_id": run["run_id"], "valid_from": _iso(start), "target_day": target.isoformat(), "published_at": run.get("generated_at", _iso(_now()))}
                            health["published_run_id"] = run["run_id"]
                            health["forecast_published_at"] = run.get("generated_at", _iso(_now()))
                except Exception as exc:
                    _record_failure(health, "forecast", exc)
                    health["forecast"].update(valid_from=_iso(start), target_day=target.isoformat(), retry_at=_iso(_now() + timedelta(seconds=FORECAST_RETRY_SECONDS)))
            elif now >= start:
                health["last_missed_forecast_target"] = _iso(start)
                health["forecast"] = {"status": "missed", "valid_from": _iso(start), "target_day": target.isoformat(), "error": "The issuance window ended during source refresh; no forecast was backdated."}
            return _finish(health_path, health)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def run_forever(root: Path) -> None:
    last_refresh = None
    refresh_delay = REFRESH_SECONDS
    while True:
        try:
            refresh = last_refresh is None or time.monotonic() - last_refresh >= refresh_delay
            result = tick(root, refresh=refresh)
            if refresh and result.get("refresh_attempted"):
                last_refresh = time.monotonic()
                refresh_delay = REFRESH_RETRY_SECONDS if result.get("rainfall", {}).get("status") == "failed" else REFRESH_SECONDS
            LOG.info("Worker status: %s", result["status"])
        except Exception:
            last_refresh, refresh_delay = time.monotonic(), REFRESH_RETRY_SECONDS
            LOG.exception("Worker cycle failed; retaining published data and retrying after a bounded delay")
        time.sleep(30)
