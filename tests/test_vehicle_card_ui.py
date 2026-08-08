from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_vehicle_card_has_route_tabs_search_incidents_gallery_and_exports():
    js = "\n".join(
        (ROOT / "static" / name).read_text(encoding="utf-8")
        for name in ("app.js", "vehicle-card.js")
    )
    for marker in (
        "VIEWS.vehicleCard",
        '"Обзор"',
        '"Ремонты"',
        '"Запчасти"',
        '"Исполнители"',
        '"ДТП и повреждения"',
        '"ТО"',
        '"Фотографии и документы"',
        '"Затраты"',
        '"История"',
        "vehicleCardIncident",
        "vehicleCardUpload",
        "/media",
        "/incidents",
        "/export.xlsx",
        "/print",
        "гаражному номеру, госномеру или VIN",
    ):
        assert marker in js
    css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert ".vehicle-card" in css
    assert ".vehicle-gallery" in css
    assert ".vehicle-timeline" in css


def test_index_uses_vehicle_card_cache_version():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert '/static/app.js?v=3.3' in html
    assert '/static/styles.css?v=3.3' in html
