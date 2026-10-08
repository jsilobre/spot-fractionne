import assert from "node:assert/strict";
import { test } from "node:test";

import { COURSE_PALETTES, courseStyle } from "../coursestyle.js";

const COLOR = /^#[0-9a-f]{6}$/;

test("palettes define the same colours", () => {
  const keys = Object.keys(COURSE_PALETTES.light).sort();
  for (const [name, palette] of Object.entries(COURSE_PALETTES)) {
    assert.deepEqual(Object.keys(palette).sort(), keys, name);
    for (const color of Object.values(palette)) assert.match(color, COLOR, name);
  }
});

test("courseStyle draws every palette on the OpenFreeMap vector tiles", () => {
  for (const palette of Object.values(COURSE_PALETTES)) {
    const style = courseStyle(palette);
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
    // No unfilled palette entry.
    assert.ok(!JSON.stringify(style).includes("undefined"));
  }
});

test("courseStyle defaults to the light palette", () => {
  assert.deepEqual(courseStyle(), courseStyle(COURSE_PALETTES.light));
});
