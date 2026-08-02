# -*- coding: utf-8 -*-
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "js" / "route_geometry_editor_behavior.js"
UI_HARNESS = ROOT / "tests" / "js" / "route_geometry_ui_behavior.js"


@pytest.mark.parametrize(
    "scenario",
    [
        "draft_lifecycle",
        "anchors_locked",
        "insert_delete",
        "sparse",
        "nearest",
        "cloning",
        "invalid",
        "labels",
    ],
)
def test_route_geometry_editor_behavior(scenario):
    node = shutil.which("node")
    assert node, "Node.js is required for executable route geometry tests"
    result = subprocess.run(
        [node, str(HARNESS), scenario],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "scenario",
    [
        "save_conflict",
        "save_success",
        "navigation_guard",
        "reset",
        "osrm_guard",
        "read_only",
        "leaflet_controls",
    ],
)
def test_route_geometry_ui_behavior(scenario):
    node = shutil.which("node")
    assert node, "Node.js is required for executable route geometry tests"
    result = subprocess.run(
        [node, str(UI_HARNESS), scenario],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_route_geometry_leaflet_controls_and_editor_styles_are_integrated():
    source = (ROOT / "static" / "route-card.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "RouteGeometryEditor.visibleVertexIndexes" in source
    assert "RouteGeometryEditor.nearestSegmentIndex" in source
    assert "routeCardDeleteGeometryPoint" in source
    assert 'currentLine.on("click"' in source
    assert 'controlMarker.on("dragend"' in source
    assert "routeCardGeometryKeydown" in source
    assert "120" in source
    assert ".route-geometry-control" in styles
    assert ".route-geometry-control.is-selected" in styles
    assert ".route-geometry-editor" in styles
    assert ".route-osrm-preview" in styles


def test_geometry_editor_loads_before_route_card_with_matching_cache_keys():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    editor = "/static/route-geometry-editor.js?v=1.0"
    card = "/static/route-card.js?v=4.1"

    assert editor in index and card in index
    assert index.index(editor) < index.index(card)
    assert "styles.css?v=3.3&amp;route=4.1" in index
    assert "app.js?v=3.2&amp;route=4.1" in index
