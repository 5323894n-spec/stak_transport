/* АТП-система: SPA без сборки. */
"use strict";
let TOKEN = localStorage.getItem("atp_token") || "";
let USER = null;
let REFS = { drivers: [], buses: [], routes: [], absence_types: [] };

const $ = (id) => document.getElementById(id);
const esc = (s) => s == null ? "" : String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
const today = () => new Date().toISOString().slice(0, 10);
const thisMonth = () => new Date().toISOString().slice(0, 7);

function toast(msg, isErr) {
  const t = document.createElement("div");
  t.className = "toast" + (isErr ? " err" : "");
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), isErr ? 7000 : 3500);
}

async function api(path, opts = {}) {
  opts.headers = Object.assign({ "Authorization": "Bearer " + TOKEN }, opts.headers || {});
  if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, opts);
  if (r.status === 401) { showLogin(); throw new Error("Требуется вход"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || ("Ошибка " + r.status));
  return data;
}
const openWin = (path) => window.open(path + (path.includes("?") ? "&" : "?") + "token=" + TOKEN, "_blank");

/* ---------- вход ---------- */
async function doLogin() {
  try {
    const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: $("lg-user").value.trim(), password: $("lg-pass").value }) });
    const d = await r.json();
    if (!r.ok) { $("lg-err").textContent = d.detail || "Ошибка входа"; return; }
    TOKEN = d.token; localStorage.setItem("atp_token", TOKEN);
    USER = d;
    boot();
  } catch (e) { $("lg-err").textContent = "Сервер недоступен"; }
}
function showLogin() { $("app").style.display = "none"; $("login-screen").style.display = "flex"; }
async function logout() { try { await api("/api/logout", { method: "POST" }); } catch (e) {}
  localStorage.removeItem("atp_token"); location.reload(); }

/* ---------- каркас ---------- */
const NAV = [
  ["Оперативная работа", null],
  ["dashboard", "Главная панель"],
  ["order", "Наряд на день"],
  ["summarySchedule", "Сводное расписание"],
  ["release", "Медосмотр и техконтроль"],
  ["waybills", "Путевые листы"],
  ["repairs", "Ремонт и ТО"],
  ["Планирование", null],
  ["schedule", "Расписания маршрутов"],
  ["roster", "График водителей"],
  ["absences", "Отпуска и отсутствия"],
  ["calendar", "Производственный календарь"],
  ["Учёт", null],
  ["timesheet", "Табель и выгрузка в 1С"],
  ["fuel", "Топливо"],
  ["reports", "Отчёты"],
  ["Справочники", null],
  ["drivers", "Водители"],
  ["buses", "Автобусы"],
  ["routes", "Маршруты"],
  ["Администрирование", null],
  ["norms", "Нормативы (Приказ 424)"],
  ["settings", "Настройки и пользователи"],
  ["audit", "Журнал аудита"],
];
const TITLES = {};
NAV.forEach(([k, v]) => { if (v) TITLES[k] = v; });
const ADMIN_ONLY = new Set(["audit"]);

function buildNav() {
  const nav = $("nav");
  nav.innerHTML = NAV.map(([k, v]) => {
    if (!v) return `<div class="grp">${k}</div>`;
    if (ADMIN_ONLY.has(k) && !["админ", "руководитель"].includes(USER.role)) return "";
    return `<a href="#/${k}" data-view="${k}">${v}</a>`;
  }).join("");
}
function route() {
  const parts = (location.hash || "#/dashboard").slice(2).split("/");
  const view = parts[0] || "dashboard";
  const args = parts.slice(1).map(decodeURIComponent);
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.view === view));
  const cardTitle = view === "vehicleCard" ? "Карточка автобуса" : view === "routeCard" ? "Карточка маршрута" : view;
  $("page-title").textContent = TITLES[view] || cardTitle;
  const fn = VIEWS[view] || VIEWS.dashboard;
  $("content").innerHTML = "<div class='muted'>Загрузка…</div>";
  fn(...args).catch(e => { $("content").innerHTML = `<div class="vio"><b>Ошибка</b>${esc(e.message)}</div>`; });
}
window.addEventListener("hashchange", route);

async function loadRefs() {
  const [d, b, r, a] = await Promise.all([
    api("/api/refs/drivers"), api("/api/refs/buses"), api("/api/refs/routes"), api("/api/refs/absence_types")]);
  REFS = { drivers: d.items, buses: b.items, routes: r.items, absence_types: a.items };
}
const drvName = (id) => { const d = REFS.drivers.find(x => x.id === id); return d ? d.fio : ""; };
const busName = (id) => { const b = REFS.buses.find(x => x.id === id); return b ? `${b.garage_number} (${b.plate || ""})` : ""; };
const routeName = (id) => { const r = REFS.routes.find(x => x.id === id); return r ? "№ " + r.number : ""; };

async function boot() {
  try { USER = await api("/api/me"); } catch (e) { showLogin(); return; }
  $("login-screen").style.display = "none";
  $("app").style.display = "grid";
  $("user-name").innerHTML = `<b>${esc(USER.full_name || USER.username)}</b> · ${esc(USER.role)}`;
  await loadRefs();
  buildNav();
  route();
}

/* ---------- универсальные элементы ---------- */
function modal(html) {
  return new Promise((resolve) => {
    const bg = document.createElement("div");
    bg.className = "modal-bg";
    bg.innerHTML = `<div class="modal">${html}</div>`;
    document.body.appendChild(bg);
    bg.addEventListener("click", (e) => { if (e.target === bg) { bg.remove(); resolve(null); } });
    bg.querySelector(".modal").addEventListener("click", (e) => {
      const seg = e.target.closest("[data-seg-k]");
      if (seg) {
        const group = seg.closest(".seg-tabs");
        const input = group ? group.querySelector(`[data-k="${seg.dataset.segK}"]`) : null;
        if (input) input.value = seg.dataset.val || "";
        if (group) group.querySelectorAll("[data-seg-k]").forEach(x => x.classList.toggle("on", x === seg));
        return;
      }
      const b = e.target.closest("[data-act]");
      if (!b) return;
      if (b.dataset.act === "cancel") { bg.remove(); resolve(null); }
      if (b.dataset.act === "ok") {
        const out = {};
        bg.querySelectorAll("[data-k]").forEach(i => { out[i.dataset.k] = i.type === "checkbox" ? (i.checked ? 1 : 0) : i.value; });
        bg.remove(); resolve(out);
      }
    });
  });
}
function field(f, v) {
  const val = v == null ? (f.def != null ? f.def : "") : v;
  if (f.type === "segments") {
    const opts = f.options.map(o => {
      const [ov, ol] = Array.isArray(o) ? o : [o, o];
      const on = String(ov) === String(val) ? "on" : "";
      return `<button type="button" class="${on}" data-seg-k="${esc(f.k)}" data-val="${esc(ov)}" aria-pressed="${on ? "true" : "false"}">${esc(ol)}</button>`;
    }).join("");
    return `<label class="f f-wide">${esc(f.label)}<div class="seg-tabs"><input data-k="${f.k}" type="hidden" value="${esc(val)}">${opts}</div></label>`;
  }
  if (f.type === "select") {
    const opts = f.options.map(o => {
      const [ov, ol] = Array.isArray(o) ? o : [o, o];
      return `<option value="${esc(ov)}" ${String(ov) === String(val) ? "selected" : ""}>${esc(ol)}</option>`;
    }).join("");
    return `<label class="f">${esc(f.label)}<select data-k="${f.k}">${f.empty ? '<option value="">—</option>' : ""}${opts}</select></label>`;
  }
  if (f.type === "textarea") return `<label class="f">${esc(f.label)}<textarea data-k="${f.k}" rows="2">${esc(val)}</textarea></label>`;
  return `<label class="f">${esc(f.label)}<input data-k="${f.k}" type="${f.type || "text"}" value="${esc(val)}" ${f.step ? `step="${f.step}"` : ""}></label>`;
}
function formModal(title, fields, values = {}, note = "") {
  const body = fields.map(f => field(f, values[f.k])).join("");
  return modal(`<h3>${esc(title)}</h3>${note ? `<div class="muted" style="margin-bottom:10px">${note}</div>` : ""}
    <div class="cols">${body}</div>
    <div class="foot"><button class="btn sec" data-act="cancel">Отмена</button>
    <button class="btn" data-act="ok">Сохранить</button></div>`);
}
async function textModal(title, label, value = "", note = "") {
  const r = await formModal(title, [{ k: "value", label, type: "textarea" }], { value }, note);
  return r ? r.value : null;
}
const tbl = (headers, rowsHtml) =>
  `<div class="tbl-wrap"><table class="grid"><thead><tr>${headers.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rowsHtml}</tbody></table></div>`;
const sevBadge = (s) => `<span class="badge ${s === "критично" ? "b-err" : s === "ошибка" ? "b-warn" : "b-mut"}">${s}</span>`;
const stBadge = (s) => {
  const m = { "утвержден": "b-ok", "выдан": "b-inf", "выполнен": "b-ok", "черновик": "b-mut", "аннулирован": "b-err",
    "оформлен": "b-inf", "скорректирован": "b-warn", "отменен": "b-err", "не сформирован": "b-mut" };
  return `<span class="badge ${m[s] || "b-mut"}">${esc(s)}</span>`;
};
function violList(violations) {
  if (!violations.length) return `<div class="badge b-ok">Нарушений не выявлено</div>`;
  return violations.map(v => `<div class="vio ${v.severity === "предупреждение" ? "w" : ""}">
    <b>${esc(v.type)} ${sevBadge(v.severity)}</b>
    ${esc(v.fio)} (таб. ${esc(v.tab_number)}) — ${esc(v.date)}${v.route ? ", маршрут " + esc(v.route) + " вых. " + esc(v.output) + " см. " + esc(v.shift) : ""}<br>
    Норма: ${esc(v.norm_value)} · Факт: ${esc(v.fact_value)}<br>
    <span class="muted">Рекомендация: ${esc(v.recommendation)}</span></div>`).join("");
}

