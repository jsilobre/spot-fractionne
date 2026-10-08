import json
from pathlib import Path

import numpy as np
import pytest
from pyproj import Transformer
from shapely.geometry import Polygon, box, mapping

from flat_segments import batch, circuits, download
from flat_segments.circuits import Candidate, SettingArea, find_candidates, select_circuits
from flat_segments.elevation import FunctionDem
from flat_segments.export import loop_properties, read_loops, write_loops
from flat_segments.network import RoadClass, Way, split_ways
from flat_segments.params import WEB_CRS, WORK_CRS, ProfileParams
from flat_segments.pipeline import read_setting_areas
from tests.helpers import make_way
from tests.test_batch import OUTLINE, gentle_wms, osm_xml, run
from tests.test_departments import collection
from tests.test_download import FakeWeb

ORIGIN = (575_000.0, 6_275_000.0)
FLAT = FunctionDem(lambda x, y: np.full_like(x, 150.0))


def corners(x: float, y: float, side: float) -> list[tuple[float, float]]:
    """Closed square, counter-clockwise from its south-west corner."""
    return [(x, y), (x + side, y), (x + side, y + side), (x, y + side), (x, y)]


def square_ways(first_id: int, x: float, y: float, side: float, **attrs: str) -> list[Way]:
    """A square made of one footway per side (so that each corner is a node)."""
    points = corners(x, y, side)
    return [
        make_way(first_id + i, [points[i], points[i + 1]], RoadClass.PATH, "footway", **attrs)
        for i in range(4)
    ]


def park(
    x: float, y: float, side: float, kind: str = "park", name: str | None = None
) -> SettingArea:
    return SettingArea(f"way/{int(x)}", kind, box(x, y, x + side, y + side), name)


def only(found: list[Candidate]) -> Candidate:
    assert len(found) == 1, [(c.source, c.setting, round(c.length_m)) for c in found]
    return found[0]


def test_setting_kind() -> None:
    assert circuits.setting_kind({"natural": "water"}) == "water"
    assert circuits.setting_kind({"landuse": "reservoir"}) == "water"
    assert circuits.setting_kind({"leisure": "park"}) == "park"
    assert circuits.setting_kind({"landuse": "forest"}) == "green"
    assert circuits.setting_kind({"landuse": "farmland"}) is None


def test_the_path_round_a_park_is_a_lap() -> None:
    x, y = ORIGIN
    ways = square_ways(1, x, y, 150.0)
    ways.append(make_way(9, [(x, y), (x + 150.0, y + 150.0)]))  # a diagonal inside
    found = find_candidates(ways, [park(x + 10, y + 10, 130.0, name="Jardin")])
    lap = only([c for c in found if c.source == "lap"])
    assert lap.length_m == pytest.approx(600.0)
    assert (lap.setting, lap.name, lap.area_id) == ("park", "Jardin", f"way/{int(x + 10)}")
    assert lap.n_crossings == 0
    assert lap.car_free == 1.0
    # The two triangles are faces inside a park: a maze of paths, not circuits.
    assert all(c.source == "lap" for c in found)


def test_a_closed_way_alone_round_a_lake_is_kept() -> None:
    x, y = ORIGIN
    ring = [(x + 100 * np.cos(a), y + 100 * np.sin(a)) for a in np.linspace(0, 2 * np.pi, 41)]
    ring[-1] = ring[0]
    lake = SettingArea("way/7", "water", Polygon(ring).buffer(-15), "Lac")
    candidate = only(find_candidates([make_way(1, ring)], [lake]))
    assert candidate.setting == "water"
    assert candidate.name == "Lac"
    assert candidate.length_m == pytest.approx(628, abs=2)


def test_two_core_keeps_closed_ways_and_drops_dead_ends() -> None:
    x, y = ORIGIN
    ways = [*square_ways(1, x, y, 100.0), make_way(5, [(x, y), (x - 50.0, y)])]
    loop = make_way(6, [*corners(x + 500, y, 50.0)])
    graph = circuits._Graph(split_ways([*ways, loop], set()), set())
    core = circuits.two_core(graph, range(len(graph.edges)))
    assert {graph.edges[e].way.id for e in core} == {1, 2, 3, 4, 6}


def test_neighbourhood_face_needs_car_free_ways_and_few_crossings() -> None:
    x, y = ORIGIN
    ways = square_ways(1, x, y, 150.0)
    assert only(find_candidates(ways, [])).setting == "neighbourhood"
    streets = [
        make_way(10 + i, [corner, (corner[0] - 30, corner[1] - 30)], RoadClass.MINOR, "residential")
        for i, corner in enumerate(corners(x, y, 150.0)[:2])
    ]
    assert find_candidates([*ways, *streets], []) == []  # two crossings
    assert only(find_candidates([*ways, *streets[:1]], [])).n_crossings == 1


