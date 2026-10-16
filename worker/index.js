// Cloudflare Worker of the site (ADR 0019): the static files are served by
// Workers static assets (wrangler.jsonc); this script only answers what no
// file matches, i.e. the comments API under /api/ (comments.js).

import { handle } from "./comments.js";

export default {
  fetch: (request, env) => handle(request, env),
};
