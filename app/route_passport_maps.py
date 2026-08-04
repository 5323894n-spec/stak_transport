# -*- coding: utf-8 -*-
"""Deterministic route schemes and OpenStreetMap passport maps.

The module renders two kinds of images used by the Word route passport:

* :func:`render_route_scheme` — a self-contained schematic diagram with no
  network access, always available and deterministic.
* :func:`render_direction_map` — an OpenStreetMap raster map that degrades to
  an offline scheme when tiles cannot be fetched.

All network access goes through an injectable ``tile_loader`` so tests stay
fully offline and deterministic.
"""

from dataclasses import dataclass
from io import BytesIO
import math
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont


OSM_ATTRIBUTION = "© OpenStreetMap contributors"
OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_SIZE = 256
MIN_ZOOM = 4
MAX_ZOOM = 17
MAX_TILES = 24
TILE_TIMEOUT = 3.0
USER_AGENT = "ATP-Servis-V2 route-passport/1.0"

_ROUTE_COLOR = (23, 105, 210)
_STOP_FILL = (255, 255, 255)
_STOP_OUTLINE = (18, 74, 145)
_INK = (16, 36, 62)
_MUTED = (95, 107, 118)
_SCHEME_BG = (255, 255, 255)
_MAP_BG = (238, 242, 245)


@dataclass(frozen=True)
class RenderedMap:
    png: bytes
    basemap_available: bool
    attribution: str = OSM_ATTRIBUTION


def direction_coordinates(section, geometry):
    """Return the polyline for a direction, preferring saved geometry."""
    if geometry is not None and len(geometry.coordinates) >= 2:
        return geometry.coordinates
    return _stop_coordinates(section)


def _stop_coordinates(section):
    return tuple(
        (float(stop["longitude"]), float(stop["latitude"]))
        for stop in section.stops
        if stop.get("longitude") is not None
        and stop.get("latitude") is not None
    )


def _font():
    try:
        return ImageFont.load_default()
    except OSError:  # pragma: no cover - Pillow always ships a default font
        return None


def _fit(points, size, padding=48):
    """Project geographic points into canvas pixels with a uniform scale."""
    width, height = size
    if not points:
        return ()
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x_span = max(max(xs) - min(xs), 1e-9)
    y_span = max(max(ys) - min(ys), 1e-9)
    scale = min(
        (width - 2 * padding) / x_span, (height - 2 * padding) / y_span
    )
    draw_width = (max(xs) - min(xs)) * scale
    draw_height = (max(ys) - min(ys)) * scale
    offset_x = (width - draw_width) / 2
    offset_y = (height - draw_height) / 2
    return tuple(
        (
            offset_x + (x - min(xs)) * scale,
            height - offset_y - (y - min(ys)) * scale,
        )
        for x, y in points
    )


# --- Web Mercator tile helpers ------------------------------------------------

def _world_pixel(longitude, latitude, zoom):
    scale = TILE_SIZE * (2 ** zoom)
    x = (longitude + 180.0) / 360.0 * scale
    sin_lat = math.sin(math.radians(latitude))
    sin_lat = min(max(sin_lat, -0.9999), 0.9999)
    y = (
        0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)
    ) * scale
    return x, y


def _span_at_zoom(points, zoom):
    pixels = [_world_pixel(lon, lat, zoom) for lon, lat in points]
    xs = [p[0] for p in pixels]
    ys = [p[1] for p in pixels]
    return max(xs) - min(xs), max(ys) - min(ys)


def _choose_zoom(points, size, padding=32):
    width, height = size
    usable_w = max(width - 2 * padding, 1)
    usable_h = max(height - 2 * padding, 1)
    chosen = MIN_ZOOM
    for zoom in range(MIN_ZOOM, MAX_ZOOM + 1):
        span_x, span_y = _span_at_zoom(points, zoom)
        if span_x > usable_w or span_y > usable_h:
            break
        tiles_x = math.ceil(width / TILE_SIZE) + 1
        tiles_y = math.ceil(height / TILE_SIZE) + 1
        if tiles_x * tiles_y > MAX_TILES:
            break
        chosen = zoom
    return chosen