/* ================= ГЛАВНАЯ ================= */
const VIEWS = {};
async function repairVehicleCard(busId) {
  openVehicleCard(busId);
}
VIEWS.vehicleCard = async (...args) => window.vehicleCardView(...args);
async function repairMaintenancePlan() {
  const refs = await api("/api/repairs/references");
  const v = await formModal("Плановое ТО", [
    { k: "vehicle_id", label: "Автобус", type: "select", options: REFS.buses.map(b => [b.id, `${b.garage_number} ${b.plate || ""}`]) },
    { k: "repair_type_id", label: "Вид ТО", type: "select", options: refs.repair_types.map(x => [x.id, x.name]) },
    { k: "name", label: "Название плана" },
    { k: "next_date", label: "Следующая дата", type: "date" },
    { k: "next_odometer", label: "Следующий пробег", type: "number", step: "1" },
    { k: "warning_days", label: "Предупреждать за дней", type: "number", def: 7 },
    { k: "warning_km", label: "Предупреждать за км", type: "number", def: 500 },
  ]);
  if (!v) return;
  await api("/api/repairs/maintenance/plans", { method: "POST", body: v });
  toast("План ТО создан"); route();
}
async function repairEvaluateMaintenance() {
  const r = await api("/api/repairs/maintenance/evaluate", { method: "POST", body: { date: today() } });
  toast(`Проверено планов: ${r.plans_checked}, создано заявок: ${r.requests_created}`); route();
}
async function repairEvaluateAlerts() {
  const r = await api("/api/repairs/alerts/evaluate", { method: "POST", body: { date: today() } });
  toast(`Создано уведомлений: ${r.notifications_created}; просрочек: ${r.overdue_created}; дефицитов: ${r.low_stock_created}`);
  route();
}async function repairReportExport() {
  const end = today(), start = end.slice(0, 8) + "01";
  const v = await formModal("Фильтры отчёта ремонта", [
    { k: "date_from", label: "Период с", type: "date" },
    { k: "date_to", label: "Период по", type: "date" },
    { k: "vehicle_id", label: "Автобус", type: "select", empty: true, options: REFS.buses.map(b => [b.id, `${b.garage_number || ""} ${b.plate || ""}`]) },
    { k: "status", label: "Статус заказ-наряда", type: "select", empty: true, options: ["черновик", "диагностика", "ожидает запчасти", "готов к работе", "в работе", "приостановлен", "контроль", "завершен", "отменен"] },
  ], { date_from: start, date_to: end });
  if (!v) return;
  const query = new URLSearchParams();
  Object.entries(v).forEach(([key, value]) => { if (value !== "") query.set(key, value); });
  openWin(`/api/repairs/reports/export.xlsx?${query.toString()}`);
}async function repairNewRequest() {
  const v = await formModal("Новая заявка на ремонт", [
    { k: "vehicle_id", label: "Автобус", type: "select", empty: true, options: REFS.buses.map(b => [b.id, `${b.garage_number || ""} ${b.plate || ""} ${b.brand || ""} ${b.model || ""}`]) },
    { k: "odometer", label: "Пробег, км", type: "number", step: "1" },
    { k: "criticality", label: "Критичность", type: "select", options: ["обычная", "высокая", "критическая"] },
    { k: "request_source", label: "Источник", type: "select", options: ["диспетчер", "водитель", "механик", "плановое ТО"] },
    { k: "location", label: "Место нахождения" },
    { k: "fault_description", label: "Описание неисправности", type: "textarea" },
  ], { criticality: "обычная", request_source: "диспетчер" });
  if (!v) return;
  await api("/api/repairs/requests", { method: "POST", body: v });
  toast("Заявка на ремонт создана"); route();
}
async function repairCreateOrder(requestId, vehicleId) {
  const refs = await api("/api/repairs/references");
  const v = await formModal("Создать заказ-наряд", [
    { k: "repair_type_id", label: "Вид ремонта", type: "select", options: refs.repair_types.map(x => [x.id, x.name]) },
    { k: "repair_post_id", label: "Ремонтный пост", type: "select", empty: true, options: refs.repair_posts.map(x => [x.id, x.name]) },
    { k: "diagnosis", label: "Предварительный диагноз", type: "textarea" },
    { k: "planned_start", label: "Плановое начало", type: "datetime-local" },
    { k: "planned_end", label: "Плановое окончание", type: "datetime-local" },
  ]);
  if (!v) return;
  await api("/api/repairs/orders", { method: "POST", body: { ...v, request_id: requestId, vehicle_id: vehicleId } });
  toast("Заказ-наряд создан"); route();
}
async function repairEditOrder(orderId) {
  const [detail, refs] = await Promise.all([api(`/api/repairs/orders/${orderId}/work`), api("/api/repairs/references")]);
  const order = detail.order;
  const v = await formModal("Редактировать заказ-наряд", [
    { k: "repair_type_id", label: "Вид ремонта", type: "select", options: refs.repair_types.map(x => [x.id, x.name]) },
    { k: "repair_post_id", label: "Ремонтный пост", type: "select", empty: true, options: refs.repair_posts.map(x => [x.id, x.name]) },
    { k: "diagnosis", label: "Диагноз", type: "textarea" },
    { k: "planned_start", label: "Плановое начало", type: "datetime-local" },
    { k: "planned_end", label: "Плановое окончание", type: "datetime-local" },
    { k: "external_cost", label: "Внешние расходы", type: "number", step: "0.01" },
    { k: "other_cost", label: "Прочие расходы", type: "number", step: "0.01" },
  ], order);
  if (!v) return;
  await api(`/api/repairs/orders/${orderId}`, { method: "PATCH", body: v });
  toast("Заказ-наряд обновлён"); route();
}async function repairAssignWorker(orderId) {
  const users = (await api("/api/repairs/workers/available")).items || [];
  const v = await formModal("Назначить исполнителя", [
    { k: "worker_id", label: "Сотрудник", type: "select", options: users.map(x => [x.id, `${x.full_name || x.username} — ${x.role}`]) },
    { k: "role", label: "Роль в ремонте", type: "select", options: ["слесарь", "электрик", "диагност", "мастер"] },
    { k: "planned_hours", label: "Плановые часы", type: "number", step: "0.1" },
    { k: "hourly_rate", label: "Ставка за час", type: "number", step: "0.01" },
  ], { role: "слесарь" });
  if (!v) return;
  await api(`/api/repairs/orders/${orderId}/workers`, { method: "POST", body: v });
  toast("Исполнитель назначен"); route();
}
async function repairWorkerStart(id) { await api(`/api/repairs/workers/${id}/start`, { method: "POST" }); toast("Работа исполнителя начата"); route(); }
async function repairWorkerFinish(id) {
  const hours = await textModal("Завершить работу исполнителя", "Фактические часы");
  if (hours === null) return;
  await api(`/api/repairs/workers/${id}/finish`, { method: "POST", body: { actual_hours: hours } });
  toast("Работа исполнителя завершена"); route();
}
async function repairWorkers(orderId) {
  const d = await api(`/api/repairs/orders/${orderId}/work`);
  const rows = (d.workers || []).map(w => `<tr><td>${esc(w.full_name || w.username)}</td><td>${esc(w.role)}</td><td>${esc(w.status)}</td><td>${esc(w.planned_hours)}</td><td>${esc(w.actual_hours)}</td><td>${esc(w.hourly_rate)}</td><td>${w.status === "назначен" ? `<button class="btn small" onclick="repairWorkerStart(${w.id})">Начать</button>` : w.status === "в работе" ? `<button class="btn small" onclick="repairWorkerFinish(${w.id})">Завершить</button>` : ""}</td></tr>`).join("");
  await modal(`<h3>Исполнители заказ-наряда</h3>${tbl(["Сотрудник", "Роль", "Статус", "План, ч", "Факт, ч", "Ставка", "Действие"], rows || `<tr><td colspan="7" class="muted">Исполнители не назначены</td></tr>`)}<div class="muted">Трудовая стоимость: ${esc(d.order.labor_cost || 0)}</div><div class="foot"><button class="btn" onclick="repairAssignWorker(${orderId})">Назначить исполнителя</button><button class="btn sec" data-act="cancel">Закрыть</button></div>`);
}
async function repairUploadAttachment(orderId) {
  const input = document.createElement("input"); input.type = "file"; input.accept = ".pdf,.jpg,.jpeg,.png,.docx,.xlsx";
  input.onchange = async () => {
    if (!input.files[0]) return;
    const body = new FormData(); body.append("file", input.files[0]); body.append("category", "документ ремонта");
    await api(`/api/repairs/orders/${orderId}/attachments`, { method: "POST", body });
    toast("Вложение загружено"); route();
  };
  input.click();
}
async function repairAttachments(orderId) {
  const items = (await api(`/api/repairs/orders/${orderId}/attachments`)).items || [];
  const rows = items.map(x => `<tr><td>${esc(x.original_name)}</td><td>${esc(x.category || "")}</td><td>${Math.ceil((x.size_bytes || 0)/1024)} КБ</td><td>${esc(x.uploaded_at || "")}</td><td><a class="btn small sec" target="_blank" href="/api/repairs/attachments/${x.id}/download?token=${TOKEN}">Скачать</a></td></tr>`).join("");
  await modal(`<h3>Вложения заказ-наряда</h3>${tbl(["Файл", "Категория", "Размер", "Загружен", "Действие"], rows || `<tr><td colspan="5" class="muted">Вложений пока нет</td></tr>`)}<div class="foot"><button class="btn" onclick="repairUploadAttachment(${orderId})">Загрузить файл</button><button class="btn sec" data-act="cancel">Закрыть</button></div>`);
}
async function repairPartAction(partLinkId, action, title) {
  const quantity = await textModal(title, "Количество");
  if (quantity === null) return;
  await api(`/api/repairs/parts/${partLinkId}/${action}`, { method: "POST", body: { quantity } });
  toast(title + ": выполнено"); route();
}
async function repairParts(orderId) {
  const d = await api(`/api/repairs/orders/${orderId}/parts`);
  const rows = (d.items || []).map(p => `<tr><td>${esc(p.code)} — ${esc(p.name)}</td><td>${esc(p.requested_qty)}</td><td>${esc(p.reserved_qty)}</td><td>${esc(p.issued_qty)}</td><td>${esc(p.installed_qty)}</td><td>${esc(p.returned_qty)}</td><td>${esc(p.stock_qty)}</td><td><button class="btn small sec" onclick="repairPartAction(${p.id},'reserve','Резервирование')">Резерв</button> <button class="btn small sec" onclick="repairPartAction(${p.id},'issue','Выдача')">Выдать</button> <button class="btn small sec" onclick="repairPartAction(${p.id},'install','Установка')">Установить</button> <button class="btn small sec" onclick="repairPartAction(${p.id},'return','Возврат')">Вернуть</button></td></tr>`).join("");
  await modal(`<h3>Запчасти заказ-наряда</h3>${tbl(["Запчасть", "Запрошено", "Резерв", "Выдано", "Установлено", "Возврат", "Склад", "Действия"], rows || `<tr><td colspan="8" class="muted">Запчасти не запрошены</td></tr>`)}<div class="muted">Стоимость запчастей: ${esc(d.order.parts_cost || 0)}</div><div class="foot"><button class="btn" onclick="repairAddPart(${orderId})">Запросить запчасть</button><button class="btn sec" data-act="cancel">Закрыть</button></div>`);
}
async function repairAddPart(orderId) {
  const parts = (await api("/api/repairs/stock/parts")).items || [];
  if (!parts.length) { toast("В справочнике склада нет запчастей", true); return; }
  const v = await formModal("Запчасти заказ-наряда", [
    { k: "part_id", label: "Запчасть", type: "select", options: parts.map(p => [p.id, `${p.code} — ${p.name} (остаток ${p.stock_qty} ${p.unit})`]) },
    { k: "quantity", label: "Количество", type: "number", step: "0.01" },
  ]);
  if (!v) return;
  await api(`/api/repairs/orders/${orderId}/parts`, { method: "POST", body: v });
  toast("Запчасть запрошена со склада"); route();
}
async function repairOperationStart(id) {
  await api(`/api/repairs/operations/${id}/start`, { method: "POST" });
  toast("Операция начата"); route();
}
async function repairOperationComplete(id) {
  const v = await formModal("Завершить операцию", [
    { k: "actual_hours", label: "Фактические часы", type: "number", step: "0.1" },
    { k: "result", label: "Результат работы", type: "textarea" },
  ]);
  if (!v) return;
  await api(`/api/repairs/operations/${id}/complete`, { method: "POST", body: v });
  toast("Операция завершена"); route();
}
async function repairOperations(orderId) {
  const d = await api(`/api/repairs/orders/${orderId}/work`);
  const rows = (d.operations || []).map(o => `<tr><td>${esc(o.sequence_no)}</td><td>${esc(o.name)}</td><td>${esc(o.status)}</td><td>${esc(o.norm_hours)}</td><td>${esc(o.actual_hours)}</td><td>${esc(o.result || "")}</td><td>${o.status === "запланирована" ? `<button class="btn small" onclick="repairOperationStart(${o.id})">Начать</button>` : o.status === "в работе" ? `<button class="btn small" onclick="repairOperationComplete(${o.id})">Завершить</button>` : ""}</td></tr>`).join("");
  await modal(`<h3>Операции заказ-наряда</h3>${tbl(["№", "Операция", "Статус", "Норматив", "Факт", "Результат", "Действие"], rows || `<tr><td colspan="7" class="muted">Операции не добавлены</td></tr>`)}<div class="muted">Плановые часы: ${esc(d.order.planned_hours || 0)} · Фактические часы: ${esc(d.order.actual_hours || 0)}</div><div class="foot"><button class="btn" onclick="repairAddOperation(${orderId})">Добавить операцию</button><button class="btn sec" data-act="cancel">Закрыть</button></div>`);
}
async function repairAddOperation(orderId) {
  const v = await formModal("Добавить операцию", [
    { k: "name", label: "Наименование операции" },
    { k: "norm_hours", label: "Норматив, часов", type: "number", step: "0.1" },
    { k: "price", label: "Стоимость работы", type: "number", step: "0.01" },
  ]);
  if (!v) return;
  await api(`/api/repairs/orders/${orderId}/operations`, { method: "POST", body: v });
  toast("Операция добавлена в заказ-наряд"); route();
}
async function repairInspect(orderId) {
  const v = await formModal("Контрольный осмотр", [
    { k: "result", label: "Результат осмотра" },
    { k: "release_allowed", label: "Выпуск разрешён", type: "checkbox" },
    { k: "defects", label: "Выявленные дефекты", type: "textarea" },
    { k: "comment", label: "Комментарий", type: "textarea" },
  ]);
  if (!v) return;
  await api(`/api/repairs/orders/${orderId}/inspection`, { method: "POST", body: v });
  toast(v.release_allowed ? "Контроль пройден" : "Возвращено на доработку"); route();
}
async function repairCloseOrder(orderId) {
  const result = await textModal("Закрыть ремонт", "Итоговый результат");
  if (!result) return;
  await api(`/api/repairs/orders/${orderId}/close`, { method: "POST", body: { result } });
  toast("Ремонт закрыт, автобус допущен"); route();
}
async function repairAdvanceOrder(id, current) {
  const next = { "черновик": "диагностика", "диагностика": "готов к работе", "готов к работе": "в работе", "в работе": "контроль" }[current];
  if (next) await repairOrderStatus(id, next);
}
async function repairOrderStatus(id, status) {
  await api(`/api/repairs/orders/${id}/status`, { method: "POST", body: { status } });
  toast("Статус заказ-наряда изменён"); route();
}
async function repairCancelRequest(id) {
  const reason = await textModal("Отмена заявки", "Причина отмены");
  if (!reason) return;
  await api(`/api/repairs/requests/${id}/cancel`, { method: "POST", body: { reason } });
  toast("Заявка отменена"); route();
}
VIEWS.repairs = async function () {
  const [requestData, orderData, maintenanceData, dashboardData, metricsData, calendarData] = await Promise.all([api("/api/repairs/requests"), api("/api/repairs/orders?active_only=true"), api("/api/repairs/maintenance/plans"), api("/api/repairs/dashboard"), api("/api/repairs/metrics/downtime"), api("/api/repairs/calendar")]);
  const items = requestData.items || [], orders = orderData.items || [], maintenance = maintenanceData.items || [], repairKpi = dashboardData, repairMetrics = metricsData;
  const calendarEvents = calendarData.items || [], calendarRows = calendarEvents.map(e => `<tr><td>${stBadge(e.event_type)}</td><td>${esc(e.start || "")}${e.end && e.end !== e.start ? `<br><span class="muted">до ${esc(e.end)}</span>` : ""}</td><td>${esc(e.garage_number || "")} ${esc(e.plate || "")}</td><td>${esc(e.order_number || e.title || "")}</td><td>${stBadge(e.status || "")}</td></tr>`).join("");
  const active = items.filter(x => !["отменена", "закрыта"].includes(x.status));
  const rows = items.map(r => `<tr><td><b>${esc(r.request_number)}</b><br><span class="muted">${esc(r.created_at || "")}</span></td><td>${esc(r.garage_number || "")}<br><span class="muted">${esc(r.plate || "")} ${esc(r.brand || "")} ${esc(r.model || "")}</span></td><td>${r.repeated ? `<span class="badge b-err">Повторная неисправность</span><br>` : ""}${esc(r.fault_description)}</td><td>${esc(r.odometer)}</td><td>${stBadge(r.criticality)}</td><td>${stBadge(r.status)}</td><td>${r.status === "новая" ? `<button class="btn small" onclick="repairCreateOrder(${r.id},${r.vehicle_id})">Создать заказ-наряд</button> <button class="btn small sec" onclick="repairCancelRequest(${r.id})">Отменить</button>` : ""}</td></tr>`).join("");
  $("content").innerHTML = `<div class="toolbar"><button class="btn" onclick="repairNewRequest()">Создать заявку</button><button class="btn sec" onclick="repairMaintenancePlan()">Плановое ТО</button><button class="btn sec" onclick="repairEvaluateMaintenance()">Проверить сроки ТО</button><button class="btn sec" onclick="repairEvaluateAlerts()">Проверить уведомления</button><button class="btn sec" onclick="repairReportExport()">Отчёт ремонта Excel</button><select aria-label="Открыть карточку автобуса" onchange="if(this.value)openVehicleCard(this.value)"><option value="">Карточка автобуса…</option>${REFS.buses.map(b => `<option value="${b.id}">${esc(b.garage_number)} · ${esc(b.plate || "")}</option>`).join("")}</select><button class="btn sec" onclick="route()">Обновить</button></div><div class="cards"><div class="card"><div class="num">${items.length}</div><div class="lbl">всего заявок</div></div><div class="card ${active.length ? "warn" : "ok"}"><div class="num">${active.length}</div><div class="lbl">Активные ремонты</div></div></div><div class="panel"><h3>Заявки на ремонт и ТО</h3>${tbl(["Заявка", "Автобус", "Неисправность", "Пробег", "Критичность", "Статус", "Действия"], rows || `<tr><td colspan="7" class="muted">Заявок пока нет</td></tr>`)}</div>`;
  $("content").innerHTML += `<div class="cards"><div class="card"><div class="num">${repairKpi.active_orders}</div><div class="lbl">активных заказов</div></div><div class="card ${repairKpi.overdue_orders ? "err" : "ok"}"><div class="num">${repairKpi.overdue_orders}</div><div class="lbl">просрочено</div></div><div class="card"><div class="num">${repairKpi.closed_orders}</div><div class="lbl">закрыто ремонтов</div></div><div class="card"><div class="num">${repairKpi.closed_cost}</div><div class="lbl">затраты по закрытым</div></div></div><div class="panel"><h3>Канбан ремонта</h3><div class="toolbar">${Object.entries(repairKpi.kanban).map(([status, rows]) => `<span class="badge b-mut">${esc(status)}: ${rows.length}</span>`).join("")}</div></div>`;
  $("content").innerHTML += `<div class="cards"><div class="card ${repairMetrics.readiness_coefficient < 0.9 ? "warn" : "ok"}"><div class="num">${(repairMetrics.readiness_coefficient * 100).toFixed(1)}%</div><div class="lbl">Коэффициент технической готовности</div></div><div class="card"><div class="num">${repairMetrics.available_buses} / ${repairMetrics.fleet_total}</div><div class="lbl">доступно автобусов</div></div><div class="card"><div class="num">${repairMetrics.total_stage_hours}</div><div class="lbl">часов простоя по этапам</div></div></div><div class="panel"><h3>Простой по этапам</h3><div class="toolbar">${Object.entries(repairMetrics.stage_hours).map(([status, hours]) => `<span class="badge b-mut">${esc(status)}: ${hours} ч</span>`).join("") || `<span class="muted">Данных о простое пока нет</span>`}</div></div>`;
  $("content").innerHTML += `<div class="panel"><h3>Календарь ремонтов и ТО</h3>${tbl(["Событие", "Дата", "Автобус", "Документ / работа", "Статус"], calendarRows || `<tr><td colspan="5" class="muted">На ближайшие 30 дней событий нет</td></tr>`)}</div>`;
  $("content").innerHTML += `<div class="panel"><h3>Заказ-наряды</h3>${tbl(["Номер", "Заявка", "Автобус", "Вид ремонта", "Мастер", "Стоимость", "Статус", "Действия"], orders.map(o => `<tr><td><b>${esc(o.order_number)}</b></td><td>${esc(o.request_number || "")}</td><td>${esc(o.garage_number)} ${esc(o.plate || "")}</td><td>${esc(o.repair_type_name || "")}</td><td>${esc(o.responsible_master_name || "")}</td><td>${esc(o.total_cost || 0)}</td><td>${stBadge(o.status)}</td><td><button class="btn small sec" onclick="repairEditOrder(${o.id})">Редактировать</button> <button class="btn small sec" onclick="repairOperations(${o.id})">Операции</button> <button class="btn small sec" onclick="repairParts(${o.id})">Запчасти</button> <button class="btn small sec" onclick="repairVehicleCard(${o.bus_id})">Карточка автобуса</button> <button class="btn small sec" onclick="repairAttachments(${o.id})">Вложения</button> <button class="btn small sec" onclick="repairWorkers(${o.id})">Исполнители</button> <button class="btn small sec" onclick="openWin('/api/repairs/orders/${o.id}/print')">Печать</button> ${["черновик","диагностика","готов к работе","в работе"].includes(o.status) ? `<button class="btn small" onclick="repairAdvanceOrder(${o.id},'${o.status}')">${o.status === "в работе" ? "На контроль" : "Следующий этап"}</button>` : ""} ${o.status === "контроль" ? `<button class="btn small" onclick="repairInspect(${o.id})">Контрольный осмотр</button>` : ""} ${o.release_allowed ? `<button class="btn small" onclick="repairCloseOrder(${o.id})">Закрыть ремонт</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="8" class="muted">Заказ-нарядов пока нет</td></tr>`)}</div>`;
};
const SUMMARY_TABS = [
  ["by_routes", "По маршрутам"], ["by_outputs", "По выходам"], ["by_drivers", "По водителям"],
  ["by_buses", "По автобусам"], ["by_time", "По времени"], ["errors", "Проверка ошибок"],
  ["export", "Экспорт в Excel"], ["history", "История формирования"],
];
function summaryState() {
  const d = today();
  window._summary = window._summary || { date_from: d, date_to: d, route_id: "", q: "", only_errors: false,
    only_no_driver: false, only_no_bus: false, include_inactive: false, tab: "by_routes", selected_id: 0, data: null, history: [] };
  return window._summary;
}
function summaryLevelClass(level) {
  if (level === "Критическая ошибка") return "sum-critical";
  if (level === "Ошибка") return "sum-error";
  if (level === "Предупреждение") return "sum-warning";
  if (level === "Информация") return "sum-info";
  return "";
}
function summaryFilteredRows(rows) {
  const st = summaryState(), q = (st.q || "").toLowerCase();
  return (rows || []).filter(r => {
    if (st.route_id && String(r.route_id || "") !== String(st.route_id)) return false;
    if (st.only_errors && !r.error_flag) return false;
    if (st.only_no_driver && (r.driver_name || r.driver_id)) return false;
    if (st.only_no_bus && (r.garage_number || r.vehicle_number || r.vehicle_id)) return false;
    return !q || JSON.stringify(r).toLowerCase().includes(q);
  });
}
function summaryTable(headers, rows, mapper) {
  return `<div class="table-scroll"><table class="summary-table"><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr class="${r.error_flag ? "sum-row-error" : ""}">${mapper(r).map(v => `<td>${v == null ? "" : esc(v)}</td>`).join("")}</tr>`).join("") || `<tr><td colspan="${headers.length}" class="muted">Нет данных</td></tr>`}</tbody></table></div>`;
}
async function summaryGenerate() {
  const st = summaryState();
  const body = { date_from: st.date_from, date_to: st.date_to, route_ids: st.route_id ? [+st.route_id] : [], include_inactive: st.include_inactive };
  st.data = await api("/api/summary-schedules/generate", { method: "POST", body });
  st.selected_id = st.data.summary.id;
  toast("Сводное расписание сформировано"); route();
}
async function summaryRecheck() {
  const st = summaryState(); if (!st.selected_id) return;
  st.data = await api(`/api/summary-schedules/${st.selected_id}/check`, { method: "POST" });
  toast("Проверка ошибок выполнена"); route();
}
function summaryExport() {
  const st = summaryState(); if (st.selected_id) openWin(`/api/summary-schedules/${st.selected_id}/export.xlsx`);
}
async function summaryOrder() {
  const st = summaryState(); if (!st.selected_id) return;
  await api(`/api/summary-schedules/${st.selected_id}/order`, { method: "POST", body: { regenerate: true, force: true } });
  toast("Наряд сформирован из сводного расписания"); location.hash = "#/order";
}
VIEWS.summarySchedule = async function () {
  const st = summaryState();
  st.history = (await api(`/api/summary-schedules?date_from=${st.date_from}&date_to=${st.date_to}`)).items || [];
  if (!st.data && st.history[0]) { st.selected_id = st.history[0].id; st.data = await api(`/api/summary-schedules/${st.selected_id}`); }
  const data = st.data || { summary: null, lines: [], errors: [], views: {} }, summary = data.summary || {};
  const tabs = SUMMARY_TABS.map(([k, label]) => `<button class="${st.tab === k ? "on" : ""}" onclick="_summary.tab='${k}'; route()">${label}</button>`).join("");
  const kpi = summary.id ? `<div class="summary-kpis"><div><b>${summary.routes_count || 0}</b><span>маршрутов</span></div><div><b>${summary.trips_count || 0}</b><span>рейсов</span></div><div><b>${summary.runs_count || 0}</b><span>выходов</span></div><div><b>${summary.drivers_count || 0}</b><span>водителей</span></div><div><b>${summary.vehicles_count || 0}</b><span>автобусов</span></div><div class="${summary.errors_count ? "sum-critical" : "sum-ok"}"><b>${summary.errors_count || 0}</b><span>ошибок</span></div></div>` : `<div class="panel muted">Сводное расписание еще не сформировано.</div>`;
  let body = "";
  if (st.tab === "by_routes") body = summaryTable(["Дата", "Маршрут", "Направление", "Рейс", "Выход", "Отправление", "Прибытие", "Автобус", "Водитель", "Примечание"], summaryFilteredRows(data.views.by_routes), r => [r.service_date, `№ ${r.route_number} ${r.route_name || ""}`, r.direction, r.trip_number, r.run_number, r.departure_time, r.arrival_time, r.garage_number || r.vehicle_number, r.driver_name, r.comment]);
  if (st.tab === "by_outputs") body = summaryTable(["Дата", "Маршрут", "Выход", "Смена", "Автобус", "Водитель", "Начало", "Окончание", "Рейсов", "Пробег"], summaryFilteredRows(data.views.by_outputs), r => [r.service_date, `№ ${r.route_number} ${r.route_name || ""}`, r.run_number, r.shift_number, r.garage_number || r.vehicle_number, r.driver_name, r.start_time, r.end_time, r.trips_count, r.distance_km]);
  if (st.tab === "by_drivers") body = summaryTable(["Дата", "Водитель", "Табельный", "Маршрут", "Выход", "Автобус", "Начало", "Окончание"], summaryFilteredRows(data.views.by_drivers), r => [r.service_date, r.driver_name, r.driver_tab_number, `№ ${r.route_number}`, r.run_number, r.garage_number || r.vehicle_number, r.start_time, r.end_time]);
  if (st.tab === "by_buses") body = summaryTable(["Дата", "Автобус", "Госномер", "Маршрут", "Выход", "Водитель", "Выезд", "Заезд", "Рейсов", "Пробег"], summaryFilteredRows(data.views.by_buses), r => [r.service_date, r.garage_number, r.vehicle_number, `№ ${r.route_number}`, r.run_number, r.driver_name, r.start_time, r.end_time, r.trips_count, r.distance_km]);
  if (st.tab === "by_time") body = summaryTable(["Дата", "Время", "Маршрут", "Рейс", "Выход", "Автобус", "Водитель", "Событие"], data.views.by_time || [], r => [r.service_date, r.time, `№ ${r.route_number || ""}`, r.trip_number || "", r.run_number || "", r.garage_number || r.vehicle_number || "", r.driver_name || "", r.event]);
  if (st.tab === "errors") body = `<div class="table-scroll"><table class="summary-table"><thead><tr><th>Уровень</th><th>Маршрут</th><th>Выход</th><th>Рейс</th><th>Объект</th><th>Описание ошибки</th><th>Рекомендация</th></tr></thead><tbody>${(data.errors || []).map(e => `<tr class="${summaryLevelClass(e.level)}"><td>${esc(e.level)}</td><td>${esc(e.route_number || "")}</td><td>${esc(e.run_number || "")}</td><td>${esc(e.trip_number || "")}</td><td>${esc(e.object_label || "")}</td><td>${esc(e.message)}</td><td>${esc(e.recommendation || "")}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">Ошибок нет</td></tr>`}</tbody></table></div>`;
  if (st.tab === "export") body = `<div class="panel"><h3>Экспорт в Excel</h3><p class="muted">Файл содержит титульный лист, сводку и таблицы по маршрутам, выходам, водителям, автобусам, времени, ошибкам и исходным данным.</p><button class="btn" onclick="summaryExport()" ${summary.id ? "" : "disabled"}>Выгрузить в Excel</button></div>`;
  if (st.tab === "history") body = summaryTable(["ID", "Создано", "Период", "Пользователь", "Маршрутов", "Рейсов", "Ошибок", "Статус", "Комментарий"], st.history, r => [r.id, r.created_at, `${r.period_start} — ${r.period_end}`, r.created_by, r.routes_count, r.trips_count, r.errors_count, r.status, r.comment]);
  $("content").innerHTML = `<h2>Сводное расписание</h2><div class="toolbar summary-toolbar">с <input type="date" value="${st.date_from}" onchange="_summary.date_from=this.value; _summary.data=null; route()"> по <input type="date" value="${st.date_to}" onchange="_summary.date_to=this.value; _summary.data=null; route()"><select onchange="_summary.route_id=this.value; route()"><option value="">Все маршруты</option>${REFS.routes.map(r => `<option value="${r.id}" ${String(r.id) === String(st.route_id) ? "selected" : ""}>№ ${esc(r.number)} ${esc(r.name || "")}</option>`).join("")}</select><input placeholder="поиск" value="${esc(st.q)}" onchange="_summary.q=this.value; route()"><label><input type="checkbox" ${st.only_errors ? "checked" : ""} onchange="_summary.only_errors=this.checked; route()"> только ошибки</label><label><input type="checkbox" ${st.only_no_driver ? "checked" : ""} onchange="_summary.only_no_driver=this.checked; route()"> без водителя</label><label><input type="checkbox" ${st.only_no_bus ? "checked" : ""} onchange="_summary.only_no_bus=this.checked; route()"> без автобуса</label></div><div class="toolbar"><button class="btn" onclick="summaryGenerate()">Сформировать сводное расписание</button><button class="btn sec" onclick="summaryRecheck()" ${summary.id ? "" : "disabled"}>Проверить ошибки</button><button class="btn" onclick="summaryOrder()" ${summary.id ? "" : "disabled"}>Сформировать наряд</button><button class="btn sec" onclick="summaryExport()" ${summary.id ? "" : "disabled"}>Выгрузить в Excel</button><button class="btn sec" onclick="_summary.data=null; route()">Обновить</button><button class="btn ghost" onclick="window._summary=null; route()">Очистить фильтры</button><button class="btn sec" onclick="toast('Версия уже сохранена после формирования')" ${summary.id ? "" : "disabled"}>Сохранить версию</button></div>${kpi}<div class="tabs">${tabs}</div>${body}`;
};

