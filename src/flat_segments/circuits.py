"""Circuits: short closed loops of the walking network (docs/algorithm.md section 16).

Two sources of candidates, both on the network of support ways split at
every shared node (``network.split_ways``), with dead ends removed (2-core):

* **laps**: for each park, garden or water area, the outer boundary of the
  car-free paths inside it (the tour of a park or of a lake);
* **faces**: the cells of the network drawn on the map. Only those around
  water and the car-free cells of a neighbourhood are kept; the cells inside
  a park or a forest are a maze of paths, not a circuit.

Candidates must be 200 m to 2 km long, compact, mostly car-free and must not
touch a MAJOR road. They are then kept if flat (:func:`max_local_grade`, on
the DEM) and thinned to one circuit per spot and size class
(:func:`select_circuits`).

This module is pure: ways, areas and the DEM sampler come from the caller.
Thresholds are constants, as for tracks (``loops.py``), so that adding
circuits does not change the parameters of the segments already produced.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Final

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from flat_segments.elevation import DemSampler, sample_stroke
from flat_segments.geometry import FloatArray, polyline_length
from flat_segments.loops import Loop, loop_id
from flat_segments.network import Edge, RoadClass, Way, split_ways
from flat_segments.osm import lit_category, surface_category
from flat_segments.params import ProfileParams
from flat_segments.profile import Structure, build_profile

#: Length range of a circuit, in metres.
MIN_LENGTH_M: Final = 200.0
MAX_LENGTH_M: Final = 2000.0
#: Minimum compactness (``4 pi area / perimeter^2``) of a face.
MIN_COMPACTNESS: Final = 0.3
#: Minimum compactness of a lap: a park along a river is long and thin.
MIN_LAP_COMPACTNESS: Final = 0.15
#: Minimum share of the length on paths (sidewalks excluded).
MIN_CAR_FREE: Final = 0.6
#: A neighbourhood face is kept with this car-free share, at most this many
#: crossings, and this minimum length and compactness.
NEIGHBOURHOOD_CAR_FREE: Final = 0.8
NEIGHBOURHOOD_MAX_CROSSINGS: Final = 1
NEIGHBOURHOOD_MIN_LENGTH_M: Final = 400.0
NEIGHBOURHOOD_MIN_COMPACTNESS: Final = 0.4
#: Areas giving laps: surface range, in square metres.
MIN_LAP_AREA_M2: Final = 3000.0
MAX_LAP_AREA_M2: Final = 2_000_000.0
#: Paths within this margin of a park (or water) count as inside it.
LAP_MARGIN_M: Final = 20.0
WATER_LAP_MARGIN_M: Final = 60.0
#: A lap must enclose this share of its park (or water) area.
MIN_LAP_COVER: Final = 0.3
MIN_WATER_LAP_COVER: Final = 0.5
#: Setting of a circuit, from the share of its inside covered by water, or by
#: parks and green spaces; a face mostly on tracks is in the countryside.
WATER_SHARE: Final = 0.15
PARK_SHARE: Final = 0.4
COUNTRYSIDE_TRACK_SHARE: Final = 0.5
#: An area only names a circuit if it covers this share of its inside.
NAME_SHARE: Final = 0.2
#: Two circuits of the same size class closer than this are the same spot.
SPACING_M: Final = 100.0
#: Upper bounds of the size classes (the last one is open).
SIZE_CLASSES_M: Final = (500.0, 1000.0)
#: Length over which the direction of a path leaving a node is measured.
BEARING_PROBE_M: Final = 3.0

#: ``footway`` values of paths along a road, which are not car-free.
ROADSIDE_FOOTWAYS: Final = frozenset({"sidewalk", "crossing"})
WATER_NATURAL: Final = frozenset({"water", "wetland"})
WATER_LANDUSE: Final = frozenset({"reservoir", "basin"})
PARK_LEISURE: Final = frozenset(
    {"park", "garden", "nature_reserve", "common", "recreation_ground", "golf_course"}
)
GREEN_LANDUSE: Final = frozenset(
    {"grass", "recreation_ground", "forest", "meadow", "village_green", "cemetery"}
)
GREEN_NATURAL: Final = frozenset({"wood", "scrub", "grassland"})

#: Settings, best first (the order breaks ties when thinning).
SETTINGS: Final = ("water", "park", "neighbourhood")


def setting_kind(tags: Mapping[str, str]) -> str | None:
    """``water``, ``park`` or ``green`` for an area that gives a setting, else ``None``."""
    if tags.get("natural") in WATER_NATURAL or tags.get("landuse") in WATER_LANDUSE:
        return "water"
    if tags.get("leisure") in PARK_LEISURE:
        return "park"
    if tags.get("landuse") in GREEN_LANDUSE or tags.get("natural") in GREEN_NATURAL:
        return "green"
    return None


@dataclass(frozen=True, slots=True, eq=False)
class SettingArea:
    """A park, green space or water area in Lambert-93.

    Attributes:
        osm_id: ``"way/123"`` or ``"relation/45"``.
        kind: ``water``, ``park`` or ``green`` (:func:`setting_kind`).
        polygon: Valid polygon.
        name: Raw ``name`` tag.
    """

    osm_id: str
    kind: str
    polygon: BaseGeometry
    name: str | None = None


@dataclass(frozen=True, slots=True, eq=False)
class Candidate:
    """A circuit before the flatness check and the thinning.

    Attributes:
        coords: ``(N, 2)`` closed ring in Lambert-93.
        edges: Edge indices along the ring (identity of the circuit).
        length_m: Length of the ring.
        compactness: ``4 pi area / length^2``.
        car_free: Share of the length on paths, sidewalks excluded.
        n_crossings: Ring nodes where a MINOR road not on the ring joins it.
        track_share: Share of the length on ``highway=track``.
        source: ``lap`` (outline of a park or lake) or ``face`` (network cell).
        structures: Bridges and tunnels along the ring.
        ways: OSM ways followed, with the length on each.
        polygon: Inside of the ring.
        setting: ``water``, ``park``, ``neighbourhood`` or ``countryside``.
        name: Name of the park or lake.
        area_id: OSM area a lap goes round.
    """

    coords: FloatArray
    edges: frozenset[int]
    length_m: float
    compactness: float
    car_free: float
    n_crossings: int
    track_share: float
    source: str
    structures: tuple[Structure, ...]
    ways: tuple[tuple[Way, float], ...]
    polygon: Polygon
    setting: str = "neighbourhood"
    name: str | None = None
    area_id: str | None = None

    @property
    def size_class(self) -> int:
        """0, 1 or 2 by length (:data:`SIZE_CLASSES_M`)."""
        return sum(self.length_m >= bound for bound in SIZE_CLASSES_M)


# --- graph ------------------------------------------------------------------


@dataclass
class _Graph:
    """Support edges with their half-edges (``2 e``: start to end, ``2 e + 1``: back)."""

    edges: list[Edge]
    barrier_nodes: set[int]
    angles: dict[int, float] = field(default_factory=dict)
    incident: defaultdict[int, list[int]] = field(default_factory=lambda: defaultdict(list))

    def __post_init__(self) -> None:
        for index, edge in enumerate(self.edges):
            self.incident[edge.start_node].append(index)
            self.incident[edge.end_node].append(index)

    def tail(self, half: int) -> int:
        edge = self.edges[half >> 1]
        return edge.end_node if half & 1 else edge.start_node

    def head(self, half: int) -> int:
        edge = self.edges[half >> 1]
        return edge.start_node if half & 1 else edge.end_node

    def coords(self, half: int) -> FloatArray:
        coords = self.edges[half >> 1].coords
        return coords[::-1] if half & 1 else coords

    def angle(self, half: int) -> float:
        """Direction of a half-edge leaving its tail, over :data:`BEARING_PROBE_M`."""
        if half not in self.angles:
            coords = self.coords(half)
            delta = coords[1] - coords[0]
            k = 1
            while math.hypot(*delta) < BEARING_PROBE_M and k + 1 < len(coords):
                k += 1
                delta = coords[k] - coords[0]
            self.angles[half] = math.atan2(delta[1], delta[0])
        return self.angles[half]

    def ring(self, halves: Sequence[int]) -> FloatArray:
        pieces = [self.coords(h) for h in halves]
        return np.vstack([pieces[0][:1], *(p[1:] for p in pieces)])


def two_core(graph: _Graph, edge_ids: Sequence[int]) -> set[int]:
    """Edges left once dead ends are removed, repeatedly.

    A closed way alone (an edge from a node to itself) counts twice at its
    node and is kept.
    """
    degree: Counter[int] = Counter()
    incident: defaultdict[int, set[int]] = defaultdict(set)
    for e in edge_ids:
        edge = graph.edges[e]
        degree[edge.start_node] += 1
        degree[edge.end_node] += 1
        incident[edge.start_node].add(e)
        incident[edge.end_node].add(e)
    live = set(edge_ids)
    stack = [node for node, d in degree.items() if d == 1]
    while stack:
        node = stack.pop()
        if degree[node] != 1:
            continue
        (e,) = incident[node]
        edge = graph.edges[e]
        live.discard(e)
        other = edge.end_node if edge.start_node == node else edge.start_node
        for end in (node, other):
            incident[end].discard(e)
            degree[end] -= 1
        if degree[other] == 1:
            stack.append(other)
    return live


def _boundary_walks(graph: _Graph, edge_ids: set[int]) -> list[list[int]]:
    """Faces of a set of edges, as closed walks of half-edges.

    Half-edges leaving each node are sorted by angle; a walk turns to the
    next one clockwise at each node, so that bounded faces are walked
    counter-clockwise and the outer face of each component clockwise.
    """
    leaving: defaultdict[int, list[int]] = defaultdict(list)
    for e in sorted(edge_ids):
        for half in (2 * e, 2 * e + 1):
            leaving[graph.tail(half)].append(half)
    position: dict[int, int] = {}
    for halves in leaving.values():
        halves.sort(key=graph.angle)
        for i, half in enumerate(halves):
            position[half] = i
    seen: set[int] = set()
    walks: list[list[int]] = []
    for e in sorted(edge_ids):
        for start in (2 * e, 2 * e + 1):
            if start in seen:
                continue
            walk: list[int] = []
            half = start
            while half not in seen:
                seen.add(half)
                walk.append(half)
                around = leaving[graph.head(half)]
                half = around[(position[half ^ 1] - 1) % len(around)]
            walks.append(walk)
    return walks


def _signed_area(ring: FloatArray) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1]))


def simple_cycles(graph: _Graph, walk: Sequence[int]) -> list[list[int]]:
    """Split a closed walk at repeated nodes into simple cycles.

    Out-and-backs (a path entered and left by the same edge) come out as
    cycles that repeat an edge, which :func:`_describe` drops.
    """
    parts: list[list[int]] = []
    stack: list[int] = []
    index: dict[int, int] = {}
    for half in walk:
        node = graph.tail(half)
        if node in index:
            i = index[node]
            part = stack[i:]
            del stack[i:]
            for q in part:
                index.pop(graph.tail(q), None)
            if part:
                parts.append(part)
        index[node] = len(stack)
        stack.append(half)
    if stack:
        parts.append(stack)
    return parts


# --- candidates -------------------------------------------------------------


def _is_car_free(edge: Edge) -> bool:
    way = edge.way
    return way.road_class is RoadClass.PATH and way.footway not in ROADSIDE_FOOTWAYS


def _describe(
    graph: _Graph,
    halves: Sequence[int],
    source: str,
    outline: BaseGeometry | None,
    min_compactness: float,
) -> Candidate | None:
    """A candidate from a cycle of half-edges, or ``None`` if it fails a filter."""
    edge_ids = [h >> 1 for h in halves]
    if len(set(edge_ids)) != len(edge_ids):
        return None
    edges = [graph.edges[e] for e in edge_ids]
    length = sum(edge.length for edge in edges)
    if not MIN_LENGTH_M <= length <= MAX_LENGTH_M:
        return None
    nodes = [graph.tail(h) for h in halves]
    if any(node in graph.barrier_nodes for node in nodes):
        return None
    car_free = sum(edge.length for edge in edges if _is_car_free(edge)) / length
    if car_free < MIN_CAR_FREE:
        return None
    ring = graph.ring(halves)
    if len(ring) < 4:
        return None  # a way that goes out and back on itself
    polygon = Polygon(ring)
    if not polygon.is_valid or polygon.area <= 0:
        return None
    compact = 4.0 * math.pi * polygon.area / length**2
    if compact < min_compactness:
        return None
    if outline is not None and not outline.contains(polygon.representative_point()):
        return None
    on_ring = set(edge_ids)
    crossings = sum(
        any(
            graph.edges[o].way.road_class is RoadClass.MINOR
            for o in graph.incident[node]
            if o not in on_ring
        )
        for node in set(nodes)
    )
    structures: list[Structure] = []
    ways: dict[int, tuple[Way, float]] = {}
    offset = 0.0
    for edge in edges:
        if edge.way.structure:
            structures.append((offset, offset + edge.length, edge.way.structure))
        known = ways.get(edge.way.id)
        ways[edge.way.id] = (edge.way, (known[1] if known else 0.0) + edge.length)
        offset += edge.length
    track = sum(edge.length for edge in edges if edge.way.highway == "track")
    return Candidate(
        coords=ring,
        edges=frozenset(edge_ids),
        length_m=length,
        compactness=compact,
        car_free=car_free,
        n_crossings=crossings,
        track_share=track / length,
        source=source,
        structures=tuple(structures),
        ways=tuple(ways.values()),
        polygon=polygon,
    )


def _laps(
    graph: _Graph,
    core: set[int],
    areas: Sequence[SettingArea],
    outline: BaseGeometry | None,
) -> list[Candidate]:
    """Outlines of the car-free paths of each park, garden or water area."""
    paths = sorted(e for e in core if _is_car_free(graph.edges[e]))
    if not paths:
        return []
    middles = shapely.points(
        [graph.edges[e].coords[len(graph.edges[e].coords) // 2] for e in paths]
    )
    tree = shapely.STRtree(middles)
    laps: list[Candidate] = []
    for area in areas:
        if not MIN_LAP_AREA_M2 <= area.polygon.area <= MAX_LAP_AREA_M2:
            continue
        water = area.kind == "water"
        grown = area.polygon.buffer(WATER_LAP_MARGIN_M if water else LAP_MARGIN_M)
        inside = [paths[i] for i in tree.query(grown, predicate="contains")]
        if not inside:
            continue
        kept = two_core(graph, inside)
        for walk in _boundary_walks(graph, kept):
            if _signed_area(graph.ring(walk)) >= 0:
                continue  # an inner face: laps go round the outside
            for cycle in simple_cycles(graph, walk):
                lap = _describe(graph, cycle, "lap", outline, MIN_LAP_COMPACTNESS)
                if lap is None:
                    continue
                cover = lap.polygon.intersection(area.polygon).area / area.polygon.area
                if cover < (MIN_WATER_LAP_COVER if water else MIN_LAP_COVER):
                    continue
                laps.append(replace(lap, name=area.name, area_id=area.osm_id))
    return laps


def _setting(
    candidate: Candidate, areas: Sequence[SettingArea], tree: shapely.STRtree
) -> Candidate:
    """Setting from the areas covering the inside of a circuit, and a name."""
    polygon = candidate.polygon
    share: Counter[str] = Counter()
    names: Counter[str] = Counter()
    for i in tree.query(polygon):
        area = areas[int(i)]
        covered = area.polygon.intersection(polygon).area
        share[area.kind] += covered
        if area.name and covered > NAME_SHARE * polygon.area:
            names[area.name] += covered
    if share["water"] >= WATER_SHARE * polygon.area:
        setting = "water"
    elif share["park"] + share["green"] >= PARK_SHARE * polygon.area:
        setting = "park"
    elif candidate.track_share >= COUNTRYSIDE_TRACK_SHARE:
        setting = "countryside"
    else:
        setting = "neighbourhood"
    name = candidate.name or (min(names, key=lambda n: (-names[n], n)) if names else None)
    return replace(candidate, setting=setting, name=name)


def _keep(candidate: Candidate) -> bool:
    """Laps, faces around water and car-free neighbourhood faces."""
    if candidate.source == "lap" or candidate.setting == "water":
        return True
    return (
        candidate.setting == "neighbourhood"
        and candidate.car_free >= NEIGHBOURHOOD_CAR_FREE
        and candidate.n_crossings <= NEIGHBOURHOOD_MAX_CROSSINGS
        and candidate.length_m >= NEIGHBOURHOOD_MIN_LENGTH_M
        and candidate.compactness >= NEIGHBOURHOOD_MIN_COMPACTNESS
    )


def find_candidates(
    ways: Sequence[Way],
    areas: Sequence[SettingArea],
    outline: BaseGeometry | None = None,
) -> list[Candidate]:
    """Circuits of a network, before the flatness check and the thinning.

    Args:
        ways: All retained ways, including MAJOR roads (barriers).
        areas: Parks, green spaces and water (:func:`setting_kind`).
        outline: Lambert-93 polygon; circuits must have their inside point in
            it (``None``: no check).

    Returns:
        Candidates, one per distinct ring (a lap wins over the same face).
    """
    barrier_nodes = {n for w in ways if w.road_class is RoadClass.MAJOR for n in w.node_ids}
    support = [w for w in ways if w.road_class is not RoadClass.MAJOR]
    graph = _Graph(split_ways(support, barrier_nodes), barrier_nodes)
    core = two_core(graph, range(len(graph.edges)))
    if outline is not None:
        shapely.prepare(outline)
    found: dict[frozenset[int], Candidate] = {}
    for walk in _boundary_walks(graph, core):
        face = _describe(graph, walk, "face", outline, MIN_COMPACTNESS)
        if face is not None:
            found.setdefault(face.edges, face)
    for lap in _laps(graph, core, areas, outline):
        found[lap.edges] = lap
    tree = shapely.STRtree([area.polygon for area in areas])
    described = (_setting(c, areas, tree) for c in found.values())
    return [c for c in described if _keep(c)]


# --- flatness and selection -------------------------------------------------


def max_local_grade(candidate: Candidate, dem: DemSampler, params: ProfileParams) -> float | None:
    """Largest absolute local grade along a circuit, or ``None`` without elevation.

    The ring is profiled over two laps, and the grade read on the middle
    lap, so that smoothing and gap filling see the ring as a loop.
    """
    ring = candidate.coords
    double = np.vstack([ring, ring[1:]])
    lap = polyline_length(ring)
    structures = [
        (a + k * lap, b + k * lap, kind) for k in (0, 1) for a, b, kind in candidate.structures
    ]
    profile = build_profile(double, sample_stroke(double, dem, params), params, structures)
    middle = (profile.distances >= lap / 2) & (profile.distances <= 1.5 * lap)
    z = profile.z[middle]
    if np.isnan(z).any():
        return None
    return float(np.abs(profile.grade_pct[middle]).max())


def _rank(candidate: Candidate) -> tuple[bool, int, int, float]:
    """Thinning order: no crossing, laps, water then park, longer first."""
    return (
        candidate.n_crossings > 0,
        candidate.source != "lap",
        SETTINGS.index(candidate.setting) if candidate.setting in SETTINGS else len(SETTINGS),
        -candidate.length_m,
    )


def select_circuits(
    candidates: Sequence[Candidate], grades: Sequence[float | None], max_grade_pct: float
) -> list[Loop]:
    """Flat circuits, one per spot and size class.

    Args:
        candidates: From :func:`find_candidates`.
        grades: :func:`max_local_grade` of each candidate.
        max_grade_pct: Largest local grade allowed.

    Returns:
        Loops sorted by id. Among circuits of a size class closer than
        :data:`SPACING_M`, the best by :func:`_rank` is kept; nested circuits
        of other sizes are kept.
    """
    flat = [
        (c, g)
        for c, g in zip(candidates, grades, strict=True)
        if g is not None and g <= max_grade_pct
    ]
    flat.sort(key=lambda item: (_rank(item[0]), loop_id(item[0].coords)))
    kept: list[tuple[Candidate, float]] = []
    by_class: defaultdict[int, list[BaseGeometry]] = defaultdict(list)
    for candidate, grade in flat:
        near = candidate.polygon.buffer(SPACING_M)
        polygons = by_class[candidate.size_class]
        if any(near.intersects(p) for p in polygons):
            continue
        polygons.append(candidate.polygon)
        kept.append((candidate, grade))
    return sorted((to_loop(c, g) for c, g in kept), key=lambda loop: loop.id)


def _by_length(values: Sequence[tuple[str, float]]) -> str:
    """Value covering the most length (ties: alphabetical)."""
    totals: defaultdict[str, float] = defaultdict(float)
    for value, length in values:
        totals[value] += length
    return min(totals, key=lambda v: (-totals[v], v))


def to_loop(candidate: Candidate, grade_max_pct: float) -> Loop:
    """The published loop of a circuit."""
    ways = candidate.ways
    longest = max(ways, key=lambda item: (item[1], -item[0].id))[0]
    return Loop(
        id=loop_id(candidate.coords),
        loop_type="circuit",
        coords=candidate.coords,
        length_m=candidate.length_m,
        lap_m=None,
        name=candidate.name,
        surface=_by_length(
            [(surface_category(w.surface, w.tracktype, w.road_class), n) for w, n in ways]
        ),
        lit=_by_length([(lit_category(w.lit), n) for w, n in ways]),
        access="public",
        opening_hours=None,
        indoor=False,
        osm_id=candidate.area_id or f"way/{longest.id}",
        setting=candidate.setting,
        n_crossings=candidate.n_crossings,
        grade_max_pct=grade_max_pct,
    )
