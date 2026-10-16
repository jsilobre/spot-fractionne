// Comments API of the site's Worker (ADR 0019): the comments left by visitors
// on a spot, stored in D1. index.js is the Worker's entry point; this module
// holds the logic, tested with `node --test` (a Worker module may only export
// handlers).
//
// Public:
//   GET  /api/comments?spot=<id>       comments of a spot, newest first, + Turnstile site key
//   POST /api/comments                 {spot, author, body, token}
//   POST /api/comments/<n>/report      {token}; hidden after REPORTS_TO_HIDE reports
// Moderation (header "Authorization: Bearer <ADMIN_TOKEN>"):
//   GET    /api/admin/comments?view=recent|reported|hidden
//   POST   /api/admin/comments/<n>     {status: visible|hidden|approved}
//   DELETE /api/admin/comments/<n>
//
// Bindings: DB (D1), WRITE_LIMIT (rate limit by IP address, never stored),
// ASSETS; vars TURNSTILE_SITEKEY; secrets TURNSTILE_SECRET, ADMIN_TOKEN.

/** A spot id, as lineage.py writes it. */
export const SPOT_ID = /^(flat|climb|long_climb|loop)-[0-9a-f]{12}(-[0-9]+)?$/;
export const AUTHOR_MAX = 40;
export const BODY_MIN = 3;
export const BODY_MAX = 1000;
export const REPORTS_TO_HIDE = 3;
const LIST_MAX = 200;
const STATUSES = ["visible", "hidden", "approved"];
const TURNSTILE_VERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify";

/**
 * Tidy text typed by a visitor: no control characters (line breaks kept in
 * the body), no blank runs, trimmed.
 */
export function cleanText(text, multiline) {
  let s = String(text ?? "")
    .normalize("NFC")
    .replace(/\r\n?/g, "\n");
  s = s.replace(multiline ? /[^\P{Cc}\n]/gu : /\p{Cc}/gu, " ");
  s = s.replace(/[^\S\n]+/g, " ");
  if (multiline) s = s.replace(/ ?\n ?/g, "\n").replace(/\n{3,}/g, "\n\n");
  return s.trim();
}

/** Check a new comment: {spot, author, body} cleaned, or {error} in French. */
export function validateComment(input) {
  const spot = String(input?.spot ?? "");
  if (!SPOT_ID.test(spot)) return { error: "Spot inconnu." };
  const author = cleanText(input?.author, false);
  const body = cleanText(input?.body, true);
  if (!author) return { error: "Indiquez un pseudo." };
  if ([...author].length > AUTHOR_MAX) return { error: `Pseudo trop long (${AUTHOR_MAX} caractères au plus).` };
  if ([...body].length < BODY_MIN) return { error: "Commentaire trop court." };
  if ([...body].length > BODY_MAX) return { error: `Commentaire trop long (${BODY_MAX} caractères au plus).` };
  if ((body.match(/https?:\/\/|www\./gi) ?? []).length > 2) return { error: "Trop de liens dans le commentaire." };
  return { spot, author, body };
}

/** Compare two strings in a time that does not depend on where they differ. */
export async function sameSecret(given, expected) {
  if (!given || !expected) return false;
  const digest = async (s) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)));
  const [a, b] = await Promise.all([digest(given), digest(expected)]);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });

const fail = (status, error) => json({ error }, status);

async function readJson(request) {
  if (!(request.headers.get("content-type") ?? "").includes("application/json")) return null;
  try {
    return await request.json();
  } catch {
    return null;
  }
}

/** Whether Turnstile vouches for this visitor (token from the page's widget). */
async function humanChecked(env, token, ip, fetchImpl) {
  if (!env.TURNSTILE_SECRET || typeof token !== "string" || !token) return false;
  const form = new FormData();
  form.append("secret", env.TURNSTILE_SECRET);
  form.append("response", token);
  if (ip) form.append("remoteip", ip);
  try {
    const response = await fetchImpl(TURNSTILE_VERIFY, { method: "POST", body: form });
    return (await response.json()).success === true;
  } catch {
    return false;
  }
}

/** Common checks of a write: same origin, rate limit, Turnstile. An error Response, or null. */
async function checkWrite(request, env, token, fetchImpl) {
  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin) return fail(403, "Origine refusée.");
  const ip = request.headers.get("cf-connecting-ip") ?? "";
  if (env.WRITE_LIMIT) {
    const { success } = await env.WRITE_LIMIT.limit({ key: ip || "unknown" });
    if (!success) return fail(429, "Trop d'envois d'un coup : réessayez dans une minute.");
  }
  if (!(await humanChecked(env, token, ip, fetchImpl))) {
    return fail(403, "Vérification anti-robot échouée : réessayez.");
  }
  return null;
}

