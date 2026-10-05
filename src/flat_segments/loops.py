"""Loops: closed circuits to run laps on (docs/algorithm.md section 15).

Phase 1 only knows running tracks mapped in OpenStreetMap (``leisure=track``).
A track is kept whole: it is flat by construction, so no elevation is sampled.
This module is pure: it works on :class:`SportArea` values already projected
to Lambert-93 (``osm.iter_sport_areas`` reads them from a file).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np

from flat_segments.geometry import FloatArray, polyline_length
from flat_segments.osm import lit_category, surface_category

#: Kind of every loop (alongside ``flat`` and ``climb`` segments).
LOOP_KIND: Final = "loop"

#: Standard lap lengths of running tracks, in metres.
STANDARD_LAPS_M: Final = (200.0, 250.0, 300.0, 1000.0 / 3.0, 400.0)
#: Largest relative gap between the measured perimeter and a standard lap.
#: An area outline follows the outer edge of the lanes: about 460 m for a
#: 400 m track with 8 lanes, hence the generous value.
LAP_TOLERANCE: Final = 0.2
#: Perimeters outside this range are not running tracks (or are mapping errors).
MIN_LENGTH_M: Final = 100.0
MAX_LENGTH_M: Final = 5000.0
#: Two tracks whose centroids are closer than this are the same track mapped
#: twice (an area and its running line, or one way per lane).
DUPLICATE_DISTANCE_M: Final = 25.0
#: Grid of the stable id (same as segments, docs/algorithm.md section 12).
ID_GRID_M: Final = 10.0

RUNNING_SPORTS: Final = frozenset({"athletics", "running"})
#: ``leisure`` values of the facilities whose access a track inherits.
FACILITY_LEISURE: Final = frozenset({"stadium", "sports_centre", "sports_hall"})
#: ``amenity`` values of the facilities whose access a track inherits.
FACILITY_AMENITY: Final = frozenset({"school", "college", "university"})
#: School grounds are closed to the public unless tagged otherwise.
RESTRICTED_AMENITY: Final = frozenset({"school", "college"})
PUBLIC_ACCESS: Final = frozenset({"yes", "permissive", "designated", "public"})
RESTRICTED_ACCESS: Final = frozenset({"no", "private", "customers", "members", "permit"})


@dataclass(frozen=True, slots=True, eq=False)
class SportArea:
    """An OSM area (closed way or multipolygon) in Lambert-93.

    Attributes:
        osm_id: ``"way/123"`` or ``"relation/45"``.
        ring: ``(N, 2)`` outer ring, closed (first point repeated last).
        tags: Raw OSM tags.
    """

    osm_id: str
    ring: FloatArray
    tags: Mapping[str, str]


@dataclass(frozen=True, slots=True, eq=False)
class Loop:
    """A closed circuit. Fields mirror docs/data-model.md (table ``loops``).

    Attributes:
        id: Stable id ``"loop-{12 hex}"``.
        loop_type: ``"track"`` (running track).
        coords: ``(N, 2)`` closed ring in Lambert-93.
        length_m: Measured length of the ring.
        lap_m: Standard lap length the ring matches, or ``None``.
        name: Name of the track, else of its facility.
        surface: Normalised surface (``paved``, ``unpaved``...).
        lit: ``yes``, ``no`` or ``unknown``.
        access: ``public``, ``restricted`` or ``unknown``.
        opening_hours: Raw ``opening_hours`` of the track or its facility.
        indoor: Covered track.
        osm_id: OSM object of the track.
    """

    id: str
    loop_type: str
    coords: FloatArray
    length_m: float
    lap_m: float | None
    name: str | None
    surface: str
    lit: str
    access: str
    opening_hours: str | None
    indoor: bool
    osm_id: str

    @property
    def kind(self) -> str:
        """Always :data:`LOOP_KIND`."""
        return LOOP_KIND


# --- tags -------------------------------------------------------------------


def _values(tags: Mapping[str, str], key: str) -> set[str]:
    """Values of a semicolon-separated tag."""
    return {v.strip() for v in tags.get(key, "").split(";") if v.strip()}


def is_running_track(tags: Mapping[str, str]) -> bool:
    """Whether an area is a running track.

    ``leisure=track`` with ``sport`` including athletics or running, or
    without ``sport`` but with a synthetic surface (tartan, rubber): horse,
    motor and cycle tracks are left out.
    """
    if tags.get("leisure") != "track":
        return False
    sports = _values(tags, "sport")
    if sports:
        return bool(sports & RUNNING_SPORTS)
    return tags.get("surface") in {"tartan", "rubber"}


def is_facility(tags: Mapping[str, str]) -> bool:
    """Whether an area may hold a track and give it its access rules."""
    return tags.get("leisure") in FACILITY_LEISURE or tags.get("amenity") in FACILITY_AMENITY


def access_category(tags: Mapping[str, str]) -> str:
    """``public``, ``restricted`` or ``unknown``, from ``foot`` then ``access``.

    A school without access tags is restricted.
    """
    for key in ("foot", "access"):
        value = tags.get(key)
        if value in PUBLIC_ACCESS:
            return "public"
        if value in RESTRICTED_ACCESS:
            return "restricted"
    if tags.get("amenity") in RESTRICTED_AMENITY:
        return "restricted"
    return "unknown"


def is_indoor(tags: Mapping[str, str], facility: Mapping[str, str]) -> bool:
    """Whether a track is covered: tagged so, or inside a sports hall."""
    return (
        tags.get("indoor") == "yes"
        or tags.get("covered") == "yes"
        or tags.get("building", "no") != "no"
        or facility.get("leisure") == "sports_hall"
    )


# --- geometry ---------------------------------------------------------------


def lap_length(length_m: float) -> float | None:
    """Standard lap closest to ``length_m``, if within :data:`LAP_TOLERANCE`."""
    best = min(STANDARD_LAPS_M, key=lambda lap: abs(length_m - lap) / lap)
    return best if abs(length_m - best) / best <= LAP_TOLERANCE else None


def ring_area(ring: FloatArray) -> float:
    """Unsigned area of a closed ring (shoelace formula)."""
    x, y = ring[:, 0], ring[:, 1]
    return float(abs(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1])) / 2.0)


def ring_centroid(ring: FloatArray) -> FloatArray:
    """Centroid of a closed ring (vertex mean for a degenerate one)."""
    x, y = ring[:, 0], ring[:, 1]
    cross = x[:-1] * y[1:] - x[1:] * y[:-1]
    twice_area = float(cross.sum())
    if abs(twice_area) < 1e-9:
        return np.asarray(ring[:-1].mean(axis=0), dtype=np.float64)
    cx = float(((x[:-1] + x[1:]) * cross).sum()) / (3.0 * twice_area)
    cy = float(((y[:-1] + y[1:]) * cross).sum()) / (3.0 * twice_area)
    return np.array([cx, cy], dtype=np.float64)


def point_in_ring(point: FloatArray, ring: FloatArray) -> bool:
    """Whether a point lies inside a closed ring (even-odd rule)."""
    px, py = float(point[0]), float(point[1])
    x0, y0 = ring[:-1, 0], ring[:-1, 1]
    x1, y1 = ring[1:, 0], ring[1:, 1]
    crosses = (y0 > py) != (y1 > py)
    with np.errstate(divide="ignore", invalid="ignore"):
        x_at = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
    return bool(np.count_nonzero(crosses & (px < x_at)) % 2)


def loop_id(ring: FloatArray, grid_m: float = ID_GRID_M) -> str:
    """Stable id ``"loop-{12 hex}"``: centroid snapped to a grid, and length.

    A loop has no natural start, so the two ends used by segment ids
    (``geometry.stable_id``) would coincide; the centroid does not depend on
    where the ring starts nor on its direction.
    """
    cx, cy = ring_centroid(ring)
    length = round(polyline_length(ring) / grid_m)
    key = f"{LOOP_KIND}|{round(cx / grid_m)}|{round(cy / grid_m)}|{length}"
    return f"{LOOP_KIND}-{hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:12]}"


# --- tracks -----------------------------------------------------------------


def _facility_of(
    centroid: FloatArray, facilities: Sequence[tuple[SportArea, FloatArray]]
) -> SportArea | None:
    """Smallest facility containing a point (``facilities`` hold their bbox)."""
    found: list[tuple[float, SportArea]] = []
    for facility, (min_x, min_y, max_x, max_y) in facilities:
        inside_bbox = min_x <= centroid[0] <= max_x and min_y <= centroid[1] <= max_y
        if inside_bbox and point_in_ring(centroid, facility.ring):
            found.append((ring_area(facility.ring), facility))
    return min(found, key=lambda item: item[0])[1] if found else None


def _track_loop(track: SportArea, facility: SportArea | None) -> Loop:
    tags = track.tags
    outer = facility.tags if facility is not None else {}
    length = polyline_length(track.ring)
    access = access_category(tags)
    if access == "unknown":
        access = access_category(outer)
    return Loop(
        id=loop_id(track.ring),
        loop_type="track",
        coords=track.ring,
        length_m=length,
        lap_m=lap_length(length),
        name=tags.get("name") or outer.get("name"),
        surface=surface_category(tags.get("surface")),
        lit=lit_category(tags.get("lit") or outer.get("lit")),
        access=access,
        opening_hours=tags.get("opening_hours") or outer.get("opening_hours"),
        indoor=is_indoor(tags, outer),
        osm_id=track.osm_id,
    )


def _preference(loop: Loop) -> tuple[int, float]:
    """Sort key among duplicates: a standard lap first, then the closest to it."""
    if loop.lap_m is None:
        return (1, 0.0)
    return (0, abs(loop.length_m - loop.lap_m))


def build_tracks(areas: Sequence[SportArea]) -> list[Loop]:
    """Running tracks among OSM areas, one loop per track.

    Args:
        areas: Tracks and the facilities around them (other areas are ignored).

    Returns:
        Loops sorted by id. A track mapped several times (area and running
        line, one way per lane) gives one loop: the one closest to a standard
        lap.
    """
    facilities = [
        (area, np.concatenate([area.ring.min(axis=0), area.ring.max(axis=0)]))
        for area in areas
        if is_facility(area.tags)
    ]
    candidates = []
    for area in areas:
        if not is_running_track(area.tags):
            continue
        length = polyline_length(area.ring)
        if not MIN_LENGTH_M <= length <= MAX_LENGTH_M:
            continue
        centroid = ring_centroid(area.ring)
        candidates.append((_track_loop(area, _facility_of(centroid, facilities)), centroid))
    kept: list[tuple[Loop, FloatArray]] = []
    for loop, centroid in sorted(candidates, key=lambda c: (_preference(c[0]), c[0].osm_id)):
        if all(np.hypot(*(centroid - c)) >= DUPLICATE_DISTANCE_M for _, c in kept):
            kept.append((loop, centroid))
    return sorted((loop for loop, _ in kept), key=lambda loop: loop.id)
