from dataclasses import replace

import numpy as np
from shapely.geometry import LineString

from flat_segments.loops import Loop
from flat_segments.network import RoadClass, StrokePart
from flat_segments.prune import prune_circuits, prune_segments, removed_stretches
from tests.helpers import piecewise, raw_profile, straight_stroke
from tests.test_detect import run


def square_loop(loop_id: str, side: float, loop_type: str = "circuit") -> Loop:
    coords = np.array([(0, 0), (side, 0), (side, side), (0, side), (0, 0)], dtype=np.float64)
    return Loop(
        id=loop_id,
        loop_type=loop_type,
        coords=coords,
        length_m=4 * side,
        lap_m=None,
        name=None,
        surface="paved",
        lit="unknown",
        access="public",
        opening_hours=None,
        indoor=False,
        osm_id="way/1",
    )


def test_segments_on_a_removed_way_go() -> None:
    stroke = straight_stroke(1000)
    [flat] = run(stroke, raw_profile(stroke, piecewise((1000, 0.0))))
    other = replace(flat, id="flat-other", osm_way_ids=(1, 2))
    assert prune_segments([flat, other], {1, 3}) == [flat]


def test_removed_stretches_follow_the_parts_of_the_strokes() -> None:
    parts = (
        StrokePart(1, 0.0, 300.0, "footway", RoadClass.PATH),
        StrokePart(2, 300.0, 500.0, "footway", RoadClass.PATH),
        StrokePart(3, 500.0, 1000.0, "footway", RoadClass.PATH),
    )
    stroke = straight_stroke(1000, parts=parts)
    [stretch] = removed_stretches([stroke, straight_stroke(1000)], {1, 3})
    assert stretch.equals(LineString([(300, 0), (500, 0)]))


def test_circuits_following_a_removed_way_go_but_tracks_stay() -> None:
    small, large = square_loop("loop-small", 200), square_loop("loop-large", 400)
    track = square_loop("loop-track", 200, loop_type="track")
    # Along the south side of both squares, 1 m off.
    along = LineString([(20, 1), (180, 1)])
    assert prune_circuits([small, large, track], [along]) == [track]
    # Crossing the squares, it is not followed.
    across = LineString([(100, -50), (100, 50)])
    assert prune_circuits([small, large, track], [across]) == [small, large, track]
    assert prune_circuits([small], []) == [small]
