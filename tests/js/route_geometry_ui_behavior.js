"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const editor = require("../../static/route-geometry-editor.js");

const anchors = [[35.9, 56.8], [35.92, 56.82]];
const geometry = {
  type: "LineString",
  coordinates: [anchors[0], [35.91, 56.81], anchors[1]],
};

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function routeCardHarness(apiImpl = async () => ({}), role = "админ") {
  const calls = [];
  const confirms = [];
  const toasts = [];
  const context = {
    console,
    RouteGeometryEditor: editor,
    VIEWS: {},
    USER: { role },
    location: { hash: "#/routeCard/1" },
    document: { querySelector: () => null },
    FormData: class FormData {},
    setTimeout: () => 1,
    clearTimeout: () => {},
    confirm: message => {
      calls.push({ kind: "confirm", message });
      return confirms.length ? confirms.shift() : false;
    },
    api: async (url, options = {}) => {
      calls.push({ kind: "api", url, options });
      return apiImpl(url, options);
    },
    toast: (message, isError) => toasts.push({ message, isError: !!isError }),
    esc: value => value == null ? "" : String(value),
    tbl: () => "",
    $: () => ({ innerHTML: "" }),
  };
  context.window = context;
  vm.createContext(context);
  const source = fs.readFileSync(path.join(__dirname, "../../static/route-card.js"), "utf8");
  vm.runInContext(source, context, { filename: "route-card.js" });
  context.renderRouteCard = () => {};
  const state = context.routeCardState(1);
  state.network = {
    route: { id: 1, number: "G-1", name: "Геометрия" },
    forward: [
      { id: 1, sequence: 1, stop: { id: 11, name: "Первая", longitude: anchors[0][0], latitude: anchors[0][1] } },
      { id: 2, sequence: 2, stop: { id: 12, name: "Вторая", longitude: anchors[1][0], latitude: anchors[1][1] } },
    ],
    backward: [
      { id: 3, sequence: 1, stop: { id: 12, name: "Вторая", longitude: anchors[1][0], latitude: anchors[1][1] } },
      { id: 4, sequence: 2, stop: { id: 11, name: "Первая", longitude: anchors[0][0], latitude: anchors[0][1] } },
    ],
    geometries: {
      forward: { geometry: plain(geometry), source: "manual", version: 4 },
      backward: null,
    },
  };
  return { context, state, calls, confirms, toasts };
}

async function saveKeepsDraftAfterConflict() {
  const { context, state } = routeCardHarness(async () => {
    const error = new Error("Линия уже изменена другим пользователем");
    error.status = 409;
    throw error;
  });
  context.routeCardStartGeometryEdit();
  editor.insertVertex(state.geometryEditor.draft, 0, [35.905, 56.805]);
  await context.routeCardSaveGeometry();
  assert.equal(state.geometryEditor.active, true);
  assert.equal(state.geometryEditor.draft.coordinates.length, 4);
  assert.match(state.geometryEditor.error, /другим пользователем/);
  assert.equal(state.geometryEditor.saving, false);
}

async function saveClosesOnlyAfterSuccessfulResponse() {
  let resolveSave;
  const pending = new Promise(resolve => { resolveSave = resolve; });
  let state;
  const harness = routeCardHarness(async (url, options) => {
    if (options.method === "PUT") return pending;
    if (url.endsWith("/network")) return state.network;
    return {};
  });
  ({ state } = harness);
  const { context, calls } = harness;
  context.routeCardStartGeometryEdit();
  editor.moveVertex(state.geometryEditor.draft, 1, [35.911, 56.811]);
  const saving = context.routeCardSaveGeometry();
  assert.equal(state.geometryEditor.active, true);
  assert.equal(state.geometryEditor.saving, true);
  const request = calls.find(call => call.kind === "api" && call.options.method === "PUT");
  assert.equal(request.url, "/api/routes/1/geometry/forward");
  assert.equal(request.options.body.expected_version, 4);
  assert.deepEqual(request.options.body.geometry.coordinates[1], [35.911, 56.811]);
  resolveSave({ version: 5 });
  await saving;
  assert.equal(state.geometryEditor.active, false);
  assert.equal(state.geometryEditor.draft, null);
}