VIEWS.dashboard = async function () {
  const d = await api("/api/dashboard");
  const notif = await api("/api/notifications");
  const unseen = notif.items.filter(n => !n.seen);
  $("notif-badge").innerHTML = unseen.length ? `<span class="badge b-err">уведомлений: ${unseen.length}</span>` : "";
  const card = (n, l, cls) => `<div class="card ${cls || ""}"><div class="num">${n}</div><div class="lbl">${l}</div></div>`;
  $("content").innerHTML = `
    <div class="toolbar"><b>Выпуск на ${d.date}</b> ${stBadge(d.order_status)}
      <a class="btn small sec" href="#/order">перейти к наряду →</a></div>
    <div class="cards">
      ${card(d.lines_total, "выходов в наряде")}
      ${card(d.drivers_assigned, "водителей в наряде", "ok")}
      ${card(d.buses_assigned, "автобусов в наряде", "ok")}
      ${card(d.lines_without_driver, "выходов без водителя", d.lines_without_driver ? "err" : "ok")}
      ${card(d.waybills_issued, "путевых листов выдано")}
      ${card(d.waybills_missing, "ПЛ не оформлено", d.waybills_missing ? "warn" : "ok")}
      ${card(d.absent_drivers, "отсутствующих водителей", d.absent_drivers ? "warn" : "")}
      ${card(d.buses_in_repair, "автобусов в ремонте", d.buses_in_repair ? "warn" : "")}
      ${card(d.violations_today, "нарушений РТиО сегодня", d.violations_critical ? "err" : d.violations_today ? "warn" : "ok")}
      ${card(d.overtime_drivers_month, "водителей с переработкой (месяц)", d.overtime_drivers_month ? "warn" : "")}
      ${card(d.fuel_overrun_month + " л", "перерасход топлива (месяц)", d.fuel_overrun_month > 0 ? "warn" : "")}
    </div>
    <div style="display:grid; grid-template-columns: 1fr 1fr; gap:14px">
      <div class="panel"><h3>Истекающие документы (30 дней)</h3>
        ${d.expiring_docs.length ? d.expiring_docs.map(x => `<div class="vio w">${esc(x)}</div>`).join("") : '<span class="badge b-ok">Всё в порядке</span>'}</div>
      <div class="panel"><h3>Уведомления <button class="btn small sec" onclick="api('/api/notifications/seen',{method:'POST'}).then(route)">отметить прочитанными</button></h3>
        ${notif.items.slice(0, 12).map(n => `<div class="vio ${n.level === "error" ? "" : "w"}">
          <b>${esc(n.category || "")} <span class="muted">${esc((n.ts || "").replace("T", " "))}</span></b>${esc(n.message)}</div>`).join("") || '<span class="muted">нет уведомлений</span>'}</div>
    </div>`;  $("content").innerHTML += `<div class="panel"><h3>Планы технического обслуживания</h3>${tbl(["Автобус", "План", "Вид", "Следующая дата", "Следующий пробег", "Текущий пробег"], maintenance.map(p => `<tr><td>${esc(p.garage_number)} ${esc(p.plate || "")}</td><td>${esc(p.name)}</td><td>${esc(p.repair_type_name)}</td><td>${esc(p.next_date || "")}</td><td>${esc(p.next_odometer || "")}</td><td>${esc(p.odometer || 0)}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">Планы ТО не созданы</td></tr>`)}</div>`;

};

/* ================= НАРЯД ================= */
VIEWS.order = async function (dateArg) {
  const date = dateArg || sessionStorage.getItem("order_date") || today();
  sessionStorage.setItem("order_date", date);
  const d = await api("/api/orders?date=" + date);
  const o = d.order;
  const head = `<div class="toolbar">
    <input type="date" id="ord-date" value="${date}" onchange="VIEWS.order(this.value)">
    ${o ? stBadge(o.status) : stBadge("не сформирован")}
    <button class="btn" onclick="orderGen('${date}', ${o ? "true" : "false"})">${o ? "Переформировать" : "Сформировать наряд"}</button>
    ${o ? `<button class="btn" onclick="orderApprove(${o.id})">Утвердить</button>
    <button class="btn sec" onclick="openWin('/api/orders/print?date=${date}')">Печать</button>
    <button class="btn sec" onclick="openWin('/api/orders/export.xlsx?date=${date}')">Excel</button>
    <button class="btn" onclick="wbFromOrder('${date}')">Оформить путевые листы</button>
    <button class="btn sec" onclick="openWin('/api/orders/waybills/print?date=${date}')">Печать всех ПЛ</button>
    <button class="btn" onclick="wbFromOrderAndPrint('${date}')">Сформировать и печатать все ПЛ</button>` : ""}
  </div>`;
  if (!o) { $("content").innerHTML = head + `<div class="panel muted">Наряд на ${date} не сформирован. Наряд собирается из утверждённого расписания, графика водителей, отсутствий и доступных автобусов.</div>`; return; }
  const rows = d.lines.map(l => {
    let wb;
    if (l.status === "отменен") {
      wb = `<button class="btn small ghost" onclick="lineRestore(${l.id})">восстановить</button>`;
    } else if (l.waybill_number) {
      wb = `<a class="btn small ghost" onclick="openWin('/api/waybills/${l.waybill_id}/print')">ПЛ № ${l.waybill_number}</a>`;
    } else if (!l.driver_id || !l.bus_id) {
      wb = `<button class="btn small danger" onclick="lineCancel(${l.id})">снять выход</button>`;
    } else {
      wb = `<button class="btn small sec" onclick="wbCreate(${l.id})">оформить ПЛ</button>`;
    }
    return `<tr>
      <td><b>№ ${esc(l.route_number)}</b><br><span class="muted">${esc(l.route_name || "")}</span></td>
      <td>${l.output_number}/${l.shift_number}</td>
      <td class="cell-btn" onclick="replaceDriver(${l.id}, '${date}')">${l.fio ? esc(l.fio) + `<br><span class="muted">таб. ${esc(l.tab_number)}</span>` : '<span class="badge b-err">нет водителя</span>'}</td>
      <td class="cell-btn" onclick="replaceBus(${l.id})">${l.garage_number ? esc(l.garage_number) + " · " + esc(l.plate || "") : '<span class="badge b-err">нет автобуса</span>'}</td>
      <td>${esc(l.report_time)}</td><td>${esc(l.depart_depot)}</td>
      <td>${esc(l.start_line)}–${esc(l.end_line)}</td><td>${esc(l.return_depot)}</td>
      <td>${l.shift_hours}</td><td>${l.trips_count}</td><td>${l.distance_km}</td><td>${l.planned_fuel}</td>
      <td>${stBadge(l.status)}</td><td>${wb}</td>
      <td class="cell-btn" onclick="lineNote(${l.id}, '${esc(l.dispatcher_note || "")}')">${esc(l.dispatcher_note || "＋")}</td></tr>`;
  }).join("");
  $("content").innerHTML = head +
    tbl(["Маршрут", "Вых/см", "Водитель", "Автобус", "Явка", "Выезд", "На линии", "Заезд", "Часы", "Рейсов", "Км", "Топл. план", "Статус", "Путевой лист", "Отметки"], rows);
};
async function orderGen(date, regen) {
  try {
    const r = await api("/api/orders/generate", { method: "POST", body: { date, regenerate: regen, force: regen } });
    toast(`Наряд сформирован: строк ${r.lines}` + (r.warnings.length ? `\nПредупреждения:\n• ` + r.warnings.slice(0, 6).join("\n• ") : ""));
    VIEWS.order(date);
  } catch (e) { toast(e.message, true); }
}
async function orderApprove(oid) {
  try {
    await api(`/api/orders/${oid}/status`, { method: "POST", body: { status: "утвержден" } });
    toast("Наряд утверждён"); route();
  } catch (e) {
    const c = await textModal("Обоснование утверждения", "Обоснование", "", "Утверждение заблокировано:\n" + e.message);
    if (c) {
      await api(`/api/orders/${oid}/status`, { method: "POST", body: { status: "утвержден", force_comment: c } })
        .then(() => { toast("Утверждено с обоснованием"); route(); }).catch(er => toast(er.message, true));
    }
  }
}
async function lineCancel(lid) {
  const reason = await textModal("Снять выход", "Причина", "", "Например: нехватка водителей или автобусов");
  if (!reason) return;
  await api(`/api/orders/line/${lid}`, { method: "PUT", body: { status: "отменен", dispatcher_note: "снят: " + reason } })
    .catch(e => toast(e.message, true));
  toast("Выход снят — попадёт в отчёт по срывам выпуска"); route();
}
async function lineRestore(lid) {
  await api(`/api/orders/line/${lid}`, { method: "PUT", body: { status: "план", dispatcher_note: "" } })
    .catch(e => toast(e.message, true));
  toast("Выход восстановлен"); route();
}
async function lineNote(lid, cur) {
  const v = await textModal("Отметка диспетчера", "Отметка", cur || "");
  if (v === null) return;
  await api(`/api/orders/line/${lid}`, { method: "PUT", body: { dispatcher_note: v } });
  route();
}
async function replaceDriver(lid, date) {
  const c = await api(`/api/orders/candidates?date=${date}&line_id=${lid}`);
  const rows = c.items.map(x => {
    const vio = x.violations && x.violations.length
      ? `<span class="badge b-warn" title="${esc(x.violations.map(v => v.type).join("; "))}">424: ${x.violations.length}</span>` : "";
    const crit = x.violations && x.violations.some(v => v.severity === "критично");
    return `<tr><td>${esc(x.fio)}</td><td>${esc(x.tab_number)}</td><td>${esc(x.roster_status)}</td>
      <td>${x.blocked ? `<span class="badge b-err">${esc(x.reason)}</span>` : vio || '<span class="badge b-ok">доступен</span>'}</td>
      <td>${x.blocked ? "" : `<button class="btn small ${crit ? "danger" : ""}" onclick="setDriver(${lid}, ${x.id})">назначить</button>`}</td></tr>`;
  }).join("");
  await modal(`<h3>Замена водителя</h3><div class="tbl-wrap" style="max-height:420px">
    <table class="grid"><thead><tr><th>ФИО</th><th>Таб.№</th><th>По графику</th><th>Доступность</th><th></th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <div class="foot"><button class="btn sec" data-act="cancel">Закрыть</button></div>`);
}
async function setDriver(lid, did) {
  document.querySelector(".modal-bg") && document.querySelector(".modal-bg").remove();
  await api(`/api/orders/line/${lid}`, { method: "PUT", body: { driver_id: did } }).catch(e => toast(e.message, true));
  toast("Водитель назначен (замена зафиксирована в аудите)"); route();
}
async function replaceBus(lid) {
  const free = REFS.buses.filter(b => ["исправен", "резерв"].includes(b.status));
  const v = await formModal("Замена автобуса", [
    { k: "bus_id", label: "Автобус", type: "select", options: free.map(b => [b.id, `${b.garage_number} · ${b.plate || ""} · ${b.brand} ${b.model} (${b.status})`]) }]);
  if (!v) return;
  await api(`/api/orders/line/${lid}`, { method: "PUT", body: { bus_id: +v.bus_id } }).catch(e => toast(e.message, true));
  toast("Автобус назначен"); route();
}
async function wbCreate(lid) {
  try {
    const pc = await api(`/api/waybills/precheck/${lid}`);
    if (pc.problems.length) { toast("Нельзя оформить ПЛ:\n• " + pc.problems.join("\n• "), true); return; }
    const r = await api(`/api/waybills/from-line/${lid}`, { method: "POST" });
    let msg = `Путевой лист № ${r.number} оформлен`;
    if (r.warnings && r.warnings.length) msg += `\nПредупреждения:\n• ` + r.warnings.join("\n• ");
    toast(msg);
    route();
  } catch (e) { toast(e.message, true); }
}
async function wbFromOrder(date, stay = false) {
  try {
    const r = await api(`/api/waybills/from-order/${date}`, { method: "POST" });
    let msg = `Оформлено путевых листов: ${r.created.length}`;
    if (r.warnings && r.warnings.length) msg += `\nПредупреждения: ${r.warnings.length}\n• ` +
      r.warnings.slice(0, 5).map(b => `${b.line} ${b.fio || ""}: ${b.warnings.join(", ")}`).join("\n• ");
    if (r.blocked.length) msg += `\nЗаблокировано: ${r.blocked.length}\n• ` +
      r.blocked.slice(0, 5).map(b => `${b.line} ${b.fio || ""}: ${b.problems.join(", ")}`).join("\n• ");
    toast(msg, r.blocked.length > 0);
    if (!stay) route();
    return r;
  } catch (e) { toast(e.message, true); return null; }
}
async function wbFromOrderAndPrint(date) {
  const r = await wbFromOrder(date, true);
  if (!r) return;
  openWin(`/api/orders/waybills/print?date=${date}`);
  route();
}

