# -*- coding: utf-8 -*-
"""Editable Word route-passport builder with D (portrait) and F (landscape).

The builder consumes the neutral :class:`RouteDocumentData` model and produces
a standards-shaped route passport as an editable ``.docx`` package. Every
administrative value that an operator must fill by hand is emitted as a blank,
editable field; everything derivable from the route network is filled
automatically. Direction pages embed a deterministic schematic plus an
OpenStreetMap raster map that degrades to an offline scheme.
"""

from dataclasses import dataclass
from io import BytesIO
import re

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .route_passport_maps import render_direction_map, render_route_scheme


DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

_DIRECTION_LABELS = {
    "forward": ("ПРЯМОГО", "прямое направление"),
    "backward": ("ОБРАТНОГО", "обратное направление"),
}


@dataclass(frozen=True)
class PassportProfile:
    code: str
    landscape: bool
    accent: str
    page_width_cm: float
    page_height_cm: float
    margin_cm: float


PROFILES = {
    "D": PassportProfile("D", False, "1F4D78", 21.0, 29.7, 1.6),
    "F": PassportProfile("F", True, "276678", 29.7, 21.0, 1.4),
}


# --- Formatting helpers -------------------------------------------------------

def _safe_token(value):
    token = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "_", str(value)).strip("_.")
    return token or "без_номера"


def passport_filename(data, options, style):
    return (
        f"Паспорт_маршрута_{_safe_token(data.route_number)}_"
        f"{style}_{options.effective_date.isoformat()}.docx"
    )


def _km(value):
    if value in (None, ""):
        return "—"
    return f"{float(value):.3f}"


def _coord(value):
    if value in (None, ""):
        return "—"
    return f"{float(value):.6f}"


def _text(value):
    if value in (None, ""):
        return "—"
    return str(value)


def _content_width(profile):
    return Cm(profile.page_width_cm - 2 * profile.margin_cm)


# --- Low-level document primitives -------------------------------------------

def _configure_section(section, profile):
    section.orientation = (
        WD_ORIENT.LANDSCAPE if profile.landscape else WD_ORIENT.PORTRAIT
    )
    section.page_width = Cm(profile.page_width_cm)
    section.page_height = Cm(profile.page_height_cm)
    margin = Cm(profile.margin_cm)
    section.left_margin = margin
    section.right_margin = margin
    section.top_margin = margin
    section.bottom_margin = margin


def _configure_styles(document, profile):
    normal = document.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10)
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), "Arial")
    rfonts.set(qn("w:hAnsi"), "Arial")
    rfonts.set(qn("w:cs"), "Arial")


def _add_page_number(section, profile):
    paragraph = section.footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.text = ""
    _add_run(paragraph, "Страница ", size=8, color=profile.accent)
    _add_field(paragraph, "PAGE")
    _add_run(paragraph, " из ", size=8, color=profile.accent)
    _add_field(paragraph, "NUMPAGES")


def _add_field(paragraph, instruction):
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {instruction} "
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)


def _add_run(paragraph, text, *, bold=False, size=10, color=None):
    run = paragraph.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    return run


def _add_heading(document, text, profile, *, size=13, space_before=12):
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(space_before)
    paragraph.paragraph_format.space_after = Pt(6)
    _add_run(paragraph, text, bold=True, size=size, color=profile.accent)
    return paragraph


def _add_centered(document, text, *, bold=False, size=12, color=None):
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_run(paragraph, text, bold=bold, size=size, color=color)
    return paragraph


def _add_blank_field(document, label, *, lines=1):
    paragraph = document.add_paragraph()
    _add_run(paragraph, f"{label}: ", bold=True)
    _add_run(paragraph, "_" * 48)
    for _ in range(lines - 1):
        extra = document.add_paragraph()
        _add_run(extra, "_" * 72)
    return paragraph


def _add_page_break(document):
    document.add_page_break()


def _shade_cell(cell, color):
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), color)
    cell._tc.get_or_add_tcPr().append(shading)


def _repeat_table_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def _set_cell_margins(cell, *, top=40, bottom=40, left=80, right=80):
    tc_pr = cell._tc.get_or_add_tcPr()
    margins = OxmlElement("w:tcMar")
    for side, value in (
        ("top", top), ("bottom", bottom), ("start", left), ("end", right)
    ):
        node = OxmlElement(f"w:{side}")
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")
        margins.append(node)
    tc_pr.append(margins)


def _add_table(document, headers, rows, widths, profile):
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    header_cells = table.rows[0].cells
    for index, title in enumerate(headers):
        cell = header_cells[index]
        cell.text = ""
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _add_run(paragraph, title, bold=True, size=9, color="FFFFFF")
        _shade_cell(cell, profile.accent)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        _set_cell_margins(cell)
    _repeat_table_header(table.rows[0])
    for record in rows:
        cells = table.add_row().cells
        for index, value in enumerate(record):
            cell = cells[index]
            cell.text = ""
            paragraph = cell.paragraphs[0]
            _add_run(paragraph, str(value), size=9)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            _set_cell_margins(cell)
    for row in table.rows:
        for index, width in enumerate(widths):
            row.cells[index].width = Cm(width)
    return table


