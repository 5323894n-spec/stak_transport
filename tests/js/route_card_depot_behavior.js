"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

function harness(api, overrides = {}) {
  const renders = [];
  const toasts = [];
  const context = vm.createContext({
    console,
    Promise,
    setTimeout,
    clearTimeout,
    api,
    renders,
    toasts,
    USER: { role: "админ" },
    VIEWS: {},
    location: { hash: "" },
    history: { back() {} },
    today: () => "2026-07-28",
    toast: (...args) => toasts.push(args),
    formModal: async () => null,
    esc: value => String(value == null ? "" : value),
    tbl: (headers, rows) => rows,
    $: () => ({ innerHTML: "" }),
    L: {},
    document: { activeElement: null, querySelector: () => null },
    ...overrides,
  });
  context.window = context;
  const source = fs.readFileSync(
    path.resolve(__dirname, "../../static/route-card.js"), "utf8"
  );
  vm.runInContext(source, context, { filename: "static/route-card.js" });
  vm.runInContext(
    "renderRouteCard = state => renders.push({ state, direction: state.depotDirection, loading: state.depotLoading, error: state.depotError })",
    context,
  );
  return { context, renders, toasts };
}

function newState(context, routeId) {
  return vm.runInContext(`routeCardState(${routeId})`, context);
}

async function routeNavigationIgnoresLateResponse() {
  const depot = deferred();
  const stops = deferred();
  const { context, renders } = harness(url =>
    url.includes("depot-stops") ? depot.promise : stops.promise
  );
  const stateA = newState(context, 1);
  const pending = vm.runInContext('routeCardLoadDepot("depot_out")', context);
  assert.equal(renders.length, 1);

  const stateB = newState(context, 2);
  depot.resolve({ items: [{ stop_id: 11 }] });
  stops.resolve({ items: [{ id: 11, name: "A" }] });
  await pending;

  assert.equal(stateA.depotDrafts.depot_out, null);
  assert.equal(stateB.depotDrafts.depot_out, null);
  assert.equal(stateB.depotError, "");
  assert.equal(renders.length, 1, "late response must not render abandoned route state");
}

async function cachedDirectionInvalidatesOldRequest() {
  const depot = deferred();
  const stops = deferred();
  const { context, renders } = harness(url =>
    url.includes("depot-stops") ? depot.promise : stops.promise
  );
  const state = newState(context, 1);
  state.depotDrafts.depot_in = [];
  const pending = vm.runInContext('routeCardLoadDepot("depot_out")', context);
  const tokenBeforeSwitch = state.depotLoadToken;

  await vm.runInContext('routeCardDepotDirection("depot_in")', context);
  assert.ok(state.depotLoadToken > tokenBeforeSwitch);
  assert.equal(state.depotLoading, false);
  const rendersAfterSwitch = renders.length;

  depot.reject(new Error("late out error"));
  stops.resolve({ items: [] });
  await pending;

  assert.equal(state.depotDirection, "depot_in");
  assert.equal(state.depotError, "");
  assert.equal(renders.length, rendersAfterSwitch);
}

async function invalidRowDoesNotPut() {
  const calls = [];
  const { context } = harness(async (url, options) => {
    calls.push({ url, options });
    return { items: [] };
  });
  const state = newState(context, 1);
  state.depotDrafts.depot_out = [{
    stop_id: 10,
    distance_from_prev_km: "not-a-number",
    run_time_day_sec: "30",
    run_time_night_sec: "40",
  }];

  const payload = vm.runInContext("routeCardDepotPayload(window._routeCard)", context);
  assert.equal(payload, null);
  await vm.runInContext("routeCardSaveDepot()", context);
  assert.equal(calls.filter(call => call.options?.method === "PUT").length, 0);
}

async function emptyRowsPutAndReload() {
  const calls = [];
  const { context } = harness(async (url, options) => {
    calls.push({ url, options });
    return { items: [] };
  });
  const state = newState(context, 1);
  state.depotDrafts.depot_out = [];

  await vm.runInContext("routeCardSaveDepot()", context);

  const put = calls.find(call => call.options?.method === "PUT");
  assert.ok(put, "empty rows must still be saved");
  assert.deepEqual(JSON.parse(JSON.stringify(put.options.body)), { items: [] });
  assert.ok(calls.some(call => call.url.includes("depot-stops?direction=depot_out")));
  assert.ok(calls.some(call => call.url === "/api/stops?active=1"));
}

