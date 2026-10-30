from datetime import date, datetime, timezone

import main
from main import cutoff_for, select_rows


def test_selects_latest_forecast_available_at_cutoff_and_keeps_later_separate():
    actual = [
        {"startTime": "2026-01-03T11:00:00+01:00", "publishTime": "2026-01-03T10:30:00Z", "fuelType": "WIND", "generation": 100},
        {"startTime": "2026-01-03T10:30:00Z", "publishTime": "2026-01-03T11:00:00Z", "fuelType": "WIND", "generation": 110},
    ]
    forecasts = [
        {"startTime": "2026-01-03T10:00:00Z", "publishTime": "2026-01-02T08:30:00Z", "generation": 90},
        {"startTime": "2026-01-03T10:00:00Z", "publishTime": "2026-01-02T09:20:00Z", "generation": 96},
        {"startTime": "2026-01-03T10:00:00Z", "publishTime": "2026-01-02T09:21:00Z", "generation": 110},
    ]
    result = select_rows(actual, forecasts, date(2026, 1, 2), date(2026, 1, 2), cutoff="09:20")
    point = result["points"][0]
    assert point["forecast"] == 96
    assert point["laterForecast"] == 110
    assert point["actual"] == 100
    assert point["metricActual"] == 105
    assert result["metrics"] == {"mae": 9, "rmse": 9, "bias": -9, "observations": 1}


def test_january_decision_dates_include_february_delivery():
    actual = [
        {"startTime": "2026-01-01T10:00:00Z", "publishTime": "2026-01-01T10:30:00Z", "fuelType": "WIND", "generation": 100},
        {"startTime": "2026-02-01T10:00:00Z", "publishTime": "2026-02-01T10:30:00Z", "fuelType": "WIND", "generation": 120},
        {"startTime": "2026-02-01T10:30:00Z", "publishTime": "2026-02-01T11:00:00Z", "fuelType": "WIND", "generation": 140},
    ]
    forecasts = [{"startTime": "2026-02-01T10:00:00Z", "publishTime": "2026-01-31T15:00:00Z", "generation": 130}]
    result = select_rows(actual, forecasts, date(2026, 1, 31), date(2026, 1, 31), cutoff="15:30")
    assert [point["startTime"] for point in result["points"]] == ["2026-02-01T10:00:00Z", "2026-02-01T10:30:00Z"]
    assert result["metrics"]["observations"] == 1


def test_default_cutoff_is_used_for_selection(monkeypatch):
    monkeypatch.setattr(main, "CUTOFF", "15:30")
    actual = [{"startTime": "2026-01-03T10:00:00Z", "publishTime": "2026-01-03T10:30:00Z", "fuelType": "WIND", "generation": 100}]
    forecasts = [
        {"startTime": "2026-01-03T10:00:00Z", "publishTime": "2026-01-02T15:30:00Z", "generation": 90},
        {"startTime": "2026-01-03T10:00:00Z", "publishTime": "2026-01-02T15:31:00Z", "generation": 110},
    ]
    result = select_rows(actual, forecasts, date(2026, 1, 2), date(2026, 1, 2))
    assert result["cutoff"] == "15:30"
    assert result["points"][0]["forecast"] == 90


def test_api_cutoff_override_is_used_for_selection(monkeypatch):
    actual = [{"startTime": "2026-01-03T10:00:00Z", "publishTime": "2026-01-03T10:30:00Z", "fuelType": "WIND", "generation": 100}]
    forecasts = [
        {"startTime": "2026-01-03T10:00:00Z", "publishTime": "2026-01-02T09:20:00Z", "generation": 90},
        {"startTime": "2026-01-03T10:00:00Z", "publishTime": "2026-01-02T09:21:00Z", "generation": 110},
    ]
    monkeypatch.setattr(main, "month_data", lambda: (actual, forecasts, "fetched"))
    result = main.forecast(date(2026, 1, 2), date(2026, 1, 2), "09:20")
    assert result["cutoff"] == "09:20"
    assert result["points"][0]["forecast"] == 90


def test_unmatched_rows_are_excluded_from_metrics_and_counted_in_coverage():
    actual = [{"startTime": "2026-01-03T10:30:00Z", "publishTime": "2026-01-03T11:00:00Z", "fuelType": "WIND", "generation": 100}]
    result = select_rows(actual, [], date(2026, 1, 2), date(2026, 1, 2))
    assert result["metrics"]["observations"] == 0
    assert result["metrics"]["mae"] is None
    assert result["coverage"] == {"expectedIntervals": 24, "actualIntervals": 1, "matchedIntervals": 0, "percent": 0.0}


def test_cutoff_uses_previous_local_delivery_day():
    stamp = datetime(2026, 1, 3, 10, tzinfo=timezone.utc)
    assert cutoff_for(stamp, "09:20") == datetime(2026, 1, 2, 9, 20, tzinfo=timezone.utc)


def test_rejecting_non_wind_actuals():
    result = select_rows([{"startTime": "2026-01-03T10:00:00Z", "publishTime": "2026-01-03T10:30:00Z", "fuelType": "GAS", "generation": 100}], [], date(2026, 1, 2), date(2026, 1, 2))
    assert result["coverage"]["actualIntervals"] == 0

