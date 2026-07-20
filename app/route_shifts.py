# -*- coding: utf-8 -*-
"""Pure helpers for grouping route output trips into structural shifts."""

from collections import Counter


def _ordered_trips(trips):
    return sorted(
        (dict(row) for row in trips),
        key=lambda row: (int(row["dep_sec"]), int(row["id"])),
    )


def _conflict(code, message, **details):
    return {"code": code, "message": message, **details}


def validate_output_shift_plan(trips, shifts):
    """Return structural conflicts without mutating the supplied rows."""
    ordered = _ordered_trips(trips)
    proposed = [dict(row) for row in shifts]
    if not ordered:
        return [] if not proposed else [
            _conflict("shift_without_trips", "Смены заданы без рейсов")
        ]

    conflicts = []
    trip_by_id = {int(row["id"]): row for row in ordered}
    position = {int(row["id"]): index for index, row in enumerate(ordered)}
    output_numbers = {row.get("output_number") for row in ordered}
    if len(output_numbers) != 1:
        conflicts.append(
            _conflict(
                "mixed_outputs",
                "План смен содержит рейсы разных выпусков",
            )
        )

    for previous, current in zip(ordered, ordered[1:]):
        if int(current["dep_sec"]) < int(previous["arr_sec"]):
            conflicts.append(
                _conflict(
                    "overlapping_trips",
                    "Рейсы выпуска пересекаются по времени",
                    previous_trip_id=int(previous["id"]),
                    trip_id=int(current["id"]),
                )
            )

    covered = []
    ranges = []
    for shift in proposed:
        shift_output = shift.get("output_number")
        if shift_output is not None and shift_output not in output_numbers:
            conflicts.append(
                _conflict(
                    "output_mismatch",
                    "Смена относится к другому выпуску",
                    shift_number=shift.get("shift_number"),
                    output_number=shift_output,
                )
            )
        shift_number = shift.get("shift_number")
        first_id = int(shift.get("trip_from_id", 0) or 0)
        last_id = int(shift.get("trip_to_id", 0) or 0)
        if first_id not in position or last_id not in position:
            conflicts.append(
                _conflict(
                    "unknown_trip",
                    "Граница смены ссылается на неизвестный рейс",
                    shift_number=shift_number,
                )
            )
            continue
        first_index = position[first_id]
        last_index = position[last_id]
        if first_index > last_index:
            conflicts.append(
                _conflict(
                    "invalid_trip_range",
                    "Начальный рейс смены расположен после конечного",
                    shift_number=shift_number,
                )
            )
            continue
        ids = [int(row["id"]) for row in ordered[first_index:last_index + 1]]
        covered.extend(ids)
        ranges.append((first_index, last_index, shift_number))
        expected_start = int(trip_by_id[first_id]["dep_sec"])
        expected_end = int(trip_by_id[last_id]["arr_sec"])
        if (
            int(shift.get("start_sec", -1)) != expected_start
            or int(shift.get("end_sec", -1)) != expected_end
        ):
            conflicts.append(
                _conflict(
                    "time_range_mismatch",
                    "Время смены не совпадает с границами рейсов",
                    shift_number=shift_number,
                )
            )

    counts = Counter(covered)
    for trip_id in trip_by_id:
        if counts[trip_id] == 0:
            conflicts.append(
                _conflict(
                    "uncovered_trip",
                    "Рейс не включён ни в одну смену",
                    trip_id=trip_id,
                )
            )
        elif counts[trip_id] > 1:
            conflicts.append(
                _conflict(
                    "duplicate_trip",
                    "Рейс включён более чем в одну смену",
                    trip_id=trip_id,
                )
            )

    ordered_ranges = sorted(ranges, key=lambda row: (row[0], row[1]))
    for previous, current in zip(ordered_ranges, ordered_ranges[1:]):
        if current[0] <= previous[1]:
            conflicts.append(
                _conflict(
                    "overlapping_shifts",
                    "Диапазоны смен пересекаются",
                    shift_numbers=[previous[2], current[2]],
                )
            )

    return conflicts


def _make_shift(ordered, start_index, end_index, shift_number, shift_type,
                handover_after_min):
    first = ordered[start_index]
    last = ordered[end_index]
    return {
        "shift_number": shift_number,
        "shift_type_id": shift_type.get("id"),
        "output_number": first.get("output_number"),
        "trip_from_id": int(first["id"]),
        "trip_to_id": int(last["id"]),
        "start_sec": int(first["dep_sec"]),
        "end_sec": int(last["arr_sec"]),
        "driver_slots": int(shift_type["driver_slots"]),
        "handover_after_min": int(handover_after_min),
    }