async function validPayloadUsesSequenceAndNumbers() {
  const { context } = harness(async () => ({ items: [] }));
  const state = newState(context, 1);
  state.depotDrafts.depot_out = [
    { stop_id: "10", distance_from_prev_km: "1,25", run_time_day_sec: "30", run_time_night_sec: "40" },
    { stop_id: 20, distance_from_prev_km: "2.5", run_time_day_sec: "50", run_time_night_sec: "60" },
  ];

  const payload = vm.runInContext("routeCardDepotPayload(window._routeCard)", context);
  assert.deepEqual(
    JSON.parse(JSON.stringify(payload)),
    [
      { stop_id: 10, sequence: 1, distance_from_prev_km: 1.25, run_time_day_sec: 30, run_time_night_sec: 40 },
      { stop_id: 20, sequence: 2, distance_from_prev_km: 2.5, run_time_day_sec: 50, run_time_night_sec: 60 },
    ],
  );
}

async function loadFailureRequiresRetryBeforeSave() {
  const calls = [];
  let attempt = 0;
  const { context } = harness(async (url, options) => {
    calls.push({ url, options });
    if (options?.method === "PUT") return {};
    if (url.includes("depot-stops") && ++attempt === 1) throw new Error("load failed");
    return { items: [] };
  });
  const state = newState(context, 1);
  await vm.runInContext('routeCardLoadDepot("depot_out")', context);
  assert.equal(state.depotDrafts.depot_out, null);
  const failedHtml = vm.runInContext("routeCardDepot(window._routeCard)", context);
  assert.match(failedHtml, /Повторить загрузку/);
  assert.doesNotMatch(failedHtml, /routeCardSaveDepot/);
  await vm.runInContext("routeCardSaveDepot()", context);
  assert.equal(calls.filter(call => call.options?.method === "PUT").length, 0);
  await vm.runInContext("routeCardRetryDepot()", context);
  assert.ok(Array.isArray(state.depotDrafts.depot_out));
  assert.equal(state.depotError, "");
}

async function oldRouteSaveCannotReloadCurrentRoute() {
  const put = deferred();
  const calls = [];
  const { context, renders, toasts } = harness((url, options) => {
    calls.push({ url, options });
    return options?.method === "PUT" ? put.promise : Promise.resolve({ items: [] });
  });
  const stateA = newState(context, 1);
  stateA.depotDrafts.depot_out = [];
  const pending = vm.runInContext("routeCardSaveDepot()", context);
  const rendersAtNavigation = renders.length;
  const stateB = newState(context, 2);
  put.resolve({});
  await pending;
  assert.equal(calls.filter(call => !call.options?.method).length, 0);
  assert.equal(stateB.depotDrafts.depot_out, null);
  assert.equal(toasts.length, 0);
  assert.equal(renders.length, rendersAtNavigation);
}

async function oldDirectionSaveCannotReloadNewDirection() {
  const put = deferred();
  const calls = [];
  const { context, toasts } = harness((url, options) => {
    calls.push({ url, options });
    return options?.method === "PUT" ? put.promise : Promise.resolve({ items: [] });
  });
  const state = newState(context, 1);
  state.depotDrafts.depot_out = [];
  state.depotDrafts.depot_in = [];
  const pending = vm.runInContext("routeCardSaveDepot()", context);
  state.depotDirection = "depot_in";
  put.resolve({});
  await pending;
  assert.equal(calls.filter(call => !call.options?.method).length, 0);
  assert.equal(toasts.length, 0);
}

async function savingLocksAllDepotMutations() {
  const put = deferred();
  const calls = [];
  const { context } = harness((url, options) => {
    calls.push({ url, options });
    return options?.method === "PUT" ? put.promise : Promise.resolve({ items: [] });
  });
  const state = newState(context, 1);
  state.depotStopOptions = [{ id: 10, name: "A" }, { id: 20, name: "B" }];
  state.depotDrafts.depot_out = [
    { stop_id: 10, distance_from_prev_km: "1", run_time_day_sec: "30", run_time_night_sec: "40" },
    { stop_id: 20, distance_from_prev_km: "2", run_time_day_sec: "50", run_time_night_sec: "60" },
  ];
  state.depotDrafts.depot_in = [];
  const before = JSON.stringify(state.depotDrafts.depot_out);
  const pending = vm.runInContext("routeCardSaveDepot()", context);
  assert.equal(state.depotSaving, true);
  await vm.runInContext('routeCardDepotDirection("depot_in")', context);
  vm.runInContext('routeCardDepotChange(0, "run_time_day_sec", "999")', context);
  vm.runInContext("routeCardDepotMove(0, 1)", context);
  vm.runInContext("routeCardDepotRemove(0)", context);
  await vm.runInContext("routeCardDepotAdd()", context);
  await vm.runInContext("routeCardSaveDepot()", context);
  assert.equal(calls.filter(call => call.options?.method === "PUT").length, 1);
  assert.equal(state.depotDirection, "depot_out");
  assert.equal(JSON.stringify(state.depotDrafts.depot_out), before);
  assert.match(vm.runInContext("routeCardDepot(window._routeCard)", context), /disabled/);
  put.resolve({});
  await pending;
  assert.ok(calls.some(call => call.url.includes("depot-stops?direction=depot_out")));
}

