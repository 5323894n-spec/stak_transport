# -*- coding: utf-8 -*-
"""Neutral data model shared by route document exporters."""

from dataclasses import dataclass
import datetime
from typing import Any


_SEASONS = {
    "winter": ("ЗИМНИЙ ПЕРИОД", "ЗИМА"),
    "summer": ("ЛЕТНИЙ ПЕРИОД", "ЛЕТО"),
}
_DAY_TYPES = (
    ("workday", ("будни", "workday")),
    ("weekend", ("выходные", "weekend", "суббота", "воскресенье")),
)


@dataclass(frozen=True)
class DocumentOptions:
    season: str
    season_label: str
    file_token: str
    effective_date: datetime.date


@dataclass(frozen=True)
class RouteSection:
    direction: str
    stops: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ScheduleTrip:
    output_number: int
    shift_number: int
    trip_number: int | None
    direction: str
    departure_sec: int | None
    arrival_sec: int | None
    distance_km: float
    break_after_sec: int
    break_type: str


@dataclass(frozen=True)
class ScheduleOutput:
    day_type: str
    output_number: int
    trips: tuple[ScheduleTrip, ...]


@dataclass(frozen=True)
class RouteDocumentData:
    route_id: int
    route_number: str
    route_name: str
    start_point: str
    end_point: str
    version: int
    forward: RouteSection
    backward: RouteSection
    depot_out: RouteSection
    depot_in: RouteSection
    schedules: dict[str, tuple[ScheduleOutput, ...]]


def parse_document_options(season, effective_date):
    """Validate request options and return labels used by official documents."""
    if season not in _SEASONS:
        raise ValueError("Сезон должен быть winter или summer")
    if not isinstance(effective_date, str):
        raise ValueError("Дата вступления в силу должна иметь формат YYYY-MM-DD")
    try:
        parsed_date = datetime.date.fromisoformat(effective_date)
    except ValueError:
        raise ValueError(
            "Дата вступления в силу должна иметь формат YYYY-MM-DD"
        ) from None
    if parsed_date.isoformat() != effective_date:
        raise ValueError("Дата вступления в силу должна иметь формат YYYY-MM-DD")
    season_label, file_token = _SEASONS[season]
    return DocumentOptions(
        season=season,
        season_label=season_label,
        file_token=file_token,
        effective_date=parsed_date,
    )


def _clock_seconds(value):
    if value in (None, ""):
        return None
    parts = str(value).strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Некорректное время расписания: {value}")
    try:
        hours, minutes = int(parts[0]), int(parts[1])
        seconds = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        raise ValueError(f"Некорректное время расписания: {value}") from None
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError(f"Некорректное время расписания: {value}")
    return hours * 3600 + minutes * 60 + seconds


def _route_sections(con, route_id):
    sections = {
        direction: RouteSection(direction, ())
        for direction in ("forward", "backward", "depot_out", "depot_in")
    }
    network_rows = con.execute(
        """
        SELECT rs.direction,rs.stop_id,rs.sequence,s.name AS name,
               s.external_code,s.address,s.latitude,s.longitude,
               rs.distance_from_prev_km,rs.cumulative_km,
               rs.run_time_day_sec,rs.run_time_night_sec,rs.dwell_time_sec,
               rs.boarding_allowed,rs.alighting_allowed,rs.is_timing_point
        FROM route_stops rs
        JOIN stops s ON s.id=rs.stop_id
        WHERE rs.route_id=? AND rs.direction IN ('forward','backward')
        ORDER BY CASE rs.direction WHEN 'forward' THEN 0 ELSE 1 END,
                 rs.sequence,rs.id
        """,
        (route_id,),
    ).fetchall()
    depot_rows = con.execute(
        """
        SELECT rds.direction,rds.stop_id,rds.sequence,s.name AS name,
               s.external_code,s.address,s.latitude,s.longitude,
               rds.distance_from_prev_km,
               rds.run_time_day_sec,rds.run_time_night_sec,rds.source
        FROM route_depot_stops rds
        JOIN stops s ON s.id=rds.stop_id
        WHERE rds.route_id=? AND rds.direction IN ('depot_out','depot_in')
        ORDER BY CASE rds.direction WHEN 'depot_out' THEN 0 ELSE 1 END,
                 rds.sequence,rds.id
        """,
        (route_id,),
    ).fetchall()
    grouped = {direction: [] for direction in sections}
    cumulative_by_direction = {"depot_out": 0.0, "depot_in": 0.0}
    for row in network_rows:
        item = dict(row)
        for key in ("distance_from_prev_km", "cumulative_km"):
            item[key] = float(item[key] or 0)
        for key in ("latitude", "longitude"):
            item[key] = float(item[key]) if item[key] is not None else None
        for key in (
            "run_time_day_sec", "run_time_night_sec", "dwell_time_sec",
            "boarding_allowed", "alighting_allowed", "is_timing_point",
        ):
            item[key] = int(item[key] or 0)
        grouped[item["direction"]].append(item)
    for row in depot_rows:
        item = dict(row)
        item["distance_from_prev_km"] = float(item["distance_from_prev_km"] or 0)
        for key in ("latitude", "longitude"):
            item[key] = float(item[key]) if item[key] is not None else None
        cumulative_by_direction[item["direction"]] += item["distance_from_prev_km"]
        item["cumulative_km"] = round(cumulative_by_direction[item["direction"]], 3)
        item["run_time_day_sec"] = int(item["run_time_day_sec"] or 0)
        item["run_time_night_sec"] = int(item["run_time_night_sec"] or 0)
        grouped[item["direction"]].append(item)
    return {
        direction: RouteSection(direction, tuple(grouped[direction]))
        for direction in sections
    }


