# -*- coding: utf-8 -*-
"""Render compact route schemes and OpenStreetMap-backed direction maps."""
from dataclasses import dataclass
from collections import OrderedDict
import time
from urllib.error import URLError
from http.client import HTTPException
from io import BytesIO
import math
from numbers import Number
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError


OSM_ATTRIBUTION = "© OpenStreetMap contributors"
_TILE_SIZE = 256
_MIN_ZOOM = 4
_MAX_ZOOM = 17
_MAX_TILES = 24
_TILE_TIMEOUT = 3
_USER_AGENT = "ATP-Servis-V2 route-passport/1.0"


@dataclass(frozen=True)
class RenderedMap:
    png: bytes
    basemap_available: bool
    attribution: str = OSM_ATTRIBUTION


def _coordinate(point):
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        return None
    longitude, latitude = point
    if (
        isinstance(longitude, bool)
        or isinstance(latitude, bool)
        or not isinstance(longitude, Number)
        or not isinstance(latitude, Number)
    ):
        return None
    longitude, latitude = float(longitude), float(latitude)
    if (
        not math.isfinite(longitude)
        or not math.isfinite(latitude)
        or not -180 <= longitude <= 180
        or not -90 <= latitude <= 90
    ):
        return None
    return longitude, latitude


def _stop_coordinates(section):
    coordinates = []
    for stop in getattr(section, "stops", ()) or ():
        if not isinstance(stop, dict):
            continue
        coordinate = _coordinate((stop.get("longitude"), stop.get("latitude")))
        if coordinate is not None:
            coordinates.append(coordinate)
    return tuple(coordinates)


def _geometry_coordinates(geometry):
    if geometry is None:
        return ()
    source = geometry.get("coordinates") if isinstance(geometry, dict) else getattr(
        geometry, "coordinates", None
    )
    if not isinstance(source, (list, tuple)):
        return ()
    return tuple(point for point in (_coordinate(value) for value in source) if point)


def direction_coordinates(section, geometry):
    """Return stored direction geometry, or the ordered complete stop coordinates."""
    saved = _geometry_coordinates(geometry)
    return saved if len(saved) >= 2 else _stop_coordinates(section)


