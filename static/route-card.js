/* Карточка маршрута: паспорт, трасса, перегоны, импорт и OSRM. */
"use strict";

const ROUTE_CARD_TABS = [
  ["passport", "Паспорт"], ["stops", "Остановки и направления"],
  ["map", "Схема трассы"], ["segments", "Перегоны и время"],
  ["periods", "Периоды дня"],
  ["history", "Импорт и история"],
];

function routeCardOpen(routeId) { location.hash = `#/routeCard/${+routeId}`; }

function routeCardState(routeId) {
  if (!window._routeCard || window._routeCard.routeId !== +routeId) {
    window._routeCard = {
      routeId: +routeId, tab: "passport", direction: "forward", network: null,
      drafts: {}, osrmPreview: null, importPreview: null, geometry: null,
      periodDay: "будни", periodDrafts: {}, periodTemplates: [],
      periodTemplateId: "", periodTemplatePreview: null,
      periodCalcPreview: null, periodError: "", periodContinuous: false,
      periodsLoading: false,
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
  if (state.tab === "periods") await routeCardLoadPeriods();
};

async function routeCardTab(tab) {
  const state = window._routeCard;
  state.tab = tab;
  renderRouteCard(state);
  if (tab === "periods" && !state.periodDrafts[state.periodDay]) {
    await routeCardLoadPeriods();
  }
}

function routeCardDirection(direction) {
  const state = window._routeCard;
  state.direction = direction;
  state.osrmPreview = null;
  state.geometry = null;
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

function routeCardFallbackMap(state, plotted = routeMapPoints(state)) {
  const line = plotted.points.map(p => `${p.x},${p.y}`).join(" ");
  const nodes = plotted.points.map((p, i) => `<g class="route-map-node" data-route-stop-id="${p.row.stop_id}"><circle cx="${p.x}" cy="${p.y}" r="8"></circle><text x="${p.x + 11}" y="${p.y - 10}">${i + 1}. ${esc(p.row.stop.name)}</text></g>`).join("");
  return plotted.points.length ? `<svg viewBox="0 0 800 360" role="img" aria-label="Схема трассы без картографической подложки"><polyline points="${line}"></polyline>${nodes}</svg>` : '<div class="route-empty">Для схемы добавьте координаты остановок</div>';
}

function routeCardMap(state) {
  const plotted = routeMapPoints(state), missing = routeCardDraft(state).length - plotted.rows.length;
  const geo = state.geometry ? `<span class="badge b-inf">OSRM: ${esc(state.geometry.type || "геометрия получена")}</span>` : "";
  return `${routeDirectionSwitch(state)}<div class="route-card-toolbar"><div><b>Координатная схема</b><div class="muted">Схема без географической подложки. Маркер можно перетащить; новые координаты сохранятся после отпускания.</div></div>
    <button class="btn sec" onclick="routeCardOsrmPreview()">Рассчитать через OSRM</button></div>
    ${missing ? `<div class="vio w"><b>Не все остановки показаны</b>Без координат: ${missing}. Добавьте широту и долготу на вкладке остановок.</div>` : ""}
    <div class="vio w route-map-warning" role="status" aria-live="polite" hidden>Подложка OpenStreetMap недоступна</div>
    <div class="route-map"><div class="route-map-canvas" hidden></div><div class="route-map-fallback">${routeCardFallbackMap(state, plotted)}</div></div>
    <div class="route-map-legend">${geo}<span>● остановка</span><span>— последовательность движения</span></div>${routeOsrmDiff(state)}`;
}

let routeMapInstance = null;
let routeMapTileTimer = null;

function routeCardDestroyMap() {
  if (routeMapTileTimer) clearTimeout(routeMapTileTimer);
  routeMapTileTimer = null;
  if (routeMapInstance) routeMapInstance.remove();
  routeMapInstance = null;
}

function routeCardGeometryPoints(state, rows) {
  const coordinates = state.geometry && state.geometry.coordinates;
  if (Array.isArray(coordinates) && coordinates.length && coordinates.every(point =>
    Array.isArray(point) && point.length >= 2 && Number.isFinite(+point[0]) && Number.isFinite(+point[1]))) {
    return coordinates.map(point => [+point[1], +point[0]]);
  }
  return rows.map(row => [+row.stop.latitude, +row.stop.longitude]);
}

function routeCardShowMapFallback(message = "Подложка OpenStreetMap недоступна") {
  const canvas = document.querySelector(".route-map-canvas");
  const fallback = document.querySelector(".route-map-fallback");
  const warning = document.querySelector(".route-map-warning");
  if (canvas) canvas.hidden = true;
  if (fallback) fallback.hidden = false;
  if (warning) { warning.textContent = message; warning.hidden = false; }
}

function routeCardBindFallbackDrag(state) {
  const svg = document.querySelector(".route-map-fallback svg");
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

function routeCardBindMap(state) {
  const rows = routeCardDraft(state).filter(row => row.stop.latitude != null && row.stop.longitude != null);
  const canvas = document.querySelector(".route-map-canvas");
  const fallback = document.querySelector(".route-map-fallback");
  if (!rows.length || !canvas || !window.L) {
    routeCardShowMapFallback();
    routeCardBindFallbackDrag(state);
    return;
  }
  try {
    canvas.hidden = false;
    if (fallback) fallback.hidden = true;
    const map = window.L.map(canvas);
    routeMapInstance = map;
    let tileLoads = 0, tileErrors = 0;
    const tileLayer = window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    tileLayer.on("tileload", () => {
      tileLoads += 1;
      if (routeMapInstance !== map) return;
      if (routeMapTileTimer) clearTimeout(routeMapTileTimer);
      routeMapTileTimer = null;
    });
    tileLayer.on("tileerror", () => { tileErrors += 1; });
    const line = routeCardGeometryPoints(state, rows);
    if (line.length > 1) {
      window.L.polyline(line, { color: "white", weight: 10 }).addTo(map);
      window.L.polyline(line, { color: "#2563eb", weight: 6 }).addTo(map);
    }
    rows.forEach((row, index) => {
      let committed = [+row.stop.latitude, +row.stop.longitude];
      let saving = false;
      const endpointClass = index === 0 ? " route-map-marker-start" : index === rows.length - 1 ? " route-map-marker-end" : "";
      const icon = window.L.divIcon({ className: `route-map-marker${endpointClass}`, html: `<span>${index + 1}</span>` });
      const marker = window.L.marker(committed, { icon, draggable: true }).addTo(map);
      marker.bindTooltip(`${index + 1}. ${esc(row.stop.name)}`);
      marker.on("dragend", async () => {
        if (saving) { marker.setLatLng(committed); return; }
        saving = true;
        marker.dragging.disable();
        const point = marker.getLatLng(), latitude = point.lat, longitude = point.lng;
        try {
          await api(`/api/stops/${row.stop_id}`, { method: "PUT", body: { latitude, longitude } });
          row.stop.latitude = latitude; row.stop.longitude = longitude;
          committed = [latitude, longitude];
          toast("Координаты остановки сохранены");
        } catch (error) {
          marker.setLatLng(committed);
          toast(error.message, true);
        } finally {
          saving = false;
          marker.dragging.enable();
        }
      });
    });
    if (rows.length === 1) map.setView(line[0], 15);
    else map.fitBounds(line, { padding: [36, 36], maxZoom: 17 });
    routeMapTileTimer = setTimeout(() => {
      if (!tileLoads && routeMapInstance === map) {
        routeCardDestroyMap();
        routeCardShowMapFallback();
        routeCardBindFallbackDrag(state);
      }
    }, 8000);
  } catch (error) {
    routeCardDestroyMap();
    routeCardShowMapFallback();
    routeCardBindFallbackDrag(state);
  }
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

function routeCardCancelOsrm() {
  window._routeCard.osrmPreview = null;
  window._routeCard.geometry = null;
  renderRouteCard(window._routeCard);
}

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

function routePeriodTime(value) {
  const minute = Math.max(0, +(value || 0));
  return `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
}

function routeCardPeriodRows(state = window._routeCard) {
  return state.periodDrafts[state.periodDay] || [];
}

async function routeCardLoadPeriods() {
  const state = window._routeCard;
  state.periodsLoading = true; state.periodError = ""; renderRouteCard(state);
  try {
    const [periods, templates] = await Promise.all([
      api(`/api/routes/${state.routeId}/periods/${state.periodDay}`),
      api("/api/period-templates"),
    ]);
    state.periodDrafts[state.periodDay] = (periods.items || []).map((row, index) => ({
      name: row.name || `Период ${index + 1}`,
      start: routePeriodTime(row.start_min), end: routePeriodTime(row.end_min),
      interval_min: row.interval_min, travel_time_factor: row.travel_time_factor || 1,
      transition_mode: row.transition_mode || "abrupt",
      transition_window_min: row.transition_window_min || 0,
      color: row.color || "#3b82f6", priority: row.priority == null ? index : row.priority,
    }));
    state.periodTemplates = templates.items || [];
  } catch (error) { state.periodError = error.message; }
  state.periodsLoading = false; renderRouteCard(state);
}

async function routeCardPeriodDay(value) {
  const state = window._routeCard;
  state.periodDay = value; state.periodTemplatePreview = null;
  state.periodCalcPreview = null; state.periodError = "";
  if (state.periodDrafts[value]) renderRouteCard(state); else await routeCardLoadPeriods();
}

function routeCardPeriodChange(index, field, value) {
  routeCardPeriodRows()[index][field] = value;
  window._routeCard.periodError = "";
}

function routeCardPeriodAdd() {
  const rows = routeCardPeriodRows(), previous = rows[rows.length - 1];
  const start = previous ? previous.end : "06:00";
  const hour = Math.min(47, +(start.split(":")[0] || 6) + 4);
  rows.push({ name: `Период ${rows.length + 1}`, start, end: `${String(hour).padStart(2, "0")}:00`,
    interval_min: 15, travel_time_factor: 1, transition_mode: "abrupt",
    transition_window_min: 0, color: "#3b82f6", priority: rows.length });
  renderRouteCard(window._routeCard);
}

function routeCardPeriodDuplicate(index) {
  const rows = routeCardPeriodRows(), source = rows[index];
  const duration = Math.max(1, routePeriodMinutes(source.end) - routePeriodMinutes(source.start));
  const start = routePeriodMinutes(source.end), end = Math.min(2879, start + duration);
  rows.splice(index + 1, 0, { ...source, name: `${source.name} — копия`,
    start: routePeriodTime(start), end: routePeriodTime(end) });
  rows.forEach((row, position) => { row.priority = position; });
  renderRouteCard(window._routeCard);
}

function routePeriodMinutes(value) {
  const [hours, minutes] = String(value || "0:0").split(":").map(Number);
  return hours * 60 + minutes;
}

function routeCardPeriodMove(index, delta) {
  const rows = routeCardPeriodRows(), next = index + delta;
  if (next < 0 || next >= rows.length) return;
  [rows[index], rows[next]] = [rows[next], rows[index]];
  rows.forEach((row, position) => { row.priority = position; });
  renderRouteCard(window._routeCard);
}

function routeCardPeriodRemove(index) {
  routeCardPeriodRows().splice(index, 1);
  renderRouteCard(window._routeCard);
}

function routeCardPeriodPayload(state = window._routeCard) {
  return routeCardPeriodRows(state).map((row, priority) => ({
    name: row.name.trim(), start: row.start, end: row.end,
    interval_min: +row.interval_min, travel_time_factor: +row.travel_time_factor,
    transition_mode: row.transition_mode,
    transition_window_min: +row.transition_window_min,
    color: row.color, priority,
  }));
}

async function routeCardPeriodSave() {
  const state = window._routeCard; state.periodError = "";
  try {
    const saved = await api(`/api/routes/${state.routeId}/periods/${state.periodDay}`, {
      method: "PUT", body: { items: routeCardPeriodPayload(state),
        require_continuous: state.periodContinuous },
    });
    state.periodDrafts[state.periodDay] = saved.items.map(row => ({ ...row,
      start: routePeriodTime(row.start_min), end: routePeriodTime(row.end_min) }));
    state.periodCalcPreview = null; toast("Периоды движения сохранены");
  } catch (error) { state.periodError = error.message; }
  renderRouteCard(state);
}

async function routeCardTemplatePreview() {
  const state = window._routeCard; state.periodError = "";
  if (!state.periodTemplateId) { state.periodError = "Выберите шаблон"; renderRouteCard(state); return; }
  try {
    state.periodTemplatePreview = await api(
      `/api/routes/${state.routeId}/periods/${state.periodDay}/template-preview`,
      { method: "POST", body: { template_id: +state.periodTemplateId } },
    );
  } catch (error) { state.periodError = error.message; }
  renderRouteCard(state);
}

function routeCardTemplateCancel() {
  window._routeCard.periodTemplatePreview = null; renderRouteCard(window._routeCard);
}

async function routeCardTemplateApply() {
  const state = window._routeCard;
  try {
    await api(`/api/routes/${state.routeId}/periods/${state.periodDay}/template-apply`, {
      method: "POST", body: { preview_token: state.periodTemplatePreview.preview_token },
    });
    state.periodTemplatePreview = null; delete state.periodDrafts[state.periodDay];
    toast("Шаблон применён"); await routeCardLoadPeriods();
  } catch (error) { state.periodError = error.message; renderRouteCard(state); }
}

async function routeCardPeriodPreview() {
  const state = window._routeCard; state.periodError = "";
  try {
    state.periodCalcPreview = await api(
      `/api/routes/${state.routeId}/periods/${state.periodDay}/preview`,
      { method: "POST", body: { terminal_layover_min: 6 } },
    );
  } catch (error) { state.periodError = error.message; }
  renderRouteCard(state);
}

function routeCardPeriodTimeline(rows) {
  if (!rows.length) return '<div class="route-empty">Добавьте хотя бы один период</div>';
  const start = Math.min(...rows.map(row => routePeriodMinutes(row.start)));
  const end = Math.max(...rows.map(row => routePeriodMinutes(row.end)));
  const span = Math.max(1, end - start);
  return `<div class="route-period-timeline">${rows.map(row => {
    const width = Math.max(3, (routePeriodMinutes(row.end) - routePeriodMinutes(row.start)) / span * 100);
    return `<div class="route-period-block" style="width:${width}%;background:${esc(row.color)}" title="${esc(row.name)}"><b>${esc(row.name)}</b><span>${esc(row.start)}–${esc(row.end)}</span></div>`;
  }).join("")}</div>`;
}

function routeCardPeriodResult(state) {
  const result = state.periodCalcPreview;
  if (!result) return "";
  const cards = result.periods.map(row => `<div class="card"><b>${esc(row.name)}</b><div class="num">${esc(row.buses_required)}</div><div class="lbl">автобусов · цикл ${esc(row.cycle_min)} мин · интервал ${esc(row.interval_min)} мин</div></div>`).join("");
  const warnings = result.warnings.map(row => `<div class="vio w route-demand-jump"><b>Скачок потребности ${row.delta > 0 ? "+" : ""}${esc(row.delta)}</b>${esc(row.from)} → ${esc(row.to)}</div>`).join("");
  return `<section class="panel"><h3>Расчётный предпросмотр</h3><p class="muted">Сохранённые рейсы не изменены.</p>
    <div class="cards route-demand-grid"><div class="card"><div class="num">${esc(result.max_buses_required)}</div><div class="lbl">максимум автобусов</div></div>${cards}</div>
    <div class="route-preview-times"><b>Отправления:</b> ${result.departures.map(row => esc(row.time)).join(", ")}</div>${warnings}</section>`;
}

function routeCardTemplateDiff(state) {
  const preview = state.periodTemplatePreview;
  if (!preview) return "";
  const rows = (preview.diff.new || []).map(row => `<tr><td>${esc(row.name)}</td><td>${routePeriodTime(row.start_min)}</td><td>${routePeriodTime(row.end_min)}</td><td>${esc(row.interval_min)}</td></tr>`).join("");
  return `<section class="panel"><h3>Предпросмотр шаблона — ещё не применён</h3><p class="muted">Было периодов: ${(preview.diff.old || []).length}; станет: ${(preview.diff.new || []).length}</p>
    ${tbl(["Период", "Начало", "Конец", "Интервал"], rows)}<div class="foot"><button class="btn sec" onclick="routeCardTemplateCancel()">Отменить</button><button class="btn" onclick="routeCardTemplateApply()">Применить шаблон</button></div></section>`;
}

function routeCardPeriods(state) {
  if (state.periodsLoading) return '<div class="route-empty">Загрузка периодов…</div>';
  const rows = routeCardPeriodRows(state);
  const editor = rows.map((row, index) => `<div class="route-period-row">
    <input value="${esc(row.name)}" aria-label="Название периода" onchange="routeCardPeriodChange(${index},'name',this.value)">
    <input type="time" value="${esc(row.start)}" onchange="routeCardPeriodChange(${index},'start',this.value)">
    <input type="time" value="${esc(row.end)}" onchange="routeCardPeriodChange(${index},'end',this.value)">
    <label>Интервал<input type="number" min="1" value="${esc(row.interval_min)}" onchange="routeCardPeriodChange(${index},'interval_min',this.value)"></label>
    <label>Коэффициент<input type="number" min="0.25" max="4" step="0.05" value="${esc(row.travel_time_factor)}" onchange="routeCardPeriodChange(${index},'travel_time_factor',this.value)"></label>
    <select onchange="routeCardPeriodChange(${index},'transition_mode',this.value)"><option value="abrupt" ${row.transition_mode === "abrupt" ? "selected" : ""}>Резко</option><option value="smooth" ${row.transition_mode === "smooth" ? "selected" : ""}>Плавно</option></select>
    <label>Переход, мин<input type="number" min="0" value="${esc(row.transition_window_min)}" onchange="routeCardPeriodChange(${index},'transition_window_min',this.value)"></label>
    <input type="color" value="${esc(row.color)}" onchange="routeCardPeriodChange(${index},'color',this.value)">
    <div class="route-period-actions"><button class="btn small sec" onclick="routeCardPeriodMove(${index},-1)" ${index ? "" : "disabled"}>↑</button><button class="btn small sec" onclick="routeCardPeriodMove(${index},1)" ${index + 1 < rows.length ? "" : "disabled"}>↓</button><button class="btn small sec" onclick="routeCardPeriodDuplicate(${index})">Копия</button><button class="btn small danger" onclick="routeCardPeriodRemove(${index})">✕</button></div></div>`).join("");
  const templateOptions = state.periodTemplates.map(item => `<option value="${item.id}" ${+state.periodTemplateId === item.id ? "selected" : ""}>${esc(item.name)}</option>`).join("");
  return `<div class="route-card-toolbar"><label>Тип дня <select onchange="routeCardPeriodDay(this.value)"><option value="будни" ${state.periodDay === "будни" ? "selected" : ""}>Будни</option><option value="суббота" ${state.periodDay === "суббота" ? "selected" : ""}>Суббота</option><option value="воскресенье" ${state.periodDay === "воскресенье" ? "selected" : ""}>Воскресенье</option></select></label>
    <label><input type="checkbox" ${state.periodContinuous ? "checked" : ""} onchange="window._routeCard.periodContinuous=this.checked"> Без разрывов</label><button class="btn sec" onclick="routeCardPeriodAdd()">+ Период</button><button class="btn" onclick="routeCardPeriodSave()">Сохранить все</button></div>
    ${state.periodError ? `<div class="vio r"><b>Не удалось выполнить действие</b>${esc(state.periodError)}. Правки сохранены в форме.</div>` : ""}
    <div class="route-period-grid">${editor || '<div class="route-empty">Периоды ещё не заданы</div>'}</div>${routeCardPeriodTimeline(rows)}
    <section class="panel"><h3>Шаблоны и расчёт</h3><div class="route-template-actions"><select onchange="window._routeCard.periodTemplateId=this.value"><option value="">Выберите шаблон</option>${templateOptions}</select><button class="btn sec" onclick="routeCardTemplatePreview()">Предпросмотр шаблона</button><button class="btn" onclick="routeCardPeriodPreview()">Рассчитать расписание</button></div></section>
    ${routeCardTemplateDiff(state)}${routeCardPeriodResult(state)}`;
}

function routeCardBody(state) {
  if (state.tab === "passport") return routeCardPassport(state);
  if (state.tab === "stops") return routeCardStops(state);
  if (state.tab === "map") return routeCardMap(state);
  if (state.tab === "segments") return routeCardSegments(state);
  if (state.tab === "periods") return routeCardPeriods(state);
  return routeCardHistory(state);
}

function renderRouteCard(state) {
  routeCardDestroyMap();
  const tabs = ROUTE_CARD_TABS.map(([key, label]) => `<button class="route-tab ${state.tab === key ? "on" : ""}" onclick="routeCardTab('${key}')">${esc(label)}</button>`).join("");
  $("content").innerHTML = `<div class="route-card">${routeCardHeader(state)}<nav class="route-tabs" aria-label="Разделы карточки маршрута">${tabs}</nav><div class="route-card-body">${routeCardBody(state)}</div></div>`;
  if (state.tab === "map") routeCardBindMap(state);
}
