# -*- coding: utf-8 -*-
"""Чистые расчёты поостановочного расписания маршрута."""

import heapq
import math

VALID_ADJUSTMENT_STRATEGIES = {
    "selected_only",
    "shift_following",
    "redistribute_remaining",
}



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


def validate_stop_times(rows, minimum_run_sec=1):
    """Validate dwell and chronological order of a stop-time matrix."""
    if not rows:
        raise ValueError("В рейсе нет поостановочного расписания")
    minimum_run_sec = int(minimum_run_sec)
    for index, row in enumerate(rows):
        if int(row["departure_sec"]) < int(row["arrival_sec"]):
            raise ValueError("Отправление не может быть раньше прибытия")
        if index and int(row["arrival_sec"]) < (
            int(rows[index - 1]["departure_sec"]) + minimum_run_sec
        ):
            raise ValueError("Нарушена последовательность времени по остановкам")
    return rows


def adjust_stop_times(rows, *, route_stop_id, departure_sec, strategy):
    """Apply one controlled manual correction to copied stop-time rows."""
    if strategy not in VALID_ADJUSTMENT_STRATEGIES:
        raise ValueError("Неизвестная стратегия корректировки")
    adjusted = [dict(row) for row in rows]
    try:
        selected_index = next(
            index for index, row in enumerate(adjusted)
            if int(row["route_stop_id"]) == int(route_stop_id)
        )
    except StopIteration:
        raise ValueError("Остановка не найдена в рейсе")
    selected = adjusted[selected_index]
    old_departure = int(selected["departure_sec"])
    new_departure = int(departure_sec)
    dwell = old_departure - int(selected["arrival_sec"])
    delta = new_departure - old_departure
    if strategy == "selected_only":
        selected["arrival_sec"] = new_departure - dwell
        selected["departure_sec"] = new_departure
    elif strategy == "shift_following":
        for index in range(selected_index, len(adjusted)):
            adjusted[index]["arrival_sec"] = int(rows[index]["arrival_sec"]) + delta
            adjusted[index]["departure_sec"] = int(rows[index]["departure_sec"]) + delta
    else:
        final_arrival = int(rows[-1]["arrival_sec"])
        old_span = final_arrival - old_departure
        new_span = final_arrival - new_departure
        if selected_index == len(adjusted) - 1:
            selected["arrival_sec"] = new_departure - dwell
            selected["departure_sec"] = new_departure
        else:
            if old_span <= 0 or new_span <= len(adjusted) - selected_index - 1:
                raise ValueError("Оставшегося времени недостаточно для перераспределения")
            selected["arrival_sec"] = new_departure - dwell
            selected["departure_sec"] = new_departure
            for index in range(selected_index + 1, len(adjusted)):
                old_row = rows[index]
                ratio = (int(old_row["arrival_sec"]) - old_departure) / old_span
                arrival = round(new_departure + ratio * new_span)
                row_dwell = int(old_row["departure_sec"]) - int(old_row["arrival_sec"])
                adjusted[index]["arrival_sec"] = arrival
                adjusted[index]["departure_sec"] = arrival + row_dwell
    selected = adjusted[selected_index]
    selected["is_manual_override"] = 1
    selected["override_strategy"] = strategy
    validate_stop_times(adjusted)
    return adjusted


def _period_at(periods, departure_sec):
    departure_min = departure_sec // 60
    for period in periods:
        if period["start_min"] <= departure_min < period["end_min"]:
            return period
    earlier = [period for period in periods if period["end_min"] <= departure_min]
    if earlier:
        return max(earlier, key=lambda item: item["end_min"])
    raise ValueError("Для времени отправления не найден период движения")


def _trace_distance(trace):
    if not trace:
        return 0.0
    cumulative = float(trace[-1].get("cumulative_km") or 0)
    if cumulative:
        return cumulative
    return round(sum(float(row.get("distance_from_prev_km") or 0) for row in trace), 3)


def build_schedule_preview(
    *,
    departures,
    periods,
    forward_trace,
    backward_trace,
    runtime_overrides,
    outputs,
    terminal_layover_sec,
):
    """Build alternating forward/backward trips without database writes."""
    outputs = int(outputs)
    terminal_layover_sec = int(terminal_layover_sec)
    if outputs < 1:
        raise ValueError("Количество выходов должно быть положительным")
    if terminal_layover_sec < 0:
        raise ValueError("Конечный отстой не может быть отрицательным")
    if not forward_trace or not backward_trace:
        raise ValueError("Для генерации нужны остановки обоих направлений")

    available = [(0, number) for number in range(1, outputs + 1)]
    heapq.heapify(available)
    trip_numbers = {number: 0 for number in range(1, outputs + 1)}
    trips = []
    generation_index = 0

    def add_trip(output_number, direction, departure_sec, trace):
        nonlocal generation_index
        period = _period_at(periods, departure_sec)
        stop_times = calculate_trip_stop_times(
            trace,
            departure_sec=departure_sec,
            runtime_factor=period.get("travel_time_factor", 1),
            runtime_overrides=runtime_overrides.get(period.get("id"), {}),
        )
        trip_numbers[output_number] += 1
        generation_index += 1
        trip = {
            "preview_id": generation_index,
            "output_number": output_number,
            "shift_number": 1,
            "trip_number": trip_numbers[output_number],
            "direction": direction,
            "departure_sec": departure_sec,
            "arrival_sec": stop_times[-1]["arrival_sec"],
            "dep_time": format_service_time(departure_sec),
            "arr_time": format_service_time(stop_times[-1]["arrival_sec"]),
            "distance_km": _trace_distance(trace),
            "break_after_min": terminal_layover_sec // 60,
            "break_type": "",
            "period_id": period.get("id"),
            "stop_times": stop_times,
        }
        trips.append(trip)
        return trip

    for departure_min in departures:
        departure_sec = int(departure_min) * 60
        available_sec, output_number = heapq.heappop(available)
        if available_sec > departure_sec:
            raise ValueError(
                "Недостаточно выходов: к отправлению "
                f"{format_service_time(departure_sec)} нет свободного автобуса"
            )
        forward = add_trip(
            output_number, "прямое", departure_sec, forward_trace
        )
        backward_departure = forward["arrival_sec"] + terminal_layover_sec
        backward = add_trip(
            output_number, "обратное", backward_departure, backward_trace
        )
        heapq.heappush(
            available,
            (backward["arrival_sec"] + terminal_layover_sec, output_number),
        )
    return trips
