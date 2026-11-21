"""Shared historical/operational features with a strict time-of-availability boundary."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

NEPAL = ZoneInfo("Asia/Kathmandu")
STATIC_FEATURES = ["elevation_m", "slope_deg", "slope_p90_deg", "relief_m", "steep_fraction"]
SEASON_FEATURES = ["season_sin", "season_cos"]
RAIN_FEATURES = ["rain_24h_mm", "rain_72h_mm", "rain_7d_mm", "rain_14d_mm", "antecedent_index_mm", "rain_change_mm"]
FEATURES = STATIC_FEATURES + SEASON_FEATURES + RAIN_FEATURES
LABELS = {
    "elevation_m": "Elevation", "slope_deg": "Median slope", "slope_p90_deg": "Steepest terrain",
    "relief_m": "Local relief", "steep_fraction": "Steep-terrain fraction",
    "season_sin": "Time of year", "season_cos": "Time of year",
    "rain_24h_mm": "Previous 24-hour rainfall", "rain_72h_mm": "Previous 72-hour rainfall",
    "rain_7d_mm": "Previous 7-day rainfall", "rain_14d_mm": "Previous 14-day rainfall",
    "antecedent_index_mm": "Antecedent wetting", "rain_change_mm": "Change in daily rainfall",
}


def issue_time(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=NEPAL).astimezone(timezone.utc)


def rainfall_cutoff(day: date) -> datetime:
    """Prior noon UTC is 6h15 before the target day's midnight Nepal issuance."""
    return datetime.combine(day - timedelta(days=1), time(hour=12), tzinfo=timezone.utc)


@dataclass
class FeatureBatch:
    day: date
    values: np.ndarray
    valid: np.ndarray
    rainfall: np.ndarray
    rain_days: list[str]


class FeatureEngine:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.grid = json.loads((data_dir / "processed/grid.geojson").read_text())
        self.cells = self.grid["features"]
        self.cell_ids = [f["properties"]["cell_id"] for f in self.cells]
        self.static = np.array([[f["properties"].get(name, np.nan)
            if f["properties"].get(name) is not None else np.nan
            for name in STATIC_FEATURES] for f in self.cells], dtype=np.float32)
        archive = np.load(data_dir / "processed/rainfall_daily.npz", allow_pickle=False)
        self.days = archive["days"].astype(str)
        self.day_index = {day: i for i, day in enumerate(self.days)}
        lats, lons = archive["latitudes"], archive["longitudes"]
        lat_idx = np.array([np.abs(lats-f["properties"]["lat"]).argmin() for f in self.cells])
        lon_idx = np.array([np.abs(lons-f["properties"]["lon"]).argmin() for f in self.cells])
        if any(abs(float(lats[i])-f["properties"]["lat"]) > .051 for i, f in zip(lat_idx,self.cells)):
            raise ValueError("Rainfall latitude grid does not cover modeled Nepal")
        if any(abs(float(lons[i])-f["properties"]["lon"]) > .051 for i, f in zip(lon_idx,self.cells)):
            raise ValueError("Rainfall longitude grid does not cover modeled Nepal")
        self.rain = archive["rain_mm"][:, lat_idx, lon_idx].astype(np.float32)
        count = archive["sample_count"]
        self.counts = count[:,lat_idx,lon_idx] if count.ndim == 3 else np.broadcast_to(count[:,None], self.rain.shape)
        self.rain[(self.counts != 48) | (self.rain < 0)] = np.nan
        archive.close()

    def available_days(self) -> list[date]:
        return [date.fromisoformat(day)+timedelta(days=1) for day in self.days
            if all((date.fromisoformat(day)-timedelta(days=k)).isoformat() in self.day_index for k in range(14))]

    def for_day(self, day: date) -> FeatureBatch:
        end = day-timedelta(days=1)
        rain_days = [(end-timedelta(days=13-i)).isoformat() for i in range(14)]
        if any(d not in self.day_index for d in rain_days):
            raise ValueError(f"Incomplete preceding 14-day rainfall archive for {day}")
        rain = self.rain[[self.day_index[d] for d in rain_days]]
        # A missing half-hour is never interpreted as zero precipitation.
        valid = np.isfinite(rain).all(axis=0) & np.isfinite(self.static).all(axis=1)
        phase = 2*np.pi*(day.timetuple().tm_yday-1)/365.2425
        season = np.broadcast_to(np.array([np.sin(phase), np.cos(phase)],dtype=np.float32),(len(self.cells),2))
        weights = np.exp(-np.arange(13,-1,-1)/4).astype(np.float32)
        dynamic = np.stack([rain[-1], rain[-3:].sum(0),rain[-7:].sum(0),rain.sum(0),
            (rain*weights[:,None]).sum(0),rain[-1]-rain[-2]], axis=1)
        return FeatureBatch(day, np.hstack([self.static,season,dynamic]).astype(np.float32),valid,rain,rain_days)


def build_labels(events: list[dict], engine: FeatureEngine) -> tuple[dict[str,set[int]],dict]:
    """Forecast recorded administrative locations, never infer a true slope location.

    The operational prototype's primary target is deliberately BIPAD's reported
    location, whose positional accuracy is unknown. COOLR is retained for audit,
    not silently pooled with those administrative points.
    """
    from shapely.geometry import Point, shape
    lookup = {f["properties"]["cell_id"]:i for i,f in enumerate(engine.cells)}
    geometries = [shape(f["geometry"]) for f in engine.cells]
    by_day: dict[str,set[int]] = {}
    counts = {"raw_events": len(events), "primary_source": "BIPAD", "excluded_other_catalogue":0,
        "excluded_known_earthquake":0,"excluded_outside_grid":0,"excluded_not_approved":0,
        "unique_positive_cell_days":0,"duplicate_cell_days":0,"unknown_trigger_records":0,
        "unverified_location_accuracy_records":0}
    for event in events:
        if event.get("source") != "BIPAD":
            counts["excluded_other_catalogue"] += 1
            continue
        if "earthquake" in str(event.get("trigger","")).lower():
            counts["excluded_known_earthquake"] += 1
            continue
        if event.get("approved") is False or event.get("verified") is False:
            counts["excluded_not_approved"] += 1
            continue
        lat,lon=event.get("lat"),event.get("lon")
        try:
            lat,lon=float(lat),float(lon)
        except (TypeError,ValueError):
            counts["excluded_outside_grid"] += 1
            continue
        if not np.isfinite([lat,lon]).all() or not (-90<=lat<=90 and -180<=lon<=180):
            counts["excluded_outside_grid"] += 1
            continue
        # Floor aligns report coordinates to the native 0.1-degree cell.
        cell_id=f"n{int(np.floor(lat*10)):03d}e{int(np.floor(lon*10)):04d}"
        idx=lookup.get(cell_id)
        if idx is None or not geometries[idx].covers(Point(lon,lat)):
            counts["excluded_outside_grid"] += 1
            continue
        day=str(event.get("date") or event.get("event_date") or "")[:10]
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        cells=by_day.setdefault(day,set())
        counts["duplicate_cell_days"] += idx in cells
        cells.add(idx)
        counts["unknown_trigger_records"] += not event.get("rainfall_confirmed",False)
        counts["unverified_location_accuracy_records"] += event.get("location_accuracy_km") is None
    counts["unique_positive_cell_days"]=sum(map(len,by_day.values()))
    return by_day,counts
