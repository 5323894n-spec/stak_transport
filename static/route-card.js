/* Карточка маршрута: паспорт, трасса, перегоны, импорт и OSRM. */
"use strict";

const ROUTE_CARD_TABS = [
  ["passport", "Паспорт"], ["stops", "Остановки и направления"],
  ["map", "Схема трассы"], ["segments", "Перегоны и время"],
  ["history", "Импорт и история"],
];

function routeCardOpen(routeId) { location.hash = `#/routeCard/${+routeId}`; }

function routeCardState(routeId) {
  if (!window._routeCard || window._routeCard.routeId !== +routeId) {
    window._routeCard = {
      routeId: +routeId, tab: "passport", direction: "forward", network: null,
      drafts: {}, osrmPreview: null, importPreview: null, geometry: null,
    };
  }
  return window._routeCard;
}

function routeDirectionLabel(direction) {
  return direction === "forward" ? "Прямое направление" : "Обратное направление";
}

function routeCardDraft(state, direction = state.direction) {
  if (!state.drafts[direction]) {
    state.drafts[direction] = (state.network[direction] || []).map(row => ({
      id: row.id, stop_id: row.stop.id, stop: { ...row.stop }, sequence: row.sequence,
      distance_from_prev_km: +(row.distance_from_prev_km || 0),
      cumulative_km: +(row.cumulative_km || 0), run_time_sec: +(row.run_time_sec || 0),
      dwell_time_sec: +(row.dwell_time_sec || 0),
      distance_source: row.distance_source || "manual",
      boarding_allowed: row.boarding_allowed !== 0,
      alighting_allowed: row.alighting_allowed !== 0,
      is_timing_point: !!row.is_timing_point, source_detail: row.source_detail || "",
    }));
  }
  return state.drafts[direction];
}

async function routeCardReload(keepDrafts = false) {
  const state = window._routeCard;
  state.network = await api(`/api/routes/${state.routeId}/network`);
  if (!keepDrafts) state.drafts = {};
  renderRouteCard(state);
}

VIEWS.routeCard = async function routeCardView(routeId) {
  if (!routeId || !Number.isFinite(+routeId)) throw new Error("Маршрут не выбран");
  const state = routeCardState(routeId);
  state.network = await api(`/api/routes/${state.routeId}/network`);
  renderRouteCard(state);
};

function routeCardTab(tab) {
  const state = window._routeCard;
  state.tab = tab;
  renderRouteCard(state);
}

function routeCardDirection(direction) {
  const state = window._routeCard;
  state.direction = direction;
  state.osrmPreview = null;
  renderRouteCard(state);
}

function routeCardHeader(state) {
  const r = state.network.route, f = state.network.forward.length, b = state.network.backward.length;
  return `<header class="route-card-head"><div><button class="btn ghost" onclick="history.back()">← Назад</button>
    <h2>Маршрут № ${esc(r.number)} · ${esc(r.name || "без названия")}</h2>
    <p>${esc(r.work_days || "режим работы не указан")} · ${esc(r.season || "сезонность не указана")}</p></div>
    <div class="route-card-status"><span class="badge ${r.active === 0 ? "b-mut" : "b-ok"}">${r.active === 0 ? "неактивен" : "действует"}</span></div></header>
    <div class="cards route-kpis"><div class="card"><div class="num">${f}</div><div class="lbl">остановок прямо</div></div>
    <div class="card"><div class="num">${b}</div><div class="lbl">остановок обратно</div></div>
    <div class="card"><div class="num">${Number(state.network.totals.forward_km || 0).toLocaleString("ru-RU")}</div><div class="lbl">км прямо</div></div>
    <div class="card"><div class="num">${Number(state.network.totals.backward_km || 0).toLocaleString("ru-RU")}</div><div class="lbl">км обратно</div></div></div>`;
}

