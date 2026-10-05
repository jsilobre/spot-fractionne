"""Segment ids kept from one published version to the next (ADR 0012).

A segment id is a hash of its rounded geometry (``geometry.stable_id``): a
small change (new DEM, edited OSM way, other parameters) gives a new id and
breaks the links already shared. Before publishing, the new segments are
therefore matched with the previously published ones:

* a new segment that covers the same ground as a previous one of the same
  kind (both covered at ``MATCH_MIN`` or more, within ``BUFFER_M``) takes
  its id; pairs are formed greedily, best match first, one to one;
* a previous id left without a match becomes an **alias** of the new segment
  that covers most of it (at least ``ALIAS_MIN`` of its length), e.g. a flat
  split in three; otherwise it is **retired** (segment gone);
* aliases and retired ids of the earlier versions are kept, their targets
  followed to the current version;
* a new segment whose own id is already taken (by a previous segment, alias
  or retired id) gets a ``-2``, ``-3``… suffix.

The previous version is read from what was published (``segments.pmtiles``
at its most detailed zoom, and ``ids/``): what the users saw is the
reference. The tile geometries are within a metre of the original ones.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

import numpy as np
import shapely
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from flat_segments.export import Published
from flat_segments.params import WEB_CRS, WORK_CRS

#: Distance under which two lines cover the same ground, in metres.
BUFFER_M: Final = 10.0
#: Share of both segments covered by the other for the id to be kept.
MATCH_MIN: Final = 0.8
#: Share of a vanished segment covered by a new one to redirect its id there.
ALIAS_MIN: Final = 0.3
#: A segment id: kind, 12 hexadecimal characters, optional ``-N`` suffix.
SEGMENT_ID: Final = re.compile(r"(flat|climb|loop)-[0-9a-f]{12}(-[0-9]+)?")


def is_segment_id(value: object) -> bool:
    """Whether ``value`` is a segment id (a damaged tile may hold anything)."""
    return isinstance(value, str) and SEGMENT_ID.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class PreviousSegment:
    """A segment of the previous version (geometry in Lambert-93)."""

    id: str
    kind: str
    geometry: BaseGeometry


@dataclass(frozen=True, slots=True)
class Redirect:
    """An id that no longer names a segment.

    ``target`` is the id to open instead (``None``: the segment is gone) and
    ``position`` ``[lon, lat]`` is where to look: the target, else the old
    segment.
    """

    target: str | None
    position: tuple[float, float]


@dataclass(frozen=True, slots=True)
class PreviousVersion:
    """What was published before: segments and redirects."""

    segments: tuple[PreviousSegment, ...]
    redirects: Mapping[str, Redirect]


@dataclass(frozen=True, slots=True)
class Lineage:
    """Result of :func:`match`: segments with their final ids, and redirects."""

    segments: tuple[Published, ...]
    redirects: Mapping[str, Redirect]
    kept: int  # segments that kept a previous id
    renamed: int  # new segments whose own id was taken
    aliased: int  # previous ids now redirected to another segment
    retired: int  # previous ids now naming nothing
    total: int = 0  # segments given a final id

    @property
    def summary(self) -> str:
        """One line for the logs."""
        new = self.total - self.kept
        return (
            f"{self.kept} ids kept, {new} new ({self.renamed} renamed), "
            f"{self.aliased} redirected, {self.retired} retired, "
            f"{len(self.redirects)} redirects in total"
        )


def read_previous(directory: Path, layer: str = "segments") -> PreviousVersion | None:
    """Read a published tileset (``segments.pmtiles``, ``ids/``), or None if absent.

    The pieces of a segment cut by tile borders are merged back. Features
    whose id is not a segment id are left out: tippecanoe before 2.55 could
    give a feature the value of another attribute as id (ADR 0009). The kind
    is taken from the id.
    """
    import geopandas as gpd

    pmtiles = directory / "segments.pmtiles"
    if not pmtiles.exists():
        return None
    frame = gpd.read_file(
        pmtiles, layer=layer, columns=["id"], engine="pyogrio", ZOOM_LEVEL=READ_ZOOM
    )
    frame = frame[frame["id"].map(is_segment_id)]
    segments: tuple[PreviousSegment, ...] = ()
    if len(frame):
        merged = frame.to_crs(WORK_CRS).dissolve(by="id")
        segments = tuple(
            PreviousSegment(str(i), str(i).split("-")[0], shapely.line_merge(geometry))
            for i, geometry in zip(merged.index, merged.geometry, strict=True)
        )
    return PreviousVersion(segments, read_redirects(directory / "ids"))


#: Zoom at which the previous segments are read back (the first of the layer).
READ_ZOOM: Final = 12
#: Margin around a chunk when reading the previous segments, in metres: a
#: previous segment crossing the chunk extent is read whole.
PREVIOUS_MARGIN_M: Final = 5000.0


@dataclass(frozen=True)
class PublishedTileset:
    """A published version, read chunk by chunk (:meth:`segments_in`)."""

    pmtiles: Path
    live: Mapping[str, tuple[float, float]]  # id -> [lon, lat] of the segments
    redirects: Mapping[str, Redirect]
    layer: str = "segments"

    def segments_in(
        self, bounds_l93: tuple[float, float, float, float], margin_m: float = PREVIOUS_MARGIN_M
    ) -> list[PreviousSegment]:
        """Previous segments around a Lambert-93 extent (pieces merged back)."""
        import geopandas as gpd
        from pyproj import Transformer

        min_x, min_y, max_x, max_y = bounds_l93
        to_mercator = Transformer.from_crs(WORK_CRS, "EPSG:3857", always_xy=True)
        bbox = to_mercator.transform_bounds(
            min_x - margin_m, min_y - margin_m, max_x + margin_m, max_y + margin_m
        )
        # Zoom 12, the first zoom of the layer: GDAL loses pieces of some long
        # segments at zoom 14 (2,081 m read for a climb of 4,509 m, whole in
        # the tiles), and the coarser geometry (about 2 m) is well within BUFFER_M.
        frame = gpd.read_file(
            self.pmtiles,
            layer=self.layer,
            columns=["id"],
            bbox=bbox,
            engine="pyogrio",
            ZOOM_LEVEL=READ_ZOOM,
        )
        frame = frame[frame["id"].map(is_segment_id)]
        if not len(frame):
            return []
        merged = frame.to_crs(WORK_CRS).dissolve(by="id")
        return [
            PreviousSegment(str(i), str(i).split("-")[0], shapely.line_merge(geometry))
            for i, geometry in zip(merged.index, merged.geometry, strict=True)
        ]


def open_previous(directory: Path) -> PublishedTileset | None:
    """The version published in ``directory`` (``segments.pmtiles``, ``ids/``), or None."""
    pmtiles = directory / "segments.pmtiles"
    if not pmtiles.exists():
        return None
    live, redirects = read_index(directory / "ids")
    return PublishedTileset(pmtiles, live, redirects)


def read_index(index_dir: Path) -> tuple[dict[str, tuple[float, float]], dict[str, Redirect]]:
    """Segments (id -> position) and redirects of a published id index.

    Entries that are not segment ids, or whose target is not one, are left out.
    """
    live: dict[str, tuple[float, float]] = {}
    for path in sorted(index_dir.glob("*.json")):
        for segment_id, entry in json.loads(path.read_text(encoding="utf-8")).items():
            if len(entry) == 2 and is_segment_id(segment_id):
                live[segment_id] = (float(entry[0]), float(entry[1]))
    return live, read_redirects(index_dir)


def read_redirects(index_dir: Path) -> dict[str, Redirect]:
    """Redirects of a published id index (entries ``[lon, lat, target]``).

    Entries that are not segment ids, or whose target is not one, are left out.
    """
    redirects: dict[str, Redirect] = {}
    for path in sorted(index_dir.glob("*.json")):
        for segment_id, entry in json.loads(path.read_text(encoding="utf-8")).items():
            if len(entry) != 3 or not is_segment_id(segment_id):
                continue
            target = entry[2]
            if target is None or is_segment_id(target):
                redirects[segment_id] = Redirect(target, (float(entry[0]), float(entry[1])))
    return redirects


def _pairs(
    current: Sequence[BaseGeometry], previous: Sequence[BaseGeometry], buffer_m: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Candidate pairs (i current, j previous) and the share of each covered by the other."""
    empty = np.zeros(0, dtype=np.intp)
    if not current or not previous:
        return empty, empty, np.zeros(0), np.zeros(0)
    cur = np.asarray(current, dtype=object)
    prev = np.asarray(previous, dtype=object)
    cur_buffers = shapely.buffer(cur, buffer_m)
    prev_buffers = shapely.buffer(prev, buffer_m)
    i, j = shapely.STRtree(prev).query(cur_buffers, predicate="intersects")
    cur_covered = shapely.length(shapely.intersection(cur[i], prev_buffers[j]))
    prev_covered = shapely.length(shapely.intersection(prev[j], cur_buffers[i]))
    with np.errstate(divide="ignore", invalid="ignore"):
        cur_share = np.nan_to_num(cur_covered / shapely.length(cur[i]))
        prev_share = np.nan_to_num(prev_covered / shapely.length(prev[j]))
    return i, j, cur_share, prev_share


