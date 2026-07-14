# -*- coding: utf-8 -*-
"""Доменная логика остановок и трасс маршрутов."""


def normalize_stop_name(value: str) -> str:
    normalized = (value or "").strip().lower().replace("«", '"').replace("»", '"')
    return " ".join(normalized.split())


def recalculate_trace(items: list[dict]) -> list[dict]:
    ordered = sorted((dict(item) for item in items), key=lambda item: int(item["sequence"]))
    cumulative = 0.0
    for expected, item in enumerate(ordered, start=1):
        if int(item["sequence"]) != expected:
            raise ValueError("Последовательность остановок должна начинаться с 1 и не иметь пропусков")
        distance = float(item.get("distance_from_prev_km") or 0)
        if distance < 0:
            raise ValueError("Расстояние перегона не может быть отрицательным")
        if expected == 1 and distance != 0:
            raise ValueError("У первой остановки расстояние от предыдущей должно быть равно 0")
        cumulative = round(cumulative + distance, 3)
        item["distance_from_prev_km"] = distance
        item["cumulative_km"] = cumulative
    return ordered