/* ================= ПУТЕВЫЕ ЛИСТЫ ================= */
VIEWS.waybills = async function () {
  const f = window._wbf || { date_from: today(), date_to: today(), status: "", number: "" };
  window._wbf = f;
  const d = await api(`/api/waybills?date_from=${f.date_from}&date_to=${f.date_to}&status=${encodeURIComponent(f.status)}&number=${f.number}`);
  const rows = d.items.map(w => `<tr>
    <td><b>${w.number}</b></td><td>${esc(w.date)}</td>
    <td>${esc(w.fio || "")}<br><span class="muted">таб. ${esc(w.tab_number || "")}</span></td>
    <td>${esc(w.garage_number || "")} · ${esc(w.plate || "")}</td>
    <td>№ ${esc(w.route_number || "")} / вых. ${esc(w.output_number)}</td>
    <td>${esc(w.depart_fact || w.depart_plan || "")} – ${esc(w.return_fact || w.return_plan || "")}</td>
    <td>${w.distance != null ? w.distance : ""}</td><td>${w.fuel_fact != null ? w.fuel_fact : ""}</td>
    <td>${stBadge(w.status)}${w.cancel_reason ? `<br><span class="muted">${esc(w.cancel_reason)}</span>` : ""}</td>
    <td>${esc(w.created_by || "")}<br><span class="muted">печатей: ${w.print_count}</span></td>
    <td style="white-space:nowrap">
      <button class="btn small sec" onclick="openWin('/api/waybills/${w.id}/print')">печать</button>
      <button class="btn small ghost" onclick="openWin('/api/waybills/${w.id}/print?duplicate=1')">дубликат</button>
      ${w.status === "оформлен" ? `<button class="btn small" onclick="wbClose(${w.id}, ${w.odo_start || 0}, ${w.fuel_start || 0})">закрыть</button>` : ""}
      ${w.status !== "аннулирован" ? `<button class="btn small danger" onclick="wbCancel(${w.id})">аннулир.</button>` : ""}
    </td></tr>`).join("");
  $("content").innerHTML = `<div class="toolbar">
      с <input type="date" value="${f.date_from}" onchange="_wbf.date_from=this.value; route()">
      по <input type="date" value="${f.date_to}" onchange="_wbf.date_to=this.value; route()">
      <select onchange="_wbf.status=this.value; route()">
        <option value="">все статусы</option>
        ${["оформлен", "выполнен", "аннулирован"].map(s => `<option ${f.status === s ? "selected" : ""}>${s}</option>`).join("")}
      </select>
      № <input style="width:90px" value="${f.number}" onchange="_wbf.number=this.value; route()">
      <button class="btn sec" onclick="openWin('/api/waybills/export.xlsx?date_from=${f.date_from}&date_to=${f.date_to}')">Журнал в Excel</button>
      ${d.numbering_gaps.length ? `<span class="badge b-err">нарушена сквозная нумерация, пропуски: ${d.numbering_gaps.join(", ")}</span>` : `<span class="badge b-ok">сквозная нумерация в порядке</span>`}
    </div>` +
    tbl(["№ ПЛ", "Дата", "Водитель", "Автобус", "Маршрут", "Выезд–возврат", "Пробег, км", "Расход, л", "Статус", "Оформил", "Действия"], rows);
};
async function wbClose(wid, odoStart, fuelStart) {
  const v = await formModal("Закрытие путевого листа", [
    { k: "return_fact", label: "Фактическое время возвращения", type: "time" },
    { k: "odo_end", label: `Одометр при возвращении (при выезде: ${odoStart})`, type: "number", step: "0.1" },
    { k: "fuel_given", label: "Выдано топлива, л", type: "number", step: "0.1", def: 0 },
    { k: "fuel_end", label: `Остаток топлива при возвращении, л (при выезде: ${fuelStart})`, type: "number", step: "0.1" },
    { k: "comment", label: "Примечание", type: "text" }]);
  if (!v) return;
  try {
    const r = await api(`/api/waybills/${wid}/close`, { method: "PUT", body: v });
    toast(`ПЛ закрыт. Пробег ${r.distance} км, расход факт ${r.fuel_fact ?? "—"} л (норма ${r.plan} л), ` +
      (r.overrun ? `перерасход ${r.overrun} л` : `экономия ${r.saving} л`), !!r.overrun);
    route();
  } catch (e) { toast(e.message, true); }
}
async function wbCancel(wid) {
  const reason = await textModal("Аннулировать путевой лист", "Причина аннулирования");
  if (!reason) return;
  await api(`/api/waybills/${wid}/cancel`, { method: "POST", body: { reason } }).catch(e => toast(e.message, true));
  toast("Путевой лист аннулирован"); route();
}

/* ================= ГРАФИК ВОДИТЕЛЕЙ ================= */
function monthDays(month) {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m, 0).getDate();
}
VIEWS.roster = async function () {
  const month = window._rosterMonth || thisMonth();
  window._rosterMonth = month;
  const days = monthDays(month);
  const from = month + "-01", to = month + "-" + String(days).padStart(2, "0");
  const d = await api(`/api/roster?date_from=${from}&date_to=${to}`);
  const byDrv = {};
  d.items.forEach(r => { (byDrv[r.driver_id] = byDrv[r.driver_id] || { fio: r.fio, tab: r.tab_number, days: {} }).days[r.date] = r; });
  const rows = Object.entries(byDrv).sort((a, b) => a[1].fio.localeCompare(b[1].fio, "ru")).map(([did, drv]) => {
    let cells = "", tot = 0;
    for (let i = 1; i <= days; i++) {
      const iso = `${month}-${String(i).padStart(2, "0")}`;
      const e = drv.days[iso];
      let cls = "rc-off", txt = "·", tip = "";
      if (e) {
        if (e.status === "работа") {
          cls = "rc-work";
          const ac = +(e.assignment_count || 0);
          txt = ac > 1 ? `${e.route_number || ""}<br>${ac} см.` : `${e.route_number || ""}<br>${e.output_number || ""}/${e.shift_number || ""}`;
          tip = `${e.assignment_label || ""} ${e.start_time || ""}–${e.end_time || ""} (${e.hours} ч)`;
          tot += e.hours || 0;
        }
        else if (e.status === "выходной") { txt = "В"; }
        else if (e.status === "РЗ") { cls = "rc-rz"; txt = "РЗ"; }
        else { cls = "rc-abs"; txt = esc(e.status); tip = e.comment || ""; }
      }
      cells += `<td class="roster-cell ${cls}" title="${esc(tip)}" onclick="rosterCell(${did}, '${iso}')">${txt}</td>`;
    }
    return `<tr><td style="white-space:nowrap"><b>${esc(drv.fio)}</b><br><span class="muted">таб. ${esc(drv.tab)}</span></td>${cells}<td><b>${tot.toFixed(1)}</b></td></tr>`;
  }).join("");
  $("content").innerHTML = `<div class="toolbar">
      <input type="month" value="${month}" onchange="_rosterMonth=this.value; route()">
      <button class="btn" onclick="rosterGen('${from}','${to}')">Сформировать график</button>
      <button class="btn sec" onclick="rosterCheck('${from}','${to}')">Проверить (Приказ 424)</button>
      <button class="btn sec" onclick="openWin('/api/roster/export.xlsx?month=${month}')">График в Excel</button>
      <button class="btn" onclick="rosterApprove('${from}','${to}')">Утвердить график</button>
      <button class="btn sec" onclick="openWin('/api/timesheet/export.xlsx?month=${month}')">Экспорт в Excel</button>
      <span class="muted">клик по ячейке — редактирование дня</span>
    </div>
    <div id="roster-vio"></div>` +
    tbl(["Водитель", ...Array.from({ length: days }, (_, i) => String(i + 1)), "Часы"], rows);
};
async function rosterGen(from, to) {
  const v = await formModal("Автоформирование графика", [
    { k: "date_from", label: "С", type: "date", def: from },
    { k: "date_to", label: "По", type: "date", def: to },
    { k: "template", label: "Шаблон", type: "select", empty: true,
      options: [["", "по графику водителя (по умолчанию)"], "2/2", "5/2", "6/1", "3/1", "4/2", "1/1"] },
    { k: "route_id", label: "Маршрут (пусто — закреплённый)", type: "select", empty: true,
      options: REFS.routes.map(r => [r.id, "№ " + r.number + " " + (r.name || "")]) },
    { k: "overwrite", label: "Перезаписать существующие дни (1 — да)", type: "number", def: 0 }],
    {}, "Учитываются отсутствия, закреплённые маршруты и автобусы. Утверждённые дни не перезаписываются без флага.");
  if (!v) return;
  v.route_id = v.route_id ? +v.route_id : null;
  v.overwrite = !!+v.overwrite;
  try {
    const r = await api("/api/roster/generate", { method: "POST", body: v });
    toast(`График сформирован: заполнено дней ${r.made}`); route();
  } catch (e) { toast(e.message, true); }
}
async function rosterCheck(from, to) {
  const r = await api(`/api/roster/check?date_from=${from}&date_to=${to}`);
  $("roster-vio").innerHTML = `<div class="panel"><h3>Проверка по Приказу Минтранса № 424 — найдено: ${r.violations.length}</h3>${violList(r.violations)}</div>`;
}
async function rosterApprove(from, to) {
  try {
    const r = await api("/api/roster/approve", { method: "POST", body: { date_from: from, date_to: to } });
    toast(`График утверждён. Нарушений: ${r.violations}, критичных: ${r.critical}`);
  } catch (e) {
    const c = await textModal("Обоснование утверждения графика", "Обоснование", "", e.message);
    if (c) await api("/api/roster/approve", { method: "POST", body: { date_from: from, date_to: to, force_comment: c } })
      .then(r => toast("Утверждено с обоснованием")).catch(er => toast(er.message, true));
  }
}
function rosterViolationHtml(violations) {
  if (!violations || !violations.length) return "";
  return `<div class="vio roster-warning"><button class="btn small ghost" data-act="close-warning">закрыть</button>
    <b>Сохранено, но есть нарушения РТиО</b>
    ${violations.map(v => `<div>${sevBadge(v.severity)} ${esc(v.type)}: факт ${esc(v.fact_value)}, норма ${esc(v.norm_value)}<br><span class="muted">${esc(v.recommendation || "")}</span></div>`).join("")}</div>`;
}

function rosterAssignmentRows(items) {
  if (!items.length) return `<div class="muted">Назначений на день пока нет.</div>`;
  return items.map(a => `<div class="assign-row">
    <div><b>№ ${esc(a.route_number || "")} · вых. ${esc(a.output_number)} / см. ${esc(a.shift_number)}</b><br>
      <span class="muted">рейсы ${esc(a.trip_from || "")}–${esc(a.trip_to || "")} · ${esc(a.start_time)}–${esc(a.end_time)} · ${esc(a.hours)} ч · ${esc(a.distance_km)} км</span></div>
    <div><button class="btn small ghost" data-act="edit-assignment" data-id="${a.id}">изм.</button>
      <button class="btn small ghost" data-act="delete-assignment" data-id="${a.id}">✕</button></div>
  </div>`).join("");
}

async function rosterCell(did, iso) {
  const driverId = +did;
  let cur = (await api(`/api/roster?date_from=${iso}&date_to=${iso}&driver_id=${driverId}`)).items[0] || { status: "работа" };
  let assignments = (await api(`/api/roster/assignments?driver_id=${driverId}&date=${iso}`)).items;
  let editing = assignments.length ? null : { route_id: cur.route_id || (REFS.routes[0] && REFS.routes[0].id), output_number: 1, shift_number: 1 };
  let violations = [];
  const bg = document.createElement("div");
  bg.className = "modal-bg";
  document.body.appendChild(bg);

  const selected = (a, b) => String(a || "") === String(b || "") ? "selected" : "";
  const statusOptions = [["работа", "работа"], ["выходной", "выходной"], ["РЗ", "резерв"], ...REFS.absence_types.map(t => [t.code, t.name])];

  function formValues() {
    const out = {};
    bg.querySelectorAll("[data-k]").forEach(i => { out[i.dataset.k] = i.value; });
    return out;
  }

  function render() {
    const e = editing || {};
    const routeId = e.route_id || cur.route_id || (REFS.routes[0] && REFS.routes[0].id) || "";
    const showEditor = editing !== null;
    bg.innerHTML = `<div class="modal roster-modal">
      <h3>${esc(drvName(driverId))} — ${iso}</h3>
      ${rosterViolationHtml(violations)}
      <div class="cols">
        <label class="f">Статус дня<select data-k="day_status">${statusOptions.map(([v,l]) => `<option value="${esc(v)}" ${selected(v, cur.status || "работа")}>${esc(l)}</option>`).join("")}</select></label>
        <label class="f">Комментарий дня<input data-k="day_comment" value="${esc(cur.comment || "")}"></label>
      </div>
      <div class="assign-head"><h3>Назначения на день</h3><button class="btn small" data-act="new-assignment">+ смена</button></div>
      <div class="assign-list">${rosterAssignmentRows(assignments)}</div>
      <div class="assign-editor" style="display:${showEditor ? "block" : "none"}">
        <h3>${e.id ? "Редактирование смены" : "Новая смена"}</h3>
        <div class="cols">
          <label class="f">Маршрут<select data-k="route_id">${REFS.routes.map(r => `<option value="${r.id}" ${selected(r.id, routeId)}>№ ${esc(r.number)} ${esc(r.name || "")}</option>`).join("")}</select></label>
          <label class="f">Выход<select data-k="output_number"><option value="${esc(e.output_number || 1)}">${esc(e.output_number || 1)}</option></select></label>
          <label class="f">Смена<select data-k="shift_number"><option value="${esc(e.shift_number || 1)}">${esc(e.shift_number || 1)}</option></select></label>
          <label class="f">С рейса<select data-k="trip_from"><option value="${esc(e.trip_from || "")}">${esc(e.trip_from || "вся смена")}</option></select></label>
          <label class="f">По рейс<select data-k="trip_to"><option value="${esc(e.trip_to || "")}">${esc(e.trip_to || "вся смена")}</option></select></label>
          <label class="f">Начало<input data-k="start_time" type="time" value="${esc(e.start_time || "")}"></label>
          <label class="f">Окончание<input data-k="end_time" type="time" value="${esc(e.end_time || "")}"></label>
          <label class="f">Комментарий<input data-k="comment" value="${esc(e.comment || "")}"></label>
        </div>
        <div id="assignment-preview" class="muted">Выберите маршрут, выход и смену.</div>
        <div class="foot"><button class="btn sec" data-act="cancel-edit">Отмена</button><button class="btn" data-act="save-assignment">Сохранить смену</button></div>
      </div>
      <div class="foot"><button class="btn sec" data-act="cancel">Закрыть</button><button class="btn sec" data-act="save-day">Сохранить статус дня</button></div>
    </div>`;
    if (showEditor) refreshScheduleOptions(!e.id && !e.start_time);
  }

  async function reloadDay() {
    cur = (await api(`/api/roster?date_from=${iso}&date_to=${iso}&driver_id=${driverId}`)).items[0] || { status: "работа" };
    assignments = (await api(`/api/roster/assignments?driver_id=${driverId}&date=${iso}`)).items;
  }

  function fillSelect(sel, values, current) {
    const unique = [];
    values.forEach(v => { if (!unique.includes(v)) unique.push(v); });
    sel.innerHTML = unique.map(v => `<option value="${esc(v)}" ${selected(v, current)}>${esc(v)}</option>`).join("");
  }

  async function refreshScheduleOptions(applySuggestion = true) {
    const route = bg.querySelector('[data-k="route_id"]');
    const output = bg.querySelector('[data-k="output_number"]');
    const shift = bg.querySelector('[data-k="shift_number"]');
    if (!route || !route.value) return;
    const outVal = +(output && output.value || 0);
    const shVal = +(shift && shift.value || 0);
    const fromVal = +(bg.querySelector('[data-k="trip_from"]')?.value || 0);
    const toVal = +(bg.querySelector('[data-k="trip_to"]')?.value || 0);
    let data = await api(`/api/roster/schedule-options?route_id=${route.value}&date=${iso}&output_number=${outVal || 0}&shift_number=${shVal || 0}&trip_from=${fromVal || 0}&trip_to=${toVal || 0}`);
    const outputs = data.outputs || [];
    if (output) fillSelect(output, outputs.map(o => o.output_number), outVal || (outputs[0] && outputs[0].output_number) || 1);
    const selectedOutput = +(output && output.value || 0);
    const shifts = outputs.filter(o => !selectedOutput || o.output_number === selectedOutput).map(o => o.shift_number);
    if (shift) fillSelect(shift, shifts.length ? shifts : [shVal || 1], shVal || shifts[0] || 1);
    const selectedShift = +(shift && shift.value || 0);
    if (!data.trips.length && selectedOutput && selectedShift) {
      data = await api(`/api/roster/schedule-options?route_id=${route.value}&date=${iso}&output_number=${selectedOutput}&shift_number=${selectedShift}&trip_from=${fromVal || 0}&trip_to=${toVal || 0}`);
    }
    const tripNums = (data.trips || []).map(t => t.trip_number).filter(v => v != null);
    const tripFrom = bg.querySelector('[data-k="trip_from"]');
    const tripTo = bg.querySelector('[data-k="trip_to"]');
    if (tripFrom) fillSelect(tripFrom, tripNums.length ? tripNums : [""], fromVal || (data.suggestion && data.suggestion.trip_from) || "");
    if (tripTo) fillSelect(tripTo, tripNums.length ? tripNums : [""], toVal || (data.suggestion && data.suggestion.trip_to) || "");
    if (applySuggestion && data.suggestion) {
      const s = data.suggestion;
      if (bg.querySelector('[data-k="start_time"]')) bg.querySelector('[data-k="start_time"]').value = s.start_time || "";
      if (bg.querySelector('[data-k="end_time"]')) bg.querySelector('[data-k="end_time"]').value = s.end_time || "";
    }
    const s = data.suggestion || {};
    const preview = bg.querySelector('#assignment-preview');
    if (preview) preview.innerHTML = `Тип дня: ${esc(data.day_type || "")} · рейсов: ${esc(s.trips_count || 0)} · ${esc(s.start_time || "--:--")}–${esc(s.end_time || "--:--")} · ${esc(s.hours || 0)} ч · ${esc(s.distance_km || 0)} км`;
  }

  bg.addEventListener("change", async (ev) => {
    const k = ev.target.dataset.k;
    if (["route_id", "output_number", "shift_number", "trip_from", "trip_to"].includes(k)) {
      await refreshScheduleOptions(true).catch(e => toast(e.message, true));
    }
  });

  bg.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "cancel") { bg.remove(); route(); return; }
    if (act === "close-warning") { violations = []; render(); return; }
    if (act === "new-assignment") { editing = { route_id: cur.route_id || (REFS.routes[0] && REFS.routes[0].id), output_number: 1, shift_number: 1 }; render(); return; }
    if (act === "cancel-edit") { editing = null; render(); return; }
    if (act === "edit-assignment") { editing = assignments.find(a => String(a.id) === String(b.dataset.id)) || null; render(); return; }
    if (act === "delete-assignment") {
      if (!confirm("Удалить назначение смены?")) return;
      await api(`/api/roster/assignment/${b.dataset.id}`, { method: "DELETE" });
      await reloadDay(); editing = null; violations = []; render(); return;
    }
    if (act === "save-day") {
      const v = formValues();
      await api("/api/roster/entry", { method: "POST", body: { driver_id: driverId, date: iso, status: v.day_status, comment: v.day_comment } });
      await reloadDay(); toast("Статус дня сохранён"); render(); return;
    }
    if (act === "save-assignment") {
      const v = formValues();
      const payload = {
        id: editing && editing.id,
        driver_id: driverId,
        date: iso,
        route_id: +v.route_id,
        output_number: +v.output_number,
        shift_number: +v.shift_number,
        trip_from: +v.trip_from || null,
        trip_to: +v.trip_to || null,
        start_time: v.start_time,
        end_time: v.end_time,
        comment: v.comment || "",
      };
      const r = await api("/api/roster/assignment", { method: "POST", body: payload });
      violations = r.violations || [];
      await reloadDay(); editing = null; toast("Назначение сохранено"); render(); return;
    }
  });

  render();
}

