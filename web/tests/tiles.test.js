import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { distanceToLineMeters } from "../filters.js";
import {
  dataUrl,
  decodeTile,
  indexKey,
  lonLatToTile,
  normalizeProperties,
  resolveIndexEntry,
  segmentsFromTiles,
  tilePointToLonLat,
  tilesCoveringCircle,
} from "../tiles.js";

// A zoom 12 tile of the sample tileset (made by tippecanoe from
// scripts/make_sample_data.py), with what it must contain: the decoded
// properties of one segment, and the original geometry of every segment.
const FIXTURE = new URL("./fixtures/sample-12-2065-1496", import.meta.url);
const tileBytes = new Uint8Array(readFileSync(`${FIXTURE.pathname}.mvt`));
const expected = JSON.parse(readFileSync(`${FIXTURE.pathname}.json`, "utf8"));

test("decodeTile reads layers, properties and geometry written by tippecanoe", () => {
  const layers = decodeTile(tileBytes);
  assert.deepEqual(Object.keys(layers).sort(), expected.layers);
  const layer = layers.segments;
  assert.equal(layer.extent, expected.extent);
  const ids = [...new Set(layer.features.map((f) => f.properties.id))].sort();
  assert.deepEqual(ids, expected.ids);
  const first = layer.features.find((f) => f.properties.id === expected.ids[0]);
  assert.deepEqual(first.properties, expected.properties);
  assert.equal(first.type, 2); // LineString
});

test("decoded segments lie on their original geometry", () => {
  const layer = decodeTile(tileBytes).segments;
  const { z, x, y } = expected;
  const segments = segmentsFromTiles([{ z, x, y, layer }]);
  assert.equal(segments.length, expected.ids.length);
  for (const segment of segments) {
    const original = expected.original[segment.id];
    for (const line of segment.geometry.coordinates) {
      for (const point of line) {
        // Points inside the tile (beyond it, the buffer may clip lines anywhere).
        assert.ok(distanceToLineMeters(point, original) < 2, `${segment.id} ${point}`);
      }
    }
  }
});

test("segmentsFromTiles merges the pieces of a segment and decodes its lists", () => {
  const piece = (id, lines) => ({
    properties: { id, kind: "flat", highways: '["cycleway"]', fits_targets_m: "[200,400]" },
    lines,
  });
  const tiles = [
    { z: 12, x: 2065, y: 1496, layer: { extent: 4096, features: [piece("flat-a", [[[0, 0], [10, 0]]])] } },
    { z: 12, x: 2066, y: 1496, layer: { extent: 4096, features: [piece("flat-a", [[[0, 0], [5, 5]]])] } },
  ];
  const [segment] = segmentsFromTiles(tiles);
  assert.equal(segment.id, "flat-a");
  assert.equal(segment.geometry.type, "MultiLineString");
  assert.equal(segment.geometry.coordinates.length, 2);
  assert.deepEqual(segment.properties.highways, ["cycleway"]);
  assert.deepEqual(segment.properties.fits_targets_m, [200, 400]);
  assert.equal(segment.properties.name, null);
  assert.deepEqual(segment.properties.quality_flags, []);
});

test("normalizeProperties survives a list that is not JSON", () => {
  const p = normalizeProperties({ id: "flat-a", osm_way_ids: "climb-d006353a8869", highways: "3" });
  assert.deepEqual(p.osm_way_ids, []);
  assert.deepEqual(p.highways, []);
});

test("normalizeProperties keeps present values", () => {
  const p = normalizeProperties({ name: "Voie verte", sinuosity: 1.1, quality_flags: '["gap_filled"]' });
  assert.equal(p.name, "Voie verte");
  assert.equal(p.sinuosity, 1.1);
  assert.deepEqual(p.quality_flags, ["gap_filled"]);
});

test("tile math round-trips and covers a circle", () => {
  const labege = [1.533, 43.531];
  const [x, y] = lonLatToTile(labege, 12);
  assert.deepEqual([x, y], [2065, 1496]);
  const [west, north] = tilePointToLonLat(12, x, y, 4096, [0, 0]);
  const [east, south] = tilePointToLonLat(12, x, y, 4096, [4096, 4096]);
  assert.ok(west <= labege[0] && labege[0] <= east);
  assert.ok(south <= labege[1] && labege[1] <= north);
  assert.deepEqual(tilesCoveringCircle(labege, 100, 12), [[2065, 1496]]);
  // 10 km around Labège at zoom 12 (tiles ~7 km wide): at most 4 x 4 tiles.
  const tiles = tilesCoveringCircle(labege, 10_000, 12);
  assert.ok(tiles.length >= 9 && tiles.length <= 16, String(tiles.length));
  assert.ok(tiles.some(([tx, ty]) => tx === x && ty === y));
});

test("indexKey uses the first characters of the id hash", () => {
  assert.equal(indexKey("flat-3fa2b1c9d0e4"), "3f");
  assert.equal(indexKey("climb-3fa2b1c9d0e4-2"), "3f");
  assert.equal(indexKey("nonsense"), "");
});

test("resolveIndexEntry reads live, moved and retired ids", () => {
  assert.deepEqual(resolveIndexEntry("flat-a", [1.5, 43.5]), { status: "live", id: "flat-a", position: [1.5, 43.5] });
  assert.deepEqual(resolveIndexEntry("flat-a", [1.5, 43.5, "flat-b"]), {
    status: "moved",
    id: "flat-b",
    position: [1.5, 43.5],
  });
  assert.deepEqual(resolveIndexEntry("flat-a", [1.5, 43.5, null]), {
    status: "retired",
    id: null,
    position: [1.5, 43.5],
  });
  assert.equal(resolveIndexEntry("flat-a", undefined), null);
  assert.equal(resolveIndexEntry("flat-a", [1.5]), null);
});

test("dataUrl resolves relative and absolute addresses", () => {
  const base = "https://jsilobre.github.io/spot-fractionne/data/";
  assert.equal(dataUrl(base, "segments.pmtiles"), `${base}segments.pmtiles`);
  assert.equal(dataUrl(base, "ids/3f.json"), `${base}ids/3f.json`);
  const r2 = "https://pub-x.r2.dev/ids/20261002T120000Z";
  assert.equal(dataUrl(base, `${r2}/3f.json`), `${r2}/3f.json`);
});
