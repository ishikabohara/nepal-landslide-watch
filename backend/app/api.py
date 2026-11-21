"""Public, read-only scientific data endpoints."""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

from .schemas import LocationResponse
from .storage import SnapshotError, SnapshotStore, coordinates, parse_time


router = APIRouter(prefix="/api")
RunSelector = Annotated[str | None, Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
Latitude = Annotated[float, Query(ge=-90, le=90)]
Longitude = Annotated[float, Query(ge=-180, le=180)]
COORDINATE_QUERY = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*[,;]\s*([+-]?\d+(?:\.\d+)?)\s*$")

# Raw catalogues can contain reporters' names, phone numbers and free-form notes.
# Expose only the scientific/public location fields required by the map.
PUBLIC_EVENT_FIELDS = frozenset({
    "id", "event_id", "source", "source_url", "date", "occurred_at", "reported_at",
    "event_date", "lat", "lon", "district", "province", "municipality", "ward",
    "trigger", "location_accuracy", "location_accuracy_km", "location_confidence",
    "fatalities", "injuries", "houses_destroyed", "cell_id", "spatial_quality",
    "temporal_quality", "quality_flags", "location_method", "event_type",
    "incident_at", "source_id", "rainfall_confirmed", "verified", "approved", "date_precision",
})


def store(request: Request) -> SnapshotStore:
    return request.app.state.store


def bounded_coordinates(lat: float, lon: float) -> None:
    if not math.isfinite(lat) or not math.isfinite(lon):
        raise HTTPException(status_code=422, detail="Coordinates must be finite.")


def source_list(snapshot: SnapshotStore) -> list[dict[str, Any]]:
    sources = snapshot.read("sources.json", [])
    if isinstance(sources, dict):
        sources = sources.get("sources", [])
    if not isinstance(sources, list):
        raise SnapshotError("Source provenance is malformed.")
    return sources


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    snapshot = store(request)
    manifest, _ = snapshot.forecast()
    model = snapshot.read("model/model_card.json")
    now = datetime.now(timezone.utc)
    valid_to = parse_time(manifest.get("valid_to")) if manifest else None
    state = "awaiting_data"
    if manifest:
        state = "historical" if manifest.get("mode") == "historical" else "ready"
        if manifest.get("mode") == "operational" and valid_to is not None and valid_to <= now:
            state = "stale"
    try:
        worker_health = snapshot.read("worker_health.json")
        if worker_health is not None and not isinstance(worker_health, dict):
            raise SnapshotError("Worker health is malformed.")
    except SnapshotError:
        worker_health = {"status": "unavailable", "message": "Worker health cannot currently be read."}
    if isinstance(worker_health, dict):
        worker_health = dict(worker_health)
        activity_times = [parse_time(worker_health.get(key)) for key in ("checked_at", "last_attempt")]
        checked_at = max((value for value in activity_times if value is not None), default=None)
        worker_health["heartbeat_age_seconds"] = round(max(0, (now - checked_at).total_seconds()), 1) if checked_at else None
        worker_health["heartbeat_stale"] = checked_at is None or (now - checked_at).total_seconds() > 300
    return {
        "service": "Nepal Landslide Watch",
        "status": state,
        "latest_run": manifest,
        "model_available": isinstance(model, dict) and bool(model) and model.get("status") != "unavailable",
        "source_count": len(source_list(snapshot)),
        "worker_health": worker_health,
        "server_time": now.isoformat().replace("+00:00", "Z"),
    }


@router.get("/runs")
def runs(request: Request, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> dict[str, Any]:
    return {"runs": store(request).runs(limit)}


@router.get("/forecast")
def forecast(request: Request, response: Response, run_id: RunSelector = None) -> dict[str, Any]:
    manifest, collection = store(request).forecast(run_id)
    if run_id is not None:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return {**collection, "manifest": manifest}


@router.get("/location", response_model=LocationResponse)
def location(
    request: Request,
    lat: Latitude,
    lon: Longitude,
    run_id: RunSelector = None,
    history_limit: Annotated[int, Query(ge=1, le=90)] = 30,
    include_history: bool = True,
) -> dict[str, Any]:
    bounded_coordinates(lat, lon)
    snapshot = store(request)
    cell = snapshot.location(lat, lon, run_id)
    if cell is None:
        return {"cell": None, "history": [], "rainfall": []}
    properties = cell.get("properties", {})
    cell_id = str(properties.get("cell_id", cell.get("id", "")))
    return {
        "cell": cell,
        "history": snapshot.history(cell_id, history_limit) if include_history else [],
        "rainfall": properties.get("rainfall_series") or [],
    }


@router.get("/history")
def history(
    request: Request,
    cell_id: Annotated[str, Query(min_length=1, max_length=128)],
    limit: Annotated[int, Query(ge=1, le=90)] = 30,
) -> dict[str, Any]:
    return {"cell_id": cell_id, "history": store(request).history(cell_id, limit)}


@router.get("/search")
def search(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict[str, Any]:
    snapshot = store(request)
    query = q.strip()
    if not query:
        return {"results": []}
    coordinate_match = COORDINATE_QUERY.fullmatch(query)
    if coordinate_match:
        lat, lon = map(float, coordinate_match.groups())
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return {"results": [{"name": f"{lat:.5f}, {lon:.5f}", "lat": lat, "lon": lon, "kind": "coordinates"}]}
        return {"results": []}
    matched = []
    for item in snapshot.places():
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        point = coordinates(item)
        aliases = [item["name"], *(item.get("search_names") or [])]
        if point and any(isinstance(alias, str) and query.casefold() in alias.casefold() for alias in aliases):
            matched.append({"name": item["name"], "lat": point[0], "lon": point[1], "kind": item.get("kind", "place")})
    matched.sort(key=lambda item: (not item["name"].casefold().startswith(query.casefold()), item["name"].casefold()))
    return {"results": matched[:limit]}


@router.get("/model")
def model(request: Request) -> dict[str, Any]:
    card = store(request).read("model/model_card.json")
    if card is None:
        return {"status": "unavailable", "message": "No trained model has been published. Run the documented data and training pipeline."}
    if not isinstance(card, dict):
        raise SnapshotError("The model card is malformed.")
    return card


@router.get("/sources")
def sources(request: Request) -> dict[str, Any]:
    return {"sources": source_list(store(request))}


def parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    try:
        numbers = tuple(float(piece) for piece in value.split(","))
        if len(numbers) != 4 or not all(math.isfinite(number) for number in numbers):
            raise ValueError
        west, south, east, north = numbers
        if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
            raise ValueError
        return west, south, east, north
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="bbox must be west,south,east,north within valid longitude/latitude bounds.") from exc


@router.get("/events")
def events(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=1000)] = 250,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    start: date | None = None,
    end: date | None = None,
    source: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    bbox: Annotated[str | None, Query(max_length=120)] = None,
) -> dict[str, Any]:
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=422, detail="start must be on or before end.")
    bounds = parse_bbox(bbox)
    selected = []
    for event in store(request).events():
        if source is not None and str(event.get("source", "")).casefold() != source.casefold():
            continue
        event_time = parse_time(event.get("date") or event.get("event_date") or event.get("occurred_at") or event.get("incident_at"))
        if start is not None or end is not None:
            if event_time is None:
                continue
            event_day = event_time.date()
            if (start and event_day < start) or (end and event_day > end):
                continue
        point = coordinates(event)
        if bounds is not None:
            west, south, east, north = bounds
            if point is None or not (west <= point[1] <= east and south <= point[0] <= north):
                continue
        public = {key: value for key, value in event.items() if key in PUBLIC_EVENT_FIELDS}
        if point:
            public.update(lat=point[0], lon=point[1])
        selected.append((event_time, public))
    minimum_time = datetime.min.replace(tzinfo=timezone.utc)
    selected.sort(key=lambda item: (item[0] or minimum_time, str(item[1].get("id", item[1].get("event_id", "")))), reverse=True)
    return {"events": [item[1] for item in selected[offset:offset + limit]], "total": len(selected), "offset": offset, "limit": limit}