def _route_schedules(con, route_id):
    rows = con.execute(
        """
        SELECT id,day_type,output_number,shift_number,trip_number,direction,
               dep_time,arr_time,distance_km,break_after_min,break_type
        FROM route_trips
        WHERE route_id=? AND day_type IN (
          'будни','workday','выходные','weekend','суббота','воскресенье'
        )
        ORDER BY CASE day_type
                   WHEN 'будни' THEN 0 WHEN 'workday' THEN 1
                   WHEN 'выходные' THEN 2 WHEN 'weekend' THEN 3
                   WHEN 'суббота' THEN 4 WHEN 'воскресенье' THEN 5
                   ELSE 6
                 END,
                 output_number,shift_number,trip_number,dep_time,id
        """,
        (route_id,),
    ).fetchall()
    grouped = {key: {} for key, _ in _DAY_TYPES}
    aliases = {
        database_value: key
        for key, database_values in _DAY_TYPES
        for database_value in database_values
    }
    for row in rows:
        item = dict(row)
        key = aliases[item["day_type"]]
        output_number = int(item["output_number"] or 0)
        trip = ScheduleTrip(
            output_number=output_number,
            shift_number=int(item["shift_number"] or 0),
            trip_number=(
                int(item["trip_number"]) if item["trip_number"] is not None else None
            ),
            direction=str(item["direction"] or ""),
            departure_sec=_clock_seconds(item["dep_time"]),
            arrival_sec=_clock_seconds(item["arr_time"]),
            distance_km=float(item["distance_km"] or 0),
            break_after_sec=int(item["break_after_min"] or 0) * 60,
            break_type=str(item["break_type"] or ""),
        )
        output_key = (item["day_type"], output_number)
        output = grouped[key].setdefault(
            output_key, {"day_type": item["day_type"], "trips": []}
        )
        output["trips"].append(trip)
    return {
        key: tuple(
            ScheduleOutput(
                day_type=values["day_type"],
                output_number=output_number,
                trips=tuple(values["trips"]),
            )
            for (_, output_number), values in outputs.items()
        )
        for key, outputs in grouped.items()
    }


def load_route_document_data(con, route_id):
    """Load one route in a bounded set of deterministic SQL queries."""
    route = con.execute(
        """
        SELECT id,number,name,start_point,end_point,version
        FROM routes WHERE id=?
        """,
        (route_id,),
    ).fetchone()
    if route is None:
        raise ValueError("Маршрут не найден")
    sections = _route_sections(con, route_id)
    return RouteDocumentData(
        route_id=int(route["id"]),
        route_number=str(route["number"] or ""),
        route_name=str(route["name"] or ""),
        start_point=str(route["start_point"] or ""),
        end_point=str(route["end_point"] or ""),
        version=int(route["version"] or 1),
        forward=sections["forward"],
        backward=sections["backward"],
        depot_out=sections["depot_out"],
        depot_in=sections["depot_in"],
        schedules=_route_schedules(con, route_id),
    )