def _midpoint(geometry: BaseGeometry) -> tuple[float, float]:
    point = shapely.line_interpolate_point(geometry, 0.5, normalized=True)
    return float(point.x), float(point.y)


def _free_id(segment_id: str, taken: set[str]) -> str:
    """``segment_id``, or with the first free ``-N`` suffix."""
    candidate, n = segment_id, 1
    while candidate in taken:
        n += 1
        candidate = f"{segment_id}-{n}"
    return candidate


class Matcher:
    """Give final ids to segments added chunk by chunk (one département at a time).

    Only light state is kept between chunks: the previous ids with their
    position, the ids taken so far, the best redirect candidate of each
    previous id. A chunk is matched with the previous segments around it
    (``previous`` given to :meth:`add`), so the whole set never has to be in
    memory (docs/phase-2/2.5-france.md).
    """

    def __init__(
        self,
        previous_live: Mapping[str, tuple[float, float]],
        previous_redirects: Mapping[str, Redirect],
        *,
        buffer_m: float = BUFFER_M,
        match_min: float = MATCH_MIN,
        alias_min: float = ALIAS_MIN,
        to_wgs84: Any = None,
    ) -> None:
        from flat_segments.geometry import make_projector

        self.previous_live = previous_live
        self.previous_redirects = previous_redirects
        self.buffer_m, self.match_min, self.alias_min = buffer_m, match_min, alias_min
        self.to_wgs84 = to_wgs84 or make_projector(WORK_CRS, WEB_CRS)
        self.kept: set[str] = set()
        self.taken: set[str] = set(previous_live) | set(previous_redirects)
        # previous id -> (share covered, target id, target position)
        self.best_alias: dict[str, tuple[float, str, tuple[float, float]]] = {}
        # Positions of the targets of earlier redirects, once kept.
        self.wanted = {r.target for r in previous_redirects.values() if r.target}
        self.positions: dict[str, tuple[float, float]] = {}
        self.total = self.renamed = 0

    def _lonlat(self, geometry: BaseGeometry) -> tuple[float, float]:
        lon, lat = self.to_wgs84(np.array([_midpoint(geometry)]))[0]
        return round(float(lon), 5), round(float(lat), 5)

    def add(
        self, current: Sequence[Published], previous: Sequence[PreviousSegment]
    ) -> list[Published]:
        """Final ids of a chunk of segments; ``previous``: the previous segments around it."""
        geometries = [LineString(s.coords) for s in current]
        candidates = [p for p in previous if p.id in self.previous_live and p.id not in self.kept]
        final: dict[int, str] = {}  # current index -> previous id kept
        aliases: list[tuple[float, int, str]] = []  # (share, current index, previous id)
        by_kind_prev: dict[str, list[int]] = defaultdict(list)
        for j, p in enumerate(candidates):
            by_kind_prev[p.kind].append(j)
        by_kind_cur: dict[str, list[int]] = defaultdict(list)
        for i, s in enumerate(current):
            by_kind_cur[str(s.kind)].append(i)

        for kind, cur_idx in by_kind_cur.items():
            prev_idx = by_kind_prev.get(kind, [])
            pair_cur, pair_prev, cur_share, prev_share = _pairs(
                [geometries[k] for k in cur_idx],
                [candidates[k].geometry for k in prev_idx],
                self.buffer_m,
            )
            score = np.minimum(cur_share, prev_share)
            used_cur: set[int] = set()
            for k in np.lexsort((-np.maximum(cur_share, prev_share), -score)):
                if score[k] < self.match_min:
                    break
                ci, pid = cur_idx[pair_cur[k]], candidates[prev_idx[pair_prev[k]]].id
                if ci not in used_cur and pid not in self.kept:
                    used_cur.add(ci)
                    self.kept.add(pid)
                    final[ci] = pid
            for k in range(len(pair_cur)):
                if prev_share[k] >= self.alias_min:
                    pid = candidates[prev_idx[pair_prev[k]]].id
                    aliases.append((float(prev_share[k]), cur_idx[pair_cur[k]], pid))

        # Final ids: kept ones, then the others, avoiding every known id.
        for i, segment in enumerate(current):
            if i in final:
                continue
            final[i] = _free_id(segment.id, self.taken)
            self.taken.add(final[i])
            self.renamed += final[i] != segment.id
        for share, i, pid in aliases:
            if pid not in self.kept and share > self.best_alias.get(pid, (-1.0,))[0]:
                self.best_alias[pid] = (share, final[i], self._lonlat(geometries[i]))
        for i, segment_id in final.items():
            if segment_id in self.wanted:
                self.positions[segment_id] = self._lonlat(geometries[i])
        self.total += len(current)
        return [s if final[i] == s.id else replace(s, id=final[i]) for i, s in enumerate(current)]

    def finish(self) -> Lineage:
        """Redirects of the previous ids not kept, and of the earlier redirects."""
        redirects: dict[str, Redirect] = {}
        aliased = retired = 0
        for pid, position in self.previous_live.items():
            if pid in self.kept:
                continue
            if pid in self.best_alias:
                _, alias_target, alias_position = self.best_alias[pid]
                redirects[pid] = Redirect(alias_target, alias_position)
                aliased += 1
            else:
                redirects[pid] = Redirect(None, position)
                retired += 1
        # Earlier redirects: follow their target (a previous segment) to this version.
        for old_id, redirect in self.previous_redirects.items():
            target = redirect.target
            if target is None:
                redirects[old_id] = redirect
            elif target in self.kept:
                redirects[old_id] = Redirect(target, self.positions[target])
            elif target in redirects:
                redirects[old_id] = redirects[target]
            else:  # not a previous segment: inconsistent index, keep the place only
                redirects[old_id] = Redirect(None, redirect.position)
        return Lineage(
            (), redirects, len(self.kept), self.renamed, aliased, retired, total=self.total
        )


def match(
    current: Sequence[Published],
    previous: PreviousVersion | None,
    *,
    buffer_m: float = BUFFER_M,
    match_min: float = MATCH_MIN,
    alias_min: float = ALIAS_MIN,
    to_wgs84: Any = None,
) -> Lineage:
    """Give the current segments their final ids, and list the redirects (one chunk)."""
    if previous is None:
        return Lineage(tuple(current), {}, 0, 0, 0, 0, total=len(current))
    matcher = Matcher(
        {},
        previous.redirects,
        buffer_m=buffer_m,
        match_min=match_min,
        alias_min=alias_min,
        to_wgs84=to_wgs84,
    )
    matcher.previous_live = {p.id: matcher._lonlat(p.geometry) for p in previous.segments}
    matcher.taken |= set(matcher.previous_live)
    segments = matcher.add(current, previous.segments)
    result = matcher.finish()
    return replace(result, segments=tuple(segments))
