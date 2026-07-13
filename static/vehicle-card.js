/* Полноэкранное техническое досье автобуса. */
"use strict";

const VEHICLE_CARD_TABS = [
  ["overview", "Обзор"], ["repairs", "Ремонты"], ["parts", "Запчасти"],
  ["workers", "Исполнители"], ["incidents", "ДТП и повреждения"],
  ["maintenance", "ТО"], ["media", "Фотографии и документы"],
  ["costs", "Затраты"], ["timeline", "История"],
];

function openVehicleCard(busId) { location.hash = `#/vehicleCard/${+busId}`; }
function vehicleCardState(busId) {
  if (!window._vehicleCard || window._vehicleCard.busId !== +busId) {
    window._vehicleCard = { busId: +busId, tab: "overview", summary: null, tabs: {}, dateFrom: "", dateTo: "" };
  }
  return window._vehicleCard;
}
function vehicleCardClear(...tabs) {
  const state = window._vehicleCard;
  tabs.forEach(name => delete state.tabs[name]);
}
window.vehicleCardView = async function (busId) {
  if (!busId || !Number.isFinite(+busId)) throw new Error("Автобус не выбран");
  const state = vehicleCardState(busId);
  state.summary = await api(`/api/repairs/vehicles/${state.busId}/card`);
  renderVehicleCard(state);
};
async function vehicleCardTab(name) {
  const state = window._vehicleCard;
  state.tab = name;
  if (name !== "overview" && !state.tabs[name]) {
    const path = name === "incidents" ? `/api/repairs/vehicles/${state.busId}/incidents` :
      name === "media" ? `/api/repairs/vehicles/${state.busId}/media` :
      `/api/repairs/vehicles/${state.busId}/card/${name}`;
    state.tabs[name] = await api(path);
  }
  renderVehicleCard(state);
}
function vehicleMoney(value) { return Number(value || 0).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function vehicleEmpty(text) { return `<div class="vehicle-empty muted">${esc(text)}</div>`; }

function vehicleCardOverview(state) {
  const d = state.summary, v = d.vehicle, active = d.active_order, next = d.next_maintenance;
  const damages = (d.open_damages || []).map(x => `<li><b>${esc(x.area)}</b> — ${esc(x.description)} (${esc(x.severity)})</li>`).join("");
  return `<div class="vehicle-overview-grid"><section class="panel"><h3>Паспорт автобуса</h3><dl class="kv"><dt>Гаражный номер</dt><dd>${esc(v.garage_number)}</dd><dt>Госномер</dt><dd>${esc(v.plate)}</dd><dt>VIN</dt><dd>${esc(v.vin)}</dd><dt>Марка / модель</dt><dd>${esc(v.brand)} ${esc(v.model)}</dd><dt>Год</dt><dd>${esc(v.year)}</dd><dt>Пробег</dt><dd>${esc(v.odometer)} км</dd><dt>Статус</dt><dd>${stBadge(v.status || "")}</dd></dl></section>
    <section class="panel"><h3>Текущее состояние</h3>${active ? `<p><b>Активный ремонт:</b> ${esc(active.order_number)} · ${stBadge(active.status)}</p><p>${esc(active.diagnosis || "")}</p><p class="muted">Ответственный мастер: ${esc(active.responsible_master_name || "не назначен")}</p>` : `<p><span class="badge b-ok">Активного ремонта нет</span></p>`}${next ? `<p><b>Следующее ТО:</b> ${esc(next.name)}<br>${esc(next.next_date || "дата не задана")} · ${esc(next.next_odometer || "—")} км</p>` : `<p class="muted">План ТО не задан</p>`}</section></div>
    ${damages ? `<div class="vio w"><b>Неустранённые повреждения: ${d.totals.open_damages}</b><ul>${damages}</ul></div>` : ""}`;
}
function vehicleCardRepairs(data) {
  const rows = (data.items || []).map(r => `<tr><td><b>${esc(r.order_number)}</b><br><span class="muted">${esc(r.created_at || "")}</span></td><td>${esc(r.repair_type_name || "")}</td><td>${esc(r.fault_description || r.diagnosis || "")}</td><td>${esc(r.responsible_master_name || "")}</td><td>${vehicleMoney(r.total_cost)}</td><td>${stBadge(r.status || "")}</td><td><button class="btn small sec" onclick="vehicleCardRepairDetails(${r.id})">Состав ремонта</button></td></tr>`).join("");
  return tbl(["Заказ-наряд", "Вид", "Неисправность / диагноз", "Ответственный мастер", "Стоимость", "Статус", "Подробно"], rows || `<tr><td colspan="7">Ремонтов пока нет</td></tr>`);
}
function vehicleCardParts(data) {
  const rows = (data.items || []).map(r => `<tr><td>${esc(r.order_number)}</td><td><b>${esc(r.code)}</b> ${esc(r.name)}</td><td>${esc(r.requested_qty)}</td><td>${esc(r.issued_qty)}</td><td>${esc(r.installed_qty)}</td><td>${vehicleMoney(r.unit_price)}</td><td>${vehicleMoney(r.line_cost)}</td><td>${esc(r.status)}</td></tr>`).join("");
  return tbl(["Заказ-наряд", "Запчасть", "Запрошено", "Выдано", "Установлено", "Цена", "Списано на сумму", "Статус"], rows || `<tr><td colspan="8">Списаний запчастей нет</td></tr>`);
}
function vehicleCardWorkers(data) {
  const rows = (data.items || []).map(r => `<tr><td>${esc(r.order_number)}</td><td><b>${esc(r.full_name || r.username)}</b></td><td>${esc(r.role)}</td><td>${esc(r.status)}</td><td>${esc(r.actual_hours)}</td><td>${vehicleMoney(r.hourly_rate)}</td><td>${vehicleMoney(r.labor_cost)}</td></tr>`).join("");
  return tbl(["Заказ-наряд", "Исполнитель", "Роль", "Статус", "Часы", "Ставка", "Стоимость работ"], rows || `<tr><td colspan="7">Исполнители не назначались</td></tr>`);
}
function vehicleCardIncidents(data) {
  const items = (data.items || []).map(i => `<article class="incident-card"><header><div><b>${esc(i.incident_type)}</b> · ${esc(i.occurred_at)} · ${esc(i.place || "место не указано")}</div>${stBadge(i.status || "")}</header><p>${esc(i.circumstances)}</p><div class="muted">Ответственный: ${esc(i.responsible_name || "не назначен")} · ущерб: ${vehicleMoney(i.actual_damage_cost || i.estimated_damage_cost)} · заказ-наряд: ${esc(i.repair_order_number || "—")}</div><div class="damage-list">${(i.damages || []).map(x => `<div class="damage ${x.resolved ? "resolved" : ""}"><span><b>${esc(x.area)}</b> — ${esc(x.description)} · ${esc(x.severity)}</span>${x.resolved ? `<span class="badge b-ok">устранено</span>` : `<button class="btn small sec" onclick="vehicleDamageResolve(${x.id})">Отметить устранённым</button>`}</div>`).join("") || `<span class="muted">Повреждения не добавлены</span>`}</div><div class="incident-actions"><button class="btn small sec" onclick="vehicleCardUpload(${i.id})">Добавить фото</button><button class="btn small danger" onclick="vehicleIncidentCancel(${i.id})">Отменить событие</button></div></article>`).join("");
  return items || vehicleEmpty("ДТП и повреждения не зарегистрированы");
}
function vehicleCardMaintenance(data) {
  const plans = (data.plans || []).map(r => `<tr><td>${esc(r.name)}</td><td>${esc(r.repair_type_name)}</td><td>${esc(r.last_date || "")}</td><td>${esc(r.next_date || "")}</td><td>${esc(r.next_odometer || "")}</td><td>${r.active ? `<span class="badge b-ok">активен</span>` : `<span class="badge b-mut">закрыт</span>`}</td></tr>`).join("");
  const events = (data.events || []).map(r => `<tr><td>${esc(r.due_date || r.completed_at || "")}</td><td>${esc(r.plan_name)}</td><td>${stBadge(r.status || "")}</td><td>${esc(r.request_number || "")}</td><td>${esc(r.order_number || "")}</td></tr>`).join("");
  return `<h3>Планы ТО</h3>${tbl(["План", "Вид", "Последнее", "Следующее", "Пробег", "Состояние"], plans || `<tr><td colspan="6">Планы не созданы</td></tr>`)}<h3>События ТО</h3>${tbl(["Дата", "План", "Статус", "Заявка", "Заказ-наряд"], events || `<tr><td colspan="5">Событий нет</td></tr>`)}`;
}
function vehicleCardMedia(data) {
  const items = (data.items || []).map(m => {
    const url = `/api/repairs/attachments/${m.id}/download?token=${encodeURIComponent(TOKEN)}`;
    const image = String(m.mime_type || "").startsWith("image/");
    return `<figure class="vehicle-photo ${m.is_cover ? "cover" : ""}">${image ? `<a href="${url}" target="_blank"><img src="${url}" alt="${esc(m.caption || m.original_name)}"></a>` : `<a class="vehicle-document" href="${url}" target="_blank">📄 ${esc(m.original_name)}</a>`}<figcaption><b>${esc(m.category || "Без категории")}</b><br>${esc(m.caption || "")}<br><span class="muted">${esc(m.captured_at || m.uploaded_at || "")}</span></figcaption><div>${image && !m.is_cover ? `<button class="btn small sec" onclick="vehicleMediaCover(${m.id})">На обложку</button>` : m.is_cover ? `<span class="badge b-ok">Обложка</span>` : ""} <button class="btn small danger" onclick="vehicleMediaCancel(${m.id})">Отменить</button></div></figure>`;
  }).join("");
  return `<div class="vehicle-gallery">${items || vehicleEmpty("Фотографии и документы ещё не добавлены")}</div>`;
}
function vehicleCardCosts(data) {
  const t = data.totals || {}, rows = (data.monthly || []).map(r => `<tr><td>${esc(r.month)}</td><td>${vehicleMoney(r.labor)}</td><td>${vehicleMoney(r.parts)}</td><td>${vehicleMoney(r.external)}</td><td>${vehicleMoney(r.other)}</td><td><b>${vehicleMoney(r.total)}</b></td></tr>`).join("");
  return `<div class="cards"><div class="card"><div class="num">${vehicleMoney(t.labor)}</div><div class="lbl">работы</div></div><div class="card"><div class="num">${vehicleMoney(t.parts)}</div><div class="lbl">запчасти</div></div><div class="card"><div class="num">${vehicleMoney(t.external)}</div><div class="lbl">внешние услуги</div></div><div class="card"><div class="num">${vehicleMoney(t.other)}</div><div class="lbl">прочие</div></div><div class="card"><div class="num">${vehicleMoney(t.total)}</div><div class="lbl">всего</div></div></div>${tbl(["Месяц", "Работы", "Запчасти", "Внешние", "Прочие", "Итого"], rows || `<tr><td colspan="6">Затрат нет</td></tr>`)}`;
}
function vehicleCardTimeline(data) {
  return `<div class="vehicle-timeline">${(data.items || []).map(x => `<div class="vehicle-timeline-item"><time>${esc(x.event_at || "")}</time><div><b>${esc(x.event_type)} · ${esc(x.title)}</b> ${x.status ? stBadge(x.status) : ""}<p>${esc(x.description || "")}</p>${x.amount ? `<span class="muted">Сумма: ${vehicleMoney(x.amount)}</span>` : ""}</div></div>`).join("") || vehicleEmpty("История автобуса пока пуста")}</div>`;
}
function vehicleCardTabBody(state) {
  const data = state.tabs[state.tab] || {};
  if (state.tab === "overview") return vehicleCardOverview(state);
  if (state.tab === "repairs") return vehicleCardRepairs(data);
  if (state.tab === "parts") return vehicleCardParts(data);
  if (state.tab === "workers") return vehicleCardWorkers(data);
  if (state.tab === "incidents") return vehicleCardIncidents(data);
  if (state.tab === "maintenance") return vehicleCardMaintenance(data);
  if (state.tab === "media") return vehicleCardMedia(data);
  if (state.tab === "costs") return vehicleCardCosts(data);
  return vehicleCardTimeline(data);
}
function renderVehicleCard(state) {
  const d = state.summary, v = d.vehicle, cover = d.cover;
  const coverUrl = cover ? `/api/repairs/attachments/${cover.id}/download?token=${encodeURIComponent(TOKEN)}` : "";
  const tabs = VEHICLE_CARD_TABS.map(([key, label]) => `<button class="vehicle-tab ${state.tab === key ? "on" : ""}" onclick="vehicleCardTab('${key}')">${esc(label)}</button>`).join("");
  $("content").innerHTML = `<div class="vehicle-card"><div class="vehicle-card-head">${coverUrl ? `<img class="vehicle-cover" src="${coverUrl}" alt="Обложка автобуса">` : `<div class="vehicle-cover placeholder">Нет фото</div>`}<div class="vehicle-identity"><button class="btn ghost" onclick="history.back()">← Назад</button><h2>${esc(v.garage_number)} · ${esc(v.plate || "без госномера")}</h2><p>${esc(v.brand)} ${esc(v.model)} · VIN ${esc(v.vin || "—")} · ${esc(v.odometer || 0)} км</p><div>${stBadge(v.status || "")}</div></div><div class="vehicle-actions"><button class="btn" onclick="vehicleCardIncident()">+ ДТП / повреждение</button><button class="btn sec" onclick="vehicleCardUpload()">+ Фото / документ</button><button class="btn sec" onclick="vehicleCardPrint()">Печать / PDF</button><button class="btn sec" onclick="vehicleCardExcel()">Excel</button></div></div><div class="cards vehicle-kpis"><div class="card"><div class="num">${esc(d.totals.repairs)}</div><div class="lbl">ремонтов</div></div><div class="card"><div class="num">${vehicleMoney(d.totals.cost)}</div><div class="lbl">общие затраты</div></div><div class="card"><div class="num">${vehicleMoney(d.totals.downtime_hours)}</div><div class="lbl">часов простоя</div></div><div class="card ${d.totals.incidents ? "warn" : "ok"}"><div class="num">${esc(d.totals.incidents)}</div><div class="lbl">ДТП и событий</div></div><div class="card ${d.totals.open_damages ? "err" : "ok"}"><div class="num">${esc(d.totals.open_damages)}</div><div class="lbl">неустранённых повреждений</div></div></div><div class="vehicle-report-period"><label>Отчёт с <input type="date" value="${esc(state.dateFrom)}" onchange="_vehicleCard.dateFrom=this.value"></label><label>по <input type="date" value="${esc(state.dateTo)}" onchange="_vehicleCard.dateTo=this.value"></label></div><nav class="vehicle-tabs" aria-label="Разделы карточки">${tabs}</nav><div class="vehicle-card-body">${vehicleCardTabBody(state)}</div></div>`;
}

async function vehicleCardRepairDetails(orderId) {
  const state = window._vehicleCard;
  for (const tab of ["operations", "parts", "workers"]) if (!state.tabs[tab]) state.tabs[tab] = await api(`/api/repairs/vehicles/${state.busId}/card/${tab}`);
  const operations = (state.tabs.operations.items || []).filter(x => x.order_id === orderId).map(x => `<tr><td>${esc(x.sequence_no)}</td><td>${esc(x.name)}</td><td>${esc(x.status)}</td><td>${esc(x.actual_hours)}</td><td>${esc(x.result || "")}</td></tr>`).join("");
  const parts = (state.tabs.parts.items || []).filter(x => x.order_id === orderId).map(x => `<tr><td>${esc(x.code)} ${esc(x.name)}</td><td>${esc(x.installed_qty)}</td><td>${vehicleMoney(x.line_cost)}</td></tr>`).join("");
  const workers = (state.tabs.workers.items || []).filter(x => x.order_id === orderId).map(x => `<tr><td>${esc(x.full_name)}</td><td>${esc(x.role)}</td><td>${esc(x.actual_hours)}</td></tr>`).join("");
  await modal(`<h3>Состав ремонта</h3><h4>Операции</h4>${tbl(["№", "Операция", "Статус", "Часы", "Результат"], operations || `<tr><td colspan="5">Нет данных</td></tr>`)}<h4>Запчасти</h4>${tbl(["Запчасть", "Установлено", "Сумма"], parts || `<tr><td colspan="3">Нет данных</td></tr>`)}<h4>Исполнители</h4>${tbl(["Исполнитель", "Роль", "Часы"], workers || `<tr><td colspan="3">Нет данных</td></tr>`)}<div class="foot"><button class="btn sec" data-act="cancel">Закрыть</button></div>`);
}
async function vehicleCardIncident() {
  const state = window._vehicleCard;
  const bg = document.createElement("div"); bg.className = "modal-bg";
  bg.innerHTML = `<div class="modal vehicle-incident-modal"><h3>Новое ДТП / повреждение</h3><div class="cols"><label class="f">Тип<select data-incident="incident_type"><option>ДТП</option><option>повреждение</option><option>вандализм</option><option>страховой случай</option></select></label><label class="f">Дата и время<input data-incident="occurred_at" type="datetime-local" value="${today()}T12:00"></label><label class="f">Место<input data-incident="place"></label><label class="f">Предварительный ущерб<input data-incident="estimated_damage_cost" type="number" min="0" value="0"></label><label class="f f-wide">Обстоятельства<textarea data-incident="circumstances" rows="3"></textarea></label><label class="f"><input data-incident="create_repair_request" type="checkbox" checked> Создать заявку на ремонт</label></div><div class="assign-head"><h4>Повреждения</h4><button class="btn small sec" type="button" data-add-damage>+ Повреждение</button></div><div data-damages></div><div class="vio" data-error style="display:none"></div><div class="foot"><button class="btn sec" data-cancel>Отмена</button><button class="btn" data-save>Зарегистрировать</button></div></div>`;
  document.body.appendChild(bg);
  const list = bg.querySelector("[data-damages]");
  const addDamage = () => list.insertAdjacentHTML("beforeend", `<div class="damage-editor"><input placeholder="Зона" data-d="area"><input placeholder="Описание" data-d="description"><select data-d="severity"><option>незначительная</option><option selected>средняя</option><option>тяжёлая</option><option>критическая</option></select><label><input type="checkbox" data-d="repair_required" checked> нужен ремонт</label><button type="button" class="btn small danger" data-remove>✕</button></div>`);
  addDamage(); bg.querySelector("[data-add-damage]").onclick = addDamage;
  bg.onclick = e => { if (e.target.matches("[data-remove]")) e.target.closest(".damage-editor").remove(); if (e.target.matches("[data-cancel]")) bg.remove(); };
  bg.querySelector("[data-save]").onclick = async () => {
    const value = key => bg.querySelector(`[data-incident="${key}"]`);
    const payload = { incident_type: value("incident_type").value, occurred_at: value("occurred_at").value, place: value("place").value, circumstances: value("circumstances").value, estimated_damage_cost: +(value("estimated_damage_cost").value || 0), create_repair_request: value("create_repair_request").checked, damages: [...list.querySelectorAll(".damage-editor")].map(row => ({ area: row.querySelector('[data-d="area"]').value, description: row.querySelector('[data-d="description"]').value, severity: row.querySelector('[data-d="severity"]').value, repair_required: row.querySelector('[data-d="repair_required"]').checked })) };
    try { await api(`/api/repairs/vehicles/${state.busId}/incidents`, { method: "POST", body: payload }); bg.remove(); vehicleCardClear("incidents", "timeline"); state.summary = await api(`/api/repairs/vehicles/${state.busId}/card`); await vehicleCardTab("incidents"); toast("Событие зарегистрировано"); }
    catch (e) { const box = bg.querySelector("[data-error]"); box.style.display = "block"; box.textContent = e.message; }
  };
}
async function vehicleCardUpload(incidentId = 0) {
  const state = window._vehicleCard, bg = document.createElement("div"); bg.className = "modal-bg";
  bg.innerHTML = `<div class="modal"><h3>Фото или документ автобуса</h3><label class="f">Файл<input data-file type="file" accept="image/jpeg,image/png,application/pdf,.docx,.xlsx"></label><div class="cols"><label class="f">Категория<select data-field="category"><option>общий вид</option><option>до ремонта</option><option>после ремонта</option><option>ДТП</option><option>повреждение</option><option>документ</option></select></label><label class="f">Дата съёмки<input data-field="captured_at" type="date" value="${today()}"></label><label class="f f-wide">Подпись<input data-field="caption"></label><label class="f">Событие ID<input data-field="incident_id" type="number" value="${incidentId || ""}"></label><label class="f">Заказ-наряд ID<input data-field="order_id" type="number"></label></div><div class="vio" data-error style="display:none"></div><div class="foot"><button class="btn sec" data-cancel>Отмена</button><button class="btn" data-save>Загрузить</button></div></div>`;
  document.body.appendChild(bg); bg.querySelector("[data-cancel]").onclick = () => bg.remove();
  bg.querySelector("[data-save]").onclick = async () => { const file = bg.querySelector("[data-file]").files[0]; if (!file) { toast("Выберите файл", true); return; } const body = new FormData(); body.append("file", file); bg.querySelectorAll("[data-field]").forEach(x => { if (x.value) body.append(x.dataset.field, x.value); }); try { await api(`/api/repairs/vehicles/${state.busId}/media`, { method: "POST", body }); bg.remove(); vehicleCardClear("media", "timeline"); state.summary = await api(`/api/repairs/vehicles/${state.busId}/card`); await vehicleCardTab("media"); toast("Файл добавлен"); } catch (e) { const box = bg.querySelector("[data-error]"); box.style.display = "block"; box.textContent = e.message; } };
}
async function vehicleCardReload(tab) { const state = window._vehicleCard; vehicleCardClear(tab); state.summary = await api(`/api/repairs/vehicles/${state.busId}/card`); await vehicleCardTab(tab); }
async function vehicleDamageResolve(id) { await api(`/api/repairs/damages/${id}`, { method: "PATCH", body: { resolved: true } }); await vehicleCardReload("incidents"); }
async function vehicleIncidentCancel(id) { const reason = await textModal("Отмена события", "Причина отмены"); if (!reason) return; await api(`/api/repairs/incidents/${id}/cancel`, { method: "POST", body: { reason } }); vehicleCardClear("timeline"); await vehicleCardReload("incidents"); }
async function vehicleMediaCover(id) { await api(`/api/repairs/media/${id}/cover`, { method: "POST" }); await vehicleCardReload("media"); }
async function vehicleMediaCancel(id) { const reason = await textModal("Отмена файла", "Причина отмены"); if (!reason) return; await api(`/api/repairs/media/${id}/cancel`, { method: "POST", body: { reason } }); vehicleCardClear("timeline"); await vehicleCardReload("media"); }
function vehicleCardReportPath(suffix) { const state = window._vehicleCard, q = new URLSearchParams(); if (state.dateFrom) q.set("date_from", state.dateFrom); if (state.dateTo) q.set("date_to", state.dateTo); return `/api/repairs/vehicles/${state.busId}/${suffix}${q.toString() ? "?" + q : ""}`; }
function vehicleCardPrint() { openWin(vehicleCardReportPath("print")); }
function vehicleCardExcel() { openWin(vehicleCardReportPath("export.xlsx")); }
