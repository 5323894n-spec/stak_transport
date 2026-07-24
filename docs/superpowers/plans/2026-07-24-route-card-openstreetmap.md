# Route Card OpenStreetMap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an interactive OpenStreetMap underlay to the route-card trace view while preserving draggable stops, OSRM geometry, and the existing SVG fallback.

**Architecture:** Vendor Leaflet 1.9.4 locally and load only OpenStreetMap raster tiles from the internet. Keep map preparation and fallback rendering in `static/route-card.js`; create and destroy Leaflet instances after DOM rendering, use OSRM geometry when available, and fall back to the existing SVG coordinate diagram when Leaflet or tiles are unavailable.

**Tech Stack:** Vanilla JavaScript, Leaflet 1.9.4, OpenStreetMap raster tiles, CSS, pytest static-contract tests, Node syntax checks.

---

## File map

- Create `static/vendor/leaflet/leaflet.js`, `leaflet.css`, `LICENSE`, and `images/*`: pinned local Leaflet distribution.
- Modify `static/index.html`: load local Leaflet assets before route-card code and bump route-card/style cache keys.
- Modify `static/route-card.js`: fallback renderer, Leaflet lifecycle, route layers, tile failure handling, draggable markers.
- Modify `static/styles.css`: map height, markers, fallback/error state, responsive and print behavior.
- Create `tests/test_route_card_map_frontend.py`: static contracts for assets, OSM attribution, lifecycle, fallback, and cache keys.
- Modify `tests/test_route_card_frontend.py`: retain existing route-card serving coverage with the new asset version.

## Task 1: Vendor Leaflet and load it locally

**Files:**
- Create: `static/vendor/leaflet/leaflet.js`
- Create: `static/vendor/leaflet/leaflet.css`
- Create: `static/vendor/leaflet/LICENSE`
- Create: `static/vendor/leaflet/images/layers.png`
- Create: `static/vendor/leaflet/images/layers-2x.png`
- Create: `static/vendor/leaflet/images/marker-icon.png`
- Create: `static/vendor/leaflet/images/marker-icon-2x.png`
- Create: `static/vendor/leaflet/images/marker-shadow.png`
- Modify: `static/index.html`
- Create: `tests/test_route_card_map_frontend.py`

- [ ] **Step 1: Write the failing local-asset test**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_leaflet_is_vendored_and_loaded_before_route_card():
    index = (ROOT / "static/index.html").read_text(encoding="utf-8")
    leaflet_dir = ROOT / "static/vendor/leaflet"

    assert (leaflet_dir / "leaflet.js").stat().st_size > 100_000
    assert (leaflet_dir / "leaflet.css").stat().st_size > 10_000
    assert "Leaflet" in (leaflet_dir / "LICENSE").read_text(encoding="utf-8")
    assert '/static/vendor/leaflet/leaflet.css?v=1.9.4' in index
    assert '/static/vendor/leaflet/leaflet.js?v=1.9.4' in index
    assert index.index("leaflet.js") < index.index("route-card.js")