def _font(size):
    for candidate in (
        "arial.ttf",
        "Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/ARIAL.TTF",
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _world_point(coordinate, zoom):
    longitude, latitude = coordinate
    latitude = max(-85.05112878, min(85.05112878, latitude))
    scale = _TILE_SIZE * (2 ** zoom)
    x = (longitude + 180.0) / 360.0 * scale
    latitude_radians = math.radians(latitude)
    y = (1.0 - math.asinh(math.tan(latitude_radians)) / math.pi) / 2.0 * scale
    return x, y


def _png(image):
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _title(section):
    direction = str(getattr(section, "direction", "") or "")
    return {
        "forward": "Направление: прямое",
        "backward": "Направление: обратное",
        "depot_out": "Направление: выезд из депо",
        "depot_in": "Направление: возврат в депо",
    }.get(direction, "Схема направления")




_ASCII_ATTRIBUTION = "(c) OpenStreetMap contributors"
_TILE_CACHE_MAX_SIZE = 256
_TILE_CACHE_TTL = 7 * 24 * 60 * 60
_TILE_CACHE = OrderedDict()


class BasemapUnavailable(Exception):
    """An expected network, tile-data, or tile-limit failure."""


@dataclass(frozen=True)
class _MosaicTransform:
    scale_x: float
    scale_y: float
    offset_x: float
    offset_y: float
    scaled_size: tuple[int, int]


def _unwrap_coordinates(coordinates):
    """Unwrap a coordinate sequence so dateline crossings take the short path."""
    unwrapped = []
    previous_longitude = None
    for longitude, latitude in coordinates:
        current = longitude
        if previous_longitude is not None:
            while current - previous_longitude > 180:
                current -= 360
            while current - previous_longitude < -180:
                current += 360
        unwrapped.append((current, latitude))
        previous_longitude = current
    return tuple(unwrapped)


def _map_points(section, geometry):
    route = direction_coordinates(section, geometry)
    stops = _stop_coordinates(section)
    if route:
        route = _unwrap_coordinates(route)
        reference = sum(point[0] for point in route) / len(route)
        stops = tuple(
            (longitude + 360 * round((reference - longitude) / 360), latitude)
            for longitude, latitude in stops
        )
    else:
        stops = _unwrap_coordinates(stops)
    return route, stops


def _screen_projector(points, size):
    width, height = size
    if not points:
        return lambda point: (width / 2, height / 2)
    projected = [_world_point(point, _MIN_ZOOM) for point in points]
    min_x = min(point[0] for point in projected)
    max_x = max(point[0] for point in projected)
    min_y = min(point[1] for point in projected)
    max_y = max(point[1] for point in projected)
    center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2
    half_x = max((max_x - min_x) / 2, 0.5)
    half_y = max((max_y - min_y) / 2, 0.5)
    min_x, max_x = center_x - half_x, center_x + half_x
    min_y, max_y = center_y - half_y, center_y + half_y
    padding = 55
    scale = min(
        (width - 2 * padding) / (max_x - min_x),
        (height - 2 * padding) / (max_y - min_y),
    )
    offset_x = width / 2 - center_x * scale
    offset_y = height / 2 - center_y * scale

    def project(coordinate):
        x, y = _world_point(coordinate, _MIN_ZOOM)
        return x * scale + offset_x, y * scale + offset_y

    return project


def _draw_route(draw, route, stops, project, size, *, title, unavailable=False):
    width, height = size
    title_font = _font(22)
    label_font = _font(16)
    try:
        draw.text((18, 14), title, fill="#18263d", font=title_font)
        if unavailable:
            draw.text(
                (18, 43), "Картографическая подложка недоступна",
                fill="#9b3412", font=label_font,
            )
    except UnicodeEncodeError:
        draw.text((18, 14), "Route direction", fill="#18263d", font=title_font)
        if unavailable:
            draw.text((18, 43), "Basemap unavailable", fill="#9b3412", font=label_font)

    if len(route) >= 2:
        line = [project(point) for point in route]
        draw.line(line, fill="#1769aa", width=6, joint="curve")
        previous, current = line[-2], line[-1]
        angle = math.atan2(current[1] - previous[1], current[0] - previous[0])
        arrow = [
            (current[0] - 15 * math.cos(angle - delta),
             current[1] - 15 * math.sin(angle - delta))
            for delta in (math.pi / 6, -math.pi / 6)
        ]
        draw.polygon([current, *arrow], fill="#1769aa")
    elif route:
        x, y = project(route[0])
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill="#1769aa")

    for number, coordinate in enumerate(stops, start=1):
        x, y = project(coordinate)
        radius = 11
        draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                     fill="white", outline="#123c69", width=3)
        text = str(number)
        box = draw.textbbox((0, 0), text, font=label_font)
        draw.text((x - (box[2] - box[0]) / 2, y - (box[3] - box[1]) / 2 - 1),
                  text, fill="#123c69", font=label_font)

    attribution_font = _font(13)
    try:
        attribution = OSM_ATTRIBUTION
        box = draw.textbbox((0, 0), attribution, font=attribution_font)
    except UnicodeEncodeError:
        attribution = _ASCII_ATTRIBUTION
        box = draw.textbbox((0, 0), attribution, font=attribution_font)
    position = (width - (box[2] - box[0]) - 12, height - 24)
    draw.rectangle((position[0] - 4, position[1] - 2, width - 8, height - 6),
                   fill=(255, 255, 255, 210))
    draw.text(position, attribution, fill="#263238", font=attribution_font)


def _fallback(section, geometry, size, *, unavailable):
    route, stops = _map_points(section, geometry)
    projector = _screen_projector(route + stops, size)
    image = Image.new("RGBA", size, "#f7f8fa")
    _draw_route(ImageDraw.Draw(image), route, stops, projector, size,
                title=_title(section), unavailable=unavailable)
    return RenderedMap(_png(image), basemap_available=False)


def render_route_scheme(section, geometry, *, size=(1200, 800)):
    """Render a deterministic offline diagram for a route direction."""
    return _fallback(section, geometry, size, unavailable=False)


