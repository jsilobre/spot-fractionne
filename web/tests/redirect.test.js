import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

// The redirect page published on the old github.io address once the site has
// its own (ADR 0017). The Pages workflow fills in __SITE_URL__.
const html = readFileSync(new URL("../redirect/index.html", import.meta.url), "utf8");
const workflow = readFileSync(new URL("../../.github/workflows/pages.yml", import.meta.url), "utf8");

function redirect(search, hash = "") {
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replaceAll("__SITE_URL__", "https://spot-fractionne.fr/");
  let target = null;
  const location = { search, hash, replace: (url) => (target = url) };
  vm.runInNewContext(script, { URL, location });
  return target;
}

test("old links land on the same segment or position", () => {
  assert.equal(redirect(""), "https://spot-fractionne.fr/");
  assert.equal(redirect("?id=flat-641dc017c07b"), "https://spot-fractionne.fr/?id=flat-641dc017c07b");
  assert.equal(
    redirect("?lat=43.6&lon=1.44&kind=climb", "#x"),
    "https://spot-fractionne.fr/?lat=43.6&lon=1.44&kind=climb#x",
  );
});

test("the Pages workflow publishes the redirect page and its service worker", () => {
  assert.ok(workflow.includes("web/redirect/index.html"));
  assert.ok(workflow.includes("web/redirect/sw.js"));
  assert.ok(workflow.includes("_redirect/404.html"));
});
