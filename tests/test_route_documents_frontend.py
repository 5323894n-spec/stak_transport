# -*- coding: utf-8 -*-
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(name):
    return (ROOT / "static" / name).read_text(encoding="utf-8")


def test_route_card_exposes_depot_editor_and_document_dialog_contracts():
    source = _source("route-card.js")

    assert '["depot", "Нулевые рейсы"]' in source
    assert "depotDirection" in source
    assert "depotDrafts" in source
    assert "Из парка" in source
    assert "В парк" in source
    assert "route-depot-tabs" in source
    assert "route-depot-grid" in source
    assert "route-depot-row" in source
    assert "Экспорт документов" in source
    assert "route-document-dialog" in source
    assert "Сезон" in source
    assert "Дата начала действия" in source


def test_depot_editor_uses_authenticated_api_contract_and_reload_after_save():
    source = _source("route-card.js")

    assert "function routeCardLoadDepot" in source
    assert "function routeCardSaveDepot" in source
    assert "function routeCardDepotPayload" in source
    assert "function routeCardDepotMove" in source
    assert "function routeCardDepotRemove" in source
    assert "function routeCardDepotAdd" in source
    assert "/depot-stops?direction=${direction}" in source
    assert "/api/routes/${routeId}/depot-stops/${direction}" in source
    assert "method: \"PUT\"" in source
    assert "await routeCardLoadDepot(direction, state)" in source


def test_depot_editor_validates_rows_without_coercing_invalid_text_to_zero():
    source = _source("route-card.js")

    assert "Number.isFinite" in source
    assert "Number.isSafeInteger" in source
    assert "Остановка обязательна" in source
    assert "Расстояние должно быть конечным неотрицательным числом" in source
    assert 'routeCardDepotRuntime(row.run_time_day_sec, "Дневное время")' in source
    assert 'routeCardDepotRuntime(row.run_time_night_sec, "Ночное время")' in source
    assert "должно быть целым числом секунд" in source
    assert "run_time_day_sec" in source
    assert "run_time_night_sec" in source
    assert "sequence: index + 1" in source
    assert "depotErrors" in source
    payload_source = source[
        source.index("function routeCardDepotPayload"):
        source.index("async function routeCardSaveDepot")
    ]
    assert "+value || 0" not in payload_source


def test_depot_editor_has_explicit_units_and_read_only_guard():
    source = _source("route-card.js")

    assert "Расстояние, км" in source
    assert "Днём, сек" in source
    assert "Ночью, сек" in source
    assert "function routeCardCanEdit" in source
    assert "routeCardCanEdit()" in source
    assert "disabled" in source


def test_depot_editor_styles_are_responsive_and_print_safe():
    styles = _source("styles.css")

    for selector in (
        ".route-depot-tabs", ".route-depot-grid", ".route-depot-row",
        ".route-document-dialog",
    ):
        assert selector in styles
    assert "@media (max-width: 760px)" in styles
    responsive = styles[styles.index("@media (max-width: 760px)"):]
    assert ".route-depot-row" in responsive
    assert "grid-row" in responsive
    assert "@media print" in styles
    print_rules = styles[styles.index("@media print"):]
    assert ".route-depot-editor-controls" in print_rules
    assert ".route-document-dialog" in print_rules


def test_route_document_asset_versions_are_bumped_without_leaflet_reordering():
    index = _source("index.html")

    assert "styles.css?v=3.2&amp;route=3.9" in index
    assert "app.js?v=3.2&amp;route=3.9" in index
    assert "route-card.js?v=3.8" in index
    assert index.index("/static/vendor/leaflet/leaflet.js") < index.index(
        "/static/route-card.js"
    )


def test_route_document_dialog_download_contract():
    source = _source("route-card.js")

    assert "function routeCardDocumentDownload" in source
    assert "schedule-document.xlsx" in source
    assert "erm-export.xlsx" in source
    assert "Укажите дату начала действия" in source
    assert "new URLSearchParams" in source
    assert "effective_date" in source
    assert "openWin" in source
    assert 'id="route-document-kind"' in source
    assert 'id="route-document-season"' in source
    assert 'id="route-document-date"' in source
    assert 'onclick="routeCardDocumentDownload()"' in source
    assert "function routeCardDocumentToday" in source
    today_source = source[
        source.index("function routeCardDocumentToday"):
        source.index("function routeCardState")
    ]
    assert "getFullYear()" in today_source
    assert "getMonth()" in today_source
    assert "getDate()" in today_source
    assert "toISOString" not in today_source
    assert "documentDate: routeCardDocumentToday()" in source
    assert 'document.getElementById("route-document-date")' in source
    assert "setCustomValidity" in source
    assert "reportValidity()" in source
    assert "focus()" in source
    date_control = source[
        source.index('<input id="route-document-date"'):
        source.index("</label>", source.index('<input id="route-document-date"'))
    ]
    assert "required" in date_control
    assert "setCustomValidity('')" in date_control