/* ================= РАСПИСАНИЯ ================= */
function normalizeBreakType(value) {
  const v = String(value || "").trim();
  const aliases = {
    "\u043e\u0431\u0435\u0434/\u043f\u0435\u0440\u0435\u0441\u043c\u0435\u043d\u043a\u0430": "\u043e\u0431\u0435\u0434",
    "\u0442\u0435\u0445\u043d\u043e\u043b\u043e\u0433\u0438\u0447\u0435\u0441\u043a\u0438\u0439": "\u0442\u0435\u0445\u043d\u043e\u043b\u043e\u0433\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u043f\u0435\u0440\u0435\u0440\u044b\u0432"
  };
  return aliases[v] || v;
}

function breakClass(value) {
  const v = normalizeBreakType(value);
  if (v === "\u043e\u0431\u0435\u0434") return " lunch";
  if (v === "\u0440\u0430\u0437\u0440\u044b\u0432") return " split";
  if (v === "\u0442\u0435\u0445\u043d\u043e\u043b\u043e\u0433\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u043f\u0435\u0440\u0435\u0440\u044b\u0432") return " tech";
  return "";
}

function scheduleStatusBadge(s) {
  if (!s || !s.trips_count) return `<span class="badge b-mut">\u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043f\u0443\u0441\u0442\u043e\u0435</span>`;
  if (s.critical_count) return `<span class="badge b-err">\u043a\u0440\u0438\u0442\u0438\u0447\u043d\u043e: ${s.critical_count}</span>`;
  if (s.error_count) return `<span class="badge b-err">\u043e\u0448\u0438\u0431\u043a\u0438: ${s.error_count}</span>`;
  if (s.warning_count) return `<span class="badge b-warn">\u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u044f: ${s.warning_count}</span>`;
  return `<span class="badge b-ok">\u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u043e</span>`;
}

function scheduleCards(s) {
  const card = (n, l, cls) => `<div class="card ${cls || ""}"><div class="num">${esc(n)}</div><div class="lbl">${esc(l)}</div></div>`;
  return `<div class="cards schedule-kpis">
    ${card(s.trips_count || 0, "\u0440\u0435\u0439\u0441\u043e\u0432")}
    ${card(s.outputs_count || 0, "\u0432\u044b\u0445\u043e\u0434\u043e\u0432")}
    ${card(s.bus_need || 0, "\u0430\u0432\u0442\u043e\u0431\u0443\u0441\u043e\u0432 \u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f")}
    ${card(s.driver_need || 0, "\u0432\u043e\u0434\u0438\u0442\u0435\u043b\u0435\u0439 \u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f")}
    ${card((s.distance_km || 0) + " \u043a\u043c", "\u043f\u043b\u0430\u043d\u043e\u0432\u044b\u0439 \u043f\u0440\u043e\u0431\u0435\u0433")}
    ${card((s.first_dep || "\u2014") + "\u2013" + (s.last_arr || "\u2014"), "\u043f\u0435\u0440\u0438\u043e\u0434 \u0434\u0432\u0438\u0436\u0435\u043d\u0438\u044f")}
    ${card(s.problems_count || 0, "\u0437\u0430\u043c\u0435\u0447\u0430\u043d\u0438\u0439", s.critical_count || s.error_count ? "err" : s.warning_count ? "warn" : "ok")}
  </div>`;
}

function tripProblemMap(problems) {
  const out = {};
  (problems || []).forEach(p => {
    if (p.trip_id) out[p.trip_id] = p;
  });
  return out;
}

function scheduleTimeline(trips, problems) {
  if (!trips.length) return `<div class="muted">\u041d\u0435\u0442 \u0440\u0435\u0439\u0441\u043e\u0432 \u0434\u043b\u044f \u0432\u0440\u0435\u043c\u0435\u043d\u043d\u043e\u0439 \u0448\u043a\u0430\u043b\u044b.</div>`;
  const bad = tripProblemMap(problems);
  const byOut = {};
  trips.forEach(t => { (byOut[t.output_number] = byOut[t.output_number] || []).push(t); });
  const toMin = (v) => { const p = String(v || "00:00").split(":"); return (+p[0]) * 60 + (+p[1]); };
  const minT = Math.min(...trips.map(t => toMin(t.dep_time)));
  const maxT = Math.max(...trips.map(t => toMin(t.arr_time)));
  const span = Math.max(60, maxT - minT);
  const hours = [];
  for (let h = Math.floor(minT / 60); h <= Math.ceil(maxT / 60); h++) hours.push(h % 24);
  const scaleStyle = `grid-template-columns:82px repeat(${hours.length}, minmax(72px, 1fr))`;
  return `<div class="timeline">
    <div class="timeline-scale" style="${scaleStyle}"><span></span>${hours.map(h => `<b>${String(h).padStart(2, "0")}:00</b>`).join("")}</div>
    ${Object.entries(byOut).map(([out, list]) => `<div class="timeline-row">
      <div class="timeline-label">\u0412\u044b\u0445. ${esc(out)}</div>
      <div class="timeline-track">${list.map(t => {
        const left = Math.max(0, (toMin(t.dep_time) - minT) / span * 100);
        const width = Math.max(4, (toMin(t.arr_time) - toMin(t.dep_time)) / span * 100);
        const cls = bad[t.id] ? " bad" : breakClass(t.break_type);
        return `<button class="timeline-trip${cls}" style="left:${left}%;width:${width}%"
          title="\u0420\u0435\u0439\u0441 ${esc(t.trip_number)} ${esc(t.dep_time)}\u2013${esc(t.arr_time)}"
          onclick='tripEdit(${JSON.stringify(t).replace(/'/g, "&#39;")})'>
          ${esc(t.trip_number)} \u00b7 ${esc(t.dep_time)}
        </button>`;
      }).join("")}</div>
    </div>`).join("")}
  </div>`;
}

VIEWS.schedule = async function () {
  const st = window._sched || { route_id: REFS.routes[0] ? REFS.routes[0].id : 0, day_type: "\u0431\u0443\u0434\u043d\u0438", q: "" };
  window._sched = st;
  if (!st.route_id) { $("content").innerHTML = "<div class='panel'>\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0441\u043e\u0437\u0434\u0430\u0439\u0442\u0435 \u043c\u0430\u0440\u0448\u0440\u0443\u0442 \u0432 \u0441\u043f\u0440\u0430\u0432\u043e\u0447\u043d\u0438\u043a\u0435.</div>"; return; }
  const [tr, chk, sum] = await Promise.all([
    api(`/api/trips?route_id=${st.route_id}&day_type=${encodeURIComponent(st.day_type)}`),
    api(`/api/routes/${st.route_id}/check?day_type=${encodeURIComponent(st.day_type)}`),
    api(`/api/routes/${st.route_id}/schedule-summary?day_type=${encodeURIComponent(st.day_type)}`)]);
  const q = (st.q || "").toLowerCase();
  const problemsByTrip = tripProblemMap(chk.problems);
  const visibleTrips = tr.items.filter(t => !q || JSON.stringify(t).toLowerCase().includes(q));
  const outs = chk.outputs.map(o => `<tr><td>${o.output_number}</td><td>${o.shift_number}</td><td>${o.start}\u2013${o.end}</td>
    <td>${o.trips}</td><td>${o.distance}</td><td>${o.hours}</td><td>${o.night_hours}</td></tr>`).join("");
  const trips = visibleTrips.map(t => {
    const p = problemsByTrip[t.id];
    const cls = p ? (p.severity === "\u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435" ? "trip-row-warning" : "trip-row-error") : "";
    const btype = normalizeBreakType(t.break_type);
    return `<tr class="${cls}">
      <td>${t.output_number}</td><td>${t.shift_number}</td><td>${t.trip_number}</td><td>${esc(t.direction)}</td>
      <td>${t.dep_time}</td><td>${t.arr_time}</td><td>${t.distance_km}</td>
      <td>${t.break_after_min || 0}${btype ? " (" + esc(btype) + ")" : ""}</td>
      <td>${p ? sevBadge(p.severity) + " " + esc(p.kind) : '<span class="badge b-ok">ok</span>'}</td>
      <td><button class="btn small ghost" onclick='tripEdit(${JSON.stringify(t).replace(/'/g, "&#39;")})'>\u0438\u0437\u043c.</button>
          <button class="btn small ghost" onclick="tripDel(${t.id})">\u2715</button></td></tr>`;
  }).join("");
  $("content").innerHTML = `<div class="schedule-hero">
      <div class="toolbar">
        <select onchange="_sched.route_id=+this.value; route()">
          ${REFS.routes.map(r => `<option value="${r.id}" ${r.id === st.route_id ? "selected" : ""}>\u2116 ${esc(r.number)} \u2014 ${esc(r.name || "")} (${esc(r.comm_type)})</option>`).join("")}
        </select>
        <div class="tabs" style="margin:0; border:none">
          ${["\u0431\u0443\u0434\u043d\u0438", "\u0441\u0443\u0431\u0431\u043e\u0442\u0430", "\u0432\u043e\u0441\u043a\u0440\u0435\u0441\u0435\u043d\u044c\u0435"].map(t => `<button class="${st.day_type === t ? "on" : ""}" onclick="_sched.day_type='${t}'; route()">${t}</button>`).join("")}
        </div>
        ${scheduleStatusBadge(sum)}
        <input placeholder="\u043f\u043e\u0438\u0441\u043a \u043f\u043e \u0440\u0435\u0439\u0441\u0430\u043c\u2026" value="${esc(st.q || "")}" onchange="_sched.q=this.value; route()">
      </div>
      <div class="toolbar">
        <button class="btn" onclick="schedGen()">\u0421\u0433\u0435\u043d\u0435\u0440\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435</button>
        <button class="btn sec" onclick="tripEdit({route_id:${st.route_id}, day_type:'${st.day_type}', output_number:1, shift_number:1, direction:'\u043f\u0440\u044f\u043c\u043e\u0435'})">+ \u0440\u0435\u0439\u0441</button>
        <button class="btn sec" onclick="schedBulkShift()">\u0421\u0434\u0432\u0438\u043d\u0443\u0442\u044c \u0432\u0440\u0435\u043c\u044f</button>
        <button class="btn sec" onclick="schedRenumber()">\u041f\u0435\u0440\u0435\u043d\u0443\u043c\u0435\u0440\u043e\u0432\u0430\u0442\u044c</button>
        <button class="btn sec" onclick="schedExport()">Excel</button>
      </div>
    </div>
    ${scheduleCards(sum)}
    <div class="schedule-layout">
      <div>
        <div class="panel"><h3>\u0420\u0435\u0439\u0441\u044b (${visibleTrips.length}/${tr.items.length})</h3>${tbl(["\u0412\u044b\u0445\u043e\u0434", "\u0421\u043c\u0435\u043d\u0430", "\u2116", "\u041d\u0430\u043f\u0440.", "\u041e\u0442\u043f\u0440.", "\u041f\u0440\u0438\u0431.", "\u041a\u043c", "\u041e\u0442\u0441\u0442\u043e\u0439", "\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430", ""], trips)}</div>
        <div class="panel"><h3>\u0412\u0440\u0435\u043c\u0435\u043d\u043d\u0430\u044f \u0448\u043a\u0430\u043b\u0430</h3>${scheduleTimeline(tr.items, chk.problems)}</div>
      </div>
      <div>
        <div class="panel"><h3>\u041e\u0448\u0438\u0431\u043a\u0438 \u0438 \u0440\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0430\u0446\u0438\u0438</h3>${chk.problems.length ? chk.problems.map(p =>
          `<div class="vio ${p.severity === "\u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435" ? "w" : ""}"><b>${sevBadge(p.severity)} ${esc(p.kind)}</b>${esc(p.message)}<br><span class="muted">${esc(p.recommendation || "")}</span></div>`).join("") : '<span class="badge b-ok">\u0417\u0430\u043c\u0435\u0447\u0430\u043d\u0438\u0439 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e</span>'}</div>
        <div class="panel"><h3>\u0412\u044b\u0445\u043e\u0434\u044b \u0438 \u0441\u043c\u0435\u043d\u044b</h3>${tbl(["\u0412\u044b\u0445\u043e\u0434", "\u0421\u043c\u0435\u043d\u0430", "\u0412\u0440\u0435\u043c\u044f", "\u0420\u0435\u0439\u0441\u043e\u0432", "\u041a\u043c", "\u0427\u0430\u0441\u044b", "\u041d\u043e\u0447\u043d\u044b\u0435"], outs)}</div>
      </div>
    </div>`;
};

