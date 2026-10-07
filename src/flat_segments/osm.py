"""OpenStreetMap input: tag interpretation and PBF reading.

The tag helpers are pure functions (docs/algorithm.md sections 1 and 9).
:func:`read_ways` streams a ``.osm.pbf`` (or ``.osm``) file with pyosmium and
returns :class:`~flat_segments.network.Way` objects projected to Lambert-93.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple

import numpy as np

from flat_segments.geometry import FloatArray, Projector, make_projector
from flat_segments.network import RoadClass, Way, drop_ways_inside
from flat_segments.params import WORK_CRS

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry

MAJOR_HIGHWAYS: Final = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
    }
)
MINOR_HIGHWAYS: Final = frozenset(
    {"residential", "unclassified", "living_street", "service", "road"}
)
PATH_HIGHWAYS: Final = frozenset(
    {"footway", "path", "cycleway", "track", "pedestrian", "bridleway"}
)
EXCLUDED_SERVICES: Final = frozenset(
    {"parking_aisle", "driveway", "drive-through", "emergency_access"}
)
NO_FOOT: Final = frozenset({"no", "private", "use_sidepath"})
NO_ACCESS: Final = frozenset({"private", "no", "agricultural", "forestry", "delivery", "military"})
FOOT_ALLOWED: Final = frozenset({"yes", "designated", "permissive"})

SURFACE_CATEGORIES: Final[Mapping[str, str]] = {
    **dict.fromkeys(
        (
            "asphalt",
            "paved",
            "concrete",
            "concrete:plates",
            "concrete:lanes",
            "paving_stones",
            "chipseal",
            "tartan",
            "rubber",
            "acrylic",
        ),
        "paved",
    ),
    **dict.fromkeys(("compacted", "fine_gravel"), "compacted"),
    **dict.fromkeys(("gravel", "pebblestone"), "gravel"),
    **dict.fromkeys(("sett", "cobblestone", "unhewn_cobblestone"), "cobbles"),
    **dict.fromkeys(
        (
            "unpaved",
            "ground",
            "dirt",
            "earth",
            "grass",
            "mud",
            "sand",
            "soil",
            "woodchips",
            "grass_paver",
        ),
        "unpaved",
    ),
}
TRACKTYPE_CATEGORIES: Final[Mapping[str, str]] = {
    "grade1": "compacted",
    "grade2": "gravel",
    "grade3": "unpaved",
    "grade4": "unpaved",
    "grade5": "unpaved",
}
LIT_YES: Final = frozenset({"yes", "24/7", "automatic", "sunset-sunrise", "limited", "interval"})
LIT_NO: Final = frozenset({"no", "disused"})

#: Tags kept when reading OSM (everything the pipeline looks at).
USED_TAGS: Final = (
    "highway",
    "footway",
    "service",
    "area",
    "access",
    "foot",
    "oneway:foot",
    "surface",
    "tracktype",
    "lit",
    "bridge",
    "tunnel",
    "covered",
    "layer",
    "name",
    "aeroway",
)
#: Share of a support way's length inside an aerodrome above which it is ignored.
AERODROME_MAX_INSIDE: Final = 0.5


def classify_way(tags: Mapping[str, str]) -> RoadClass | None:
    """Role of a way in the network, or ``None`` if it is ignored.

    MAJOR roads are kept whatever their access tags, since they remain
    barriers. Support ways (MINOR, PATH) must be runnable both ways.
    """
    highway = tags.get("highway")
    if highway in MAJOR_HIGHWAYS:
        return RoadClass.MAJOR
    if highway in MINOR_HIGHWAYS:
        road_class = RoadClass.MINOR
    elif highway in PATH_HIGHWAYS:
        road_class = RoadClass.PATH
    else:
        return None
    foot = tags.get("foot")
    if (
        tags.get("area") == "yes"
        or tags.get("service") in EXCLUDED_SERVICES
        or foot in NO_FOOT
        or (tags.get("access") in NO_ACCESS and foot not in FOOT_ALLOWED)
        or tags.get("oneway:foot") == "yes"
        or "aeroway" in tags
    ):
        return None
    return road_class


def structure_of(tags: Mapping[str, str]) -> str | None:
    """``"bridge"``, ``"tunnel"`` (including ``covered=yes``) or ``None``."""
    if tags.get("bridge", "no") != "no":
        return "bridge"
    if tags.get("tunnel", "no") != "no" or tags.get("covered") == "yes":
        return "tunnel"
    return None


def surface_category(
    surface: str | None,
    tracktype: str | None = None,
    road_class: RoadClass | None = None,
) -> str:
    """Normalised surface: paved, compacted, gravel, cobbles, unpaved or unknown.

    Without a ``surface`` tag, falls back on ``tracktype``, then assumes that
    MINOR roads are paved.
    """
    if surface is not None:
        return SURFACE_CATEGORIES.get(surface, "unknown")
    if tracktype is not None and tracktype in TRACKTYPE_CATEGORIES:
        return TRACKTYPE_CATEGORIES[tracktype]
    if road_class is RoadClass.MINOR:
        return "paved"
    return "unknown"


def lit_category(lit: str | None) -> str:
    """Normalised lighting of a way: yes, no or unknown."""
    if lit in LIT_YES:
        return "yes"
    if lit in LIT_NO:
        return "no"
    return "unknown"


def way_from_osm(
    way_id: int,
    node_ids: tuple[int, ...],
    coords: FloatArray,
    tags: Mapping[str, str],
) -> Way | None:
    """Build a :class:`Way` from OSM data, or ``None`` if it is ignored."""
    road_class = classify_way(tags)
    if road_class is None or len(node_ids) < 2:
        return None
    return Way(
        id=way_id,
        node_ids=node_ids,
        coords=coords,
        road_class=road_class,
        highway=tags["highway"],
        surface=tags.get("surface"),
        tracktype=tags.get("tracktype"),
        lit=tags.get("lit"),
        structure=structure_of(tags),
        name=tags.get("name"),
        footway=tags.get("footway"),
    )


def _in_bbox(lonlat: FloatArray, bbox: tuple[float, float, float, float]) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    inside = (
        (lonlat[:, 0] >= min_lon)
        & (lonlat[:, 0] <= max_lon)
        & (lonlat[:, 1] >= min_lat)
        & (lonlat[:, 1] <= max_lat)
    )
    return bool(inside.any())


class RawWay(NamedTuple):
    """A relevant OSM way as read from the file (WGS84)."""

    id: int
    node_ids: tuple[int, ...]
    lonlat: FloatArray
    tags: dict[str, str]


def iter_relevant_ways(
    path: Path,
    bbox: tuple[float, float, float, float] | None = None,
    area: BaseGeometry | None = None,
) -> Iterator[RawWay]:
    """Stream the ``highway=*`` ways kept by :func:`classify_way`.

    Args:
        path: ``.osm.pbf`` or ``.osm`` file.
        bbox: WGS84 ``(min_lon, min_lat, max_lon, max_lat)``; ways with at
            least one node inside are kept whole.
        area: WGS84 polygon (e.g. a département outline grown by a margin);
            ways with at least one node inside are kept whole.
    """
    import osmium
    import shapely

    if area is not None:
        shapely.prepare(area)
        area_bbox = area.bounds

    processor = (
        osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations()
        .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
        .with_filter(osmium.filter.KeyFilter("highway"))
    )
    for obj in processor:
        if not isinstance(obj, osmium.osm.Way):
            continue
        tags = {key: obj.tags[key] for key in USED_TAGS if key in obj.tags}
        if classify_way(tags) is None:
            continue
        nodes = [n for n in obj.nodes if n.location.valid()]
        if len(nodes) < 2:
            continue
        lonlat = np.array([(n.location.lon, n.location.lat) for n in nodes], dtype=np.float64)
        if bbox is not None and not _in_bbox(lonlat, bbox):
            continue
        if area is not None and not (
            _in_bbox(lonlat, area_bbox)
            and shapely.contains_xy(area, lonlat[:, 0], lonlat[:, 1]).any()
        ):
            continue
        yield RawWay(obj.id, tuple(n.ref for n in nodes), lonlat, tags)


def read_ways(
    path: Path,
    bbox: tuple[float, float, float, float] | None = None,
    project: Projector | None = None,
    area: BaseGeometry | None = None,
) -> list[Way]:
    """Read all relevant ``highway=*`` ways from an OSM file.

    Args:
        path: ``.osm.pbf`` or ``.osm`` file.
        bbox: WGS84 ``(min_lon, min_lat, max_lon, max_lat)``; ways with at
            least one node inside are kept whole.
        project: Projection of lon/lat arrays; defaults to Lambert-93.
        area: WGS84 polygon; ways with at least one node inside are kept whole.

    Returns:
        Ways in file order, including MAJOR roads (used as barriers), without
        the support ways lying mostly inside an aerodrome unless they are
        open to pedestrians (``foot=yes``…). The aerodromes are read from the
        same file: a file cut by :func:`clip_osm` has none.
    """
    from shapely.geometry import Polygon

    project = project or make_projector("EPSG:4326", WORK_CRS)
    ways: list[Way] = []
    open_to_foot: list[int] = []
    for raw in iter_relevant_ways(path, bbox, area):
        way = way_from_osm(raw.id, raw.node_ids, project(raw.lonlat), raw.tags)
        if way is not None:
            ways.append(way)
            if raw.tags.get("foot") in FOOT_ALLOWED:
                open_to_foot.append(way.id)
    aerodromes = [
        Polygon(project(ring), [project(hole)] if hole is not None else []).buffer(0)
        for raw in iter_aerodromes(path, bbox, area)
        for ring, hole in zip(raw.rings, raw.holes, strict=True)
    ]
    return drop_ways_inside(ways, aerodromes, AERODROME_MAX_INSIDE, keep=open_to_foot)


def iter_aerodromes(
    path: Path,
    bbox: tuple[float, float, float, float] | None = None,
    area: BaseGeometry | None = None,
) -> Iterator[RawArea]:
    """Stream the ``aeroway=aerodrome`` areas (airports and airfields).

    Their service roads, runways' edges and aprons are closed to the public
    but rarely tagged so. Arguments as :func:`iter_sport_areas`.
    """
    for raw in _iter_areas(path, ("aeroway",), ("aeroway",), bbox, area):
        if raw.tags.get("aeroway") == "aerodrome":
            yield raw


def len_of(ring: FloatArray) -> float:
    """Length of a lon/lat ring in degrees (only to compare rings of one area)."""
    return float(np.hypot(*np.diff(ring, axis=0).T).sum())


class RawArea(NamedTuple):
    """An OSM area (closed way or multipolygon) as read from the file (WGS84)."""

    osm_id: str
    """``"way/123"`` or ``"relation/45"``."""
    rings: list[FloatArray]
    """Outer rings, closed."""
    holes: list[FloatArray | None]
    """Longest inner ring of each outer ring, if any: the inner edge of a
    track mapped as a ring-shaped area, close to its running line."""
    tags: dict[str, str]


#: Tag keys of the areas read by :func:`iter_sport_areas`.
SPORT_AREA_KEYS: Final = ("leisure", "amenity")
#: Tags kept on those areas (everything ``loops.py`` looks at).
SPORT_AREA_TAGS: Final = (
    "leisure",
    "amenity",
    "sport",
    "surface",
    "lit",
    "access",
    "foot",
    "name",
    "opening_hours",
    "indoor",
    "covered",
    "building",
)


def iter_sport_areas(
    path: Path,
    bbox: tuple[float, float, float, float] | None = None,
    area: BaseGeometry | None = None,
) -> Iterator[RawArea]:
    """Stream the areas with a ``leisure`` or ``amenity`` tag.

    Closed ways and multipolygon relations alike (pyosmium assembles them).
    Filtering them further (tracks, sports facilities) is up to ``loops.py``.

    Args:
        path: ``.osm.pbf`` or ``.osm`` file.
        bbox: WGS84 ``(min_lon, min_lat, max_lon, max_lat)``; areas with at
            least one node inside are kept.
        area: WGS84 polygon; areas with at least one node inside are kept.
    """
    return _iter_areas(path, SPORT_AREA_KEYS, SPORT_AREA_TAGS, bbox, area)


#: Tag keys of the areas read by :func:`iter_setting_areas`.
SETTING_AREA_KEYS: Final = ("leisure", "landuse", "natural")
#: Tags kept on those areas (everything ``circuits.py`` looks at).
SETTING_AREA_TAGS: Final = ("leisure", "landuse", "natural", "water", "name")


def iter_setting_areas(
    path: Path,
    bbox: tuple[float, float, float, float] | None = None,
    area: BaseGeometry | None = None,
) -> Iterator[RawArea]:
    """Stream the areas with a ``leisure``, ``landuse`` or ``natural`` tag.

    Parks, water and green spaces among them give circuits their setting
    (``circuits.setting_kind``). Arguments as :func:`iter_sport_areas`.
    """
    return _iter_areas(path, SETTING_AREA_KEYS, SETTING_AREA_TAGS, bbox, area)


def _iter_areas(
    path: Path,
    keys: tuple[str, ...],
    kept_tags: tuple[str, ...],
    bbox: tuple[float, float, float, float] | None,
    area: BaseGeometry | None,
) -> Iterator[RawArea]:
    """Areas with one of ``keys``, with their ``kept_tags``."""
    import osmium
    import shapely

    if area is not None:
        shapely.prepare(area)
        area_bbox = area.bounds

    processor = (
        osmium.FileProcessor(str(path))
        .with_areas(osmium.filter.KeyFilter(*keys))
        .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
        .with_filter(osmium.filter.KeyFilter(*keys))
    )
    for obj in processor:
        if not isinstance(obj, osmium.osm.Area):
            continue
        rings: list[FloatArray] = []
        holes: list[FloatArray | None] = []
        for outer in obj.outer_rings():
            ring = np.array([(n.lon, n.lat) for n in outer], dtype=np.float64)
            if len(ring) < 4:
                continue
            inners = [
                np.array([(n.lon, n.lat) for n in inner], dtype=np.float64)
                for inner in obj.inner_rings(outer)
            ]
            inners = [r for r in inners if len(r) >= 4]
            rings.append(ring)
            holes.append(max(inners, key=len_of) if inners else None)
        if not rings:
            continue
        lonlat = np.vstack(rings)
        if bbox is not None and not _in_bbox(lonlat, bbox):
            continue
        if area is not None and not (
            _in_bbox(lonlat, area_bbox)
            and shapely.contains_xy(area, lonlat[:, 0], lonlat[:, 1]).any()
        ):
            continue
        tags = {key: obj.tags[key] for key in kept_tags if key in obj.tags}
        kind = "way" if obj.from_way() else "relation"
        yield RawArea(f"{kind}/{obj.orig_id()}", rings, holes, tags)


def clip_osm(src: Path, dst: Path, bbox: tuple[float, float, float, float]) -> tuple[int, int]:
    """Write the relevant ways touching ``bbox``, and their nodes, to a new file.

    A pure-Python alternative to ``osmium extract``: two passes over ``src``,
    the second one filtering by id inside libosmium. Reading the small output
    gives the same ways as reading ``src`` with the same bbox.

    Returns:
        ``(number of ways, number of nodes)`` written.
    """
    import osmium

    way_ids: set[int] = set()
    node_ids: set[int] = set()
    for raw in iter_relevant_ways(src, bbox):
        way_ids.add(raw.id)
        node_ids.update(raw.node_ids)
    node_filter = osmium.filter.IdFilter(node_ids)
    node_filter.enable_for(osmium.osm.NODE)
    way_filter = osmium.filter.IdFilter(way_ids)
    way_filter.enable_for(osmium.osm.WAY)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with osmium.SimpleWriter(str(dst), overwrite=True) as writer:
        osmium.apply(
            str(src),
            osmium.filter.EntityFilter(osmium.osm.NODE | osmium.osm.WAY),
            node_filter,
            way_filter,
            writer,
        )
    return len(way_ids), len(node_ids)
