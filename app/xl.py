# -*- coding: utf-8 -*-
"""Экспорт в Excel."""
import io
from urllib.parse import quote
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
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
