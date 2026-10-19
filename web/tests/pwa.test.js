import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

import { needsIosInstallHint } from "../pwa.js";

const web = new URL("..", import.meta.url);
const read = (path) => readFileSync(new URL(path, web), "utf8");

/** The service worker's top-level declarations, run in a sandbox without a browser. */
function loadServiceWorker() {
  const context = { URL, self: { addEventListener() {} } };
  vm.runInNewContext(`${read("sw.js")}\nthis.exported = { SHELL, CDN, requestStrategy };`, context);
  return context.exported;
}

const SCOPE = "https://jsilobre.github.io/spot-fractionne/";
const PAGE_ORIGIN = "https://jsilobre.github.io/spot-fractionne";

test("the page links the manifest and its icons exist", () => {
  const html = read("index.html");
  assert.ok(html.includes('rel="manifest" href="manifest.webmanifest"'));
  const manifest = JSON.parse(read("manifest.webmanifest"));
  assert.equal(manifest.start_url, "./");
  assert.equal(manifest.display, "standalone");
  const sizes = manifest.icons.map((icon) => icon.sizes);
  assert.ok(sizes.includes("192x192") && sizes.includes("512x512"));
  assert.ok(manifest.icons.some((icon) => icon.purpose === "maskable"));
  for (const icon of manifest.icons) assert.ok(existsSync(new URL(icon.src, web)), `${icon.src} is missing`);
});

test("the service worker caches existing page files, all deployed", () => {
  const workflow = read("../.github/workflows/pages.yml");
  for (const file of loadServiceWorker().SHELL) {
    if (file === "./") continue;
    assert.ok(existsSync(new URL(file, web)), `${file} is missing`);
    const dir = file.split("/")[0];
    const deployed = workflow.includes(`web/${file}`) || (file.includes("/") && workflow.includes(`web/${dir} `));
    assert.ok(deployed, `${file} is not deployed`);
  }
});

test("the service worker caches every module of the page", () => {
  const shell = loadServiceWorker().SHELL;
  const html = read("index.html");
  for (const [, src] of html.matchAll(/<script type="module" src="([^"]+)"/g)) assert.ok(shell.includes(src), src);
});

test("the service worker's CDN list matches the libraries pinned in index.html", () => {
  const html = read("index.html");
  const pinned = new Set([...html.matchAll(/"(https:\/\/unpkg\.com\/[^"]+)"/g)].map((m) => m[1]));
  assert.deepEqual(new Set(loadServiceWorker().CDN), pinned);
});

test("requestStrategy serves page files network first", () => {
  const { requestStrategy } = loadServiceWorker();
  assert.equal(requestStrategy("GET", `${PAGE_ORIGIN}/`, false, SCOPE), "network-first");
  assert.equal(requestStrategy("GET", `${PAGE_ORIGIN}/?id=abc`, false, SCOPE), "network-first");
  assert.equal(requestStrategy("GET", `${PAGE_ORIGIN}/app.js`, false, SCOPE), "network-first");
});

test("requestStrategy serves the pinned libraries cache first", () => {
  const { requestStrategy } = loadServiceWorker();
  const url = "https://unpkg.com/pmtiles@4.5.0/dist/esm/index.js";
  assert.equal(requestStrategy("GET", url, false, SCOPE), "cache-first");
  assert.equal(requestStrategy("GET", "https://unpkg.com/pmtiles@9.9.9/x.js", false, SCOPE), null);
});

test("requestStrategy leaves data, tiles and other requests to the network", () => {
  const { requestStrategy } = loadServiceWorker();
  assert.equal(requestStrategy("GET", `${PAGE_ORIGIN}/data/segments.json`, false, SCOPE), null);
  assert.equal(requestStrategy("GET", `${PAGE_ORIGIN}/data/segments.pmtiles`, true, SCOPE), null);
  assert.equal(requestStrategy("GET", `${PAGE_ORIGIN}/api/comments?spot=flat-0123456789ab`, false, SCOPE), null);
  assert.equal(requestStrategy("GET", "https://data.example.r2.dev/segments.pmtiles", true, SCOPE), null);
  assert.equal(requestStrategy("GET", "https://tiles.openfreemap.org/styles/liberty", false, SCOPE), null);
  assert.equal(requestStrategy("GET", "https://jsilobre.github.io/other/", false, SCOPE), null);
  assert.equal(requestStrategy("POST", `${PAGE_ORIGIN}/`, false, SCOPE), null);
  assert.equal(requestStrategy("GET", `${PAGE_ORIGIN}/app.js`, true, SCOPE), null);
});

test("needsIosInstallHint only on iOS outside the installed app", () => {
  const iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1";
  const mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15";
  const android = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36";
  assert.equal(needsIosInstallHint(iphone, 5, false), true);
  assert.equal(needsIosInstallHint(iphone, 5, true), false);
  assert.equal(needsIosInstallHint(mac, 5, false), true); // iPadOS
  assert.equal(needsIosInstallHint(mac, 0, false), false);
  assert.equal(needsIosInstallHint(android, 5, false), false);
});
