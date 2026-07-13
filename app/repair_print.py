# -*- coding: utf-8 -*-
"""Печатные формы заказ-нарядов ремонта."""
import html
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from . import db
from .auth import current_user

router = APIRouter(prefix="/api/repairs", tags=["repair-print"])
e = lambda value: html.escape(str(value or ""))

def table(headers, rows):
    head = "".join(f"<th>{e(x)}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{e(x)}</td>" for x in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body or '<tr><td colspan=9>Нет данных</td></tr>'}</tbody></table>"

@router.get("/orders/{order_id}/print", response_class=HTMLResponse)
def print_order(order_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        order = db.one(con.execute(
            "SELECT ro.*,ro.number order_number,rr.number request_number,rr.description fault_description,b.garage_number,b.plate,b.brand,b.model,b.vin,rt.name repair_type_name,u.full_name master_name "
            "FROM repair_orders ro LEFT JOIN repair_requests rr ON rr.id=ro.request_id JOIN buses b ON b.id=ro.bus_id LEFT JOIN repair_types rt ON rt.id=ro.repair_type_id LEFT JOIN users u ON u.id=ro.responsible_master_id WHERE ro.id=?", (order_id,)))
        if not order: raise HTTPException(404, "Заказ-наряд не найден")
        operations = db.rows(con.execute("SELECT sequence_no,name,status,norm_hours,actual_hours,result FROM repair_operations WHERE order_id=? ORDER BY sequence_no,id", (order_id,)))
        workers = db.rows(con.execute("SELECT u.full_name,rw.role,rw.status,rw.planned_hours,rw.actual_hours,rw.hourly_rate FROM repair_order_workers rw JOIN users u ON u.id=rw.worker_id WHERE rw.order_id=? ORDER BY rw.id", (order_id,)))
        parts = db.rows(con.execute("SELECT p.code,p.name,p.unit,rp.issued_qty,rp.installed_qty,rp.unit_price FROM repair_parts rp JOIN parts p ON p.id=rp.part_id WHERE rp.order_id=? ORDER BY rp.id", (order_id,)))
        org = db.get_settings(con).get("org_name") or "Автотранспортное предприятие"
    finally: con.close()
    operations_table = table(["№","Операция","Статус","Норма, ч","Факт, ч","Результат"], ([x[k] for k in x] for x in operations))
    workers_table = table(["Исполнитель","Роль","Статус","План, ч","Факт, ч","Ставка"], ([x[k] for k in x] for x in workers))
    parts_table = table(["Код","Запчасть","Ед.","Выдано","Установлено","Цена"], ([x[k] for k in x] for x in parts))
    return f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>{e(order['order_number'])}</title><style>
    @page{{size:A4;margin:12mm}}body{{font:12px Arial;color:#111}}h1{{font-size:18px;text-align:center}}h2{{font-size:14px;margin:14px 0 6px}}.meta{{display:grid;grid-template-columns:1fr 1fr;gap:4px 18px}}table{{width:100%;border-collapse:collapse;font-size:10px}}th,td{{border:1px solid #555;padding:4px;vertical-align:top}}th{{background:#e7eef6}}.totals{{margin-top:12px;text-align:right}}.sign{{display:flex;justify-content:space-between;margin-top:30px}}@media print{{button{{display:none}}}}</style></head><body>
    <button onclick='print()'>Печать</button><div>{e(org)}</div><h1>ЗАКАЗ-НАРЯД НА РЕМОНТ № {e(order['order_number'])}</h1>
    <div class='meta'><div><b>Заявка:</b> {e(order['request_number'])}</div><div><b>Статус:</b> {e(order['status'])}</div><div><b>Автобус:</b> {e(order['garage_number'])} {e(order['plate'])}</div><div><b>Марка/модель:</b> {e(order['brand'])} {e(order['model'])}</div><div><b>VIN:</b> {e(order['vin'])}</div><div><b>Пробег:</b> {e(order['odometer_in'])}</div><div><b>Вид ремонта:</b> {e(order['repair_type_name'])}</div><div><b>Ответственный мастер:</b> {e(order['master_name'])}</div></div>
    <p><b>Неисправность:</b> {e(order['fault_description'])}</p><p><b>Диагноз:</b> {e(order['diagnosis'])}</p>
    <h2>Операции</h2>{operations_table}<h2>Исполнители</h2>{workers_table}<h2>Запчасти</h2>{parts_table}
    <div class='totals'><b>Работы:</b> {e(order['labor_cost'])} &nbsp; <b>Запчасти:</b> {e(order['parts_cost'])} &nbsp; <b>Итого:</b> {e(order['total_cost'])}</div>
    <p><b>Результат ремонта:</b> {e(order['result'])}</p><div class='sign'><span>Подпись мастера __________________</span><span>Контроль __________________</span><span>Автобус принял __________________</span></div></body></html>"""
