# -*- coding: utf-8 -*-
import json

import pytest
from fastapi.testclient import TestClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def make_client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "route-osrm.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()
    client = TestClient(app)
    token = client.post("/api/login", json={
        "username": "admin", "password": "admin",
    }).json()["token"]
    client.headers.update({"Authorization": "Bearer " + token})
    route_id = client.post("/api/refs/routes", json={
        "number": "12", "name": "OSRM маршрут",
    }).json()["id"]
    return client, route_id


def add_trace(client, route_id, with_coordinates=True):
    coordinates = [
        {"latitude": 56.801, "longitude": 35.901},
        {"latitude": 56.802, "longitude": 35.902},
    ] if with_coordinates else [{}, {}]
    stop_ids = []
    for index, name in enumerate(("Вокзал", "Автовокзал")):
        response = client.post("/api/stops", json={
            "name": name,
            "external_code": str(100 + index),
            **coordinates[index],
        })
        stop_ids.append(response.json()["id"])
    response = client.put(f"/api/routes/{route_id}/stops/forward", json={"items": [
        {"stop_id": stop_ids[0], "sequence": 1, "distance_from_prev_km": 0},
        {"stop_id": stop_ids[1], "sequence": 2, "distance_from_prev_km": 1.0,
         "run_time_sec": 300},
    ]})
    assert response.status_code == 200


def add_three_stop_trace(client, route_id, directions=("forward",)):
    coordinates = [
        [35.901, 56.801],
        [35.906, 56.804],
        [35.912, 56.809],
    ]
    stop_ids = []
    for index, (longitude, latitude) in enumerate(coordinates, 1):
        response = client.post("/api/stops", json={
            "name": f"OSRM точка {index}",
            "external_code": f"OSRM-{index}",
            "longitude": longitude,
            "latitude": latitude,
        })
        assert response.status_code == 200, response.text
        stop_ids.append(response.json()["id"])

    anchors = {}
    for direction in directions:
        ordered_ids = stop_ids if direction == "forward" else list(reversed(stop_ids))
        response = client.put(
            f"/api/routes/{route_id}/stops/{direction}",
            json={"items": [
                {
                    "stop_id": stop_id,
                    "sequence": sequence,
                    "distance_from_prev_km": 0 if sequence == 1 else sequence,
                    "run_time_sec": 0 if sequence == 1 else sequence * 100,
                }
                for sequence, stop_id in enumerate(ordered_ids, 1)
            ]},
        )
        assert response.status_code == 200, response.text
        anchors[direction] = (
            coordinates if direction == "forward" else list(reversed(coordinates))
        )
    return anchors


def realistic_osrm_result(anchors):
    first, middle, last = anchors
    return {
        "geometry": {
            "type": "LineString",
            "coordinates": [
                [first[0] + 0.00004, first[1] - 0.00003],
                [(first[0] + middle[0]) / 2, (first[1] + middle[1]) / 2],
                [middle[0] - 0.00003, middle[1] + 0.00002],
                [(middle[0] + last[0]) / 2, (middle[1] + last[1]) / 2],
                [last[0] + 0.00002, last[1] - 0.00004],
            ],
        },
        "legs": [
            {"distance": 1250, "duration": 180},
            {"distance": 1750, "duration": 240},
        ],
    }


def osrm_audits(route_id):
    from app import db

    con = db.connect()
    try:
        return [dict(row) for row in con.execute(
            "SELECT * FROM audit_log WHERE action='применение трассы OSRM' "
            "AND object_id=? ORDER BY id",
            (str(route_id),),
        )]
    finally:
        con.close()


def osrm_state(route_id, direction, token):
    from app import db
    from app.route_geometry import get_geometry

    con = db.connect()
    try:
        length_field = "length_km" if direction == "forward" else "length_back_km"
        preview = con.execute(
            "SELECT applied_at FROM route_import_previews WHERE token=?", (token,)
        ).fetchone()
        return {
            "stops": [dict(row) for row in con.execute(
                "SELECT id,distance_from_prev_km,run_time_sec,cumulative_km,"
                "distance_source FROM route_stops WHERE route_id=? AND direction=? "
                "ORDER BY sequence",
                (route_id, direction),
            )],
            "geometry": get_geometry(con, route_id, direction),
            "length": con.execute(
                f"SELECT {length_field} FROM routes WHERE id=?", (route_id,)
            ).fetchone()[length_field],
            "applied_at": preview["applied_at"] if preview else None,
            "audits": osrm_audits(route_id),
        }
    finally:
        con.close()


