"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const downloads = [];
const toasts = [];
const context = vm.createContext({
  console, Promise, URLSearchParams, setTimeout, clearTimeout,
  api: async () => ({}), USER: { role: "админ" }, VIEWS: {},
  location: { hash: "" }, history: { back() {} }, today: () => "2026-07-28",
  toast: (...args) => toasts.push(args),
  openWin: url => downloads.push({ url, open: context.window._routeCard.documentDialogOpen }),
  formModal: async () => null, esc: value => String(value == null ? "" : value),
  tbl: (headers, rows) => rows, $: () => ({ innerHTML: "" }), L: {},
  document: { activeElement: null, querySelector: () => null },
});
context.window = context;
vm.runInContext(fs.readFileSync(path.resolve(__dirname, "../../static/route-card.js"), "utf8"), context);
vm.runInContext("renderRouteCard = () => {}", context);
const state = vm.runInContext("routeCardState(17)", context);

state.documentDialogOpen = true; state.documentDate = "";
vm.runInContext("routeCardDocumentDownload()", context);
assert.deepEqual(downloads, []); assert.equal(state.documentDialogOpen, true);
assert.deepEqual(toasts, [["Укажите дату начала действия", true]]);

state.documentType = "schedule"; state.documentSeason = "winter"; state.documentDate = "2026-07-28";
vm.runInContext("routeCardDocumentDownload()", context);
assert.deepEqual(downloads[0], { url: "/api/routes/17/schedule-document.xlsx?season=winter&effective_date=2026-07-28", open: true });
assert.equal(state.documentDialogOpen, false);

state.documentDialogOpen = true; state.documentType = "erm"; state.documentSeason = "summer"; state.documentDate = "2026-08-01";
vm.runInContext("routeCardDocumentDownload()", context);
assert.deepEqual(downloads[1], { url: "/api/routes/17/erm-export.xlsx?season=summer&effective_date=2026-08-01", open: true });
assert.equal(state.documentDialogOpen, false);
