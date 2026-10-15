import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  bandLengthM,
  buildProfile,
  chartPoint,
  distanceAtX,
  ELEVATION_URL,
  elevationAt,
  elevationRequest,
  gradeAt,
  gradeBands,
  gradeClass,
  lineLengthM,
  nearestSample,
  orderedLine,
  parseElevations,
  profileSvg,
  RESOURCES,
  sampleCount,
  verticalRange,
} from "../profile.js";

// Answer of the altimetry service along 43.6° N from 1.44° to 1.45° E (Toulouse, 15/10/2026).
const answer = JSON.parse(readFileSync(new URL("./fixtures/elevation-toulouse.json", import.meta.url), "utf8"));

const feature = (kind, coordinates, type = "MultiLineString") => ({
  type: "Feature",
  properties: { kind },
  geometry: { type, coordinates },
});

// Samples every ~8 m along 43.5° N with the given elevations.
const samples = (zs) => zs.map((z, i) => ({ lon: 1.5 + i * 0.0001, lat: 43.5, z }));

test("orderedLine chains pieces cut by tile edges and drops their overlap", () => {
  const pieces = [
    [
      [1.502, 43.5],
      [1.503, 43.5],
    ],
    [
      [1.5, 43.5],
      [1.501, 43.5],
      [1.50202, 43.5], // overlaps the next piece by ~2 m
    ],
  ];
  const line = orderedLine(feature("climb", pieces));
  assert.deepEqual(line, [
    [1.5, 43.5],
    [1.501, 43.5],
    [1.50202, 43.5],
    [1.503, 43.5],
  ]);
});

test("orderedLine keeps a climb uphill and starts a flat from its end nearest to the position", () => {
  const coords = [
    [1.5, 43.5],
    [1.501, 43.5],
    [1.502, 43.5],
  ];
  const nearEnd = [1.5025, 43.5];
  assert.deepEqual(orderedLine(feature("climb", coords, "LineString"), nearEnd)[0], [1.5, 43.5]);
  assert.deepEqual(orderedLine(feature("flat", coords, "LineString"), nearEnd)[0], [1.502, 43.5]);
  assert.deepEqual(orderedLine(feature("flat", coords, "LineString"), [1.4995, 43.5])[0], [1.5, 43.5]);
});

test("orderedLine starts a loop at its point nearest to the position and closes it", () => {
  const ring = [
    [1.5, 43.5],
    [1.501, 43.5],
    [1.501, 43.501],
    [1.5, 43.501],
    [1.5, 43.5],
  ];
  const line = orderedLine(feature("loop", ring, "LineString"), [1.5012, 43.5012]);
  assert.deepEqual(line, [
    [1.501, 43.501],
    [1.5, 43.501],
    [1.5, 43.5],
    [1.501, 43.5],
    [1.501, 43.501],
  ]);
});

test("sampleCount samples every 5 m, at most 401 points", () => {
  assert.equal(sampleCount(400), 81);
  assert.equal(sampleCount(1), 2);
  assert.equal(sampleCount(10_000), 401);
});

test("elevationRequest posts the vertices, lon before lat, with the LiDAR resource first", () => {
  const line = [
    [1.44, 43.6],
    [1.45, 43.6],
  ];
  const { url, init } = elevationRequest(line);
  assert.equal(url, ELEVATION_URL);
  assert.equal(init.method, "POST");
  const body = JSON.parse(init.body);
  assert.equal(body.lon, "1.440000|1.450000");
  assert.equal(body.lat, "43.600000|43.600000");
  assert.equal(body.resource, RESOURCES[0].id);
  assert.equal(body.resource, "ign_lidar_hd_mnt_mono_wld");
  assert.equal(body.sampling, String(sampleCount(lineLengthM(line))));
  assert.equal(JSON.parse(elevationRequest(line, "ign_rge_alti_wld").init.body).resource, "ign_rge_alti_wld");
});

test("parseElevations reads the samples of the service", () => {
  const points = parseElevations(answer);
  assert.equal(points.length, 41);
  assert.deepEqual(points[0], { lon: 1.44, lat: 43.6, z: 133.52 });
});

