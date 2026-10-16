import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { test } from "node:test";

import { BODY_MAX, cleanText, handle, REPORTS_TO_HIDE, sameSecret, validateComment } from "../comments.js";

const ORIGIN = "https://spot-fractionne.fr";
const SPOT = "climb-0123456789ab";
const migration = readFileSync(new URL("../migrations/0001_comments.sql", import.meta.url), "utf8");

/** D1's binding API over an in-memory SQLite database (same SQL dialect). */
function fakeD1() {
  const db = new DatabaseSync(":memory:");
  db.exec(migration);
  return {
    sql: db,
    prepare(query) {
      let params = [];
      const statement = {
        bind: (...values) => ((params = values), statement),
        all: async () => ({
          results: db
            .prepare(query)
            .all(...params)
            .map((row) => ({ ...row })),
        }),
        first: async () => {
          const row = db.prepare(query).get(...params);
          return row ? { ...row } : null;
        },
        run: async () => db.prepare(query).run(...params),
      };
      return statement;
    },
  };
}

function setup({ limit = true } = {}) {
  return {
    DB: fakeD1(),
    WRITE_LIMIT: { limit: async () => ({ success: limit }) },
    TURNSTILE_SITEKEY: "site-key",
    TURNSTILE_SECRET: "secret",
    ADMIN_TOKEN: "admin-password",
  };
}

// Turnstile answers success only for the token "human".
const turnstile = async (_url, { body }) => new Response(JSON.stringify({ success: body.get("response") === "human" }));

function call(env, method, path, body, headers = {}) {
  const init = { method, headers: { origin: ORIGIN, ...headers } };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers["content-type"] = "application/json";
  }
  return handle(new Request(`${ORIGIN}${path}`, init), env, turnstile);
}

const post = (env, body) =>
  call(env, "POST", "/api/comments", {
    spot: SPOT,
    author: "Léa",
    body: "Bien éclairé le soir.",
    token: "human",
    ...body,
  });
const list = async (env, spot = SPOT) => (await (await call(env, "GET", `/api/comments?spot=${spot}`)).json()).comments;
const asAdmin = { authorization: "Bearer admin-password" };

test("cleanText tidies what visitors type", () => {
  assert.equal(cleanText("  Léa\t \u0007 R. ", false), "Léa R.");
  assert.equal(cleanText("a \r\n\r\n\r\n\n b\u0000c", true), "a\n\nb c");
  assert.equal(cleanText(undefined, true), "");
});

test("validateComment checks the spot, pseudonym and text", () => {
  assert.deepEqual(validateComment({ spot: SPOT, author: " Léa ", body: "Très bien" }), {
    spot: SPOT,
    author: "Léa",
    body: "Très bien",
  });
  assert.match(validateComment({ spot: "../x", author: "a", body: "abc" }).error, /Spot/);
  assert.match(validateComment({ spot: SPOT, author: "  ", body: "abc" }).error, /pseudo/);
  assert.match(validateComment({ spot: SPOT, author: "a", body: " a " }).error, /court/);
  assert.match(validateComment({ spot: SPOT, author: "a", body: "x".repeat(BODY_MAX + 1) }).error, /long/);
  assert.match(validateComment({ spot: SPOT, author: "a", body: "http://a www.b https://c" }).error, /liens/);
});

test("sameSecret", async () => {
  assert.equal(await sameSecret("abc", "abc"), true);
  assert.equal(await sameSecret("abd", "abc"), false);
  assert.equal(await sameSecret("", "abc"), false);
  assert.equal(await sameSecret("abc", undefined), false);
});

test("a comment is posted, then listed newest first, per spot", async () => {
  const env = setup();
  const created = await post(env, {});
  assert.equal(created.status, 201);
  const { comment } = await created.json();
  assert.equal(comment.author, "Léa");
  assert.match(comment.created_at, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/);
  await post(env, { body: "Boueux après la pluie." });
  await post(env, { spot: "flat-0123456789ab", body: "Autre spot." });
  const comments = await list(env);
  assert.deepEqual(
    comments.map((c) => c.body),
    ["Boueux après la pluie.", "Bien éclairé le soir."],
  );
  assert.deepEqual(Object.keys(comments[0]).sort(), ["author", "body", "created_at", "id"]);
  const response = await call(env, "GET", `/api/comments?spot=${SPOT}`);
  assert.equal((await response.json()).sitekey, "site-key");
  assert.equal(response.headers.get("cache-control"), "no-store");
});

