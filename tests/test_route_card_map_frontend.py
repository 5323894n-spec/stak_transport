import re
from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def function_body(source, name):
    match = re.search(rf"function {name}\([^)]*\)\s*\{{(.*?)\n\}}", source, re.DOTALL)
    assert match, f"missing function {name}"
    return match.group(1)


def test_leaflet_is_vendored_and_loaded_before_route_card():
    vendor = ROOT / "static" / "vendor" / "leaflet"
    leaflet_js = vendor / "leaflet.js"
    leaflet_css = vendor / "leaflet.css"
    license_file = vendor / "LICENSE"
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert leaflet_js.stat().st_size > 100_000
    assert leaflet_css.stat().st_size > 10_000
    assert sha256(leaflet_js.read_bytes()).hexdigest() == (
        "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a"
    )
    assert sha256(leaflet_css.read_bytes()).hexdigest() == (
        "a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6"
    )
    license_bytes = license_file.read_bytes()
    license_text = license_bytes.decode("utf-8")
    assert "Copyright (c) 2010-2023, Volodymyr Agafonkin" in license_text
    assert "Redistribution and use in source and binary forms" in license_text
    assert sha256(license_bytes).hexdigest() == (
        "53e8dc25862014e4324741ca18fbe3611e11d42ef69f59f86ea8c5389647d4cb"
    )
    assert "/static/vendor/leaflet/leaflet.css?v=1.9.4" in index
    leaflet_script = "/static/vendor/leaflet/leaflet.js?v=1.9.4"
    assert leaflet_script in index
    assert index.index(leaflet_script) < index.index("/static/route-card.js")


def test_route_card_keeps_svg_fallback_beside_the_map_canvas():
    source = (ROOT / "static" / "route-card.js").read_text(encoding="utf-8")
    compact_source = "".join(line.strip() for line in source.splitlines())

    assert "function routeCardFallbackMap" in source
    assert 'class="route-map-canvas"' in source
    assert 'class="route-map-fallback"' in source
    assert 'aria-label="Схема трассы без картографической подложки"' in source
    assert "Подложка OpenStreetMap недоступна" in source
    assert '<div class="route-map"><div class="route-map-canvas" hidden></div><div class="route-map-fallback">' in compact_source
    assert 'class="vio w route-map-warning" role="status" aria-live="polite" hidden' in source


def test_route_map_styles_own_responsive_screen_and_print_sizing():
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert ".route-map-canvas" in styles
    assert ".route-map-fallback" in styles
    assert ".route-leaflet-marker" in styles
    assert "height: 460px" in styles
    assert "height: 320px" in styles
    assert "@media print" in styles
    assert 'styles.css?v=3.2&amp;route=3.9' in index
    assert 'route-card.js?v=3.8' in index
    print_styles = styles.split("@media print", 2)[2].split("\n}", 1)[0]
    assert ".route-map { min-height: 360px;" in print_styles


def test_route_card_builds_and_cleans_up_leaflet_map():
    source = (ROOT / "static" / "route-card.js").read_text(encoding="utf-8")

    assert "function routeCardDestroyMap" in source
    assert "function routeCardGeometryPoints" in source
    assert "window.L.map" in source
    assert 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png' in source
    assert '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' in source
    assert 'tileLayer.on("tileerror"' in source
    assert "routeMapInstance.remove()" in source
    assert "fitBounds" in source
    assert 'canvas.style.width = "100%"' not in source
    assert 'canvas.style.height = "390px"' not in source



def test_spa_navigation_destroys_leaflet_map_before_replacing_content():
    app_source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    route_body = function_body(app_source, "route")

    cleanup = 'if (typeof routeCardDestroyMap === "function") routeCardDestroyMap();'
    assert cleanup in route_body
    assert route_body.index(cleanup) < route_body.index('$("content").innerHTML')


def test_route_card_leaflet_markers_are_draggable_and_persist_coordinates():
    source = (ROOT / "static" / "route-card.js").read_text(encoding="utf-8")

    assert "draggable: true" in source
    assert 'className: `route-map-marker${endpointClass}`' in source
    assert "iconSize: [28, 28]" in source
    assert "iconAnchor: [14, 14]" in source
    assert 'marker.on("dragend"' in source
    assert "let committed =" in source
    assert "let saving = false" in source
    assert "if (saving)" in source
    assert "marker.setLatLng(committed)" in source
    assert "marker.dragging.disable()" in source
    assert "committed = [latitude, longitude]" in source
    assert "marker.dragging.enable()" in source
    assert 'method: "PUT"' in source
    assert "row.stop.latitude = latitude" in source
    assert "row.stop.longitude = longitude" in source



def test_route_map_marker_labels_keep_full_stop_sequence_when_coordinates_are_missing():
    source = (ROOT / "static" / "route-card.js").read_text(encoding="utf-8")
    points = function_body(source, "routeMapPoints")
    bind = function_body(source, "routeCardBindMap")

    assert ".map((row, index) => ({ row, sequence: index + 1 }))" in points
    assert "${p.sequence}. ${esc(p.row.stop.name)}" in source
    assert ".map((row, index) => ({ row, sequence: index + 1 }))" in bind
    assert "html: `<span>${sequence}</span>`" in bind
    assert "marker.bindTooltip(`${sequence}. ${esc(row.stop.name)}`)" in bind


def test_route_card_clears_stale_osrm_geometry_on_direction_change_and_cancel():
    source = (ROOT / "static" / "route-card.js").read_text(encoding="utf-8")
    direction = function_body(source, "routeCardDirection")
    cancel = function_body(source, "routeCardCancelOsrm")

    assert "state.osrmPreview = null" in direction
    assert "state.geometry = null" in direction
    assert "window._routeCard.osrmPreview = null" in cancel
    assert "window._routeCard.geometry = null" in cancel
