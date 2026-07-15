# -*- coding: utf-8 -*-
import pytest

from app.route_network import normalize_stop_name, recalculate_trace


def test_normalize_stop_name_collapses_case_spacing_and_quotes():
    assert normalize_stop_name('  ОП «Автовокзал» ') == 'оп "автовокзал"'


def test_recalculate_trace_builds_cumulative_distance():
    rows = recalculate_trace([
        {"sequence": 1, "distance_from_prev_km": 0},
        {"sequence": 2, "distance_from_prev_km": 1.25},
        {"sequence": 3, "distance_from_prev_km": 0.75},
    ])

    assert [row["cumulative_km"] for row in rows] == [0.0, 1.25, 2.0]


def test_recalculate_trace_rejects_sequence_gaps():
    with pytest.raises(ValueError, match="не иметь пропусков"):
        recalculate_trace([
            {"sequence": 1, "distance_from_prev_km": 0},
            {"sequence": 3, "distance_from_prev_km": 1.0},
        ])


def test_recalculate_trace_rejects_nonzero_first_segment():
    with pytest.raises(ValueError, match="первой остановки"):
        recalculate_trace([{"sequence": 1, "distance_from_prev_km": 0.5}])


def test_recalculate_trace_rejects_negative_distance():
    with pytest.raises(ValueError, match="отрицательным"):
        recalculate_trace([
            {"sequence": 1, "distance_from_prev_km": 0},
            {"sequence": 2, "distance_from_prev_km": -0.1},
        ])
