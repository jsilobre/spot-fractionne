import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

import {
  circlePolygon,
  buildUrlSearch,
  DEFAULT_CRITERIA,
  distanceToLineMeters,
  featuresBounds,
  filterSegments,
  haversineMeters,
  loopType,
  distanceToGeometryMeters,
  isLoop,
  mapFilter,
  overviewFilter,
  LOOP_SINUOSITY,
  matches,
  parseLatLon,
  parseUrlState,
  sortResults,
} from "../filters.js";

const near = (actual, expected, tolerance) =>
  assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} not within ${tolerance} of ${expected}`);

const feature = (properties, coordinates = [[1.53, 43.53], [1.535, 43.53]]) => ({
  type: "Feature",
  geometry: { type: "LineString", coordinates },
  properties: {
    kind: "flat",
    length_m: 400,
    grade_mean_pct: 0.2,
    grade_max_pct: 0.8,
    n_crossings: 0,
    surface: "paved",
    score: 80,
    ...properties,
  },
});

test("haversine: one degree of latitude is about 111.2 km", () => {
  near(haversineMeters([1.5, 43], [1.5, 44]), 111_195, 10);
  assert.equal(haversineMeters([1.5, 43.5], [1.5, 43.5]), 0);
});

test("distance to a line uses the nearest point, not the vertices", () => {
  const line = [[1.5, 43.5], [1.52, 43.5]];
  const above = [1.51, 43.501]; // ~111 m north of the middle of the line
  near(distanceToLineMeters(above, line), 111.2, 0.5);
  const beyond = [1.53, 43.5]; // past the east end
  near(distanceToLineMeters(beyond, line), haversineMeters(beyond, [1.52, 43.5]), 0.5);
});

test("flat criteria: kind, length, local grade, crossings, surface", () => {
  const c = { ...DEFAULT_CRITERIA };
  assert.equal(matches(feature({}).properties, c), true);
  assert.equal(matches(feature({ kind: "climb" }).properties, c), false);
  assert.equal(matches(feature({ length_m: 150 }).properties, c), false);
  assert.equal(matches(feature({ grade_max_pct: 2.5 }).properties, c), false);
  assert.equal(matches(feature({ n_crossings: 2 }).properties, { ...c, noCrossing: true }), false);
  assert.equal(matches(feature({ surface: "gravel" }).properties, { ...c, pavedOnly: true }), false);
});

test("loop criteria: every track of the kind, or only public ones", () => {
  const loop = { kind: "loop", length_m: 199, access: "unknown", surface: "unknown" };
  const loops = { ...DEFAULT_CRITERIA, kind: "loop", minLengthM: 400, pavedOnly: true };
  assert.equal(matches(loop, loops), true); // length and surface do not apply
  assert.equal(matches(loop, { ...loops, publicOnly: true }), false);
  assert.equal(matches({ ...loop, access: "public" }, { ...loops, publicOnly: true }), true);
  assert.equal(matches(loop, DEFAULT_CRITERIA), false);
  assert.equal(evaluate(overviewFilter(loops), { kind: "loop", length_m: 199 }), true);
});

test("loop types: tracks, and circuits by setting", () => {
  const track = { kind: "loop", access: "unknown" };
  const lake = { kind: "loop", loop_type: "circuit", setting: "water", access: "public" };
  const loops = { ...DEFAULT_CRITERIA, kind: "loop" };
  assert.equal(loopType(track), "track");
  assert.equal(loopType(lake), "water");
  assert.equal(matches(lake, loops), true);
  assert.equal(matches(lake, { ...loops, loopTypes: ["track"] }), false);
  assert.equal(matches(track, { ...loops, loopTypes: ["track"] }), true);
  assert.equal(matches(lake, { ...loops, publicOnly: true }), true); // circuits are public
});

test("loops have no score: sorting by score keeps them last, nearest first", () => {
  const results = [
    { feature: feature({ kind: "loop", score: undefined }), distanceM: 100 },
    { feature: feature({ score: 50 }), distanceM: 300 },
  ];
  assert.deepEqual(
    sortResults([...results], "score").map((r) => r.distanceM),
    [300, 100],
  );
  const tracks = [800, 200, 500].map((d) => ({ feature: feature({ kind: "loop", score: undefined }), distanceM: d }));
  assert.deepEqual(
    sortResults(tracks, "score").map((r) => r.distanceM),
    [200, 500, 800],
  );
});

test("climb criteria use the mean grade range", () => {
  const c = { ...DEFAULT_CRITERIA, kind: "climb", minLengthM: 100, minMeanGradePct: 5, maxMeanGradePct: 8 };
  assert.equal(matches(feature({ kind: "climb", grade_mean_pct: 6 }).properties, c), true);
  assert.equal(matches(feature({ kind: "climb", grade_mean_pct: 4 }).properties, c), false);
  assert.equal(matches(feature({ kind: "climb", grade_mean_pct: 9 }).properties, c), false);
});

test("filterSegments applies the distance limit only with a position", () => {
  const close = feature({ score: 50 });
  const far = feature({ score: 90 }, [[1.7, 43.6], [1.705, 43.6]]);
  const c = { ...DEFAULT_CRITERIA, maxDistanceM: 2000 };
  assert.equal(filterSegments([close, far], c).length, 2);
  const results = filterSegments([close, far], c, [1.532, 43.531]);
  assert.deepEqual(results.map((r) => r.feature), [close]);
  near(results[0].distanceM, 111, 5);
});

test("filterSegments keeps the pinned segment whatever the criteria", () => {
  const gentle = feature({ id: "climb-a", kind: "climb", grade_mean_pct: 2.97 });
  const far = feature({ id: "climb-b", kind: "climb", grade_mean_pct: 5 }, [[1.7, 43.6], [1.705, 43.6]]);
  const c = { ...DEFAULT_CRITERIA, kind: "climb", maxDistanceM: 2000 };
  const position = [1.532, 43.531];
  assert.deepEqual(filterSegments([gentle, far], c, position), []);
  assert.deepEqual(filterSegments([gentle, far], c, position, "climb-a").map((r) => r.feature), [gentle]);
  const [pinnedFar] = filterSegments([gentle, far], c, position, "climb-b");
  assert.equal(pinnedFar.feature, far);
  assert.ok(pinnedFar.distanceM > 2000); // distance still reported
});

test("sortResults by distance then score, or by score", () => {
  const a = { feature: feature({ score: 60 }), distanceM: 300 };
  const b = { feature: feature({ score: 90 }), distanceM: 800 };
  const c = { feature: feature({ score: 70 }), distanceM: null };
  assert.deepEqual(sortResults([b, c, a], "distance"), [a, b, c]);
  assert.deepEqual(sortResults([a, b, c], "score"), [b, c, a]);
});

test("parseLatLon accepts 'lat, lon' and rejects garbage", () => {
  assert.deepEqual(parseLatLon("43.531, 1.533"), [1.533, 43.531]);
  assert.deepEqual(parseLatLon(" 43.531 1.533 "), [1.533, 43.531]);
  assert.deepEqual(parseLatLon("-12.5;45"), [45, -12.5]);
  assert.equal(parseLatLon("Labège"), null);
  assert.equal(parseLatLon("95, 1"), null);
});

test("featuresBounds", () => {
  const f = [feature({}, [[1, 2], [3, 4]]), feature({}, [[0, 5], [2, 3]])];
  assert.deepEqual(featuresBounds(f), [[0, 2], [3, 5]]);
  assert.equal(featuresBounds([]), null);
});

test("the sample tileset describes itself and indexes every segment", () => {
  const dir = new URL("../data/sample/", import.meta.url);
  const metadata = JSON.parse(readFileSync(new URL("segments.json", dir), "utf8"));
  assert.equal(metadata.sample, true);
  assert.equal(metadata.tiles.url, "segments.pmtiles");
  assert.equal(metadata.tiles.minzoom, 12);
  assert.equal(metadata.bounds.length, 4);
  assert.ok(metadata.counts.flat > 0 && metadata.counts.climb > 0);
  assert.ok(metadata.counts.loop > 0);
  const total = metadata.counts.flat + metadata.counts.climb + metadata.counts.loop;
  const indexed = readdirSync(new URL("ids/", dir)).flatMap((name) =>
    Object.keys(JSON.parse(readFileSync(new URL(`ids/${name}`, dir), "utf8"))),
  );
  assert.equal(indexed.length, total);
});

test("URL state: parse and build are inverse, invalid values ignored", () => {
  const state = { id: "flat-3fa2b1c9d0e4", kind: "climb", position: [1.53321, 43.53081] };
  const search = buildUrlSearch(state);
  assert.equal(search, "?id=flat-3fa2b1c9d0e4&kind=climb&lat=43.53081&lon=1.53321");
  assert.deepEqual(parseUrlState(search), state);
  assert.equal(buildUrlSearch({ kind: "flat" }), "");
  assert.deepEqual(parseUrlState("?kind=hill&lat=95&lon=1"), { id: null, kind: null, position: null });
  assert.deepEqual(parseUrlState("?lat=43.5"), { id: null, kind: null, position: null });
  assert.deepEqual(parseUrlState(""), { id: null, kind: null, position: null });
  assert.equal(parseUrlState("?kind=loop").kind, "loop");
  assert.equal(buildUrlSearch({ kind: "loop" }), "?kind=loop");
});

test("segments whose ends are close are shown as loops", () => {
  assert.equal(isLoop({ sinuosity: null }), true); // closed ring
  assert.equal(isLoop({ sinuosity: 225.7 }), true);
  assert.equal(isLoop({ sinuosity: LOOP_SINUOSITY }), false);
  assert.equal(isLoop({ sinuosity: 1.05 }), false);
});

// Evaluate the few MapLibre expression operators used by mapFilter.
function evaluate(expression, properties) {
  const [op, ...args] = expression;
  const value = (arg) => (Array.isArray(arg) ? evaluate(arg, properties) : arg);
  if (op === "get") return properties[args[0]];
  if (op === "literal") return args[0];
  if (op === "match") {
    const [input, labels, then, otherwise] = args;
    return labels.includes(value(input)) ? then : otherwise;
  }
  if (op === "case") {
    const [condition, then, otherwise] = args;
    return value(condition) ? value(then) : value(otherwise);
  }
  if (op === "all") return args.every((a) => value(a));
  if (op === "any") return args.some((a) => value(a));
  const [a, b] = args.map(value);
  return { "==": a === b, ">=": a >= b, "<=": a <= b }[op];
}

test("mapFilter selects exactly what matches selects, plus the pinned segment", () => {
  const segments = [
    { id: "a", kind: "flat", length_m: 400, grade_max_pct: 1.5, n_crossings: 0, surface: "paved" },
    { id: "b", kind: "flat", length_m: 150, grade_max_pct: 0.5, n_crossings: 0, surface: "paved" },
    { id: "c", kind: "flat", length_m: 900, grade_max_pct: 2.5, n_crossings: 2, surface: "gravel" },
    { id: "d", kind: "climb", length_m: 300, grade_mean_pct: 2.97, n_crossings: 0, surface: "paved" },
    { id: "e", kind: "climb", length_m: 300, grade_mean_pct: 8, n_crossings: 1, surface: "unknown" },
    { id: "f", kind: "loop", length_m: 199, access: "public", surface: "unknown" },
    { id: "g", kind: "loop", length_m: 398, access: "unknown", surface: "paved" },
    { id: "h", kind: "loop", loop_type: "circuit", setting: "water", length_m: 800, access: "public" },
    { id: "i", kind: "loop", loop_type: "circuit", setting: "park", length_m: 500, access: "public" },
  ];
  const variants = [
    { ...DEFAULT_CRITERIA },
    { ...DEFAULT_CRITERIA, kind: "climb" },
    { ...DEFAULT_CRITERIA, kind: "loop" },
    { ...DEFAULT_CRITERIA, kind: "loop", publicOnly: true, noCrossing: true, pavedOnly: true, minLengthM: 1000 },
    { ...DEFAULT_CRITERIA, kind: "loop", loopTypes: ["track", "park"] },
    { ...DEFAULT_CRITERIA, kind: "loop", loopTypes: ["water"], publicOnly: true },
    { ...DEFAULT_CRITERIA, kind: "loop", loopTypes: [] },
    { ...DEFAULT_CRITERIA, noCrossing: true, pavedOnly: true, maxLocalGradePct: 3, minLengthM: 100 },
    { ...DEFAULT_CRITERIA, kind: "climb", minMeanGradePct: 5, maxMeanGradePct: 10 },
  ];
  for (const criteria of variants) {
    for (const p of segments) {
      assert.equal(evaluate(mapFilter(criteria), p), matches(p, criteria), `${p.id} ${JSON.stringify(criteria)}`);
    }
  }
  const climbs = { ...DEFAULT_CRITERIA, kind: "climb" };
  assert.equal(evaluate(mapFilter(climbs), segments[3]), false);
  assert.equal(evaluate(mapFilter(climbs, "d"), segments[3]), true);
  assert.equal(evaluate(overviewFilter(climbs), { kind: "climb", length_m: 300 }), true);
  assert.equal(evaluate(overviewFilter(climbs), { kind: "flat", length_m: 300 }), false);
});

test("map filters show only the listed segments, and the pinned one", () => {
  const flat = { id: "a", kind: "flat", length_m: 400, grade_max_pct: 1, n_crossings: 0, surface: "paved" };
  const criteria = { ...DEFAULT_CRITERIA };
  assert.equal(evaluate(mapFilter(criteria, null, ["a", "b"]), flat), true);
  assert.equal(evaluate(mapFilter(criteria, null, ["b"]), flat), false);
  assert.equal(evaluate(mapFilter(criteria, null, []), flat), false);
  assert.equal(evaluate(mapFilter(criteria, "a", []), flat), true);
  // Listed but no longer matching (criteria changed before the list is rebuilt).
  assert.equal(evaluate(mapFilter({ ...criteria, minLengthM: 500 }, null, ["a"]), flat), false);
  assert.equal(evaluate(overviewFilter(criteria, ["a"]), flat), true);
  assert.equal(evaluate(overviewFilter(criteria, []), flat), false);
});

test("circlePolygon is a closed ring at the given distance", () => {
  const center = [1.533, 43.531];
  const [ring] = circlePolygon(center, 5000, 32).geometry.coordinates;
  assert.equal(ring.length, 33);
  assert.deepEqual(ring[0], ring[32]);
  for (const point of ring) assert.ok(Math.abs(haversineMeters(center, point) - 5000) < 0.01, String(point));
});

test("distances and bounds handle segments split into several lines", () => {
  const geometry = {
    type: "MultiLineString",
    coordinates: [
      [[1.5, 43.5], [1.51, 43.5]],
      [[1.6, 43.6], [1.61, 43.6]],
    ],
  };
  near(distanceToGeometryMeters([1.605, 43.6], geometry), 0, 0.01);
  assert.deepEqual(featuresBounds([{ geometry }]), [[1.5, 43.5], [1.61, 43.6]]);
  assert.equal(isLoop({}), true); // no sinuosity in the tile: closed ring
});
