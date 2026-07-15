# -*- coding: utf-8 -*-
"""Изолированный клиент маршрутизации OSRM."""
import json
import socket
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "https://router.project-osrm.org"


class OSRMError(RuntimeError):
    pass


class OSRMTimeout(OSRMError):
    pass


def _validated_route(payload, expected_legs):
    if not isinstance(payload, dict) or payload.get("code") != "Ok":
        raise OSRMError("OSRM вернул ошибку маршрутизации")
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes:
        raise OSRMError("OSRM не вернул маршрут")
    route = routes[0]
    geometry = route.get("geometry")
    legs = route.get("legs")
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        raise OSRMError("OSRM вернул некорректную геометрию")
    if not isinstance(geometry.get("coordinates"), list):
        raise OSRMError("OSRM вернул некорректные координаты")
    if not isinstance(legs, list) or len(legs) != expected_legs:
        raise OSRMError("Количество перегонов OSRM не совпадает с остановками")
    normalized_legs = []
    for leg in legs:
        try:
            distance = float(leg["distance"])
            duration = float(leg["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OSRMError("OSRM вернул некорректные параметры перегона") from exc
        if distance < 0 or duration < 0:
            raise OSRMError("OSRM вернул отрицательное расстояние или время")
        normalized_legs.append({"distance": distance, "duration": duration})
    return {"geometry": geometry, "legs": normalized_legs}


def request_route(coordinates, base_url=DEFAULT_BASE_URL, timeout=10):
    if len(coordinates) < 2:
        raise OSRMError("Для расчёта OSRM нужны минимум две остановки")
    coordinate_text = ";".join(f"{float(lon):.6f},{float(lat):.6f}" for lon, lat in coordinates)
    encoded = urllib.parse.quote(coordinate_text, safe=";,")
    url = (
        f"{str(base_url or DEFAULT_BASE_URL).rstrip('/')}/route/v1/driving/{encoded}"
        "?overview=full&geometries=geojson&steps=false"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "ATP-System/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (TimeoutError, socket.timeout) as exc:
        raise OSRMTimeout("Сервис OSRM не ответил вовремя") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise OSRMTimeout("Сервис OSRM не ответил вовремя") from exc
        raise OSRMError("Сервис OSRM недоступен") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OSRMError("OSRM вернул нечитаемый ответ") from exc
    return _validated_route(payload, len(coordinates) - 1)
