"""Export of the segments as vector tiles in a PMTiles archive (ADR 0009).

The web page reads three things next to each other (``web/data/``):

* ``segments.pmtiles``: two layers.
  * ``segments``: every segment (and loop, ADR 0014) with all its public properties, zooms 12 to
    14. The page builds its result list from the zoom 12 tiles, so nothing
    may be dropped there.
  * ``overview``: a lighter view for zooms 8 to 11, with only ``id``,
    ``kind`` and ``length_m``, thinned out by tippecanoe where too dense.
* ``segments.json``: what the GeoJSON ``metadata`` member used to hold
  (attribution, date, parameters), plus the bounds and counts of the set.
* ``ids/XX.json``: where each segment is, for links holding only an ``id``:
  ``[lon, lat]``, or ``[lon, lat, target]`` for an id of an earlier version
  (``target``: the id to open instead, ``null`` if the segment is gone; see
  ``lineage.py``). The ids are spread over small files by the first
  hexadecimal characters of their hash part: 2 (256 files), or more for a
  large set (``index_prefix_length``).

Vector tiles have no list nor null values: lists (``highways``…) are written
as JSON strings, and null properties (``name``…) are left out.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Final

import numpy as np

from flat_segments.detect import Segment, SegmentKind
from flat_segments.export import (
    COORD_DECIMALS,
    SCHEMA_VERSION,
    Published,
    attribution_for,
    published_properties,
)
from flat_segments.geometry import Projector, make_projector
from flat_segments.lineage import Redirect
from flat_segments.loops import LOOP_KIND
from flat_segments.params import WEB_CRS, WORK_CRS

DETAIL_LAYER: Final = "segments"
OVERVIEW_LAYER: Final = "overview"
DETAIL_ZOOMS: Final = (12, 14)
OVERVIEW_ZOOMS: Final = (8, 11)
OVERVIEW_FIELDS: Final = ("id", "kind", "length_m")
#: Number of hexadecimal characters of the id hash naming its index file (at least).
INDEX_PREFIX_LENGTH: Final = 2
#: Entries per index file above which the prefix grows by one character.
INDEX_FILE_ENTRIES: Final = 2000
#: Oldest usable tippecanoe. Before 2.55.0, hash collisions in its string pool
#: give some features the attribute values of others: 5 to 18 segments out of
#: 350,000 on the ex-Midi-Pyrénées, with tippecanoe 2.49 (Ubuntu package).
MIN_TIPPECANOE: Final = (2, 55, 0)


class TippecanoeError(RuntimeError):
    """tippecanoe is missing, too old or failed."""


class TilesetCheckError(TippecanoeError):
    """The written tiles do not hold the segments as they were given."""


@dataclass(frozen=True)
class TilesetFiles:
    """Files written by :func:`write_tileset`."""

    pmtiles: Path
    metadata: Path
    index_dir: Path


def parse_version(text: str) -> tuple[int, int, int] | None:
    """``(major, minor, patch)`` in the output of ``tippecanoe --version``."""
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def tippecanoe_version() -> tuple[int, int, int] | None:
    """Version of the ``tippecanoe`` on the PATH, or None if absent."""
    path = shutil.which("tippecanoe")
    if path is None:
        return None
    result = subprocess.run([path, "--version"], capture_output=True, text=True, check=False)
    return parse_version(result.stdout + result.stderr)


def check_tippecanoe() -> None:
    """Make sure that tippecanoe and tile-join are installed, in a usable version.

    Raises:
        TippecanoeError: If they are missing or older than ``MIN_TIPPECANOE``.
    """
    version = tippecanoe_version()
    if version is None or shutil.which("tile-join") is None:
        raise TippecanoeError("tippecanoe and tile-join not found: install tippecanoe (ADR 0009)")
    if version < MIN_TIPPECANOE:
        found, needed = (".".join(map(str, v)) for v in (version, MIN_TIPPECANOE))
        raise TippecanoeError(
            f"tippecanoe {found} mixes up attribute values on large sets: "
            f"install {needed} or later (ADR 0009)"
        )


def tippecanoe_available() -> bool:
    """Whether ``tippecanoe`` and ``tile-join`` are installed in a usable version."""
    try:
        check_tippecanoe()
    except TippecanoeError:
        return False
    return True


#: Every kind of feature in the tiles.
KINDS: Final = (*(kind.value for kind in SegmentKind), LOOP_KIND)


def tile_properties(segment: Published) -> dict[str, Any]:
    """Public properties for a vector tile: lists as JSON, no nulls."""
    props = {}
    for name, value in published_properties(segment).items():
        if value is None:
            continue
        props[name] = json.dumps(value, separators=(",", ":")) if isinstance(value, list) else value
    return props


def index_key(segment_id: str, prefix_length: int = INDEX_PREFIX_LENGTH) -> str:
    """Name (without extension) of the index file holding ``segment_id``."""
    return segment_id.split("-")[1][:prefix_length]


def index_prefix_length(n_entries: int) -> int:
    """Prefix length keeping index files under ``INDEX_FILE_ENTRIES`` entries on average."""
    length = INDEX_PREFIX_LENGTH
    while n_entries > INDEX_FILE_ENTRIES * 16**length:
        length += 1
    return length


def _write_features(
    segments: Sequence[Published], path: Path, to_wgs84: Projector, fields: Sequence[str] | None
) -> None:
    """Newline-delimited GeoJSON features (tippecanoe reads them in parallel)."""
    with path.open("w", encoding="utf-8") as handle:
        _append_features(segments, handle, to_wgs84, fields)


def _append_features(
    segments: Sequence[Published],
    handle: IO[str],
    to_wgs84: Projector,
    fields: Sequence[str] | None,
) -> None:
    for segment in segments:
        props = tile_properties(segment)
        if fields is not None:
            props = {k: v for k, v in props.items() if k in fields}
        coords = np.round(to_wgs84(segment.coords), COORD_DECIMALS).tolist()
        feature = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": props,
        }
        handle.write(json.dumps(feature, ensure_ascii=False, separators=(",", ":")) + "\n")


def _run(args: list[str]) -> None:
    try:
        subprocess.run(args, check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise TippecanoeError(f"{args[0]} not found: install tippecanoe (ADR 0009)") from error
    except subprocess.CalledProcessError as error:
        raise TippecanoeError(f"{args[0]} failed: {error.stderr.strip()[-500:]}") from error


def write_pmtiles(
    segments: Sequence[Published],
    path: Path,
    attribution: Sequence[str],
    to_wgs84: Projector | None = None,
) -> Path:
    """Write the two-layer PMTiles archive with tippecanoe and tile-join.

    Raises:
        TippecanoeError: If the tools are missing, too old or fail.
    """
    check_tippecanoe()
    to_wgs84 = to_wgs84 or make_projector(WORK_CRS, WEB_CRS)
    with tempfile.TemporaryDirectory(prefix="flat-segments-tiles-") as tmp:
        work = Path(tmp)
        detail_in, overview_in = work / "detail.geojsonl", work / "overview.geojsonl"
        _write_features(segments, detail_in, to_wgs84, None)
        _write_features(segments, overview_in, to_wgs84, OVERVIEW_FIELDS)
        _build_pmtiles(detail_in, overview_in, path, attribution)
    return path


def _build_pmtiles(
    detail_in: Path, overview_in: Path, path: Path, attribution: Sequence[str]
) -> None:
    """Run tippecanoe on the feature files of both layers, then tile-join."""
    check_tippecanoe()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flat-segments-tiles-") as tmp:
        work = Path(tmp)
        detail, overview = work / "detail.pmtiles", work / "overview.pmtiles"
        common = ["tippecanoe", "--quiet", "--force", "--read-parallel"]
        _run(
            [
                *common,
                f"--output={detail}",
                f"--layer={DETAIL_LAYER}",
                f"--minimum-zoom={DETAIL_ZOOMS[0]}",
                f"--maximum-zoom={DETAIL_ZOOMS[1]}",
                "--no-feature-limit",
                "--no-tile-size-limit",
                str(detail_in),
            ]
        )
        _run(
            [
                *common,
                f"--output={overview}",
                f"--layer={OVERVIEW_LAYER}",
                f"--minimum-zoom={OVERVIEW_ZOOMS[0]}",
                f"--maximum-zoom={OVERVIEW_ZOOMS[1]}",
                "--drop-densest-as-needed",
                str(overview_in),
            ]
        )
        _run(
            [
                "tile-join",
                "--quiet",
                "--force",
                "--no-tile-size-limit",
                f"--output={path}",
                "--name=flat-segments",
                f"--attribution={' ; '.join(attribution)}",
                str(detail),
                str(overview),
            ]
        )


def _same(expected: Any, found: Any) -> bool:
    """Whether a property read back from the tiles is the one written."""
    if expected is None:
        return found is None or (isinstance(found, float) and math.isnan(found))
    if (
        isinstance(expected, bool | int | float)
        and not isinstance(found, str)
        and found is not None
    ):
        return math.isclose(float(expected), float(found), rel_tol=1e-9, abs_tol=1e-9)
    return bool(expected == found)


def tileset_mismatches(
    expected: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], limit: int = 5
) -> list[str]:
    """Differences between the properties written and the features read back.

    Args:
        expected: Properties written, by segment id (``tile_properties``).
        rows: Features read back from the tiles (several per segment cut by
            tile borders), as property mappings.
        limit: Number of differences to describe at most.

    Returns:
        Descriptions of the differences (empty if none), plus the number of
        segments missing from the tiles.
    """
    problems: list[str] = []
    seen: set[str] = set()
    fields = sorted({name for props in expected.values() for name in props})
    for row in rows:
        segment_id = str(row.get("id"))
        if segment_id not in expected:
            problems.append(f"unknown feature id {segment_id!r}")
            continue
        seen.add(segment_id)
        props = expected[segment_id]
        for name in fields:
            if not _same(props.get(name), row.get(name)):
                problems.append(
                    f"{segment_id}: {name} = {row.get(name)!r}, expected {props.get(name)!r}"
                )
                break
    missing = len(expected) - len(seen)
    described = problems[:limit] + (
        [f"... {len(problems) - limit} more"] if len(problems) > limit else []
    )
    return described + ([f"{missing} segments missing"] if missing else [])


def verify_chunk(
    path: Path, segments: Sequence[Published], to_wgs84: Projector | None = None
) -> tuple[list[str], set[str]]:
    """Check the tiles around a chunk of segments (one département).

    Returns:
        The differences found for these segments (:func:`tileset_mismatches`),
        and the ids of the other features read around them, to check that
        they are known.
    """
    import pyogrio  # type: ignore[import-untyped]
    from pyproj import Transformer

    if not segments:
        return [], set()
    to_wgs84 = to_wgs84 or make_projector(WORK_CRS, WEB_CRS)
    lonlat = to_wgs84(np.vstack([s.coords for s in segments]))
    to_mercator = Transformer.from_crs(WEB_CRS, "EPSG:3857", always_xy=True)
    bbox = to_mercator.transform_bounds(*lonlat.min(axis=0), *lonlat.max(axis=0))
    frame = pyogrio.read_dataframe(
        path, layer=DETAIL_LAYER, read_geometry=False, ZOOM_LEVEL=DETAIL_ZOOMS[0], bbox=bbox
    )
    rows = frame.drop(columns=[c for c in ("mvt_id",) if c in frame.columns]).to_dict("records")
    expected = {s.id: tile_properties(s) for s in segments}
    mine = [row for row in rows if str(row.get("id")) in expected]
    others = {str(row.get("id")) for row in rows} - set(expected)
    return tileset_mismatches(expected, mine), others


def verify_pmtiles(path: Path, segments: Sequence[Published]) -> None:
    """Read the detail layer back at its first zoom and compare it with the segments.

    Raises:
        TilesetCheckError: If a segment is missing or a property differs.
    """
    import pyogrio

    frame = pyogrio.read_dataframe(
        path, layer=DETAIL_LAYER, read_geometry=False, ZOOM_LEVEL=DETAIL_ZOOMS[0]
    )
    rows = frame.drop(columns=[c for c in ("mvt_id",) if c in frame.columns]).to_dict("records")
    expected = {s.id: tile_properties(s) for s in segments}
    problems = tileset_mismatches(expected, rows)
    if problems:
        raise TilesetCheckError(f"{path} does not hold the segments: " + "; ".join(problems))


def write_id_index(
    segments: Sequence[Published],
    directory: Path,
    to_wgs84: Projector | None = None,
    *,
    redirects: Mapping[str, Redirect] | None = None,
    prefix_length: int = INDEX_PREFIX_LENGTH,
) -> int:
    """Write ``directory/XX.json``: id -> ``[lon, lat]`` of the segment midpoint.

    Redirected ids get ``[lon, lat, target]``. Older files are removed first.
    Returns the number of files written.
    """
    to_wgs84 = to_wgs84 or make_projector(WORK_CRS, WEB_CRS)
    shards: dict[str, dict[str, list[Any]]] = defaultdict(dict)
    for segment in segments:
        mid = to_wgs84(segment.coords[len(segment.coords) // 2][None, :])[0]
        shards[index_key(segment.id, prefix_length)][segment.id] = [round(float(v), 5) for v in mid]
    for segment_id, redirect in (redirects or {}).items():
        lon, lat = redirect.position
        shards[index_key(segment_id, prefix_length)][segment_id] = [lon, lat, redirect.target]
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    for key, entries in shards.items():
        text = json.dumps(dict(sorted(entries.items())), separators=(",", ":"))
        (directory / f"{key}.json").write_text(text + "\n", encoding="utf-8")
    return len(shards)


def tileset_metadata(
    segments: Sequence[Published],
    *,
    sample: bool = False,
    generated_at: datetime | None = None,
    attribution: Sequence[str] | None = None,
    params: Mapping[str, Any] | None = None,
    to_wgs84: Projector | None = None,
    index_prefix: int = INDEX_PREFIX_LENGTH,
    tiles_url: str = "segments.pmtiles",
    index_url: str = "ids",
) -> dict[str, Any]:
    """Content of ``segments.json`` (see docs/data-model.md).

    ``tiles_url`` and ``index_url`` are relative to ``segments.json``, or
    absolute when the data is published elsewhere (ADR 0013).
    """
    to_wgs84 = to_wgs84 or make_projector(WORK_CRS, WEB_CRS)
    bounds = None
    if segments:
        lonlat = to_wgs84(np.vstack([s.coords for s in segments]))
        bounds = [round(float(v), 5) for v in (*lonlat.min(axis=0), *lonlat.max(axis=0))]
    return _metadata(
        bounds=bounds,
        counts={kind: sum(str(s.kind) == kind for s in segments) for kind in KINDS},
        attribution=list(attribution if attribution is not None else attribution_for(segments)),
        sample=sample,
        generated_at=generated_at,
        params=params,
        index_prefix=index_prefix,
        tiles_url=tiles_url,
        index_url=index_url,
    )


def _metadata(
    *,
    bounds: list[float] | None,
    counts: Mapping[str, int],
    attribution: list[str],
    sample: bool,
    generated_at: datetime | None,
    params: Mapping[str, Any] | None,
    index_prefix: int,
    tiles_url: str,
    index_url: str,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(UTC)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sample": sample,
        "attribution": attribution,
        "bounds": bounds,
        "counts": dict(counts),
        "tiles": {
            "url": tiles_url,
            "layer": DETAIL_LAYER,
            "minzoom": DETAIL_ZOOMS[0],
            "maxzoom": DETAIL_ZOOMS[1],
            "overview_layer": OVERVIEW_LAYER,
            "overview_minzoom": OVERVIEW_ZOOMS[0],
            "index": index_url,
            "index_prefix_length": index_prefix,
        },
        **({"params": dict(params)} if params is not None else {}),
    }


class TilesetWriter:
    """Write ``segments.pmtiles``, ``segments.json`` and ``ids/`` from chunks of segments.

    Segments are given one chunk (département) at a time (:meth:`add`): their
    features and index entries go to files at once, and only running totals
    stay in memory (bounds, counts, ids). :meth:`close` builds the tiles, checks
    them chunk by chunk, then writes the index and the metadata.
    """

    def __init__(
        self,
        directory: Path,
        *,
        sample: bool = False,
        generated_at: datetime | None = None,
        attribution: Sequence[str] | None = None,
        params: Mapping[str, Any] | None = None,
        tiles_url: str = "segments.pmtiles",
        index_url: str = "ids",
    ) -> None:
        self.files = TilesetFiles(
            directory / "segments.pmtiles", directory / "segments.json", directory / "ids"
        )
        self.options: dict[str, Any] = {
            "sample": sample,
            "generated_at": generated_at,
            "params": params,
            "tiles_url": tiles_url,
            "index_url": index_url,
        }
        self.attribution = attribution
        self.to_wgs84 = make_projector(WORK_CRS, WEB_CRS)
        self._tmp = tempfile.TemporaryDirectory(prefix="flat-segments-tiles-")
        self.work = Path(self._tmp.name)
        self._detail = (self.work / "detail.geojsonl").open("w", encoding="utf-8")
        self._overview = (self.work / "overview.geojsonl").open("w", encoding="utf-8")
        self._buckets: dict[str, IO[str]] = {}  # index entries by first 2 hex characters
        self.ids: set[str] = set()
        self.n_entries = 0
        self.counts = dict.fromkeys(KINDS, 0)
        self.lonlat_min = np.full(2, np.inf)
        self.lonlat_max = np.full(2, -np.inf)
        self._by_source: dict[str, Segment] = {}  # one segment per elevation source

    @property
    def total(self) -> int:
        """Number of segments added."""
        return len(self.ids)

    def _entry(self, segment_id: str, value: list[Any]) -> None:
        key = index_key(segment_id, INDEX_PREFIX_LENGTH)
        if key not in self._buckets:
            self._buckets[key] = (self.work / f"index-{key}.jsonl").open("w", encoding="utf-8")
        self._buckets[key].write(json.dumps([segment_id, value], separators=(",", ":")) + "\n")
        self.n_entries += 1

    def add(self, segments: Sequence[Published]) -> None:
        """Add a chunk of segments (with their final ids).

        Raises:
            ValueError: If a segment id was already added.
        """
        for segment in segments:
            if segment.id in self.ids:
                raise ValueError(f"duplicate segment ids across the files: {segment.id}")
            self.ids.add(segment.id)
            self.counts[str(segment.kind)] += 1
            if isinstance(segment, Segment):
                self._by_source.setdefault(segment.elevation_source, segment)
            mid = self.to_wgs84(segment.coords[len(segment.coords) // 2][None, :])[0]
            self._entry(segment.id, [round(float(v), 5) for v in mid])
        if segments:
            lonlat = self.to_wgs84(np.vstack([s.coords for s in segments]))
            self.lonlat_min = np.minimum(self.lonlat_min, lonlat.min(axis=0))
            self.lonlat_max = np.maximum(self.lonlat_max, lonlat.max(axis=0))
        _append_features(segments, self._detail, self.to_wgs84, None)
        _append_features(segments, self._overview, self.to_wgs84, OVERVIEW_FIELDS)

    def add_redirects(self, redirects: Mapping[str, Redirect]) -> None:
        """Add the ids of earlier versions (``[lon, lat, target]`` in the index)."""
        for segment_id, redirect in redirects.items():
            lon, lat = redirect.position
            self._entry(segment_id, [lon, lat, redirect.target])

    def metadata(self) -> dict[str, Any]:
        """Content of ``segments.json`` for the segments added."""
        bounds = None
        if self.ids:
            bounds = [round(float(v), 5) for v in (*self.lonlat_min, *self.lonlat_max)]
        return _metadata(
            bounds=bounds,
            counts=self.counts,
            attribution=list(
                self.attribution
                if self.attribution is not None
                else attribution_for(list(self._by_source.values()))
            ),
            index_prefix=index_prefix_length(self.n_entries),
            **self.options,
        )

    def close(self, chunks: Callable[[], Iterable[Sequence[Published]]]) -> TilesetFiles:
        """Build and check the tiles, then write the index and the metadata.

        Args:
            chunks: Gives the segments again, chunk by chunk, with their final
                ids, to check the tiles.

        Raises:
            TippecanoeError: If the tools are missing, too old or fail, or if
                the tiles do not hold the segments (``TilesetCheckError``).
        """
        try:
            self._detail.close()
            self._overview.close()
            metadata = self.metadata()
            _build_pmtiles(
                self.work / "detail.geojsonl",
                self.work / "overview.geojsonl",
                self.files.pmtiles,
                metadata["attribution"],
            )
            problems: list[str] = []
            others: set[str] = set()
            for chunk in chunks():
                found, around = verify_chunk(self.files.pmtiles, chunk, self.to_wgs84)
                problems += found
                others |= around
            problems += [f"unknown feature id {i!r}" for i in sorted(others - self.ids)[:5]]
            if problems:
                raise TilesetCheckError(
                    f"{self.files.pmtiles} does not hold the segments: " + "; ".join(problems[:8])
                )
            self._write_index(metadata["tiles"]["index_prefix_length"])
            self.files.metadata.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
            )
        finally:
            self.cleanup()
        return self.files

    def _write_index(self, prefix_length: int) -> None:
        directory = self.files.index_dir
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
        for handle in self._buckets.values():
            handle.close()
        for bucket in sorted(self.work.glob("index-*.jsonl")):
            shards: dict[str, dict[str, Any]] = defaultdict(dict)
            with bucket.open(encoding="utf-8") as handle:
                for line in handle:
                    segment_id, value = json.loads(line)
                    shards[index_key(segment_id, prefix_length)][segment_id] = value
            for key, entries in shards.items():
                text = json.dumps(dict(sorted(entries.items())), separators=(",", ":"))
                (directory / f"{key}.json").write_text(text + "\n", encoding="utf-8")

    def cleanup(self) -> None:
        """Remove the work files."""
        for handle in (self._detail, self._overview, *self._buckets.values()):
            handle.close()
        self._tmp.cleanup()


def write_tileset(
    segments: Sequence[Published],
    directory: Path,
    *,
    sample: bool = False,
    generated_at: datetime | None = None,
    attribution: Sequence[str] | None = None,
    params: Mapping[str, Any] | None = None,
    redirects: Mapping[str, Redirect] | None = None,
    tiles_url: str = "segments.pmtiles",
    index_url: str = "ids",
) -> TilesetFiles:
    """Write ``segments.pmtiles``, ``segments.json`` and ``ids/`` in ``directory``.

    The tiles are read back and checked before the other files are written.

    Raises:
        TippecanoeError: If the tools are missing, too old or fail, or if the
            tiles do not hold the segments (``TilesetCheckError``).
    """
    writer = TilesetWriter(
        directory,
        sample=sample,
        generated_at=generated_at,
        attribution=attribution,
        params=params,
        tiles_url=tiles_url,
        index_url=index_url,
    )
    try:
        writer.add(segments)
        writer.add_redirects(redirects or {})
    except BaseException:
        writer.cleanup()
        raise
    return writer.close(lambda: [segments])
