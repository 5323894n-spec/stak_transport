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
    assert "Leaflet" in license_file.read_text(encoding="utf-8")
    assert "/static/vendor/leaflet/leaflet.css?v=1.9.4" in index
    leaflet_script = "/static/vendor/leaflet/leaflet.js?v=1.9.4"
    assert leaflet_script in index
    assert index.index(leaflet_script) < index.index("/static/route-card.js")