function cancelAndNavigationGuard() {
  const { context, state, confirms } = routeCardHarness();
  context.routeCardStartGeometryEdit();
  editor.insertVertex(state.geometryEditor.draft, 0, [35.905, 56.805]);
  confirms.push(false);
  context.routeCardDirection("backward");
  assert.equal(state.direction, "forward");
  assert.equal(state.geometryEditor.active, true);
  confirms.push(true);
  context.routeCardDirection("backward");
  assert.equal(state.direction, "backward");
  assert.equal(state.geometryEditor.active, false);

  state.direction = "forward";
  state.tab = "map";
  context.routeCardStartGeometryEdit();
  editor.insertVertex(state.geometryEditor.draft, 0, [35.905, 56.805]);
  confirms.push(false);
  context.routeCardTab("stops");
  assert.equal(state.tab, "map");
}

async function resetRequiresConfirmationAndVersion() {
  const { context, state, calls, confirms } = routeCardHarness();
  confirms.push(false);
  await context.routeCardResetGeometry();
  assert.equal(calls.filter(call => call.kind === "api").length, 0);
  confirms.push(true);
  await context.routeCardResetGeometry();
  const request = calls.find(call => call.kind === "api");
  assert.equal(request.url, "/api/routes/1/geometry/forward");
  assert.equal(request.options.method, "DELETE");
  assert.equal(request.options.body.expected_version, 4);
  assert.equal(state.geometryEditor.active, false);
}

async function osrmGuardsDirtyDraftAndSendsGeometryVersion() {
  const preview = {
    preview_token: "preview-1",
    geometry_version: 4,
    geometry: plain(geometry),
    diff: [],
  };
  const { context, state, calls, confirms } = routeCardHarness(async url =>
    url.includes("/preview/") ? preview : {});
  context.routeCardStartGeometryEdit();
  editor.insertVertex(state.geometryEditor.draft, 0, [35.905, 56.805]);
  confirms.push(false);
  await context.routeCardOsrmPreview();
  assert.equal(state.osrmPreview, null);
  confirms.push(true);
  await context.routeCardOsrmPreview();
  assert.equal(state.geometryEditor.active, false);
  assert.equal(state.osrmPreview.preview_token, "preview-1");

  confirms.push(false);
  await context.routeCardOsrmApply();
  assert.equal(calls.filter(call => call.kind === "api" && call.url.includes("/apply/")).length, 0);
  confirms.push(true);
  await context.routeCardOsrmApply();
  const apply = calls.find(call => call.kind === "api" && call.url.includes("/apply/"));
  assert.deepEqual(plain(apply.options.body), {
    preview_token: "preview-1",
    expected_geometry_version: 4,
  });
}

function readOnlyRoleCannotStartEditing() {
  const { context, state } = routeCardHarness(async () => ({}), "диспетчер");
  context.routeCardStartGeometryEdit();
  assert.equal(state.geometryEditor.active, false);
  assert.doesNotMatch(context.routeCardMap(state), /routeCardStartGeometryEdit/);
}

function fakeLeaflet() {
  const markers = [];
  const polylines = [];
  function evented(value) {
    const handlers = {};
    return Object.assign(value, {
      on(name, handler) { handlers[name] = handler; return this; },
      fire(name, event = {}) { return handlers[name] && handlers[name](event); },
    });
  }
  const map = evented({
    remove() {},
    setView() {},
    fitBounds() {},
    latLngToLayerPoint(value) {
      const lat = Array.isArray(value) ? value[0] : value.lat;
      const lng = Array.isArray(value) ? value[1] : value.lng;
      return { x: lng * 1000, y: lat * 1000 };
    },
  });
  const L = {
    map: () => map,
    tileLayer: () => evented({ addTo() { return this; } }),
    layerGroup: () => ({ addTo() { return this; } }),
    polyline: (points, options) => evented({
      points,
      options,
      addTo() { polylines.push(this); return this; },
      setLatLngs(next) { this.points = next; return this; },
    }),
    divIcon: options => ({ options }),
    marker: (latlng, options) => evented({
      options,
      _latlng: { lat: +latlng[0], lng: +latlng[1] },
      addTo() { markers.push(this); return this; },
      bindTooltip() { return this; },
      getLatLng() { return this._latlng; },
      setLatLng(next) {
        this._latlng = Array.isArray(next)
          ? { lat: +next[0], lng: +next[1] } : { lat: +next.lat, lng: +next.lng };
        return this;
      },
      dragging: { disable() {}, enable() {} },
    }),
  };
  return { L, map, markers, polylines };
}

