"use strict";
// Regression: VIEWS.revenue must WRITE into #content (route() ignores return value).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const content = { innerHTML: "" };
const payloads = {
  "/api/revenue/sheets": { items: [{ number: 1, date: "2026-08-09", expected_amount: 100, submitted_amount: 90, difference: -10, status: "сдан" }] },
  "/api/revenue/fare-types": { items: [{ id: 1, name: "Разовый", unit: "поездка" }] },
  "/api/revenue/tariffs": { items: [] },
};
const ctx = vm.createContext({
  console, VIEWS: {}, URLSearchParams,
  api: async (u) => payloads[u.split("?")[0]] || { items: [] },
  esc: (v) => String(v == null ? "" : v),
  $: (id) => (id === "content" ? content : { innerHTML: "" }),
  openWin() {}, today: () => "2026-08-09", thisMonth: () => "2026-08", route() {}, toast() {},
});
ctx.window = ctx;
vm.runInContext(fs.readFileSync(path.resolve(__dirname, "../../static/revenue.js"), "utf8"), ctx);

(async () => {
  ctx.window._revenue = { tab: "sheets" };
  content.innerHTML = "";
  await ctx.VIEWS.revenue();
  assert.ok(content.innerHTML.includes("Листы выручки"), "revenue view must write board into #content");
  console.log("revenue render OK");
})().catch((e) => { console.error(e); process.exit(1); });
