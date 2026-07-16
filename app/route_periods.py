# -*- coding: utf-8 -*-
"""Проверка периодов дня и расчёт интервальных предпросмотров."""

import math

VALID_TRANSITIONS = {"abrupt", "smooth"}


def parse_time(value):
    """Convert HH:MM or an extended-day minute number to integer minutes."""
    if isinstance(value, bool):
        raise ValueError("Некорректное время периода")
    if isinstance(value, int):
        if 0 <= value < 2880:
            return value
        raise ValueError("Некорректное время периода")
    try:
        parts = str(value).strip().split(":")
        if len(parts) != 2:
            raise ValueError
        hours, minutes = map(int, parts)
    except (TypeError, ValueError):
        raise ValueError("Некорректное время периода")
    total = hours * 60 + minutes
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= total < 2880:
        raise ValueError("Некорректное время периода")
    return total


def validate_periods(
    items,
    *,
    require_continuous=False,
    service_start=None,
    service_end=None,
):
    """Normalize and validate a complete route/day period set."""
    if not isinstance(items, list):
        raise ValueError("Периоды должны быть переданы списком")
    normalized = []
    for source in items:
        if not isinstance(source, dict):
            raise ValueError("Период должен быть объектом")
        row = dict(source)
        row["start_min"] = parse_time(row.get("start_min", row.get("start")))
        row["end_min"] = parse_time(row.get("end_min", row.get("end")))
        try:
            row["interval_min"] = int(row["interval_min"])
            row["travel_time_factor"] = float(row.get("travel_time_factor", 1))
            row["transition_window_min"] = int(row.get("transition_window_min", 0))
            row["priority"] = int(row.get("priority", 0))
        except (KeyError, TypeError, ValueError):
            raise ValueError("Некорректные числовые параметры периода")
        row["transition_mode"] = str(row.get("transition_mode", "abrupt"))
        if row["end_min"] <= row["start_min"]:
            raise ValueError("Конец периода должен быть позже начала")
        if row["interval_min"] < 1:
            raise ValueError("Интервал должен быть не меньше 1 минуты")
        if not 0.25 <= row["travel_time_factor"] <= 4:
            raise ValueError("Коэффициент времени должен быть от 0.25 до 4")
        if row["transition_mode"] not in VALID_TRANSITIONS:
            raise ValueError("Неизвестный способ перехода")
        if row["transition_window_min"] < 0:
            raise ValueError("Окно перехода не может быть отрицательным")
        normalized.append(row)

    normalized.sort(key=lambda row: (row["start_min"], row["priority"]))
    for previous, current in zip(normalized, normalized[1:]):
        if current["start_min"] < previous["end_min"]:
            raise ValueError("Периоды пересекаются")
        if require_continuous and current["start_min"] != previous["end_min"]:
            raise ValueError("Между периодами есть запрещённый разрыв")

    if require_continuous and normalized:
        if (
            service_start is not None
            and normalized[0]["start_min"] != parse_time(service_start)
        ):
            raise ValueError("Периоды не покрывают начало работы")
        if (
            service_end is not None
            and normalized[-1]["end_min"] != parse_time(service_end)
        ):
            raise ValueError("Периоды не покрывают окончание работы")
    return normalized


def _gap_for_period(period, previous_interval, elapsed):
    target = period["interval_min"]
    window = period.get("transition_window_min", 0)
    if (
        period.get("transition_mode") != "smooth"
        or not window
        or previous_interval is None
    ):
        return target
    progress = min(1.0, max(0.0, elapsed / window))
    return max(
        1,
        round(previous_interval + (target - previous_interval) * progress),
    )


def calculate_period_preview(
    items,
    *,
    forward_min,
    backward_min,
    terminal_layover_min=6,
):
    """Generate a non-destructive departure and vehicle-demand preview."""
    forward_min = int(forward_min)
    backward_min = int(backward_min)
    terminal_layover_min = int(terminal_layover_min)
    if forward_min <= 0 or backward_min <= 0:
        raise ValueError("Время рейса должно быть положительным")
    if terminal_layover_min < 0:
        raise ValueError("Конечный отстой не может быть отрицательным")

    periods = validate_periods(items)
    departures = []
    summaries = []
    previous_interval = None
    for period in periods:
        factor = period["travel_time_factor"]
        cycle = math.ceil(
            (forward_min + backward_min) * factor + terminal_layover_min * 2
        )
        demand = math.ceil(cycle / period["interval_min"])
        summaries.append({**period, "cycle_min": cycle, "buses_required": demand})
        cursor = (
            period["start_min"]
            if not departures
            else max(period["start_min"], departures[-1] + 1)
        )
        while cursor < period["end_min"]:
            if not departures or cursor > departures[-1]:
                departures.append(cursor)
            elapsed = cursor - period["start_min"]
            cursor += _gap_for_period(period, previous_interval, elapsed)
        previous_interval = period["interval_min"]

    warnings = []
    for previous, current in zip(summaries, summaries[1:]):
        delta = current["buses_required"] - previous["buses_required"]
        if abs(delta) >= 2:
            warnings.append({
                "code": "demand_jump", "from": previous.get("name", ""),
                "to": current.get("name", ""), "delta": delta,
            })
    return {
        "departures": departures,
        "periods": summaries,
        "max_buses_required": max(
            (period["buses_required"] for period in summaries), default=0
        ),
        "warnings": warnings,
    }