def _tile_bounds(points):
    if not points:
        raise BasemapUnavailable("There are no coordinates to map")
    for zoom in range(_MAX_ZOOM, _MIN_ZOOM - 1, -1):
        world = [_world_point(point, zoom) for point in points]
        limit = 2 ** zoom
        min_x = math.floor(min(point[0] for point in world) / _TILE_SIZE) - 1
        max_x = math.floor(max(point[0] for point in world) / _TILE_SIZE) + 1
        min_y = max(0, math.floor(min(point[1] for point in world) / _TILE_SIZE) - 1)
        max_y = min(limit - 1, math.floor(max(point[1] for point in world) / _TILE_SIZE) + 1)
        if (max_x - min_x + 1) * (max_y - min_y + 1) <= _MAX_TILES:
            return zoom, min_x, max_x, min_y, max_y
    raise BasemapUnavailable("Map extent exceeds the tile limit")


def _decode_tile(content):
    try:
        with Image.open(BytesIO(content)) as tile:
            if tile.size != (_TILE_SIZE, _TILE_SIZE):
                raise BasemapUnavailable("Tile size is not 256 by 256")
            converted = tile.convert("RGBA")
            converted.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise BasemapUnavailable("Tile data is not a readable PNG") from exc
    return converted


def _tile_image(url, loader):
    key = (id(loader), url)
    cached = _TILE_CACHE.pop(key, None)
    now = time.monotonic()
    if cached is not None:
        cached_loader, expires_at, tile = cached
        if cached_loader is loader and expires_at > now:
            _TILE_CACHE[key] = cached
            return tile.copy()
    content = loader(url, _TILE_TIMEOUT)
    if not isinstance(content, bytes):
        raise ValueError("Tile loader must return bytes")
    tile = _decode_tile(content)
    _TILE_CACHE[key] = (loader, now + _TILE_CACHE_TTL, tile.copy())
    while len(_TILE_CACHE) > _TILE_CACHE_MAX_SIZE:
        _TILE_CACHE.popitem(last=False)
    return tile


def _mosaic_transform(source_size, target_size):
    source_width, source_height = source_size
    target_width, target_height = target_size
    scale = min(target_width / source_width, target_height / source_height)
    scaled_size = (
        max(1, round(source_width * scale)),
        max(1, round(source_height * scale)),
    )
    return _MosaicTransform(
        scale_x=scale,
        scale_y=scale,
        offset_x=(target_width - scaled_size[0]) // 2,
        offset_y=(target_height - scaled_size[1]) // 2,
        scaled_size=scaled_size,
    )


def _basemap(section, geometry, size, loader):
    route, stops = _map_points(section, geometry)
    bounds = _tile_bounds(route + stops)
    zoom, min_x, max_x, min_y, max_y = bounds
    columns, rows = max_x - min_x + 1, max_y - min_y + 1
    mosaic = Image.new("RGBA", (columns * _TILE_SIZE, rows * _TILE_SIZE))
    limit = 2 ** zoom
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            url = f"https://tile.openstreetmap.org/{zoom}/{x % limit}/{y}.png"
            mosaic.alpha_composite(
                _tile_image(url, loader),
                ((x - min_x) * _TILE_SIZE, (y - min_y) * _TILE_SIZE),
            )

    transform = _mosaic_transform(mosaic.size, size)
    screen = Image.new("RGBA", size, "#f7f8fa")
    scaled = mosaic.resize(transform.scaled_size, Image.Resampling.LANCZOS)
    screen.alpha_composite(scaled, (round(transform.offset_x), round(transform.offset_y)))
    world_origin = min_x * _TILE_SIZE, min_y * _TILE_SIZE

    def project(coordinate):
        x, y = _world_point(coordinate, zoom)
        return (
            transform.offset_x + (x - world_origin[0]) * transform.scale_x,
            transform.offset_y + (y - world_origin[1]) * transform.scale_y,
        )

    _draw_route(ImageDraw.Draw(screen), route, stops, project, size,
                title=_title(section))
    return RenderedMap(_png(screen), basemap_available=True)


def render_direction_map(section, geometry, *, size=(1200, 800), tile_loader=None):
    """Render an OSM direction map, with a labelled coordinate fallback."""
    route, stops = _map_points(section, geometry)
    if not route and not stops:
        return _fallback(section, geometry, size, unavailable=False)
    try:
        return _basemap(section, geometry, size, tile_loader or _default_tile_loader)
    except (BasemapUnavailable, HTTPException, URLError, TimeoutError, OSError):
        return _fallback(section, geometry, size, unavailable=True)


def _default_tile_loader(url, timeout):
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()