@pytest.mark.parametrize(
    ("ways", "reason"),
    [
        (square_ways(1, *ORIGIN, 40.0), "too short"),
        (square_ways(1, *ORIGIN, 600.0), "too long"),
        (square_ways(1, *ORIGIN, 150.0, footway="sidewalk"), "along roads"),
        (
            [
                *square_ways(1, *ORIGIN, 150.0),
                make_way(9, [ORIGIN, (ORIGIN[0] - 50, ORIGIN[1])], RoadClass.MAJOR, "primary"),
            ],
            "touches a major road",
        ),
    ],
)
def test_faces_that_are_not_circuits(ways: list[Way], reason: str) -> None:
    assert find_candidates(ways, []) == [], reason


def test_a_way_back_on_itself_is_not_a_circuit() -> None:
    x, y = ORIGIN
    there_and_back = make_way(1, [(x, y), (x + 150.0, y), (x, y)])
    assert find_candidates([there_and_back], []) == []


def test_thin_rectangles_are_not_circuits() -> None:
    x, y = ORIGIN
    points = [(x, y), (x + 280, y), (x + 280, y + 20), (x, y + 20), (x, y)]
    ways = [make_way(i, [points[i], points[i + 1]]) for i in range(4)]
    assert find_candidates(ways, []) == []


def test_circuits_must_lie_in_the_outline() -> None:
    x, y = ORIGIN
    ways = square_ways(1, x, y, 150.0)
    assert find_candidates(ways, [], box(x - 10, y - 10, x + 300, y + 300))
    assert find_candidates(ways, [], box(x + 200, y, x + 300, y + 300)) == []


def test_max_local_grade_sees_the_ring_as_a_loop() -> None:
    x, y = ORIGIN
    candidate = only(find_candidates(square_ways(1, x, y, 150.0), []))
    params = ProfileParams()
    assert circuits.max_local_grade(candidate, FLAT, params) == pytest.approx(0.0)
    tilted = FunctionDem(lambda px, py: 150.0 + 0.03 * (px - x))
    assert circuits.max_local_grade(candidate, tilted, params) == pytest.approx(3.0, abs=0.2)
    hole = FunctionDem(lambda px, py: np.where(px > x + 50, np.nan, 150.0))
    assert circuits.max_local_grade(candidate, hole, params) is None


def test_bridges_are_crossed_by_interpolation() -> None:
    x, y = ORIGIN
    ways = square_ways(1, x, y, 150.0)
    ways[0] = make_way(1, [(x, y), (x + 150.0, y)], structure="bridge")
    candidate = only(find_candidates(ways, []))
    assert [s[2] for s in candidate.structures] == ["bridge"]
    river = FunctionDem(lambda px, py: np.where(py < y + 10, 140.0, 150.0))
    assert circuits.max_local_grade(candidate, river, ProfileParams()) == pytest.approx(0.0)


def test_select_keeps_flat_circuits_one_per_spot_and_size() -> None:
    x, y = ORIGIN
    found = find_candidates(
        [
            *square_ways(1, x, y, 150.0),  # 600 m
            *square_ways(11, x + 160, y, 150.0, surface="asphalt"),  # 600 m, 10 m away
            *square_ways(21, x + 20, y + 20, 110.0),  # 440 m, inside the first
            *square_ways(31, x + 1000, y, 150.0),  # 600 m, far away
        ],
        [],
    )
    assert len(found) == 4
    grades = [5.0 if c.coords[:, 0].min() > x + 900 else 0.5 for c in found]
    loops = select_circuits(found, grades, 2.0)
    assert sorted(round(loop.length_m) for loop in loops) == [440, 600]
    loop = loops[0]
    assert (loop.loop_type, loop.access, loop.lap_m, loop.indoor) == (
        "circuit",
        "public",
        None,
        False,
    )
    assert loop.grade_max_pct == 0.5
    assert loop.id.startswith("loop-")


def test_select_prefers_the_flatter_circuit_of_a_spot() -> None:
    x, y = ORIGIN
    found = find_candidates([*square_ways(1, x, y, 150.0), *square_ways(11, x + 160, y, 150.0)], [])
    assert len(found) == 2
    for steep in (0, 1):
        grades = [4.0 if i == steep else 1.5 for i in range(2)]
        [loop] = select_circuits(found, grades)
        assert loop.grade_max_pct == 1.5
    assert [len(select_circuits(found, [g, g])) for g in (4.8, 5.2)] == [1, 0]


@pytest.mark.parametrize(
    ("grade", "band"), [(0.3, 0), (2.0, 0), (2.01, 1), (2.5, 1), (2.51, 2), (5.0, 6)]
)
def test_grade_band(grade: float, band: int) -> None:
    assert circuits.grade_band(grade) == band


