// static/revenue.js — вкладка «Выручка»: тарифы, листы выручки, отчёты.

function revenueRecalcExpected(lines) {
  return (lines || []).reduce(
    (sum, ln) => sum + (Number(ln.unit_price) || 0) * (Number(ln.tickets_count) || 0),
    0,
  );
}
if (typeof window !== "undefined") window.revenueRecalcExpected = revenueRecalcExpected;

function revenueStatusBadge(status) {
  return `<span class="revenue-status revenue-status-${esc(status)}">${esc(status)}</span>`;
}

if (typeof VIEWS !== "undefined") {
  VIEWS.revenue = async function () {
    const st = window._revenue || { tab: "sheets" };
    window._revenue = st;
    const toolbar = `<div class="route-card-toolbar revenue-toolbar">
      <button class="btn ${st.tab !== "tariffs" ? "" : "sec"}" onclick="window._revenue.tab='sheets';route()">Листы выручки</button>
      <button class="btn ${st.tab === "tariffs" ? "" : "sec"}" onclick="window._revenue.tab='tariffs';route()">Тарифы</button>
      <button class="btn sec" onclick="openWin('/api/revenue/report.xlsx?date_from='+thisMonth()+'-01&date_to='+today()+'&group_by=route')">Отчёт по маршрутам (Excel)</button>
    </div>`;

    if (st.tab === "tariffs") {
      const [types, tariffs] = await Promise.all([
        api("/api/revenue/fare-types?include_inactive=true"),
        api("/api/revenue/tariffs"),
      ]);
      const priceByType = {};
      tariffs.items.forEach(t => {
        (priceByType[t.fare_type_id] = priceByType[t.fare_type_id] || []).push(t);
      });
      const rows = types.items.map(t => {
        const versions = (priceByType[t.id] || [])
          .map(v => `${v.price} ₽ c ${esc(v.valid_from)}${v.valid_to ? " по " + esc(v.valid_to) : ""}`)
          .join("<br>") || "<span class=\"muted\">нет тарифа</span>";
        return `<tr><td>${esc(t.name)}</td><td>${esc(t.unit)}</td><td>${versions}</td></tr>`;
      }).join("");
      $("content").innerHTML = `<div class="revenue-tab">${toolbar}
        <h3>Виды билетов и тарифы</h3>
        <table><thead><tr><th>Вид билета</th><th>Единица</th><th>Тарифы (версии)</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
      return;
    }

    const data = await api("/api/revenue/sheets");
    const rows = data.items.map(s =>
      `<tr><td>${s.number}</td><td>${esc(s.date)}</td>` +
      `<td class="num">${(s.expected_amount || 0).toFixed(2)}</td>` +
      `<td class="num">${(s.submitted_amount || 0).toFixed(2)}</td>` +
      `<td class="num ${s.difference < 0 ? "revenue-shortage" : ""}">${(s.difference || 0).toFixed(2)}</td>` +
      `<td>${revenueStatusBadge(s.status)}</td></tr>`,
    ).join("");
    const empty = data.items.length ? "" : `<p class="muted">Листы выручки ещё не заведены. Лист создаётся из путевого листа.</p>`;
    $("content").innerHTML = `<div class="revenue-tab">${toolbar}
      <h3>Листы выручки</h3>${empty}
      <table><thead><tr><th>№</th><th>Дата</th><th>Ожидаемо, ₽</th><th>Сдано, ₽</th><th>Разница, ₽</th><th>Статус</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  };
}
