# -*- coding: utf-8 -*-
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_route_card_contains_complete_period_editor_and_safe_template_flow():
    source = (ROOT / "static" / "route-card.js").read_text(encoding="utf-8")
    assert '["periods",' in source
    assert "routeCardPeriods" in source
    assert "routeCardPeriodAdd" in source
    assert "routeCardPeriodDuplicate" in source
    assert "routeCardPeriodMove" in source
    assert "routeCardPeriodSave" in source
    assert "template-preview" in source
    assert "template-apply" in source
    assert "routeCardPeriodPreview" in source
    assert "periods/${state.routeId}/${state.periodDay}" not in source
    assert "/periods/${state.periodDay}/preview" in source


def test_period_styles_and_route_cache_version_are_present():
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    for class_name in ("route-period-grid", "route-period-row", "route-period-timeline",
                       "route-period-block", "route-demand-grid", "route-demand-jump"):
        assert "." + class_name in styles
    assert "route=4.0" in index
