"""Leakage, missingness and publication invariants on tiny isolated test inputs."""

import json
from datetime import date, datetime, timezone

import numpy as np
import pytest

from backend.app.features import FEATURES, STATIC_FEATURES, FeatureEngine, build_labels, issue_time, rainfall_cutoff
from backend.app.sources.common import save_json


@pytest.fixture
def feature_data(tmp_path):
    directory = tmp_path / "data"
    cells = []
    for lat, cell_id in ((27.05, "n270e0850"), (27.15, "n271e0850")):
        properties = {"cell_id": cell_id, "lat": lat, "lon": 85.05, **{name: 1.0 for name in STATIC_FEATURES}}
        cells.append({"type": "Feature", "id": cell_id, "properties": properties,
            "geometry": {"type": "Polygon", "coordinates": [[[85, lat - .05], [85.1, lat - .05], [85.1, lat + .05], [85, lat + .05], [85, lat - .05]]]}})
    save_json(directory / "processed/grid.geojson", {"type": "FeatureCollection", "features": cells})
    days = np.arange("2024-01-01", "2024-01-22", dtype="datetime64[D]").astype(str)
    rain = np.broadcast_to(np.arange(1, 22, dtype=np.float32)[:, None, None], (21, 2, 1)).copy()
    np.savez_compressed(directory / "processed/rainfall_daily.npz", days=days, rain_mm=rain,
        sample_count=np.full_like(rain, 48, dtype=np.uint8), latitudes=np.array([27.05, 27.15]), longitudes=np.array([85.05]))
    return directory


def change_rain_archive(directory, mutate):
    path = directory / "processed/rainfall_daily.npz"
    with np.load(path) as archive:
        arrays = {name: archive[name].copy() for name in archive.files}
    mutate(arrays)
    np.savez_compressed(path, **arrays)


def test_feature_windows_end_before_nepal_midnight_with_declared_latency(feature_data):
    day = date(2024, 1, 15)
    engine = FeatureEngine(feature_data)
    batch = engine.for_day(day)
    assert issue_time(day) == datetime(2024, 1, 14, 18, 15, tzinfo=timezone.utc)
    assert rainfall_cutoff(day) == datetime(2024, 1, 14, 12, tzinfo=timezone.utc)
    assert (issue_time(day) - rainfall_cutoff(day)).total_seconds() == 6.25 * 3600
    assert batch.rain_days == [f"2024-01-{number:02d}" for number in range(1, 15)]
    values = dict(zip(FEATURES, batch.values[0]))
    assert values["rain_24h_mm"] == 14
    assert values["rain_72h_mm"] == 39
    assert values["rain_7d_mm"] == 77
    assert values["rain_14d_mm"] == 105
    assert values["rain_change_mm"] == 1


def test_future_weather_cannot_change_a_historical_feature_vector(feature_data):
    day = date(2024, 1, 15)
    before = FeatureEngine(feature_data).for_day(day)
    change_rain_archive(feature_data, lambda arrays: arrays["rain_mm"].__setitem__(slice(14, None), 9999))
    after = FeatureEngine(feature_data).for_day(day)
    np.testing.assert_array_equal(before.values, after.values)
    np.testing.assert_array_equal(before.valid, after.valid)


def test_one_missing_half_hour_invalidates_only_affected_cell(feature_data):
    change_rain_archive(feature_data, lambda arrays: arrays["sample_count"].__setitem__((13, 1, 0), 47))
    batch = FeatureEngine(feature_data).for_day(date(2024, 1, 15))
    assert batch.valid.tolist() == [True, False]
    assert np.isnan(batch.rainfall[-1, 1])
    assert np.isnan(batch.values[1, FEATURES.index("rain_24h_mm")])
    assert np.isfinite(batch.values[0]).all()


def test_missing_day_and_uncovered_geography_are_not_filled(feature_data):
    with pytest.raises(ValueError, match="Incomplete"):
        FeatureEngine(feature_data).for_day(date(2024, 1, 10))
    change_rain_archive(feature_data, lambda arrays: arrays["latitudes"].__setitem__(slice(None), [20, 21]))
    with pytest.raises(ValueError, match="latitude grid"):
        FeatureEngine(feature_data)


def test_labels_deduplicate_recorded_cell_days_and_do_not_mix_catalogues(feature_data):
    engine = FeatureEngine(feature_data)
    base = {"source": "BIPAD", "date": "2024-01-15", "lat": 27.05, "lon": 85.05, "approved": True, "verified": True, "trigger": "unknown", "location_accuracy_km": None}
    events = [dict(base), dict(base), {**base, "source": "NASA COOLR"}, {**base, "trigger": "earthquake"}, {**base, "lat": 0, "lon": 0}, {**base, "approved": False}]
    labels, audit = build_labels(events, engine)
    assert labels == {"2024-01-15": {0}}
    assert audit["unique_positive_cell_days"] == 1
    assert audit["duplicate_cell_days"] == 1
    assert audit["excluded_other_catalogue"] == 1
    assert audit["excluded_known_earthquake"] == 1
    assert audit["excluded_outside_grid"] == 1
    assert audit["excluded_not_approved"] == 1


