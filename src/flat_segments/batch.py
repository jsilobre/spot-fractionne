"""Batch production by département (phase 2.1, docs/phase-2/2.1-departement.md).

A département is processed in six resumable steps, on its outline grown by
a margin (:data:`~flat_segments.departments.BORDER_MARGIN_M`):

1. ``strokes``: ways of a regional OSM extract inside the grown outline;
2. ``dem``: DEM tiles touching the grown outline (one national 4 km grid);
3. ``profiles``: elevation sampled along the strokes;
4. ``segments``: detection, then the segments whose midpoint lies in the
   département itself;
5. ``loops``: running tracks mapped in OSM (no elevation needed), kept the
   same way;
6. ``circuits``: flat loops of the network whose inside point lies in the
   département; their elevation is read on the DEM tiles touching them
   (fetched again if the DEM is gone). The DEM can then go.

Outputs go to ``<root>/<code>/``. ``state.json`` records each finished step
(duration, counts) and the parameters used: a new run resumes after the last
finished step, and refuses to mix parameters.
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import shapely

from flat_segments.config import params_to_toml
from flat_segments.departments import (
    BORDER_MARGIN_M,
    Department,
    load_department,
    owned_segments,
    transform_geometry,
)
from flat_segments.detect import SegmentKind, detect_all
from flat_segments.download import (
    DEM_RESOLUTION_M,
    DEM_TILE_SIZE_M,
    WMS_FALLBACK_LAYER,
    Opener,
    download_dem,
    fallback_vrt_path,
    snap_bounds,
    urlopen,
)
from flat_segments.params import WEB_CRS, WORK_CRS, PipelineParams

STEPS: Final = ("strokes", "dem", "profiles", "segments", "loops", "circuits")
#: Margin of the DEM fetched around circuits (``circuits`` step).
CIRCUIT_DEM_MARGIN_M: Final = 50.0


class StateError(RuntimeError):
    """The saved state cannot be resumed with these parameters."""


@dataclass(frozen=True)
class DepartmentPaths:
    """Files of one département (see :func:`department_paths`)."""

    root: Path
    strokes: Path
    dem_dir: Path
    dem: Path
    profiles: Path
    segments: Path
    loops: Path
    circuits: Path
    params: Path
    state: Path


def department_paths(root: Path) -> DepartmentPaths:
    """Files of the département whose folder is ``root``."""
    return DepartmentPaths(
        root=root,
        strokes=root / "strokes.parquet",
        dem_dir=root / "dem",
        dem=root / "dem" / "dem.vrt",
        profiles=root / "profiles.parquet",
        segments=root / "segments.parquet",
        loops=root / "loops.parquet",
        circuits=root / "circuits.parquet",
        params=root / "segments.params.toml",
        state=root / "state.json",
    )


def _load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return state
    return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    """Write the state atomically (an interrupted run keeps the previous one)."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _dir_size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