async function leafletControlsEditDraftWithoutHttp() {
  const { context, state, calls } = routeCardHarness();
  context.routeCardStartGeometryEdit();
  const leaflet = fakeLeaflet();
  const canvas = { hidden: true };
  const fallback = { hidden: false };
  const warning = { hidden: true, textContent: "" };
  context.L = leaflet.L;
  context.document.querySelector = selector => ({
    ".route-map-canvas": canvas,
    ".route-map-fallback": fallback,
    ".route-map-warning": warning,
  })[selector] || null;

  context.routeCardBindMap(state);
  const stopMarker = leaflet.markers.find(marker =>
    marker.options.icon.options.className.includes("route-map-marker"));
  const controlMarker = leaflet.markers.find(marker =>
    marker.options.icon.options.className.includes("route-geometry-control"));
  assert.ok(stopMarker && controlMarker);
  assert.equal(stopMarker.options.draggable, false);
  assert.equal(controlMarker.options.draggable, true);
  assert.equal(controlMarker.options.keyboard, true);

  controlMarker.fire("click", { originalEvent: { stopPropagation() {} } });
  assert.equal(state.geometryEditor.draft.selectedIndex, controlMarker.options.geometryIndex);
  controlMarker._latlng = { lng: 35.911, lat: 56.811 };
  await controlMarker.fire("dragend", { target: controlMarker });
  assert.deepEqual(
    state.geometryEditor.draft.coordinates[controlMarker.options.geometryIndex],
    [35.911, 56.811],
  );

  const currentLine = leaflet.polylines.find(line => line.options.color === "#2563eb");
  assert.ok(currentLine);
  currentLine.fire("click", { latlng: { lng: 35.905, lat: 56.805 } });
  assert.equal(state.geometryEditor.draft.userIndexes.size, 1);
  assert.notEqual(state.geometryEditor.draft.selectedIndex, null);
  context.routeCardDeleteGeometryPoint();
  assert.equal(state.geometryEditor.draft.userIndexes.size, 0);
  assert.equal(calls.filter(call => call.kind === "api").length, 0);
}


function leafletViewportIsRestoredAfterGeometryRerender() {
  const { context, state } = routeCardHarness();
  context.routeCardStartGeometryEdit();
  const leaflet = fakeLeaflet();
  const canvas = { hidden: true };
  const fallback = { hidden: false };
  const warning = { hidden: true, textContent: "" };
  context.L = leaflet.L;
  context.document.querySelector = selector => ({
    ".route-map-canvas": canvas,
    ".route-map-fallback": fallback,
    ".route-map-warning": warning,
  })[selector] || null;
  context.routeCardBindMap(state);

  leaflet.map.getCenter = () => ({ lat: 56.805, lng: 35.905 });
  leaflet.map.getZoom = () => 16;
  context.routeCardRememberMapView(state, leaflet.map);
  assert.deepEqual(plain(state.mapViewport), {
    latitude: 56.805,
    longitude: 35.905,
    zoom: 16,
  });

  let restored = null;
  leaflet.map.setView = (center, zoom, options) => {
    restored = { center: plain(center), zoom, options: plain(options) };
  };
  leaflet.map.fitBounds = () => assert.fail("fitBounds resets the editor viewport");
  context.routeCardBindMap(state);
  assert.deepEqual(restored, { center: [56.805, 35.905], zoom: 16, options: { animate: false } });
}
const scenarios = {
  save_conflict: saveKeepsDraftAfterConflict,
  save_success: saveClosesOnlyAfterSuccessfulResponse,
  navigation_guard: cancelAndNavigationGuard,
  reset: resetRequiresConfirmationAndVersion,
  osrm_guard: osrmGuardsDirtyDraftAndSendsGeometryVersion,
  read_only: readOnlyRoleCannotStartEditing,
  leaflet_controls: leafletControlsEditDraftWithoutHttp,
  viewport: leafletViewportIsRestoredAfterGeometryRerender,
};

const scenario = process.argv[2];
assert.ok(scenarios[scenario], `unknown scenario: ${scenario}`);
Promise.resolve(scenarios[scenario]()).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
