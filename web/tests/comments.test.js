import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  AUTHOR_MAX,
  BODY_MAX,
  BODY_MIN,
  commentHtml,
  commentsTitle,
  commentsUrl,
  draftError,
  formatCommentDate,
  reportUrl,
} from "../comments.js";

test("API URLs, relative to the page", () => {
  assert.equal(commentsUrl("climb-0123456789ab-2"), "api/comments?spot=climb-0123456789ab-2");
  assert.equal(reportUrl(42), "api/comments/42/report");
});

test("the page checks drafts with the Worker's limits", () => {
  const worker = readFileSync(new URL("../../worker/comments.js", import.meta.url), "utf8");
  for (const [name, value] of Object.entries({ AUTHOR_MAX, BODY_MIN, BODY_MAX })) {
    assert.match(worker, new RegExp(`export const ${name} = ${value};`), name);
  }
  assert.equal(draftError({ author: "Léa", body: "Bien éclairé" }), null);
  assert.match(draftError({ author: " ", body: "Bien" }), /pseudo/);
  assert.match(draftError({ author: "a".repeat(AUTHOR_MAX + 1), body: "Bien" }), /Pseudo trop long/);
  assert.match(draftError({ author: "Léa", body: "ok" }), /Écrivez/);
  assert.match(draftError({ author: "Léa", body: "x".repeat(BODY_MAX + 1) }), /trop long/);
});

test("comment dates read like a conversation", () => {
  const now = new Date(2026, 9, 16, 18, 0);
  const at = (y, m, d, h = 12) => new Date(y, m, d, h).toISOString();
  assert.equal(formatCommentDate(at(2026, 9, 16, 8), now), "aujourd'hui");
  assert.equal(formatCommentDate(at(2026, 9, 15, 23), now), "hier");
  assert.equal(formatCommentDate(at(2026, 9, 12), now), "il y a 4 jours");
  assert.match(formatCommentDate(at(2026, 8, 2), now), /2 sept\.? 2026/);
  assert.equal(formatCommentDate("nope", now), "");
});

test("comments are shown as text, never as markup", () => {
  const comment = {
    id: 7,
    author: "<b>Léa</b>",
    body: 'Boueux\n<img src=x onerror="alert(1)">',
    created_at: "2026-10-16T08:00:00Z",
  };
  const html = commentHtml(comment, new Date("2026-10-16T18:00:00Z"));
  assert.ok(html.includes("&lt;b&gt;Léa&lt;/b&gt;"));
  assert.ok(html.includes("Boueux<br>&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"));
  assert.ok(html.includes('data-id="7"'));
  assert.ok(!html.includes("<img"));
  const reported = commentHtml(comment, new Date(), true);
  assert.ok(reported.includes("Signalé") && !reported.includes("<button"));
});

test("the section title counts the comments", () => {
  assert.equal(commentsTitle(0), "Commentaires");
  assert.equal(commentsTitle(3), "Commentaires (3)");
});
