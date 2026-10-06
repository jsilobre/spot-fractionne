"""Generate the fictitious sample dataset of the web page.

Builds a synthetic network and terrain around Labège, runs the real detection
pipeline on it, adds two running tracks and the flat circuits of the network,
and writes the tileset of ``web/data/sample/`` (flagged as sample data; needs
tippecanoe, ADR 0009). Nothing here comes from OSM or IGN: the segments are
fake and must not be used to go running.

Usage: ``uv run python scripts/make_sample_data.py``
"""

from __future__ import annotations

import itertools
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from flat_segments.circuits import SettingArea, find_candidates, max_local_grade, select_circuits
from flat_segments.detect import SegmentKind, detect_all
from flat_segments.elevation import FunctionDem, sample_stroke
from flat_segments.geometry import FloatArray
from flat_segments.loops import SportArea, build_tracks
from flat_segments.network import RoadClass, Way, build_strokes
from flat_segments.params import PipelineParams
from flat_segments.tiles import write_tileset

#: Local origin in Lambert-93 (Labège).
ORIGIN = np.array([581_376.0, 6_271_316.0])
OUTPUT = Path(__file__).resolve().parents[1] / "web" / "data" / "sample"
GENERATED_AT = datetime(2026, 9, 30, tzinfo=UTC)
ATTRIBUTION = ("Données fictives générées par scripts/make_sample_data.py",)

_node_ids: dict[tuple[float, float], int] = {}
_next_node = itertools.count(1)
_next_way = itertools.count(1)


def terrain(x: FloatArray, y: FloatArray) -> FloatArray:
    """Synthetic terrain: a hill to the north, a steep bank to the south, a river."""
    u, v = x - ORIGIN[0], y - ORIGIN[1]
    z = 155.0 + 0.002 * u
    z = z + 0.06 * np.clip(v - 250.0, 0.0, 400.0) + 0.015 * np.clip(v - 650.0, 0.0, None)
    z = z + 0.09 * np.clip(-v - 350.0, 0.0, 200.0)
    z = z - 7.0 * np.exp(-(((u - 600.0) / 12.0) ** 2))
    return np.asarray(z, dtype=np.float64)


def _node(point: tuple[float, float]) -> int:
    key = (round(point[0], 3), round(point[1], 3))
    if key not in _node_ids:
        _node_ids[key] = next(_next_node)
    return _node_ids[key]


def way(
    points: list[tuple[float, float]],
    road_class: RoadClass,
    highway: str,
    **attrs: str,
) -> Way:
    """A way through local ``(u, v)`` points; equal points share a node."""
    coords = np.array(points, dtype=np.float64) + ORIGIN
    return Way(
        id=next(_next_way),
        node_ids=tuple(_node(p) for p in points),
        coords=coords,
        road_class=road_class,
        highway=highway,
        **attrs,
    )


def line(
    start: tuple[float, float], end: tuple[float, float], step: float = 100.0
) -> list[tuple[float, float]]:
    """Points every ``step`` metres from ``start`` to ``end`` (both included)."""
    n = max(1, math.ceil(math.dist(start, end) / step))
    return [
        (start[0] + (end[0] - start[0]) * k / n, start[1] + (end[1] - start[1]) * k / n)
        for k in range(n + 1)
    ]