def test_normalize_osrm_geometry_snaps_ordered_anchors_and_preserves_shape():
    from app import route_geometry

    anchors = [[35.901, 56.801], [35.906, 56.804], [35.912, 56.809]]
    geometry = realistic_osrm_result(anchors)["geometry"]
    intermediate = [geometry["coordinates"][1][:], geometry["coordinates"][3][:]]

    normalized = route_geometry.normalize_osrm_geometry(geometry, anchors)

    assert normalized["coordinates"][0] == anchors[0]
    assert normalized["coordinates"][2] == anchors[1]
    assert normalized["coordinates"][4] == anchors[2]
    assert [normalized["coordinates"][1], normalized["coordinates"][3]] == intermediate

def test_osrm_client_validates_and_normalizes_response(monkeypatch):
    import app.osrm as osrm

    monkeypatch.setattr(osrm.urllib.request, "urlopen", lambda request, timeout: FakeResponse({
        "code": "Ok",
        "routes": [{
            "geometry": {"type": "LineString", "coordinates": [[35.901, 56.801], [35.902, 56.802]]},
            "legs": [{"distance": 1500.4, "duration": 240.2}],
        }],
    }))

    result = osrm.request_route([(35.901, 56.801), (35.902, 56.802)],
                                base_url="https://router.test")

    assert result["legs"] == [{"distance": 1500.4, "duration": 240.2}]
    assert result["geometry"]["type"] == "LineString"


def test_osrm_preview_does_not_apply_until_confirmed(tmp_path, monkeypatch):
    import app.osrm as osrm

    client, route_id = make_client(tmp_path)
    add_trace(client, route_id)
    monkeypatch.setattr(osrm, "request_route", lambda coordinates, **kwargs: {
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "legs": [{"distance": 1500, "duration": 240}],
    })

    preview = client.post(f"/api/routes/{route_id}/osrm/preview/forward")

    assert preview.status_code == 200, preview.text
    assert preview.json()["diff"][0]["old_distance_km"] == 1.0
    assert preview.json()["diff"][0]["new_distance_km"] == 1.5
    before = client.get(f"/api/routes/{route_id}/network").json()
    assert before["forward"][1]["distance_from_prev_km"] == 1.0

    applied = client.post(f"/api/routes/{route_id}/osrm/apply/forward", json={
        "preview_token": preview.json()["preview_token"],
        "expected_geometry_version": preview.json()["geometry_version"],
    })

    assert applied.status_code == 200, applied.text
    after = client.get(f"/api/routes/{route_id}/network").json()
    assert after["forward"][1]["distance_from_prev_km"] == 1.5
    assert after["forward"][1]["run_time_sec"] == 240
    assert after["forward"][1]["distance_source"] == "auto_osrm"


def test_osrm_preview_requires_coordinates(tmp_path):
    client, route_id = make_client(tmp_path)
    add_trace(client, route_id, with_coordinates=False)

    response = client.post(f"/api/routes/{route_id}/osrm/preview/forward")

    assert response.status_code == 400
    assert "координат" in response.json()["detail"]




