# -*- coding: utf-8 -*-
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(name):
    return (ROOT / "static" / name).read_text(encoding="utf-8")


def _passport_tab(src):
    start = src.index("function routeCardPassport")
    end = src.index("function ", start + 1)
    return src[start:end]


def test_passport_tab_offers_word_generation_buttons():
    src = _src("route-card.js")
    assert "function routeCardGeneratePassport" in src
    assert "routeCardGeneratePassport('D')" in src
    assert "routeCardGeneratePassport('F')" in src
    assert "passport-document.docx" in src
    tab = _passport_tab(src)
    assert "Паспорт" in tab and "routeCardGeneratePassport" in tab


def test_passport_tab_has_editable_comment_not_raw_erm_json():
    src = _src("route-card.js")
    tab = _passport_tab(src)
    # raw notes dump removed from the passport tab
    assert "esc(r.notes" not in tab
    # editable comment control present
    assert 'id="route-comment"' in tab
    assert "function routeCardSaveComment" in src
    assert "/comment" in src
    # ERM JSON heuristic present so JSON notes are not shown as text
    assert "function routeCardCommentValue" in src
    assert 'startsWith("{")' in src


def test_route_card_asset_version_bumped():
    index = _src("index.html")
    assert "route-card.js?v=4.4" in index
