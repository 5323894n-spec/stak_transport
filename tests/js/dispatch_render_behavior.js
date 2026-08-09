"use strict";
// Regression: VIEWS.dispatch must WRITE into #content (route() ignores return value).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const content = { innerHTML: "" };
const board = {
  date: "2026-08-09", source_mode: "manual", has_order: true, order_approved: true,
  rows: [{ output_id: 1, order_line_id: 1, route_number: "7", output_number: 1, shift_number: 1,
           driver_fio: "Иванов", garage_number: "Г1", plan_release: "05:50", actual_release: "05:55",
           deviation_min: 5, status: "выпущен", reason: null }],
  summary: { planned: 1, released: 1, on_line: 0, off_line: 0, disrupted: 0, replaced: 0,
             release_regularity: 0, trip_regularity: 100, trips_recorded: 1 },
};
const ctx = vm.createContext({
  console, VIEWS: {}, URLSearchParams,
  api: async (u) => (u.includes("/board") ? board : { items: [] }),
  esc: (v) => String(v == null ? "" : v),
  $: (id) => (id === "content" ? content : { innerHTML: "", value: "" }),
  openWin() {}, today: () => "2026-08-09", route() {}, toast() {}, prompt: () => null,
});
ctx.window = ctx;
vm.runInContext(fs.readFileSync(path.resolve(__dirname, "../../static/dispatch.js"), "utf8"), ctx);

(async () => {
  ctx.window._dispatch = { date: "2026-08-09", tab: "release", selectedLine: null };
  content.innerHTML = "";
  await ctx.VIEWS.dispatch();
  assert.ok(content.innerHTML.includes("dispatch-board"), "dispatch view must write board into #content");
  assert.ok(content.innerHTML.includes("Иванов"), "dispatch board must contain the row data");
  console.log("dispatch render OK");
})().catch((e) => { console.error(e); process.exit(1); });
