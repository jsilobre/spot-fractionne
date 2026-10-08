import assert from "node:assert/strict";
import { test } from "node:test";

import { BASEMAPS, basemapById, DEFAULT_BASEMAP, ignTileUrl } from "../basemaps.js";

test("basemaps have unique ids and a label", () => {
  assert.equal(new Set(BASEMAPS.map((b) => b.id)).size, BASEMAPS.length);
  for (const b of BASEMAPS) assert.ok(b.label);
});

test("raster basemaps credit their source and use https tiles", () => {
  for (const { style } of BASEMAPS.filter((b) => typeof b.style === "object")) {
    assert.equal(style.version, 8);
    const source = style.sources.basemap;
    assert.ok(source.attribution);
    for (const url of source.tiles) {
      assert.ok(url.startsWith("https://"));
      for (const placeholder of ["{z}", "{x}", "{y}"]) assert.ok(url.includes(placeholder), url);
    }
  }
});

test("ignTileUrl builds a WMTS GetTile request on the Web Mercator grid", () => {
  const url = new URL(
    ignTileUrl("ORTHOIMAGERY.ORTHOPHOTOS", "image/jpeg")
      .replace("{z}", "13")
      .replace("{y}", "2990")
      .replace("{x}", "4130"),
  );
  assert.equal(url.origin, "https://data.geopf.fr");
  assert.equal(url.searchParams.get("LAYER"), "ORTHOIMAGERY.ORTHOPHOTOS");
  assert.equal(url.searchParams.get("FORMAT"), "image/jpeg");
  assert.equal(url.searchParams.get("TILEMATRIXSET"), "PM");
  assert.equal(url.searchParams.get("TILEMATRIX"), "13");
  assert.equal(url.searchParams.get("TILEROW"), "2990");
  assert.equal(url.searchParams.get("TILECOL"), "4130");
});

test("basemapById falls back to the default basemap", () => {
  assert.equal(basemapById("photo").id, "photo");
  assert.equal(basemapById("nope").id, DEFAULT_BASEMAP);
  assert.equal(basemapById(null).id, DEFAULT_BASEMAP);
});