async function schedGen() {
  const st = window._sched;
  const r = REFS.routes.find(x => x.id === st.route_id) || {};
  const v = await formModal("\u0413\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u044f \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044f (" + st.day_type + ")", [
    { k: "mode", label: "\u0420\u0435\u0436\u0438\u043c \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438", type: "select", options: [["interval", "\u043f\u043e \u0438\u043d\u0442\u0435\u0440\u0432\u0430\u043b\u0443"], ["outputs", "\u043f\u043e \u043a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u0443 \u0432\u044b\u0445\u043e\u0434\u043e\u0432"]] },
    { k: "outputs", label: "\u041a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u0432\u044b\u0445\u043e\u0434\u043e\u0432", type: "number", def: r.outputs_count || 2 },
    { k: "first_dep", label: "\u041f\u0435\u0440\u0432\u043e\u0435 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435", type: "time", def: "05:40" },
    { k: "last_dep", label: "\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0435\u0435 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435", type: "time", def: "21:40" },
    { k: "trip_time", label: "\u0412\u0440\u0435\u043c\u044f \u0440\u0435\u0439\u0441\u0430 \u2014 \u043f\u0440\u044f\u043c\u043e\u0435, \u043c\u0438\u043d", type: "number", def: r.trip_time_min || 45 },
    { k: "trip_time_back", label: "\u0412\u0440\u0435\u043c\u044f \u0440\u0435\u0439\u0441\u0430 \u2014 \u043e\u0431\u0440\u0430\u0442\u043d\u043e\u0435, \u043c\u0438\u043d", type: "number", def: r.trip_time_back_min || r.trip_time_min || 45 },
    { k: "interval", label: "\u0418\u043d\u0442\u0435\u0440\u0432\u0430\u043b, \u043c\u0438\u043d", type: "number", def: r.interval_min || 15 },
    { k: "rest_min", label: "\u041c\u0435\u0436\u0440\u0435\u0439\u0441\u043e\u0432\u044b\u0439 \u043e\u0442\u0441\u0442\u043e\u0439, \u043c\u0438\u043d", type: "number", def: 6 },
    { k: "lunch_min", label: "\u041e\u0431\u0435\u0434/\u043f\u0435\u0440\u0435\u0441\u043c\u0435\u043d\u043a\u0430, \u043c\u0438\u043d", type: "number", def: 40 },
    { k: "distance", label: "\u041f\u0440\u043e\u0431\u0435\u0433 \u043f\u0440\u044f\u043c\u043e\u0433\u043e \u0440\u0435\u0439\u0441\u0430, \u043a\u043c", type: "number", step: "0.1", def: r.length_km || 10 },
    { k: "distance_back", label: "\u041f\u0440\u043e\u0431\u0435\u0433 \u043e\u0431\u0440\u0430\u0442\u043d\u043e\u0433\u043e \u0440\u0435\u0439\u0441\u0430, \u043a\u043c", type: "number", step: "0.1", def: r.length_back_km || r.length_km || 10 }],
    {}, "\u0421\u0443\u0449\u0435\u0441\u0442\u0432\u0443\u044e\u0449\u0435\u0435 \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u044d\u0442\u043e\u0433\u043e \u0434\u043d\u044f \u0431\u0443\u0434\u0435\u0442 \u0437\u0430\u043c\u0435\u043d\u0435\u043d\u043e. \u0421\u043c\u0435\u043d\u044b \u0434\u0435\u043b\u044f\u0442\u0441\u044f \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438.");
  if (!v) return;
  Object.keys(v).forEach(k => { if (!["first_dep", "last_dep", "mode"].includes(k)) v[k] = +v[k]; });
  v.route_id = st.route_id; v.day_type = st.day_type;
  const res = await api("/api/trips/generate", { method: "POST", body: v }).catch(e => toast(e.message, true));
  if (res) { toast(`\u0421\u043e\u0437\u0434\u0430\u043d\u043e \u0440\u0435\u0439\u0441\u043e\u0432: ${res.trips}`); route(); }
}

async function schedBulkShift() {
  const st = window._sched;
  const v = await formModal("\u041c\u0430\u0441\u0441\u043e\u0432\u044b\u0439 \u0441\u0434\u0432\u0438\u0433 \u0440\u0435\u0439\u0441\u043e\u0432", [
    { k: "minutes", label: "\u0421\u0434\u0432\u0438\u0433, \u043c\u0438\u043d\u0443\u0442 (+ \u043f\u043e\u0437\u0436\u0435 / - \u0440\u0430\u043d\u044c\u0448\u0435)", type: "number", def: 5 },
    { k: "output_number", label: "\u0422\u043e\u043b\u044c\u043a\u043e \u0432\u044b\u0445\u043e\u0434 (0 \u2014 \u0432\u0441\u0435)", type: "number", def: 0 }]);
  if (!v) return;
  v.route_id = st.route_id; v.day_type = st.day_type;
  v.minutes = +v.minutes; v.output_number = +v.output_number || 0;
  try {
    const r = await api("/api/trips/bulk-shift", { method: "POST", body: v });
    toast(`\u0421\u0434\u0432\u0438\u043d\u0443\u0442\u043e \u0440\u0435\u0439\u0441\u043e\u0432: ${r.updated}`);
    route();
  } catch (e) { toast(e.message, true); }
}

async function schedRenumber() {
  const st = window._sched;
  try {
    const r = await api("/api/trips/renumber", { method: "POST", body: { route_id: st.route_id, day_type: st.day_type } });
    toast(`\u041f\u0435\u0440\u0435\u043d\u0443\u043c\u0435\u0440\u043e\u0432\u0430\u043d\u043e \u0440\u0435\u0439\u0441\u043e\u0432: ${r.updated}`);
    route();
  } catch (e) { toast(e.message, true); }
}

function schedExport() {
  const st = window._sched;
  openWin(`/api/routes/${st.route_id}/schedule-export.xlsx?day_type=${encodeURIComponent(st.day_type)}`);
}

async function tripEdit(t) {
  t = Object.assign({}, t, { break_type: normalizeBreakType(t.break_type) });
  const v = await formModal(t.id ? "\u0420\u0435\u0439\u0441" : "\u041d\u043e\u0432\u044b\u0439 \u0440\u0435\u0439\u0441", [
    { k: "output_number", label: "\u0412\u044b\u0445\u043e\u0434", type: "number" }, { k: "shift_number", label: "\u0421\u043c\u0435\u043d\u0430", type: "number" },
    { k: "trip_number", label: "\u2116 \u0440\u0435\u0439\u0441\u0430", type: "number" },
    { k: "direction", label: "\u041d\u0430\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435", type: "select", options: ["\u043f\u0440\u044f\u043c\u043e\u0435", "\u043e\u0431\u0440\u0430\u0442\u043d\u043e\u0435", "\u043d\u0443\u043b\u0435\u0432\u043e\u0439"] },
    { k: "dep_time", label: "\u041e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435", type: "time" }, { k: "arr_time", label: "\u041f\u0440\u0438\u0431\u044b\u0442\u0438\u0435", type: "time" },
    { k: "distance_km", label: "\u041a\u043c", type: "number", step: "0.1" },
    { k: "break_after_min", label: "\u041e\u0442\u0441\u0442\u043e\u0439 \u043f\u043e\u0441\u043b\u0435, \u043c\u0438\u043d", type: "number" },
    { k: "break_type", label: "\u0422\u0438\u043f \u043f\u0435\u0440\u0435\u0440\u044b\u0432\u0430", type: "segments", options: [["", "\u043d\u0435\u0442"], ["\u043e\u0431\u0435\u0434", "\u043e\u0431\u0435\u0434"], ["\u0440\u0430\u0437\u0440\u044b\u0432", "\u0440\u0430\u0437\u0440\u044b\u0432"], ["\u0442\u0435\u0445\u043d\u043e\u043b\u043e\u0433\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u043f\u0435\u0440\u0435\u0440\u044b\u0432", "\u0442\u0435\u0445\u043d\u043e\u043b. \u043f\u0435\u0440\u0435\u0440\u044b\u0432"]] }], t);
  if (!v) return;
  Object.assign(v, { id: t.id, route_id: t.route_id, day_type: t.day_type });
  ["output_number", "shift_number", "trip_number", "distance_km", "break_after_min"].forEach(k => v[k] = +v[k] || 0);
  try {
    const res = await api("/api/trips", { method: "POST", body: v });
    toast(res.shifted ? `\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e. \u0421\u0434\u0432\u0438\u043d\u0443\u0442\u043e \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0445 \u0440\u0435\u0439\u0441\u043e\u0432: ${res.shifted}` : "\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e");
    route();
  } catch (e) { toast(e.message, true); }
}
async function tripDel(id) { if (confirm("\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0440\u0435\u0439\u0441?")) { await api("/api/trips/" + id, { method: "DELETE" }); route(); } }

/* ================= ОТСУТСТВИЯ ================= */
VIEWS.absences = async function () {
  const d = await api("/api/absences");
  const rows = d.items.map(a => `<tr><td>${esc(a.fio)}<br><span class="muted">таб. ${esc(a.tab_number)}</span></td>
    <td><span class="badge b-warn">${esc(a.type_code)}</span> ${esc(a.type_name || "")}</td>
    <td>${esc(a.date_from)} — ${esc(a.date_to)}</td><td>${esc(a.status)}</td><td>${esc(a.comment || "")}</td>
    <td><button class="btn small danger" onclick="absDel(${a.id})">удалить</button></td></tr>`).join("");
  $("content").innerHTML = `<div class="toolbar">
      <button class="btn" onclick="absAdd()">+ Оформить отсутствие</button>
      <span class="muted">При оформлении водитель автоматически исключается из графика и наряда на период.</span>
    </div>` + tbl(["Водитель", "Вид", "Период", "Статус", "Комментарий", ""], rows);
};
async function absAdd() {
  const v = await formModal("Оформление отсутствия", [
    { k: "driver_id", label: "Водитель", type: "select", options: REFS.drivers.filter(d => d.status !== "уволен").map(d => [d.id, d.fio + " (таб. " + d.tab_number + ")"]) },
    { k: "type_code", label: "Вид отсутствия", type: "select", options: REFS.absence_types.map(t => [t.code, t.name]) },
    { k: "date_from", label: "С", type: "date", def: today() }, { k: "date_to", label: "По", type: "date", def: today() },
    { k: "status", label: "Статус", type: "select", options: ["утверждено", "план"] },
    { k: "comment", label: "Комментарий", type: "text" }]);
  if (!v) return;
  v.driver_id = +v.driver_id;
  try {
    const r = await api("/api/absences", { method: "POST", body: v });
    toast(r.roster_affected ? `Оформлено. Снято смен из графика: ${r.roster_affected} — подберите замену в наряде.` : "Оформлено");
    route();
  } catch (e) { toast(e.message, true); }
}
async function absDel(id) { if (confirm("Удалить запись об отсутствии?")) { await api("/api/absences/" + id, { method: "DELETE" }); route(); } }

/* ================= КАЛЕНДАРЬ ================= */
VIEWS.calendar = async function () {
  const year = window._calYear || new Date().getFullYear();
  window._calYear = year;
  const cal = await api("/api/calendar?year=" + year);
  const CLS = { "рабочий": "", "выходной": "rc-off", "праздник": "rc-abs", "предпраздничный": "rc-rz" };
  const months = [];
  for (let m = 1; m <= 12; m++) {
    const mm = String(m).padStart(2, "0");
    const days = new Date(year, m, 0).getDate();
    let cells = "";
    for (let d = 1; d <= days; d++) {
      const iso = `${year}-${mm}-${String(d).padStart(2, "0")}`;
      const t = cal[iso];
      cells += `<td class="roster-cell ${CLS[t] || ""}" title="${t}" onclick="calToggle('${iso}','${t}')">${d}</td>`;
      if ((d + new Date(year, m - 1, 1).getDay() - 1) % 7 === 0) cells += "</tr><tr>";
    }
    months.push(`<div class="panel"><h3>${["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"][m]}</h3>
      <table class="grid"><tbody><tr>${cells}</tr></tbody></table></div>`);
  }
  $("content").innerHTML = `<div class="toolbar">
      <input type="number" style="width:90px" value="${year}" onchange="_calYear=+this.value; route()">
      <span class="muted">Клик по дню: рабочий → выходной → праздник → предпраздничный. Праздники РФ проставлены автоматически.</span>
    </div><div style="display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:12px">${months.join("")}</div>`;
};
async function calToggle(iso, cur) {
  const order = ["рабочий", "выходной", "праздник", "предпраздничный"];
  const next = order[(order.indexOf(cur) + 1) % 4];
  await api("/api/calendar", { method: "POST", body: { date: iso, day_type: next } });
  route();
}

/* ================= ТАБЕЛЬ И 1С ================= */
VIEWS.timesheet = async function () {
  const month = window._tsMonth || thisMonth();
  window._tsMonth = month;
  const d = await api("/api/timesheet?month=" + month);
  const hist = await api("/api/export1c/history");
  const days = d.days_in_month;
  const rows = d.rows.map(r => {
    const cells = r.days.map(c => {
      const cls = c.code === "Я" || c.code === "РВ" ? "rc-work" : c.code === "В" ? "rc-off" : c.code === "РЗ" ? "rc-rz" : "rc-abs";
      return `<td class="roster-cell ${cls}" title="${c.date}">${c.code === "В" ? "·" : esc(c.code)}${c.hours ? "<br>" + c.hours : ""}</td>`;
    }).join("");
    return `<tr><td style="white-space:nowrap"><b>${esc(r.fio)}</b><br><span class="muted">таб. ${esc(r.tab_number)} · ${esc(r.division || "")}</span></td>
      ${cells}<td>${r.days_worked}</td><td><b>${r.total_hours}</b></td><td>${r.night_hours}</td>
      <td>${r.holiday_hours}</td><td>${r.overtime ? `<span class="badge b-warn">${r.overtime}</span>` : "0"}</td><td>${r.undertime}</td></tr>`;
  }).join("");
  const histRows = hist.items.map(h => `<tr><td>${esc((h.created_at || "").replace("T", " "))}</td><td>${esc(h.created_by)}</td>
    <td>${esc(h.period_from)} — ${esc(h.period_to)}</td><td>${esc(h.fmt)}</td><td>${h.employees}</td>
    <td>v${h.version}</td><td>${esc(h.status)}</td><td>${esc(h.file_name)}</td></tr>`).join("");
  const from = month + "-01", to = month + "-" + String(days).padStart(2, "0");
  $("content").innerHTML = `<div class="toolbar">
      <input type="month" value="${month}" onchange="_tsMonth=this.value; route()">
      <span class="badge b-inf">норма месяца: ${d.norm_hours} ч</span>
      <button class="btn sec" onclick="openWin('/api/timesheet/export.xlsx?month=${month}')">Табель в Excel</button>
      <b style="margin-left:20px">Выгрузка в 1С:</b>
      <select id="fmt1c"><option value="csv">CSV</option><option value="xml">XML</option><option value="json">JSON</option><option value="xlsx">Excel</option></select>
      <button class="btn" onclick="openWin('/api/export1c?date_from=${from}&date_to=${to}&fmt=' + $('fmt1c').value); setTimeout(route, 1500)">Выгрузить за месяц</button>
    </div>` +
    tbl(["Водитель", ...Array.from({ length: days }, (_, i) => String(i + 1)), "Дн", "Часы", "Ночн", "Праздн", "Сверх", "Недор"], rows) +
    `<div class="panel" style="margin-top:14px"><h3>История выгрузок в 1С</h3>${tbl(["Когда", "Кто", "Период", "Формат", "Сотр.", "Версия", "Статус", "Файл"], histRows)}</div>`;
};

/* ================= ТОПЛИВО ================= */
VIEWS.fuel = async function () {
  const f = window._fuel || { date_from: thisMonth() + "-01", date_to: today(), tab: "records", group: "bus" };
  window._fuel = f;
  let body = "";
  if (f.tab === "records") {
    const d = await api(`/api/fuel?date_from=${f.date_from}&date_to=${f.date_to}`);
    const rows = d.items.map(r => `<tr><td>${esc(r.date)}</td><td>${esc(r.garage_number || "")} ${esc(r.plate || "")}</td>
      <td>${esc(r.fio || "")}</td><td>${esc(r.route_number || "")}</td><td>${r.waybill_number || ""}</td>
      <td><span class="badge ${r.kind === "рейс" ? "b-inf" : r.kind === "заправка" ? "b-ok" : "b-warn"}">${esc(r.kind)}</span></td>
      <td>${r.distance || ""}</td><td>${r.rate || ""}</td><td>${r.plan_litres || ""}</td><td>${r.fact_litres || ""}</td>
      <td>${r.given_litres || ""}</td><td>${r.start_balance ?? ""} → ${r.end_balance ?? ""}</td>
      <td>${r.saving ? `<span class="badge b-ok">−${r.saving}</span>` : ""}${r.overrun ? `<span class="badge b-err">+${r.overrun}</span>` : ""}</td>
      <td>${esc(r.responsible || "")}</td></tr>`).join("");
    body = `<div class="cards">
      <div class="card"><div class="num">${d.totals.plan}</div><div class="lbl">план, л</div></div>
      <div class="card"><div class="num">${d.totals.fact}</div><div class="lbl">факт, л</div></div>
      <div class="card"><div class="num">${d.totals.given}</div><div class="lbl">выдано, л</div></div>
      <div class="card ok"><div class="num">${d.totals.saving}</div><div class="lbl">экономия, л</div></div>
      <div class="card err"><div class="num">${d.totals.overrun}</div><div class="lbl">перерасход, л</div></div></div>` +
      tbl(["Дата", "Автобус", "Водитель", "Маршрут", "№ ПЛ", "Операция", "Км", "Норма", "План, л", "Факт, л", "Выдано", "Остаток", "Эконом./перерасх.", "Ответственный"], rows);
  } else {
    const d = await api(`/api/fuel/report?date_from=${f.date_from}&date_to=${f.date_to}&group=${f.group}`);
    const rows = d.items.map(r => `<tr><td>${esc(r.name || "—")}</td><td>${r.trips}</td><td>${r.km || 0}</td>
      <td>${r.plan || 0}</td><td>${r.fact || 0}</td>
      <td>${r.saving ? `<span class="badge b-ok">${r.saving}</span>` : "0"}</td>
      <td>${r.overrun ? `<span class="badge b-err">${r.overrun}</span>` : "0"}</td></tr>`).join("");
    body = `<div class="toolbar">
        <select onchange="_fuel.group=this.value; route()">
          <option value="bus" ${f.group === "bus" ? "selected" : ""}>по автобусам</option>
          <option value="driver" ${f.group === "driver" ? "selected" : ""}>по водителям</option>
          <option value="route" ${f.group === "route" ? "selected" : ""}>по маршрутам</option>
        </select></div>` +
      tbl(["Объект", "Рейсо-смен", "Пробег, км", "План, л", "Факт, л", "Экономия", "Перерасход"], rows);
  }
  $("content").innerHTML = `<div class="toolbar">
      с <input type="date" value="${f.date_from}" onchange="_fuel.date_from=this.value; route()">
      по <input type="date" value="${f.date_to}" onchange="_fuel.date_to=this.value; route()">
      <div class="tabs" style="margin:0;border:none">
        <button class="${f.tab === "records" ? "on" : ""}" onclick="_fuel.tab='records'; route()">Ведомость</button>
        <button class="${f.tab === "report" ? "on" : ""}" onclick="_fuel.tab='report'; route()">Сводный отчёт</button>
      </div>
      <button class="btn" onclick="fuelOp('заправка')">+ Заправка</button>
      <button class="btn sec" onclick="fuelOp('корректировка/слив')">− Корректировка</button>
      <button class="btn sec" onclick="openWin('/api/fuel/export.xlsx?date_from=${f.date_from}&date_to=${f.date_to}')">Ведомость в Excel</button>
    </div>` + body;
};
async function fuelOp(kind) {
  const v = await formModal(kind === "заправка" ? "Заправка" : "Корректировка / слив", [
    { k: "bus_id", label: "Автобус", type: "select", options: REFS.buses.map(b => [b.id, `${b.garage_number} · ${b.plate || ""} (остаток ${b.fuel_balance ?? "?"} л)`]) },
    { k: "date", label: "Дата", type: "date", def: today() },
    { k: "litres", label: "Литры", type: "number", step: "0.1" },
    { k: "comment", label: "Комментарий", type: "text" }]);
  if (!v) return;
  v.bus_id = +v.bus_id; v.litres = +v.litres; v.kind = kind === "заправка" ? "заправка" : "корректировка";
  try {
    const r = await api("/api/fuel/refuel", { method: "POST", body: v });
    toast(`Проведено. Новый остаток: ${r.balance} л`);
    await loadRefs(); route();
  } catch (e) { toast(e.message, true); }
}

