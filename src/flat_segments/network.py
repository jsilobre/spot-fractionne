"""Network graph and chaining of OSM ways into continuous strokes.

Ways are split into edges at every shared node, then edges are paired at
each node by "good continuation" (smallest deflection) to form strokes.
Strokes stop at dead ends, where no straight continuation exists, and at any
node shared with a MAJOR road. Junctions passed through are recorded as
events along the stroke. See docs/algorithm.md section 2.
"""

from __future__ import annotations

import itertools
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import numpy as np

from flat_segments.geometry import (
    FloatArray,
    bearing_deg,
    dedupe_vertices,
    deflection_deg,
    interpolate_at,
    polyline_length,
)
from flat_segments.params import NetworkParams

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry


class RoadClass(StrEnum):
    """Role of an OSM way in the network (docs/algorithm.md section 1)."""

    MAJOR = "major"
    """Busy road: never a segment support, cuts strokes."""
    MINOR = "minor"
    """Low-traffic road: support; intersecting it counts as a crossing."""
    PATH = "path"
    """Footway, cycleway, track…: support; intersections are junctions."""


class EventKind(StrEnum):
    """Kind of junction passed through by a stroke."""

    CROSSING = "crossing"
    """Intersection with a MINOR road."""
    JUNCTION = "junction"
    """Intersection with paths only."""


@dataclass(frozen=True, slots=True, eq=False)
class Way:
    """An OSM way retained for the network, in Lambert-93.

    Attributes:
        id: OSM way id.
        node_ids: OSM node ids, one per vertex.
        coords: ``(N, 2)`` vertex coordinates.
        road_class: Role in the network.
        highway: Raw ``highway`` tag.
        surface: Raw ``surface`` tag.
        tracktype: Raw ``tracktype`` tag.
        lit: Raw ``lit`` tag.
        structure: ``"bridge"``, ``"tunnel"`` or ``None``.
        name: Raw ``name`` tag.
        footway: Raw ``footway`` tag (``sidewalk``: along a road).
    """

    id: int
    node_ids: tuple[int, ...]
    coords: FloatArray
    road_class: RoadClass
    highway: str
    surface: str | None = None
    tracktype: str | None = None
    lit: str | None = None
    structure: str | None = None
    name: str | None = None
    footway: str | None = None


@dataclass(frozen=True, slots=True)
class StrokePart:
    """The stretch of a stroke that follows one OSM way."""

    way_id: int
    start_m: float
    end_m: float
    highway: str
    road_class: RoadClass
    surface: str | None = None
    tracktype: str | None = None
    lit: str | None = None
    structure: str | None = None
    name: str | None = None

    @property
    def length_m(self) -> float:
        """Length of the part."""
        return self.end_m - self.start_m


@dataclass(frozen=True, slots=True)
class StrokeEvent:
    """A junction passed through by a stroke."""

    offset_m: float
    kind: EventKind
    node_id: int


@dataclass(frozen=True, slots=True, eq=False)
class Stroke:
    """A continuous polyline of the network.

    Attributes:
        id: Run-local identifier (``"s000001"``...).
        coords: ``(N, 2)`` polyline in Lambert-93.
        parts: OSM ways followed, in order, with their extent along the stroke.
        events: Junctions passed through, ordered by ``offset_m``.
        is_ring: Whether the stroke closes on itself.
    """

    id: str
    coords: FloatArray
    parts: tuple[StrokePart, ...]
    events: tuple[StrokeEvent, ...] = ()
    is_ring: bool = False

    @property
    def length_m(self) -> float:
        """Length of the stroke."""
        return polyline_length(self.coords)

    def structures(self) -> list[tuple[float, float, str]]:
        """Bridges and tunnels as ``(start_m, end_m, kind)`` intervals."""
        return [(p.start_m, p.end_m, p.structure) for p in self.parts if p.structure]