```

- [ ] **Step 2: Run the test and verify the expected failure**

Run: `python -m pytest tests/test_route_card_map_frontend.py::test_leaflet_is_vendored_and_loaded_before_route_card -q`

Expected: FAIL because `static/vendor/leaflet` does not exist.

- [ ] **Step 3: Download the pinned official Leaflet 1.9.4 distribution**

Use a temporary directory and the archive linked from the official Leaflet download page:

```powershell
$leafletTemp = Join-Path $env:TEMP "atp-leaflet-1.9.4"
New-Item -ItemType Directory -Force $leafletTemp | Out-Null
Invoke-WebRequest "https://leafletjs-cdn.s3.amazonaws.com/content/leaflet/v1.9.4/leaflet.zip" -OutFile "$leafletTemp\leaflet.zip"
Expand-Archive -Force "$leafletTemp\leaflet.zip" "$leafletTemp\dist"
New-Item -ItemType Directory -Force "static\vendor\leaflet" | Out-Null
Copy-Item "$leafletTemp\dist\leaflet.js" "static\vendor\leaflet\leaflet.js"
Copy-Item "$leafletTemp\dist\leaflet.css" "static\vendor\leaflet\leaflet.css"
Copy-Item -Recurse "$leafletTemp\dist\images" "static\vendor\leaflet\images"
Invoke-WebRequest "https://raw.githubusercontent.com/Leaflet/Leaflet/v1.9.4/LICENSE" -OutFile "static\vendor\leaflet\LICENSE"
```

Verify the two published SHA-256 SRI values against the downloaded JavaScript and CSS:

```powershell
$jsHash = [Convert]::ToBase64String((Get-FileHash static\vendor\leaflet\leaflet.js -Algorithm SHA256).Hash -split '(?<=\G.{2})' | Where-Object { $_ } | ForEach-Object {[Convert]::ToByte($_,16)})
$cssHash = [Convert]::ToBase64String((Get-FileHash static\vendor\leaflet\leaflet.css -Algorithm SHA256).Hash -split '(?<=\G.{2})' | Where-Object { $_ } | ForEach-Object {[Convert]::ToByte($_,16)})
if ($jsHash -ne '20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=') { throw "Leaflet JS checksum mismatch" }
if ($cssHash -ne 'p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=') { throw "Leaflet CSS checksum mismatch" }
```

- [ ] **Step 4: Load the local assets in `static/index.html`**

Insert the stylesheet after the application stylesheet and the script before `route-card.js`:

```html
<link rel="stylesheet" href="/static/vendor/leaflet/leaflet.css?v=1.9.4">
...
<script src="/static/vendor/leaflet/leaflet.js?v=1.9.4"></script>
<script src="/static/route-card.js?v=3.5"></script>
```

- [ ] **Step 5: Run the asset test**

Run: `python -m pytest tests/test_route_card_map_frontend.py::test_leaflet_is_vendored_and_loaded_before_route_card -q`

Expected: PASS.

- [ ] **Step 6: Commit the vendored dependency**

```powershell
git add static/vendor/leaflet static/index.html tests/test_route_card_map_frontend.py
git commit -m "build(routes): vendor Leaflet map assets"
```

## Task 2: Preserve the SVG renderer as an explicit fallback

**Files:**
- Modify: `static/route-card.js`
- Modify: `tests/test_route_card_map_frontend.py`

- [ ] **Step 1: Add a failing fallback contract test**

```python
def test_route_map_keeps_svg_fallback_and_leaflet_container():
    source = (ROOT / "static/route-card.js").read_text(encoding="utf-8")

    assert "function routeCardFallbackMap" in source
    assert 'class="route-map-canvas"' in source
    assert 'class="route-map-fallback"' in source
    assert 'aria-label="Схема трассы без картографической подложки"' in source
    assert "Подложка OpenStreetMap недоступна" in source
```

- [ ] **Step 2: Run the test and verify the expected failure**

Run: `python -m pytest tests/test_route_card_map_frontend.py::test_route_map_keeps_svg_fallback_and_leaflet_container -q`

Expected: FAIL because the existing SVG is embedded directly in `routeCardMap`.

- [ ] **Step 3: Extract the current SVG into a pure fallback function**

Add this function immediately after `routeMapPoints` and move the existing SVG construction into it:

```javascript
function routeCardFallbackMap(state, plotted = routeMapPoints(state)) {
  const line = plotted.points.map(point => `${point.x},${point.y}`).join(" ");
  const nodes = plotted.points.map((point, index) =>
    `<g class="route-map-node" data-route-stop-id="${point.row.stop_id}">` +
      `<circle cx="${point.x}" cy="${point.y}" r="8"></circle>` +
      `<text x="${point.x + 11}" y="${point.y - 10}">${index + 1}. ${esc(point.row.stop.name)}</text>` +
    `</g>`).join("");
  if (!plotted.points.length) return '<div class="route-empty">Для схемы добавьте координаты остановок</div>';
  return `<svg viewBox="0 0 800 360" role="img" aria-label="Схема трассы без картографической подложки"><polyline points="${line}"></polyline>${nodes}</svg>`;
}
```

Render both containers from `routeCardMap`:

```javascript
<div class="route-map">
  <div class="route-map-canvas" aria-label="Интерактивная карта трассы"></div>
  <div class="route-map-fallback" hidden>${routeCardFallbackMap(state, plotted)}</div>