test("writes need Turnstile, the rate limit and the site's origin", async () => {
  const env = setup();
  assert.equal((await post(env, { token: "robot" })).status, 403);
  assert.equal((await post(env, { token: undefined })).status, 403);
  assert.equal(
    (
      await call(
        env,
        "POST",
        "/api/comments",
        { spot: SPOT, author: "a", body: "abc", token: "human" },
        { origin: "https://evil.example" },
      )
    ).status,
    403,
  );
  assert.equal((await post(setup({ limit: false }), {})).status, 429);
  assert.equal((await post({ ...setup(), TURNSTILE_SECRET: undefined }, {})).status, 403);
  assert.equal((await post(env, { body: "a" })).status, 400);
  assert.equal((await call(env, "GET", "/api/comments?spot=nope")).status, 400);
  assert.deepEqual(await list(env), []);
});

test(`a comment is hidden after ${REPORTS_TO_HIDE} reports, unless approved`, async () => {
  const env = setup();
  const { comment } = await (await post(env, {})).json();
  const report = (id, token = "human") => call(env, "POST", `/api/comments/${id}/report`, { token });
  assert.equal((await report(comment.id, "robot")).status, 403);
  for (let i = 0; i < REPORTS_TO_HIDE - 1; i++) assert.equal((await report(comment.id)).status, 200);
  assert.equal((await list(env)).length, 1);
  await report(comment.id);
  assert.equal((await list(env)).length, 0);
  // The moderator puts it back: approved, reports no longer hide it.
  await call(env, "POST", `/api/admin/comments/${comment.id}`, { status: "approved" }, asAdmin);
  await report(comment.id);
  await report(comment.id);
  assert.equal((await list(env)).length, 1);
});

test("moderation needs the admin password", async () => {
  const env = setup();
  await post(env, {});
  assert.equal((await call(env, "GET", "/api/admin/comments")).status, 401);
  assert.equal(
    (await call(env, "GET", "/api/admin/comments", undefined, { authorization: "Bearer wrong" })).status,
    401,
  );
  assert.equal(
    (await call({ ...env, ADMIN_TOKEN: undefined }, "GET", "/api/admin/comments", undefined, asAdmin)).status,
    503,
  );
  const { comments } = await (await call(env, "GET", "/api/admin/comments?view=recent", undefined, asAdmin)).json();
  assert.equal(comments.length, 1);
  assert.equal(comments[0].spot_id, SPOT);
  assert.equal(comments[0].status, "visible");
});

test("the moderator lists reported and hidden comments, hides and deletes", async () => {
  const env = setup();
  const first = (await (await post(env, {})).json()).comment;
  const second = (await (await post(env, { body: "Second." })).json()).comment;
  await call(env, "POST", `/api/comments/${first.id}/report`, { token: "human" });
  const view = async (name) =>
    (await (await call(env, "GET", `/api/admin/comments?view=${name}`, undefined, asAdmin)).json()).comments.map(
      (c) => c.id,
    );
  assert.deepEqual(await view("reported"), [first.id]);
  assert.equal((await call(env, "POST", `/api/admin/comments/${second.id}`, { status: "nope" }, asAdmin)).status, 400);
  await call(env, "POST", `/api/admin/comments/${second.id}`, { status: "hidden" }, asAdmin);
  assert.deepEqual(await view("hidden"), [second.id]);
  assert.deepEqual(
    (await list(env)).map((c) => c.id),
    [first.id],
  );
  await call(env, "DELETE", `/api/admin/comments/${first.id}`, undefined, asAdmin);
  assert.deepEqual(await view("recent"), [second.id]);
  assert.equal((await call(env, "GET", "/api/admin/comments?view=nope", undefined, asAdmin)).status, 400);
});

test("other paths go to the static files", async () => {
  const env = { ...setup(), ASSETS: { fetch: async () => new Response("page") } };
  assert.equal(await (await call(env, "GET", "/index.html")).text(), "page");
  assert.equal((await call(env, "GET", "/api/nothing")).status, 404);
  assert.equal((await call({ ...env, DB: undefined }, "GET", `/api/comments?spot=${SPOT}`)).status, 503);
});
