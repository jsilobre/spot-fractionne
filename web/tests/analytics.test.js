import assert from "node:assert/strict";
import { test } from "node:test";

import { analyticsPath } from "../analytics.js";

test("analyticsPath keeps the page path", () => {
  assert.equal(analyticsPath("/spot-fractionne/", ""), "/spot-fractionne/");
  assert.equal(analyticsPath("", ""), "/");
});

test("analyticsPath keeps the segment id and category", () => {
  assert.equal(analyticsPath("/spot-fractionne/", "?id=abc123"), "/spot-fractionne/?id=abc123");
  assert.equal(analyticsPath("/spot-fractionne/", "?kind=climb&id=x"), "/spot-fractionne/?id=x&kind=climb");
});

test("analyticsPath drops the position", () => {
  assert.equal(analyticsPath("/spot-fractionne/", "?lat=43.6&lon=1.44"), "/spot-fractionne/");
  assert.equal(analyticsPath("/spot-fractionne/", "?lat=43.6&lon=1.44&kind=loop"), "/spot-fractionne/?kind=loop");
});
