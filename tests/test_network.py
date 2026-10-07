import pytest

from flat_segments.network import EventKind, RoadClass, Stroke, build_strokes, drop_ways_inside
from flat_segments.params import NetworkParams
from tests.helpers import make_way


def by_way(strokes: list[Stroke], way_id: int) -> Stroke:
    return next(s for s in strokes if any(p.way_id == way_id for p in s.parts))


def test_ways_joined_end_to_end_form_one_stroke() -> None:
    strokes = build_strokes(
        [make_way(1, [(0, 0), (100, 0)]), make_way(2, [(100, 0), (250, 5)], surface="asphalt")]
    )
    assert len(strokes) == 1
    stroke = strokes[0]
    assert stroke.length_m == pytest.approx(100 + (150**2 + 5**2) ** 0.5)
    assert [p.way_id for p in stroke.parts] == [1, 2]
    assert stroke.parts[1].start_m == pytest.approx(100)
    assert stroke.parts[1].surface == "asphalt"
    assert stroke.events == ()
    assert not stroke.is_ring


def test_reversed_way_is_chained_in_the_right_direction() -> None:
    strokes = build_strokes([make_way(1, [(0, 0), (100, 0)]), make_way(2, [(200, 0), (100, 0)])])
    assert len(strokes) == 1
    xs = strokes[0].coords[:, 0]
    assert sorted(xs) == list(xs) or sorted(xs, reverse=True) == list(xs)


def test_crossing_paths_continue_straight_with_a_junction_event() -> None:
    strokes = build_strokes(
        [make_way(1, [(-100, 0), (0, 0), (100, 0)]), make_way(2, [(0, -100), (0, 0), (0, 100)])]
    )
    assert len(strokes) == 2
    for stroke in strokes:
        assert stroke.length_m == pytest.approx(200)
        assert len(stroke.parts) == 1  # the split way is merged back into one part
        assert [(e.offset_m, e.kind) for e in stroke.events] == [(100, EventKind.JUNCTION)]


def test_path_crossing_a_minor_road_records_a_crossing() -> None:
    strokes = build_strokes(
        [
            make_way(1, [(-100, 0), (0, 0), (100, 0)]),
            make_way(2, [(0, -100), (0, 0), (0, 100)], RoadClass.MINOR, "residential"),
        ]
    )
    assert by_way(strokes, 1).events[0].kind is EventKind.CROSSING
    # For a runner on the road, a footpath joining is only a junction.
    assert by_way(strokes, 2).events[0].kind is EventKind.JUNCTION


def test_major_road_cuts_strokes_and_is_not_a_support() -> None:
    strokes = build_strokes(
        [
            make_way(1, [(-100, 0), (0, 0), (100, 0)]),
            make_way(2, [(0, -100), (0, 0), (0, 100)], RoadClass.MAJOR, "tertiary"),
        ]
    )
    assert len(strokes) == 2
    assert all(s.parts[0].way_id == 1 for s in strokes)
    assert all(s.length_m == pytest.approx(100) for s in strokes)


def test_t_junction_keeps_the_straight_line() -> None:
    strokes = build_strokes(
        [make_way(1, [(-100, 0), (0, 0), (100, 0)]), make_way(2, [(0, 0), (0, 80)])]
    )
    assert len(strokes) == 2
    main = by_way(strokes, 1)
    assert main.length_m == pytest.approx(200)
    assert len(main.events) == 1
    assert by_way(strokes, 2).length_m == pytest.approx(80)


def test_y_junction_follows_the_smallest_deflection() -> None:
    strokes = build_strokes(
        [
            make_way(1, [(0, -100), (0, 0)]),
            make_way(2, [(0, 0), (30, 100)]),  # ~17 degrees off straight
            make_way(3, [(0, 0), (-100, 60)]),  # ~59 degrees off straight
        ]
    )
    assert len(strokes) == 2
    assert {p.way_id for p in by_way(strokes, 1).parts} == {1, 2}
    assert [p.way_id for p in by_way(strokes, 3).parts] == [3]


def test_sharp_fork_does_not_continue() -> None:
    strokes = build_strokes(
        [
            make_way(1, [(0, -100), (0, 0)]),
            make_way(2, [(0, 0), (100, 60)]),
            make_way(3, [(0, 0), (-100, 60)]),
        ],
        NetworkParams(max_deflection_deg=35),
    )
    assert len(strokes) == 3


def test_sharp_turn_at_degree_two_continues() -> None:
    strokes = build_strokes([make_way(1, [(0, 0), (100, 0)]), make_way(2, [(100, 0), (100, 100)])])
    assert len(strokes) == 1
    assert strokes[0].length_m == pytest.approx(200)


def test_closed_way_is_a_ring() -> None:
    strokes = build_strokes([make_way(1, [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)])])
    assert len(strokes) == 1
    assert strokes[0].is_ring
    assert strokes[0].length_m == pytest.approx(400)


def test_structures_and_ids() -> None:
    strokes = build_strokes(
        [
            make_way(1, [(0, 0), (100, 0)]),
            make_way(2, [(100, 0), (140, 0)], structure="bridge"),
            make_way(3, [(140, 0), (300, 0)]),
        ]
    )
    assert strokes[0].id == "s000001"
    [(start, end, kind)] = strokes[0].structures()
    assert start == pytest.approx(100)
    assert end == pytest.approx(140)
    assert kind == "bridge"


def test_ways_mostly_inside_an_area_are_dropped() -> None:
    from shapely.geometry import box

    airport = box(0, 0, 1000, 500)
    ways = [
        make_way(1, [(100, 100), (500, 110), (900, 100)]),  # service road along the runway
        make_way(2, [(-300, 600), (1300, 600)]),  # public road outside the fence
        make_way(3, [(-700, 250), (300, 250)]),  # 30 % inside
        make_way(4, [(100, 300), (900, 300)], road_class=RoadClass.MAJOR),
        make_way(5, [(100, 400), (900, 400)]),  # explicitly open to pedestrians
    ]
    kept = drop_ways_inside(ways, [airport], 0.5, keep=[5])
    assert [w.id for w in kept] == [2, 3, 4, 5]
    assert drop_ways_inside(ways, [], 0.5) == ways
