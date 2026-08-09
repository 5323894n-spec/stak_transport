# -*- coding: utf-8 -*-
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(name):
    return (ROOT / "static" / name).read_text(encoding="utf-8")


def test_dispatch_nav_and_view():
    app = _src("app.js")
    js = _src("dispatch.js")
    assert '["dispatch", "Диспетчер"]' in app
    assert "VIEWS.dispatch" in js
    assert "/api/dispatch/board" in js
    assert "/api/dispatch/telemetry" in js
    assert "/api/dispatch/source-mode" in js
    assert "dispatchDeviationLabel" in js
    assert "Смоделировать выпуск" in js


def test_index_loads_dispatch_script():
    index = _src("index.html")
    assert "/static/dispatch.js?v=1.1" in index
    assert "app.js?v=3.4" in index


def test_dispatch_styles():
    styles = _src("styles.css")
    assert ".dispatch-tab" in styles
    assert "@media print" in styles
