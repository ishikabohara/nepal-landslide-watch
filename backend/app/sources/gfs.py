"""Actual NOAA GFS precipitation context, with explicit accumulation accounting.

This is meteorological context only. The first landslide model does not train on
GFS; adding these values to an explanation would misrepresent that model.

Primary documentation:
https://www.nco.ncep.noaa.gov/pmb/products/gfs/
https://www.cpc.ncep.noaa.gov/products/wesley/wgrib2/ncep_norm.html
https://registry.opendata.aws/noaa-gfs-bdp-pds/
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import xarray as xr

from .common import save_json, sha256, update_source, utc_now


S3_BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
KATHMANDU = ZoneInfo("Asia/Kathmandu")
STEP_PATTERN = re.compile(r":APCP:surface:(\d+)-(\d+) hour acc fcst:")


@dataclass(frozen=True)
class PrecipitationMessage:
    start_byte: int
    end_byte: int
    start_hour: int
    end_hour: int
    index_line: str


def target_window(target: date) -> tuple[datetime, datetime, datetime]:
    """Use the previous UTC day's 06Z cycle for a midnight Nepal issuance."""
    valid_from = datetime.combine(target, time.min, KATHMANDU).astimezone(timezone.utc)
    valid_to = valid_from + timedelta(days=1)
    cycle = valid_from.replace(hour=6, minute=0, second=0, microsecond=0)
    if cycle >= valid_from:
        raise ValueError("The selected GFS cycle must precede forecast issuance.")
    return cycle, valid_from, valid_to


def select_precipitation(index_text: str, forecast_hour: int) -> PrecipitationMessage:
    """Select the shortest APCP interval and bound it with the next GRIB offset."""
    lines = [line for line in index_text.splitlines() if line.strip()]
    candidates = []
    for position, line in enumerate(lines):
        match = STEP_PATTERN.search(line)
        if not match:
            continue
        start_hour, end_hour = map(int, match.groups())
        if end_hour != forecast_hour or start_hour >= end_hour:
            continue
        if position + 1 >= len(lines):
            raise ValueError("Cannot bound the APCP message without the next GRIB offset.")
        start_byte = int(line.split(":", 2)[1])
        end_byte = int(lines[position + 1].split(":", 2)[1]) - 1
        if not (0 <= start_byte <= end_byte) or end_byte - start_byte > 20_000_000:
            raise ValueError("The APCP byte range is invalid or unexpectedly large.")
        candidates.append(PrecipitationMessage(start_byte, end_byte, start_hour, end_hour, line))
    if not candidates:
        raise ValueError(f"No surface APCP accumulation ending at forecast hour {forecast_hour}.")
    return min(candidates, key=lambda message: message.end_hour - message.start_hour)


def normalize_accumulations(
    fields: list[tuple[int, int, np.ndarray]],
) -> list[tuple[int, int, np.ndarray]]:
    """Turn GFS's alternating 3h/6h totals into non-overlapping 3h intervals.

The 12–18 total contains the 12–15 total. Subtract the latter; summing both
double-counts precipitation. Larger negative differences indicate invalid
inputs. Tiny GRIB packing differences (<=0.05 mm) are clipped to zero.
"""
    normalized = []
    previous: dict[tuple[int, int], np.ndarray] = {}
    for start, end, values in sorted(fields, key=lambda field: field[1]):
        values = np.asarray(values, dtype=np.float64)
        if not np.all(np.isfinite(values)) or np.min(values) < -0.05:
            raise ValueError("GFS precipitation contains missing or invalid values.")
        if end - start == 3:
            increment = values.copy()
            interval_start = start
        elif end - start == 6:
            earlier = previous.get((start, end - 3))
            if earlier is None:
                raise ValueError("A six-hour APCP total is missing its preceding three-hour total.")
            if earlier.shape != values.shape:
                raise ValueError("The GFS accumulation grids do not match.")
            increment = values - earlier
            interval_start = end - 3
        else:
            raise ValueError(f"Unsupported GFS accumulation length: {end - start} hours.")
        if np.min(increment) < -0.05:
            raise ValueError("GFS cumulative precipitation decreased beyond GRIB packing tolerance.")
        previous[(start, end)] = values
        normalized.append((interval_start, end, np.maximum(increment, 0)))
    return normalized