def _paste_tiles(points, size, tile_loader):
    width, height = size
    zoom = _choose_zoom(points, size)
    pixels = [_world_pixel(lon, lat, zoom) for lon, lat in points]
    center_x = (min(p[0] for p in pixels) + max(p[0] for p in pixels)) / 2
    center_y = (min(p[1] for p in pixels) + max(p[1] for p in pixels)) / 2
    world_left = center_x - width / 2
    world_top = center_y - height / 2

    first_tile_x = int(math.floor(world_left / TILE_SIZE))
    last_tile_x = int(math.floor((world_left + width) / TILE_SIZE))
    first_tile_y = int(math.floor(world_top / TILE_SIZE))
    last_tile_y = int(math.floor((world_top + height) / TILE_SIZE))
    tile_count = (last_tile_x - first_tile_x + 1) * (
        last_tile_y - first_tile_y + 1
    )
    if tile_count > MAX_TILES:
        raise ValueError("Слишком много тайлов для карты паспорта")

    max_index = 2 ** zoom
    canvas = Image.new("RGB", size, _MAP_BG)
    pasted = False
    for tile_x in range(first_tile_x, last_tile_x + 1):
        for tile_y in range(first_tile_y, last_tile_y + 1):
            if not (0 <= tile_y < max_index):
                continue
            wrapped_x = tile_x % max_index
            url = OSM_TILE_URL.format(z=zoom, x=wrapped_x, y=tile_y)
            data = tile_loader(url, TILE_TIMEOUT)
            tile = Image.open(BytesIO(data)).convert("RGB")
            canvas.paste(
                tile,
                (
                    int(round(tile_x * TILE_SIZE - world_left)),
                    int(round(tile_y * TILE_SIZE - world_top)),
                ),
            )
            pasted = True
    if not pasted:
        raise ValueError("Карта паспорта не получила ни одного тайла")

    def project(point):
        px, py = _world_pixel(point[0], point[1], zoom)
        return px - world_left, py - world_top

    return canvas, project


def _default_tile_loader(url, timeout):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


# --- Public renderers ---------------------------------------------------------

def _draw_route(draw, line):
    if len(line) >= 2:
        draw.line(line, fill=_ROUTE_COLOR, width=8, joint="curve")


def _draw_stops(draw, stop_points, font):
    for number, (x, y) in enumerate(stop_points, 1):
        draw.ellipse(
            (x - 8, y - 8, x + 8, y + 8),
            fill=_STOP_FILL,
            outline=_STOP_OUTLINE,
            width=4,
        )
        draw.text((x + 12, y - 8), str(number), fill=_INK, font=font)


def render_direction_map(
    section, geometry, *, size=(1200, 800), tile_loader=_default_tile_loader
):
    """Render an OSM direction map, degrading to an offline scheme."""
    route = direction_coordinates(section, geometry)
    stops = _stop_coordinates(section)
    font = _font()
    basemap = False
    project = None
    canvas = None
    if len(route) >= 2:
        try:
            canvas, project = _paste_tiles(route, size, tile_loader)
            basemap = True
        except (OSError, ValueError, TimeoutError):
            canvas = None
            project = None

    if basemap and project is not None:
        line = tuple(project(point) for point in route)
        stop_points = tuple(project(point) for point in stops)
    else:
        canvas = Image.new("RGB", size, _MAP_BG)
        line = _fit(route, size)
        stop_points = _fit(stops, size)

    draw = ImageDraw.Draw(canvas)
    _draw_route(draw, line)
    _draw_stops(draw, stop_points, font)
    if not basemap:
        draw.text(
            (24, size[1] - 34),
            "Картографическая подложка недоступна",
            fill=_MUTED,
            font=font,
        )
    draw.text(
        (size[0] - 240, size[1] - 22),
        OSM_ATTRIBUTION,
        fill=_MUTED,
        font=font,
    )
    output = BytesIO()
    canvas.save(output, "PNG", optimize=True)
    return RenderedMap(output.getvalue(), basemap)


def render_route_scheme(section, geometry, *, size=(1200, 800)):
    """Render a deterministic, network-free schematic of one direction."""
    route = direction_coordinates(section, geometry)
    stops = _stop_coordinates(section)
    font = _font()
    canvas = Image.new("RGB", size, _SCHEME_BG)
    draw = ImageDraw.Draw(canvas)
    line = _fit(route, size)
    _draw_route(draw, line)
    if len(line) >= 2:
        _draw_arrow(draw, line[-2], line[-1])
    _draw_stops(draw, _fit(stops, size), font)
    output = BytesIO()
    canvas.save(output, "PNG", optimize=True)
    return output.getvalue()


def _draw_arrow(draw, start, end, length=18):
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    for offset in (math.radians(160), math.radians(-160)):
        draw.line(
            [
                end,
                (
                    end[0] + length * math.cos(angle + offset),
                    end[1] + length * math.sin(angle + offset),
                ),
            ],
            fill=_ROUTE_COLOR,
            width=6,
        )