@dataclass(frozen=True, slots=True, eq=False)
class Edge:
    """A piece of way between two network nodes (shared, barrier or end)."""

    way: Way
    start_node: int
    end_node: int
    coords: FloatArray
    length: float


# An edge end: (edge index, 0 = start / 1 = end).
_End = tuple[int, int]


def drop_ways_inside(
    ways: Sequence[Way],
    areas: Sequence[BaseGeometry],
    max_inside: float,
    keep: Iterable[int] = (),
) -> list[Way]:
    """Remove the support ways lying mostly inside one of ``areas``.

    Used for the areas closed to the public, such as aerodromes
    (docs/algorithm.md section 1), whose service roads look like any other.

    Args:
        ways: Ways in Lambert-93.
        areas: Polygons in Lambert-93.
        max_inside: Largest share of a way's length (0 to 1) that may lie
            inside one area; a way above it is removed.
        keep: Ids of ways never removed (explicitly open to pedestrians).
            MAJOR roads are never removed either: they stay barriers.

    Returns:
        The other ways, in their input order.
    """
    import shapely

    kept_ids = set(keep)
    candidates = [
        i
        for i, way in enumerate(ways)
        if way.road_class is not RoadClass.MAJOR and way.id not in kept_ids
    ]
    if not areas or not candidates:
        return list(ways)
    lines = shapely.linestrings([ways[i].coords for i in candidates])
    tree = shapely.STRtree(list(areas))
    line_index, area_index = tree.query(lines, predicate="intersects")
    inside = shapely.intersection(lines[line_index], tree.geometries[area_index])
    share = shapely.length(inside) / np.maximum(shapely.length(lines[line_index]), 1e-9)
    dropped = {candidates[i] for i in line_index[share > max_inside]}
    return [way for i, way in enumerate(ways) if i not in dropped]


def split_ways(ways: Sequence[Way], barrier_nodes: set[int]) -> list[Edge]:
    """Split support ways at shared nodes, barrier nodes and self-intersections."""
    usage: Counter[int] = Counter()
    for way in ways:
        usage.update(way.node_ids)
    edges: list[Edge] = []
    for way in ways:
        ids = way.node_ids
        cuts = [0]
        cuts += [i for i in range(1, len(ids) - 1) if usage[ids[i]] >= 2 or ids[i] in barrier_nodes]
        cuts.append(len(ids) - 1)
        for a, b in itertools.pairwise(cuts):
            coords = dedupe_vertices(way.coords[a : b + 1])
            length = polyline_length(coords)
            if length > 0:
                edges.append(Edge(way, ids[a], ids[b], coords, length))
    return edges


def _end_bearing(edge: Edge, end: int, probe_m: float) -> float:
    """Bearing of an edge leaving the node at its ``end``."""
    coords = edge.coords if end == 0 else edge.coords[::-1]
    target = interpolate_at(coords, [min(probe_m, edge.length)])[0]
    return bearing_deg(coords[0], target)


def _pair_ends(
    edges: Sequence[Edge],
    incidence: dict[int, list[_End]],
    barrier_nodes: set[int],
    params: NetworkParams,
) -> dict[_End, _End]:
    """Pair edge ends at each node by smallest deflection."""
    partner: dict[_End, _End] = {}
    for node, ends in incidence.items():
        if node in barrier_nodes or len(ends) < 2:
            continue
        if len(ends) == 2:
            partner[ends[0]], partner[ends[1]] = ends[1], ends[0]
            continue
        bearings = [_end_bearing(edges[e], side, params.bearing_probe_m) for e, side in ends]
        candidates = sorted(
            (deflection_deg(bearings[i], bearings[j]), i, j)
            for i, j in itertools.combinations(range(len(ends)), 2)
        )
        used: set[int] = set()
        for deflection, i, j in candidates:
            if deflection > params.max_deflection_deg:
                break
            if i in used or j in used:
                continue
            partner[ends[i]], partner[ends[j]] = ends[j], ends[i]
            used.update((i, j))
    return partner


