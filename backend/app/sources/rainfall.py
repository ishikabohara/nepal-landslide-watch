"""NASA IMERG Early from dynamical.org's public, versioned Icechunk replica.

All calculations use actual half-hour rates. No missing-value interpolation.
Daily files are labeled by the UTC DATE OF THE INTERVAL END at 12:00.
"""
from __future__ import annotations

from pathlib import Path
import fcntl
import json
import tempfile
from urllib.parse import urlparse

from .common import get_json, save_json, sha256, update_source, utc_now

STAC_URL = "https://stac.dynamical.org/nasa-imerg-analysis-early/collection.json"
CATALOG_URL = "https://dynamical.org/catalog/nasa-imerg-analysis-early/"


def acquire_recent_rainfall(root: Path, days: int = 17, *, merge_recent: bool = True) -> Path:
    """Refresh 17 days and merge complete daily periods into the active archive.

    Set merge_recent=False to acquire a strip without modifying daily inputs.
    """
    import icechunk
    import numpy as np
    import xarray as xr

    if not 16 <= days <= 30:
        raise ValueError("Recent acquisition must cover 16–30 days to support 14 complete daily features")
    catalog = get_json(STAC_URL)
    address = urlparse(catalog["assets"]["icechunk"]["href"])
    storage = icechunk.s3_storage(bucket=address.netloc, prefix=address.path.strip("/"),
        region="us-west-2", anonymous=True)
    session = icechunk.Repository.open(storage).readonly_session("main")
    dataset = xr.open_zarr(session.store, chunks=None, consolidated=False)
    variable = dataset.precipitation_surface
    if variable.attrs.get("units") != "kg m-2 s-1":
        raise ValueError("Unexpected rainfall units")
    variable = variable.sel(latitude=slice(30.55, 26.25), longitude=slice(80, 88.3)).isel(time=slice(-days * 48, None))
    values = np.asarray(variable.values, dtype="float32")
    values[(values < 0) | ~np.isfinite(values)] = np.nan
    times = np.asarray(variable.time.values).astype("datetime64[s]")
    if not np.all(np.diff(times) == np.timedelta64(30, "m")):
        raise ValueError("Recent rainfall timestamps are not a contiguous half-hour sequence")
    output = root / "data/processed/rainfall_recent_halfhour.npz"
    _save_npz(output, times=times.astype(str), latitudes=variable.latitude.values,
        longitudes=variable.longitude.values, rain_mm=values * np.float32(1800))
    metadata = {"source": "NASA IMERG Early Collection 07 via dynamical.org", "catalog_url": CATALOG_URL,
        "snapshot_id": str(session.snapshot_id), "accessed_at": utc_now(),
        "latest_observation_start": str(times[-1]) + "Z", "latest_observation_end": str(times[-1] + np.timedelta64(30, "m")) + "Z",
        "source_units": "kg m-2 s-1", "output_units": "mm per half hour", "conversion": "multiply by 1800 seconds",
        "time_coordinate": "start of half-hour interval", "shape": list(values.shape),
        "valid_fraction": float(np.isfinite(values).mean()), "sha256": sha256(output),
        "revision": "V07C for observations from 2026-03-04; audit deployment shift from historical V07B"}
    save_json(root / "data/processed/rainfall_recent_provenance.json", metadata)
    if merge_recent:
        merge_recent_daily(root, times, variable.latitude.values, variable.longitude.values,
            values * np.float32(1800), metadata)
    source_path = root / "data/sources.json"
    sources = json.loads(source_path.read_text()) if source_path.exists() else []
    existing = next((record for record in sources if record.get("id") == "imerg"), {})
    update_source(root, {**existing, "id": "imerg", "name": "NASA IMERG Early via dynamical.org",
        "url": CATALOG_URL, "status": "available", "accessed_at": metadata["accessed_at"],
        "latest_observation_at": metadata["latest_observation_end"], "recent_snapshot_id": metadata["snapshot_id"]})
    dataset.close()
    return output