/* ================= МЕДОСМОТР / ТЕХКОНТРОЛЬ ================= */
VIEWS.release = async function () {
  const date = window._relDate || today();
  window._relDate = date;
  const [med, tech, settings] = await Promise.all([api("/api/medical?date=" + date), api("/api/tech?date=" + date), api("/api/settings")]);
  const waybillMode = settings.waybill_issue_mode || "strict_med_tech";
  const modeHint = {
    strict_med_tech: "ПЛ нельзя оформить без допуска медика и разрешения механика.",
    medical_only: "ПЛ нельзя оформить без допуска медика; механик даёт предупреждение, кроме явного запрета выпуска.",
    advisory: "ПЛ можно оформить без отметок медика и механика, система покажет предупреждения."
  }[waybillMode] || "ПЛ нельзя оформить без допуска медика и разрешения механика.";
  const medRows = med.items.map(m => `<tr><td>${esc(m.time)}</td><td>${esc(m.fio)}</td><td>${esc(m.type)}</td>
    <td>${m.result === "допущен" ? '<span class="badge b-ok">допущен</span>' : '<span class="badge b-err">НЕ допущен</span>'}</td>
    <td>${esc(m.medic_name || "")}</td></tr>`).join("");
  const techRows = tech.items.map(t => `<tr><td>${esc(t.time)}</td><td>${esc(t.garage_number)} · ${esc(t.plate || "")}</td>
    <td>${t.result === "выпуск разрешен" ? '<span class="badge b-ok">разрешён</span>' : '<span class="badge b-err">ЗАПРЕЩЁН</span>'}</td>
    <td>${t.odometer || ""}</td><td>${esc(t.mechanic_name || "")}</td><td>${esc(t.notes || "")}</td></tr>`).join("");
  $("content").innerHTML = `<div class="toolbar">
      <input type="date" value="${date}" onchange="_relDate=this.value; route()">
      <button class="btn" onclick="medAdd('${date}')">+ Медосмотр</button>
      <button class="btn" onclick="techAdd('${date}')">+ Техконтроль</button>
      <span class="muted">${modeHint}</span>
    </div>
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:14px">
      <div class="panel"><h3>Медицинские осмотры (${med.items.length})</h3>${tbl(["Время", "Водитель", "Тип", "Результат", "Медработник"], medRows)}</div>
      <div class="panel"><h3>Технический контроль (${tech.items.length})</h3>${tbl(["Время", "Автобус", "Результат", "Одометр", "Механик", "Замечания"], techRows)}</div>
    </div>`;
};
async function medAdd(date) {
  const v = await formModal("Медицинский осмотр", [
    { k: "driver_id", label: "Водитель", type: "select", options: REFS.drivers.filter(d => d.status === "работает").map(d => [d.id, d.fio]) },
    { k: "type", label: "Тип осмотра", type: "select", options: ["предрейсовый", "предсменный", "послерейсовый", "послесменный"] },
    { k: "result", label: "Результат", type: "select", options: ["допущен", "не допущен"] },
    { k: "time", label: "Время", type: "time", def: new Date().toTimeString().slice(0, 5) },
    { k: "medic_name", label: "Медработник", type: "text", def: USER.full_name },
    { k: "org", label: "Организация", type: "text" },
    { k: "comment", label: "Комментарий", type: "text" }]);
  if (!v) return;
  v.driver_id = +v.driver_id; v.date = date;
  await api("/api/medical", { method: "POST", body: v }).catch(e => toast(e.message, true));
  route();
}
async function techAdd(date) {
  const v = await formModal("Предрейсовый технический контроль", [
    { k: "bus_id", label: "Автобус", type: "select", options: REFS.buses.filter(b => b.status !== "списан").map(b => [b.id, `${b.garage_number} · ${b.plate || ""}`]) },
    { k: "result", label: "Результат", type: "select", options: ["выпуск разрешен", "выпуск запрещен"] },
    { k: "time", label: "Время", type: "time", def: new Date().toTimeString().slice(0, 5) },
    { k: "odometer", label: "Показания одометра", type: "number" },
    { k: "mechanic_name", label: "Механик / контролёр", type: "text", def: USER.full_name },
    { k: "notes", label: "Технические замечания", type: "text" }]);
  if (!v) return;
  v.bus_id = +v.bus_id; v.date = date; v.odometer = +v.odometer || null;
  await api("/api/tech", { method: "POST", body: v }).catch(e => toast(e.message, true));
  route();
}

/* ================= СПРАВОЧНИКИ ================= */
const REF_CFG = {
  drivers: {
    title: "Водители", table: "drivers",
    cols: [["tab_number", "Таб.№"], ["fio", "ФИО"], ["division", "Подразделение"], ["driver_class", "Класс"],
      ["license_number", "Вод. удостоверение"], ["license_expires", "ВУ до"], ["default_schedule", "График"],
      ["assigned_route_id", "Маршрут", (v) => routeName(v)], ["assigned_bus_id", "Автобус", (v) => busName(v)],
      ["phone", "Телефон"], ["status", "Статус", (v) => `<span class="badge ${v === "работает" ? "b-ok" : "b-warn"}">${esc(v)}</span>`]],
    fields: () => [
      { k: "tab_number", label: "Табельный номер" }, { k: "fio", label: "ФИО" },
      { k: "birth_date", label: "Дата рождения", type: "date" }, { k: "division", label: "Подразделение / колонна" },
      { k: "position", label: "Должность", def: "Водитель автобуса" },
      { k: "license_categories", label: "Категории ВУ", def: "D" }, { k: "license_number", label: "Серия и номер ВУ" },
      { k: "license_issued", label: "ВУ выдано", type: "date" }, { k: "license_expires", label: "ВУ действует до", type: "date" },
      { k: "snils", label: "СНИЛС" }, { k: "inn", label: "ИНН" }, { k: "phone", label: "Телефон" },
      { k: "address", label: "Адрес" },
      { k: "employment_type", label: "Тип занятости", type: "select", options: ["Основное место работы", "Совместительство", "Подработка"] },
      { k: "default_schedule", label: "График по умолчанию", type: "select", options: ["2/2", "5/2", "6/1", "3/1", "4/2", "1/1"] },
      { k: "assigned_route_id", label: "Закреплённый маршрут", type: "select", empty: true, options: REFS.routes.map(r => [r.id, "№ " + r.number]) },
      { k: "assigned_bus_id", label: "Закреплённый автобус", type: "select", empty: true, options: REFS.buses.map(b => [b.id, b.garage_number + " · " + (b.plate || "")]) },
      { k: "driver_class", label: "Класс водителя", type: "select", options: ["1", "2", "3"] },
      { k: "bus_type_permits", label: "Допуски (типы автобусов)", def: "большой,средний,малый" },
      { k: "hired_date", label: "Дата приёма", type: "date" }, { k: "fired_date", label: "Дата увольнения", type: "date" },
      { k: "status", label: "Статус", type: "select", options: ["работает", "отпуск", "больничный", "отстранен", "уволен"] },
      { k: "med_info", label: "Медкомиссия (сведения)" }, { k: "training_info", label: "Обучение, инструктажи" },
      { k: "restrictions", label: "Ограничения" }, { k: "notes", label: "Примечания", type: "textarea" }],
    intFields: ["assigned_route_id", "assigned_bus_id"],
  },
  buses: {
    title: "Автобусы", table: "buses",
    cols: [["garage_number", "Гар.№"], ["plate", "Госномер"], ["brand", "Марка"], ["model", "Модель"],
      ["bus_class", "Класс"], ["capacity", "Вмест."], ["fuel_rate", "Норма л/100км"],
      ["odometer", "Одометр"], ["fuel_balance", "Остаток топл."], ["osago_expires", "ОСАГО до"],
      ["next_to_date", "ТО"], ["status", "Статус", (v) => `<span class="badge ${v === "исправен" ? "b-ok" : v === "в ремонте" ? "b-err" : "b-mut"}">${esc(v)}</span>`]],
    fields: () => [
      { k: "garage_number", label: "Гаражный номер" }, { k: "plate", label: "Гос. рег. знак" },
      { k: "vin", label: "VIN" }, { k: "brand", label: "Марка" }, { k: "model", label: "Модель" },
      { k: "year", label: "Год выпуска", type: "number" },
      { k: "bus_class", label: "Класс", type: "select", options: ["большой", "средний", "малый"] },
      { k: "capacity", label: "Вместимость", type: "number" },
      { k: "fuel_type", label: "Топливо", type: "select", options: ["ДТ", "Бензин", "Газ (КПГ)", "Электро"] },
      { k: "fuel_rate", label: "Норма расхода, л/100км", type: "number", step: "0.1" },
      { k: "winter_coeff", label: "Зимний коэффициент", type: "number", step: "0.01", def: 1.1 },
      { k: "column_name", label: "Колонна" },
      { k: "assigned_driver_id", label: "Закреплённый водитель", type: "select", empty: true, options: REFS.drivers.map(d => [d.id, d.fio]) },
      { k: "next_to_date", label: "Дата следующего ТО", type: "date" },
      { k: "osago_expires", label: "ОСАГО до", type: "date" }, { k: "diag_card_expires", label: "Диагностическая карта до", type: "date" },
      { k: "status", label: "Статус", type: "select", options: ["исправен", "на линии", "в ремонте", "резерв", "списан"] },
      { k: "odometer", label: "Одометр, км", type: "number", step: "0.1" },
      { k: "tank_capacity", label: "Бак, л", type: "number" }, { k: "fuel_balance", label: "Остаток топлива, л", type: "number", step: "0.1" },
      { k: "equipment", label: "Оборудование", def: "тахограф,ГЛОНАСС" }],
    intFields: ["assigned_driver_id", "year", "capacity"],
  },
  routes: {
    title: "Маршруты", table: "routes",
    cols: [["number", "№"], ["name", "Наименование"], ["comm_type", "Сообщение"], ["length_km", "Км"],
      ["trip_time_min", "Рейс, мин"], ["interval_min", "Интервал"], ["outputs_count", "Выходов"],
      ["bus_types", "Типы ТС"], ["work_days", "Дни"], ["version", "Версия"]],
    fields: () => [
      { k: "number", label: "Номер маршрута" }, { k: "name", label: "Наименование" },
      { k: "comm_type", label: "Вид сообщения", type: "select", options: ["городское", "пригородное", "межмуниципальное"] },
      { k: "transport_type", label: "Вид перевозки", def: "Регулярные перевозки пассажиров и багажа" },
      { k: "start_point", label: "Начальный пункт" }, { k: "end_point", label: "Конечный пункт" },
      { k: "stops", label: "Остановки — прямое направление (через запятую)", type: "textarea" },
      { k: "stops_back", label: "Остановки — обратное направление (через запятую)", type: "textarea" },
      { k: "length_km", label: "Протяжённость — прямое, км", type: "number", step: "0.1" },
      { k: "length_back_km", label: "Протяжённость — обратное, км", type: "number", step: "0.1" },
      { k: "trip_time_min", label: "Время рейса — прямое, мин", type: "number" },
      { k: "trip_time_back_min", label: "Время рейса — обратное, мин", type: "number" },
      { k: "interval_min", label: "Интервал, мин", type: "number" },
      { k: "outputs_count", label: "Количество выходов", type: "number" },
      { k: "bus_types", label: "Типы автобусов", def: "большой" },
      { k: "season", label: "Сезонность", def: "круглогодично" }, { k: "work_days", label: "Дни работы", def: "ежедневно" },
      { k: "version", label: "Версия расписания", type: "number", def: 1 },
      { k: "notes", label: "Примечания", type: "textarea" }],
    intFields: ["trip_time_min", "trip_time_back_min", "interval_min", "outputs_count", "version"],
  },
};
function refView(kind) {
  return async function () {
    const cfg = REF_CFG[kind];
    const q = window["_q_" + kind] || "";
    const d = await api(`/api/refs/${cfg.table}?q=` + encodeURIComponent(q));
    const rows = d.items.map(item => {
      const tds = cfg.cols.map(([k, , fmt]) => `<td>${fmt ? fmt(item[k]) : esc(item[k])}</td>`).join("");
      return `<tr>${tds}<td style="white-space:nowrap">
        <button class="btn small ghost" onclick="refEdit('${kind}', ${item.id})">изменить</button>
        <button class="btn small ghost" onclick="refDel('${kind}', ${item.id})">✕</button></td></tr>`;
    }).join("");
    const ermImport = kind === "routes" ?
      `<label class="btn sec" style="cursor:pointer">Импорт ЭРМ<input type="file" accept=".xlsx" style="display:none" onchange="routeErmImport(this)"></label>` : "";
    $("content").innerHTML = `<div class="toolbar">
        <input placeholder="поиск…" value="${esc(q)}" onchange="window._q_${kind}=this.value; route()">
        <button class="btn" onclick="refEdit('${kind}', 0)">+ Добавить</button>
        <button class="btn sec" onclick="openWin('/api/refs/${cfg.table}/export.xlsx')">Экспорт в Excel</button>
        <label class="btn sec" style="cursor:pointer">Импорт из Excel<input type="file" accept=".xlsx" style="display:none" onchange="refImport('${kind}', this)"></label>
        ${ermImport}
        <span class="muted">${d.items.length} записей</span>
      </div>` + tbl([...cfg.cols.map(c => c[1]), ""], rows);
  };
}
VIEWS.drivers = refView("drivers");
VIEWS.buses = async function () {
  const q = window._q_buses || "";
  const d = await api("/api/refs/buses?q=" + encodeURIComponent(q));
  const cfg = REF_CFG.buses;
  const rows = d.items.map(item => `<tr>${cfg.cols.map(([k, , fmt]) => `<td>${fmt ? fmt(item[k]) : esc(item[k])}</td>`).join("")}<td style="white-space:nowrap"><button class="btn small" onclick="openVehicleCard(${item.id})">Карточка</button> <button class="btn small ghost" onclick="refEdit('buses',${item.id})">изменить</button> <button class="btn small ghost" onclick="refDel('buses',${item.id})">✕</button></td></tr>`).join("");
  $("content").innerHTML = `<div class="toolbar"><input style="min-width:330px" placeholder="Поиск по гаражному номеру, госномеру или VIN" value="${esc(q)}" onchange="_q_buses=this.value;route()"><button class="btn" onclick="refEdit('buses',0)">+ Добавить</button><button class="btn sec" onclick="openWin('/api/refs/buses/export.xlsx')">Экспорт в Excel</button><label class="btn sec">Импорт из Excel<input type="file" accept=".xlsx" style="display:none" onchange="refImport('buses',this)"></label><span class="muted">${d.items.length} записей</span></div>${tbl([...cfg.cols.map(c => c[1]), "Действия"], rows || `<tr><td colspan="${cfg.cols.length + 1}">Автобусы не найдены</td></tr>`)}`;
};
VIEWS.routes = async function () {
  const q = window._q_routes || "";
  const cfg = REF_CFG.routes;
  const data = await api(`/api/refs/${cfg.table}?q=${encodeURIComponent(q)}`);
  const rows = data.items.map(item => `<tr>${cfg.cols.map(([key, , fmt]) => `<td>${fmt ? fmt(item[key]) : esc(item[key])}</td>`).join("")}
    <td style="white-space:nowrap"><button class="btn small" onclick="routeCardOpen(${item.id})">Карточка</button>
    <button class="btn small ghost" onclick="refEdit('routes',${item.id})">изменить</button>
    <button class="btn small ghost" onclick="refDel('routes',${item.id})">✕</button></td></tr>`).join("");
  $("content").innerHTML = `<div class="toolbar"><input placeholder="поиск…" value="${esc(q)}" onchange="_q_routes=this.value;route()">
    <button class="btn" onclick="refEdit('routes',0)">+ Добавить</button><button class="btn sec" onclick="openWin('/api/refs/routes/export.xlsx')">Экспорт в Excel</button>
    <label class="btn sec">Импорт из Excel<input type="file" accept=".xlsx" hidden onchange="refImport('routes',this)"></label>
    <label class="btn sec">Импорт ЭРМ<input type="file" accept=".xlsx" hidden onchange="routeErmImport(this)"></label>
    <span class="muted">${data.items.length} записей</span></div>${tbl([...cfg.cols.map(c => c[1]), "Действия"], rows || `<tr><td colspan="${cfg.cols.length + 1}">Маршруты не найдены</td></tr>`)}`;
};
async function refEdit(kind, id) {
  const cfg = REF_CFG[kind];
  const cur = id ? (await api(`/api/refs/${cfg.table}`)).items.find(x => x.id === id) : {};
  const v = await formModal(cfg.title + (id ? ": редактирование" : ": новая запись"), cfg.fields(), cur || {});
  if (!v) return;
  (cfg.intFields || []).forEach(k => { v[k] = v[k] === "" ? null : +v[k]; });
  try {
    await api(id ? `/api/refs/${cfg.table}/${id}` : `/api/refs/${cfg.table}`,
      { method: id ? "PUT" : "POST", body: v });
    toast("Сохранено"); await loadRefs(); route();
  } catch (e) { toast(e.message, true); }
}
async function refDel(kind, id) {
  if (!confirm("Удалить запись? (действие фиксируется в аудите)")) return;
  await api(`/api/refs/${REF_CFG[kind].table}/${id}`, { method: "DELETE" }).catch(e => toast(e.message, true));
  await loadRefs(); route();
}
async function refImport(kind, input) {
  const fd = new FormData();
  fd.append("file", input.files[0]);
  try {
    const r = await api(`/api/import/${REF_CFG[kind].table}`, { method: "POST", body: fd });
    toast(`Импорт: добавлено ${r.added}, дублей пропущено ${r.skipped}, ошибок ${r.errors.length}` +
      (r.errors.length ? "\n" + r.errors.slice(0, 4).join("\n") : ""), r.errors.length > 0);
    await loadRefs(); route();
  } catch (e) { toast(e.message, true); }
}

