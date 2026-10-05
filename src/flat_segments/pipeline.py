"""Pipeline steps as plain functions, shared by the CLI commands.

Each step reads and writes files (docs/architecture.md, section 3.1). The
parameters used by ``detect`` are saved next to its output
(``segments.params.toml``) and embedded in the GeoJSON metadata by
``export``, so that a published dataset always says how it was produced.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from flat_segments.config import params_to_toml
from flat_segments.detect import Segment, detect_all
from flat_segments.params import PipelineParams

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry

    from flat_segments.export import Published
    from flat_segments.lineage import Lineage
    from flat_segments.loops import Loop
    from flat_segments.tiles import TilesetFiles


@dataclass(frozen=True, slots=True)
class DataPaths:
    """Default file locations (``data/`` is not versioned)."""

    pbf: Path = Path("data/raw/pilot.osm.pbf")
    dem: Path = Path("data/raw/dem/pilot.vrt")
    strokes: Path = Path("data/interim/strokes.parquet")
    profiles: Path = Path("data/interim/profiles.parquet")
    segments: Path = Path("data/processed/segments.parquet")
    loops: Path = Path("data/processed/loops.parquet")
    geojson: Path = Path("data/processed/segments.geojson")  # for inspection
    web_data: Path = Path("web/data")  # published tiles (export-pmtiles)


def loops_sibling(segments_path: Path) -> Path:
    """Loops published with a segments file: ``loops.parquet`` in the same folder."""
    return segments_path.with_name("loops.parquet")


def read_published(segments_path: Path) -> list[Published]:
    """Segments of a file, followed by the loops next to it (if any)."""
    from flat_segments.export import read_loops, read_segments

    items: list[Published] = list(read_segments(segments_path))
    loops = loops_sibling(segments_path)
    if loops.exists():
        items += read_loops(loops)
    return items


def params_sidecar(segments_path: Path) -> Path:
    """Path of the parameters file written next to a segments file."""
    return segments_path.with_suffix(".params.toml")


def run_extract(
    pbf: Path,
    bbox: tuple[float, float, float, float] | None,
    out: Path,
    params: PipelineParams,
) -> tuple[int, int]:
    """Read OSM ways and chain them into strokes.

    Returns:
        ``(number of ways, number of strokes)``.
    """
    from flat_segments.export import write_strokes
    from flat_segments.network import build_strokes
    from flat_segments.osm import read_ways

    ways = read_ways(pbf, bbox)
    strokes = build_strokes(ways, params.network)
    write_strokes(strokes, out)
    return len(ways), len(strokes)


def read_loops_from_osm(
    pbf: Path,
    bbox: tuple[float, float, float, float] | None = None,
    area: BaseGeometry | None = None,
) -> tuple[int, list[Loop]]:
    """Read the OSM sports areas around ``bbox`` or ``area`` and build the loops.

    Returns:
        ``(number of areas read, loops)``.
    """
    from flat_segments.geometry import make_projector
    from flat_segments.loops import SportArea, build_tracks
    from flat_segments.osm import iter_sport_areas
    from flat_segments.params import WORK_CRS

    project = make_projector("EPSG:4326", WORK_CRS)
    areas = [
        SportArea(
            raw.osm_id if len(raw.rings) == 1 else f"{raw.osm_id}#{i}",
            project(ring),
            raw.tags,
            project(hole) if hole is not None else None,
        )
        for raw in iter_sport_areas(pbf, bbox, area)
        for i, (ring, hole) in enumerate(zip(raw.rings, raw.holes, strict=True))
    ]
    return len(areas), build_tracks(areas)


def run_loops(
    pbf: Path, bbox: tuple[float, float, float, float] | None, out: Path
) -> tuple[int, int]:
    """Find the running tracks of an OSM extract (with all its tags, not a clipped one).

    Returns:
        ``(number of sports areas read, number of loops)``.
    """
    from flat_segments.export import write_loops

    n_areas, loops = read_loops_from_osm(pbf, bbox)
    write_loops(loops, out)
    return n_areas, len(loops)


def run_elevation(
    dem: Path,
    strokes: Path,
    out: Path,
    params: PipelineParams,
    source: str | None = None,
    fallback: Path | None = None,
) -> tuple[int, int]:
    """Sample the DEM along every stroke.

    ``source`` defaults to the one recorded in the raster (``download-dem``
    tags its tiles and VRT), else ``rge_alti_1m``. With a ``fallback`` raster
    (``download_dem(fallback_layer=...)``), a stroke with missing elevations
    is sampled again on it when its profile has gaps that cannot be filled
    (not under a bridge or a tunnel, longer than ``max_gap_fill_m``). Its
    fallback profile is kept, whole, when it leaves fewer such gaps: one
    source per stroke.

    Returns:
        Number of profiles written, and how many of them come from ``fallback``.
    """
    import numpy as np

    from flat_segments.elevation import DEFAULT_SOURCE, RasterDem, raster_source, sample_stroke
    from flat_segments.export import ProfileTable, read_strokes, write_profiles
    from flat_segments.geometry import FloatArray, resample
    from flat_segments.network import Stroke
    from flat_segments.profile import fill_profile

    if source is None:
        source = raster_source(dem) or DEFAULT_SOURCE
    all_strokes = read_strokes(strokes)
    with RasterDem(dem) as sampler:
        z_raw = {s.id: sample_stroke(s.coords, sampler, params.profile) for s in all_strokes}
    source_by_stroke: dict[str, str] = {}
    if fallback is not None:
        fallback_source = raster_source(fallback) or DEFAULT_SOURCE

        def unfilled(stroke: Stroke, z: FloatArray) -> int:
            """Samples left without elevation once bridges, tunnels and short gaps are filled."""
            distances, _ = resample(stroke.coords, params.profile.step_m)
            filled, *_ = fill_profile(distances, z, stroke.structures(), params.profile)
            return int(np.isnan(filled).sum())

        with RasterDem(fallback) as sampler:
            for stroke in all_strokes:
                if not np.isnan(z_raw[stroke.id]).any():
                    continue
                missing = unfilled(stroke, z_raw[stroke.id])
                if missing == 0:
                    continue  # e.g. water under a bridge: the main profile is complete
                z = sample_stroke(stroke.coords, sampler, params.profile)
                if unfilled(stroke, z) < missing:
                    z_raw[stroke.id] = z
                    source_by_stroke[stroke.id] = fallback_source
    write_profiles(ProfileTable(z_raw, params.profile.step_m, source, source_by_stroke), out)
    return len(z_raw), len(source_by_stroke)


def run_detect(strokes: Path, profiles: Path, out: Path, params: PipelineParams) -> list[Segment]:
    """Detect, score and deduplicate segments; save them and the parameters used.

    Raises:
        ValueError: If the profiles were sampled with another step.
    """
    from flat_segments.export import read_profiles, read_strokes, write_segments

    table = read_profiles(profiles)
    if table.z_raw and abs(table.step_m - params.profile.step_m) > 1e-9:
        raise ValueError(
            f"profiles sampled every {table.step_m} m, expected {params.profile.step_m} m: "
            "rerun `elevation` with the same profile.step_m"
        )
    segments = detect_all(
        read_strokes(strokes), table.z_raw, params, table.elevation_source, table.source_by_stroke
    )
    write_segments(segments, out)
    params_sidecar(out).write_text(params_to_toml(params), encoding="utf-8")
    return segments


def run_export(segments: Path, out: Path, *, sample: bool = False) -> int:
    """Export segments to GeoJSON, with the detection parameters in the metadata.

    Returns:
        Number of exported segments.
    """
    from flat_segments.export import read_segments, segments_to_geojson, write_geojson

    all_segments = read_segments(segments)
    params: dict[str, Any] | None = None
    sidecar = params_sidecar(segments)
    if sidecar.exists():
        params = tomllib.loads(sidecar.read_text(encoding="utf-8"))
    write_geojson(segments_to_geojson(all_segments, sample=sample, params=params), out)
    return len(all_segments)


def run_publish(
    segments_files: Sequence[Path],
    out_dir: Path,
    *,
    sample: bool = False,
    previous: Path | None = None,
    tiles_url: str = "segments.pmtiles",
    index_url: str = "ids",
) -> tuple[int, TilesetFiles, Lineage | None]:
    """Publish one or more segments files (pilot, départements) as a tileset.

    The loops of each file (``loops.parquet`` next to it) are published with it.

    The parameters recorded next to each file (``segments.params.toml``) must
    be identical: a published set says how it was produced. With
    ``previous`` (a published folder, possibly ``out_dir`` itself), the ids
    are matched with the published ones so that links keep working
    (``lineage.py``). ``tiles_url`` and ``index_url`` say where the page will
    find the tiles and the index (``segments.json``).

    The files are processed one at a time (matching with the previous
    segments around them, then writing): the memory needed is that of the
    largest file, not of the whole set.

    Returns:
        ``(number of segments, files written, id matching or None)``.

    Raises:
        ValueError: If the files were produced with different parameters or
            share segment ids.
        TippecanoeError: If tippecanoe is missing or fails.
    """
    from flat_segments.lineage import Matcher, open_previous
    from flat_segments.tiles import TilesetWriter

    sidecars = {
        params_sidecar(f).read_text(encoding="utf-8")
        for f in segments_files
        if params_sidecar(f).exists()
    }
    if len(sidecars) > 1:
        raise ValueError("segments produced with different parameters cannot be published together")
    params = tomllib.loads(sidecars.pop()) if sidecars else None
    published = open_previous(previous) if previous is not None else None
    matcher = Matcher(published.live, published.redirects) if published else None
    writer = TilesetWriter(
        out_dir, sample=sample, params=params, tiles_url=tiles_url, index_url=index_url
    )
    renamed: list[dict[str, str]] = []  # per file: own id -> final id, when they differ
    own_ids: set[str] = set()
    try:
        for path in segments_files:
            segments = read_published(path)
            for segment in segments:
                if segment.id in own_ids:
                    raise ValueError(f"duplicate segment ids across the files: {segment.id}")
                own_ids.add(segment.id)
            final = segments
            if matcher is not None and published is not None and segments:
                around = published.segments_in(_bounds(segments))
                final = matcher.add(segments, around)
            renamed.append(
                {s.id: f.id for s, f in zip(segments, final, strict=True) if s.id != f.id}
            )
            writer.add(final)
        lineage = matcher.finish() if matcher is not None else None
        if lineage is not None:
            writer.add_redirects(lineage.redirects)
    except BaseException:
        writer.cleanup()
        raise

    def chunks() -> Iterator[list[Published]]:
        for path, ids in zip(segments_files, renamed, strict=True):
            yield [replace(s, id=ids[s.id]) if s.id in ids else s for s in read_published(path)]

    files = writer.close(chunks)
    return writer.total, files, lineage


def _bounds(segments: Sequence[Published]) -> tuple[float, float, float, float]:
    """Lambert-93 extent of segments."""
    import numpy as np

    coords = np.vstack([s.coords for s in segments])
    min_x, min_y = coords.min(axis=0)
    max_x, max_y = coords.max(axis=0)
    return float(min_x), float(min_y), float(max_x), float(max_y)


def run_all(
    paths: DataPaths,
    bbox: tuple[float, float, float, float] | None,
    params: PipelineParams,
    *,
    source: str | None = None,
    sample: bool = False,
) -> list[Segment]:
    """Run the four steps in sequence."""
    run_extract(paths.pbf, bbox, paths.strokes, params)
    run_elevation(paths.dem, paths.strokes, paths.profiles, params, source)
    segments = run_detect(paths.strokes, paths.profiles, paths.segments, params)
    run_export(paths.segments, paths.geojson, sample=sample)
    return segments
