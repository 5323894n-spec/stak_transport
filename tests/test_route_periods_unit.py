# -*- coding: utf-8 -*-
import pytest

from app.route_periods import validate_periods


def test_validate_periods_orders_and_normalizes_values():
    rows = validate_periods(
        [
            {
                "name": "Вечер",
                "start": "16:00",
                "end": "22:00",
                "interval_min": 15,
                "travel_time_factor": 1.1,
                "transition_mode": "smooth",
                "transition_window_min": 30,
            },
            {
                "name": "Утро",
                "start": "06:00",
                "end": "16:00",
                "interval_min": 10,
                "travel_time_factor": 1.0,
                "transition_mode": "abrupt",
            },
        ],
        require_continuous=True,
        service_start="06:00",
        service_end="22:00",
    )
    assert [(row["start_min"], row["end_min"]) for row in rows] == [
        (360, 960),
        (960, 1320),
    ]
    assert rows[1]["travel_time_factor"] == 1.1
    assert rows[1]["transition_window_min"] == 30


@pytest.mark.parametrize(
    "rows,message",
    [
        (
            [
                {"name": "A", "start": "06:00", "end": "10:00", "interval_min": 10},
                {"name": "B", "start": "09:30", "end": "12:00", "interval_min": 15},
            ],
            "пересекаются",
        ),
        (
            [{"name": "A", "start": "06:00", "end": "10:00", "interval_min": 0}],
            "Интервал",
        ),
        (
            [{"name": "A", "start": "06:00", "end": "10:00", "interval_min": 10,
              "transition_mode": "unknown"}],
            "способ перехода",
        ),
    ],
)
def test_validate_periods_rejects_invalid_sets(rows, message):
    with pytest.raises(ValueError, match=message):
        validate_periods(rows)


def test_continuous_mode_rejects_gap():
    with pytest.raises(ValueError, match="разрыв"):
        validate_periods(
            [
                {"name": "A", "start": "06:00", "end": "10:00", "interval_min": 10},
                {"name": "B", "start": "10:30", "end": "12:00", "interval_min": 15},
            ],
            require_continuous=True,
            service_start="06:00",
            service_end="12:00",
        )


def test_validate_periods_rejects_invalid_clock_value():
    with pytest.raises(ValueError, match="Некорректное время"):
        validate_periods([
            {"name": "A", "start": "06:90", "end": "10:00", "interval_min": 10}
        ])
