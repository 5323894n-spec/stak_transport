# -*- coding: utf-8 -*-
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_schedule_shift_workspace_exposes_settings_preview_and_actions():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    for identifier in (
        "scheduleShiftSettings",
        "scheduleShiftPreview",
        "scheduleShiftApply",
        "scheduleOutputShifts",
        "scheduleShiftEdit",
    ):
        assert identifier in source

    for endpoint in (
        "/shift-generation/preview",
        "/shift-generation/apply",
        "/output-shifts/reset-manual",
    ):
        assert endpoint in source

    assert 'id="schedule-shift-preview-button"' in source


def test_schedule_shift_loads_and_mutations_are_scope_guarded():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    for contract in (
        "scheduleLoadEpoch",
        "scheduleScopeCurrent",
        "scheduleMutationScopeCurrent",
        "shiftBusy",
    ):
        assert contract in source
    assert "const loadEpoch = ++st.scheduleLoadEpoch" in source
    assert "if (!scheduleScopeCurrent" in source
    assert source.count("st.shiftPreview = null") >= 4
    assert "if (!scheduleScopeCurrent(st, routeId, dayType, loadEpoch)) return;\n    throw error;" in source


def test_output_shift_workspace_uses_direct_read_model():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "/output-shifts?day_type=${encodeURIComponent(dayType)}" in source
    assert "scheduleShiftCandidateDates" not in source
    assert "assignment_count_scope" in source
    assert "назначений всего" in source


def test_reset_controls_are_limited_to_manual_locks_and_warn_about_assignments():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "const anyLocked = structural.some" in source
    assert "const outputLocked = shifts.some" in source
    assert "locked ? `<div class=\"toolbar\">" in source
    assert "могут быть отвязаны; при необходимости их потребуется привязать заново" in source


def test_schedule_shift_workspace_has_state_styles():
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    for selector in (
        ".schedule-output-shifts",
        ".schedule-shift-card",
        ".schedule-driver-slots",
        ".schedule-shift-locked",
        ".schedule-shift-conflict",
    ):
        assert selector in styles


def test_schedule_assets_use_route_cache_version_3_8():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert 'styles.css?v=3.2&amp;route=3.9' in index
    assert 'app.js?v=3.2&amp;route=3.9' in index
    assert 'route-card.js?v=3.8' in index
