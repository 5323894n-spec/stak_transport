# -*- coding: utf-8 -*-
import pytest

from app.route_timetable import calculate_trip_stop_times, format_service_time


def test_stop_times_apply_factor_to_runs_but_not_dwell():
    trace = [
        {
            "id": 1,
            "sequence": 1,
            "run_time_sec": 0,
            "dwell_time_sec": 30,
            "is_timing_point": 1,
        },
        {
            "id": 2,
            "sequence": 2,
            "run_time_sec": 300,
            "dwell_time_sec": 45,
            "is_timing_point": 0,
        },
        {
            "id": 3,
            "sequence": 3,
            "run_time_sec": 420,
            "dwell_time_sec": 0,
            "is_timing_point": 1,
        },
    ]

    rows = calculate_trip_stop_times(
        trace,
        departure_sec=6 * 3600,
        runtime_factor=1.2,
        runtime_overrides={},
    )

    assert rows[0]["arrival_sec"] == rows[0]["departure_sec"] == 21600
    assert rows[1]["arrival_sec"] == 21960
    assert rows[1]["departure_sec"] == 22005
    assert rows[2]["arrival_sec"] == 22509
    assert rows[2]["departure_sec"] == 22509
    assert [row["is_timing_point"] for row in rows] == [1, 0, 1]


def test_explicit_runtime_override_wins_and_midnight_is_extended_day():
    trace = [
        {"id": 10, "sequence": 1, "run_time_sec": 0, "dwell_time_sec": 0},
        {"id": 11, "sequence": 2, "run_time_sec": 900, "dwell_time_sec": 0},
    ]

    rows = calculate_trip_stop_times(
        trace,
        departure_sec=23 * 3600 + 55 * 60,
        runtime_factor=2,
        runtime_overrides={11: 600},
    )

    assert rows[-1]["arrival_sec"] == 24 * 3600 + 5 * 60
    assert format_service_time(rows[-1]["arrival_sec"]) == "24:05"


def test_calculation_rejects_empty_trace_and_missing_segment_runtime():
    with pytest.raises(ValueError, match="останов"):
        calculate_trip_stop_times([], departure_sec=0)

    with pytest.raises(ValueError, match="время хода"):
        calculate_trip_stop_times(
            [
                {"id": 1, "sequence": 1, "run_time_sec": 0},
                {"id": 2, "sequence": 2, "run_time_sec": 0},
            ],
            departure_sec=0,
        )