async function mainRouteRuntimeValidation() {
  const calls = [];
  const { context } = harness(async (url, options) => {
    calls.push({ url, options });
    return { items: [] };
  });
  const state = newState(context, 1);
  state.tab = "segments";
  state.drafts.forward = [{
    stop_id: 10, stop: { id: 10, name: "A" }, distance_from_prev_km: 0,
    run_time_sec: 20, run_time_day_sec: "30", run_time_night_sec: "40",
    dwell_time_sec: 0, distance_source: "manual",
    boarding_allowed: true, alighting_allowed: true, is_timing_point: false,
  }];
  vm.runInContext('routeCardSegment(0, "run_time_day_sec", "-1")', context);
  assert.equal(state.drafts.forward[0].run_time_day_sec, "-1");
  await vm.runInContext("routeCardSaveTrace()", context);
  assert.equal(calls.filter(call => call.options?.method === "PUT").length, 0);
  assert.ok(state.segmentErrors[0]);
  assert.match(vm.runInContext("routeCardSegments(window._routeCard)", context), /route-segment-error/);
  vm.runInContext('routeCardSegment(0, "run_time_day_sec", "31")', context);
  vm.runInContext('routeCardSegment(0, "run_time_night_sec", "41")', context);
  vm.runInContext("routeCardReload = async () => {}", context);
  await vm.runInContext("routeCardSaveTrace()", context);
  const saved = calls.find(call => call.options?.method === "PUT");
  assert.equal(saved.options.body.items[0].run_time_day_sec, 31);
  assert.equal(saved.options.body.items[0].run_time_night_sec, 41);
}

async function documentModalKeyboardAndFocus() {
  const documentStub = { activeElement: null, querySelector: () => null };
  const focusable = name => ({ name, focus() { documentStub.activeElement = this; } });
  const first = focusable("first");
  const last = focusable("last");
  const modal = { querySelector: () => first, querySelectorAll: () => [first, last] };
  const reopenedButton = focusable("opener");
  documentStub.querySelector = selector => selector === ".route-document-dialog"
    ? modal : selector === ".route-document-open" ? reopenedButton : null;
  const { context } = harness(async () => ({}), { document: documentStub });
  const state = newState(context, 1);
  vm.runInContext("renderRouteCard = () => {}", context);
  vm.runInContext("routeCardOpenDocumentDialog()", context);
  assert.equal(documentStub.activeElement, first);
  documentStub.activeElement = last;
  const tab = { key: "Tab", shiftKey: false, preventDefault() { this.prevented = true; } };
  context.modalEvent = tab;
  vm.runInContext("routeCardDocumentKeydown(modalEvent)", context);
  assert.equal(documentStub.activeElement, first);
  assert.equal(tab.prevented, true);
  documentStub.activeElement = first;
  const shiftTab = { key: "Tab", shiftKey: true, preventDefault() { this.prevented = true; } };
  context.modalEvent = shiftTab;
  vm.runInContext("routeCardDocumentKeydown(modalEvent)", context);
  assert.equal(documentStub.activeElement, last);
  const escape = { key: "Escape", preventDefault() { this.prevented = true; } };
  context.modalEvent = escape;
  vm.runInContext("routeCardDocumentKeydown(modalEvent)", context);
  assert.equal(state.documentDialogOpen, false);
  assert.equal(documentStub.activeElement, reopenedButton);
}
const scenarios = {
  route_navigation: routeNavigationIgnoresLateResponse,
  cached_direction: cachedDirectionInvalidatesOldRequest,
  invalid_row: invalidRowDoesNotPut,
  empty_rows: emptyRowsPutAndReload,
  valid_payload: validPayloadUsesSequenceAndNumbers,
  load_failure_retry: loadFailureRequiresRetryBeforeSave,
  save_route_race: oldRouteSaveCannotReloadCurrentRoute,
  save_direction_race: oldDirectionSaveCannotReloadNewDirection,
  saving_locks_mutations: savingLocksAllDepotMutations,
  main_runtime_validation: mainRouteRuntimeValidation,
  document_modal_keyboard: documentModalKeyboardAndFocus,
};

async function main() {
  const scenario = process.argv[2];
  assert.ok(scenarios[scenario], `unknown scenario: ${scenario}`);
  await scenarios[scenario]();
}

main().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
