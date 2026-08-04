"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class FakeDate extends Date {
  constructor(...args) {
    super(...(args.length ? args : ["2026-07-27T21:00:00.000Z"]));
  }

  getFullYear() { return 2026; }
  getMonth() { return 6; }
  getDate() { return 28; }
  toISOString() { return "2026-07-27T21:00:00.000Z"; }
}

const downloads = [];
const toasts = [];
const dateInput = {
  customValidity: "",
  reportValidityCalls: 0,
  focusCalls: 0,
  setCustomValidity(message) { this.customValidity = message; },
  reportValidity() { this.reportValidityCalls += 1; return false; },
  focus() { this.focusCalls += 1; },
};
const context = vm.createContext({
  console, Promise, URLSearchParams, setTimeout, clearTimeout, Date: FakeDate,
  api: async () => ({}), USER: { role: "админ" }, VIEWS: {},
  location: { hash: "" }, history: { back() {} },
  today: () => "2026-07-27",
  toast: (...args) => toasts.push(args),
  openWin: url => downloads.push({ url, open: context.window._routeCard.documentDialogOpen }),
  formModal: async () => null, esc: value => String(value == null ? "" : value),
  tbl: (headers, rows) => rows, $: () => ({ innerHTML: "" }), L: {},
  document: {
    activeElement: null,
    querySelector: () => null,
    getElementById: id => id === "route-document-date" ? dateInput : null,
  },
});
context.window = context;
vm.runInContext(fs.readFileSync(path.resolve(__dirname, "../../static/route-card.js"), "utf8"), context);
vm.runInContext("renderRouteCard = () => {}", context);
const state = vm.runInContext("routeCardState(17)", context);

assert.equal(state.documentDate, "2026-07-28");

state.documentDialogOpen = true; state.documentDate = "";
vm.runInContext("routeCardDocumentDownload()", context);
assert.deepEqual(downloads, []); assert.equal(state.documentDialogOpen, true);
assert.deepEqual(toasts, [["Укажите дату начала действия", true]]);
assert.equal(dateInput.customValidity, "Укажите дату начала действия");
assert.equal(dateInput.reportValidityCalls, 1);
assert.equal(dateInput.focusCalls, 1);

state.documentType = "schedule"; state.documentSeason = "winter"; state.documentDate = "2026-07-28";
vm.runInContext("routeCardDocumentDownload()", context);
assert.deepEqual(downloads[0], { url: "/api/routes/17/schedule-document.xlsx?season=winter&effective_date=2026-07-28", open: true });
assert.equal(state.documentDialogOpen, false);

state.documentDialogOpen = true; state.documentType = "erm"; state.documentSeason = "summer"; state.documentDate = "2026-08-01";
vm.runInContext("routeCardDocumentDownload()", context);
assert.deepEqual(downloads[1], { url: "/api/routes/17/erm-export.xlsx?season=summer&effective_date=2026-08-01", open: true });
assert.equal(state.documentDialogOpen, false);

state.documentDialogOpen = true; state.documentType = "passport-d"; state.documentSeason = "winter"; state.documentDate = "2026-08-03";
vm.runInContext("routeCardDocumentDownload()", context);
assert.deepEqual(downloads[2], { url: "/api/routes/17/passport-document.docx?style=D&effective_date=2026-08-03", open: true });
assert.ok(!downloads[2].url.includes("season"), "passport URL must not carry a season");
assert.equal(state.documentDialogOpen, false);

state.documentDialogOpen = true; state.documentType = "passport-f"; state.documentSeason = "summer"; state.documentDate = "2026-08-03";
vm.runInContext("routeCardDocumentDownload()", context);
assert.deepEqual(downloads[3], { url: "/api/routes/17/passport-document.docx?style=F&effective_date=2026-08-03", open: true });
assert.ok(!downloads[3].url.includes("season"), "passport URL must not carry a season");
assert.equal(state.documentDialogOpen, false);