</div>
```

Keep the existing missing-coordinate warning, direction switch, OSRM button, legend, and `routeOsrmDiff(state)` output unchanged.

- [ ] **Step 4: Add a small fallback switch helper**

```javascript
function routeCardShowMapFallback(message = "Подложка OpenStreetMap недоступна") {
  const canvas = document.querySelector(".route-map-canvas");
  const fallback = document.querySelector(".route-map-fallback");
  const warning = document.querySelector(".route-map-warning");
  if (canvas) canvas.hidden = true;
  if (fallback) fallback.hidden = false;
  if (warning) { warning.hidden = false; warning.textContent = message; }
}
```

Add `<div class="vio w route-map-warning" hidden></div>` before `.route-map`.

- [ ] **Step 5: Run the fallback test and existing route-card tests**

Run: `python -m pytest tests/test_route_card_map_frontend.py tests/test_route_card_frontend.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the fallback extraction**

```powershell
git add static/route-card.js tests/test_route_card_map_frontend.py
git commit -m "refactor(routes): preserve route map fallback"
```

## Task 3: Render OpenStreetMap route layers and manage lifecycle

**Files:**
- Modify: `static/route-card.js`
- Modify: `tests/test_route_card_map_frontend.py`

- [ ] **Step 1: Add failing map lifecycle and layer tests**

```python
def test_route_map_uses_osm_tiles_attribution_and_cleans_up_instances():
    source = (ROOT / "static/route-card.js").read_text(encoding="utf-8")

    for token in (
        "function routeCardDestroyMap",
        "function routeCardGeometryPoints",
        "window.L.map",
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "&copy; OpenStreetMap contributors",
        "tileerror",
        "routeMapInstance.remove()",
        "fitBounds",
    ):
        assert token in source


def test_route_map_has_draggable_stop_markers_and_persists_dragend():
    source = (ROOT / "static/route-card.js").read_text(encoding="utf-8")

    assert "draggable: true" in source
    assert '.on("dragend"' in source
    assert "marker.setLatLng(original)" in source
    assert "PUT" in source
    assert "row.stop.latitude = latitude" in source
    assert "row.stop.longitude = longitude" in source
```

- [ ] **Step 2: Run the lifecycle tests and verify the expected failure**

Run: `python -m pytest tests/test_route_card_map_frontend.py -q`

Expected: FAIL because no Leaflet lifecycle or OSM layer exists.

- [ ] **Step 3: Add cleanup and geometry normalization**

Add these helpers above `routeCardBindMap`:

```javascript
function routeCardDestroyMap(state = window._routeCard) {
  if (state && state.routeMapTimer) clearTimeout(state.routeMapTimer);
  if (state && state.routeMapInstance) state.routeMapInstance.remove();
  if (state) { state.routeMapTimer = null; state.routeMapInstance = null; }
}

function routeCardGeometryPoints(state, plotted) {
  const coordinates = state.geometry && state.geometry.coordinates;
  if (Array.isArray(coordinates) && coordinates.length > 1) {
    return coordinates
      .filter(point => Array.isArray(point) && point.length >= 2 && Number.isFinite(+point[0]) && Number.isFinite(+point[1]))
      .map(point => [+point[1], +point[0]]);
  }
  return plotted.rows.map(row => [+row.stop.latitude, +row.stop.longitude]);
}
```

Call `routeCardDestroyMap(state)` at the start of `renderRouteCard`, before replacing `content.innerHTML`.

- [ ] **Step 4: Replace `routeCardBindMap` with Leaflet initialization**

Implement this complete flow:

```javascript
function routeCardBindMap(state) {
  const canvas = document.querySelector(".route-map-canvas");
  const plotted = routeMapPoints(state);
  if (!canvas || !plotted.rows.length) { routeCardShowMapFallback("Добавьте координаты остановок"); return; }
  if (!window.L) { routeCardShowMapFallback(); routeCardBindFallbackDrag(state); return; }

  try {
    const map = window.L.map(canvas, { zoomControl: true, preferCanvas: true });
    state.routeMapInstance = map;
    let tileLoaded = false;
    const tiles = window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
    });
    tiles.on("tileload", () => { tileLoaded = true; if (state.routeMapTimer) clearTimeout(state.routeMapTimer); });
    tiles.on("tileerror", () => { state.routeMapTileErrors = (state.routeMapTileErrors || 0) + 1; });
    tiles.addTo(map);

    const routePoints = routeCardGeometryPoints(state, plotted);
    window.L.polyline(routePoints, { color: "#ffffff", weight: 10, opacity: .9, interactive: false }).addTo(map);
    window.L.polyline(routePoints, { color: "#2563eb", weight: 6, opacity: .95, interactive: false }).addTo(map);

    plotted.rows.forEach((row, index) => {
      const original = [+row.stop.latitude, +row.stop.longitude];
      const endpoint = index === 0 ? " start" : index === plotted.rows.length - 1 ? " end" : "";
      const icon = window.L.divIcon({ className: `route-leaflet-marker${endpoint}`, html: `<span>${index + 1}</span>`, iconSize: [28, 28], iconAnchor: [14, 14] });
      const marker = window.L.marker(original, { draggable: true, icon }).addTo(map).bindTooltip(esc(row.stop.name));
      marker.on("dragend", async () => {
        const value = marker.getLatLng(), latitude = value.lat, longitude = value.lng;
        try {
          await api(`/api/stops/${row.stop_id}`, { method: "PUT", body: { latitude, longitude } });
          row.stop.latitude = latitude; row.stop.longitude = longitude;
          toast("Координаты остановки сохранены");
        } catch (error) { marker.setLatLng(original); toast(error.message, true); }
      });
    });

    const bounds = window.L.latLngBounds(plotted.rows.map(row => [+row.stop.latitude, +row.stop.longitude]));
    if (plotted.rows.length === 1) map.setView(bounds.getCenter(), 15);
    else map.fitBounds(bounds, { padding: [36, 36], maxZoom: 17 });
    state.routeMapTimer = setTimeout(() => {
      if (!tileLoaded && state.routeMapInstance === map) { routeCardDestroyMap(state); routeCardShowMapFallback(); routeCardBindFallbackDrag(state); }
    }, 8000);
  } catch (error) { routeCardDestroyMap(state); routeCardShowMapFallback(); routeCardBindFallbackDrag(state); }
}
```

- [ ] **Step 5: Rename the old SVG drag binder**

Move the current SVG pointer implementation unchanged into:

```javascript
function routeCardBindFallbackDrag(state) {
  const svg = document.querySelector(".route-map-fallback svg");
  // existing pointerdown, pointermove, and pointerup implementation
}
```

The Leaflet failure paths call it only after making `.route-map-fallback` visible.

- [ ] **Step 6: Run map tests and syntax check**

Run:

```powershell
python -m pytest tests/test_route_card_map_frontend.py tests/test_route_card_frontend.py tests/test_route_osrm.py -q
node --check static/route-card.js
```

Expected: all tests PASS and Node exits 0.

- [ ] **Step 7: Commit interactive map behavior**

```powershell
git add static/route-card.js tests/test_route_card_map_frontend.py
git commit -m "feat(routes): show OpenStreetMap trace underlay"
```

## Task 4: Style interactive, fallback, responsive, and print states

**Files:**
- Modify: `static/styles.css`
- Modify: `static/index.html`
- Modify: `tests/test_route_card_map_frontend.py`
- Modify: `tests/test_route_card_frontend.py`

- [ ] **Step 1: Add failing style and cache tests**

```python
def test_route_map_styles_cover_interactive_fallback_mobile_and_print():
    styles = (ROOT / "static/styles.css").read_text(encoding="utf-8")
    index = (ROOT / "static/index.html").read_text(encoding="utf-8")

    for token in (
        ".route-map-canvas",
        ".route-map-fallback",
        ".route-leaflet-marker",
        "height: 460px",
        "height: 320px",
        "@media print",
    ):
        assert token in styles
    assert 'styles.css?v=3.2&amp;route=3.7' in index
    assert 'route-card.js?v=3.5' in index
```

- [ ] **Step 2: Run the style test and verify the expected failure**

Run: `python -m pytest tests/test_route_card_map_frontend.py::test_route_map_styles_cover_interactive_fallback_mobile_and_print -q`

Expected: FAIL because interactive-map styles and new cache keys are absent.

