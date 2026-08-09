// static/dispatch.js — вкладка «Диспетчер»: табло выпуска и регулярность.

function dispatchDeviationLabel(min) {
  if (min == null) return "—";
  if (min === 0) return "0′";
  return (min > 0 ? "+" : "−") + Math.abs(min) + "′";
}

function dispatchOnTime(min, tolerance) {
  if (min == null) return false;
  return Math.abs(min) <= (tolerance == null ? 2 : tolerance);
}

if (typeof window !== "undefined") {
  window.dispatchDeviationLabel = dispatchDeviationLabel;
  window.dispatchOnTime = dispatchOnTime;
}

async function dispatchSetMode(mode) {
  const st = window._dispatch;
  await api("/api/dispatch/source-mode", { method: "PUT", body: { date: st.date, mode } });
  route();
}

async function dispatchStatus(outputId, status, needReason) {
  const st = window._dispatch;
  let reason = null;
  if (needReason) {
    reason = prompt("Причина:");
    if (reason == null) return;
  }
  try {
    await api(`/api/dispatch/outputs/${outputId}/status`, { method: "POST", body: { status, reason } });
    route();
  } catch (error) { toast(error.message, true); }
}

async function dispatchSimulate(garageNumber) {
  const st = window._dispatch;
  try {
    await api("/api/dispatch/telemetry", { method: "POST", body: { date: st.date, garage_number: garageNumber, event: "release" } });
    toast("GPS: выпуск смоделирован");
    route();
  } catch (error) { toast(error.message, true); }
}

async function dispatchSaveTrip(lineId, tripNumber) {
  const input = document.getElementById(`disp-trip-${lineId}-${tripNumber}`);
  if (!input || !input.value) return;
  try {
    await api(`/api/dispatch/trips/${lineId}/${tripNumber}`, { method: "PUT", body: { actual_dep: input.value } });
    route();
  } catch (error) { toast(error.message, true); }
}

function dispatchSelectOutput(lineId) { window._dispatch.selectedLine = lineId; route(); }

