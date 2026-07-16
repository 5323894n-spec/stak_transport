# -*- coding: utf-8 -*-

import pytest

from app.route_shifts import build_output_shifts, validate_output_shift_plan


def trip(trip_id, dep, arr, output_number=1):
    return {
        "id": trip_id,
        "dep_sec": dep,
        "arr_sec": arr,
        "output_number": output_number,
    }


def shift_type(*, driver_slots=1, planned=480, maximum=600):
    return {
        "id": 1,
        "planned_duration_min": planned,
        "max_duration_min": maximum,
        "driver_slots": driver_slots,
    }


def test_splitter_uses_valid_handover_gap_and_covers_every_trip():
    trips = [
        trip(1, 6 * 3600, 9 * 3600),
        trip(2, 9 * 3600 + 15 * 60, 13 * 3600),
        trip(3, 13 * 3600 + 20 * 60, 17 * 3600),
    ]

    shifts = build_output_shifts(
        trips,
        shift_type=shift_type(),
        handover_min=10,
    )

    assert [
        (row["trip_from_id"], row["trip_to_id"]) for row in shifts
    ] == [(1, 2), (3, 3)]
    assert shifts[0]["handover_after_min"] == 20
    assert validate_output_shift_plan(trips, shifts) == []


def test_splitter_rejects_long_output_without_valid_handover():
    trips = [
        trip(1, 6 * 3600, 12 * 3600),
        trip(2, 12 * 3600 + 5 * 60, 18 * 3600),
    ]

    with pytest.raises(ValueError, match="пересмен"):
        build_output_shifts(
            trips,
            shift_type=shift_type(),
            handover_min=10,
        )


def test_two_driver_type_keeps_long_output_as_one_shift():
    trips = [
        trip(1, 6 * 3600, 12 * 3600),
        trip(2, 12 * 3600 + 10 * 60, 20 * 3600),
    ]

    shifts = build_output_shifts(
        trips,
        shift_type=shift_type(
            driver_slots=2,
            planned=900,
            maximum=1080,
        ),
        handover_min=10,
    )

    assert len(shifts) == 1
    assert shifts[0]["driver_slots"] == 2
    assert shifts[0]["trip_from_id"] == 1
    assert shifts[0]["trip_to_id"] == 2


def test_splitter_rejects_overlapping_trips():
    trips = [trip(1, 6 * 3600, 10 * 3600), trip(2, 9 * 3600, 12 * 3600)]

    with pytest.raises(ValueError, match="пересека"):
        build_output_shifts(
            trips,
            shift_type=shift_type(),
            handover_min=10,
        )


def test_validator_reports_uncovered_and_duplicate_trips():
    trips = [trip(1, 6 * 3600, 8 * 3600), trip(2, 9 * 3600, 11 * 3600)]
    shifts = [
        {
            "shift_number": 1,
            "trip_from_id": 1,
            "trip_to_id": 1,
            "start_sec": 6 * 3600,
            "end_sec": 8 * 3600,
            "driver_slots": 1,
            "handover_after_min": 60,
        },
        {
            "shift_number": 2,
            "trip_from_id": 1,
            "trip_to_id": 1,
            "start_sec": 6 * 3600,
            "end_sec": 8 * 3600,
            "driver_slots": 1,
            "handover_after_min": 0,
        },
    ]

    codes = {item["code"] for item in validate_output_shift_plan(trips, shifts)}

    assert {"duplicate_trip", "uncovered_trip"} <= codes


def test_validator_reports_shift_assigned_to_another_output():
    trips = [trip(1, 6 * 3600, 8 * 3600, output_number=7)]
    shifts = [
        {
            "shift_number": 1,
            "output_number": 8,
            "trip_from_id": 1,
            "trip_to_id": 1,
            "start_sec": 6 * 3600,
            "end_sec": 8 * 3600,
            "driver_slots": 1,
            "handover_after_min": 0,
        }
    ]

    conflicts = validate_output_shift_plan(trips, shifts)

    assert any(
        item["code"] == "output_mismatch" for item in conflicts
    )