@pytest.fixture
def publisher_bundle(monkeypatch):
    from backend.app import modeling

    class FixtureEstimator:
        def predict_proba(self, rows):
            return np.broadcast_to([.8, .2], (len(rows), 2))

        def get_booster(self):
            return self

        def predict(self, matrix, pred_contribs=False):
            return np.zeros((matrix.num_row(), len(FEATURES) + 1))

    class FixtureCalibrator:
        def predict_proba(self, logits):
            p = 1 / (1 + np.exp(-logits[:, 0]))
            return np.stack([1 - p, p], axis=1)

    bundle = {"model": FixtureEstimator(), "calibrator": FixtureCalibrator(), "reference": np.linspace(0, 1, 101),
        "features": FEATURES, "card": {"model_version": "fixture-v1", "target": "fixture reported-cell target", "limitations": ["fixture-only"],
        "training_period": {"start": "2020-01-01", "end": "2022-12-31"}, "calibration_period": {"start": "2023-01-16", "end": "2023-12-31"},
        "evaluation_period": {"start": "2024-01-16", "end": "2024-12-31"}}}
    monkeypatch.setattr(modeling.joblib, "load", lambda path: bundle)
    return bundle


def test_publisher_keeps_missing_cells_null_and_discloses_replay(feature_data, publisher_bundle):
    from backend.app import modeling

    change_rain_archive(feature_data, lambda arrays: arrays["sample_count"].__setitem__((13, 1, 0), 47))
    manifest = modeling.predict(feature_data, date(2024, 1, 15))
    published = json.loads((feature_data / "published/runs" / manifest["run_id"] / "forecast.geojson").read_text())
    assert manifest["mode"] == "historical"
    assert manifest["issue_time_is_simulated"] is True
    assert manifest["summary"]["unavailable"] == 1
    valid, missing = [feature["properties"] for feature in published["features"]]
    assert valid["probability"] is None
    assert 0 <= valid["score"] <= 1
    assert missing["score"] is None
    assert missing["probability"] is None
    assert missing["rain_24h_mm"] is None
    assert missing["risk_level"] == "unavailable"
    assert missing["drivers"] == []
    assert "NaN" not in json.dumps(published, allow_nan=False)
    assert valid["input_age_hours"] == 6.25
    assert len(manifest["input_provenance"]["rainfall_window_sha256"]) == 64


@pytest.mark.parametrize("end_time, expected", [("2024-01-15T18:15:00Z", 15.0), ("2024-01-15T18:16:00Z", None)])
def test_gfs_context_requires_the_exact_target_interval(feature_data, publisher_bundle, end_time, expected):
    from backend.app import modeling

    save_json(feature_data / "processed/gfs_forecast.json", {
        "cycle": "2024-01-14T06:00:00Z", "valid_from": "2024-01-14T18:15:00Z", "valid_to": end_time,
        "cells": {"n270e0850": {"forecast_rain_24h_mm": 15.0}}, "role": "context_only_not_used_by_landslide_model",
    })
    manifest = modeling.predict(feature_data, date(2024, 1, 15))
    forecast_path = feature_data / "published/runs" / manifest["run_id"] / "forecast.geojson"
    properties = json.loads(forecast_path.read_text())["features"][0]["properties"]
    assert properties["forecast_rain_24h_mm"] == expected
    assert properties["input_sources"]["forecast_rainfall_role"] == "context_only_not_used_by_model"
    assert all(driver["feature"] != "forecast_rain_24h_mm" for driver in properties["drivers"])


def test_publication_recovers_an_incomplete_directory_and_preserves_newer_current(feature_data, publisher_bundle):
    from backend.app import modeling

    incomplete = feature_data / "published/runs/2024-01-15-replay-fixture-v1"
    incomplete.mkdir(parents=True)
    (incomplete / "forecast.geojson").write_text('{"incomplete":')
    newer = modeling.predict(feature_data, date(2024, 1, 16))
    older = modeling.predict(feature_data, date(2024, 1, 15))
    assert (incomplete / "manifest.json").is_file()
    assert older["run_id"] == incomplete.name
    current = json.loads((feature_data / "published/current.json").read_text())
    assert current["run_id"] == newer["run_id"]
    # Repeated requests preserve the identical immutable run bytes.
    content = (incomplete / "manifest.json").read_bytes()
    repeated = modeling.predict(feature_data, date(2024, 1, 15))
    assert repeated == older
    assert (incomplete / "manifest.json").read_bytes() == content


def test_operational_publication_cannot_backdate_a_historical_run(feature_data, publisher_bundle):
    from backend.app import modeling

    with pytest.raises(ValueError, match="before the target day"):
        modeling.predict(feature_data, date(2024, 1, 15), operational=True)
    assert not (feature_data / "published/current.json").exists()


def test_invalid_event_coordinates_and_dates_are_excluded(feature_data):
    base = {"source": "BIPAD", "date": "2024-01-15", "lat": 27.05, "lon": 85.05, "approved": True, "verified": True}
    labels, audit = build_labels([
        {**base, "date": None}, {**base, "date": "not-a-date"}, {**base, "lat": float("nan")},
        {**base, "lon": float("inf")}, {**base, "lat": "invalid"}, {**base, "lat": None},
    ], FeatureEngine(feature_data))
    assert labels == {}
    assert audit["excluded_outside_grid"] == 4