def network() -> list[Way]:
    """The synthetic network (local coordinates in metres)."""
    path, minor, major = RoadClass.PATH, RoadClass.MINOR, RoadClass.MAJOR
    greenway = {"surface": "asphalt", "lit": "yes", "name": "Voie verte (fictive)"}
    return [
        # Greenway west-east, cut by a tertiary road, bridge over the river at u = 600.
        way(line((-1000, 0), (-700, 0)), path, "cycleway", **greenway),
        way(
            sorted({*line((-700, 0), (575, 0)), (-400, 0), (-200, 0), (200, 0)}),
            path,
            "cycleway",
            **greenway,
        ),
        way([(575, 0), (625, 0)], path, "cycleway", structure="bridge", **greenway),
        way(sorted({*line((625, 0), (1500, 0)), (1000, 0)}), path, "cycleway", **greenway),
        way(line((-700, -300), (-700, 300)), major, "tertiary"),
        # Residential streets crossing the greenway.
        way(
            sorted({*line((-400, -250), (-400, 200)), (-400, 0)}, key=lambda p: p[1]),
            minor,
            "residential",
        ),
        way(
            sorted(
                {*line((1000, -250), (1000, 200)), (1000, -150), (1000, -144), (1000, 0)},
                key=lambda p: p[1],
            ),
            minor,
            "residential",
            name="Rue des Exemples",
        ),
        # A street with a separately mapped sidewalk (deduplicated).
        way(sorted({*line((700, -150), (1500, -150)), (1000, -150)}), minor, "residential"),
        way(
            sorted({*line((700, -144), (1500, -144)), (1000, -144)}),
            path,
            "footway",
            surface="asphalt",
            lit="yes",
        ),
        # Hill track to the north (6 % between v = 250 and 650).
        way(line((200, 0), (200, 900), 50), path, "track", tracktype="grade2"),
        # Steep footpath to the south (9 % between v = -350 and -550).
        way(line((-200, 0), (-200, -700), 50), path, "footway", surface="ground"),
        # Winding path: too sinuous for intervals.
        way([(1600 + 30 * k, -100 + (30 if k % 2 else 0)) for k in range(28)], path, "path"),
        # Pond loop (a lap round the water).
        way(
            [
                (-1000 + 70 * math.cos(a), 100 + 70 * math.sin(a))
                for a in np.linspace(0, 2 * math.pi, 37)[:-1]
            ]
            + [(-930.0, 100.0)],
            path,
            "footway",
            surface="compacted",
        ),
        # Park loop.
        way(
            [
                (-950 + 120 * math.cos(a), -200 + 120 * math.sin(a))
                for a in np.linspace(0, 2 * math.pi, 49)[:-1]
            ]
            + [(-830.0, -200.0)],
            path,
            "footway",
            surface="compacted",
            name="Boucle du parc (fictive)",
        ),
    ]


def stadium(center: tuple[float, float], straight: float, radius: float) -> FloatArray:
    """Closed outline of a running track (two straights, two bends), Lambert-93."""
    cx, cy = center
    points = []
    for x0, first in ((straight / 2, -90.0), (-straight / 2, 90.0)):
        angles = np.radians(np.linspace(first, first + 180.0, 25))
        points += [(cx + x0 + radius * math.cos(a), cy + radius * math.sin(a)) for a in angles]
    ring = np.array(points, dtype=np.float64) + ORIGIN
    return np.vstack([ring, ring[:1]])


def tracks() -> list[SportArea]:
    """Two running tracks and their facilities (local coordinates in metres)."""
    athletics = {"leisure": "track", "sport": "athletics", "surface": "tartan"}
    return [
        # A 400 m track mapped as a ring-shaped area (lanes between both rings).
        SportArea(
            "way/1",
            stadium((-300, -450), 84.39, 46.0),
            athletics,
            stadium((-300, -450), 84.39, 36.8),
        ),
        SportArea(
            "way/2",
            stadium((-300, -450), 220.0, 110.0),
            {"leisure": "stadium", "name": "Stade (fictif)", "access": "yes", "lit": "yes"},
        ),
        # A 250 m school track.
        SportArea("way/3", stadium((1250, 300), 50.0, 23.8), athletics),
        SportArea(
            "way/4",
            stadium((1250, 300), 120.0, 70.0),
            {"amenity": "school", "name": "Collège (fictif)"},
        ),
    ]


def settings() -> list[SettingArea]:
    """A park and a pond (local coordinates in metres)."""

    def disc(u: float, v: float, radius: float) -> BaseGeometry:
        return Point(ORIGIN[0] + u, ORIGIN[1] + v).buffer(radius)

    return [
        SettingArea("way/5", "park", disc(-950, -200, 125), "Parc (fictif)"),
        SettingArea("way/6", "water", disc(-1000, 100, 50), "Étang (fictif)"),
    ]


def main() -> None:
    """Run the pipeline on the synthetic network and write the sample tileset."""
    params = PipelineParams()
    ways = network()
    strokes = build_strokes(ways, params.network)
    dem = FunctionDem(terrain)
    z_raw = {s.id: sample_stroke(s.coords, dem, params.profile) for s in strokes}
    segments = detect_all(strokes, z_raw, params, elevation_source="synthetic")
    loops = build_tracks(tracks())
    candidates = find_candidates(ways, settings())
    grades = [max_local_grade(c, dem, params.profile) for c in candidates]
    circuits = select_circuits(candidates, grades, params.detection.flat.max_local_grade_pct)
    write_tileset(
        [*segments, *loops, *circuits],
        OUTPUT,
        sample=True,
        generated_at=GENERATED_AT,
        attribution=ATTRIBUTION,
    )
    n_flat = sum(s.kind is SegmentKind.FLAT for s in segments)
    print(
        f"{len(strokes)} strokes -> {n_flat} flat segments, {len(segments) - n_flat} climbs, "
        f"{len(loops)} tracks, {len(circuits)} circuits"
    )
    print(f"written to {OUTPUT}")


if __name__ == "__main__":
    main()
