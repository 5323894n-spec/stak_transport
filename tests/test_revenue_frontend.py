# -*- coding: utf-8 -*-
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(name):
    return (ROOT / "static" / name).read_text(encoding="utf-8")


def test_nav_and_view_registered():
    app = _src("app.js")
    assert '["revenue", "Выручка"]' in app
    revenue = _src("revenue.js")
    assert "VIEWS.revenue" in revenue
    assert "/api/revenue/sheets" in revenue
    assert "/api/revenue/fare-types" in revenue
    assert "revenueRecalcExpected" in revenue


def test_index_loads_revenue_script():
    index = _src("index.html")
    assert "/static/revenue.js?v=1.1" in index


def test_styles_have_revenue_rules():
    styles = _src("styles.css")
    assert ".revenue-tab" in styles
    assert "@media print" in styles