def test_circuit_fields_round_trip(tmp_path: Path) -> None:
    x, y = ORIGIN
    found = find_candidates(square_ways(1, x, y, 150.0, surface="asphalt", lit="yes"), [])
    [loop] = select_circuits(found, [0.84], 2.0)
    assert (loop.surface, loop.lit, loop.osm_id) == ("paved", "yes", "way/1")
    write_loops([loop], tmp_path / "circuits.parquet")
    [back] = read_loops(tmp_path / "circuits.parquet")
    assert (back.setting, back.n_crossings, back.grade_max_pct) == ("neighbourhood", 0, 0.84)
    props = loop_properties(back)
    assert (props["setting"], props["n_crossings"], props["grade_max_pct"]) == (
        "neighbourhood",
        0,
        0.84,
    )


def lonlat_square(center_lon: float, center_lat: float, side: float) -> list[tuple[float, float]]:
    to_l93 = Transformer.from_crs(WEB_CRS, WORK_CRS, always_xy=True)
    to_wgs84 = Transformer.from_crs(WORK_CRS, WEB_CRS, always_xy=True)
    cx, cy = to_l93.transform(center_lon, center_lat)
    return [to_wgs84.transform(px, py) for px, py in corners(cx - side / 2, cy - side / 2, side)]


def osm_with_parks() -> str:
    """The batch network, with a footway round a park inside and one in the margin."""
    nodes, ways = [], []
    for k, lat in enumerate((43.525, 43.546)):
        base = 1000 * (k + 1)
        points = lonlat_square(1.53, lat, 150.0)[:-1]
        for i, (lon, node_lat) in enumerate(points):
            nodes.append(f'<node id="{base + i}" lat="{node_lat:.7f}" lon="{lon:.7f}"/>')
        refs = "".join(f'<nd ref="{base + i}"/>' for i in (0, 1, 2, 3, 0))
        ways.append(f'<way id="{base}">{refs}<tag k="highway" v="footway"/></way>')
        ways.append(f'<way id="{base + 1}">{refs}<tag k="leisure" v="park"/></way>')
    return osm_xml().replace("</osm>", f"{''.join(nodes)}{''.join(ways)}</osm>")


def test_department_keeps_the_flat_circuits_inside(tmp_path: Path) -> None:
    pbf = tmp_path / "region.osm"
    pbf.write_text(osm_with_parks())
    outlines = tmp_path / "departements.geojson"
    outlines.write_bytes(collection(("31", "Test", mapping(OUTLINE))))
    assert {a.kind for a in read_setting_areas(pbf)} == {"park"}
    state = run((pbf, outlines), tmp_path / "out", FakeWeb({download.WMS_URL: gentle_wms}))
    step = state["steps"]["circuits"]
    assert (step["candidates"], step["circuits"]) == (1, 1)
    paths = batch.department_paths(tmp_path / "out" / "31")
    [circuit] = read_loops(paths.circuits)
    assert (circuit.loop_type, circuit.setting, circuit.osm_id) == ("circuit", "park", "way/1001")
    assert circuit.length_m == pytest.approx(600, abs=1)
    assert not paths.dem_dir.exists()
    assert "| 1 | " in batch.summary_table({"31": state})


def test_circuits_redone_from_the_published_files_only(tmp_path: Path) -> None:
    """As the Production workflow with circuits_run: the artifact of a run, minus circuits."""
    pbf = tmp_path / "region.osm"
    pbf.write_text(osm_with_parks())
    outlines = tmp_path / "departements.geojson"
    outlines.write_bytes(collection(("31", "Test", mapping(OUTLINE))))
    web = FakeWeb({download.WMS_URL: gentle_wms})
    state = run((pbf, outlines), tmp_path / "out", web)
    paths = batch.department_paths(tmp_path / "out" / "31")
    kept = {paths.segments, paths.loops, paths.circuits, paths.params, paths.state}
    for path in paths.root.rglob("*"):
        if path.is_file() and path not in kept:
            path.unlink()
    segments = paths.segments.read_bytes()
    del state["steps"]["circuits"]
    paths.state.write_text(json.dumps(state))
    again = run((pbf, outlines), tmp_path / "out", web)
    assert again["steps"]["circuits"]["circuits"] == 1
    assert again["steps"]["segments"] == state["steps"]["segments"]
    assert paths.segments.read_bytes() == segments
    assert len(read_loops(paths.circuits)) == 1


def test_circuits_are_published_with_their_segments(tmp_path: Path) -> None:
    from flat_segments.export import write_segments
    from flat_segments.pipeline import read_published

    x, y = ORIGIN
    [loop] = select_circuits(find_candidates(square_ways(1, x, y, 150.0), []), [0.5], 2.0)
    write_segments([], tmp_path / "segments.parquet")
    write_loops([loop], tmp_path / "circuits.parquet")
    assert [item.id for item in read_published(tmp_path / "segments.parquet")] == [loop.id]
