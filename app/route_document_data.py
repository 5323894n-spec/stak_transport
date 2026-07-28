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
    shift_number: int | None
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
    schedules: dict[str, dict[str, tuple[ScheduleOutput, ...]]]


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


def _business_sequence(row):
    """Stable route-planning sequence used only to select the service-day anchor."""
    trip_number = row["trip_number"]
    shift_number = row["shift_number"]
    return (
        trip_number is None,
        int(trip_number) if trip_number is not None else 0,
        shift_number is None,
        int(shift_number) if shift_number is not None else 0,
        int(row["id"]),
    )


def _chronological_rows(rows):
    """Order trips on a service-day axis without an arbitrary clock cutoff.

    The business anchor is the lowest non-null trip number, then shift/id. Times
    earlier than its departure belong to the following calendar day. Missing
    departures sort last. If the anchor has no departure, the first stable row
    that has one supplies the anchor.
    """
    business_rows = sorted(rows, key=_business_sequence)
    anchor_row = next(
        (row for row in business_rows if row["trip_number"] is not None),
        business_rows[0],
    )
    anchor_sec = _clock_seconds(anchor_row["dep_time"])
    if anchor_sec is None:
        anchor_sec = next(
            (
                departure
                for row in business_rows
                if (departure := _clock_seconds(row["dep_time"])) is not None
            ),
            None,
        )

    def chronology_key(row):
        departure = _clock_seconds(row["dep_time"])
        if departure is None:
            return (1, 0, *_business_sequence(row))
        normalized = (
            departure + 86400
            if anchor_sec is not None and departure < anchor_sec
            else departure
        )
        return (0, normalized, *_business_sequence(row))

    return sorted(rows, key=chronology_key)


def _schedule_trip(row):
    return ScheduleTrip(
        output_number=int(row["output_number"] or 0),
        shift_number=(
            int(row["shift_number"]) if row["shift_number"] is not None else None
        ),
        trip_number=(
            int(row["trip_number"]) if row["trip_number"] is not None else None
        ),
        direction=str(row["direction"] or ""),
        departure_sec=_clock_seconds(row["dep_time"]),
        arrival_sec=_clock_seconds(row["arr_time"]),
        distance_km=float(row["distance_km"] or 0),
        break_after_sec=int(row["break_after_min"] or 0) * 60,
        break_type=str(row["break_type"] or ""),
    )


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
                 output_number,id
        """,
        (route_id,),
    ).fetchall()
    aliases = {
        database_value: key
        for key, database_values in _DAY_TYPES
        for database_value in database_values
    }
    grouped = {key: {} for key, _ in _DAY_TYPES}
    for source_row in rows:
        row = dict(source_row)
        outer_key = aliases[row["day_type"]]
        variant_outputs = grouped[outer_key].setdefault(row["day_type"], {})
        variant_outputs.setdefault(int(row["output_number"] or 0), []).append(row)

    return {
        outer_key: {
            variant: tuple(
                ScheduleOutput(
                    day_type=variant,
                    output_number=output_number,
                    trips=tuple(
                        _schedule_trip(row)
                        for row in _chronological_rows(output_rows)
                    ),
                )
                for output_number, output_rows in sorted(outputs.items())
            )
            for variant, outputs in variants.items()
        }
        for outer_key, variants in grouped.items()
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
