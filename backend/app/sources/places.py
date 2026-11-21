"""Small real Nepal gazetteer from GeoNames' CC BY 4.0 country extract."""

from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from pathlib import Path

import httpx
from shapely.geometry import Point, shape
from shapely.ops import unary_union

from .common import save_json, sha256, update_source, utc_now


GEONAMES_URL = "https://download.geonames.org/export/dump/NP.zip"


def acquire_places(root: Path) -> Path:
    archive = root / "data/raw/geonames_NP.zip"
    if not archive.exists():
        with httpx.stream("GET", GEONAMES_URL, timeout=90, follow_redirects=True) as response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > 30_000_000:
                    raise ValueError("The GeoNames Nepal archive is unexpectedly large.")
        archive.parent.mkdir(parents=True, exist_ok=True)
        temporary = archive.with_suffix(".zip.tmp")
        temporary.write_bytes(content)
        temporary.replace(archive)
    country = json.loads((root / "data/processed/nepal_boundary.geojson").read_text())
    boundary = unary_union([shape(feature["geometry"]) for feature in country["features"]])
    places = []
    with zipfile.ZipFile(archive) as zipped:
        metadata = zipped.getinfo("NP.txt")
        if metadata.file_size > 150_000_000:
            raise ValueError("The GeoNames Nepal member is unexpectedly large.")
        # Read the named member directly; never extract arbitrary archive paths.
        with zipped.open("NP.txt") as stream:
            for row in csv.reader(io.TextIOWrapper(stream, encoding="utf-8"), delimiter="\t"):
                if len(row) != 19 or row[6] != "P" or row[8] != "NP":
                    continue
                lat, lon = float(row[4]), float(row[5])
                if not math.isfinite(lat) or not math.isfinite(lon) or not boundary.covers(Point(lon, lat)):
                    continue
                places.append({
                    "name": row[1], "lat": lat, "lon": lon,
                    "kind": "capital" if row[7] == "PPLC" else "town" if row[7].startswith("PPLA") else "settlement",
                    "source": "GeoNames", "geoname_id": int(row[0]), "source_url": f"https://www.geonames.org/{row[0]}/",
                    "search_names": sorted({value for value in [row[2], *row[3].split(",")] if value and value != row[1]}),
                    "population": int(row[14] or 0), "modified_at": row[18],
                })
    if not places:
        raise ValueError("No populated Nepal places survived GeoNames parsing and country clipping.")
    places.sort(key=lambda place: (-place["population"], place["name"]))
    output = root / "data/processed/places.json"
    save_json(output, places)
    update_source(root, {
        "id": "geonames", "name": "GeoNames Nepal populated places", "url": GEONAMES_URL,
        "status": "available", "accessed_at": utc_now(), "record_count": len(places),
        "license": "Creative Commons Attribution 4.0", "attribution": "GeoNames (geonames.org), CC BY 4.0",
        "sha256": sha256(archive), "notes": "Populated-place records clipped to the pinned Nepal boundary; used only for map search, not as model features.",
    })
    return output
