"""Deadline and concurrent publication regressions with isolated fixture models."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.app import modeling
from backend.app.features import issue_time
from backend.app.sources.common import save_json
from backend.tests.test_features import feature_data, publisher_bundle


@pytest.fixture
def publication_clock(monkeypatch):
    clock = [datetime(2024, 1, 14, 18, 5, tzinfo=timezone.utc)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(modeling, "datetime", Clock)
    return clock


def previous_pointer(directory):
    path = directory / "published/current.json"
    save_json(path, {"run_id": "previous-fixture", "valid_from": "2024-01-13T18:15:00+00:00"})
    return path, path.read_bytes()


def assert_no_new_publication(directory, current, before):
    assert current.read_bytes() == before
    runs = directory / "published/runs"
    assert not runs.exists() or list(runs.iterdir()) == []
    assert list((directory / "published").glob(".current-*")) == []


def test_slow_inference_crossing_midnight_never_publishes(feature_data, publisher_bundle, publication_clock, monkeypatch):
    current, before = previous_pointer(feature_data)
    original = publisher_bundle["model"].predict_proba

    def slow_inference(values):
        prediction = original(values)
        publication_clock[0] = issue_time(date(2024, 1, 15)) + timedelta(seconds=1)
        return prediction

    monkeypatch.setattr(publisher_bundle["model"], "predict_proba", slow_inference)
    with pytest.raises(ValueError, match="inference finished after the issuance deadline"):
        modeling.predict(feature_data, date(2024, 1, 15), operational=True)
    assert_no_new_publication(feature_data, current, before)


@pytest.mark.parametrize("slow_file", ["forecast.geojson", "manifest.json", "current_pointer"])
def test_deadline_is_rechecked_after_every_staging_phase(feature_data, publisher_bundle, publication_clock, monkeypatch, slow_file):
    current, before = previous_pointer(feature_data)
    original = modeling.write_json

    def delayed_serialization(path, value):
        original(path, value)
        selected = path.name == slow_file or (slow_file == "current_pointer" and path.name.startswith(".current-"))
        if selected:
            publication_clock[0] = issue_time(date(2024, 1, 15))

    monkeypatch.setattr(modeling, "write_json", delayed_serialization)
    with pytest.raises(ValueError, match="issuance deadline"):
        modeling.predict(feature_data, date(2024, 1, 15), operational=True)
    assert_no_new_publication(feature_data, current, before)


def test_generated_and_issued_times_follow_completed_work(feature_data, publisher_bundle, publication_clock, monkeypatch):
    begun_at = publication_clock[0]
    original_prediction = publisher_bundle["model"].predict_proba
    original_writer = modeling.write_json

    def inference(values):
        prediction = original_prediction(values)
        publication_clock[0] += timedelta(minutes=2)
        return prediction

    def serialize(path, value):
        original_writer(path, value)
        if path.name == "forecast.geojson":
            publication_clock[0] += timedelta(seconds=10)

    monkeypatch.setattr(publisher_bundle["model"], "predict_proba", inference)
    monkeypatch.setattr(modeling, "write_json", serialize)
    manifest = modeling.predict(feature_data, date(2024, 1, 15), operational=True)
    generated_at = begun_at + timedelta(minutes=2)
    issued_at = generated_at + timedelta(seconds=10)
    assert manifest["generated_at"] == generated_at.isoformat()
    assert manifest["issued_at"] == issued_at.isoformat()
    assert generated_at <= issued_at < issue_time(date(2024, 1, 15))
    run = feature_data / "published/runs" / manifest["run_id"]
    properties = json.loads((run / "forecast.geojson").read_text())["features"][0]["properties"]
    assert properties["input_age_reference_at"] == generated_at.isoformat()
    assert properties["input_age_hours"] == 6.12
    assert json.loads((run / "manifest.json").read_text()) == manifest
    assert json.loads((feature_data / "published/current.json").read_text()) == manifest


def test_simultaneous_same_run_calls_infer_once_and_preserve_immutable_run(feature_data, publisher_bundle, publication_clock, monkeypatch):
    original = publisher_bundle["model"].predict_proba
    inference_calls = []
    barrier = threading.Barrier(2)

    def slow_inference(values):
        inference_calls.append(True)
        time.sleep(.08)  # Allow the second caller to contend for publish.lock.
        return original(values)

    def publish():
        barrier.wait(timeout=5)
        return modeling.predict(feature_data, date(2024, 1, 15), operational=True)

    monkeypatch.setattr(publisher_bundle["model"], "predict_proba", slow_inference)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(publish), pool.submit(publish)
        a, b = first.result(timeout=10), second.result(timeout=10)
    assert a == b
    assert len(inference_calls) == 1
    runs = feature_data / "published/runs"
    assert [path.name for path in runs.iterdir()] == [a["run_id"]]
    assert json.loads((feature_data / "published/current.json").read_text()) == a


def test_completed_run_is_reused_before_reading_new_inputs_or_checking_old_deadline(feature_data, publisher_bundle, publication_clock, monkeypatch):
    first = modeling.predict(feature_data, date(2024, 1, 15), operational=True)
    path = feature_data / "published/runs" / first["run_id"] / "manifest.json"
    before = path.read_bytes()
    publication_clock[0] += timedelta(days=1)

    def forbidden_read(*args):
        raise AssertionError("An immutable existing run must not reread source features.")

    monkeypatch.setattr(modeling, "FeatureEngine", forbidden_read)
    assert modeling.predict(feature_data, date(2024, 1, 15), operational=True) == first
    assert path.read_bytes() == before