def test_osrm_preview_rejects_coordinate_outlier_before_external_request(
    tmp_path, monkeypatch,
):
    from app import osrm

    client, route_id = make_client(tmp_path)
    add_three_stop_trace(client, route_id)
    network = client.get(f"/api/routes/{route_id}/network").json()
    middle_stop_id = network["forward"][1]["stop"]["id"]
    updated = client.put(f"/api/stops/{middle_stop_id}", json={
        "latitude": 56.94577771717275,
        "longitude": 35.15280271543731,
    })
    assert updated.status_code == 200, updated.text

    called = False

    def unexpected_request(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("OSRM must not receive implausible coordinates")

    monkeypatch.setattr(osrm, "request_route", unexpected_request)
    response = client.post(f"/api/routes/{route_id}/osrm/preview/forward")

    assert response.status_code == 400
    assert called is False
def test_osrm_timeout_is_reported_as_service_unavailable(tmp_path, monkeypatch):
    import app.osrm as osrm

    client, route_id = make_client(tmp_path)
    add_trace(client, route_id)

    def timeout(*args, **kwargs):
        raise osrm.OSRMTimeout("Сервис OSRM не ответил вовремя")

    monkeypatch.setattr(osrm, "request_route", timeout)
    response = client.post(f"/api/routes/{route_id}/osrm/preview/forward")

    assert response.status_code == 503
    assert "OSRM" in response.json()["detail"]


def test_osrm_preview_snaps_all_anchors_without_mutating_network(tmp_path, monkeypatch):
    import app.osrm as osrm

    client, route_id = make_client(tmp_path)
    anchors = add_three_stop_trace(client, route_id)["forward"]
    result = realistic_osrm_result(anchors)
    monkeypatch.setattr(osrm, "request_route", lambda coordinates, **kwargs: result)

    preview = client.post(f"/api/routes/{route_id}/osrm/preview/forward")

    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["geometry_version"] == 0
    assert body["geometry"]["coordinates"][0] == anchors[0]
    assert body["geometry"]["coordinates"][2] == anchors[1]
    assert body["geometry"]["coordinates"][4] == anchors[2]
    network = client.get(f"/api/routes/{route_id}/network").json()
    assert network["geometries"]["forward"] is None
    assert [row["distance_from_prev_km"] for row in network["forward"]] == [0.0, 2.0, 3.0]
    assert [row["run_time_sec"] for row in network["forward"]] == [0, 200, 300]


def test_osrm_apply_persists_geometry_and_distance_updates_atomically(tmp_path, monkeypatch):
    import app.osrm as osrm

    client, route_id = make_client(tmp_path)
    anchors = add_three_stop_trace(client, route_id)["forward"]
    monkeypatch.setattr(
        osrm, "request_route", lambda coordinates, **kwargs: realistic_osrm_result(anchors)
    )
    preview = client.post(f"/api/routes/{route_id}/osrm/preview/forward")
    assert preview.status_code == 200, preview.text

    applied = client.post(f"/api/routes/{route_id}/osrm/apply/forward", json={
        "preview_token": preview.json()["preview_token"],
        "expected_geometry_version": 0,
    })

    assert applied.status_code == 200, applied.text
    network = client.get(f"/api/routes/{route_id}/network").json()
    assert [row["distance_from_prev_km"] for row in network["forward"]] == [0.0, 1.25, 1.75]
    assert [row["run_time_sec"] for row in network["forward"]] == [0, 180, 240]
    geometry = network["geometries"]["forward"]
    assert geometry["source"] == "osrm"
    assert geometry["version"] == 1
    assert geometry["geometry"] == preview.json()["geometry"]


def test_osrm_apply_rejects_stale_preview_without_partial_mutation(tmp_path, monkeypatch):
    import app.osrm as osrm

    client, route_id = make_client(tmp_path)
    anchors = add_three_stop_trace(client, route_id)["forward"]
    manual_v1 = {"type": "LineString", "coordinates": [
        anchors[0], [35.904, 56.803], anchors[1], anchors[2],
    ]}
    saved = client.put(f"/api/routes/{route_id}/geometry/forward", json={
        "geometry": manual_v1, "expected_version": 0,
    })
    assert saved.status_code == 200, saved.text
    monkeypatch.setattr(
        osrm, "request_route", lambda coordinates, **kwargs: realistic_osrm_result(anchors)
    )
    preview = client.post(f"/api/routes/{route_id}/osrm/preview/forward")
    assert preview.status_code == 200, preview.text
    assert preview.json()["geometry_version"] == 1
    token = preview.json()["preview_token"]
    manual_v2 = {"type": "LineString", "coordinates": [
        anchors[0], [35.905, 56.8035], anchors[1], anchors[2],
    ]}
    updated = client.put(f"/api/routes/{route_id}/geometry/forward", json={
        "geometry": manual_v2, "expected_version": 1,
    })
    assert updated.status_code == 200, updated.text
    before = osrm_state(route_id, "forward", token)

    applied = client.post(f"/api/routes/{route_id}/osrm/apply/forward", json={
        "preview_token": token,
        "expected_geometry_version": 1,
    })

    assert applied.status_code == 409, applied.text
    assert osrm_state(route_id, "forward", token) == before


def test_osrm_apply_rejects_version_different_from_preview_before_mutation(tmp_path, monkeypatch):
    import app.osrm as osrm

    client, route_id = make_client(tmp_path)
    anchors = add_three_stop_trace(client, route_id)["forward"]
    monkeypatch.setattr(
        osrm, "request_route", lambda coordinates, **kwargs: realistic_osrm_result(anchors)
    )
    preview = client.post(f"/api/routes/{route_id}/osrm/preview/forward")
    assert preview.status_code == 200, preview.text
    token = preview.json()["preview_token"]
    before = osrm_state(route_id, "forward", token)

    applied = client.post(f"/api/routes/{route_id}/osrm/apply/forward", json={
        "preview_token": token,
        "expected_geometry_version": 1,
    })

    assert applied.status_code == 409, applied.text
    assert osrm_state(route_id, "forward", token) == before


def test_osrm_preview_rejects_geometry_with_fewer_vertices_than_stops(tmp_path, monkeypatch):
    import app.osrm as osrm
    from app import db

    client, route_id = make_client(tmp_path)
    anchors = add_three_stop_trace(client, route_id)["forward"]
    monkeypatch.setattr(osrm, "request_route", lambda coordinates, **kwargs: {
        "geometry": {"type": "LineString", "coordinates": [anchors[0], anchors[-1]]},
        "legs": [
            {"distance": 1000, "duration": 100},
            {"distance": 1000, "duration": 100},
        ],
    })

    response = client.post(f"/api/routes/{route_id}/osrm/preview/forward")

    assert response.status_code == 502, response.text
    assert "координат" in response.json()["detail"].lower()
    con = db.connect()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM route_import_previews WHERE route_id=? "
            "AND source_name='osrm:forward'",
            (route_id,),
        ).fetchone()[0] == 0
    finally:
        con.close()


