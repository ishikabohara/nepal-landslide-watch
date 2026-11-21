from __future__ import annotations

from pathlib import Path

from .common import get_json, save_json, sha256, update_source, utc_now

METADATA_URL = "https://www.geoboundaries.org/api/current/gbOpen/NPL/ADM0/"


def acquire_boundary(root: Path) -> Path:
    metadata = get_json(METADATA_URL)
    save_json(root / "data/raw/nepal_boundary_metadata.json", metadata)
    source_url = metadata["simplifiedGeometryGeoJSON"]
    boundary = get_json(source_url)
    if boundary.get("type") != "FeatureCollection" or not boundary.get("features"):
        raise ValueError("Nepal boundary response is not a populated GeoJSON FeatureCollection")
    path = root / "data/processed/nepal_boundary.geojson"
    save_json(path, boundary)
    update_source(root, {
        "id": "geoboundaries_nepal", "name": "geoBoundaries Nepal ADM0",
        "url": METADATA_URL, "download_url": source_url, "status": "available",
        "accessed_at": utc_now(), "version": metadata["boundaryID"],
        "license": metadata["boundaryLicense"], "sha256": sha256(path),
        "notes": "Simplified national boundary; administrative delineation, not a political position.",
    })
    return path
