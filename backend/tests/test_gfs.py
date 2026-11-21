"""Accumulation/time accounting tests; all numerical fixtures stay in tests."""

from datetime import date, datetime, timezone

import numpy as np
import pytest

from backend.app.sources.gfs import integrate_window, normalize_accumulations, select_precipitation, target_window


def test_nepal_calendar_day_uses_preissuance_cycle_and_fractional_boundaries():
    cycle, start, end = target_window(date(2026, 9, 8))
    assert cycle == datetime(2026, 9, 7, 6, tzinfo=timezone.utc)
    assert start == datetime(2026, 9, 7, 18, 15, tzinfo=timezone.utc)
    assert (start - cycle).total_seconds() / 3600 == 12.25
    assert (end - cycle).total_seconds() / 3600 == 36.25


def test_select_short_apcp_field_not_whole_cycle_total():
    index = "\n".join([
        "595:100:d=2026090706:PRATE:surface:15 hour fcst:",
        "596:200:d=2026090706:APCP:surface:12-15 hour acc fcst:",
        "597:300:d=2026090706:APCP:surface:0-15 hour acc fcst:",
        "598:800:d=2026090706:ACPCP:surface:12-15 hour acc fcst:",
    ])
    selected = select_precipitation(index, 15)
    assert (selected.start_byte, selected.end_byte) == (200, 299)
    assert (selected.start_hour, selected.end_hour) == (12, 15)
    with pytest.raises(ValueError):
        select_precipitation(index, 18)


def test_nested_accumulations_are_subtracted_not_double_counted():
    intervals = normalize_accumulations([(12, 15, np.array([4.0, 2.0])), (12, 18, np.array([10.0, 3.0])), (18, 21, np.array([3.0, 1.0]))])
    assert intervals[1][0:2] == (15, 18)
    np.testing.assert_allclose(intervals[1][2], [6, 1])
    np.testing.assert_allclose(integrate_window(intervals, 12, 21), [13, 4])


def test_fractional_interval_integration_preserves_exact_day_length():
    intervals = [(start, start + 3, np.array([3.0])) for start in range(12, 39, 3)]
    np.testing.assert_allclose(integrate_window(intervals, 12.25, 36.25), [24])
    uneven = [(12, 15, np.array([12.])), (15, 18, np.array([6.]))]
    np.testing.assert_allclose(integrate_window(uneven, 12.25, 17), [15])


@pytest.mark.parametrize("fields", [
    [(12, 18, np.array([10.]))],
    [(12, 15, np.array([10.])), (12, 18, np.array([5.]))],
    [(12, 15, np.array([np.nan]))],
    [(0, 15, np.array([10.]))],
])
def test_missing_or_invalid_accumulations_fail_closed(fields):
    with pytest.raises(ValueError):
        normalize_accumulations(fields)


def test_incomplete_or_overlapping_time_coverage_fails():
    for intervals in (
        [(12, 15, np.array([1.])), (18, 21, np.array([1.]))],
        [(12, 18, np.array([1.])), (15, 21, np.array([1.]))],
    ):
        with pytest.raises(ValueError):
            integrate_window(intervals, 12.25, 20)