def build_output_shifts(trips, *, shift_type, handover_min):
    """Build a deterministic contiguous shift plan for one output."""
    ordered = _ordered_trips(trips)
    if not ordered:
        return []
    if len({row.get("output_number") for row in ordered}) != 1:
        raise ValueError("Нельзя объединить в смены рейсы разных выпусков")

    for row in ordered:
        if int(row["arr_sec"]) <= int(row["dep_sec"]):
            raise ValueError("Время окончания рейса должно быть позже начала")
    for previous, current in zip(ordered, ordered[1:]):
        if int(current["dep_sec"]) < int(previous["arr_sec"]):
            raise ValueError("Рейсы выпуска пересекаются по времени")

    planned_sec = int(shift_type["planned_duration_min"]) * 60
    maximum_sec = int(shift_type["max_duration_min"]) * 60
    driver_slots = int(shift_type["driver_slots"])
    if planned_sec <= 0 or maximum_sec < planned_sec:
        raise ValueError("Некорректная длительность типа смены")
    if driver_slots not in (1, 2):
        raise ValueError("Количество водительских мест должно быть 1 или 2")
    if int(handover_min) < 0:
        raise ValueError("Время пересмены не может быть отрицательным")

    total_span = int(ordered[-1]["arr_sec"]) - int(ordered[0]["dep_sec"])
    if driver_slots == 2:
        if total_span > maximum_sec:
            raise ValueError("Длительность выпуска превышает максимум смены")
        return [_make_shift(ordered, 0, len(ordered) - 1, 1, shift_type, 0)]

    handover_sec = int(handover_min) * 60
    result = []
    start_index = 0
    while start_index < len(ordered):
        remaining_span = (
            int(ordered[-1]["arr_sec"]) - int(ordered[start_index]["dep_sec"])
        )
        if remaining_span <= maximum_sec:
            result.append(
                _make_shift(
                    ordered, start_index, len(ordered) - 1,
                    len(result) + 1, shift_type, 0,
                )
            )
            break

        candidates = []
        for end_index in range(start_index, len(ordered) - 1):
            span = (
                int(ordered[end_index]["arr_sec"])
                - int(ordered[start_index]["dep_sec"])
            )
            gap = (
                int(ordered[end_index + 1]["dep_sec"])
                - int(ordered[end_index]["arr_sec"])
            )
            if span <= maximum_sec and gap >= handover_sec:
                candidates.append((abs(span - planned_sec), -end_index,
                                   end_index, gap))
        if not candidates:
            raise ValueError("Нет допустимого места пересмены между рейсами")
        _, _, end_index, gap = min(candidates)
        result.append(
            _make_shift(
                ordered, start_index, end_index, len(result) + 1,
                shift_type, gap // 60,
            )
        )
        start_index = end_index + 1

    conflicts = validate_output_shift_plan(ordered, result)
    if conflicts:
        raise ValueError("План смен не покрывает рейсы выпуска без конфликтов")
    return result


def replace_shift_boundaries(
    trips, shifts, *, shift_id, trip_from_id, trip_to_id, shift_type
):
    """Replace one shift range while preserving exact contiguous coverage."""
    ordered = _ordered_trips(trips)
    proposed = [dict(row) for row in shifts]
    if validate_output_shift_plan(ordered, proposed):
        raise ValueError("Исходный план смен содержит конфликты")
    positions = {int(row["id"]): index for index, row in enumerate(ordered)}
    try:
        first = positions[int(trip_from_id)]
        last = positions[int(trip_to_id)]
    except (KeyError, TypeError, ValueError):
        raise ValueError("Граница ссылается на неизвестный рейс")
    if first > last:
        raise ValueError("Начальный рейс смены расположен после конечного")

    proposed.sort(key=lambda row: positions[int(row["trip_from_id"])])
    selected_index = next(
        (index for index, row in enumerate(proposed)
         if int(row.get("id", 0)) == int(shift_id)),
        None,
    )
    if selected_index is None:
        raise ValueError("Смена не найдена")
    if selected_index == 0 and first != 0:
        raise ValueError("Первый рейс выпуска должен быть покрыт")
    if selected_index == len(proposed) - 1 and last != len(ordered) - 1:
        raise ValueError("Последний рейс выпуска должен быть покрыт")

    ranges = [
        [positions[int(row["trip_from_id"])], positions[int(row["trip_to_id"])]]
        for row in proposed
    ]
    ranges[selected_index] = [first, last]
    if selected_index:
        ranges[selected_index - 1][1] = first - 1
    if selected_index + 1 < len(ranges):
        ranges[selected_index + 1][0] = last + 1
    if any(start > end for start, end in ranges):
        raise ValueError("Изменение оставляет соседнюю смену без рейсов")

    result = []
    for index, (row, (start, end)) in enumerate(zip(proposed, ranges), start=1):
        updated = dict(row)
        updated.update({
            "shift_number": index,
            "trip_from_id": int(ordered[start]["id"]),
            "trip_to_id": int(ordered[end]["id"]),
            "start_sec": int(ordered[start]["dep_sec"]),
            "end_sec": int(ordered[end]["arr_sec"]),
        })
        if index - 1 == selected_index:
            try:
                slots = int(shift_type["driver_slots"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("Некорректное количество водительских мест")
            if slots not in (1, 2):
                raise ValueError("Количество водительских мест должно быть 1 или 2")
            updated["shift_type_id"] = int(shift_type["id"])
            updated["driver_slots"] = slots
        result.append(updated)
    if validate_output_shift_plan(ordered, result):
        raise ValueError("Изменённый план смен содержит конфликты")
    return result


_replace_shift_boundaries_validated_plan = replace_shift_boundaries


def replace_shift_boundaries(
    trips, shifts, *, shift_id, trip_from_id, trip_to_id, shift_type
):
    try:
        int(shift_type["id"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Некорректный тип смены")
    return _replace_shift_boundaries_validated_plan(
        trips, shifts, shift_id=shift_id, trip_from_id=trip_from_id,
        trip_to_id=trip_to_id, shift_type=shift_type)
