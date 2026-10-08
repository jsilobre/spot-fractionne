import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

// The Pages workflow copies the page's files one by one: a module missing
// from that list is a 404 in production, and the whole page fails to load.
test("the Pages workflow deploys every module of the page", () => {
  const workflow = readFileSync(new URL("../../.github/workflows/pages.yml", import.meta.url), "utf8");
  const modules = readdirSync(new URL("..", import.meta.url)).filter((f) => f.endsWith(".js"));
  for (const file of modules) assert.ok(workflow.includes(`web/${file}`), `${file} is not deployed`);
});
