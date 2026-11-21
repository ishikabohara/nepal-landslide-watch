"""Acquire public event inventories while retaining label uncertainty."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from .common import get_json, save_json, sha256, update_source, utc_now

BIPAD_URL = "https://bipadportal.gov.np/api/v1/incident/?hazard=17&limit=10000&ordering=id"
COOLR_BASE = "https://gis.earthdata.nasa.gov/portal/rest/services/Landslides/COOLR_Reports_Points/FeatureServer/0/query"
RAIN_TRIGGERS = {"rain", "rainfall", "downpour", "continuous_rain", "flooding", "tropical_cyclone", "monsoon"}
EXCLUDED_TRIGGERS = {"earthquake", "construction", "mining", "freeze_thaw", "snowfall_snowmelt", "volcanic_eruption"}


def _coordinates(point: dict | None):
    if not point or point.get("type") != "Point":
        return None, None
    coordinates = point.get("coordinates", [])
    if len(coordinates) < 2:
        return None, None
    lon, lat = float(coordinates[0]), float(coordinates[1])
    if not (79 <= lon <= 90 and 25 <= lat <= 32):
        return None, None
    return lat, lon


def acquire_events(root: Path) -> Path:
    normalized = []
    rows, seen, urls = [], set(), []
    url = BIPAD_URL
    while url:
        if url in urls:
            raise ValueError("BIPAD returned a repeated pagination URL")
        urls.append(url)
        page = get_json(url)
        batch = page.get("results", [])
        if not batch:
            break  # API count is a sentinel, not a trustworthy record count.
        for source_row in batch:
            if source_row["id"] in seen:
                continue
            seen.add(source_row["id"])
            # Retain necessary event fields only, without personal descriptions or contacts.
            row = {k: source_row.get(k) for k in (
                "id", "title", "point", "incidentOn", "reportedOn", "createdOn", "modifiedOn",
                "cause", "verified", "approved", "source", "dataSource", "hazard", "wards")}
            rows.append(row)
            lat, lon = _coordinates(row["point"])
            timestamp = row.get("incidentOn")
            flags = ["date_only", "trigger_unconfirmed", "location_accuracy_unreported"]
            if lat is None:
                flags.append("missing_or_invalid_coordinates")
            if not timestamp:
                flags.append("missing_date")
            if not row.get("verified"):
                flags.append("unverified")
            if not row.get("approved"):
                flags.append("unapproved")
            normalized.append({
                "id": f"bipad-{row['id']}", "source": "BIPAD", "source_id": row["id"],
                "title": row.get("title") or "Reported landslide", "date": timestamp[:10] if timestamp else None,
                "event_date": timestamp[:10] if timestamp else None, "incident_at": timestamp,
                "reported_at": row.get("reportedOn"), "lat": lat, "lon": lon,
                "trigger": row.get("cause") or "unknown", "rainfall_confirmed": False,
                "verified": bool(row.get("verified")), "approved": bool(row.get("approved")),
                "date_precision": "day", "location_accuracy_km": None,
                "quality_flags": flags, "source_url": BIPAD_URL,
            })
        url = page.get("next")
        if len(urls) > 100:
            raise ValueError("BIPAD pagination exceeded bounded acquisition limit")
    raw_path = root / "data/raw/bipad_landslides.json"
    save_json(raw_path, {"accessed_at": utc_now(), "query_urls": urls, "results": rows})
    update_source(root, {"id": "bipad", "name": "BIPAD Nepal landslide incident inventory",
        "url": BIPAD_URL, "status": "available", "accessed_at": utc_now(), "record_count": len(rows),
        "sha256": sha256(raw_path), "notes": "Hazard 17. Trigger unknown; police/admin reports are incomplete and reporting is delayed. API count sentinel ignored; pagination exhausted."})

    query = {"where": "country_name='Nepal'", "outFields": "*", "returnGeometry": "true", "f": "geojson"}
    coolr_url = COOLR_BASE + "?" + urlencode(query)
    coolr = get_json(coolr_url)
    if coolr.get("exceededTransferLimit") or coolr.get("properties", {}).get("exceededTransferLimit"):
        raise ValueError("COOLR transfer limit exceeded; do not silently truncate event inventory")
    sanitized = []
    for feature in coolr.get("features", []):
        properties = feature["properties"]
        keep = {key: properties.get(key) for key in (
            "objectid", "event_id", "event_date", "event_time", "event_title", "location_accuracy",
            "landslide_category", "landslide_trigger", "event_import_source", "event_import_id",
            "latitude", "longitude", "country_name", "source_name", "source_link", "submitted_date")}
        sanitized.append({"type": "Feature", "geometry": feature.get("geometry"), "properties": keep})
        lat, lon = _coordinates(feature.get("geometry"))
        timestamp = keep.get("event_date")
        date = datetime.fromtimestamp(timestamp / 1000, timezone.utc).date().isoformat() if timestamp else None
        trigger = (keep.get("landslide_trigger") or "unknown").lower().replace(" ", "_")
        accuracy_raw = str(keep.get("location_accuracy") or "unknown")
        try:
            accuracy = float(accuracy_raw.lower().replace("km", "").strip())
        except ValueError:
            accuracy = None
        flags = ["date_only", "media_reporting_bias"]
        if trigger not in RAIN_TRIGGERS:
            flags.append("non_rainfall_trigger" if trigger in EXCLUDED_TRIGGERS else "trigger_unconfirmed")
        if accuracy is None:
            flags.append("location_accuracy_unreported")
        elif accuracy > 5:
            flags.append("coarse_location")
        if lat is None:
            flags.append("missing_or_invalid_coordinates")
        normalized.append({"id": f"coolr-{keep['objectid']}", "source": "NASA COOLR", "source_id": keep["event_id"],
            "title": keep.get("event_title") or "Reported landslide", "date": date, "event_date": date,
            "incident_at": None, "lat": lat, "lon": lon, "trigger": trigger,
            "rainfall_confirmed": trigger in RAIN_TRIGGERS, "verified": None, "approved": None,
            "date_precision": "day", "location_accuracy_km": accuracy, "quality_flags": flags,
            "source_url": keep.get("source_link") or coolr_url})
    raw_path = root / "data/raw/coolr_nepal.geojson"
    save_json(raw_path, {"type": "FeatureCollection", "features": sanitized})
    update_source(root, {"id": "coolr", "name": "NASA COOLR Nepal reported landslides",
        "url": coolr_url, "status": "available", "accessed_at": utc_now(), "record_count": len(sanitized),
        "sha256": sha256(raw_path), "notes": "Reported events, not complete occurrence truth. Cross-source duplicates retained for explicit model-stage deduplication."})
    normalized.sort(key=lambda event: (event.get("date") or "", event["id"]))
    output = root / "data/processed/events.json"
    save_json(output, normalized)
    return output