async function routeErmImport(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await api("/api/import/routes/erm", { method: "POST", body: fd });
    const s = r.summary || {};
    const status = r.created ? "создан" : "обновлён";
    toast(`ЭРМ: ${status} маршрут № ${r.route.number} ${r.route.name || ""}. ` +
      `Остановок: ${s.route_stops_forward || 0}/${s.route_stops_backward || 0}, нулевых секций: ${s.depot_sections || 0}`);
    await loadRefs(); route();
  } catch (e) {
    toast(e.message, true);
  } finally {
    input.value = "";
  }
}

/* ================= ОТЧЁТЫ ================= */
VIEWS.reports = async function () {
  const f = window._rep || { tab: "summary", date_from: thisMonth() + "-01", date_to: today(), month: thisMonth() };
  window._rep = f;
  let body = "";
  if (f.tab === "summary") {
    const s = await api(`/api/reports/summary?date_from=${f.date_from}&date_to=${f.date_to}`);
    body = `<div class="cards">` + Object.entries(s).filter(([k]) => k !== "период").map(([k, v]) =>
      `<div class="card"><div class="num">${v}</div><div class="lbl">${k.replace(/_/g, " ")}</div></div>`).join("") + "</div>";
  } else if (f.tab === "overtime") {
    const d = await api("/api/reports/overtime?month=" + f.month);
    body = tbl(["Водитель", "Таб.№", "Подразделение", "Часы", "Норма", "Сверхурочные", "Недоработка", "Ночные", "Праздничные"],
      d.items.map(r => `<tr><td>${esc(r.fio)}</td><td>${esc(r.tab_number)}</td><td>${esc(r.division || "")}</td>
        <td>${r.total}</td><td>${r.norm}</td><td>${r.overtime ? `<span class="badge b-err">${r.overtime}</span>` : 0}</td>
        <td>${r.undertime}</td><td>${r.night}</td><td>${r.holiday}</td></tr>`).join(""));
  } else if (f.tab === "release") {
    const d = await api("/api/reports/release?date=" + f.date_to);
    body = `<div class="cards">` + Object.entries(d.summary).map(([k, v]) =>
      `<div class="card ${k === "срывы" && v ? "err" : ""}"><div class="num">${v}</div><div class="lbl">${k}</div></div>`).join("") + "</div>" +
      tbl(["Маршрут", "Вых/см", "Водитель", "Автобус", "ПЛ №", "Статус ПЛ", "Факт выезд-возврат", "Пробег"],
        d.items.map(l => `<tr><td>№ ${esc(l.rn)}</td><td>${l.output_number}/${l.shift_number}</td><td>${esc(l.fio || "—")}</td>
          <td>${esc(l.garage_number || "—")}</td><td>${l.wn || '<span class="badge b-err">нет</span>'}</td>
          <td>${l.wstatus ? stBadge(l.wstatus) : ""}</td><td>${esc(l.depart_fact || "")}–${esc(l.return_fact || "")}</td>
          <td>${l.wdist || ""}</td></tr>`).join(""));
  } else if (f.tab === "twork") {
    const d = await api(`/api/reports/transport-work?date_from=${f.date_from}&date_to=${f.date_to}`);
    body = tbl(["Маршрут", "Наименование", "ПЛ выполнено", "Рейсов план", "Км план", "Км факт", "Выполнение"],
      d.items.map(r => {
        const pct = r.km_plan ? Math.round((r.km_fact || 0) / r.km_plan * 100) : 0;
        return `<tr><td>№ ${esc(r.number)}</td><td>${esc(r.name || "")}</td><td>${r.waybills || 0}</td>
          <td>${r.trips_plan || 0}</td><td>${r.km_plan || 0}</td><td>${r.km_fact || 0}</td>
          <td><span class="badge ${pct >= 95 ? "b-ok" : pct >= 80 ? "b-warn" : "b-err"}">${pct}%</span></td></tr>`;
      }).join(""));
  } else if (f.tab === "violations") {
    const d = await api(`/api/roster/check?date_from=${f.date_from}&date_to=${f.date_to}`);
    body = `<div class="panel">${violList(d.violations)}</div>`;
  }
  $("content").innerHTML = `<div class="toolbar">
      <div class="tabs" style="margin:0;border:none">
        ${[["summary", "Сводный отчёт"], ["overtime", "Переработки и ночные"], ["release", "Выпуск на линию"],
           ["twork", "Транспортная работа"], ["violations", "Нарушения РТиО"]].map(([k, l]) =>
          `<button class="${f.tab === k ? "on" : ""}" onclick="_rep.tab='${k}'; route()">${l}</button>`).join("")}
      </div>
      с <input type="date" value="${f.date_from}" onchange="_rep.date_from=this.value; _rep.month=this.value.slice(0,7); route()">
      по <input type="date" value="${f.date_to}" onchange="_rep.date_to=this.value; route()">
    </div>` + body;
};

/* ================= НОРМАТИВЫ ================= */
const NORM_LABELS = {
  max_shift_hours: "Максимальная смена, ч", max_shift_hours_summed: "Макс. смена при суммированном учёте, ч",
  max_driving_day: "Время управления в день, ч", max_driving_day_ext: "Время управления (допустимое расширение), ч",
  max_driving_ext_per_week: "Расширений управления в неделю, раз", max_driving_week: "Время управления в неделю, ч",
  max_driving_2weeks: "Время управления за 2 недели, ч", driving_before_break_h: "Управление до перерыва, ч",
  break_min_minutes: "Минимальный перерыв, мин", intershift_rest_factor: "Междусменный отдых (× смены)",
  min_intershift_rest_summed_h: "Мин. междусменный отдых (суммир. учёт), ч", weekly_rest_h: "Еженедельный отдых, ч",
  night_start: "Начало ночного времени", night_end: "Конец ночного времени", week_norm_hours: "Норма недели, ч",
  overtime_year_max_h: "Сверхурочные в год макс., ч", overtime_2days_max_h: "Сверхурочные за 2 дня макс., ч",
  max_consecutive_workdays: "Рабочих дней подряд макс.", prep_final_minutes: "Подготовительно-заключительное, мин",
  med_check_minutes: "Медосмотр, мин", summed_accounting: "Суммированный учёт (1/0)",
  accounting_period_months: "Учётный период, мес.",
};
VIEWS.norms = async function () {
  const d = await api("/api/norms");
  const rows = d.items.map(n => `<tr><td><b>${esc(n.name)}</b><br><span class="muted">${esc(n.doc_ref || "")}</span></td>
    <td>${esc(n.valid_from)} — ${esc(n.valid_to)}</td>
    <td>${n.active ? '<span class="badge b-ok">действует</span>' : '<span class="badge b-mut">архив</span>'}</td>
    <td>${esc(n.comment || "")}</td>
    <td><button class="btn small ghost" onclick='normEdit(${JSON.stringify(n).replace(/'/g, "&#39;")})'>изменить</button></td></tr>`).join("");
  $("content").innerHTML = `<div class="panel muted">Нормативы не «зашиты» в код: при изменении законодательства создайте новую версию
      с датой начала действия. Проверки графиков и нарядов автоматически используют версию, действующую на проверяемую дату.
      Базовые значения соответствуют Приказу Минтранса РФ от 16.10.2020 № 424 — перед эксплуатацией сверьте их с актуальной редакцией.</div>
    <div class="toolbar"><button class="btn" onclick="normEdit(null)">+ Новая версия нормативов</button></div>` +
    tbl(["Наименование / документ", "Период действия", "Статус", "Комментарий", ""], rows);
};
async function normEdit(n) {
  const params = n ? n.params : (await api("/api/norms")).defaults;
  const fields = [
    { k: "name", label: "Наименование версии" },
    { k: "valid_from", label: "Действует с", type: "date" }, { k: "valid_to", label: "Действует по", type: "date" },
    { k: "doc_ref", label: "Нормативный документ" }, { k: "comment", label: "Комментарий администратора" },
    { k: "active", label: "Действует (1/0)", type: "number" },
    ...Object.keys(params).map(k => ({ k: "p_" + k, label: NORM_LABELS[k] || k, type: ["night_start", "night_end"].includes(k) ? "time" : "text" }))];
  const vals = n ? { name: n.name, valid_from: n.valid_from, valid_to: n.valid_to, doc_ref: n.doc_ref, comment: n.comment, active: n.active } :
    { name: "Нормативы (новая версия)", valid_from: today(), valid_to: "2099-12-31", active: 1, doc_ref: "Приказ Минтранса РФ № 424" };
  Object.entries(params).forEach(([k, v]) => vals["p_" + k] = v);
  const v = await formModal(n ? "Версия нормативов" : "Новая версия нормативов", fields, vals);
  if (!v) return;
  const out = { name: v.name, valid_from: v.valid_from, valid_to: v.valid_to, doc_ref: v.doc_ref,
    comment: v.comment, active: +v.active, params: {} };
  if (n) out.id = n.id;
  Object.keys(v).filter(k => k.startsWith("p_")).forEach(k => {
    const key = k.slice(2);
    out.params[key] = ["night_start", "night_end"].includes(key) ? v[k] : +v[k];
  });
  await api("/api/norms", { method: "POST", body: out }).catch(e => toast(e.message, true));
  toast("Версия нормативов сохранена"); route();
}

/* ================= НАСТРОЙКИ ================= */
VIEWS.settings = async function () {
  const st = await api("/api/settings");
  const codes = await api("/api/time-codes");
  let usersHtml = "";
  if (USER.role === "админ") {
    const u = await api("/api/users");
    usersHtml = `<div class="panel"><h3>Пользователи <button class="btn small" onclick="userEdit(null)">+ добавить</button></h3>` +
      tbl(["Логин", "ФИО", "Роль", "Активен", ""], u.items.map(x => `<tr><td>${esc(x.username)}</td><td>${esc(x.full_name || "")}</td>
        <td>${esc(x.role)}</td><td>${x.active ? "да" : "нет"}</td>
        <td><button class="btn small ghost" onclick='userEdit(${JSON.stringify(x)})'>изм.</button></td></tr>`).join("")) + "</div>";
  }
  const waybillMode = st.waybill_issue_mode || "strict_med_tech";
  const modeHtml = USER.role === "админ" ? `<div class="panel"><h3>Правила оформления путевых листов</h3>
    <label class="f">Режим оформления ПЛ<select data-set="waybill_issue_mode">
      <option value="strict_med_tech">Медик и механик обязательны</option>
      <option value="medical_only">Обязателен только медик</option>
      <option value="advisory">Свободное оформление с предупреждениями</option>
    </select></label>
    <div class="muted">Режим применяется к одиночному и массовому оформлению ПЛ в наряде.</div></div>` : "";
  const repeatHtml = USER.role === "админ" ? `<div class="panel"><h3>Настройки ремонта и ТО</h3>
    <label class="f">Период повторной неисправности, дней<input data-set="repair_repeat_days" type="number" min="1" max="365" value="${esc(st.repair_repeat_days || 30)}"></label>
    <div class="muted">Заявка с таким же описанием по тому же автобусу в пределах периода помечается как повторная.</div>
    <button class="btn" onclick="saveSettings()">Сохранить настройки ремонта</button></div>` : "";  const orgFields = [["org_name", "Наименование перевозчика"], ["org_address", "Адрес"], ["org_phone", "Телефон"],
    ["org_ogrn", "ОГРН"], ["org_inn", "ИНН"], ["org_okpo", "ОКПО"],
    ["org_owner", "Собственник / владелец (если отличается)"],
    ["org_control_place", "Место проведения техконтроля"],
    ["org_license_reg", "Лицензия: регистрационный №"], ["org_license_series", "Лицензия: серия"],
    ["org_license_number", "Лицензия: №"], ["waybill_series", "Серия путевого листа"],
    ["waybill_prefix", "Префикс номера ПЛ"], ["session_timeout_min", "Тайм-аут сессии, мин"]];
  $("content").innerHTML = `
    <div class="panel"><h3>Реквизиты предприятия (печатаются в путевом листе)</h3>
      <div class="cols" style="display:grid; grid-template-columns:1fr 1fr; gap:0 16px">
      ${orgFields.map(([k, l]) => `<label class="f">${l}<input data-set="${k}" value="${esc(st[k] || "")}"></label>`).join("")}</div>
      <button class="btn" onclick="saveSettings()">Сохранить реквизиты</button></div>
    ${modeHtml}
    ${repeatHtml}
    <div class="panel"><h3>Коды видов времени (соответствие 1С)</h3>
      ${tbl(["Код", "Наименование", "Код в 1С", ""], codes.items.map(c => `<tr><td><b>${esc(c.code)}</b></td>
        <td>${esc(c.name)}</td><td><input style="width:80px" id="tc-${esc(c.code)}" value="${esc(c.code_1c || "")}"></td>
        <td><button class="btn small sec" onclick="saveCode('${esc(c.code)}', '${esc(c.name)}')">сохранить</button></td></tr>`).join(""))}</div>
    ${usersHtml}`;
  const wbMode = document.querySelector('[data-set="waybill_issue_mode"]');
  if (wbMode) wbMode.value = waybillMode;
};
async function saveSettings() {
  const out = {};
  document.querySelectorAll("[data-set]").forEach(i => out[i.dataset.set] = i.value);
  await api("/api/settings", { method: "POST", body: out }).catch(e => toast(e.message, true));
  toast("Настройки сохранены");
}
async function saveCode(code, name) {
  await api("/api/time-codes", { method: "POST", body: { code, name, code_1c: $("tc-" + code).value } })
    .catch(e => toast(e.message, true));
  toast("Код сохранён");
}
async function userEdit(u) {
  const v = await formModal(u ? "Пользователь" : "Новый пользователь", [
    { k: "username", label: "Логин" }, { k: "full_name", label: "ФИО" },
    { k: "role", label: "Роль", type: "select", options: ["админ", "диспетчер", "эксплуатация", "кадры", "бухгалтер", "механик", "медик", "топливо", "руководитель", "водитель"] },
    { k: "password", label: "Пароль (пусто — не менять)", type: "password" },
    { k: "active", label: "Активен (1/0)", type: "number", def: 1 }], u || {});
  if (!v) return;
  if (u) v.id = u.id;
  v.active = +v.active;
  await api("/api/users", { method: "POST", body: v }).catch(e => toast(e.message, true));
  toast("Сохранено"); route();
}

/* ================= АУДИТ ================= */
VIEWS.audit = async function () {
  const f = window._audit || { date_from: today(), date_to: today(), username: "", object_type: "" };
  window._audit = f;
  const d = await api(`/api/audit?date_from=${f.date_from}&date_to=${f.date_to}&username=${encodeURIComponent(f.username)}&object_type=${encodeURIComponent(f.object_type)}`);
  const rows = d.items.map(a => `<tr><td style="white-space:nowrap">${esc((a.ts || "").replace("T", " "))}</td>
    <td>${esc(a.username)}</td><td><b>${esc(a.action)}</b></td><td>${esc(a.object_type)} ${esc(a.object_id || "")}</td>
    <td class="muted" style="max-width:240px; overflow:hidden; text-overflow:ellipsis">${esc((a.old_value || "").slice(0, 160))}</td>
    <td class="muted" style="max-width:240px; overflow:hidden; text-overflow:ellipsis">${esc((a.new_value || "").slice(0, 160))}</td>
    <td>${esc(a.comment || "")}</td></tr>`).join("");
  $("content").innerHTML = `<div class="toolbar">
      с <input type="date" value="${f.date_from}" onchange="_audit.date_from=this.value; route()">
      по <input type="date" value="${f.date_to}" onchange="_audit.date_to=this.value; route()">
      пользователь <input style="width:120px" value="${esc(f.username)}" onchange="_audit.username=this.value; route()">
      объект <input style="width:120px" value="${esc(f.object_type)}" onchange="_audit.object_type=this.value; route()">
      <span class="muted">${d.items.length} записей</span>
    </div>` + tbl(["Время", "Пользователь", "Действие", "Объект", "Было", "Стало", "Комментарий"], rows);
};

/* ---------- запуск ---------- */
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && $("login-screen").style.display !== "none" && document.activeElement.id.startsWith("lg-")) doLogin();
});
if (TOKEN) boot(); else showLogin();
