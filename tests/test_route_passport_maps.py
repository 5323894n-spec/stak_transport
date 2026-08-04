# -*- coding: utf-8 -*-
from io import BytesIO

from PIL import Image

from app.route_document_data import RouteGeometryData, RouteSection
from app.route_passport_maps import (
    OSM_ATTRIBUTION,
    direction_coordinates,
    render_direction_map,
    render_route_scheme,
)


STOPS = RouteSection(
    "forward",
    (
        {"name": "A", "longitude": 30.0, "latitude": 50.0},
        {"name": "B", "longitude": 31.0, "latitude": 51.0},
    ),
)


def test_saved_manual_geometry_has_priority_over_stop_chord():
    geometry = RouteGeometryData(
        "forward", "manual", 3, ((30.0, 50.0), (30.4, 50.7), (31.0, 51.0))
    )
    assert direction_coordinates(STOPS, geometry) == geometry.coordinates


def test_direction_coordinates_falls_back_to_stop_chord():
    assert direction_coordinates(STOPS, None) == ((30.0, 50.0), (31.0, 51.0))


def test_direction_coordinates_skips_stops_without_coordinates():
    section = RouteSection(
        "forward",
        (
            {"name": "A", "longitude": 30.0, "latitude": 50.0},
            {"name": "B", "longitude": None, "latitude": None},
            {"name": "C", "longitude": 31.0, "latitude": 51.0},
        ),
    )
    assert direction_coordinates(section, None) == ((30.0, 50.0), (31.0, 51.0))


def test_map_returns_valid_png_when_tile_download_fails():
    def unavailable(url, timeout):
        raise OSError("offline")

    result = render_direction_map(
        STOPS, None, size=(900, 600), tile_loader=unavailable
    )
    image = Image.open(BytesIO(result.png))
    assert image.format == "PNG"
    assert image.size == (900, 600)
    assert result.basemap_available is False
    assert "OpenStreetMap" in result.attribution
    assert result.attribution == OSM_ATTRIBUTION


def test_map_uses_basemap_when_tiles_available():
    def solid_tile(url, timeout):
        buffer = BytesIO()
        Image.new("RGB", (256, 256), "#d8e6c8").save(buffer, "PNG")
        return buffer.getvalue()

    result = render_direction_map(
        STOPS, None, size=(600, 400), tile_loader=solid_tile
    )
    image = Image.open(BytesIO(result.png))
    assert image.size == (600, 400)
    assert result.basemap_available is True


def test_route_scheme_is_deterministic_offline_png():
    first = render_route_scheme(STOPS, None, size=(640, 480))
    second = render_route_scheme(STOPS, None, size=(640, 480))
    assert first == second
    image = Image.open(BytesIO(first))
    assert image.format == "PNG"
    assert image.size == (640, 480)


def test_route_scheme_prefers_saved_geometry():
    geometry = RouteGeometryData(
        "forward", "manual", 1, ((30.0, 50.0), (30.5, 50.2), (31.0, 51.0))
    )
    with_geometry = render_route_scheme(STOPS, geometry, size=(640, 480))
    without = render_route_scheme(STOPS, None, size=(640, 480))
    assert with_geometry != without
