# -*- coding: utf-8 -*-
import io

from PIL import Image

import pytest

from app import route_passport_maps as maps
from app.route_document_data import RouteGeometryData, RouteSection
from app.route_passport_maps import (
    OSM_ATTRIBUTION,
    direction_coordinates,
    render_direction_map,
    render_route_scheme,
)


def _section(*coordinates):
    return RouteSection(
        direction="forward",
        stops=tuple(
            {
                "name": f"Остановка {number}",
                "longitude": longitude,
                "latitude": latitude,
            }
            for number, (longitude, latitude) in enumerate(coordinates, start=1)
        ),
    )


def _png_size(png):
    with Image.open(io.BytesIO(png)) as image:
        return image.size


def _tile_bytes(color=(240, 240, 240, 255)):
    image = Image.new("RGBA", (256, 256), color)
    data = io.BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


def test_direction_coordinates_prioritize_manual_geometry_over_stop_line():
    section = _section((37.0, 55.0), (38.0, 56.0))
    geometry = RouteGeometryData(
        direction="forward",
        source="manual",
        version=1,
        coordinates=((37.0, 55.0), (37.4, 55.2), (38.0, 56.0)),
    )

    assert direction_coordinates(section, geometry) == (
        (37.0, 55.0), (37.4, 55.2), (38.0, 56.0)
    )


def test_direction_coordinates_falls_back_to_complete_stop_coordinates_only():
    section = RouteSection(
        direction="forward",
        stops=(
            {"longitude": 37.0, "latitude": 55.0},
            {"longitude": None, "latitude": 55.5},
            {"longitude": 38.0, "latitude": 56.0},
        ),
    )

    assert direction_coordinates(section, None) == ((37.0, 55.0), (38.0, 56.0))


def test_direction_map_falls_back_to_nonempty_png_when_tiles_fail():
    section = _section((37.0, 55.0), (38.0, 56.0))

    def unavailable(url, timeout):
        raise OSError("network unavailable")

    rendered = render_direction_map(
        section, None, size=(900, 600), tile_loader=unavailable
    )

    assert _png_size(rendered.png) == (900, 600)
    assert rendered.basemap_available is False
    assert rendered.attribution == OSM_ATTRIBUTION
    with Image.open(io.BytesIO(rendered.png)) as image:
        assert image.getbbox() is not None


def test_offline_scheme_is_valid_and_deterministic():
    section = _section((37.0, 55.0), (38.0, 56.0))

    first = render_route_scheme(section, None, size=(640, 480))
    second = render_route_scheme(section, None, size=(640, 480))

    assert _png_size(first.png) == (640, 480)
    assert first.png == second.png
    assert first.basemap_available is False
    assert first.attribution == OSM_ATTRIBUTION


def test_direction_map_uses_tile_loader_without_network():
    section = _section((37.0, 55.0), (38.0, 56.0))
    calls = []

    def loader(url, timeout):
        calls.append((url, timeout))
        return _tile_bytes()

    rendered = render_direction_map(section, None, tile_loader=loader)

    assert rendered.basemap_available is True
    assert rendered.attribution == OSM_ATTRIBUTION
    assert _png_size(rendered.png) == (1200, 800)
    assert calls
    assert all(url.startswith("https://tile.openstreetmap.org/") for url, _ in calls)
    assert all(timeout == 3 for _, timeout in calls)


def test_direction_map_limits_tile_loader_to_24_calls():
    section = _section((179.0, 55.0), (-179.0, 55.0))
    calls = []

    def loader(url, timeout):
        calls.append(url)
        return _tile_bytes()

    rendered = render_direction_map(section, None, tile_loader=loader)

    assert rendered.basemap_available is True
    assert 0 < len(calls) <= 24


def test_renderers_accept_zero_or_one_coordinate():
    empty = RouteSection(direction="forward", stops=())
    one = _section((37.0, 55.0))

    for section in (empty, one):
        assert _png_size(render_route_scheme(section, None).png) == (1200, 800)
        fallback = render_direction_map(
            section, None, tile_loader=lambda url, timeout: _tile_bytes()
        )
        assert _png_size(fallback.png) == (1200, 800)


@pytest.fixture(autouse=True)
def _clear_tile_cache():
    maps._TILE_CACHE.clear()
    yield
    maps._TILE_CACHE.clear()


def test_direction_map_without_coordinates_does_not_load_tiles():
    calls = []

    rendered = render_direction_map(
        RouteSection(direction="forward", stops=()),
        None,
        tile_loader=lambda url, timeout: calls.append(url) or _tile_bytes(),
    )

    assert rendered.basemap_available is False
    assert calls == []


def test_mosaic_transform_preserves_one_pixel_scale_for_both_axes():
    transform = maps._mosaic_transform((768, 512), (900, 600))

    assert transform.scale_x == transform.scale_y


def test_antimeridian_coordinates_are_unwrapped_to_the_short_crossing():
    unwrapped = maps._unwrap_coordinates(((179.0, 55.0), (-179.0, 55.0)))

    assert unwrapped[1][0] - unwrapped[0][0] == pytest.approx(2.0)


def test_single_point_is_centered_in_the_fallback_projection():
    project = maps._screen_projector(((37.0, 55.0),), (900, 600))

    assert project((37.0, 55.0)) == (450.0, 300.0)


def test_loader_contract_value_error_is_not_converted_to_fallback():
    def invalid_loader(url, timeout):
        raise ValueError("loader contract")

    with pytest.raises(ValueError, match="loader contract"):
        render_direction_map(
            _section((37.0, 55.0), (38.0, 56.0)),
            None,
            tile_loader=invalid_loader,
        )


def test_corrupted_tile_is_not_cached_and_is_retried():
    calls = []

    def loader(url, timeout):
        calls.append(url)
        return b"not a png" if len(calls) == 1 else _tile_bytes()

    section = _section((37.0, 55.0), (38.0, 56.0))
    assert render_direction_map(section, None, tile_loader=loader).basemap_available is False
    assert render_direction_map(section, None, tile_loader=loader).basemap_available is True
    assert len(calls) > 1


def test_tile_cache_is_scoped_to_the_loader_identity():
    section = _section((37.0, 55.0), (38.0, 56.0))
    first_calls, second_calls = [], []

    def first(url, timeout):
        first_calls.append(url)
        return _tile_bytes((220, 10, 10, 255))

    def second(url, timeout):
        second_calls.append(url)
        return _tile_bytes((10, 10, 220, 255))

    assert render_direction_map(section, None, tile_loader=first).basemap_available
    assert render_direction_map(section, None, tile_loader=second).basemap_available
    assert first_calls and second_calls


def test_ascii_attribution_keeps_full_legal_wording():
    assert maps._ASCII_ATTRIBUTION == "(c) OpenStreetMap contributors"


def test_direction_map_falls_back_after_interrupted_http_response():
    section = _section((37.0, 55.0), (38.0, 56.0))

    def interrupted(url, timeout):
        from http.client import IncompleteRead
        raise IncompleteRead(b"", 256)

    rendered = render_direction_map(section, None, size=(900, 600), tile_loader=interrupted)
    assert _png_size(rendered.png) == (900, 600)
    assert rendered.basemap_available is False