def _walk(
    start: int, partner: dict[_End, _End], visited: list[bool]
) -> tuple[list[tuple[int, bool]], bool]:
    """Follow pairings from edge ``start`` in both directions.

    Returns:
        The ordered ``(edge, forward)`` sequence and whether it closes a ring.
    """
    visited[start] = True
    sequence: deque[tuple[int, bool]] = deque([(start, True)])
    current: _End = (start, 1)
    while current in partner:
        edge, side = partner[current]
        if visited[edge]:
            return list(sequence), edge == start
        visited[edge] = True
        forward = side == 0
        sequence.append((edge, forward))
        current = (edge, 1 if forward else 0)
    current = (start, 0)
    while current in partner:
        edge, side = partner[current]
        if visited[edge]:
            break
        visited[edge] = True
        forward = side == 1
        sequence.appendleft((edge, forward))
        current = (edge, 0 if forward else 1)
    return list(sequence), False


def _part_for(way: Way, start: float, end: float) -> StrokePart:
    return StrokePart(
        way_id=way.id,
        start_m=start,
        end_m=end,
        highway=way.highway,
        road_class=way.road_class,
        surface=way.surface,
        tracktype=way.tracktype,
        lit=way.lit,
        structure=way.structure,
        name=way.name,
    )


def _assemble(
    stroke_id: str,
    sequence: list[tuple[int, bool]],
    is_ring: bool,
    edges: Sequence[Edge],
    incidence: dict[int, list[_End]],
) -> Stroke:
    """Concatenate edges into a stroke with its parts and events."""
    pieces: list[FloatArray] = []
    parts: list[StrokePart] = []
    events: list[StrokeEvent] = []
    offset = 0.0
    for k, (e, forward) in enumerate(sequence):
        edge = edges[e]
        coords = edge.coords if forward else edge.coords[::-1]
        pieces.append(coords if k == 0 else coords[1:])
        if parts and parts[-1].way_id == edge.way.id:
            last = parts.pop()
            parts.append(_part_for(edge.way, last.start_m, offset + edge.length))
        else:
            parts.append(_part_for(edge.way, offset, offset + edge.length))
        offset += edge.length
        if k == len(sequence) - 1:
            break
        node = edge.end_node if forward else edge.start_node
        ends = incidence[node]
        if len(ends) >= 3:
            next_e = sequence[k + 1][0]
            others = [edges[o] for o, _ in ends if o not in (e, next_e)]
            minor = any(o.way.road_class is RoadClass.MINOR for o in others)
            kind = EventKind.CROSSING if minor else EventKind.JUNCTION
            events.append(StrokeEvent(offset, kind, node))
    return Stroke(stroke_id, np.vstack(pieces), tuple(parts), tuple(events), is_ring)


def build_strokes(ways: Iterable[Way], params: NetworkParams | None = None) -> list[Stroke]:
    """Chain ways into strokes by good continuation.

    Args:
        ways: All retained ways, including MAJOR roads (used as barriers).
        params: Network parameters (defaults if omitted).

    Returns:
        Strokes in a deterministic order for a given input order.
    """
    params = params or NetworkParams()
    all_ways = list(ways)
    barrier_nodes = {n for w in all_ways if w.road_class is RoadClass.MAJOR for n in w.node_ids}
    support = [w for w in all_ways if w.road_class is not RoadClass.MAJOR]
    edges = split_ways(support, barrier_nodes)
    incidence: dict[int, list[_End]] = defaultdict(list)
    for index, edge in enumerate(edges):
        incidence[edge.start_node].append((index, 0))
        incidence[edge.end_node].append((index, 1))
    partner = _pair_ends(edges, incidence, barrier_nodes, params)
    visited = [False] * len(edges)
    strokes: list[Stroke] = []
    for index in range(len(edges)):
        if visited[index]:
            continue
        sequence, is_ring = _walk(index, partner, visited)
        strokes.append(_assemble(f"s{len(strokes) + 1:06d}", sequence, is_ring, edges, incidence))
    return strokes