def run_department(
    code: str,
    pbf: Path,
    departments_file: Path,
    root: Path,
    params: PipelineParams,
    *,
    opener: Opener = urlopen,
    keep_dem: bool = False,
    force: bool = False,
    margin_m: float = BORDER_MARGIN_M,
    dem_resolution_m: float = DEM_RESOLUTION_M,
    dem_tile_size_m: float = DEM_TILE_SIZE_M,
    fallback_layer: str | None = WMS_FALLBACK_LAYER,
    log: Callable[[str], Any] = print,
) -> dict[str, Any]:
    """Process one département, resuming after its last finished step.

    Args:
        code: INSEE code of the département (``"31"``).
        pbf: OSM extract covering the département and its margin (regional).
        departments_file: Outlines written by ``download-departments``.
        root: Parent folder of the département folders.
        params: Pipeline parameters (all steps).
        opener: URL opener for the DEM service (tests inject a fake one).
        keep_dem: Keep the DEM tiles at the end (deleted by default).
        force: Start again from scratch (the state is discarded).
        margin_m: Margin around the outline, so that border ways are not cut.
        dem_resolution_m: DEM pixel size.
        dem_tile_size_m: DEM tile size (one national grid of such tiles).
        fallback_layer: WMS layer fetched where the DEM has nodata (RGE ALTI,
            ADR 0007); a stroke with missing elevations is sampled on it.
        log: Progress messages.

    Returns:
        The state: ``code``, ``name`` and, for each step, its duration and
        counts.

    Raises:
        StateError: If a previous run used other parameters (use ``force``).
    """
    from flat_segments.circuits import find_candidates, select_circuits
    from flat_segments.export import (
        read_profiles,
        read_strokes,
        write_loops,
        write_segments,
        write_strokes,
    )
    from flat_segments.network import build_strokes
    from flat_segments.osm import read_ways
    from flat_segments.pipeline import (
        circuit_grades,
        read_loops_from_osm,
        read_setting_areas,
        run_elevation,
    )

    department: Department = load_department(departments_file, code)
    paths = department_paths(root / code)
    paths.root.mkdir(parents=True, exist_ok=True)
    params_toml = params_to_toml(params)
    state = {} if force else _load_state(paths.state)
    if state and state.get("params") != params_toml:
        raise StateError(
            f"{paths.state}: started with other parameters; rerun with --force to start again"
        )
    if not state:
        state = {"code": code, "name": department.name, "params": params_toml, "steps": {}}
    steps: dict[str, Any] = state["steps"]
    area_l93 = department.work_area_l93(margin_m)
    area_wgs84 = transform_geometry(area_l93, WORK_CRS, WEB_CRS)

    def finish(step: str, start: float, **counts: Any) -> None:
        steps[step] = {"seconds": round(time.monotonic() - start, 1), **counts}
        _save_state(paths.state, state)
        log(f"{code} {step}: " + ", ".join(f"{k}={v}" for k, v in steps[step].items()))

    if "strokes" not in steps:
        start = time.monotonic()
        ways = read_ways(pbf, area=area_wgs84)
        strokes = build_strokes(ways, params.network)
        write_strokes(strokes, paths.strokes)
        finish("strokes", start, ways=len(ways), strokes=len(strokes))

    if "profiles" not in steps:
        if "dem" in steps and not paths.dem.exists():
            del steps["dem"]  # removed since: download it again (tiles on disk are kept)
        if "dem" not in steps:
            start = time.monotonic()
            bounds = snap_bounds(area_l93.bounds, dem_tile_size_m)
            n_tiles = 0

            def count(index: int, total: int, _tile: object) -> None:
                nonlocal n_tiles
                n_tiles = total
                if index % 50 == 0:
                    log(f"{code} dem: tile {index}/{total}")

            download_dem(
                bounds,
                paths.dem_dir,
                opener,
                tile_size_m=dem_tile_size_m,
                resolution_m=dem_resolution_m,
                area=area_l93,
                fallback_layer=fallback_layer,
                vrt_name=paths.dem.name,
                on_tile=count,
            )
            fallback = fallback_vrt_path(paths.dem)
            n_fallback = len(list((paths.dem_dir / "fallback").glob("*.tif")))
            finish(
                "dem",
                start,
                tiles=n_tiles,
                fallback_tiles=n_fallback if fallback.exists() else 0,
                mb=round(_dir_size_mb(paths.dem_dir)),
            )
        start = time.monotonic()
        fallback = fallback_vrt_path(paths.dem)
        n_profiles, n_on_fallback = run_elevation(
            paths.dem,
            paths.strokes,
            paths.profiles,
            params,
            fallback=fallback if fallback.exists() else None,
        )
        finish("profiles", start, profiles=n_profiles, fallback=n_on_fallback)

    if "segments" not in steps:
        start = time.monotonic()
        table = read_profiles(paths.profiles)
        detected = detect_all(
            read_strokes(paths.strokes),
            table.z_raw,
            params,
            table.elevation_source,
            table.source_by_stroke,
        )
        kept = owned_segments(detected, department.outline_l93())
        write_segments(kept, paths.segments)
        paths.params.write_text(params_toml, encoding="utf-8")
        finish(
            "segments",
            start,
            detected=len(detected),
            flats=sum(s.kind is SegmentKind.FLAT for s in kept),
            climbs=sum(s.kind is SegmentKind.CLIMB for s in kept),
            km=round(sum(s.length_m for s in kept) / 1000, 1),
        )

    if "loops" not in steps:
        start = time.monotonic()
        n_areas, loops = read_loops_from_osm(pbf, area=area_wgs84)
        owned = owned_segments(loops, department.outline_l93())
        write_loops(owned, paths.loops)
        finish("loops", start, areas=n_areas, tracks=len(owned))

    if "circuits" not in steps:
        start = time.monotonic()
        candidates = find_candidates(
            read_ways(pbf, area=area_wgs84),
            read_setting_areas(pbf, area=area_wgs84),
            department.outline_l93(),
        )
        grades: list[float | None] = []
        if candidates:
            around = shapely.union_all([c.polygon.buffer(CIRCUIT_DEM_MARGIN_M) for c in candidates])
            vrt = download_dem(
                snap_bounds(around.bounds, dem_tile_size_m),
                paths.dem_dir,
                opener,
                tile_size_m=dem_tile_size_m,
                resolution_m=dem_resolution_m,
                area=around,
                fallback_layer=fallback_layer,
                vrt_name="circuits.vrt",
            )
            fallback = fallback_vrt_path(vrt)
            grades = circuit_grades(
                candidates, vrt, params, fallback if fallback.exists() else None
            )
        circuits = select_circuits(candidates, grades)
        write_loops(circuits, paths.circuits)
        finish("circuits", start, candidates=len(candidates), circuits=len(circuits))

    if not keep_dem and paths.dem_dir.exists():
        shutil.rmtree(paths.dem_dir)
    return state


def summary_table(states: dict[str, dict[str, Any] | str]) -> str:
    """Markdown table of a batch: duration and counts, or the error."""
    lines = [
        "| département | strokes | DEM tiles | flats | climbs | km | tracks | circuits"
        " | minutes | status |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for code, state in states.items():
        if isinstance(state, str):
            lines.append(f"| {code} | | | | | | | | | error: {state} |")
            continue
        steps = state["steps"]
        minutes = sum(s.get("seconds", 0) for s in steps.values()) / 60
        segments = steps.get("segments", {})
        lines.append(
            f"| {code} {state.get('name', '')} | {steps.get('strokes', {}).get('strokes', '')}"
            f" | {steps.get('dem', {}).get('tiles', '')} | {segments.get('flats', '')}"
            f" | {segments.get('climbs', '')} | {segments.get('km', '')}"
            f" | {steps.get('loops', {}).get('tracks', '')}"
            f" | {steps.get('circuits', {}).get('circuits', '')} | {minutes:.1f} | ok |"
        )
    return "\n".join(lines) + "\n"
