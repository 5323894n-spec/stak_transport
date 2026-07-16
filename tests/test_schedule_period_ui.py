# -*- coding: utf-8 -*-
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_schedule_view_offers_period_preview_without_replacing_legacy_generator():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "schedulePeriodPreview" in source
    assert "/periods/${st.day_type}/preview" in source
    assert "/api/trips/generate" in source
    assert "scheduleOpenPeriods" in source
    assert "routeCardState" in source


def test_schedule_period_preview_is_explicitly_non_destructive():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "route_trips" in source
    assert "schedule-period-preview" in source
