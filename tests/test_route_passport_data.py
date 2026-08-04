# -*- coding: utf-8 -*-
import dataclasses
import json

import pytest

from app.route_document_data import load_route_document_data
from tests.test_route_document_data import _database


def _save_geometry_row(
    con, route_id, direction, coordinates, source="manual", version=1
):
    con.execute(
        """
        INSERT INTO route_geometries(
          route_id, direction, geometry_json, source, version,
          updated_by, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            route_id,
            direction,
            json.dumps(
                {"type": "LineString", "coordinates": coordinates},
                ensure_ascii=False,
            ),
            source,
            version,
            "admin",
            "2026-08-03T00:00:00",
            "2026-08-03T00:00:00",
        ),
    )


def test_document_data_exposes_saved_geometry_for_each_direction(tmp_path):
    con, route_id = _database(tmp_path)
    try:
        _save_geometry_row(
            con, route_id, "forward", [[30.0, 50.0], [31.0, 51.0]], "manual", 3
        )
        con.commit()
        data = load_route_document_data(con, route_id)
    finally:
        con.close()
    assert data.geometries["forward"].source == "manual"
    assert data.geometries["forward"].version == 3
    assert data.geometries["forward"].coordinates == (
        (30.0, 50.0),
        (31.0, 51.0),
    )
    assert data.geometries["backward"] is None


def test_document_data_geometry_is_immutable_and_optional(tmp_path):
    con, route_id = _database(tmp_path)
    try:
        data = load_route_document_data(con, route_id)
        assert data.geometries == {"forward": None, "backward": None}
        _save_geometry_row(
            con, route_id, "forward", [[30.0, 50.0], [31.0, 51.0]]
        )
        con.commit()
        geometry = load_route_document_data(con, route_id).geometries["forward"]
    finally:
        con.close()
    with pytest.raises(dataclasses.FrozenInstanceError):
        geometry.source = "osrm"