async function listComments(env, url) {
  const spot = url.searchParams.get("spot") ?? "";
  if (!SPOT_ID.test(spot)) return fail(400, "Spot inconnu.");
  const { results } = await env.DB.prepare(
    `SELECT id, author, body, created_at FROM comments
     WHERE spot_id = ? AND status != 'hidden' ORDER BY created_at DESC, id DESC LIMIT ?`,
  )
    .bind(spot, LIST_MAX)
    .all();
  return json({ comments: results, sitekey: env.TURNSTILE_SITEKEY ?? null });
}

async function addComment(request, env, fetchImpl) {
  const input = await readJson(request);
  if (!input) return fail(400, "Requête invalide.");
  const comment = validateComment(input);
  if (comment.error) return fail(400, comment.error);
  const refused = await checkWrite(request, env, input.token, fetchImpl);
  if (refused) return refused;
  const row = await env.DB.prepare(
    "INSERT INTO comments (spot_id, author, body) VALUES (?, ?, ?) RETURNING id, author, body, created_at",
  )
    .bind(comment.spot, comment.author, comment.body)
    .first();
  return json({ comment: row }, 201);
}

async function reportComment(request, env, id, fetchImpl) {
  const input = await readJson(request);
  if (!input) return fail(400, "Requête invalide.");
  const refused = await checkWrite(request, env, input.token, fetchImpl);
  if (refused) return refused;
  await env.DB.prepare(
    `UPDATE comments SET reports = reports + 1,
       status = CASE WHEN status = 'visible' AND reports + 1 >= ? THEN 'hidden' ELSE status END
     WHERE id = ?`,
  )
    .bind(REPORTS_TO_HIDE, id)
    .run();
  return json({ ok: true });
}

async function admin(request, env, url, id) {
  if (!env.ADMIN_TOKEN) return fail(503, "Modération non configurée (secret ADMIN_TOKEN).");
  const given = (request.headers.get("authorization") ?? "").replace(/^Bearer /, "");
  if (!(await sameSecret(given, env.ADMIN_TOKEN))) return fail(401, "Mot de passe refusé.");
  if (id === null && request.method === "GET") {
    const view = url.searchParams.get("view") ?? "recent";
    const where = { recent: "1 = 1", reported: "reports > 0 AND status != 'approved'", hidden: "status = 'hidden'" }[
      view
    ];
    if (!where) return fail(400, "Vue inconnue.");
    const { results } = await env.DB.prepare(
      `SELECT id, spot_id, author, body, created_at, status, reports FROM comments
       WHERE ${where} ORDER BY created_at DESC, id DESC LIMIT ?`,
    )
      .bind(LIST_MAX)
      .all();
    return json({ comments: results });
  }
  if (id !== null && request.method === "POST") {
    const status = (await readJson(request))?.status;
    if (!STATUSES.includes(status)) return fail(400, "Statut inconnu.");
    await env.DB.prepare("UPDATE comments SET status = ? WHERE id = ?").bind(status, id).run();
    return json({ ok: true });
  }
  if (id !== null && request.method === "DELETE") {
    await env.DB.prepare("DELETE FROM comments WHERE id = ?").bind(id).run();
    return json({ ok: true });
  }
  return fail(405, "Méthode non permise.");
}

/** Answer a request; `fetchImpl` is replaced in tests. */
export async function handle(request, env, fetchImpl = fetch) {
  const url = new URL(request.url);
  const path = url.pathname;
  if (!path.startsWith("/api/")) return env.ASSETS ? env.ASSETS.fetch(request) : fail(404, "Introuvable.");
  if (!env.DB) return fail(503, "Commentaires indisponibles.");
  try {
    if (path === "/api/comments") {
      if (request.method === "GET") return await listComments(env, url);
      if (request.method === "POST") return await addComment(request, env, fetchImpl);
      return fail(405, "Méthode non permise.");
    }
    let match = path.match(/^\/api\/comments\/(\d+)\/report$/);
    if (match) {
      if (request.method !== "POST") return fail(405, "Méthode non permise.");
      return await reportComment(request, env, Number(match[1]), fetchImpl);
    }
    match = path.match(/^\/api\/admin\/comments(?:\/(\d+))?$/);
    if (match) return await admin(request, env, url, match[1] ? Number(match[1]) : null);
    return fail(404, "Introuvable.");
  } catch (error) {
    console.error(error);
    return fail(500, "Erreur du serveur : réessayez plus tard.");
  }
}