def _add_picture(document, png, profile, *, max_cm=None):
    width = _content_width(profile)
    if max_cm is not None:
        width = min(width, Cm(max_cm))
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    run.add_picture(BytesIO(png), width=width)


# --- Content sections ---------------------------------------------------------

def _direction_length_km(section):
    total = 0.0
    for stop in section.stops:
        distance = stop.get("distance_from_prev_km")
        if distance is not None:
            total += float(distance)
    return total


def _add_cover(document, data, options, profile):
    if profile.code == "F":
        _add_technical_cover(document, data, options, profile)
    else:
        _add_department_cover(document, data, options, profile)


def _add_department_cover(document, data, options, profile):
    _add_blank_field(document, "УТВЕРЖДАЮ", lines=2)
    for _ in range(3):
        document.add_paragraph()
    _add_centered(
        document,
        "ПАСПОРТ МАРШРУТА РЕГУЛЯРНОГО СООБЩЕНИЯ",
        bold=True,
        size=20,
        color=profile.accent,
    )
    _add_centered(
        document,
        f"№ {_text(data.route_number)}  «{_text(data.route_name)}»",
        bold=True,
        size=16,
    )
    _add_centered(
        document,
        f"{_text(data.start_point)} — {_text(data.end_point)}",
        size=13,
    )
    document.add_paragraph()
    _add_centered(document, options.season_label, size=12, color=profile.accent)
    _add_centered(
        document,
        f"Дата введения: {options.effective_date.strftime('%d.%m.%Y')}",
        size=11,
    )
    _add_centered(document, f"Редакция маршрута: {data.version}", size=11)
    _add_page_break(document)


def _add_technical_cover(document, data, options, profile):
    _add_centered(
        document,
        "ПАСПОРТ МАРШРУТА РЕГУЛЯРНОГО СООБЩЕНИЯ",
        bold=True,
        size=18,
        color=profile.accent,
    )
    document.add_paragraph()
    rows = (
        ("Номер маршрута", _text(data.route_number)),
        ("Наименование", _text(data.route_name)),
        ("Начальный пункт", _text(data.start_point)),
        ("Конечный пункт", _text(data.end_point)),
        ("Длина прямого направления, км", _km(_direction_length_km(data.forward))),
        ("Длина обратного направления, км", _km(_direction_length_km(data.backward))),
        ("Вид сообщения", "регулярные перевозки пассажиров"),
        ("Дата введения", options.effective_date.strftime("%d.%m.%Y")),
        ("Период действия", options.season_label),
        ("Редакция", str(data.version)),
    )
    _add_table(
        document,
        ("Показатель", "Значение"),
        rows,
        _table_widths(profile, "cover"),
        profile,
    )
    _add_page_break(document)


def _add_approval_page(document, data, options, profile):
    _add_heading(document, "СОГЛАСОВАНИЕ И УТВЕРЖДЕНИЕ", profile)
    _add_blank_field(document, "Организация-перевозчик")
    _add_blank_field(document, "Согласовано (ГИБДД)", lines=2)
    _add_blank_field(document, "Согласовано (орган власти)", lines=2)
    _add_blank_field(document, "Утвердил (должность, Ф.И.О.)")
    _add_blank_field(document, "Дата утверждения")
    _add_page_break(document)


def _add_full_scheme(document, data, profile):
    _add_heading(document, "СХЕМА МАРШРУТА", profile)
    section = data.forward if data.forward.stops else data.backward
    geometry = data.geometries.get(section.direction)
    scheme = render_route_scheme(section, geometry, size=(1400, 900))
    _add_picture(document, scheme, profile)
    _add_centered(
        document,
        "Схема носит справочный характер; редактируется в текстовом редакторе.",
        size=8,
        color="5F6B78",
    )
    _add_page_break(document)


def _add_direction_pages(document, data, direction, profile, *, tile_loader):
    section = getattr(data, direction)
    geometry = data.geometries.get(direction)
    heading, caption = _DIRECTION_LABELS[direction]
    _add_heading(document, f"СХЕМА {heading} НАПРАВЛЕНИЯ", profile)
    scheme = render_route_scheme(section, geometry, size=(1400, 900))
    _add_picture(document, scheme, profile)
    _add_centered(document, f"Схематическое изображение ({caption})", size=8, color="5F6B78")

    _add_heading(document, f"КАРТА {heading} НАПРАВЛЕНИЯ (OpenStreetMap)", profile)
    rendered = render_direction_map(
        section, geometry, size=(1400, 900), tile_loader=tile_loader
    ) if tile_loader is not None else render_direction_map(
        section, geometry, size=(1400, 900)
    )
    _add_picture(document, rendered.png, profile)
    _add_centered(document, rendered.attribution, size=8, color="5F6B78")
    _add_page_break(document)


