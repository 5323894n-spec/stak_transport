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


def test_schedule_assets_use_route_cache_version_3_6():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert 'styles.css?v=3.2&amp;route=3.6' in index
    assert 'app.js?v=3.2&amp;route=3.6' in index