function routeCardPassport(state) {
  const r = state.network.route;
  return `<div class="route-passport"><section class="panel"><h3>Основные сведения</h3><dl class="kv">
    <dt>Номер</dt><dd>${esc(r.number)}</dd><dt>Наименование</dt><dd>${esc(r.name)}</dd>
    <dt>Тип перевозок</dt><dd>${esc(r.transport_type || r.route_type || "—")}</dd>
    <dt>Дни работы</dt><dd>${esc(r.work_days || "—")}</dd><dt>Сезонность</dt><dd>${esc(r.season || "—")}</dd>
    <dt>Допустимые автобусы</dt><dd>${esc(r.bus_types || "—")}</dd><dt>Версия</dt><dd>${esc(r.version || 1)}</dd></dl></section>
    <section class="panel"><h3>Плановые показатели</h3><dl class="kv">
    <dt>Интервал</dt><dd>${esc(r.interval_min || "—")} мин</dd><dt>Выходов</dt><dd>${esc(r.outputs_count || "—")}</dd>
    <dt>Время прямо</dt><dd>${esc(r.trip_time_min || "—")} мин</dd><dt>Время обратно</dt><dd>${esc(r.trip_time_back_min || "—")} мин</dd>
    <dt>Примечания</dt><dd>${esc(r.notes || "—")}</dd></dl></section></div>`;
}

function routeDirectionSwitch(state) {
  return `<div class="route-direction-switch"><button class="btn ${state.direction === "forward" ? "" : "sec"}" onclick="routeCardDirection('forward')">Прямо</button>
    <button class="btn ${state.direction === "backward" ? "" : "sec"}" onclick="routeCardDirection('backward')">Обратно</button></div>`;
}

function routeCardStops(state) {
  const rows = routeCardDraft(state).map((row, index) => `<div class="route-stop-row">
    <span class="route-stop-seq">${index + 1}</span><div><b>${esc(row.stop.name)}</b><div class="muted">${esc(row.stop.external_code || "без кода")} · ${esc(row.stop.address || "адрес не указан")}</div></div>
    <div class="route-stop-coords">${row.stop.latitude == null ? '<span class="badge b-warn">нет координат</span>' : `${Number(row.stop.latitude).toFixed(6)}, ${Number(row.stop.longitude).toFixed(6)}`}</div>
    <div class="route-stop-actions"><button class="btn small sec" onclick="routeCardMove(${index},-1)" ${index ? "" : "disabled"}>↑</button>
    <button class="btn small sec" onclick="routeCardMove(${index},1)" ${index + 1 < routeCardDraft(state).length ? "" : "disabled"}>↓</button>
    <button class="btn small sec" onclick="routeCardCoordinates(${index})">Координаты</button>
    <button class="btn small danger" onclick="routeCardRemoveStop(${index})">✕</button></div></div>`).join("");
  return `${routeDirectionSwitch(state)}<div class="route-card-toolbar"><b>${routeDirectionLabel(state.direction)}</b><span class="muted">Порядок можно менять стрелками</span>
    <button class="btn sec" onclick="routeCardAddStop()">+ Остановка</button><button class="btn" onclick="routeCardSaveTrace()">Сохранить трассу</button></div>
    <div class="route-stop-list">${rows || '<div class="route-empty">Остановки ещё не добавлены</div>'}</div>`;
}

function routeCardMove(index, delta) {
  const state = window._routeCard, rows = routeCardDraft(state), next = index + delta;
  if (next < 0 || next >= rows.length) return;
  [rows[index], rows[next]] = [rows[next], rows[index]];
  renderRouteCard(state);
}

function routeCardRemoveStop(index) {
  const state = window._routeCard;
  routeCardDraft(state).splice(index, 1);
  renderRouteCard(state);
}

async function routeCardAddStop() {
  const state = window._routeCard, data = await api("/api/stops?active=1");
  if (!data.items.length) { toast("Сначала создайте остановку в справочнике", true); return; }
  const value = await formModal("Добавить остановку", [{ k: "stop_id", label: "Остановка", type: "select", options: data.items.map(s => [s.id, `${s.name}${s.external_code ? " · " + s.external_code : ""}`]) }]);
  if (!value) return;
  const stop = data.items.find(s => s.id === +value.stop_id);
  routeCardDraft(state).push({ stop_id: stop.id, stop: { ...stop }, distance_from_prev_km: 0, run_time_sec: 0, dwell_time_sec: 0, distance_source: "manual", boarding_allowed: true, alighting_allowed: true, is_timing_point: false });
  renderRouteCard(state);
}

