from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest
from pyproj import Transformer
from shapely.geometry import LineString, box, mapping
from typer.testing import CliRunner

from flat_segments import batch, cli, download
from flat_segments.config import load_params
from flat_segments.departments import load_department
from flat_segments.detect import SegmentKind
from flat_segments.export import read_loops, read_segments
from flat_segments.params import WEB_CRS, WORK_CRS, PipelineParams
from tests.test_departments import collection
from tests.test_download import FakeWeb
from tests.test_loops import stadium

# A small "département" east of Labège, and three cycleways of 2 km:
# A inside it, B in the 2 km margin north of it, C far away.
OUTLINE = box(1.52, 43.52, 1.555, 43.54)
LATS = {1: 43.530, 2: 43.548, 3: 43.600}
LONS = np.linspace(1.525, 1.550, 11)


def osm_xml() -> str:
    nodes, ways = [], []
    for way_id, lat in LATS.items():
        refs = []
        for i, lon in enumerate(LONS):
            node_id = way_id * 100 + i
            nodes.append(f'<node id="{node_id}" lat="{lat}" lon="{lon:.5f}" version="1"/>')
            refs.append(f'<nd ref="{node_id}"/>')
        ways.append(
            f'<way id="{way_id}" version="1">{"".join(refs)}'
            '<tag k="highway" v="cycleway"/><tag k="surface" v="asphalt"/></way>'
        )
    return f'<?xml version="1.0"?><osm version="0.6">{"".join(nodes)}{"".join(ways)}</osm>'


def gentle_wms(url: str) -> bytes:
    """Answer a GetMap request with a 0.2 % slope (flat) towards the east."""
    query = parse_qs(urlsplit(url).query)
    min_x, _, max_x, _ = (float(v) for v in query["BBOX"][0].split(","))
    width, height = int(query["WIDTH"][0]), int(query["HEIGHT"][0])
    xs = min_x + (max_x - min_x) / width * (np.arange(width) + 0.5)
    grid = np.broadcast_to(150.0 + 0.002 * (xs - 580_000), (height, width))
    return grid.astype("<f4").tobytes()


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, Path]:
    pbf = tmp_path / "region.osm"
    pbf.write_text(osm_xml())
    outlines = tmp_path / "departements.geojson"
    outlines.write_bytes(collection(("31", "Test", mapping(OUTLINE))))
    return pbf, outlines