- [ ] **Step 3: Add map styles**

Replace the existing `.route-map` block with:

```css
.route-map { position: relative; min-height: 460px; border: 1px solid var(--line); border-radius: 11px; background: #f8fafc; overflow: hidden; }
.route-map-canvas, .route-map-fallback { width: 100%; height: 460px; }
.route-map-canvas[hidden], .route-map-fallback[hidden] { display: none; }
.route-map-fallback { background: linear-gradient(#eef3f9 1px, transparent 1px), linear-gradient(90deg, #eef3f9 1px, transparent 1px), #fff; background-size: 28px 28px; }
.route-map-fallback svg { display: block; width: 100%; height: 100%; }
.route-leaflet-marker { background: transparent; border: 0; }
.route-leaflet-marker span { display: grid; place-items: center; width: 28px; height: 28px; border: 3px solid #fff; border-radius: 50%; background: var(--accent); color: #fff; font-size: 12px; font-weight: 800; box-shadow: 0 2px 8px #0f172a55; }
.route-leaflet-marker.start span { background: #15803d; }
.route-leaflet-marker.end span { background: #b91c1c; }
.route-map-warning { margin-bottom: 10px; }
@media (max-width: 767px) {
  .route-map { min-height: 320px; }
  .route-map-canvas, .route-map-fallback { height: 320px; }
}
@media print {
  .route-map-canvas { display: none !important; }
  .route-map-fallback { display: block !important; height: 360px; }
}
```

Retain the current `.route-map polyline`, `.route-map-node`, and legend rules for fallback mode.

- [ ] **Step 4: Bump shared cache keys**

In `static/index.html`, update both shared resource URLs from `route=3.6` to `route=3.7`; keep the Leaflet version at `1.9.4` and route-card version at `3.5`.

Update `tests/test_route_card_frontend.py` to assert `/static/route-card.js?v=3.5` and update route frontend tests that explicitly expect `route=3.6` to `route=3.7`.

- [ ] **Step 5: Run frontend regressions**

Run:

```powershell
python -m pytest tests/test_route_card_map_frontend.py tests/test_route_card_frontend.py tests/test_route_period_frontend.py tests/test_route_shift_frontend.py tests/test_route_timetable_frontend.py -q
node --check static/route-card.js
```

Expected: all tests PASS and Node exits 0.

- [ ] **Step 6: Commit styles and cache keys**

```powershell
git add static/styles.css static/index.html tests/test_route_card_map_frontend.py tests/test_route_card_frontend.py tests/test_route_period_frontend.py tests/test_route_shift_frontend.py tests/test_route_timetable_frontend.py
git commit -m "style(routes): finish responsive route map"
```

## Task 5: Verify OpenStreetMap end to end

**Files:**
- Modify: this plan only with evidence-backed completion marks.

- [ ] **Step 1: Run the complete test suite**

Run: `python -m pytest tests -q`

Expected: all tests PASS.

- [ ] **Step 2: Run static and repository checks**

```powershell
node --check static/app.js
node --check static/route-card.js
python -m compileall -q app
git diff --check
git status --short
```

Expected: every command exits 0 and only intentional plan completion marks remain.

- [ ] **Step 3: Verify online browser behavior**

Open a route with at least three geocoded stops and verify:

1. OpenStreetMap tiles appear and the attribution is visible.
2. The route line and numbered stops appear above the tiles.
3. Zoom and pan do not change stop coordinates.
4. Drag one stop, reload the card, and confirm the coordinate persists.
5. Generate an OSRM preview and confirm its geometry replaces the stop-to-stop line without applying data.

Expected: all five checks succeed without console errors.

- [ ] **Step 4: Verify offline fallback**

Block requests to `tile.openstreetmap.org` in browser developer tools and reopen the map tab. Wait at least 8 seconds.

Expected: the warning «Подложка OpenStreetMap недоступна» appears, the SVG trace remains visible and draggable, and OSRM controls remain available.

- [ ] **Step 5: Review and finish the branch**

Use `superpowers:verification-before-completion`, `superpowers:requesting-code-review`, and `superpowers:finishing-a-development-branch`. Do not merge until automated tests and both online/offline browser scenarios pass.