async function routeCardCoordinates(index) {
  const state = window._routeCard, row = routeCardDraft(state)[index];
  const value = await formModal("Координаты остановки", [
    { k: "latitude", label: "Широта", type: "number", step: "0.000001" },
    { k: "longitude", label: "Долгота", type: "number", step: "0.000001" },
  ], row.stop);
  if (!value) return;
  const latitude = value.latitude === "" ? null : +value.latitude, longitude = value.longitude === "" ? null : +value.longitude;
  await api(`/api/stops/${row.stop_id}`, { method: "PUT", body: { latitude, longitude } });
  row.stop.latitude = latitude; row.stop.longitude = longitude;
  toast("Координаты сохранены"); renderRouteCard(state);
}

function routeTracePayload(state) {
  return routeCardDraft(state).map((row, index) => ({
    stop_id: row.stop_id, sequence: index + 1,
    distance_from_prev_km: index ? +(row.distance_from_prev_km || 0) : 0,
    run_time_sec: +(row.run_time_sec || 0), dwell_time_sec: +(row.dwell_time_sec || 0),
    distance_source: row.distance_source || "manual", boarding_allowed: row.boarding_allowed,
    alighting_allowed: row.alighting_allowed, is_timing_point: row.is_timing_point,
    source_detail: row.source_detail || null,
  }));
}

async function routeCardSaveTrace() {
  const state = window._routeCard;
  await api(`/api/routes/${state.routeId}/stops/${state.direction}`, { method: "PUT", body: { items: routeTracePayload(state) } });
  toast("Трасса сохранена"); await routeCardReload();
}

function routeCardSegments(state) {
  const rows = routeCardDraft(state).map((row, index) => `<tr><td>${index + 1}</td><td>${esc(row.stop.name)}</td>
    <td><input type="number" min="0" step="0.001" value="${index ? esc(row.distance_from_prev_km) : 0}" ${index ? "" : "disabled"} onchange="routeCardSegment(${index},'distance_from_prev_km',this.value)"></td>
    <td><input type="number" min="0" step="1" value="${esc(row.run_time_sec)}" onchange="routeCardSegment(${index},'run_time_sec',this.value)"></td>
    <td><input type="number" min="0" step="1" value="${esc(row.dwell_time_sec)}" onchange="routeCardSegment(${index},'dwell_time_sec',this.value)"></td>
    <td>${esc(row.distance_source || "manual")}</td></tr>`).join("");
  return `${routeDirectionSwitch(state)}<div class="route-card-toolbar"><b>${routeDirectionLabel(state.direction)}</b><button class="btn" onclick="routeCardSaveTrace()">Сохранить перегоны</button></div>
    ${tbl(["№", "Остановка", "От предыдущей, км", "Ход, сек", "Стоянка, сек", "Источник"], rows || '<tr><td colspan="6">Нет остановок</td></tr>')}`;
}

function routeCardSegment(index, field, value) {
  routeCardDraft(window._routeCard)[index][field] = Math.max(0, +value || 0);
}

function routeMapPoints(state) {
  const rows = routeCardDraft(state).filter(row => row.stop.latitude != null && row.stop.longitude != null);
  if (!rows.length) return { rows: [], points: [] };
  const lats = rows.map(r => +r.stop.latitude), lons = rows.map(r => +r.stop.longitude);
  let minLat = Math.min(...lats), maxLat = Math.max(...lats), minLon = Math.min(...lons), maxLon = Math.max(...lons);
  if (minLat === maxLat) { minLat -= .01; maxLat += .01; }
  if (minLon === maxLon) { minLon -= .01; maxLon += .01; }
  state.mapBounds = { minLat, maxLat, minLon, maxLon };
  const points = rows.map(row => ({ row, x: 35 + ((+row.stop.longitude - minLon) / (maxLon - minLon)) * 730, y: 325 - ((+row.stop.latitude - minLat) / (maxLat - minLat)) * 280 }));
  return { rows, points };
}

