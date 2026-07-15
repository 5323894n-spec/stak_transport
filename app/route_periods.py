# -*- coding: utf-8 -*-
"""Проверка периодов дня и расчёт интервальных предпросмотров."""

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