def _cumulative_rows(section):
    cumulative = 0.0
    rows = []
    for number, stop in enumerate(section.stops, 1):
        distance = stop.get("distance_from_prev_km")
        if distance is not None:
            cumulative += float(distance)
        rows.append((number, stop, cumulative))
    return rows


def _add_distance_tables(document, data, profile):
    _add_heading(document, "ТАБЛИЦА РАССТОЯНИЙ", profile)
    for direction in ("forward", "backward"):
        section = getattr(data, direction)
        if not section.stops:
            continue
        heading, _ = _DIRECTION_LABELS[direction]
        _add_centered(
            document, f"{heading} направление", bold=True, size=11,
            color=profile.accent,
        )
        rows = [
            (
                number,
                _text(stop.get("name")),
                _km(stop.get("distance_from_prev_km")),
                _km(cumulative),
            )
            for number, stop, cumulative in _cumulative_rows(section)
        ]
        _add_table(
            document,
            ("№", "Остановочный пункт", "Расст. от пред., км", "Нарастающим, км"),
            rows,
            _table_widths(profile, "distance"),
            profile,
        )
    _add_page_break(document)


def _add_coordinate_tables(document, data, profile):
    _add_heading(document, "КООРДИНАТЫ ОСТАНОВОЧНЫХ ПУНКТОВ", profile)
    for direction in ("forward", "backward"):
        section = getattr(data, direction)
        if not section.stops:
            continue
        heading, _ = _DIRECTION_LABELS[direction]
        _add_centered(
            document, f"{heading} направление", bold=True, size=11,
            color=profile.accent,
        )
        rows = [
            (
                number,
                _text(stop.get("external_code")),
                _km(stop.get("distance_from_prev_km")),
                _km(cumulative),
                _text(stop.get("name")),
                _text(stop.get("address")),
                _coord(stop.get("latitude")),
                _coord(stop.get("longitude")),
                "",
            )
            for number, stop, cumulative in _cumulative_rows(section)
        ]
        _add_table(
            document,
            (
                "№", "Код", "Расст., км", "Нараст., км", "Остановка",
                "Адрес", "Широта", "Долгота", "МО / ОКАТО",
            ),
            rows,
            _table_widths(profile, "coordinates"),
            profile,
        )
    _add_page_break(document)


def _add_road_characteristics(document, profile):
    _add_heading(document, "ХАРАКТЕРИСТИКА ДОРОГИ", profile)
    rows = [
        (name, "", "", "")
        for name in (
            "Тип покрытия",
            "Ширина проезжей части",
            "Наличие тротуаров",
            "Искусственные сооружения",
            "Опасные участки",
            "Освещение",
        )
    ]
    _add_table(
        document,
        ("Участок / показатель", "Протяжённость", "Состояние", "Примечание"),
        rows,
        _table_widths(profile, "road"),
        profile,
    )
    _add_blank_field(document, "Дополнительные сведения о дороге", lines=2)


def _table_widths(profile, table_kind):
    widths = {
        ("D", "cover"): (7.0, 10.8),
        ("F", "cover"): (9.0, 17.9),
        ("D", "distance"): (1.2, 9.4, 3.6, 3.6),
        ("F", "distance"): (1.4, 14.6, 5.4, 5.5),
        ("D", "coordinates"): (
            0.9, 1.8, 1.9, 1.9, 3.4, 3.4, 1.9, 1.9, 0.7,
        ),
        ("F", "coordinates"): (
            1.1, 2.4, 2.4, 2.4, 5.0, 5.0, 2.7, 2.7, 3.2,
        ),
        ("D", "road"): (7.0, 3.6, 3.6, 3.6),
        ("F", "road"): (11.9, 5.0, 5.0, 5.0),
    }
    return widths[(profile.code, table_kind)]


# --- Orchestration ------------------------------------------------------------

def build_route_passport(data, options, *, style, tile_loader=None):
    normalized = str(style).upper()
    if normalized not in PROFILES:
        raise ValueError("Оформление паспорта должно быть D или F")
    if not data.forward.stops and not data.backward.stops:
        raise ValueError(
            "Для паспорта маршрута необходимо добавить остановки"
        )
    profile = PROFILES[normalized]
    document = Document()
    _configure_styles(document, profile)
    _configure_section(document.sections[0], profile)
    _add_page_number(document.sections[0], profile)
    _add_cover(document, data, options, profile)
    _add_approval_page(document, data, options, profile)
    _add_full_scheme(document, data, profile)
    for direction in ("forward", "backward"):
        if getattr(data, direction).stops:
            _add_direction_pages(
                document, data, direction, profile, tile_loader=tile_loader
            )
    _add_distance_tables(document, data, profile)
    _add_coordinate_tables(document, data, profile)
    _add_road_characteristics(document, profile)
    output = BytesIO()
    document.save(output)
    return output.getvalue()