def run(
    inputs: tuple[Path, Path],
    root: Path,
    web: FakeWeb,
    params: PipelineParams | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    pbf, outlines = inputs
    return batch.run_department(
        "31",
        pbf,
        outlines,
        root,
        params or load_params(),
        opener=web,
        dem_resolution_m=20.0,
        log=lambda _: None,
        **kwargs,
    )


def test_department_keeps_the_segments_whose_midpoint_is_inside(
    inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    web = FakeWeb({download.WMS_URL: gentle_wms})
    state = run(inputs, tmp_path / "out", web)
    paths = batch.department_paths(tmp_path / "out" / "31")
    assert list(state["steps"]) == list(batch.STEPS)
    assert state["steps"]["strokes"]["ways"] == 2  # C lies beyond the margin
    assert state["steps"]["segments"]["detected"] == 2  # flats on A and B
    [segment] = read_segments(paths.segments)
    assert segment.kind is SegmentKind.FLAT
    outline = load_department(inputs[1], "31").outline_l93()
    assert outline.contains(LineString(segment.coords))  # A, not B
    assert segment.elevation_source == "lidar_hd"
    assert not paths.dem_dir.exists()  # deleted once sampled
    assert paths.params.exists()
    assert len(web.requests) == state["steps"]["dem"]["tiles"] > 0  # no fallback tile
    assert (state["steps"]["dem"]["fallback_tiles"], state["steps"]["profiles"]["fallback"]) == (
        0,
        0,
    )


def patchy_wms(missing_m: float) -> Callable[[str], bytes]:
    """``gentle_wms``, without LiDAR HD over ``missing_m`` west of the middle of the ways."""
    middle_x, _ = Transformer.from_crs(WEB_CRS, WORK_CRS, always_xy=True).transform(1.5375, 43.53)

    def answer(url: str) -> bytes:
        query = parse_qs(urlsplit(url).query)
        if query["LAYERS"][0] == download.WMS_FALLBACK_LAYER:
            return gentle_wms(url)
        min_x, _, max_x, _ = (float(v) for v in query["BBOX"][0].split(","))
        width = int(query["WIDTH"][0])
        xs = min_x + (max_x - min_x) / width * (np.arange(width) + 0.5)
        grid = np.frombuffer(gentle_wms(url), "<f4").reshape(-1, width).copy()
        grid[:, (xs < middle_x) & (xs > middle_x - missing_m)] = -9999.0
        return grid.astype("<f4").tobytes()

    return answer


def test_department_falls_back_on_the_rge_alti_where_the_lidar_hd_is_missing(
    inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    state = run(inputs, tmp_path, FakeWeb({download.WMS_URL: patchy_wms(5000.0)}))
    assert state["steps"]["dem"]["fallback_tiles"] > 0
    assert state["steps"]["profiles"]["fallback"] == 2  # A and B, each half missing
    [segment] = read_segments(batch.department_paths(tmp_path / "31").segments)
    assert segment.elevation_source == "rge_alti_wms"  # the whole stroke
    assert segment.length_m > 1500


def test_department_keeps_the_lidar_hd_across_gaps_it_can_fill(
    inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    # 20 m pixels here: a missing pixel leaves about 60 m without elevation.
    params = load_params(None, ["profile.max_gap_fill_m=100"])
    state = run(inputs, tmp_path, FakeWeb({download.WMS_URL: patchy_wms(8.0)}), params)
    assert state["steps"]["dem"]["fallback_tiles"] > 0  # fetched, but not needed
    assert state["steps"]["profiles"]["fallback"] == 0
    [segment] = read_segments(batch.department_paths(tmp_path / "31").segments)
    assert segment.elevation_source == "lidar_hd"


def test_department_resumes_and_refuses_other_parameters(
    inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    web = FakeWeb({download.WMS_URL: gentle_wms})
    first = run(inputs, tmp_path, web, keep_dem=True)
    requests = len(web.requests)
    assert batch.department_paths(tmp_path / "31").dem.exists()
    again = run(inputs, tmp_path, web)
    assert again == first  # nothing recomputed
    assert len(web.requests) == requests
    other = load_params(None, ["flat.max_local_grade_pct=1.5"])
    with pytest.raises(batch.StateError, match="--force"):
        run(inputs, tmp_path, web, params=other)
    redone = run(inputs, tmp_path, web, params=other, force=True)
    assert redone["params"] != first["params"]
    assert len(web.requests) > requests  # the DEM was deleted by the second run


def test_departments_command_reports_failures_and_continues(
    inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(download, "urlopen", FakeWeb({download.WMS_URL: gentle_wms}))
    pbf, outlines = inputs
    args = ["--pbf", str(pbf), "--departments-file", str(outlines), "--root", str(tmp_path)]
    result = CliRunner().invoke(cli.app, ["departments", "99", "31", *args])
    assert result.exit_code == 1
    assert "| 99 | | | | | | | | error: KeyError" in result.output
    assert "| 31 Test |" in result.output
    assert (tmp_path / "31" / "segments.parquet").exists()


def test_department_keeps_the_tracks_whose_midpoint_is_inside(
    inputs: tuple[Path, Path], tmp_path: Path
) -> None:
    to_l93 = Transformer.from_crs(WEB_CRS, WORK_CRS, always_xy=True)
    to_wgs84 = Transformer.from_crs(WORK_CRS, WEB_CRS, always_xy=True)
    nodes, ways = [], []
    for way_id, lat in ((50, 43.525), (51, 43.546)):  # inside, then in the margin
        center = to_l93.transform(1.53, lat)
        ring = stadium(center)[:-1]
        refs = []
        for i, (x, y) in enumerate(ring):
            lon, node_lat = to_wgs84.transform(x, y)
            nodes.append(f'<node id="{way_id * 1000 + i}" lat="{node_lat:.7f}" lon="{lon:.7f}"/>')
            refs.append(f'<nd ref="{way_id * 1000 + i}"/>')
        refs.append(refs[0])
        ways.append(
            f'<way id="{way_id}">{"".join(refs)}'
            '<tag k="leisure" v="track"/><tag k="sport" v="athletics"/></way>'
        )
    pbf, outlines = inputs
    xml = osm_xml().replace("</osm>", f"{''.join(nodes)}{''.join(ways)}</osm>")
    pbf.write_text(xml)
    state = run((pbf, outlines), tmp_path / "out", FakeWeb({download.WMS_URL: gentle_wms}))
    loops = state["steps"]["loops"]
    assert (loops["areas"], loops["tracks"]) == (2, 1)
    [track] = read_loops(batch.department_paths(tmp_path / "out" / "31").loops)
    assert track.osm_id == "way/50"
