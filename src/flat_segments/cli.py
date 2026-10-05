"""Command-line interface of the offline pipeline.

``extract`` -> ``elevation`` -> ``detect`` -> ``export`` (or ``pipeline`` for
all four); each step reads and writes files under ``data/`` (see
docs/architecture.md, section 3.1). Parameters come from the defaults, an
optional ``--config`` TOML file and ``--set key=value`` overrides.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from flat_segments import __version__
from flat_segments import pipeline as steps
from flat_segments.config import ConfigError, load_params, params_to_toml
from flat_segments.detect import Segment, SegmentKind
from flat_segments.download import (
    DEM_RESOLUTION_M,
    DEM_TILE_SIZE_M,
    GEOFABRIK_URL,
    WMS_LAYER,
    WMS_URL,
)
from flat_segments.params import PILOT_BBOX_WGS84, PipelineParams

PATHS = steps.DataPaths()
DEFAULT_BBOX = ",".join(str(v) for v in PILOT_BBOX_WGS84)


def _in(help_text: str) -> typer.models.OptionInfo:
    option: typer.models.OptionInfo = typer.Option(help=help_text, exists=True, dir_okay=False)
    return option


def _out(help_text: str) -> typer.models.OptionInfo:
    option: typer.models.OptionInfo = typer.Option(help=help_text, dir_okay=False)
    return option


PbfIn = Annotated[Path, _in("OSM extract (.osm.pbf or .osm).")]
DemIn = Annotated[Path, _in("DEM raster in Lambert-93 (GeoTIFF, VRT).")]
StrokesIn = Annotated[Path, _in("Strokes GeoParquet.")]
ProfilesIn = Annotated[Path, _in("Profiles Parquet.")]
SegmentsIn = Annotated[Path, _in("Segments GeoParquet.")]
StrokesOut = Annotated[Path, _out("Output strokes GeoParquet.")]
ProfilesOut = Annotated[Path, _out("Output profiles Parquet.")]
SegmentsOut = Annotated[Path, _out("Output segments GeoParquet.")]
GeojsonOut = Annotated[Path, _out("Output GeoJSON (WGS84).")]
BboxOpt = Annotated[str, typer.Option(help="WGS84 bbox: min_lon,min_lat,max_lon,max_lat.")]
SourceOpt = Annotated[
    str | None,
    typer.Option(help="Elevation source label (default: read from the DEM, else rge_alti_1m)."),
]
SampleOpt = Annotated[bool, typer.Option(help="Flag the data as fictitious.")]
ConfigOpt = Annotated[
    Path | None,
    typer.Option("--config", help="TOML parameters file.", exists=True, dir_okay=False),
]
SetOpt = Annotated[
    list[str] | None,
    typer.Option("--set", help="Parameter override, e.g. flat.max_local_grade_pct=1.5."),
]

app = typer.Typer(
    help="Offline pipeline: OSM network + DEM -> flat segments and climbs for runners.",
    no_args_is_help=True,
    add_completion=False,
)


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    """Parse ``"min_lon,min_lat,max_lon,max_lat"``.

    Raises:
        typer.BadParameter: If the value is malformed or inverted.
    """
    try:
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in value.split(","))
    except ValueError as error:
        raise typer.BadParameter("expected min_lon,min_lat,max_lon,max_lat") from error
    if min_lon >= max_lon or min_lat >= max_lat:
        raise typer.BadParameter("min values must be lower than max values")
    return min_lon, min_lat, max_lon, max_lat


def get_params(config: Path | None, overrides: list[str] | None) -> PipelineParams:
    """Load parameters, reporting configuration errors as CLI errors.

    Raises:
        typer.BadParameter: On an invalid file or override.
    """
    try:
        return load_params(config, overrides or ())
    except ConfigError as error:
        raise typer.BadParameter(str(error)) from error


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    """Offline pipeline: OSM network + DEM -> segments."""


@app.command()
def config(config: ConfigOpt = None, overrides: SetOpt = None) -> None:
    """Print the effective parameters as TOML (defaults, --config, --set)."""
    typer.echo(params_to_toml(get_params(config, overrides)), nl=False)


@app.command()
def extract(
    pbf: PbfIn = PATHS.pbf,
    bbox: BboxOpt = DEFAULT_BBOX,
    out: StrokesOut = PATHS.strokes,
    config: ConfigOpt = None,
    overrides: SetOpt = None,
) -> None:
    """Read OSM ways and chain them into strokes."""
    params = get_params(config, overrides)
    n_ways, n_strokes = steps.run_extract(pbf, parse_bbox(bbox), out, params)
    typer.echo(f"{n_ways} ways -> {n_strokes} strokes -> {out}")


@app.command()
def elevation(
    dem: DemIn = PATHS.dem,
    strokes: StrokesIn = PATHS.strokes,
    out: ProfilesOut = PATHS.profiles,
    source: SourceOpt = None,
    config: ConfigOpt = None,
    overrides: SetOpt = None,
) -> None:
    """Sample the DEM along every stroke."""
    params = get_params(config, overrides)
    count, _ = steps.run_elevation(dem, strokes, out, params, source)
    typer.echo(f"{count} profiles -> {out}")


def _report_detection(segments: list[Segment], out: Path) -> None:
    n_flat = sum(s.kind is SegmentKind.FLAT for s in segments)
    typer.echo(f"{n_flat} flat segments, {len(segments) - n_flat} climbs -> {out}")


@app.command()
def detect(
    strokes: StrokesIn = PATHS.strokes,
    profiles: ProfilesIn = PATHS.profiles,
    out: SegmentsOut = PATHS.segments,
    config: ConfigOpt = None,
    overrides: SetOpt = None,
) -> None:
    """Detect, score and deduplicate flat segments and climbs."""
    params = get_params(config, overrides)
    try:
        segments = steps.run_detect(strokes, profiles, out, params)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _report_detection(segments, out)


@app.command()
def loops(
    pbf: Annotated[
        Path,
        typer.Option(
            help="OSM extract with all its tags (regional; the clipped pilot file only has ways).",
            exists=True,
            dir_okay=False,
        ),
    ],
    bbox: BboxOpt = DEFAULT_BBOX,
    out: Annotated[Path, _out("Output loops GeoParquet.")] = PATHS.loops,
) -> None:
    """Find the running tracks (loops) mapped in OSM."""
    n_areas, n_loops = steps.run_loops(pbf, parse_bbox(bbox), out)
    typer.echo(f"{n_areas} sports areas -> {n_loops} tracks -> {out}")


@app.command()
def export(
    segments: SegmentsIn = PATHS.segments,
    out: GeojsonOut = PATHS.geojson,
    sample: SampleOpt = False,
) -> None:
    """Export segments to GeoJSON (inspection; the page reads export-pmtiles)."""
    count = steps.run_export(segments, out, sample=sample)
    typer.echo(f"{count} segments -> {out}")


@app.command("export-pmtiles")
def export_pmtiles(
    segments: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Segments GeoParquet files (default: the pilot run); the loops.parquet "
            "next to each one is published with it.",
            exists=True,
        ),
    ] = None,
    out_dir: Annotated[
        Path, typer.Option(help="Published folder (segments.pmtiles, segments.json, ids/).")
    ] = PATHS.web_data,
    sample: SampleOpt = False,
    previous: Annotated[
        Path | None,
        typer.Option(
            help="Previously published folder: keep its ids (may be --out-dir itself).",
            exists=True,
            file_okay=False,
        ),
    ] = None,
    tiles_url: Annotated[
        str,
        typer.Option(help="Address of the tiles in segments.json (relative, or absolute: R2)."),
    ] = "segments.pmtiles",
    index_url: Annotated[
        str, typer.Option(help="Address of the id index folder in segments.json.")
    ] = "ids",
) -> None:
    """Publish segments as vector tiles for the web page (needs tippecanoe)."""
    from flat_segments.tiles import TippecanoeError

    try:
        count, files, lineage = steps.run_publish(
            segments or [PATHS.segments],
            out_dir,
            sample=sample,
            previous=previous,
            tiles_url=tiles_url,
            index_url=index_url,
        )
    except (ValueError, TippecanoeError) as error:
        typer.echo(f"Export failed: {error}", err=True)
        raise typer.Exit(1) from error
    if lineage is not None:
        typer.echo(f"ids: {lineage.summary}")
    typer.echo(f"{count} segments -> {files.pmtiles}, {files.metadata}, {files.index_dir}/")


@app.command()
def pipeline(
    pbf: PbfIn = PATHS.pbf,
    dem: DemIn = PATHS.dem,
    bbox: BboxOpt = DEFAULT_BBOX,
    out: GeojsonOut = PATHS.geojson,
    source: SourceOpt = None,
    sample: SampleOpt = False,
    config: ConfigOpt = None,
    overrides: SetOpt = None,
) -> None:
    """Run extract, elevation, detect and export in sequence (default paths)."""
    params = get_params(config, overrides)
    paths = steps.DataPaths(pbf=pbf, dem=dem, geojson=out)
    try:
        segments = steps.run_all(paths, parse_bbox(bbox), params, source=source, sample=sample)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _report_detection(segments, paths.segments)
    typer.echo(f"GeoJSON -> {out}")


@app.command("download-osm")
def download_osm(
    url: Annotated[
        str, typer.Option(help="Extract URL (Geofabrik or a mirror publishing a .md5).")
    ] = GEOFABRIK_URL,
    out_dir: Annotated[Path, typer.Option(help="Download folder.", file_okay=False)] = Path(
        "data/raw"
    ),
    clip: Annotated[bool, typer.Option(help="Also write the clipped pilot extract.")] = True,
    bbox: BboxOpt = DEFAULT_BBOX,
    clipped: Annotated[Path, _out("Clipped extract (with --clip).")] = PATHS.pbf,
    force: Annotated[bool, typer.Option(help="Download even if up to date.")] = False,
) -> None:
    """Download the OSM extract (MD5-checked), then clip it to the bbox."""
    from flat_segments import download as dl
    from flat_segments.osm import clip_osm

    try:
        path = dl.download_osm(out_dir, url, dl.urlopen, force=force)
    except dl.DownloadError as error:
        typer.echo(f"Download failed: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"OSM extract -> {path}")
    if clip:
        n_ways, n_nodes = clip_osm(path, clipped, parse_bbox(bbox))
        typer.echo(f"{n_ways} ways, {n_nodes} nodes -> {clipped}")


@app.command("download-dem")
def download_dem(
    bbox: BboxOpt = DEFAULT_BBOX,
    out_dir: Annotated[
        Path, typer.Option(help="Output folder.", file_okay=False)
    ] = PATHS.dem.parent,
    tile_size_m: Annotated[float, typer.Option(help="Tile size (metres).")] = DEM_TILE_SIZE_M,
    resolution_m: Annotated[float, typer.Option(help="Pixel size (metres).")] = DEM_RESOLUTION_M,
    wms_url: Annotated[str, typer.Option(help="WMS endpoint.")] = WMS_URL,
    layer: Annotated[str, typer.Option(help="WMS elevation layer.")] = WMS_LAYER,
    force: Annotated[bool, typer.Option(help="Download tiles already on disk.")] = False,
) -> None:
    """Download elevation tiles over the bbox (WMS, LiDAR HD) and assemble a VRT."""
    from flat_segments import download as dl
    from flat_segments.elevation import bbox_to_lambert93

    def progress(index: int, total: int, tile: dl.DemTile) -> None:
        typer.echo(f"tile {index}/{total} {tile.name}")

    try:
        vrt = dl.download_dem(
            bbox_to_lambert93(parse_bbox(bbox)),
            out_dir,
            dl.urlopen,
            tile_size_m=tile_size_m,
            resolution_m=resolution_m,
            base_url=wms_url,
            layer=layer,
            vrt_name=PATHS.dem.name,
            force=force,
            on_tile=progress,
        )
    except (dl.DownloadError, ValueError) as error:
        typer.echo(f"Download failed: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"DEM -> {vrt}")


DEPARTMENTS_FILE = Path("data/raw/departements.geojson")
DEPARTMENTS_ROOT = Path("data/departments")
RegionalPbf = Annotated[
    Path,
    typer.Option(help="Regional OSM extract covering the département(s) and margin.", exists=True),
]
DepartmentsFileOpt = Annotated[
    Path, typer.Option(help="Outlines from download-departments.", exists=True, dir_okay=False)
]
RootOpt = Annotated[Path, typer.Option(help="Output folder (one sub-folder per département).")]
KeepDemOpt = Annotated[bool, typer.Option(help="Keep the DEM tiles after sampling.")]
ForceOpt = Annotated[bool, typer.Option(help="Start again from scratch.")]


@app.command("download-departments")
def download_departments(
    out: Annotated[Path, _out("Output GeoJSON (WGS84).")] = DEPARTMENTS_FILE,
) -> None:
    """Download the outlines of the French départements (IGN Admin Express)."""
    from flat_segments import departments as dep
    from flat_segments import download as dl

    try:
        path = dep.download_departments(out, dl.urlopen)
    except dl.DownloadError as error:
        typer.echo(f"Download failed: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"départements -> {path}")


def _run_departments(
    codes: list[str],
    pbf: Path,
    departments_file: Path,
    root: Path,
    params: PipelineParams,
    keep_dem: bool,
    force: bool,
) -> dict[str, dict[str, object] | str]:
    from flat_segments import download as dl
    from flat_segments.batch import run_department

    states: dict[str, dict[str, object] | str] = {}
    for code in codes:
        try:
            states[code] = run_department(
                code,
                pbf,
                departments_file,
                root,
                params,
                opener=dl.urlopen,
                keep_dem=keep_dem,
                force=force,
                log=typer.echo,
            )
        except Exception as error:  # one failure must not stop the batch
            typer.echo(f"{code}: failed: {error}", err=True)
            states[code] = f"{type(error).__name__}: {error}"
    return states


@app.command()
def department(
    code: Annotated[str, typer.Argument(help="INSEE code, e.g. 31.")],
    pbf: RegionalPbf,
    departments_file: DepartmentsFileOpt = DEPARTMENTS_FILE,
    root: RootOpt = DEPARTMENTS_ROOT,
    keep_dem: KeepDemOpt = False,
    force: ForceOpt = False,
    config: ConfigOpt = None,
    overrides: SetOpt = None,
) -> None:
    """Process one département (resumable): strokes, DEM, profiles, segments, loops."""
    departments([code], pbf, departments_file, root, keep_dem, force, config, overrides)


@app.command()
def departments(
    codes: Annotated[list[str], typer.Argument(help="INSEE codes, e.g. 09 12 31.")],
    pbf: RegionalPbf,
    departments_file: DepartmentsFileOpt = DEPARTMENTS_FILE,
    root: RootOpt = DEPARTMENTS_ROOT,
    keep_dem: KeepDemOpt = False,
    force: ForceOpt = False,
    config: ConfigOpt = None,
    overrides: SetOpt = None,
) -> None:
    """Process several départements in turn; a failure does not stop the others."""
    from flat_segments.batch import summary_table

    params = get_params(config, overrides)
    states = _run_departments(codes, pbf, departments_file, root, params, keep_dem, force)
    typer.echo(summary_table(states), nl=False)
    if any(isinstance(state, str) for state in states.values()):
        raise typer.Exit(1)


@app.command("department-codes")
def department_codes(
    codes: Annotated[
        list[str] | None, typer.Argument(help="INSEE codes (default: all of metropolitan France).")
    ] = None,
    departments_file: DepartmentsFileOpt = DEPARTMENTS_FILE,
) -> None:
    """Print département codes as a JSON list (checked against the outlines file)."""
    import json

    from flat_segments import departments as dep

    known = dep.department_codes(departments_file, overseas=True)
    unknown = [c for c in codes or [] if c not in known]
    if unknown:
        raise typer.BadParameter(f"unknown départements: {' '.join(unknown)}")
    typer.echo(json.dumps(codes or dep.department_codes(departments_file)))


@app.command("department-summary")
def department_summary(
    states: Annotated[
        list[Path], typer.Argument(help="state.json files of processed départements.", exists=True)
    ],
) -> None:
    """Print the Markdown summary table of processed départements."""
    import json

    from flat_segments.batch import summary_table

    loaded = [json.loads(path.read_text(encoding="utf-8")) for path in states]
    table = {state["code"]: state for state in sorted(loaded, key=lambda state: state["code"])}
    typer.echo(summary_table(table), nl=False)


@app.command("renumber-osm")
def renumber_osm(
    pbf: Annotated[Path, typer.Argument(help="OSM extract.", exists=True, dir_okay=False)],
    out: Annotated[Path, typer.Argument(help="Output extract.", dir_okay=False)],
) -> None:
    """Number the nodes from 1, before cut-osm on a large file (needs osmium-tool)."""
    from flat_segments.osm_extracts import OsmiumError, renumber_nodes

    try:
        renumber_nodes(pbf, out)
    except OsmiumError as error:
        typer.echo(f"Renumbering failed: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"{pbf} -> {out} ({out.stat().st_size / 1e6:.1f} MB)")


@app.command("cut-osm")
def cut_osm(
    pbf: Annotated[Path, typer.Argument(help="National OSM extract.", exists=True, dir_okay=False)],
    codes: Annotated[
        list[str] | None, typer.Argument(help="INSEE codes (default: all of metropolitan France).")
    ] = None,
    departments_file: DepartmentsFileOpt = DEPARTMENTS_FILE,
    out_dir: Annotated[Path, typer.Option(help="Folder of the extracts (CODE.osm.pbf).")] = Path(
        "data/osm"
    ),
    batch_size: Annotated[
        int, typer.Option(help="Départements cut per pass over the national file.", min=1)
    ] = 12,
) -> None:
    """Cut one OSM extract per département (needs osmium-tool)."""
    from flat_segments import departments as dep
    from flat_segments.osm_extracts import OsmiumError, cut_extracts

    try:
        selected = dep.load_departments(
            departments_file, codes or dep.department_codes(departments_file)
        )
    except KeyError as error:
        raise typer.BadParameter(str(error)) from error

    def report(files: list[Path]) -> None:
        for path in files:
            typer.echo(f"{path} ({path.stat().st_size / 1e6:.1f} MB)")

    try:
        cut_extracts(pbf, selected, out_dir, batch_size=batch_size, on_batch=report)
    except OsmiumError as error:
        typer.echo(f"Cut failed: {error}", err=True)
        raise typer.Exit(1) from error


def _detection_params(
    segments: Path, config: Path | None, overrides: list[str] | None
) -> PipelineParams:
    """Parameters used to detect ``segments`` (their sidecar) unless --config is given."""
    sidecar = steps.params_sidecar(segments)
    return get_params(config or (sidecar if sidecar.exists() else None), overrides)


@app.command()
def report(
    segments: SegmentsIn = PATHS.segments,
    out: Annotated[Path | None, _out("Write the Markdown report here.")] = None,
) -> None:
    """Summarise a segments file (counts, lengths, targets, crossings, surfaces)."""
    from flat_segments.calibration import summarize
    from flat_segments.export import read_segments

    text = summarize(read_segments(segments))
    if out is None:
        typer.echo(text, nl=False)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        typer.echo(f"report -> {out}")


@app.command()
def compare(
    a: Annotated[Path, typer.Argument(help="Reference segments GeoParquet.", exists=True)],
    b: Annotated[Path, typer.Argument(help="Segments GeoParquet to compare.", exists=True)],
    buffer_m: Annotated[float, typer.Option(help="Distance under which a point matches.")] = 10.0,
    missing: Annotated[bool, typer.Option(help="List the ids of A missing in B.")] = False,
) -> None:
    """Compare two segments files: counts, km, overlap in both directions."""
    from flat_segments.calibration import compare as compare_runs
    from flat_segments.calibration import format_comparison
    from flat_segments.export import read_segments

    rows = compare_runs(read_segments(a), read_segments(b), buffer_m)
    typer.echo(format_comparison(rows, "A", "B"), nl=False)
    if missing:
        for row in rows:
            for segment_id in row.missing_in_b:
                typer.echo(segment_id)


@app.command()
def sweep(
    key: Annotated[str, typer.Argument(help="Parameter, e.g. flat.max_local_grade_pct.")],
    values: Annotated[list[str], typer.Argument(help="Values to try (TOML syntax).")],
    strokes: StrokesIn = PATHS.strokes,
    profiles: ProfilesIn = PATHS.profiles,
    config: ConfigOpt = None,
    overrides: SetOpt = None,
) -> None:
    """Rerun detection for several values of one parameter and compare counts."""
    from flat_segments import calibration
    from flat_segments.export import read_profiles, read_strokes

    params = get_params(config, overrides)
    table = read_profiles(profiles)
    try:
        rows = calibration.sweep(
            read_strokes(strokes), table.z_raw, params, key, values, table.elevation_source
        )
    except ConfigError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(calibration.format_sweep(key, rows), nl=False)


@app.command()
def inspect(
    segment_id: Annotated[str, typer.Argument(help="Segment id, e.g. flat-3fa2b1c9d0e4.")],
    segments: SegmentsIn = PATHS.segments,
    strokes: StrokesIn = PATHS.strokes,
    profiles: ProfilesIn = PATHS.profiles,
    out: Annotated[Path | None, _out("PNG path (default: next to the segments).")] = None,
    config: ConfigOpt = None,
    overrides: SetOpt = None,
) -> None:
    """Plot the elevation and grade profile around one segment (needs matplotlib)."""
    from flat_segments.calibration import plot_segment
    from flat_segments.export import read_profiles, read_segments, read_strokes
    from flat_segments.profile import build_profile

    segment = next((s for s in read_segments(segments) if s.id == segment_id), None)
    if segment is None:
        raise typer.BadParameter(f"no segment {segment_id!r} in {segments}")
    stroke = next(s for s in read_strokes(strokes) if s.id == segment.stroke_id)
    params = _detection_params(segments, config, overrides)
    z_raw = read_profiles(profiles).z_raw[stroke.id]
    profile = build_profile(stroke.coords, z_raw, params.profile, stroke.structures())
    path = out or segments.parent / "inspect" / f"{segment_id}.png"
    typer.echo(f"profile -> {plot_segment(segment, stroke, profile, params, path)}")


@app.command("validation-sheet")
def validation_sheet(
    segments: SegmentsIn = PATHS.segments,
    count: Annotated[int, typer.Option(help="Number of segments to check.", min=1)] = 20,
    out: Annotated[Path, _out("Markdown sheet.")] = Path("docs/validation/pilot.md"),
    site_url: Annotated[str, typer.Option(help="Base URL of the map page.")] = (
        "https://jsilobre.github.io/spot-fractionne/"
    ),
) -> None:
    """Write the field validation sheet (a representative sample of segments)."""
    from flat_segments import calibration
    from flat_segments.export import read_segments

    sample = calibration.select_for_validation(read_segments(segments), count)
    text = calibration.validation_sheet(sample, site_url=site_url, source=str(segments))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"{len(sample)} segments -> {out}")