function routeCardMap(state) {
  const plotted = routeMapPoints(state), missing = routeCardDraft(state).length - plotted.rows.length;
  const line = plotted.points.map(p => `${p.x},${p.y}`).join(" ");
  const nodes = plotted.points.map((p, i) => `<g class="route-map-node" data-route-stop-id="${p.row.stop_id}"><circle cx="${p.x}" cy="${p.y}" r="8"></circle><text x="${p.x + 11}" y="${p.y - 10}">${i + 1}. ${esc(p.row.stop.name)}</text></g>`).join("");
  const geo = state.geometry ? `<span class="badge b-inf">OSRM: ${esc(state.geometry.type || "геометрия получена")}</span>` : "";
  return `${routeDirectionSwitch(state)}<div class="route-card-toolbar"><div><b>Координатная схема</b><div class="muted">Схема без географической подложки. Маркер можно перетащить; новые координаты сохранятся после отпускания.</div></div>
    <button class="btn sec" onclick="routeCardOsrmPreview()">Рассчитать через OSRM</button></div>
    ${missing ? `<div class="vio w"><b>Не все остановки показаны</b>Без координат: ${missing}. Добавьте широту и долготу на вкладке остановок.</div>` : ""}
    <div class="route-map">${plotted.points.length ? `<svg viewBox="0 0 800 360" role="img" aria-label="Схема трассы"><polyline points="${line}"></polyline>${nodes}</svg>` : '<div class="route-empty">Для схемы добавьте координаты остановок</div>'}</div>
    <div class="route-map-legend">${geo}<span>● остановка</span><span>— последовательность движения</span></div>${routeOsrmDiff(state)}`;
}

function routeCardBindMap(state) {
  const svg = document.querySelector(".route-map svg");
  if (!svg || !state.mapBounds) return;
  let drag = null;
  const position = event => {
    const rect = svg.getBoundingClientRect();
    return {
      x: Math.max(35, Math.min(765, (event.clientX - rect.left) * 800 / rect.width)),
      y: Math.max(45, Math.min(325, (event.clientY - rect.top) * 360 / rect.height)),
    };
  };
  svg.addEventListener("pointerdown", event => {
    const node = event.target.closest("[data-route-stop-id]");
    if (!node) return;
    drag = { node, stopId: +node.dataset.routeStopId, pointerId: event.pointerId };
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener("pointermove", event => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const p = position(event), circle = drag.node.querySelector("circle"), text = drag.node.querySelector("text");
    circle.setAttribute("cx", p.x); circle.setAttribute("cy", p.y);
    text.setAttribute("x", p.x + 11); text.setAttribute("y", p.y - 10);
  });
  svg.addEventListener("pointerup", async event => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const p = position(event), bounds = state.mapBounds;
    const longitude = bounds.minLon + ((p.x - 35) / 730) * (bounds.maxLon - bounds.minLon);
    const latitude = bounds.minLat + ((325 - p.y) / 280) * (bounds.maxLat - bounds.minLat);
    const row = routeCardDraft(state).find(item => item.stop_id === drag.stopId);
    drag = null;
    try {
      await api(`/api/stops/${row.stop_id}`, { method: "PUT", body: { latitude, longitude } });
      row.stop.latitude = latitude; row.stop.longitude = longitude;
      toast("Координаты остановки сохранены"); renderRouteCard(state);
    } catch (error) { toast(error.message, true); renderRouteCard(state); }
  });
}

function routeDiffTable(diff) {
  const rows = (diff || []).map(item => `<tr><td>${esc(item.sequence)}</td><td>${esc(item.old_distance_km)}</td><td><b>${esc(item.new_distance_km)}</b></td><td>${esc(item.old_run_time_sec)}</td><td><b>${esc(item.new_run_time_sec)}</b></td></tr>`).join("");
  return `<div class="route-diff">${tbl(["Остановка №", "Было, км", "Станет, км", "Было, сек", "Станет, сек"], rows)}</div>`;
}

function routeOsrmDiff(state) {
  if (!state.osrmPreview) return "";
  return `<section class="panel"><h3>Предпросмотр OSRM — данные ещё не применены</h3>${routeDiffTable(state.osrmPreview.diff)}
    <div class="foot"><button class="btn sec" onclick="routeCardCancelOsrm()">Отменить</button><button class="btn" onclick="routeCardOsrmApply()">Применить расчёт</button></div></section>`;
}

async function routeCardOsrmPreview() {
  const state = window._routeCard;
  state.osrmPreview = await api(`/api/routes/${state.routeId}/osrm/preview/${state.direction}`, { method: "POST" });
  state.geometry = state.osrmPreview.geometry; renderRouteCard(state);
}

