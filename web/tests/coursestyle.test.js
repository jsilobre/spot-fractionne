import assert from "node:assert/strict";
import { test } from "node:test";

import { COURSE_COLORS, courseStyle } from "../coursestyle.js";

test("course colours are hex colours", () => {
  for (const color of Object.values(COURSE_COLORS)) assert.match(color, /^#[0-9a-f]{6}$/);
});

test("courseStyle draws on the OpenFreeMap vector tiles", () => {
  const style = courseStyle();
  assert.equal(style.version, 8);
  assert.equal(style.sources.openmaptiles.url, "https://tiles.openfreemap.org/planet");
  assert.ok(style.glyphs.startsWith("https://"));
  const ids = style.layers.map((l) => l.id);
  assert.equal(new Set(ids).size, ids.length);
  assert.equal(style.layers[0].type, "background");
  for (const layer of style.layers.slice(1)) {
    assert.equal(layer.source, "openmaptiles", layer.id);
    assert.ok(layer["source-layer"], layer.id);
    // MapLibre rejects a null filter: absent or an expression.
    if ("filter" in layer) assert.ok(Array.isArray(layer.filter), layer.id);
  }
  // Every colour the layers use is defined.
  assert.ok(!JSON.stringify(style).includes("undefined"));
});
