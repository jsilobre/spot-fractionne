// Comments left by visitors on a spot (ADR 0019), served by the site's Worker
// under /api/comments (worker/comments.js). Pure helpers, tested with
// `node --test`, plus the loader of Cloudflare Turnstile, the anti-robot check
// required to post or report.

export const COMMENTS_API = "api/comments";
// Same limits as the Worker (worker/comments.js).
export const AUTHOR_MAX = 40;
export const BODY_MIN = 3;
export const BODY_MAX = 1000;
const TURNSTILE_SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

/** URL of the comments of a spot. */
export const commentsUrl = (spotId) => `${COMMENTS_API}?${new URLSearchParams({ spot: spotId })}`;

/** URL to report a comment. */
export const reportUrl = (commentId) => `${COMMENTS_API}/${encodeURIComponent(commentId)}/report`;

/** What is wrong with a draft, in French, or null. */
export function draftError({ author, body }) {
  const a = String(author ?? "").trim();
  const b = String(body ?? "").trim();
  if (!a) return "Indiquez un pseudo.";
  if ([...a].length > AUTHOR_MAX) return `Pseudo trop long (${AUTHOR_MAX} caractères au plus).`;
  if ([...b].length < BODY_MIN) return "Écrivez votre commentaire.";
  if ([...b].length > BODY_MAX) return `Commentaire trop long (${BODY_MAX} caractères au plus).`;
  return null;
}

/** "aujourd'hui", "hier", "il y a 3 jours", else "12 oct. 2026". `iso` is UTC. */
export function formatCommentDate(iso, now = new Date()) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const day = (d) => Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  const days = Math.round((day(now) - day(date)) / 86_400_000);
  if (days <= 0) return "aujourd'hui";
  if (days === 1) return "hier";
  if (days < 7) return `il y a ${days} jours`;
  return date.toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
}

/** Summary line of the comments section, e.g. "Commentaires (2)". */
export function commentsTitle(count) {
  return count > 0 ? `Commentaires (${count})` : "Commentaires";
}

function escapeHtml(text) {
  return String(text).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

/** One comment as list item: pseudonym, date, report button, text (line breaks kept, no links). */
export function commentHtml(comment, now = new Date(), reported = false) {
  const report = reported
    ? `<span class="comment-reported">Signalé</span>`
    : `<button type="button" class="comment-report" data-id="${escapeHtml(comment.id)}">Signaler</button>`;
  return `<li class="comment">
    <p class="comment-meta"><strong>${escapeHtml(comment.author)}</strong> · ${escapeHtml(formatCommentDate(comment.created_at, now))} ${report}</p>
    <p class="comment-body">${escapeHtml(comment.body).replaceAll("\n", "<br>")}</p>
  </li>`;
}

let turnstileLoading = null;

/** Load Cloudflare Turnstile once; resolves with `window.turnstile`. */
export function loadTurnstile() {
  turnstileLoading ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = TURNSTILE_SCRIPT;
    script.async = true;
    script.onload = () => resolve(window.turnstile);
    script.onerror = () => {
      turnstileLoading = null; // retried next time
      reject(new Error("Turnstile unavailable"));
    };
    document.head.append(script);
  });
  return turnstileLoading;
}

/**
 * An invisible Turnstile widget in `container`: `token()` resolves with a
 * fresh single-use token (the widget is reset after each use).
 */
export async function turnstileWidget(container, sitekey) {
  const turnstile = await loadTurnstile();
  let current = null;
  let waiting = [];
  const settle = (value) => {
    current = value;
    if (value) for (const resolve of waiting) resolve(value);
    if (value) waiting = [];
  };
  const id = turnstile.render(container, {
    sitekey,
    size: "flexible",
    appearance: "interaction-only",
    language: "fr",
    callback: settle,
    "expired-callback": () => settle(null),
    "error-callback": () => settle(null),
  });
  return {
    async token(timeoutMs = 20_000) {
      const token =
        current ??
        (await new Promise((resolve, reject) => {
          waiting.push(resolve);
          setTimeout(() => reject(new Error("Turnstile timeout")), timeoutMs);
        }));
      current = null;
      turnstile.reset(id);
      return token;
    },
    remove: () => turnstile.remove(id),
  };
}
