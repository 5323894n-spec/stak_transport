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

function harness(api) {
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
    tbl: () => "",
    $: () => ({ innerHTML: "" }),
    L: {},
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

const scenarios = {
  route_navigation: routeNavigationIgnoresLateResponse,
  cached_direction: cachedDirectionInvalidatesOldRequest,
  invalid_row: invalidRowDoesNotPut,
  empty_rows: emptyRowsPutAndReload,
  valid_payload: validPayloadUsesSequenceAndNumbers,
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
