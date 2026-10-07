// Visit counting with GoatCounter (no cookies, no personal data).
// The counter script is vendored in vendor/goatcounter-count.js (ISC licence).

export const GOATCOUNTER_ENDPOINT = "https://spot-fractionne.goatcounter.com/count";

// Query parameters worth keeping in the counted path: which segment a shared
// link opens and which category. The position (lat, lon) is dropped.
const KEPT_PARAMS = ["id", "kind"];

/** Path recorded for a visit: the page path plus the kept query parameters. */
export function analyticsPath(pathname, search) {
  const params = new URLSearchParams(search);
  const kept = new URLSearchParams();
  for (const name of KEPT_PARAMS) {
    const value = params.get(name);
    if (value) kept.set(name, value);
  }
  const query = kept.toString();
  return (pathname || "/") + (query ? `?${query}` : "");
}

if (typeof document !== "undefined") {
  window.goatcounter = { path: () => analyticsPath(location.pathname, location.search) };
  const script = document.createElement("script");
  script.async = true;
  script.src = "vendor/goatcounter-count.js";
  script.dataset.goatcounter = GOATCOUNTER_ENDPOINT;
  document.head.append(script);
}
