// Moderation page of the comments (ADR 0019): lists recent, reported and
// hidden comments, and hides, approves or deletes them through the Worker's
// /api/admin/ routes. The password is the Worker secret ADMIN_TOKEN, kept
// for the browser tab only (sessionStorage).

const TOKEN_KEY = "moderation-token";
const API = "api/admin/comments";
const STATUS_LABELS = { visible: "visible", hidden: "masqué", approved: "validé" };

const $ = (id) => document.getElementById(id);
let view = "reported";

function readToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function writeToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // storage blocked: the password is asked again at the next load
  }
}

let token = readToken();

function escapeHtml(text) {
  return String(text).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

async function api(path, init = {}) {
  const response = await fetch(path, {
    ...init,
    headers: { ...init.headers, authorization: `Bearer ${token}` },
  });
  const body = (response.headers.get("content-type") ?? "").includes("application/json") ? await response.json() : {};
  if (response.status === 401) {
    showLogin("Mot de passe refusé.");
    throw new Error("unauthorized");
  }
  if (!response.ok) throw new Error(body.error ?? `Erreur ${response.status}`);
  return body;
}

function showLogin(message = "") {
  token = null;
  writeToken(null);
  $("login").hidden = false;
  $("views").hidden = true;
  $("table").hidden = true;
  $("status").textContent = message;
}

function row(c) {
  const date = new Date(c.created_at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
  const actions = [
    c.status !== "hidden" ? `<button data-action="hidden">Masquer</button>` : "",
    c.status !== "approved" ? `<button data-action="approved">Valider</button>` : "",
    `<button data-action="delete">Supprimer</button>`,
  ].join("");
  return `<tr data-id="${c.id}" class="${c.status}">
    <td>${escapeHtml(date)}</td>
    <td><a href="./?id=${encodeURIComponent(c.spot_id)}" target="_blank" rel="noopener">${escapeHtml(c.spot_id)}</a></td>
    <td>${escapeHtml(c.author)}</td>
    <td class="body">${escapeHtml(c.body)}</td>
    <td>${STATUS_LABELS[c.status] ?? escapeHtml(c.status)}${c.reports ? `, ${c.reports} signalement${c.reports > 1 ? "s" : ""}` : ""}</td>
    <td class="actions">${actions}</td>
  </tr>`;
}

async function refresh() {
  for (const button of $("views").querySelectorAll("[data-view]")) {
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  }
  $("status").textContent = "Chargement…";
  try {
    const { comments } = await api(`${API}?view=${view}`);
    $("login").hidden = true;
    $("views").hidden = false;
    $("table").hidden = !comments.length;
    $("table").querySelector("tbody").innerHTML = comments.map(row).join("");
    $("status").textContent = comments.length ? "" : "Aucun commentaire ici.";
  } catch (error) {
    if (error.message !== "unauthorized") $("status").textContent = error.message;
  }
}

$("login").addEventListener("submit", (event) => {
  event.preventDefault();
  token = $("password").value.trim();
  writeToken(token);
  $("password").value = "";
  refresh();
});

$("logout").addEventListener("click", () => showLogin());

$("views").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (!button) return;
  view = button.dataset.view;
  refresh();
});

$("table").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const id = button.closest("tr").dataset.id;
  const action = button.dataset.action;
  if (action === "delete" && !confirm("Supprimer définitivement ce commentaire ?")) return;
  try {
    if (action === "delete") await api(`${API}/${id}`, { method: "DELETE" });
    else {
      await api(`${API}/${id}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ status: action }),
      });
    }
    refresh();
  } catch (error) {
    if (error.message !== "unauthorized") $("status").textContent = error.message;
  }
});

if (token) refresh();
else showLogin();