@pytest.mark.parametrize("failure_point", ["save_geometry", "audit"])
def test_osrm_apply_rolls_back_everything_after_injected_failure(
    tmp_path, monkeypatch, failure_point
):
    import app.osrm as osrm
    import app.api_route_network as api_route_network

    client, route_id = make_client(tmp_path)
    anchors = add_three_stop_trace(client, route_id)["forward"]
    monkeypatch.setattr(
        osrm, "request_route", lambda coordinates, **kwargs: realistic_osrm_result(anchors)
    )
    preview = client.post(f"/api/routes/{route_id}/osrm/preview/forward")
    assert preview.status_code == 200, preview.text
    token = preview.json()["preview_token"]
    before = osrm_state(route_id, "forward", token)

    def fail(*args, **kwargs):
        raise RuntimeError(f"forced {failure_point} failure")

    if failure_point == "save_geometry":
        monkeypatch.setattr(api_route_network, "save_geometry", fail)
    else:
        monkeypatch.setattr(api_route_network.db, "audit", fail)

    with pytest.raises(RuntimeError, match=f"forced {failure_point} failure"):
        client.post(f"/api/routes/{route_id}/osrm/apply/forward", json={
            "preview_token": token,
            "expected_geometry_version": 0,
        })

    assert osrm_state(route_id, "forward", token) == before