if (typeof VIEWS !== "undefined") {
  VIEWS.dispatch = async function () {
    const st = window._dispatch || { date: today(), tab: "release", selectedLine: null };
    window._dispatch = st;
    const board = await api(`/api/dispatch/board?date=${st.date}`);
    const s = board.summary;
    const gps = board.source_mode === "gps";
    const toolbar = `<div class="route-card-toolbar dispatch-toolbar">
      <label>Дата <input type="date" value="${esc(st.date)}" onchange="window._dispatch.date=this.value;route()"></label>
      <span class="dispatch-source">Источник:
        <button class="btn ${gps ? "sec" : ""}" onclick="dispatchSetMode('manual')">Ручной</button>
        <button class="btn ${gps ? "" : "sec"}" onclick="dispatchSetMode('gps')">GPS</button></span>
      <button class="btn ${st.tab === "release" ? "" : "sec"}" onclick="window._dispatch.tab='release';route()">Выпуск</button>
      <button class="btn ${st.tab === "adherence" ? "" : "sec"}" onclick="window._dispatch.tab='adherence';route()">Регулярность</button>
      <button class="btn sec" onclick="openWin('/api/dispatch/report.xlsx?date='+window._dispatch.date)">Отчёт Excel</button>
    </div>`;
    const summary = `<div class="cards dispatch-summary">
      <div class="card"><div class="num">${s.planned}</div><div class="lbl">план выходов</div></div>
      <div class="card"><div class="num">${s.released}</div><div class="lbl">выпущено</div></div>
      <div class="card"><div class="num">${s.on_line}</div><div class="lbl">на линии</div></div>
      <div class="card"><div class="num">${s.off_line}</div><div class="lbl">сходы</div></div>
      <div class="card"><div class="num">${s.disrupted}</div><div class="lbl">срывы</div></div>
      <div class="card"><div class="num">${s.release_regularity}%</div><div class="lbl">регулярность выпуска</div></div></div>`;

    if (!board.has_order) {
      $("content").innerHTML = `<div class="dispatch-tab">${toolbar}<p class="muted">На эту дату нет утверждённого наряда. Сформируйте и утвердите наряд на день.</p></div>`;
      return;
    }

    if (st.tab === "adherence") {
      const line = st.selectedLine || (board.rows[0] && board.rows[0].order_line_id);
      const options = board.rows.map(r => `<option value="${r.order_line_id}" ${r.order_line_id === line ? "selected" : ""}>№ ${esc(r.route_number)} · выход ${r.output_number}/${r.shift_number} · ${esc(r.driver_fio || "")}</option>`).join("");
      const facts = line ? (await api(`/api/dispatch/adherence?date=${st.date}&order_line_id=${line}`)).items : [];
      const rows = facts.map(f => `<tr><td>${f.trip_number}</td><td>${esc(f.plan_dep || "—")}</td>
        <td><input id="disp-trip-${line}-${f.trip_number}" type="time" value="${esc(f.actual_dep || "")}"><button class="btn small" onclick="dispatchSaveTrip(${line},${f.trip_number})">✓</button></td>
        <td class="num ${dispatchOnTime(f.deviation_min) ? "" : "revenue-shortage"}">${dispatchDeviationLabel(f.deviation_min)}</td>
        <td>${f.on_time == null ? "" : (f.on_time ? "вовремя" : "с отклонением")}</td></tr>`).join("");
      $("content").innerHTML = `<div class="dispatch-tab">${toolbar}${summary}
        <label>Выход <select onchange="dispatchSelectOutput(+this.value)">${options}</select></label>
        <p class="muted">Регулярность рейсов: ${s.trip_regularity != null ? s.trip_regularity + "%" : "—"}</p>
        <table class="dispatch-board"><thead><tr><th>Рейс</th><th>План</th><th>Факт</th><th>Откл.</th><th>Оценка</th></tr></thead><tbody>${rows}</tbody></table></div>`;
      return;
    }

    const rows = board.rows.map(r => {
      const actions = gps
        ? `<button class="btn small sec" onclick="dispatchSimulate('${esc(r.garage_number || "")}')">Смоделировать выпуск</button>`
        : `<button class="btn small" onclick="dispatchStatus(${r.output_id},'выпущен',false)">Выпустить</button>
           <button class="btn small sec" onclick="dispatchStatus(${r.output_id},'на_линии',false)">На линии</button>
           <button class="btn small sec" onclick="dispatchStatus(${r.output_id},'сошёл',true)">Сошёл</button>
           <button class="btn small danger" onclick="dispatchStatus(${r.output_id},'срыв',true)">Срыв</button>`;
      return `<tr><td>№ ${esc(r.route_number)}</td><td>${r.output_number}/${r.shift_number}</td>
        <td>${esc(r.driver_fio || "—")}</td><td>${esc(r.garage_number || "—")}</td>
        <td>${esc(r.plan_release || "—")}</td><td>${esc(r.actual_release || "—")}</td>
        <td class="num ${dispatchOnTime(r.deviation_min) ? "" : "revenue-shortage"}">${dispatchDeviationLabel(r.deviation_min)}</td>
        <td><span class="dispatch-status dispatch-status-${esc(r.status)}">${esc(r.status)}</span>${r.reason ? ' · ' + esc(r.reason) : ''}</td>
        <td class="dispatch-actions">${actions}</td></tr>`;
    }).join("");
    $("content").innerHTML = `<div class="dispatch-tab">${toolbar}${summary}
      ${gps ? '<p class="muted">Режим GPS: статусы поступают по телеметрии. Кнопка «Смоделировать выпуск» имитирует событие.</p>' : ''}
      <table class="dispatch-board"><thead><tr><th>Маршрут</th><th>Выход</th><th>Водитель</th><th>Автобус</th><th>План</th><th>Факт</th><th>Откл.</th><th>Статус</th><th>Действия</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  };
}
