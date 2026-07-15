# -*- coding: utf-8 -*-
from app.route_periods import calculate_period_preview


def test_abrupt_periods_generate_expected_departures_and_demand():
    result = calculate_period_preview(
        [
            {
                "name": "Пик",
                "start": "06:00",
                "end": "07:00",
                "interval_min": 10,
                "travel_time_factor": 1,
                "transition_mode": "abrupt",
            },
            {
                "name": "День",
                "start": "07:00",
                "end": "08:00",
                "interval_min": 20,
                "travel_time_factor": 1,
                "transition_mode": "abrupt",
            },
        ],
        forward_min=40,
        backward_min=40,
        terminal_layover_min=5,
    )
    assert result["departures"][:3] == [360, 370, 380]
    assert 420 in result["departures"]
    assert result["periods"][0]["cycle_min"] == 90
    assert result["periods"][0]["buses_required"] == 9
    assert result["periods"][1]["buses_required"] == 5
    assert result["max_buses_required"] == 9


def test_smooth_transition_has_no_gap_outside_neighbor_intervals():
    result = calculate_period_preview(
        [
            {
                "name": "Пик",
                "start": "06:00",
                "end": "07:00",
                "interval_min": 10,
                "transition_mode": "abrupt",
            },
            {
                "name": "День",
                "start": "07:00",
                "end": "09:00",
                "interval_min": 20,
                "transition_mode": "smooth",
                "transition_window_min": 60,
            },
        ],
        forward_min=30,
        backward_min=30,
    )
    gaps = [
        later - earlier
        for earlier, later in zip(result["departures"], result["departures"][1:])
    ]
    assert all(10 <= gap <= 20 for gap in gaps)
    assert gaps[-1] == 20


def test_demand_jump_creates_warning():
    result = calculate_period_preview(
        [
            {"name": "Пик", "start": "06:00", "end": "07:00", "interval_min": 5},
            {"name": "День", "start": "07:00", "end": "09:00", "interval_min": 30},
        ],
        forward_min=45,
        backward_min=45,
        terminal_layover_min=5,
    )
    assert result["warnings"] == [
        {"code": "demand_jump", "from": "Пик", "to": "День", "delta": -16}
    ]