def test_osrm_apply_writes_one_summary_audit_and_cannot_repeat(tmp_path, monkeypatch):
    import app.osrm as osrm

    client, route_id = make_client(tmp_path)
    anchors = add_three_stop_trace(client, route_id)["forward"]
    monkeypatch.setattr(
        osrm, "request_route", lambda coordinates, **kwargs: realistic_osrm_result(anchors)
    )
    preview = client.post(f"/api/routes/{route_id}/osrm/preview/forward")
    token = preview.json()["preview_token"]
    payload = {"preview_token": token, "expected_geometry_version": 0}

    first = client.post(f"/api/routes/{route_id}/osrm/apply/forward", json=payload)
    second = client.post(f"/api/routes/{route_id}/osrm/apply/forward", json=payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    audits = osrm_audits(route_id)
    assert len(audits) == 1
    old_summary = json.loads(audits[0]["old_value"])
    new_summary = json.loads(audits[0]["new_value"])
    assert old_summary == {
        "direction": "forward", "source": None, "version": 0, "coordinates": 0,
    }
    assert new_summary["direction"] == "forward"
    assert new_summary["source"] == "osrm"
    assert new_summary["version"] == 1
    assert new_summary["coordinates"] == 5
    assert new_summary["total_km"] == 3.0
    assert new_summary["diff"] == preview.json()["diff"]
    assert "LineString" not in audits[0]["old_value"]
    assert "LineString" not in audits[0]["new_value"]


def test_osrm_forward_and_backward_geometry_versions_are_independent(tmp_path, monkeypatch):
    import app.osrm as osrm

    client, route_id = make_client(tmp_path)
    add_three_stop_trace(client, route_id, directions=("forward", "backward"))
    monkeypatch.setattr(
        osrm,
        "request_route",
        lambda coordinates, **kwargs: realistic_osrm_result([list(point) for point in coordinates]),
    )

    forward_preview = client.post(f"/api/routes/{route_id}/osrm/preview/forward")
    assert forward_preview.status_code == 200, forward_preview.text
    assert forward_preview.json()["geometry_version"] == 0
    forward_apply = client.post(f"/api/routes/{route_id}/osrm/apply/forward", json={
        "preview_token": forward_preview.json()["preview_token"],
        "expected_geometry_version": 0,
    })
    assert forward_apply.status_code == 200, forward_apply.text
    halfway = client.get(f"/api/routes/{route_id}/network").json()
    assert halfway["geometries"]["forward"]["version"] == 1
    assert halfway["geometries"]["backward"] is None
    assert [row["distance_from_prev_km"] for row in halfway["backward"]] == [0.0, 2.0, 3.0]

    backward_preview = client.post(f"/api/routes/{route_id}/osrm/preview/backward")
    assert backward_preview.status_code == 200, backward_preview.text
    assert backward_preview.json()["geometry_version"] == 0
    backward_apply = client.post(f"/api/routes/{route_id}/osrm/apply/backward", json={
        "preview_token": backward_preview.json()["preview_token"],
        "expected_geometry_version": 0,
    })
    assert backward_apply.status_code == 200, backward_apply.text
    network = client.get(f"/api/routes/{route_id}/network").json()
    assert network["geometries"]["forward"]["version"] == 1
    assert network["geometries"]["backward"]["version"] == 1
    assert network["geometries"]["forward"]["geometry"] != network["geometries"]["backward"]["geometry"]


def test_normalize_osrm_geometry_uses_global_monotone_minimum():
    from app.route_geometry import normalize_osrm_geometry

    geometry = {
        "type": "LineString",
        "coordinates": [[0.1, 0], [10, 0], [0, 0], [20, 0]],
    }
    anchors = [[0, 0], [10, 0]]

    normalized = normalize_osrm_geometry(geometry, anchors)

    assert normalized["coordinates"] == [
        [0.0, 0.0], [10.0, 0.0], [0.0, 0.0], [20.0, 0.0],
    ]


def test_normalize_osrm_geometry_breaks_ties_lexicographically_and_preserves_vertices():
    from app.route_geometry import normalize_osrm_geometry

    geometry = {
        "type": "LineString",
        "coordinates": [[1, 0], [2, 1], [1, 0], [4, 0]],
    }
    anchors = [[0, 0], [4, 0]]

    normalized = normalize_osrm_geometry(geometry, anchors)

    assert normalized["coordinates"] == [
        [0.0, 0.0], [2.0, 1.0], [1.0, 0.0], [4.0, 0.0],
    ]


@pytest.mark.parametrize("duplicate", [[0, 0], [0.0000005, 0]])
def test_normalize_osrm_geometry_rejects_adjacent_duplicate_vertices(duplicate):
    from app.route_geometry import GeometryValidationError, normalize_osrm_geometry

    with pytest.raises(GeometryValidationError, match="Соседние координаты"):
        normalize_osrm_geometry(
            {"type": "LineString", "coordinates": [[0, 0], duplicate, [1, 0]]},
            [[0, 0], [1, 0]],
        )


def test_normalize_osrm_geometry_rejects_unreasonably_complex_assignment(monkeypatch):
    from app import route_geometry

    monkeypatch.setattr(
        route_geometry, "MAX_OSRM_ASSIGNMENT_CELLS", 3, raising=False
    )

    with pytest.raises(
        route_geometry.GeometryValidationError, match="слишком слож"
    ):
        route_geometry.normalize_osrm_geometry(
            {"type": "LineString", "coordinates": [[0, 0], [1, 1], [2, 2]]},
            [[0, 0], [2, 2]],
        )


def test_osrm_preview_plan_records_exact_ordered_trace_snapshot(tmp_path, monkeypatch):
    import app.osrm as osrm
    from app import db

    client, route_id = make_client(tmp_path)
    anchors = add_three_stop_trace(client, route_id)["forward"]
    monkeypatch.setattr(
        osrm, "request_route", lambda coordinates, **kwargs: realistic_osrm_result(anchors)
    )

    preview = client.post(f"/api/routes/{route_id}/osrm/preview/forward")
    assert preview.status_code == 200, preview.text
    con = db.connect()
    try:
        plan = json.loads(con.execute(
            "SELECT payload_json FROM route_import_previews WHERE token=?",
            (preview.json()["preview_token"],),
        ).fetchone()["payload_json"])
    finally:
        con.close()

    snapshot = plan["trace_snapshot"]
    assert snapshot["segment_count"] == 2
    assert [item["sequence"] for item in snapshot["stops"]] == [1, 2, 3]
    assert [item["longitude"] for item in snapshot["stops"]] == [
        anchor[0] for anchor in anchors
    ]
    assert [item["latitude"] for item in snapshot["stops"]] == [
        anchor[1] for anchor in anchors
    ]
    assert all(
        {"route_stop_id", "stop_id", "sequence", "longitude", "latitude"}
        <= item.keys()
        for item in snapshot["stops"]
    )


def test_osrm_apply_rejects_shortened_recreated_trace_with_reused_row_ids(
    tmp_path, monkeypatch
):
    import app.osrm as osrm

    client, route_id = make_client(tmp_path)
    anchors = add_three_stop_trace(client, route_id)["forward"]
    monkeypatch.setattr(
        osrm, "request_route", lambda coordinates, **kwargs: realistic_osrm_result(anchors)
    )
    original_rows = client.get(f"/api/routes/{route_id}/network").json()["forward"]
    preview = client.post(f"/api/routes/{route_id}/osrm/preview/forward")
    assert preview.status_code == 200, preview.text
    token = preview.json()["preview_token"]

    replaced = client.put(f"/api/routes/{route_id}/stops/forward", json={"items": [
        {
            "stop_id": original_rows[0]["stop"]["id"],
            "sequence": 1,
            "distance_from_prev_km": 0,
            "run_time_sec": 0,
        },
        {
            "stop_id": original_rows[-1]["stop"]["id"],
            "sequence": 2,
            "distance_from_prev_km": 9,
            "run_time_sec": 900,
        },
    ]})
    assert replaced.status_code == 200, replaced.text
    recreated_rows = replaced.json()["items"]
    assert [row["id"] for row in recreated_rows] == [
        row["id"] for row in original_rows[:2]
    ]
    before = osrm_state(route_id, "forward", token)

    applied = client.post(f"/api/routes/{route_id}/osrm/apply/forward", json={
        "preview_token": token,
        "expected_geometry_version": 0,
    })

    assert applied.status_code == 409, applied.text
    assert "трасс" in applied.json()["detail"].lower()
    assert osrm_state(route_id, "forward", token) == before