function routeCardCancelOsrm() { window._routeCard.osrmPreview = null; renderRouteCard(window._routeCard); }

async function routeCardOsrmApply() {
  const state = window._routeCard;
  await api(`/api/routes/${state.routeId}/osrm/apply/${state.direction}`, { method: "POST", body: { preview_token: state.osrmPreview.preview_token } });
  state.osrmPreview = null; toast("Расчёт OSRM применён"); await routeCardReload();
}

function routeCardHistory(state) {
  const preview = state.importPreview;
  const conflicts = preview ? (preview.conflicts || []).map(x => `<li>${esc(typeof x === "string" ? x : JSON.stringify(x))}</li>`).join("") : "";
  const rows = preview ? (preview.rows || []).map(x => `<tr><td>${esc(x.direction)}</td><td>${esc(x.sequence)}</td><td>${esc(x.name || x.stop_name)}</td><td>${esc(x.action || x.status)}</td></tr>`).join("") : "";
  return `<section class="panel"><h3>Импорт трассы из Excel или CSV</h3><p class="muted">Сначала формируется предпросмотр. База изменится только после отдельного подтверждения.</p>
    <label class="btn sec">Выбрать файл<input type="file" accept=".xlsx,.csv" hidden onchange="routeCardImportPreview(this)"></label></section>
    ${preview ? `<section class="panel"><h3>Предпросмотр импорта — данные ещё не применены</h3>
      ${conflicts ? `<div class="vio w"><b>Требуют внимания</b><ul>${conflicts}</ul></div>` : ""}
      <div class="route-diff">${tbl(["Направление", "№", "Остановка", "Изменение"], rows || '<tr><td colspan="4">Изменений нет</td></tr>')}</div>
      <div class="foot"><button class="btn sec" onclick="routeCardCancelImport()">Отменить</button><button class="btn" onclick="routeCardImportApply()" ${conflicts ? "disabled" : ""}>Применить импорт</button></div></section>` : ""}
    <section class="panel"><h3>Миграция старого списка остановок</h3><p class="muted">Заполняет нормализованную трассу из прежних текстовых полей. Повторный запуск безопасен.</p>
      <button class="btn sec" onclick="routeCardMigrate()">Проверить и перенести</button></section>`;
}

async function routeCardImportPreview(input) {
  const state = window._routeCard, file = input.files && input.files[0]; if (!file) return;
  const body = new FormData(); body.append("file", file);
  state.importPreview = await api(`/api/routes/${state.routeId}/network-import/preview`, { method: "POST", body });
  renderRouteCard(state);
}

function routeCardCancelImport() { window._routeCard.importPreview = null; renderRouteCard(window._routeCard); }

async function routeCardImportApply() {
  const state = window._routeCard;
  await api(`/api/routes/${state.routeId}/network-import/apply`, { method: "POST", body: { preview_token: state.importPreview.preview_token } });
  state.importPreview = null; toast("Импорт применён"); await routeCardReload();
}

async function routeCardMigrate() {
  const state = window._routeCard, result = await api(`/api/routes/${state.routeId}/migrate-network`, { method: "POST" });
  toast(result.status === "needs_review" ? "Нужна ручная проверка совпадений" : "Миграция выполнена", result.status === "needs_review");
  await routeCardReload();
}

function routeCardBody(state) {
  if (state.tab === "passport") return routeCardPassport(state);
  if (state.tab === "stops") return routeCardStops(state);
  if (state.tab === "map") return routeCardMap(state);
  if (state.tab === "segments") return routeCardSegments(state);
  return routeCardHistory(state);
}

function renderRouteCard(state) {
  const tabs = ROUTE_CARD_TABS.map(([key, label]) => `<button class="route-tab ${state.tab === key ? "on" : ""}" onclick="routeCardTab('${key}')">${esc(label)}</button>`).join("");
  $("content").innerHTML = `<div class="route-card">${routeCardHeader(state)}<nav class="route-tabs" aria-label="Разделы карточки маршрута">${tabs}</nav><div class="route-card-body">${routeCardBody(state)}</div></div>`;
  if (state.tab === "map") routeCardBindMap(state);
}
