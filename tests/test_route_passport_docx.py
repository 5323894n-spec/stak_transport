# -*- coding: utf-8 -*-
import datetime
from io import BytesIO
from zipfile import ZipFile

import pytest
from docx import Document
from docx.enum.section import WD_ORIENT

from app.route_document_data import DocumentOptions, load_route_document_data
from app.route_passport_docx import build_route_passport, passport_filename
from tests.test_route_document_data import _database


def _offline_tiles(url, timeout):
    raise OSError("offline")


def _passport(tmp_path, style):
    con, route_id = _database(tmp_path)
    try:
        data = load_route_document_data(con, route_id)
    finally:
        con.close()
    options = DocumentOptions(
        season="winter",
        season_label="ЗИМНИЙ ПЕРИОД",
        file_token="ЗИМА",
        effective_date=datetime.date(2026, 8, 3),
    )
    payload = build_route_passport(
        data, options, style=style, tile_loader=_offline_tiles
    )
    return data, options, payload


def _text(document):
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _all_cells(document):
    return [
        cell.text
        for table in document.tables
        for row in table.rows
        for cell in row.cells
    ]


def test_portrait_d_is_valid_editable_docx_with_required_sections(tmp_path):
    data, options, payload = _passport(tmp_path, "D")
    with ZipFile(BytesIO(payload)) as package:
        assert "word/document.xml" in package.namelist()
    document = Document(BytesIO(payload))
    text = _text(document)
    assert "ПАСПОРТ МАРШРУТА РЕГУЛЯРНОГО СООБЩЕНИЯ" in text
    assert "СХЕМА ПРЯМОГО НАПРАВЛЕНИЯ" in text
    assert "ХАРАКТЕРИСТИКА ДОРОГИ" in text
    assert document.sections[0].orientation == WD_ORIENT.PORTRAIT
    assert passport_filename(data, options, "D").endswith("_D_2026-08-03.docx")


def test_passport_embeds_direction_images_and_stop_tables(tmp_path):
    _, _, payload = _passport(tmp_path, "D")
    with ZipFile(BytesIO(payload)) as package:
        images = [n for n in package.namelist() if n.startswith("word/media/")]
    assert images, "passport must embed rendered PNG images"
    document = Document(BytesIO(payload))
    cells = _all_cells(document)
    assert "Вокзал" in cells
    assert "Аэропорт" in cells
    assert "2.500" in cells


def test_passport_filename_is_filesystem_safe(tmp_path):
    data, options, _ = _passport(tmp_path, "D")
    name = passport_filename(data, options, "D")
    assert name == "Паспорт_маршрута_42_D_2026-08-03.docx"


def test_passport_rejects_unknown_style(tmp_path):
    data, options, _ = _passport(tmp_path, "D")
    with pytest.raises(ValueError, match="D или F"):
        build_route_passport(data, options, style="X", tile_loader=_offline_tiles)


def test_landscape_f_uses_wide_pages_and_same_stop_values(tmp_path):
    con, route_id = _database(tmp_path)
    try:
        data = load_route_document_data(con, route_id)
    finally:
        con.close()
    options = DocumentOptions(
        "winter", "ЗИМНИЙ ПЕРИОД", "ЗИМА", datetime.date(2026, 8, 3)
    )
    d_payload = build_route_passport(
        data, options, style="D", tile_loader=_offline_tiles
    )
    f_payload = build_route_passport(
        data, options, style="F", tile_loader=_offline_tiles
    )
    d = Document(BytesIO(d_payload))
    f = Document(BytesIO(f_payload))
    assert f.sections[0].orientation == WD_ORIENT.LANDSCAPE
    assert f.sections[0].page_width > f.sections[0].page_height
    assert d.sections[0].page_width < d.sections[0].page_height
    d_cells = _all_cells(d)
    f_cells = _all_cells(f)
    for value in ("Вокзал", "Аэропорт", "2.500"):
        assert value in d_cells
        assert value in f_cells


def test_passport_requires_stops(tmp_path):
    con, _ = _database(tmp_path)
    try:
        route_id = con.execute(
            "INSERT INTO routes(number,name) VALUES(?,?)", ("99", "Пустой")
        ).lastrowid
        con.commit()
        empty = load_route_document_data(con, route_id)
    finally:
        con.close()
    options = DocumentOptions(
        "winter", "ЗИМНИЙ ПЕРИОД", "ЗИМА", datetime.date(2026, 8, 3)
    )
    with pytest.raises(ValueError, match="остановк"):
        build_route_passport(empty, options, style="D", tile_loader=_offline_tiles)
