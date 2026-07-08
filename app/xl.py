# -*- coding: utf-8 -*-
"""Экспорт в Excel."""
import datetime
import io
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from fastapi.responses import Response


def xlsx_response(title, headers, data_rows, filename="export.xlsx", col_widths=None):
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31] or "Лист1"
    thin = Border(*[Side(style="thin")] * 4)
    ws.append([title])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(1, len(headers)))
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(headers)
    for c in ws[2]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDE7F3")
        c.border = thin
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for r in data_rows:
        ws.append(list(r))
    for row in ws.iter_rows(min_row=3):
        for c in row:
            c.border = thin
    for i, h in enumerate(headers, 1):
        w = (col_widths[i-1] if col_widths and i <= len(col_widths) else max(10, min(35, len(str(h)) + 4)))
        ws.column_dimensions[ws.cell(row=2, column=i).column_letter].width = w
    buf = io.BytesIO()
    wb.save(buf)
    encoded_name = quote(filename, safe="")
    return Response(buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"})


def _xlsx_download_response(wb, filename):
    buf = io.BytesIO()
    wb.save(buf)
    encoded_name = quote(filename, safe="")
    return Response(buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"})


def _date_label(date_text):
    try:
        return datetime.date.fromisoformat(date_text).strftime("%d.%m.%Y")
    except Exception:
        return date_text or ""


def _num(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _merge_value(ws, row, col1, col2, value, *, fill=None, font=None, alignment=None, border=None):
    ws.merge_cells(start_row=row, start_column=col1, end_row=row, end_column=col2)
    cell = ws.cell(row=row, column=col1, value=value)
    if fill:
        cell.fill = fill
    if font:
        cell.font = font
    if alignment:
        cell.alignment = alignment
    if border:
        for col in range(col1, col2 + 1):
            ws.cell(row=row, column=col).border = border
    return cell


def order_xlsx_response(order, settings, lines, filename="naryad.xlsx"):
    headers = [
        "Маршрут", "Выход", "Смена", "Водитель", "Таб.№", "Автобус (гар.)", "Госномер", "Явка", "Выезд",
        "Начало", "Окончание", "Заезд", "Часы", "Рейсов", "Пробег, км", "План. топливо, л", "Статус", "Отметки",
    ]
    header_row = 9
    data_start = header_row + 1
    last_col = len(headers)
    last_letter = get_column_letter(last_col)
    date_text = order.get("date", "")
    sheet_title = f"Наряд {date_text}"[:31] or "Наряд"

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    ws.sheet_view.showGridLines = False

    dark = "1F4E78"
    mid = "D9EAF7"
    light = "EEF5FB"
    card_fill = PatternFill("solid", fgColor="EAF2F8")
    header_fill = PatternFill("solid", fgColor=dark)
    sub_fill = PatternFill("solid", fgColor=mid)
    neutral_fill = PatternFill("solid", fgColor="F6F8FA")
    warn_fill = PatternFill("solid", fgColor="FFF2CC")
    ok_fill = PatternFill("solid", fgColor="E2F0D9")
    bad_fill = PatternFill("solid", fgColor="F4CCCC")
    white_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    title_font = Font(name="Calibri", size=16, bold=True, color=dark)
    org_font = Font(name="Calibri", size=12, bold=True, color="22313F")
    bold_font = Font(name="Calibri", size=10, bold=True, color="22313F")
    body_font = Font(name="Calibri", size=10, color="22313F")
    small_font = Font(name="Calibri", size=9, color="566573")
    thin_side = Side(style="thin", color="B7C9D6")
    medium_side = Side(style="medium", color="6C8EAD")
    thin = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
    header_border = Border(left=thin_side, right=thin_side, top=medium_side, bottom=medium_side)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right = Alignment(horizontal="right", vertical="center")

    org_name = settings.get("org_name") or "Автотранспортное предприятие"
    approved = order.get("approved_by") or ""
    status = order.get("status") or ""
    date_label = _date_label(date_text)
    now_label = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")

    _merge_value(ws, 1, 1, last_col, org_name, font=org_font, alignment=center)
    _merge_value(ws, 2, 1, last_col, "НАРЯД НА ВЫПУСК АВТОБУСОВ", font=title_font, alignment=center)
    meta = f"на {date_label}   |   статус: {status or '—'}"
    if approved:
        meta += f"   |   утвердил: {approved}"
    _merge_value(ws, 3, 1, last_col, meta, font=small_font, alignment=center)

    data_end = data_start + len(lines) - 1 if lines else data_start
    card_pairs = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16), (17, 18)]
    summary = [
        ("Всего строк", f"=COUNTA(Q{data_start}:Q{data_end})"),
        ("В работе", f"=COUNTIFS(Q{data_start}:Q{data_end},\"<>отменен\",Q{data_start}:Q{data_end},\"<>\")"),
        ("Отменено", f"=COUNTIF(Q{data_start}:Q{data_end},\"отменен\")"),
        ("Водителей", f"=COUNTA(D{data_start}:D{data_end})"),
        ("Автобусов", f"=COUNTA(F{data_start}:F{data_end})"),
        ("Часов", f"=SUM(M{data_start}:M{data_end})"),
        ("Рейсов", f"=SUM(N{data_start}:N{data_end})"),
        ("Пробег, км", f"=SUM(O{data_start}:O{data_end})"),
        ("Топливо, л", f"=SUM(P{data_start}:P{data_end})"),
    ]
    for (col1, col2), (label, formula) in zip(card_pairs, summary):
        _merge_value(ws, 5, col1, col2, label, fill=card_fill, font=small_font, alignment=center, border=thin)
        cell = _merge_value(ws, 6, col1, col2, formula, fill=PatternFill("solid", fgColor="FFFFFF"), font=bold_font, alignment=center, border=thin)
        if label in {"Часов", "Пробег, км", "Топливо, л"}:
            cell.number_format = "0.0"
        else:
            cell.number_format = "0"

    _merge_value(ws, 8, 1, last_col, "Детализация выпусков", fill=sub_fill, font=bold_font, alignment=left, border=thin)
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col, value=header)
        cell.fill = header_fill
        cell.font = white_font
        cell.alignment = center
        cell.border = header_border

    for offset, line in enumerate(lines):
        row = data_start + offset
        route = f"№ {line.get('rn') or ''} {line.get('route_name') or ''}".strip()
        values = [
            route,
            line.get("output_number"),
            line.get("shift_number"),
            line.get("fio") or "",
            line.get("tab_number") or "",
            line.get("garage_number") or "",
            line.get("plate") or "",
            line.get("report_time") or "",
            line.get("depart_depot") or "",
            line.get("start_line") or "",
            line.get("end_line") or "",
            line.get("return_depot") or "",
            _num(line.get("shift_hours")),
            int(_num(line.get("trips_count"))),
            _num(line.get("distance_km")),
            _num(line.get("planned_fuel")),
            line.get("status") or "",
            line.get("dispatcher_note") or "",
        ]
        for col, value in enumerate(values, 1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.font = body_font
            cell.border = thin
            cell.alignment = right if col in (2, 3, 13, 14, 15, 16) else left
            if col in (13, 15, 16):
                cell.number_format = "0.0"
            elif col == 14:
                cell.number_format = "0"
        if not line.get("fio") or not line.get("garage_number"):
            for col in range(1, last_col + 1):
                ws.cell(row=row, column=col).fill = warn_fill
        status_value = (line.get("status") or "").lower()
        status_cell = ws.cell(row=row, column=17)
        if status_value in ("выдан", "выполнен"):
            status_cell.fill = ok_fill
        elif status_value == "отменен":
            status_cell.fill = bad_fill
        else:
            status_cell.fill = neutral_fill

    if not lines:
        for col in range(1, last_col + 1):
            cell = ws.cell(row=data_start, column=col, value="")
            cell.border = thin
            cell.font = body_font
            cell.alignment = left

    table_ref = f"A{header_row}:{last_letter}{data_end}"
    ws.auto_filter.ref = table_ref
    ws.freeze_panes = f"A{data_start}"
    ws.print_title_rows = f"$1:${header_row}"

    widths = [24, 8, 8, 26, 10, 14, 14, 10, 10, 10, 10, 10, 9, 9, 12, 14, 12, 24]
    for col, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    for row in range(1, data_end + 1):
        ws.row_dimensions[row].height = 22
    ws.row_dimensions[2].height = 28
    ws.row_dimensions[header_row].height = 30

    cf_range = f"A{data_start}:{last_letter}{data_end}"
    ws.conditional_formatting.add(cf_range, FormulaRule(formula=[f'$Q{data_start}="отменен"'], fill=bad_fill))
    ws.conditional_formatting.add(cf_range, FormulaRule(formula=[f'OR($D{data_start}="",$F{data_start}="")'], fill=warn_fill))

    sign_row = data_end + 3
    _merge_value(ws, sign_row, 1, last_col, "Подписи ответственных лиц", fill=sub_fill, font=bold_font, alignment=left, border=thin)
    sign_labels = [
        ("Диспетчер", 1, 4),
        ("Начальник эксплуатации", 5, 9),
        ("Механик", 10, 13),
        ("Медработник", 14, 18),
    ]
    for label, col1, col2 in sign_labels:
        _merge_value(ws, sign_row + 1, col1, col2, f"{label}: __________________ /__________________/", font=body_font, alignment=left, border=thin)
    _merge_value(ws, sign_row + 3, 1, last_col, f"Файл сформирован: {now_label}", font=small_font, alignment=left)

    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_margins.left = 0.25
    ws.page_margins.right = 0.25
    ws.page_margins.top = 0.35
    ws.page_margins.bottom = 0.35
    ws.page_margins.header = 0.1
    ws.page_margins.footer = 0.1

    return _xlsx_download_response(wb, filename)

