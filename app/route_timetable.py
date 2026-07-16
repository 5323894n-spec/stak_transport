# -*- coding: utf-8 -*-
"""Чистые расчёты поостановочного расписания маршрута."""

import math


def format_service_time(seconds):
    """Format service-day seconds without wrapping values after midnight."""
    seconds = int(seconds)
    if seconds < 0:
        raise ValueError("Время не может быть отрицательным")
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def calculate_trip_stop_times(
    trace,
    *,
    departure_sec,
    runtime_factor=1.0,
    runtime_overrides=None,
):
    """Calculate arrival/departure seconds for every stop of one trip."""
    if not trace:
        raise ValueError("В направлении нет остановок")
    try:
        cursor = int(departure_sec)
        factor = float(runtime_factor)
    except (TypeError, ValueError):
        raise ValueError("Некорректные параметры времени рейса")
    if cursor < 0 or factor <= 0:
        raise ValueError("Некорректные параметры времени рейса")

    runtime_overrides = runtime_overrides or {}
    result = []
    last_index = len(trace) - 1
    for index, stop in enumerate(trace):
        if index:
            explicit = runtime_overrides.get(stop["id"])
            base_runtime = stop.get("run_time_sec") or 0
            run_time = int(
                explicit
                if explicit is not None
                else math.ceil(int(base_runtime) * factor)
            )
            if run_time <= 0:
                raise ValueError("Для перегона не задано положительное время хода")
            cursor += run_time

        arrival = cursor
        dwell = (
            0
            if index in (0, last_index)
            else max(0, int(stop.get("dwell_time_sec") or 0))
        )
        cursor += dwell
        result.append(
            {
                "route_stop_id": stop["id"],
                "sequence": int(stop["sequence"]),
                "arrival_sec": arrival,
                "departure_sec": cursor,
                "is_timing_point": 1 if stop.get("is_timing_point") else 0,
            }
        )
    return result