def merge_recent_daily(root: Path, times, latitudes, longitudes, halfhour_mm, metadata: dict) -> Path:
    """Append/replace real, complete daily periods without filling absent dates.

    A cross-process lock protects the read/merge/write cycle. Readers see either
    the previous complete NPZ or the new complete NPZ through atomic replacement.
    """
    import numpy as np

    times = np.asarray(times).astype("datetime64[s]")
    labels = (times - np.timedelta64(12, "h")).astype("datetime64[D]") + np.timedelta64(1, "D")
    days, daily, counts = [], [], []
    for day in np.unique(labels):
        selected = labels == day
        expected_start = day.astype("datetime64[s]") - np.timedelta64(12, "h")
        if selected.sum() != 48 or times[selected][0] != expected_start:
            continue
        values = halfhour_mm[selected]
        count = np.isfinite(values).sum(axis=0).astype("uint8")
        total = np.nansum(values, axis=0, dtype="float32")
        total[count != 48] = np.nan
        days.append(str(day))
        daily.append(total)
        counts.append(count)
    if not days:
        raise ValueError("Recent rainfall has no complete noon-to-noon daily interval")
    output = root / "data/processed/rainfall_daily.npz"
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.with_suffix(".merge.lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        previous_hash = sha256(output) if output.exists() else None
        incoming_days = np.asarray(days)
        incoming_daily = np.asarray(daily, dtype="float32")
        incoming_counts = np.asarray(counts, dtype="uint8")
        if output.exists():
            with np.load(output, allow_pickle=False) as existing:
                if not np.array_equal(existing["latitudes"], latitudes) or not np.array_equal(existing["longitudes"], longitudes):
                    raise ValueError("Recent rainfall coordinates differ from the existing historical grid")
                old_days = existing["days"].astype(str)
                preserve = ~np.isin(old_days, incoming_days)
                merged_days = np.concatenate([old_days[preserve], incoming_days])
                merged_rain = np.concatenate([existing["rain_mm"][preserve], incoming_daily])
                merged_counts = np.concatenate([existing["sample_count"][preserve], incoming_counts])
        else:
            merged_days, merged_rain, merged_counts = incoming_days, incoming_daily, incoming_counts
        order = np.argsort(merged_days)
        _save_npz(output, days=merged_days[order], latitudes=latitudes, longitudes=longitudes,
            rain_mm=merged_rain[order], sample_count=merged_counts[order])
        provenance_path = root / "data/processed/rainfall_provenance.json"
        provenance = json.loads(provenance_path.read_text()) if provenance_path.exists() else {}
        provenance.setdefault("historical_archive_sha256", previous_hash)
        provenance.update({"sha256": sha256(output), "daily_start": str(merged_days[order][0]),
            "daily_end": str(merged_days[order][-1]), "shape": list(merged_rain.shape),
            "valid_daily_fraction": float(np.isfinite(merged_rain).mean()),
            "latest_observation_start": metadata["latest_observation_start"],
            "latest_observation_end": metadata["latest_observation_end"],
            "recent_snapshot_id": metadata["snapshot_id"], "recent_accessed_at": metadata["accessed_at"],
            "recent_merged_days": days, "gaps_not_filled": True,
            "daily_label": "UTC date of interval END at 12:00:00Z",
            "daily_interval": "[previous date 12:00:00Z, label date 12:00:00Z)",
            "output_units": "mm", "missing_policy": "NaN if any half-hour is missing; absent dates remain absent"})
        save_json(provenance_path, provenance)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return output


def _save_npz(path: Path, **arrays) -> None:
    import numpy as np
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.stem}.", suffix=".npz", delete=False) as stream:
            temporary = Path(stream.name)
            np.savez_compressed(stream, **arrays)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def acquire_rainfall(root: Path, start: str = "2022-01-01", end: str | None = None,
                     *, output_stem: str = "rainfall_daily", snapshot_id: str | None = None) -> Path:
    import icechunk
    import numpy as np
    import xarray as xr

    if not output_stem.replace("_", "").isalnum():
        raise ValueError("Output stem must contain only letters, numbers, and underscores")

    accessed_at = utc_now()
    catalog = get_json(STAC_URL)
    save_json(root / "data/raw/imerg_stac.json", catalog)
    url = catalog["assets"]["icechunk"]["href"]
    address = urlparse(url)
    # Native anonymous S3 range reads avoid severe HTTPS shard-read overhead.
    storage = icechunk.s3_storage(bucket=address.netloc, prefix=address.path.strip("/"),
        region="us-west-2", anonymous=True)
    repository = icechunk.Repository.open(storage)
    session = repository.readonly_session(snapshot_id=snapshot_id) if snapshot_id else repository.readonly_session("main")
    snapshot = str(session.snapshot_id)
    dataset = xr.open_zarr(session.store, chunks=None, consolidated=False)
    variable = dataset["precipitation_surface"]
    if variable.attrs.get("units") != "kg m-2 s-1":
        raise ValueError(f"Unexpected rainfall units: {variable.attrs.get('units')!r}")
    # Nepal envelope; boundary clipping is performed by the model grid builder.
    latitudes = np.asarray(dataset.latitude.values)
    longitudes = np.asarray(dataset.longitude.values)
    lat_indices = np.flatnonzero((latitudes >= 26.25) & (latitudes <= 30.55))
    lon_indices = np.flatnonzero((longitudes >= 80.00) & (longitudes <= 88.30))
    variable = variable.isel(latitude=slice(lat_indices[0], lat_indices[-1] + 1), longitude=slice(lon_indices[0], lon_indices[-1] + 1))
    latitudes = latitudes[lat_indices].astype("float64")
    longitudes = longitudes[lon_indices].astype("float64")
    all_times = np.asarray(dataset.time.values).astype("datetime64[ns]")
    if not np.all(np.diff(all_times).astype("timedelta64[m]") == np.timedelta64(30, "m")):
        raise ValueError("Archive timestamps are not a contiguous 30-minute sequence")
    last_observation_end = all_times[-1] + np.timedelta64(30, "m")
    last_complete_end_day = (last_observation_end - np.timedelta64(12, "h")).astype("datetime64[D]")
    end_day = min(np.datetime64(end, "D"), last_complete_end_day) if end else last_complete_end_day
    first_day = np.datetime64(start, "D")
    if first_day > end_day:
        raise ValueError("No complete requested daily intervals are available")
    days = np.arange(first_day, end_day + np.timedelta64(1, "D"), dtype="datetime64[D]")
    interval_start = first_day.astype("datetime64[ns]") - np.timedelta64(12, "h")
    interval_end = end_day.astype("datetime64[ns]") + np.timedelta64(12, "h")
    begin = int(np.searchsorted(all_times, interval_start))
    finish = int(np.searchsorted(all_times, interval_end))
    shape = (len(days), len(latitudes), len(longitudes))
    totals = np.zeros(shape, dtype="float32")
    counts = np.zeros(shape, dtype="uint8")
    cache_dir = root / "data/raw/imerg_nepal_chunks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_identity = {"archive_url": url, "snapshot_id": snapshot, "bbox": [80.0, 26.25, 88.3, 30.55],
        "units": "kg m-2 s-1", "latitudes": latitudes.tolist(), "longitudes": longitudes.tolist()}
    # Historical chunks are reusable because their exact snapshot is recorded separately.
    save_json(root / "data/raw/imerg_session.json", cache_identity)
    chunk_size = 1440
    native_starts = list(range((begin // chunk_size) * chunk_size, finish, chunk_size))
    for number, native_start in enumerate(native_starts, 1):
        chunk_end = min(native_start + chunk_size, len(all_times))
        left, right = max(begin, native_start), min(finish, chunk_end)
        cache_path = cache_dir / f"{snapshot}_{native_start}_{chunk_end}.npz"
        if cache_path.exists():
            with np.load(cache_path, allow_pickle=False) as cached:
                rates = cached["rates"]
        else:
            # Crop remotely before materializing values; at most two adjacent spatial chunks.
            rates = np.asarray(variable.isel(time=slice(native_start, chunk_end)).values, dtype="float32")
            rates[(rates < 0) | ~np.isfinite(rates)] = np.nan
            _save_npz(cache_path, times=all_times[native_start:chunk_end].astype("datetime64[s]"), rates=rates,
                latitudes=latitudes, longitudes=longitudes)
        selected = rates[left - native_start:right - native_start]
        selected_times = all_times[left:right]
        labels = (selected_times - np.timedelta64(12, "h")).astype("datetime64[D]") + np.timedelta64(1, "D")
        day_indices = (labels - first_day).astype(int)
        for day_index in np.unique(day_indices):
            values = selected[day_indices == day_index]
            totals[day_index] += np.nansum(values, axis=0, dtype="float32") * np.float32(1800)
            counts[day_index] += np.isfinite(values).sum(axis=0).astype("uint8")
        print(f"IMERG Nepal chunk {number}/{len(native_starts)}: {selected_times[0]} through {selected_times[-1]}", flush=True)
    totals[counts != 48] = np.nan
    output = root / f"data/processed/{output_stem}.npz"
    _save_npz(output, days=days.astype(str), latitudes=latitudes, longitudes=longitudes,
        rain_mm=totals, sample_count=counts)
    # Preserve a recent half-hour strip for actual current environmental values and subsequent runs.
    recent_begin = max(0, len(all_times) - 17 * 48)
    recent = np.asarray(variable.isel(time=slice(recent_begin, None)).values, dtype="float32")
    recent[(recent < 0) | ~np.isfinite(recent)] = np.nan
    recent_path = root / "data/processed/rainfall_recent_halfhour.npz"
    _save_npz(recent_path, times=all_times[recent_begin:].astype("datetime64[s]").astype(str),
        latitudes=latitudes, longitudes=longitudes, rain_mm=recent * np.float32(1800))
    metadata = {
        "source": "NASA IMERG Early Collection 07 via dynamical.org", "catalog_url": CATALOG_URL,
        "stac_url": STAC_URL, "archive_url": url, "archive_version": catalog["version"],
        "snapshot_id": snapshot, "accessed_at": accessed_at, "source_units": "kg m-2 s-1",
        "output_units": "mm", "half_hour_conversion": "rate multiplied by 1800 seconds",
        "daily_label": "UTC date of interval END at 12:00:00Z",
        "daily_interval": "[previous date 12:00:00Z, label date 12:00:00Z)",
        "minimum_samples_per_daily_value": 48, "missing_policy": "NaN if any half-hour is missing",
        "time_coordinate": "start of half-hour averaging interval", "daily_start": str(first_day),
        "daily_end": str(end_day), "latest_observation_start": str(all_times[-1].astype("datetime64[s]")) + "Z",
        "latest_observation_end": str(last_observation_end.astype("datetime64[s]")) + "Z", "shape": list(shape),
        "valid_daily_fraction": float(np.isfinite(totals).mean()), "sha256": sha256(output),
        "latest_values_path": str(recent_path.relative_to(root)),
        "revision_note": "NASA V07B before 2026-03-04, V07C from 2026-03-04 per CMR. Retrospectively processed Early archive is not an original-as-issued replay.",
        "license": "CC BY 4.0", "attribution": catalog.get("attribution"),
        "rounding_note": "Replica documents less than 0.5% additional value rounding error.",
        "live_caveat": "Actual replica delay may exceed NASA nominal 4h; measure observation age at each issue.",
    }
    provenance_name = "rainfall_provenance" if output_stem == "rainfall_daily" else output_stem + "_provenance"
    save_json(root / f"data/processed/{provenance_name}.json", metadata)
    update_source(root, {"id": "imerg" if output_stem == "rainfall_daily" else "imerg_" + output_stem,
        "name": "NASA IMERG Early via dynamical.org", "url": CATALOG_URL,
        "status": "available", "accessed_at": accessed_at, "latest_observation_at": metadata["latest_observation_end"],
        "spatial_resolution": "0.1 degrees", "temporal_resolution": "30 minutes; noon-to-noon aggregation",
        "version": f"Collection 07 / replica {catalog['version']}", "snapshot_id": snapshot,
        "sha256": metadata["sha256"], "notes": metadata["revision_note"]})
    dataset.close()
    return output
