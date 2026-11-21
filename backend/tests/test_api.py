"""API invariants using deliberately tiny fixture geometry, never production data."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


def write_json(root: Path, relative: str, value) -> None:
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(destination)


def manifest(run_id: str, day: str, mode: str = "historical"):
    return {
        "run_id": run_id, "issued_at": f"{day}T00:00:00Z", "generated_at": f"{day}T00:10:00Z",
        "valid_from": f"{day}T00:00:00Z", "valid_to": f"{day}T23:59:59Z", "mode": mode,
        "model_version": "test-fixture", "target": "recorded landslide", "score_type": "uncalibrated_score",
        "cell_count": 1, "summary": {"moderate": 1}, "source_times": {}, "custom_provenance": {"preserved": True},
    }


def forecast(score: float):
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", "id": "fixture-cell",
            "geometry": {"type": "Polygon", "coordinates": [[[85, 27], [86, 27], [86, 28], [85, 28], [85, 27]]]},
            "properties": {
                "cell_id": "fixture-cell", "score": score, "probability": None, "risk_level": "moderate",
                "rain_24h_mm": None, "rainfall_series": [{"time": "2024-07-01T00:00:00Z", "rain_mm": 12.5}],
                "quality_flags": ["fixture_only"],
            },
        }],
    }


@pytest.fixture
def data_dir(tmp_path):
    root = tmp_path / "data"
    old = manifest("earlier", "2023-07-01")
    current = manifest("latest", "2024-07-01")
    for item, score in ((old, 0.2), (current, 0.4)):
        write_json(root, f"published/runs/{item['run_id']}/manifest.json", item)
        write_json(root, f"published/runs/{item['run_id']}/forecast.geojson", forecast(score))
    write_json(root, "published/current.json", current)
    # A triangle clips the forecast's rectangular footprint at the country edge.
    grid = forecast(0.0)
    grid["features"][0]["geometry"]["coordinates"] = [[[85, 27], [86, 27], [85, 28], [85, 27]]]
    write_json(root, "processed/grid.geojson", grid)
    write_json(root, "model/model_card.json", {"name": "fixture model", "metrics": {"test": {"pr_auc": 0.3}}, "limitations": ["fixture"]})
    write_json(root, "sources.json", [{"id": "fixture", "name": "test source", "availability": {"latency_hours": 4}}])
    write_json(root, "processed/places.json", [{"name": "Fixture place", "lat": 27.2, "lon": 85.2, "kind": "test"}])
    write_json(root, "processed/events.json", [
        {"id": "fixture-old", "date": "2023-07-01", "lat": 27.2, "lon": 85.2, "source": "fixture", "contact_phone": "private", "reporter": {"email": "private"}},
        {"id": "fixture-new", "date": "2024-07-01", "lat": 27.3, "lon": 85.3, "source": "fixture", "rainfall_confirmed": False, "quality_flags": ["uncertain_location"]},
    ])
    return root


@pytest.fixture
def client(data_dir):
    with TestClient(create_app(data_dir=data_dir, frontend_dir=data_dir / "frontend")) as client:
        yield client


def test_missing_data_is_honest_and_not_an_operational_forecast(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path / "empty", frontend_dir=tmp_path / "empty-frontend"))
    status = client.get("/api/status").json()
    assert status["status"] == "awaiting_data"
    assert status["latest_run"] is None
    assert status["model_available"] is False
    assert client.get("/api/forecast").json()["features"] == []
    assert client.get("/api/model").json()["status"] == "unavailable"
    assert client.get("/api/events").json()["events"] == []
    assert client.get("/api/location?lat=27.2&lon=85.2").json() == {"cell": None, "history": [], "rainfall": []}
    assert client.get("/").status_code == 503


def test_status_and_metadata_keep_historical_and_extended_fields(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    status = response.json()
    assert status["status"] == "historical"
    assert status["latest_run"]["custom_provenance"]["preserved"] is True
    assert status["model_available"] is True
    assert status["source_count"] == 1
    assert client.get("/api/model").json()["metrics"]["test"]["pr_auc"] == 0.3
    assert client.get("/api/sources").json()["sources"][0]["availability"]["latency_hours"] == 4


def test_current_pointer_is_reloaded_after_atomic_replacement(client, data_dir):
    assert client.get("/api/forecast").json()["manifest"]["run_id"] == "latest"
    old = json.loads((data_dir / "published/runs/earlier/manifest.json").read_text())
    write_json(data_dir, "published/current.json", old)
    response = client.get("/api/forecast").json()
    assert response["manifest"]["run_id"] == "earlier"
    assert response["features"][0]["properties"]["score"] == 0.2


def test_expired_operational_run_is_stale(client, data_dir):
    write_json(data_dir, "published/current.json", manifest("latest", "2024-07-01", mode="operational"))
    assert client.get("/api/status").json()["status"] == "stale"


def test_location_matches_containing_country_geometry_and_history(client):
    result = client.get("/api/location?lat=27.2&lon=85.2").json()
    assert result["cell"]["properties"]["cell_id"] == "fixture-cell"
    assert result["cell"]["properties"]["probability"] is None
    assert result["cell"]["properties"]["rain_24h_mm"] is None
    assert [item["run_id"] for item in result["history"]] == ["latest", "earlier"]
    assert result["rainfall"][0]["rain_mm"] == 12.5
    # Both distant points and the unclipped part of a border cell stay unavailable.
    for lat, lon in ((0, 0), (27.8, 85.8)):
        assert client.get("/api/location", params={"lat": lat, "lon": lon}).json() == {"cell": None, "history": [], "rainfall": []}


def test_historical_run_selection_and_immutable_cache(client):
    response = client.get("/api/forecast?run_id=earlier")
    assert response.json()["features"][0]["properties"]["score"] == 0.2
    assert "immutable" in response.headers["cache-control"]
    assert "immutable" not in client.get("/api/forecast").headers["cache-control"]
    assert client.get("/api/forecast?run_id=unknown").status_code == 404
    assert client.get("/api/location?lat=27.2&lon=85.2&run_id=earlier").json()["cell"]["properties"]["score"] == 0.2
    assert len(client.get("/api/history?cell_id=fixture-cell&limit=1").json()["history"]) == 1
    assert [item["run_id"] for item in client.get("/api/runs").json()["runs"]] == ["latest", "earlier"]


@pytest.mark.parametrize("run_id", ["../private", "..", "one/../../secret", "a..b", "a\\b", "/tmp", "a%2fb"])
def test_run_selectors_cannot_escape_publication_root(client, run_id):
    assert client.get("/api/forecast", params={"run_id": run_id}).status_code == 422


def test_symlink_cannot_expose_files_outside_data_root(client, data_dir, tmp_path):
    outside = tmp_path / "private.json"
    outside.write_text('{"private":"never return this"}')
    current = data_dir / "published/current.json"
    current.unlink()
    current.symlink_to(outside)
    response = client.get("/api/status")
    assert response.status_code == 503
    assert "never return this" not in response.text


def test_corrupt_selected_data_returns_unavailable_not_empty_or_mocked(client, data_dir):
    (data_dir / "published/runs/latest/forecast.geojson").write_text('{"type":broken')
    for endpoint in ("/api/forecast", "/api/status"):
        response = client.get(endpoint)
        assert response.status_code == 503
        assert response.json()["status"] == "unavailable"
        assert response.headers["retry-after"] == "60"


def test_nonfinite_published_json_is_rejected(client, data_dir):
    (data_dir / "model/model_card.json").write_text('{"score": NaN}')
    assert client.get("/api/model").status_code == 503


@pytest.mark.parametrize("query", ["lat=91&lon=85", "lat=27&lon=181", "lat=nan&lon=85", "lat=27&lon=inf", "lat=27&lon=85&history_limit=1000"])
def test_coordinates_and_query_sizes_are_bounded(client, query):
    assert client.get("/api/location?" + query).status_code == 422


def test_events_are_bounded_filtered_and_strip_personal_fields(client):
    response = client.get("/api/events?limit=1").json()
    assert response["total"] == 2
    assert [event["id"] for event in response["events"]] == ["fixture-new"]
    assert response["events"][0]["rainfall_confirmed"] is False
    older = client.get("/api/events?end=2023-12-31&source=fixture&bbox=85,27,86,28").json()
    assert older["total"] == 1
    assert older["events"][0]["id"] == "fixture-old"
    assert "contact_phone" not in older["events"][0]
    assert "reporter" not in older["events"][0]
    assert client.get("/api/events?bbox=0,0,1,1").json()["events"] == []
    assert client.get("/api/events?start=2025-01-01&end=2024-01-01").status_code == 422
    assert client.get("/api/events?bbox=nan,0,1,1").status_code == 422
    assert client.get("/api/events?limit=1001").status_code == 422


def test_search_uses_real_index_and_parses_coordinates(client):
    assert client.get("/api/search?q=fix").json()["results"] == [{"name": "Fixture place", "lat": 27.2, "lon": 85.2, "kind": "test"}]
    assert client.get("/api/search?q=unknown").json()["results"] == []
    assert client.get("/api/search?q=27.2,85.2").json()["results"][0]["kind"] == "coordinates"
    assert client.get("/api/search?q=127.2,85.2").json()["results"] == []


def test_cors_default_does_not_allow_arbitrary_origins(client):
    allowed = client.options("/api/status", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = client.options("/api/status", headers={"Origin": "https://unrelated.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in denied.headers


def test_large_forecast_is_compressed_without_changing_geojson(client, data_dir):
    collection = forecast(0.4)
    collection["features"] *= 10
    write_json(data_dir, "published/runs/latest/forecast.geojson", collection)
    response = client.get("/api/forecast", headers={"Accept-Encoding": "gzip"})
    assert response.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in response.headers["vary"].lower()
    assert response.json()["features"] == collection["features"]


def test_static_frontend_spa_and_path_security(tmp_path):
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>fixture dashboard</html>")
    (frontend / "assets").mkdir()
    (frontend / "assets/app-abc.js").write_text("/* fixture asset */")
    (frontend / ".env").write_text("SECRET=private")
    private = tmp_path / "private.txt"
    private.write_text("private information")
    (frontend / "escape.txt").symlink_to(private)
    client = TestClient(create_app(data_dir=tmp_path / "data", frontend_dir=frontend))
    assert client.get("/").status_code == 200
    assert "fixture dashboard" in client.get("/methodology").text
    assert "immutable" in client.get("/assets/app-abc.js").headers["cache-control"]
    for path in ("/assets/missing.js", "/.env", "/escape.txt", "/%2E%2E/private.txt", "/api/unknown"):
        response = client.get(path)
        assert response.status_code == 404
        assert "private information" not in response.text
    assert client.get("/healthz").json() == {"status": "ok"}
