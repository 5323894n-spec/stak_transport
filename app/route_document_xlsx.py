# -*- coding: utf-8 -*-
"""Shared OpenPyXL presentation helpers for route documents."""

from io import BytesIO
from urllib.parse import quote

from fastapi.responses import StreamingResponse
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


NAVY = "17324D"
BLUE = "DCE8F3"
PALE_BLUE = "EEF4FB"
PALE_GREEN = "E9F6EE"
PALE_AMBER = "FFF3DD"

_WHITE = "FFFFFF"
_BORDER = Border(
    left=Side(style="thin", color=NAVY),
    right=Side(style="thin", color=NAVY),
    top=Side(style="thin", color=NAVY),
    bottom=Side(style="thin", color=NAVY),
)


def apply_sheet_setup(ws, *, landscape=True):
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.oddFooter.center.text = "Страница &P из &N"
    return ws


def write_excel_time(cell, seconds):
    cell.value = None if seconds is None else seconds / 86400
    cell.number_format = "[h]:mm"
    return cell


def _write_band(ws, row, text, *, start_col, end_col, color, font_color, size):
    if end_col < start_col:
        raise ValueError("Последний столбец полосы не может быть меньше первого")
    ws.merge_cells(
        start_row=row, start_column=start_col, end_row=row, end_column=end_col
    )
    cell = ws.cell(row, start_col, text)
    cell.fill = PatternFill("solid", fgColor=color)
    cell.font = Font(bold=True, color=font_color, size=size)
    cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    cell.border = _BORDER
    for column in range(start_col + 1, end_col + 1):
        merged_cell = ws.cell(row, column)
        merged_cell.fill = PatternFill("solid", fgColor=color)
        merged_cell.border = _BORDER
    return cell


def write_title_band(ws, row, text, *, start_col=1, end_col=8):
    return _write_band(
        ws, row, text, start_col=start_col, end_col=end_col,
        color=NAVY, font_color=_WHITE, size=14,
    )


def write_section_header(ws, row, text, *, start_col=1, end_col=8):
    return _write_band(
        ws, row, text, start_col=start_col, end_col=end_col,
        color=BLUE, font_color=NAVY, size=11,
    )


def write_table_header(ws, row, headers, *, start_col=1):
    cells = []
    for offset, header in enumerate(headers):
        cell = ws.cell(row, start_col + offset, header)
        cell.fill = PatternFill("solid", fgColor=PALE_BLUE)
        cell.font = Font(bold=True, color=NAVY)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _BORDER
        cells.append(cell)
    return tuple(cells)


def apply_warning_cell(cell):
    cell.fill = PatternFill("solid", fgColor=PALE_AMBER)
    cell.font = Font(color=NAVY)
    cell.alignment = Alignment(vertical="top", wrap_text=True)
    cell.border = _BORDER
    return cell


def set_print_area(ws, *, min_row=1, min_col=1, max_row=None, max_col=None):
    max_row = ws.max_row if max_row is None else max_row
    max_col = ws.max_column if max_col is None else max_col
    start = f"{get_column_letter(min_col)}{min_row}"
    end = f"{get_column_letter(max_col)}{max_row}"
    ws.print_area = f"{start}:{end}"
    return ws.print_area


def _xlsx_download_response(workbook, filename):
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    safe_filename = str(filename).replace("\r", "").replace("\n", "")
    encoded_name = quote(safe_filename, safe="")
    return StreamingResponse(
        stream,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"
        },
    )