test("parseElevations rejects answers without ground", () => {
  const sea = { elevations: [0, 1, 2].map((i) => ({ lon: 5 + i / 100, lat: 43, z: -99999 })) };
  assert.equal(parseElevations(sea), null);
  assert.equal(parseElevations({ error: "x" }), null);
  assert.equal(parseElevations(null), null);
  const oneHole = { elevations: samples([10, -99999, 12, 13, 14, 15]) };
  assert.ok(Number.isNaN(parseElevations(oneHole)[1].z));
});

test("buildProfile measures the distance and keeps a regular slope", () => {
  // A 5 % slope over ~160 m, with one missing sample.
  const zs = Array.from({ length: 21 }, (_, i) => 100 + i * 8.07 * 0.05);
  zs[7] = NaN;
  const profile = buildProfile(samples(zs));
  assert.ok(Math.abs(profile.lengthM - 161.3) < 1, profile.lengthM);
  assert.ok(Math.abs(profile.z[0] - 100) < 0.01);
  assert.ok(Math.abs(profile.z.at(-1) - zs.at(-1)) < 0.01);
  assert.ok(Math.abs(profile.z[7] - (100 + 7 * 8.07 * 0.05)) < 0.05);
  assert.ok(Math.abs(gradeAt(profile, 10) - 5) < 0.1);
  for (const band of gradeBands(profile)) assert.ok(Math.abs(band.grade - 5) < 0.1, band.grade);
});

test("buildProfile removes a spike", () => {
  const zs = Array(21).fill(50);
  zs[10] = 58; // a parked lorry or a wall in the ground model
  const profile = buildProfile(samples(zs));
  assert.ok(profile.max - profile.min < 0.5, profile.max - profile.min);
});

test("gradeBands split the length into round bands", () => {
  assert.equal(bandLengthM(200), 10);
  assert.equal(bandLengthM(400), 20);
  assert.equal(bandLengthM(1000), 50);
  assert.equal(bandLengthM(3000), 200);
  const profile = buildProfile(parseElevations(answer));
  const bands = gradeBands(profile);
  assert.equal(bands[0].from, 0);
  assert.equal(bands.at(-1).to, profile.lengthM);
  for (let i = 1; i < bands.length; i++) assert.equal(bands[i].from, bands[i - 1].to);
});

test("gradeClass sorts grades by steepness, uphill or downhill", () => {
  assert.equal(gradeClass(0.5), 0);
  assert.equal(gradeClass(-3), 1);
  assert.equal(gradeClass(6), 2);
  assert.equal(gradeClass(10), 3);
  assert.equal(gradeClass(-20), 4);
});

test("verticalRange spans at least 10 m so that a flat looks flat", () => {
  const flat = buildProfile(samples([100, 100.4, 100.2, 100.6, 100.3]));
  const [low, high] = verticalRange(flat);
  assert.ok(Math.abs(high - low - 10) < 1e-9);
  assert.ok(low < flat.min && high > flat.max);
});

test("elevationAt interpolates between samples", () => {
  const profile = { d: [0, 10, 20], z: [100, 102, 101], lengthM: 20 };
  assert.equal(elevationAt(profile, 5), 101);
  assert.equal(elevationAt(profile, -1), 100);
  assert.equal(elevationAt(profile, 25), 101);
  assert.equal(nearestSample(profile, 14), 1);
});

test("profileSvg draws the bands, the line and the labels", () => {
  const profile = buildProfile(parseElevations(answer));
  const svg = profileSvg(profile);
  assert.match(svg, /^<svg class="profile-chart" viewBox="0 0 300 110"/);
  assert.equal((svg.match(/<path class="grade-\d"/g) ?? []).length, gradeBands(profile).length);
  assert.match(svg, /class="profile-line"/);
  assert.match(svg, />\d+ m<\/text>/);
  assert.match(svg, />8\d\d m<\/text>/); // length, ~805 m
  assert.doesNotMatch(svg, /NaN/);
});

test("distanceAtX and chartPoint match the chart's scales", () => {
  const profile = buildProfile(parseElevations(answer));
  assert.equal(distanceAtX(profile, 34), 0);
  assert.equal(distanceAtX(profile, 294), profile.lengthM);
  assert.equal(distanceAtX(profile, 0), 0);
  const [x, y] = chartPoint(profile, profile.d.length - 1);
  assert.equal(x, 294);
  assert.ok(y >= 6 && y <= 94);
});