def integrate_window(
    intervals: list[tuple[int, int, np.ndarray]], start_hour: float, end_hour: float,
) -> np.ndarray:
    """Integrate overlap, assuming a constant rate inside each 3h interval."""
    if start_hour >= end_hour or not intervals:
        raise ValueError("A nonempty forecast window is required.")
    total = np.zeros_like(intervals[0][2], dtype=np.float64)
    cursor = start_hour
    for start, end, values in sorted(intervals, key=lambda field: field[0]):
        overlap_start, overlap_end = max(start_hour, start), min(end_hour, end)
        if overlap_start >= overlap_end:
            continue
        if abs(overlap_start - cursor) > 1e-9:
            raise ValueError("GFS accumulation intervals contain a gap or overlap.")
        total += values * ((overlap_end - overlap_start) / (end - start))
        cursor = overlap_end
    if abs(cursor - end_hour) > 1e-9:
        raise ValueError("GFS precipitation does not cover the whole target day.")
    return total


def _download_message(client: httpx.Client, url: str, message: PrecipitationMessage, path: Path) -> dict:
    expected_bytes = message.end_byte - message.start_byte + 1
    sidecar = path.with_suffix(".json")
    if path.exists() and sidecar.exists():
        metadata = json.loads(sidecar.read_text())
        if path.stat().st_size == expected_bytes and metadata.get("sha256") == sha256(path):
            return metadata
    # Stream headers first, so an ignored Range cannot download the whole file.
    with client.stream("GET", url, headers={"Range": f"bytes={message.start_byte}-{message.end_byte}"}) as response:
        response.raise_for_status()
        if response.status_code != 206 or not response.headers.get("content-range", "").startswith(f"bytes {message.start_byte}-{message.end_byte}/"):
            raise ValueError("NOAA did not honor the bounded GRIB byte-range request.")
        payload = bytearray()
        for chunk in response.iter_bytes():
            payload.extend(chunk)
            if len(payload) > expected_bytes:
                raise ValueError("NOAA returned more than the requested GRIB message.")
    if len(payload) != expected_bytes or not payload.startswith(b"GRIB") or not payload.endswith(b"7777"):
        raise ValueError("The downloaded GRIB message is incomplete.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".grib2.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
    metadata = {
        "source_url": url, "index_url": url + ".idx", "index_line": message.index_line,
        "range_start": message.start_byte, "range_end": message.end_byte,
        "sha256": hashlib.sha256(payload).hexdigest(), "retrieved_at": utc_now(),
        "start_hour": message.start_hour, "end_hour": message.end_hour,
    }
    save_json(sidecar, metadata)
    return metadata


def _decode_cells(path: Path, cells: list[dict], message: PrecipitationMessage, cycle: datetime) -> tuple[np.ndarray, dict]:
    with xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": "", "read_keys": ["startStep", "endStep"]}) as dataset:
        if len(dataset.data_vars) != 1:
            raise ValueError("The bounded GRIB must contain exactly one precipitation field.")
        field = dataset[next(iter(dataset.data_vars))]
        attrs = field.attrs
        if attrs.get("GRIB_stepType") != "accum" or attrs.get("GRIB_typeOfLevel") != "surface" or attrs.get("GRIB_paramId") != 228228:
            raise ValueError("The downloaded GRIB is not surface total precipitation.")
        if attrs.get("GRIB_units") not in {"kg m**-2", "kg m-2", "kg/m^2"}:
            raise ValueError(f"Unsupported precipitation units: {attrs.get('GRIB_units')}")
        if attrs.get("GRIB_startStep") != message.start_hour or attrs.get("GRIB_endStep") != message.end_hour:
            raise ValueError("The GRIB accumulation interval disagrees with its index.")
        actual_cycle = np.datetime64(dataset.time.values, "s")
        if actual_cycle != np.datetime64(cycle.replace(tzinfo=None), "s"):
            raise ValueError("The downloaded GRIB belongs to another forecast cycle.")
        latitudes = xr.DataArray([cell["properties"]["lat"] for cell in cells], dims="cell")
        longitudes = xr.DataArray([cell["properties"]["lon"] for cell in cells], dims="cell")
        selected = field.sel(latitude=latitudes, longitude=longitudes, method="nearest", tolerance=0.126).load()
        values = np.asarray(selected.values, dtype=np.float64)
        return values, {"units": attrs["GRIB_units"], "sample_latitudes": selected.latitude.values.tolist(), "sample_longitudes": selected.longitude.values.tolist()}


def acquire_gfs(root: Path, target_day: str | None = None) -> Path:
    target = date.fromisoformat(target_day) if target_day else datetime.now(KATHMANDU).date()
    cycle, valid_from, valid_to = target_window(target)
    cells = json.loads((root / "data/processed/grid.geojson").read_text())["features"]
    if not cells:
        raise ValueError("A real Nepal modeling grid is required before fetching GFS.")
    start_hour = (valid_from - cycle).total_seconds() / 3600
    end_hour = (valid_to - cycle).total_seconds() / 3600
    hours = range(3 * (math.floor(start_hour / 3) + 1), 3 * math.ceil(end_hour / 3) + 1, 3)
    fields, provenance, selected_metadata = [], [], None
    with httpx.Client(timeout=90, follow_redirects=True, headers={"User-Agent": "NepalLandslideResearch/0.1"}) as client:
        for hour in hours:
            base_url = f"{S3_BASE}/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos/gfs.t{cycle:%H}z.pgrb2.0p25.f{hour:03d}"
            directory = root / f"data/raw/gfs/{cycle:%Y%m%d%H}"
            directory.mkdir(parents=True, exist_ok=True)
            index_path = directory / f"f{hour:03d}.idx"
            if not index_path.exists():
                response = client.get(base_url + ".idx")
                response.raise_for_status()
                temporary = index_path.with_suffix(".idx.tmp")
                temporary.write_text(response.text)
                temporary.replace(index_path)
            message = select_precipitation(index_path.read_text(), hour)
            path = directory / f"apcp_f{hour:03d}.grib2"
            metadata = _download_message(client, base_url, message, path)
            values, selected_metadata = _decode_cells(path, cells, message, cycle)
            fields.append((message.start_hour, message.end_hour, values))
            provenance.append(metadata)
    normalized = normalize_accumulations(fields)
    total = integrate_window(normalized, start_hour, end_hour)
    if not np.all(np.isfinite(total)):
        raise ValueError("The target-day forecast contains missing values.")
    iso = lambda value: value.isoformat().replace("+00:00", "Z")
    output = {
        "status": "available", "provider": "NOAA GFS", "product": "gfs.pgrb2.0p25 APCP surface",
        "cycle": iso(cycle), "issued_at": iso(valid_from), "valid_from": iso(valid_from), "valid_to": iso(valid_to),
        "target_date_nepal": target.isoformat(), "generated_at": utc_now(), "grid_resolution_degrees": 0.25,
        "units": "mm water equivalent", "role": "context_only_not_used_by_landslide_model",
        "model_input": False, "source_urls": [item["source_url"] for item in provenance], "provenance": provenance,
        "method": "Shortest available surface APCP accumulation; subtract nested six-hour totals to get consecutive three-hour totals; integrate exact overlap with Nepal calendar day.",
        "limitations": [
            "The first landslide model does not use this forecast as an input or explanatory driver.",
            "Nepal midnight falls 15 minutes into a three-hour model interval. Boundary intervals are weighted assuming uniform precipitation within each three-hour interval.",
            "The nearest 0.25-degree forecast grid point is assigned to each 0.1-degree Nepal cell; this does not create finer weather resolution.",
            "APCP is total liquid-water-equivalent precipitation, including frozen precipitation; it is not strictly rainfall at high elevations.",
            "Retrospective retrieval proves the forecast cycle existed, not that every file arrived by historical issuance. Retrieval times are preserved for prospective auditing.",
        ],
        "cells": {
            str(cell["properties"]["cell_id"]): {
                "forecast_rain_24h_mm": round(float(total[index]), 3),
                "forecast_source_lat": selected_metadata["sample_latitudes"][index],
                "forecast_source_lon": selected_metadata["sample_longitudes"][index],
            }
            for index, cell in enumerate(cells)
        },
    }
    # Preserve a cycle-and-target snapshot, then replace the public context atomically.
    archive = root / f"data/processed/gfs/{target.isoformat()}_{cycle:%Y%m%d%H}.json"
    if not archive.exists():
        save_json(archive, output)
    else:
        existing = json.loads(archive.read_text())
        if existing.get("cells") != output["cells"] or existing.get("source_urls") != output["source_urls"]:
            raise ValueError("A saved GFS snapshot changed; create a versioned revision instead of overwriting it.")
        output = existing
    destination = root / "data/processed/gfs_forecast.json"
    save_json(destination, output)
    update_source(root, {
        "id": "gfs", "name": "NOAA GFS forecast precipitation", "url": "https://registry.opendata.aws/noaa-gfs-bdp-pds/",
        "status": "available", "cycle": iso(cycle), "valid_from": iso(valid_from), "valid_to": iso(valid_to),
        "accessed_at": utc_now(), "resolution_degrees": 0.25, "model_input": False,
        "sha256": sha256(archive), "notes": output["limitations"],
    })
    return destination
