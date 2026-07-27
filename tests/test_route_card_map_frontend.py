from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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

    assert "function routeCardFallbackMap" in source
    assert 'class="route-map-canvas"' in source
    assert 'class="route-map-fallback"' in source
    assert 'aria-label="Схема трассы без картографической подложки"' in source
    assert "Подложка OpenStreetMap недоступна" in source
