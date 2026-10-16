"""Remove from computed results what a stricter choice of ways now excludes.

When a fix only removes ways from the network (airports, cemeteries, railway
platforms...), the segments and circuits already computed can be filtered
instead of computed again (docs/architecture.md section 8):

* a segment goes if one of its ``osm_way_ids`` is no longer kept;
* a circuit (``loop_type="circuit"``) goes if it follows a removed way: most
  of that way's stretch, as the strokes of the same run recorded it, lies
  along the circuit. Circuits store no way ids, hence the geometry.

Running tracks do not come from the network and are kept. Crossing counts
are not recomputed, and a stroke cut by a removed way is not extended.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING, Final

import numpy as np

from flat_segments.detect import Segment
from flat_segments.loops import Loop
from flat_segments.network import Stroke

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry

#: Distance from a circuit within which a removed way counts as followed.
FOLLOW_TOLERANCE_M: Final = 3.0
#: Share of a removed way's stretch along a circuit above which it goes.
FOLLOW_MIN_SHARE: Final = 0.5


def prune_segments(segments: Sequence[Segment], kept_way_ids: Collection[int]) -> list[Segment]:
    """The segments whose ways are all still kept, in their input order."""
    kept = set(kept_way_ids)
    return [s for s in segments if all(way_id in kept for way_id in s.osm_way_ids)]


def removed_stretches(
    strokes: Sequence[Stroke], kept_way_ids: Collection[int]
) -> list[BaseGeometry]:
    """Stretches of the strokes along ways that are no longer kept (Lambert-93)."""
    import shapely
    from shapely.ops import substring

    kept = set(kept_way_ids)
    stretches: list[BaseGeometry] = []
    for stroke in strokes:
        removed = [p for p in stroke.parts if p.way_id not in kept]
        if not removed:
            continue
        line = shapely.LineString(stroke.coords)
        stretches.extend(substring(line, p.start_m, p.end_m) for p in removed)
    return stretches


def prune_circuits(
    loops: Sequence[Loop],
    removed: Sequence[BaseGeometry],
    tolerance_m: float = FOLLOW_TOLERANCE_M,
    min_share: float = FOLLOW_MIN_SHARE,
) -> list[Loop]:
    """The loops that follow none of the ``removed`` stretches.

    Only circuits are tested; running tracks are always kept.
    """
    import shapely

    circuits = [i for i, loop in enumerate(loops) if loop.loop_type == "circuit"]
    stretches = [s for s in removed if s.length > 0]
    if not circuits or not stretches:
        return list(loops)
    rings = np.array([shapely.LineString(loops[i].coords) for i in circuits], dtype=object)
    corridors = shapely.buffer(rings, tolerance_m)
    tree = shapely.STRtree(np.array(stretches, dtype=object))
    circuit_index, stretch_index = tree.query(corridors, predicate="intersects")
    pieces = np.array(stretches, dtype=object)[stretch_index]
    along = shapely.length(shapely.intersection(pieces, corridors[circuit_index]))
    followed = along > min_share * shapely.length(pieces)
    dropped = {circuits[i] for i in circuit_index[followed]}
    return [loop for i, loop in enumerate(loops) if i not in dropped]
