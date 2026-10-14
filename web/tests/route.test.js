import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { navigationUrl, parseRoute, ROUTE_URL, routeUrl, segmentStart } from "../route.js";

// Answer of the route service from 43.531, 1.533 to 43.530, 1.536 near Labège (14/10/2026).
const answer = JSON.parse(readFileSync(new URL("./fixtures/route-labege.json", import.meta.url), "utf8"));

const feature = (kind, coordinates, type = "MultiLineString") => ({
  type: "Feature",
  properties: { kind },
  geometry: { type, coordinates },
});

test("routeUrl asks for a walking route as GeoJSON, lon before lat", () => {
  const url = new URL(routeUrl([1.533, 43.531], [1.536, 43.53]));
  assert.equal(`${url.origin}${url.pathname}`, ROUTE_URL);
  assert.equal(url.searchParams.get("profile"), "pedestrian");
  assert.equal(url.searchParams.get("start"), "1.533000,43.531000");
  assert.equal(url.searchParams.get("end"), "1.536000,43.530000");
  assert.equal(url.searchParams.get("geometryFormat"), "geojson");
});

test("parseRoute reads the line, its length and its walking time", () => {
  const route = parseRoute(answer);
  assert.equal(route.coordinates.length, 16);
  assert.deepEqual(route.coordinates.at(-1), [1.536, 43.53]);
  assert.equal(route.distanceM, 376.169);
  assert.equal(route.durationS, 338.552);
});

test("parseRoute returns null without a route", () => {
  assert.equal(parseRoute({ error: { message: " No path found " } }), null);
  assert.equal(parseRoute(null), null);
  assert.equal(parseRoute({ geometry: { coordinates: [[1, 43]] }, distance: 0, duration: 0 }), null);
});

// A line going east along 43.5° N, about 80 m per 0.001°.
const line = [
  [1.5, 43.5],
  [1.501, 43.5],
  [1.502, 43.5],
  [1.503, 43.5],
];
const west = [1.49, 43.5];
const east = [1.51, 43.5];

test("a climb starts at the first point of its line, wherever the position", () => {
  const climb = feature("climb", [line]);
  assert.deepEqual(segmentStart(climb, east), [1.5, 43.5]);
  assert.deepEqual(segmentStart(feature("long_climb", line, "LineString"), east), [1.5, 43.5]);
});

test("a flat is joined at its end nearest to the position", () => {
  const flat = feature("flat", [line]);
  assert.deepEqual(segmentStart(flat, west), [1.5, 43.5]);
  assert.deepEqual(segmentStart(flat, east), [1.503, 43.5]);
});

test("pieces cut by tile edges are not mistaken for ends", () => {
  // Two overlapping pieces (tile buffers), given in any order.
  const pieces = [
    [
      [1.5015, 43.5],
      [1.502, 43.5],
      [1.503, 43.5],
    ],
    [
      [1.5, 43.5],
      [1.501, 43.5],
      [1.502, 43.5],
    ],
  ];
  assert.deepEqual(segmentStart(feature("climb", pieces), east), [1.5, 43.5]);
  assert.deepEqual(segmentStart(feature("flat", pieces), [1.5012, 43.5001]), [1.5, 43.5]);
  assert.deepEqual(segmentStart(feature("flat", pieces), [1.5025, 43.5001]), [1.503, 43.5]);
});

test("a loop is joined at its point nearest to the position", () => {
  const ring = [
    [1.5, 43.5],
    [1.501, 43.5],
    [1.501, 43.501],
    [1.5, 43.501],
    [1.5, 43.5],
  ];
  assert.deepEqual(segmentStart(feature("loop", [ring]), [1.51, 43.502]), [1.501, 43.501]);
});

test("navigationUrl hands the walk to the phone's GPS app", () => {
  const to = [1.536, 43.53];
  const android = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36";
  const iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1";
  const mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15";
  assert.equal(navigationUrl(to, android), "geo:43.530000,1.536000?q=43.530000,1.536000");
  assert.equal(navigationUrl(to, iphone), "https://maps.apple.com/?daddr=43.530000,1.536000&dirflg=w");
  assert.equal(navigationUrl(to, mac, 5), "https://maps.apple.com/?daddr=43.530000,1.536000&dirflg=w"); // iPadOS
  assert.equal(
    navigationUrl(to, mac),
    "https://www.google.com/maps/dir/?api=1&destination=43.530000,1.536000&travelmode=walking",
  );
});
