"""Read immutable runs and atomically published pointers from durable storage.

The scheduler and API must share this directory on a single persistent volume.
No process-local state is required for correctness. Caches are invalidated by
file metadata and can be discarded at any time.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shapely.geometry import Point, shape
from shapely.strtree import STRtree


RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
EMPTY_FORECAST = {"type": "FeatureCollection", "features": [], "availability": "unavailable"}


class SnapshotError(RuntimeError):
    """A publication is incomplete or corrupt and must not be represented as data."""


class RunNotFound(LookupError):
    pass


def validate_run_id(run_id: str) -> str:
    if not RUN_ID.fullmatch(run_id) or ".." in run_id:
        raise ValueError("Invalid run_id; use a published run identifier.")
    return run_id


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def coordinates(item: dict[str, Any]) -> tuple[float, float] | None:
    """Accept normalized records and public Point GeoJSON records."""
    try:
        lat, lon = item.get("lat", item.get("latitude")), item.get("lon", item.get("longitude"))
        if lat is None or lon is None:
            geometry = item.get("geometry") or item.get("point") or {}
            if geometry.get("type") != "Point":
                return None
            lon, lat = geometry["coordinates"][:2]
        lat, lon = float(lat), float(lon)
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (TypeError, ValueError, KeyError, IndexError, AttributeError):
        pass
    return None


class GeometryIndex:
    def __init__(self, collection: dict[str, Any]):
        self.features: list[dict[str, Any]] = []
        self.geometries = []
        for feature in collection.get("features", []):
            try:
                geometry = shape(feature["geometry"])
                if geometry.is_empty or not geometry.is_valid:
                    continue
                self.geometries.append(geometry)
                self.features.append(feature)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
        self.tree = STRtree(self.geometries)

    def containing(self, lat: float, lon: float) -> dict[str, Any] | None:
        point = Point(lon, lat)
        candidates = [
            self.features[int(index)]
            for index in self.tree.query(point)
            if self.geometries[int(index)].covers(point)
        ]
        # Shared cell edges have a deterministic owner, independent of tree order.
        return min(candidates, key=lambda f: str(f.get("properties", {}).get("cell_id", f.get("id", "")))) if candidates else None


class SnapshotStore:
    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir).expanduser().resolve()
        self._cache: OrderedDict[Path, tuple[tuple[int, int], Any]] = OrderedDict()
        self._indexes: OrderedDict[Path, tuple[tuple[int, int], GeometryIndex]] = OrderedDict()
        self._lock = threading.RLock()

    def _path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise SnapshotError("A data path resolves outside the configured data directory.")
        return path

    @staticmethod
    def _fingerprint(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
            return stat.st_mtime_ns, stat.st_size
        except FileNotFoundError:
            return None

    def read(self, relative: str, default: Any = None) -> Any:
        path = self._path(relative)
        fingerprint = self._fingerprint(path)
        if fingerprint is None:
            return default
        with self._lock:
            cached = self._cache.get(path)
            if cached and cached[0] == fingerprint:
                self._cache.move_to_end(path)
                return cached[1]
        try:
            # Opening one inode means a concurrent atomic rename never mixes runs.
            with path.open("r", encoding="utf-8") as stream:
                result = json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Non-finite JSON: {value}")))
        except (OSError, ValueError, UnicodeError) as exc:
            raise SnapshotError(f"Cannot read the published {path.name} snapshot.") from exc
        with self._lock:
            self._cache[path] = (fingerprint, result)
            self._cache.move_to_end(path)
            while len(self._cache) > 12:
                self._cache.popitem(last=False)
        return result

    def current(self) -> dict[str, Any] | None:
        current = self.read("published/current.json")
        if current is None:
            return None
        if not isinstance(current, dict):
            raise SnapshotError("The current publication manifest is malformed.")
        try:
            validate_run_id(current["run_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SnapshotError("The current publication has an invalid run identifier.") from exc
        return current

    def manifest(self, run_id: str | None = None) -> dict[str, Any] | None:
        if run_id is None:
            return self.current()
        run_id = validate_run_id(run_id)
        manifest = self.read(f"published/runs/{run_id}/manifest.json")
        if manifest is None:
            raise RunNotFound(run_id)
        if not isinstance(manifest, dict) or manifest.get("run_id") != run_id:
            raise SnapshotError("The requested publication manifest is malformed.")
        return manifest

    def forecast(self, run_id: str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        manifest = self.manifest(run_id)
        if manifest is None:
            return None, dict(EMPTY_FORECAST)
        run_id = validate_run_id(manifest["run_id"])
        forecast = self.read(f"published/runs/{run_id}/forecast.geojson")
        if not isinstance(forecast, dict) or forecast.get("type") != "FeatureCollection" or not isinstance(forecast.get("features"), list):
            raise SnapshotError("The published forecast is missing or malformed.")
        return manifest, forecast

    def runs(self, limit: int = 100) -> list[dict[str, Any]]:
        directory = self._path("published/runs")
        if not directory.exists():
            return []
        runs = []
        for path in directory.glob("*/manifest.json"):
            try:
                manifest = self.manifest(path.parent.name)
                if manifest is not None and self._path(f"published/runs/{path.parent.name}/forecast.geojson").is_file():
                    runs.append(manifest)
            except (ValueError, RunNotFound, SnapshotError):
                # An incomplete unselected run must not hide complete publications.
                continue
        minimum_time = datetime.min.replace(tzinfo=timezone.utc)
        runs.sort(key=lambda value: (parse_time(value.get("issued_at")) or minimum_time, str(value.get("run_id"))), reverse=True)
        return runs[:limit]

    def _index(self, relative: str, collection: dict[str, Any]) -> GeometryIndex:
        path = self._path(relative)
        fingerprint = self._fingerprint(path)
        with self._lock:
            cached = self._indexes.get(path)
            if cached and cached[0] == fingerprint:
                self._indexes.move_to_end(path)
                return cached[1]
        index = GeometryIndex(collection)
        if fingerprint is not None:
            with self._lock:
                self._indexes[path] = (fingerprint, index)
                self._indexes.move_to_end(path)
                while len(self._indexes) > 4:
                    self._indexes.popitem(last=False)
        return index

    def location(self, lat: float, lon: float, run_id: str | None = None) -> dict[str, Any] | None:
        manifest, collection = self.forecast(run_id)
        if manifest is None:
            return None
        # The country-clipped modeling grid is an additional guard when a forecast
        # accidentally carries an unclipped rectangular cell footprint.
        grid = self.read("processed/grid.geojson")
        if isinstance(grid, dict) and self._index("processed/grid.geojson", grid).containing(lat, lon) is None:
            return None
        return self._index(f"published/runs/{manifest['run_id']}/forecast.geojson", collection).containing(lat, lon)

    def history(self, cell_id: str, limit: int = 30) -> list[dict[str, Any]]:
        result = []
        for manifest in self.runs(limit=limit):
            try:
                _, forecast = self.forecast(manifest["run_id"])
            except (SnapshotError, RunNotFound):
                continue
            properties = next((feature.get("properties", {}) for feature in forecast["features"] if str(feature.get("properties", {}).get("cell_id", feature.get("id", ""))) == cell_id), None)
            if properties is None:
                continue
            result.append({
                **{key: manifest.get(key) for key in ("run_id", "issued_at", "valid_from", "valid_to", "mode")},
                **{key: properties.get(key) for key in ("score", "probability", "risk_level")},
            })
        return result

    def places(self) -> list[dict[str, Any]]:
        data = self.read("processed/places.json", [])
        if not isinstance(data, list):
            raise SnapshotError("The place index is malformed.")
        return data

    def events(self) -> list[dict[str, Any]]:
        data = self.read("processed/events.json", [])
        if isinstance(data, dict) and data.get("type") == "FeatureCollection":
            data = [{**(feature.get("properties") or {}), "geometry": feature.get("geometry")} for feature in data.get("features", [])]
        if not isinstance(data, list):
            raise SnapshotError("The event catalogue is malformed.")
        return [event for event in data if isinstance(event, dict)]
