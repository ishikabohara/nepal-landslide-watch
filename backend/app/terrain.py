"""Build a rainfall-aligned Nepal grid and terrain summaries from real Copernicus DEM.

GLO-90 is deliberately used instead of requiring an Earthdata login for NASADEM.
Slopes are computed in metres from native approximately 90 m DSM pixels. They
describe terrain, not the effective resolution of a forecast.
"""
from __future__ import annotations

import hashlib
import json
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import rasterio
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union
from .sources.common import update_source


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w",dir=path.parent,prefix=f".{path.name}-",suffix=".tmp",delete=False) as stream:
        json.dump(value,stream,allow_nan=False,separators=(",", ":"))
        tmp=Path(stream.name)
    tmp.replace(path)


def load_boundary(path: Path):
    data = json.loads(path.read_text())
    if data.get("type") == "FeatureCollection":
        return unary_union([shape(f["geometry"]) for f in data["features"]])
    return shape(data.get("geometry", data))


def create_grid(boundary_path: Path, output_path: Path) -> dict:
    boundary = load_boundary(boundary_path)
    west, south, east, north = boundary.bounds
    features = []
    for ilat in range(math.floor(south * 10), math.ceil(north * 10)):
        for ilon in range(math.floor(west * 10), math.ceil(east * 10)):
            cell = box(ilon / 10, ilat / 10, (ilon + 1) / 10, (ilat + 1) / 10)
            if not cell.intersects(boundary):
                continue
            clipped = cell.intersection(boundary)
            if clipped.is_empty or clipped.area < 1e-7:
                continue
            cell_id = f"n{ilat:03d}e{ilon:04d}"
            features.append({"type": "Feature", "id": cell_id,
                "geometry": mapping(clipped), "properties": {
                    "cell_id": cell_id, "lat": round((ilat + .5) / 10, 3),
                    "lon": round((ilon + .5) / 10, 3),
                    "grid_lat_index": ilat, "grid_lon_index": ilon,
                    "elevation_m": None, "slope_deg": None, "slope_p90_deg": None,
                    "relief_m": None, "steep_fraction": None}})
    result = {"type": "FeatureCollection", "features": features}
    write_json(output_path, result)
    return result


def build_terrain(data_dir: Path, boundary_path: Path, *, refresh: bool = False) -> dict:
    output = data_dir / "processed/grid.geojson"
    if output.exists() and not refresh:
        grid = json.loads(output.read_text())
    else:
        grid = create_grid(boundary_path, output)
    groups: dict[tuple[int, int], list[dict]] = {}
    for f in grid["features"]:
        p = f["properties"]
        key = (math.floor(p["lat"]), math.floor(p["lon"]))
        groups.setdefault(key, []).append(f)
    cache = data_dir / "raw/terrain"
    cache.mkdir(parents=True, exist_ok=True)
    provenance = []
    with httpx.Client(timeout=180, follow_redirects=True) as client:
        for (lat, lon), cells in groups.items():
            stem = f"Copernicus_DSM_COG_30_N{lat:02d}_00_E{lon:03d}_00_DEM"
            url = f"https://copernicus-dem-90m.s3.amazonaws.com/{stem}/{stem}.tif"
            path = cache / f"{stem}.tif"
            if not path.exists():
                response = client.get(url)
                response.raise_for_status()
                tmp = path.with_suffix(".part")
                tmp.write_bytes(response.content)
                tmp.replace(path)
            with rasterio.open(path) as src:
                dem = src.read(1).astype(np.float64)
                nodata = src.nodata
                if nodata is not None:
                    dem[dem == nodata] = np.nan
                dx = abs(src.transform.a) * 111320 * math.cos(math.radians(lat + .5))
                dy = abs(src.transform.e) * 111320
                gy, gx = np.gradient(dem, dy, dx)
                slope = np.degrees(np.arctan(np.hypot(gx, gy)))
                for feature in cells:
                    p = feature["properties"]
                    ilat, ilon = p["grid_lat_index"], p["grid_lon_index"]
                    window = rasterio.windows.from_bounds(ilon / 10, ilat / 10,
                        (ilon + 1) / 10, (ilat + 1) / 10, src.transform)
                    row, col = int(round(window.row_off)), int(round(window.col_off))
                    height, width = int(round(window.height)), int(round(window.width))
                    row, col = max(0, row), max(0, col)
                    elev = dem[row:row+height, col:col+width]
                    incl = slope[row:row+height, col:col+width]
                    valid = np.isfinite(elev) & np.isfinite(incl)
                    if valid.any():
                        p.update(elevation_m=round(float(np.median(elev[valid])), 1),
                            slope_deg=round(float(np.median(incl[valid])), 2),
                            slope_p90_deg=round(float(np.percentile(incl[valid], 90)), 2),
                            relief_m=round(float(np.percentile(elev[valid], 95)-np.percentile(elev[valid], 5)), 1),
                            steep_fraction=round(float(np.mean(incl[valid] >= 25)), 4),
                            terrain_source="copernicus_glo90", terrain_resolution_m=90)
            provenance.append({"url": url, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
            write_json(output, grid)
            print(f"Terrain {lat}N {lon}E: {len(cells)} cells", flush=True)
    terrain_provenance = {
        "source": "Copernicus DEM GLO-90, public 2021 release",
        "url": "https://registry.opendata.aws/copernicus-dem/",
        "retrieved_at": datetime.now(timezone.utc).isoformat(), "resolution_m": 90,
        "limitations": ["Digital surface model includes vegetation and infrastructure.",
            "Cell terrain statistics include the complete source cell, including a border buffer outside Nepal.",
            "Grid spacing remains 0.1 degree; terrain resolution does not establish forecast resolution."],
        "files": provenance}
    write_json(data_dir / "processed/terrain_provenance.json", terrain_provenance)
    update_source(data_dir.parent, {
        "id": "copernicus_glo90", "name": terrain_provenance["source"],
        "url": terrain_provenance["url"], "status": "available",
        "accessed_at": terrain_provenance["retrieved_at"],
        "spatial_resolution": "Approximately 90 m DSM; summaries on the 0.1 degree prediction grid",
        "temporal_resolution": "Static terrain, public 2021 release",
        "license": "Copernicus DEM public license; see the original source",
        "notes": terrain_provenance["limitations"],
    })
    return grid
