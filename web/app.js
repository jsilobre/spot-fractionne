// Map page: reads precomputed segments from vector tiles (PMTiles), builds the
// result list from the tiles around the search point (filters.js, tiles.js)
// and shows on the map only the segments of that list. No build step.
import * as maplibregl from "maplibre-gl";
import { PMTiles, Protocol } from "pmtiles";

import {
  buildUrlSearch,
  circlePolygon,
  DEFAULT_CRITERIA,
  effortLengthM,
  featuresBounds,
  filterSegments,
  haversineMeters,
  isLoop,
  lineParts,
  mapFilter,
  overviewFilter,
  parseLatLon,
  parseUrlState,
  sortResults,
  steepGrade,
} from "./filters.js";
import { geocodeUrl, parseGeocodeResults } from "./geocode.js";
import {
  dataUrl,
  decodeTile,
  indexKey,
  normalizeProperties,
  resolveIndexEntry,
  segmentsFromTiles,
  tilesCoveringCircle,
} from "./tiles.js";
import { BASEMAPS, basemapById } from "./basemaps.js";
import { navigationUrl, parseRoute, routeUrl, segmentStart } from "./route.js";
import {
  buildProfile,
  chartPoint,
  distanceAtX,
  elevationRequest,
  GRADE_CLASSES,
  gradeAt,
  nearestSample,
  orderedLine,
  parseElevations,
  profileSvg,
  RESOURCES,
} from "./profile.js";

// Published tileset, or the fictitious sample when there is none.
const DATA_DIRS = ["data/", "data/sample/"];
const BASEMAP_KEY = "basemap"; // localStorage key of the chosen basemap
const DETAILS_KEY = "segment-details"; // localStorage key: "open" if the popup details were left open
const PROFILE_KEY = "segment-profile"; // localStorage key: "open" if the elevation profile was left open
// Narrow screens (same width as style.css): the popup is a sheet along the bottom of the map.
const SHEET_QUERY = matchMedia("(max-width: 720px)");
const FALLBACK_STYLE = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#eef0ea" } }],
};
const INITIAL_VIEW = { center: [1.535, 43.53], zoom: 13 };
const MAX_RESULTS = 50;
const COLORS = { flat: "#1f6fb2", climb: "#d4570f", long_climb: "#a0207a", loop: "#2a8a4a" };
const ROUTE_COLOR = "#e0245e"; // the position marker's color
// Radius of the tiles read to open a linked segment around its indexed position.
const LINK_RADIUS_M = 1500;
// Pause in the typing before suggesting addresses.
const SUGGEST_DELAY_MS = 300;
// Offer to search again once the map center is this far from the search
// center, as a fraction of the search radius.
const SEARCH_HERE_FRACTION = 0.25;

const NO_POSITION_STATUS =
  "Aucune position : déplacez la carte puis « Rechercher ici », ou choisissez une position ci-dessus.";

const SURFACE_LABELS = {
  paved: "revêtu (asphalte, béton…)",
  compacted: "stabilisé",
  gravel: "gravier",
  cobbles: "pavés",
  unpaved: "terre, herbe",
  unknown: "inconnu",
};
const LIT_LABELS = { yes: "oui", partial: "en partie", no: "non", unknown: "inconnu" };
const ACCESS_LABELS = { public: "libre", restricted: "réservé (club, école…)", unknown: "inconnu" };
const FLAG_LABELS = {
  bridge_interpolated: "altitude interpolée sur un pont",
  tunnel_interpolated: "altitude interpolée dans un tunnel",
  gap_filled: "trou du MNT comblé",
  dem_coarse: "MNT grossier (30 m)",
};

const $ = (id) => document.getElementById(id);

const state = {
  metadata: null, // segments.json of the tileset
  dataBase: null, // absolute URL of the folder of segments.json
  archive: null, // PMTiles
  tiles: new Map(), // "z/x/y" -> Promise of a decoded tile (or null)
  results: [],
  searchArea: null, // {center, radiusM} of the latest result query
  query: 0, // id of the latest result query (older answers are ignored)
  missingTiles: 0, // tiles of the latest query that could not be read
  position: null,
  positionLabel: "", // how the position was given
  // Search center without a position: set once (initial view or linked
  // segment). The circle never follows the map; it moves only on an explicit
  // action (position, "search here").
  fallbackCenter: null,
  criteria: { ...DEFAULT_CRITERIA },
  sortBy: "distance",
  selectedId: null,
  selectedFeature: null, // the whole selected segment, for its route
  // Walking route from the position to the selected segment:
  // {coordinates, distanceM, durationS}, "loading", or an error message.
  route: null,
  routeQuery: 0, // id of the latest route request (older answers are ignored)
  // Elevation profiles by segment id: a profile (profile.js), "loading" or an error message.
  profiles: new Map(),
  profileScroll: false, // scroll the phone sheet to the profile once drawn
  pinnedId: null, // segment opened by a link: shown even if the filters exclude it
  placing: false, // the next click on the map sets the position
};

// --- formatting ---------------------------------------------------------------

const fmt = (value, digits = 0) =>
  value === null || value === undefined
    ? "—"
    : Number(value).toLocaleString("fr-FR", { maximumFractionDigits: digits });

const formatLength = (m) => (m >= 1000 ? `${fmt(m / 1000, 2)} km` : `${fmt(m)} m`);
/** A distance between places: whole kilometres from 10 km. */
const formatDistance = (m) => (m >= 10_000 ? `${fmt(Math.round(m / 1000))} km` : formatLength(m));

const kindLabel = (kind) =>
  ({ flat: "Plat", climb: "Côte", long_climb: "Côte longue", loop: "Piste" })[kind] ?? kind;

/** Circuits of the network, by setting. */
const SETTING_LABELS = { water: "Tour de lac", park: "Tour de parc", neighbourhood: "Boucle de quartier" };

const isCircuit = (p) => p.kind === "loop" && p.loop_type === "circuit";

/** "Piste", "Tour de lac"... */
const loopLabel = (p) => (isCircuit(p) ? (SETTING_LABELS[p.setting] ?? "Boucle") : kindLabel(p.kind));

/** Lap of a loop: a track's standard length, else "about" its measured length; a circuit's length. */
const formatLap = (p) => {
  if (isCircuit(p)) return formatLength(p.length_m);
  return p.lap_m != null ? formatLength(p.lap_m) : `environ ${formatLength(p.length_m)}`;
};

/** Time to run `lengthM` at `paceSecPerKm`, e.g. "6 min 30". */
const formatDuration = (lengthM, paceSecPerKm) => {
  const seconds = Math.round(((lengthM / 1000) * paceSecPerKm) / 10) * 10;
  const rest = seconds % 60;
  return `${Math.floor(seconds / 60)} min${rest ? ` ${String(rest).padStart(2, "0")}` : ""}`;
};

/** A walking time in minutes, e.g. "12 min" or "1 h 05". */
const formatWalk = (seconds) => {
  const minutes = Math.max(1, Math.round(seconds / 60));
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
};

const crossings = (n) => `${fmt(n)} traversée${n > 1 ? "s" : ""}`;

function titleOf(properties) {
  const p = properties;
  if (p.name) return p.name;
  if (p.kind === "loop") return `${loopLabel(p)} de ${formatLap(p)}`;
  return `${kindLabel(p.kind)} de ${formatLength(p.length_m)}`;
}

function escapeHtml(text) {
  return String(text).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

/** Laps needed to cover `distanceM`, e.g. "2,5 tours". */
function laps(p, distanceM) {
  const n = distanceM / (p.lap_m ?? p.length_m);
  return `${fmt(n, 1)} tour${n >= 2 ? "s" : ""}`;
}

/**
 * The popup: title, one-line summary and route always shown, the rest folded
 * under "Détails" (left open or closed from one segment to the next).
 */
function popupFrame(p, distanceM, detailsHtml) {
  const flagged = p.quality_flags?.length ? " ⚠" : "";
  // An athletics track is flat by construction: no profile.
  const profile =
    p.kind === "loop" && !isCircuit(p)
      ? ""
      : `<details class="popup-profile"${readStored(PROFILE_KEY) === "open" ? " open" : ""}>
      <summary>Profil altimétrique</summary>
      <div class="profile-box"></div>
    </details>`;
  return `<div class="popup">
    <h3>${escapeHtml(titleOf(p))}</h3>
    <p class="popup-summary">${escapeHtml(summaryParts(p, distanceM).join(" · "))}</p>
    <div class="route-box"></div>
    ${profile}
    <details class="popup-details"${readStored(DETAILS_KEY) === "open" ? " open" : ""}>
      <summary>Détails${flagged}</summary>
      ${detailsHtml}
    </details>
  </div>`;
}

function loopPopupHtml(p, distanceM) {
  const circuit = isCircuit(p);
  const rows = circuit
    ? [
        ["Type", loopLabel(p)],
        ["Tour", formatLap(p)],
        ["Pour 1 km / 5 km", `${laps(p, 1000)} / ${laps(p, 5000)}`],
        ["Traversées de rue", fmt(p.n_crossings)],
        ["Pente locale max", `${fmt(p.grade_max_pct, 1)} %`],
      ]
    : [
        ["Tour", formatLap(p)],
        ["Longueur mesurée", formatLength(p.length_m)],
        ["Pour 1 km / 5 km", `${laps(p, 1000)} / ${laps(p, 5000)}`],
        ["Accès", ACCESS_LABELS[p.access] ?? p.access],
      ];
  if (p.opening_hours) rows.push(["Horaires (OSM)", p.opening_hours]);
  rows.push(["Revêtement", SURFACE_LABELS[p.surface] ?? p.surface], ["Éclairage", LIT_LABELS[p.lit] ?? p.lit]);
  if (distanceM !== null && distanceM !== undefined) rows.push(["Distance", formatLength(distanceM)]);
  const link = buildUrlSearch({ id: p.id, kind: "loop" }) || "?";
  return popupFrame(
    p,
    distanceM,
    `<dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(v)}</dd>`).join("")}</dl>
    <p class="hint"><a href="${escapeHtml(link)}">Lien direct vers cette ${circuit ? "boucle" : "piste"}</a></p>`,
  );
}

/** Rows "Plus raide sur 100 m"… of a climb, for the lengths it covers. */
function steepRows(p) {
  if (p.kind === "flat") return [];
  return [100, 200, 400]
    .filter((lengthM) => p[`steep_${lengthM}_pct`] != null)
    .map((lengthM) => [`Plus raide sur ${formatLength(lengthM)}`, `${fmt(p[`steep_${lengthM}_pct`], 1)} %`]);
}

function popupHtml(properties, distanceM) {
  const p = properties;
  if (p.kind === "loop") return loopPopupHtml(p, distanceM);
  const rows = [
    ["Longueur", formatLength(p.length_m)],
    ["Pente moyenne", `${fmt(p.grade_mean_pct, 1)} %`],
    ["Pente locale max", `${fmt(p.grade_max_pct, 1)} %`],
    ...steepRows(p),
    ["D+ / D-", `${fmt(p.elev_gain_m, 1)} / ${fmt(p.elev_loss_m, 1)} m`],
    ["Traversées de route", fmt(p.n_crossings)],
    ["Carrefours", fmt(p.n_junctions)],
    ["Sinuosité", isLoop(p) ? "boucle" : fmt(p.sinuosity, 2)],
    ["Revêtement", SURFACE_LABELS[p.surface] ?? p.surface],
    ["Éclairage", LIT_LABELS[p.lit] ?? p.lit],
    ["Longueurs cibles", p.fits_targets_m.length ? p.fits_targets_m.map(formatLength).join(", ") : "—"],
    ["Score", `${fmt(p.score)} / 100`],
  ];
  if (distanceM !== null && distanceM !== undefined) rows.push(["Distance", formatLength(distanceM)]);
  const flags = p.quality_flags.map((f) => FLAG_LABELS[f] ?? f);
  const link = buildUrlSearch({ id: p.id }) || "?";
  return popupFrame(
    p,
    distanceM,
    `<dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(v)}</dd>`).join("")}</dl>
    ${flags.length ? `<p class="flags">⚠ ${escapeHtml(flags.join(" ; "))}</p>` : ""}
    <p class="hint"><a href="${escapeHtml(link)}">Lien direct vers ce segment</a></p>`,
  );
}

// --- map ----------------------------------------------------------------------

const protocol = new Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

let basemap = basemapById(readStored(BASEMAP_KEY));
const map = new maplibregl.Map({
  container: "map",
  style: basemap.style,
  ...INITIAL_VIEW,
  attributionControl: false, // added once the data attribution is known
});
globalThis.flatSegmentsMap = map; // handy for debugging from the browser console
map.addControl(new maplibregl.NavigationControl(), "top-right");
map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
map.addControl({ onAdd: basemapControl, onRemove() {} }, "top-right");

let usingFallback = false;
let styleReady = false;
map.on("error", (event) => {
  // The basemap is optional: if its style cannot be loaded, keep a plain background.
  if (!styleReady && !usingFallback) {
    usingFallback = true;
    console.warn("Basemap unavailable, using a plain background.", event.error);
    map.setStyle(FALLBACK_STYLE);
  } else {
    console.error(event.error);
  }
});

function readStored(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null; // storage blocked: default choice
  }
}

function writeStored(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // storage blocked: the choice lasts for this visit only
  }
}

/** Map control: a list of the basemaps, the choice kept for the next visit. */
function basemapControl() {
  const container = document.createElement("div");
  container.className = "maplibregl-ctrl maplibregl-ctrl-group basemap-control";
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Fond de carte");
  select.title = "Fond de carte";
  for (const { id, label } of BASEMAPS) select.append(new Option(label, id, false, id === basemap.id));
  select.addEventListener("change", () => {
    basemap = basemapById(select.value);
    writeStored(BASEMAP_KEY, basemap.id);
    // A failing basemap falls back to the plain background again.
    styleReady = false;
    usingFallback = false;
    // No diff: a full reload fires "style.load", which redraws our layers.
    map.setStyle(basemap.style, { diff: false });
  });
  container.append(select);
  return container;
}

const popup = new maplibregl.Popup({ maxWidth: "300px", className: "segment-popup" });
// The position: a marker (DOM element) that can be dragged to another place.
const marker = new maplibregl.Marker({
  element: Object.assign(document.createElement("div"), { className: "position-marker" }),
  draggable: true,
});
marker.on("dragend", () => {
  const { lng, lat } = marker.getLngLat();
  setPosition([lng, lat], "point déplacé");
});
const CLICK_SLACK_PX = 6;
// Clickable layers; a click picks the topmost, so results win over context.
const SEGMENT_LAYERS = ["segments", "overview", "context", "overview-context"];

function addDataLayers() {
  styleReady = true;
  map.addSource("search-area", { type: "geojson", data: emptyCollection() });
  // Below the segments, added after it.
  map.addLayer({
    id: "search-area",
    type: "line",
    source: "search-area",
    paint: { "line-color": "#4a4a4a", "line-width": 1.5, "line-opacity": 0.7, "line-dasharray": [3, 2] },
  });
  map.addSource("route", { type: "geojson", data: emptyCollection() });
  map.addLayer({
    id: "route",
    type: "line",
    source: "route",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": ROUTE_COLOR, "line-width": 4, "line-opacity": 0.85, "line-dasharray": [1, 1.5] },
  });
  if (state.archive) addSegmentLayers();
  // A style change (basemap fallback) removes our layers: redraw everything.
  render();
}

/**
 * Segment layers: full detail from zoom 12, a light overview below. Under
 * each, a faint "context" layer shows every segment meeting the criteria,
 * beyond the search circle.
 */
function addSegmentLayers() {
  if (map.getSource("segments")) return;
  const tiles = state.metadata.tiles;
  map.addSource("segments", {
    type: "vector",
    url: `pmtiles://${state.archive.source.getKey()}`,
    promoteId: { [tiles.layer]: "id", [tiles.overview_layer]: "id" },
  });
  const color = [
    "match",
    ["get", "kind"],
    "flat",
    COLORS.flat,
    "climb",
    COLORS.climb,
    "long_climb",
    COLORS.long_climb,
    COLORS.loop,
  ];
  const selected = ["boolean", ["feature-state", "selected"], false];
  // Zoom may only appear at the top level of an interpolate expression.
  const width = (low, high, factor) => [
    "interpolate",
    ["linear"],
    ["zoom"],
    8,
    ["case", selected, low * factor * 0.4, low * 0.4],
    11,
    ["case", selected, low * factor, low],
    16,
    ["case", selected, high * factor, high],
  ];
  const layer = (id, sourceLayer, paint, zooms) =>
    map.addLayer({
      id,
      type: "line",
      source: "segments",
      "source-layer": sourceLayer,
      layout: { "line-cap": "round", "line-join": "round" },
      paint,
      ...zooms,
    });
  const detail = { minzoom: tiles.minzoom };
  const overview = { minzoom: tiles.overview_minzoom, maxzoom: tiles.minzoom };
  const faint = ["case", selected, 1, 0.35];
  layer("overview-context", tiles.overview_layer, { "line-color": color, "line-width": width(2, 4, 1), "line-opacity": 0.3 }, overview);
  layer("context", tiles.layer, { "line-color": color, "line-width": width(2, 4, 1.8), "line-opacity": faint }, detail);
  layer("overview", tiles.overview_layer, { "line-color": color, "line-width": width(3, 7, 1), "line-opacity": 0.8 }, overview);
  layer("segments-casing", tiles.layer, { "line-color": "#ffffff", "line-width": width(6, 10, 1.6) }, detail);
  layer("segments", tiles.layer, { "line-color": color, "line-width": width(3, 7, 1.8) }, detail);
  updateMapFilters();
  // After a basemap change, highlight the selected segment again.
  if (state.selectedId !== null) {
    map.setFeatureState({ source: "segments", sourceLayer: tiles.layer, id: state.selectedId }, { selected: true });
  }
}

map.on("style.load", addDataLayers);

map.on("click", (event) => {
  if (state.placing) {
    setPosition([event.lngLat.lng, event.lngLat.lat], "point placé sur la carte");
    return;
  }
  const layers = SEGMENT_LAYERS.filter((id) => map.getLayer(id));
  // A few pixels of slack: the context lines are thin, fingers are not.
  const { x, y } = event.point;
  const box = [
    [x - CLICK_SLACK_PX, y - CLICK_SLACK_PX],
    [x + CLICK_SLACK_PX, y + CLICK_SLACK_PX],
  ];
  const [hit] = layers.length ? map.queryRenderedFeatures(box, { layers }) : [];
  if (hit?.layer.id === "segments" || hit?.layer.id === "context") {
    const feature = { type: "Feature", geometry: hit.geometry, properties: normalizeProperties(hit.properties) };
    selectSegment(hit.properties.id, event.lngLat, feature);
  } else if (hit) {
    // Overview: zoom in where the details (and the popup) are.
    map.flyTo({ center: event.lngLat, zoom: state.metadata.tiles.minzoom + 1 });
  }
  // Elsewhere: nothing (the popup closes by itself); the position moves only
  // with the "place" button or by dragging the marker.
});
for (const id of SEGMENT_LAYERS) {
  map.on("mouseenter", id, () => state.placing || (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", id, () => state.placing || (map.getCanvas().style.cursor = ""));
}
// The circle stays put when the map moves: a button offers to search here.
map.on("moveend", updateSearchHere);

const searchHere = Object.assign(document.createElement("button"), {
  type: "button",
  className: "search-here",
  textContent: "🔍 Rechercher ici",
  hidden: true,
});
searchHere.addEventListener("click", () => setPosition(mapCenter(), "centre de la carte"));
map.getContainer().append(searchHere);

$("results-away-search").addEventListener("click", () => setPosition(mapCenter(), "centre de la carte"));

/**
 * Show "search here" when the map center is away from the search circle, and
 * warn above the list when the circle is out of view: its results are
 * elsewhere, and clicking one would take the map there.
 */
function updateSearchHere() {
  const area = state.searchArea;
  const awayM = area ? haversineMeters(mapCenter(), area.center) : 0;
  searchHere.hidden = !area || awayM < area.radiusM * SEARCH_HERE_FRACTION;
  const away = Boolean(area) && !circleInView(area);
  $("results-away").hidden = !away;
  $("results").classList.toggle("away", away);
  if (away) {
    $("results-away-text").textContent =
      `Ces résultats sont autour du point de recherche, à ${formatDistance(awayM)} de la zone affichée.`;
  }
}

/** Whether a search circle shows at least partly in the map view. */
function circleInView({ center, radiusM }) {
  const bounds = map.getBounds();
  const [lon, lat] = center;
  const clampedLon = Math.min(Math.max(lon, bounds.getWest()), bounds.getEast());
  const clampedLat = Math.min(Math.max(lat, bounds.getSouth()), bounds.getNorth());
  return haversineMeters(center, [clampedLon, clampedLat]) <= radiusM;
}

function emptyCollection() {
  return { type: "FeatureCollection", features: [] };
}

// --- tiles --------------------------------------------------------------------

/** Decoded segment layer of a tile at the query zoom (cached), or null if empty. */
function loadTile(z, x, y) {
  const key = `${z}/${x}/${y}`;
  if (!state.tiles.has(key)) {
    const fetchTile = () => state.archive.getZxy(z, x, y);
    const promise = fetchTile()
      .catch(fetchTile) // one more try: mobile networks drop requests
      .then((response) => {
        if (!response) return null;
        const layer = decodeTile(new Uint8Array(response.data))[state.metadata.tiles.layer];
        return layer ? { z, x, y, layer } : null;
      });
    // A failure is not cached: the next query asks again.
    promise.catch(() => state.tiles.delete(key));
    state.tiles.set(key, promise);
  }
  return state.tiles.get(key);
}

/**
 * Segments (GeoJSON features) of the tiles covering a circle, and the number
 * of tiles that could not be read (their segments are missing).
 */
async function segmentsAround(center, radiusM) {
  const z = state.metadata.tiles.minzoom;
  const settled = await Promise.allSettled(
    tilesCoveringCircle(center, radiusM, z).map(([x, y]) => loadTile(z, x, y)),
  );
  const tiles = settled.filter((s) => s.status === "fulfilled").map((s) => s.value);
  const failed = settled.length - tiles.length;
  if (failed) console.warn(`${failed} tile(s) could not be read`, settled.find((s) => s.reason)?.reason);
  return { features: segmentsFromTiles(tiles.filter(Boolean)), failed };
}

function mapCenter() {
  const { lng, lat } = map.getCenter();
  return [lng, lat];
}

function searchCenter() {
  state.fallbackCenter ??= mapCenter();
  return state.position ?? state.fallbackCenter;
}

// --- state updates ------------------------------------------------------------

/**
 * Map filters: the criteria, and the ids of the result list, which hold the
 * distance limit (an expression cannot measure the distance to a line). The
 * context layers take the criteria only.
 */
function updateMapFilters() {
  if (!map.getLayer("segments")) return;
  const ids = state.results.map((r) => r.feature.properties.id);
  const filter = mapFilter(state.criteria, state.pinnedId, ids);
  map.setFilter("segments", filter);
  map.setFilter("segments-casing", filter);
  map.setFilter("overview", overviewFilter(state.criteria, ids));
  map.setFilter("context", mapFilter(state.criteria));
  map.setFilter("overview-context", overviewFilter(state.criteria));
}

function applyFilters() {
  updateMapFilters();
  refreshResults();
}

/** Recompute the result list from the tiles around the search center. */
async function refreshResults() {
  if (!state.archive) return;
  const query = ++state.query;
  const center = searchCenter();
  const { features, failed } = await segmentsAround(center, state.criteria.maxDistanceM);
  if (query !== state.query) return; // a newer query was started meanwhile
  state.results = sortResults(
    filterSegments(features, state.criteria, center, state.pinnedId),
    state.sortBy,
  );
  state.missingTiles = failed;
  state.searchArea = { center, radiusM: state.criteria.maxDistanceM };
  updateMapFilters();
  render();
  updateSearchHere();
}

function clearSelection() {
  const id = state.selectedId;
  state.selectedId = null; // before popup.remove(), which fires "close"
  state.selectedFeature = null;
  clearRoute();
  setProfileCursor(null);
  if (id !== null && map.getSource("segments")) {
    map.setFeatureState({ source: "segments", sourceLayer: state.metadata.tiles.layer, id }, { selected: false });
  }
  popup.remove();
  setSegmentOpen(false);
  updateUrl();
}

/**
 * On phones, a selected segment shrinks the panel below the map (results
 * list) so that more map shows around the segment and its route.
 */
function setSegmentOpen(open) {
  const changed = document.body.classList.contains("segment-open") !== open;
  document.body.classList.toggle("segment-open", open);
  if (changed && SHEET_QUERY.matches) map.resize();
}

/** The linked segment follows the filters again. */
function unpin() {
  state.pinnedId = null;
  applyFilters();
}

popup.on("close", () => {
  // Closed by the user (clearSelection resets selectedId before closing it).
  if (state.selectedId === null) return;
  const wasPinned = state.selectedId === state.pinnedId;
  clearSelection();
  if (wasPinned) unpin();
});

function updateUrl() {
  const search = buildUrlSearch({
    id: state.selectedId,
    kind: state.criteria.kind,
    position: state.position,
  });
  history.replaceState(null, "", search || location.pathname);
}

/** Run `callback` once our map layers exist (the style may still be loading). */
function whenLayersReady(callback) {
  if (map.getSource("segments")) callback();
  else map.once("style.load", callback); // registered after addDataLayers, so runs after it
}

function render() {
  if (state.position) marker.setLngLat(state.position).addTo(map);
  else marker.remove();
  const area = map.getSource("search-area");
  if (area) {
    const { center, radiusM } = state.searchArea ?? {};
    area.setData(center ? circlePolygon(center, radiusM) : emptyCollection());
  }
  map.getSource("route")?.setData(routeCollection());
  renderResults();
}

/** One-line summary of a segment: distance, length, grade, score... */
function summaryParts(p, distanceM) {
  let meta;
  if (isCircuit(p)) {
    meta = [loopLabel(p).toLowerCase(), formatLap(p), crossings(p.n_crossings)];
  } else if (p.kind === "loop") {
    meta = [`piste, tour de ${formatLap(p)}`, `accès ${ACCESS_LABELS[p.access] ?? p.access}`];
    if (p.indoor) meta.push("couverte");
  } else {
    let grade = p.kind === "flat" ? `max ${fmt(p.grade_max_pct, 1)} %` : `${fmt(p.grade_mean_pct, 1)} %`;
    // A long climb kept for its steep stretch: say where the grade asked for is.
    const lengthM = state.criteria.minLengthM;
    if (p.kind === "climb" && p.grade_mean_pct < state.criteria.minMeanGradePct) {
      grade += ` (dont ${formatLength(lengthM)} à ${fmt(steepGrade(p, lengthM), 1)} %)`;
    }
    if (p.kind === "long_climb") grade += ` · ${formatDuration(p.length_m, state.criteria.paceSecPerKm)}`;
    meta = [
      formatLength(p.length_m),
      grade,
      crossings(p.n_crossings),
      `score ${fmt(p.score)}`,
    ];
  }
  if (distanceM !== null && distanceM !== undefined) meta.unshift(`à ${formatLength(distanceM)}`);
  return meta;
}

function renderResults() {
  const list = $("results");
  list.replaceChildren();
  $("result-count").textContent = `(${state.results.length})`;
  for (const { feature, distanceM } of state.results.slice(0, MAX_RESULTS)) {
    const p = feature.properties;
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = p.kind;
    button.dataset.id = p.id;
    const meta = summaryParts(p, distanceM);
    button.innerHTML = `<span class="result-title">${escapeHtml(titleOf(p))}</span>
      <span class="result-meta">${escapeHtml(meta.join(" · "))}</span>`;
    button.addEventListener("click", () => selectSegment(p.id));
    item.append(button);
    list.append(item);
  }
  if (state.missingTiles) {
    const warning = document.createElement("li");
    warning.className = "hint warning";
    warning.textContent =
      "Une partie de la zone n'a pas pu être chargée (réseau) : la liste est incomplète. " +
      "Elle se complètera à la prochaine recherche.";
    list.prepend(warning);
  } else if (!state.results.length) {
    const empty = document.createElement("li");
    empty.className = "hint";
    empty.textContent = "Aucun segment ne correspond : élargissez les critères ou cherchez ailleurs (« Rechercher ici »).";
    list.append(empty);
  }
}

/**
 * Select a segment: highlight, popup, URL. `feature` is given when the
 * segment comes from the map or a link rather than from the result list.
 */
function selectSegment(id, lngLat = null, feature = null) {
  if (state.pinnedId !== null && id !== state.pinnedId) unpin();
  const result = state.results.find((r) => r.feature.properties.id === id);
  const selected = feature ?? result?.feature;
  if (!selected) return;
  clearSelection();
  state.selectedId = id;
  // A segment clicked on the map is cut to the tiles on screen: prefer the
  // whole one of the result list.
  state.selectedFeature = result?.feature ?? selected;
  map.setFeatureState({ source: "segments", sourceLayer: state.metadata.tiles.layer, id }, { selected: true });
  setSegmentOpen(true);
  // The shrunk list keeps the selected segment in view.
  document.querySelector(`#results button[data-id="${CSS.escape(String(id))}"]`)?.scrollIntoView({ block: "nearest" });
  const lines = lineParts(selected.geometry);
  const longest = lines.reduce((a, b) => (b.length > a.length ? b : a));
  const anchor = lngLat ?? longest[Math.floor(longest.length / 2)];
  popup
    .setLngLat(anchor)
    .setHTML(popupHtml(selected.properties, result?.distanceM ?? null))
    .addTo(map);
  const card = popup.getElement();
  card.querySelector(".popup-details")?.addEventListener("toggle", (event) => {
    writeStored(DETAILS_KEY, event.target.open ? "open" : "closed");
    refitSheet();
  });
  card.querySelector(".popup-profile")?.addEventListener("toggle", (event) => {
    writeStored(PROFILE_KEY, event.target.open ? "open" : "closed");
    if (event.target.open) {
      state.profileScroll = SHEET_QUERY.matches; // once drawn (the sheet is short)
      loadProfile();
    } else {
      setProfileCursor(null);
    }
    refitSheet();
  });
  renderRouteBox();
  if (card.querySelector(".popup-profile")?.open) loadProfile();
  if (!lngLat) {
    map.fitBounds(featuresBounds([selected]), { padding: popupPadding(), maxZoom: 16 });
  } else {
    // A segment clicked under the card: bring it beside (or above) the card.
    const card = popup.getElement();
    const { x, y } = map.project(lngLat);
    if (SHEET_QUERY.matches) {
      const limit = map.getContainer().clientHeight - card.offsetHeight - 30;
      if (y > limit) map.panBy([0, y - limit]);
    } else {
      const limit = card.offsetLeft + card.offsetWidth + 30;
      if (x < limit && y < card.offsetTop + card.offsetHeight + 30) map.panBy([x - limit, 0]);
    }
  }
  updateUrl();
}

/** The sheet changed height: keep the segment and its route above it. */
function refitSheet() {
  if (!SHEET_QUERY.matches || !state.selectedFeature) return;
  const features = state.route?.coordinates ? [routeFeature(state.route), state.selectedFeature] : [state.selectedFeature];
  map.fitBounds(featuresBounds(features), { padding: popupPadding(), maxZoom: 16 });
}

/** Map padding that keeps what is fitted clear of the card (beside it, or above the sheet). */
function popupPadding() {
  const { clientWidth: w, clientHeight: h } = map.getContainer();
  const side = Math.min(60, w * 0.1);
  const edge = Math.min(60, h * 0.1);
  const card = popup.isOpen() ? popup.getElement() : null;
  if (SHEET_QUERY.matches) {
    return { top: edge, bottom: Math.min((card?.offsetHeight ?? 0) + 30, h * 0.6), left: side, right: side };
  }
  const left = card ? card.offsetLeft + card.offsetWidth + 30 : side;
  return { top: edge, bottom: edge, left: Math.min(left, w * 0.5), right: side };
}

// --- route to the selected segment ---------------------------------------------

function routeCollection() {
  const route = state.route;
  if (!route?.coordinates) return emptyCollection();
  return { type: "FeatureCollection", features: [routeFeature(route)] };
}

const routeFeature = (route) => ({
  type: "Feature",
  properties: {},
  geometry: { type: "LineString", coordinates: route.coordinates },
});

function clearRoute() {
  state.routeQuery++; // a pending answer is dropped
  state.route = null;
  map.getSource("route")?.setData(emptyCollection());
}

/** The route part of the popup: compute button, result, GPS app link. */
function renderRouteBox() {
  const box = popup.getElement()?.querySelector(".route-box");
  if (!box || !state.selectedFeature) return;
  const route = state.route;
  const parts = [];
  if (!state.position) {
    parts.push(`<p class="hint">Donnez votre position (📍 ou ✚) pour calculer l'itinéraire à pied jusqu'au départ.</p>`);
  } else if (route === "loading") {
    parts.push(`<p class="hint">Calcul de l'itinéraire…</p>`);
  } else if (route?.coordinates) {
    parts.push(
      `<p class="route-result">À pied jusqu'au départ : <strong>${escapeHtml(formatLength(route.distanceM))}</strong>, ` +
        `environ ${escapeHtml(formatWalk(route.durationS))} en marchant</p>`,
    );
  } else {
    if (typeof route === "string") parts.push(`<p class="flags">${escapeHtml(route)}</p>`);
    parts.push(`<button type="button" class="route-button">🧭 Itinéraire depuis ma position</button>`);
  }
  const start = segmentStart(state.selectedFeature, state.position ?? searchCenter());
  const gps = navigationUrl(start, navigator.userAgent, navigator.maxTouchPoints);
  parts.push(`<p class="hint"><a href="${escapeHtml(gps)}" target="_blank" rel="noopener">Y aller avec une appli GPS</a></p>`);
  box.innerHTML = parts.join("");
  box.querySelector(".route-button")?.addEventListener("click", computeRoute);
}

/** Walking route from the position to the start of the selected segment. */
async function computeRoute() {
  const feature = state.selectedFeature;
  const from = state.position;
  if (!feature || !from) return;
  const query = ++state.routeQuery;
  state.route = "loading";
  renderRouteBox();
  let route = null;
  let message = "Itinéraire impossible à calculer (réseau). Réessayez.";
  try {
    const response = await fetch(routeUrl(from, segmentStart(feature, from)));
    // The service answers 4xx with {"error": …} when there is no path.
    route = parseRoute(await response.json());
    if (!route) message = "Aucun itinéraire à pied trouvé jusqu'à ce segment.";
  } catch (error) {
    console.warn("Route unavailable", error);
  }
  if (query !== state.routeQuery) return; // selection or position changed meanwhile
  state.route = route ?? message;
  map.getSource("route")?.setData(routeCollection());
  renderRouteBox();
  if (route) {
    const bounds = featuresBounds([routeFeature(route), feature]);
    map.fitBounds(bounds, { padding: popupPadding(), maxZoom: 16 });
  }
}

// --- elevation profile of the selected segment ------------------------------------

// Chart size (viewBox units, scaled to the card's width): flatter in the phone sheet.
const chartSize = () => (SHEET_QUERY.matches ? { width: 360, height: 84 } : { width: 300, height: 110 });

const GRADE_LEGEND = [
  `&lt; ${GRADE_CLASSES[0]}`,
  ...GRADE_CLASSES.slice(1).map((limit, i) => `${GRADE_CLASSES[i]}–${limit}`),
  `&gt; ${GRADE_CLASSES.at(-1)} %`,
];

/** Fetch (once per segment) the elevation profile of the selected segment, then draw it. */
async function loadProfile() {
  const feature = state.selectedFeature;
  if (!feature) return;
  const id = feature.properties.id;
  if (state.profiles.has(id)) return renderProfileBox();
  state.profiles.set(id, "loading");
  renderProfileBox();
  const line = orderedLine(feature, state.position ?? searchCenter());
  let result = "Profil indisponible (réseau). Refermez puis rouvrez pour réessayer.";
  for (const resource of RESOURCES) {
    try {
      const { url, init } = elevationRequest(line, resource.id);
      const response = await fetch(url, init);
      const samples = response.ok ? parseElevations(await response.json()) : null;
      if (samples) {
        result = { ...buildProfile(samples), source: resource.label };
        break;
      }
      result = "Pas d'altitude disponible ici.";
    } catch (error) {
      console.warn("Elevation profile unavailable", error);
    }
  }
  if (typeof result === "string") state.profiles.delete(id); // retried at the next opening
  else state.profiles.set(id, result);
  if (state.selectedFeature?.properties.id !== id) return; // another segment meanwhile
  renderProfileBox(result);
}

/** The profile part of the popup: chart, legend, readout under the pointer. */
function renderProfileBox(override = null) {
  const box = popup.getElement()?.querySelector(".profile-box");
  if (!box || !state.selectedFeature) return;
  const p = state.selectedFeature.properties;
  const profile = override ?? state.profiles.get(p.id);
  if (profile === "loading" || profile === undefined) {
    box.innerHTML = `<p class="hint">Chargement du profil…</p>`;
    return;
  }
  if (typeof profile === "string") {
    box.innerHTML = `<p class="flags">${escapeHtml(profile)}</p>`;
    return;
  }
  const bridge = p.on_structure || p.quality_flags?.includes("bridge_interpolated");
  const metres = (z) => `${fmt(Math.round(z))} m`;
  const size = chartSize();
  box.innerHTML = `${profileSvg(profile, size)}
    <p class="profile-readout">De ${metres(profile.z[0])} à ${metres(profile.z.at(-1))} d'altitude</p>
    <p class="profile-legend">Pente :${GRADE_LEGEND.map((text, i) => ` <span class="swatch grade-${i}"></span>${text}`).join("")}</p>
    <p class="hint">Altitudes ${escapeHtml(profile.source)}, lissées${bridge ? " ; sur un pont, la courbe suit le sol en dessous" : ""}.</p>`;
  if (state.profileScroll) {
    // Bring the chart into view in the phone sheet.
    state.profileScroll = false;
    const content = popup.getElement().querySelector(".maplibregl-popup-content");
    const details = box.closest(".popup-profile");
    if (content) content.scrollTop += details.getBoundingClientRect().top - content.getBoundingClientRect().top - 8;
  }
  const svg = box.querySelector("svg");
  const readout = box.querySelector(".profile-readout");
  const idle = readout.textContent;
  const show = (event) => {
    const rect = svg.getBoundingClientRect();
    const vx = ((event.clientX - rect.left) / rect.width) * svg.viewBox.baseVal.width;
    const i = nearestSample(profile, distanceAtX(profile, vx, size));
    const [x, y] = chartPoint(profile, i, size);
    const cursor = svg.querySelector(".profile-cursor");
    cursor.setAttribute("x1", x);
    cursor.setAttribute("x2", x);
    cursor.setAttribute("visibility", "visible");
    const dot = svg.querySelector(".profile-dot");
    dot.setAttribute("cx", x);
    dot.setAttribute("cy", y);
    dot.setAttribute("visibility", "visible");
    readout.textContent =
      `${formatLength(profile.d[i])} : ${metres(profile.z[i])} d'altitude, ` + `pente ${fmt(gradeAt(profile, i), 1)} %`;
    setProfileCursor(profile.points[i]);
  };
  const hide = () => {
    svg.querySelector(".profile-cursor").setAttribute("visibility", "hidden");
    svg.querySelector(".profile-dot").setAttribute("visibility", "hidden");
    readout.textContent = idle;
    setProfileCursor(null);
  };
  svg.addEventListener("pointermove", show);
  svg.addEventListener("pointerdown", show);
  svg.addEventListener("pointerleave", hide);
}

/** Point on the map matching the pointer on the profile chart (null hides it). */
function setProfileCursor(point) {
  if (!styleReady) return;
  if (!map.getSource("profile-cursor")) {
    if (!point) return;
    map.addSource("profile-cursor", { type: "geojson", data: emptyCollection() });
    map.addLayer({
      id: "profile-cursor",
      type: "circle",
      source: "profile-cursor",
      paint: { "circle-radius": 6, "circle-color": "#ffffff", "circle-stroke-color": "#222222", "circle-stroke-width": 2.5 },
    });
  }
  map.getSource("profile-cursor").setData(
    point
      ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: point } }] }
      : emptyCollection(),
  );
}

/**
 * Show a segment from a link, even if the current filters hide it. An id of
 * an earlier version opens the segment that replaced it (lineage.py).
 */
async function focusSegment(id) {
  const status = (text) => ($("position-status").textContent = text);
  const notFound = () => status(`Segment introuvable : ${id}.`);
  const { index, index_prefix_length: prefix } = state.metadata.tiles;
  const response = await fetch(dataUrl(state.dataBase, `${index}/${indexKey(id, prefix)}.json`));
  const entry = resolveIndexEntry(id, response.ok ? (await response.json())[id] : undefined);
  if (!entry) return notFound();
  if (entry.status === "retired") {
    status(`Le segment ${id} n'existe plus dans les données à jour : la carte montre où il était.`);
    map.jumpTo({ center: entry.position, zoom: 15 });
    if (!state.position) {
      state.fallbackCenter = entry.position;
      refreshResults();
    }
    updateUrl(); // drop the dead id from the address
    return undefined;
  }
  const { features } = await segmentsAround(entry.position, LINK_RADIUS_M);
  const feature = features.find((f) => f.id === entry.id);
  if (!feature) return notFound();
  if (entry.status === "moved") {
    status(`Le segment ${id} a changé lors d'une mise à jour des données : voici ce qui le remplace.`);
  }
  $(`kind-${feature.properties.kind}`).checked = true;
  readControls();
  state.pinnedId = entry.id;
  updateMapFilters();
  whenLayersReady(() => selectSegment(entry.id, null, feature));
  // Without a position, search around the linked segment.
  if (!state.position) state.fallbackCenter = entry.position;
  refreshResults();
  return undefined;
}

function setPosition(position, label) {
  state.position = position;
  state.positionLabel = label;
  if (state.placing) setPlacing(false); // the position was given another way
  clearRoute(); // it started from the old position
  renderRouteBox();
  showPosition();
  applyFilters();
  updateUrl();
}

function showPosition() {
  const [lon, lat] = state.position;
  $("position-status").textContent = `Position : ${lat.toFixed(5)}, ${lon.toFixed(5)} (${state.positionLabel}).`;
}

// --- controls -----------------------------------------------------------------

function readControls() {
  const c = state.criteria;
  c.kind = document.querySelector('input[name="kind"]:checked').value;
  c.effortMin = Number($("effort-min").value);
  c.paceSecPerKm = Number($("pace").value);
  c.minLengthM =
    c.kind === "long_climb" ? effortLengthM(c.effortMin, c.paceSecPerKm) : Number($("min-length").value);
  c.maxLocalGradePct = Number($("max-local-grade").value);
  c.maxLoopGradePct = Number($("max-loop-grade").value);
  let lo = Number($("min-mean-grade").value);
  let hi = Number($("max-mean-grade").value);
  if (lo > hi) [lo, hi] = [hi, lo];
  c.minMeanGradePct = lo;
  c.maxMeanGradePct = hi;
  c.maxDistanceM = Number($("max-distance").value) * 1000;
  c.noCrossing = $("no-crossing").checked;
  c.surface = $("surface").value;
  c.publicOnly = $("public-only").checked;
  c.loopTypes = [...document.querySelectorAll('input[name="loop-type"]:checked')].map((e) => e.value);
  state.sortBy = $("sort-by").value;

  $("max-local-grade-value").textContent = `${fmt(c.maxLocalGradePct, 1)} %`;
  $("max-loop-grade-value").textContent = `${fmt(c.maxLoopGradePct, 1)} %`;
  $("effort-value").textContent = `${formatLength(effortLengthM(c.effortMin, c.paceSecPerKm))} minimum`;
  $("mean-grade-value").textContent = `${fmt(lo, 1)} à ${fmt(hi, 1)} %`;
  $("max-distance-value").textContent = `${fmt(c.maxDistanceM / 1000, 1)} km`;
  for (const element of document.querySelectorAll("[data-kind]")) {
    element.hidden = !element.dataset.kind.split(" ").includes(c.kind);
  }
}

function onControlsChange() {
  readControls();
  applyFilters();
  updateUrl();
}

for (const element of document.querySelectorAll(".panel input, .panel select")) {
  if (element.id !== "latlon") element.addEventListener("input", onControlsChange);
}

$("locate").addEventListener("click", () => {
  if (!navigator.geolocation) {
    $("position-status").textContent = "Géolocalisation indisponible dans ce navigateur.";
    return;
  }
  $("position-status").textContent = "Localisation en cours…";
  navigator.geolocation.getCurrentPosition(
    ({ coords }) => {
      setPosition([coords.longitude, coords.latitude], `précision ${Math.round(coords.accuracy)} m`);
      map.flyTo({ center: state.position, zoom: 14 });
    },
    (error) => {
      $("position-status").textContent = `Localisation refusée ou impossible (${error.message}).`;
    },
    { enableHighAccuracy: true, timeout: 10000 },
  );
});

/** Placement mode: the next click on the map sets the position. */
function setPlacing(placing) {
  state.placing = placing;
  $("place").setAttribute("aria-pressed", String(placing));
  map.getCanvas().style.cursor = placing ? "crosshair" : "";
  if (placing) {
    $("position-status").textContent = "Cliquez sur la carte pour placer le point (Échap pour annuler).";
  } else if (!state.position) {
    $("position-status").textContent = NO_POSITION_STATUS;
  } else {
    showPosition();
  }
}

$("place").addEventListener("click", () => setPlacing(!state.placing));

// "À propos" dialog; also opens from a link to #a-propos.
const about = $("about");
$("about-button").addEventListener("click", () => about.showModal());
// A click on the backdrop (outside the dialog's box) closes it.
about.addEventListener("click", (event) => {
  if (event.target === about) about.close();
});
if (location.hash === "#a-propos") about.showModal();
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.placing) setPlacing(false);
});

// Address search: suggestions while typing, the chosen (or first) one on submit.
const search = { results: [], active: -1, text: "", timer: null, controller: null };

async function geocode(text) {
  const { lng, lat } = map.getCenter();
  const url = geocodeUrl(text, [lng, lat]);
  if (!url) return [];
  search.controller?.abort();
  search.controller = new AbortController();
  const response = await fetch(url, { signal: search.controller.signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return parseGeocodeResults(await response.json());
}

function showSuggestions(results, text) {
  search.results = results;
  search.text = text;
  search.active = -1;
  const list = $("suggestions");
  list.replaceChildren(
    ...results.map(({ label }, i) => {
      const item = document.createElement("li");
      item.id = `suggestion-${i}`;
      item.setAttribute("role", "option");
      item.textContent = label;
      // mousedown, not click: the input must not lose the focus (blur hides the list).
      item.addEventListener("mousedown", (event) => {
        event.preventDefault();
        chooseAddress(results[i]);
      });
      return item;
    }),
  );
  list.hidden = !results.length;
  $("latlon").setAttribute("aria-expanded", String(!list.hidden));
  highlight(-1);
}

function hideSuggestions() {
  clearTimeout(search.timer);
  search.controller?.abort();
  showSuggestions([], "");
}

function highlight(index) {
  search.active = index;
  for (const [i, item] of [...$("suggestions").children].entries()) {
    item.setAttribute("aria-selected", String(i === index));
  }
  if (index >= 0) $("latlon").setAttribute("aria-activedescendant", `suggestion-${index}`);
  else $("latlon").removeAttribute("aria-activedescendant");
}

function chooseAddress({ label, position }) {
  $("latlon").value = label;
  hideSuggestions();
  setPosition(position, label);
  map.flyTo({ center: position, zoom: 14 });
}

$("latlon").addEventListener("input", () => {
  const text = $("latlon").value;
  clearTimeout(search.timer);
  if (parseLatLon(text) || !geocodeUrl(text)) {
    hideSuggestions();
    return;
  }
  search.timer = setTimeout(async () => {
    try {
      showSuggestions(await geocode(text), text);
    } catch (error) {
      if (error.name !== "AbortError") console.warn("Address suggestions unavailable", error);
    }
  }, SUGGEST_DELAY_MS);
});

$("latlon").addEventListener("keydown", (event) => {
  const count = search.results.length;
  if (!count) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const step = event.key === "ArrowDown" ? 1 : -1;
    // Cycle through the suggestions and back to the typed text (index -1).
    highlight(((search.active + 1 + step + count + 1) % (count + 1)) - 1);
  } else if (event.key === "Escape") {
    hideSuggestions();
  }
});
$("latlon").addEventListener("blur", hideSuggestions);

$("latlon-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("latlon").value;
  const position = parseLatLon(text);
  if (position) {
    hideSuggestions();
    setPosition(position, "saisie");
    map.flyTo({ center: position, zoom: 14 });
    return;
  }
  if (!geocodeUrl(text)) {
    $("position-status").textContent = "Saisissez une adresse, ou latitude, longitude (ex. 43.531, 1.533).";
    return;
  }
  // The highlighted suggestion, or the first one for this text.
  if (search.text === text && search.results.length) {
    chooseAddress(search.results[Math.max(0, search.active)]);
    return;
  }
  $("position-status").textContent = "Recherche de l'adresse…";
  clearTimeout(search.timer); // a pending suggestion request would abort this one
  try {
    const [first] = await geocode(text);
    if (first) chooseAddress(first);
    else $("position-status").textContent = `Adresse introuvable : ${text}.`;
  } catch (error) {
    if (error.name === "AbortError") return;
    console.warn(error);
    $("position-status").textContent = "Recherche d'adresse impossible (réseau). Réessayez, ou saisissez latitude, longitude.";
  }
});

// --- data loading -------------------------------------------------------------

/** The published tileset, or the sample one: its segments.json and folder. */
async function fetchMetadata() {
  for (const dir of DATA_DIRS) {
    const response = await fetch(`${dir}segments.json`);
    if (response.ok) return { dir, metadata: await response.json() };
  }
  throw new Error("no segments.json");
}

async function loadData() {
  const { dir, metadata } = await fetchMetadata();
  state.metadata = metadata;
  state.dataBase = new URL(dir, location.href).href;
  const url = dataUrl(state.dataBase, metadata.tiles.url);
  state.archive = new PMTiles(url);
  protocol.add(state.archive);
  $("sample-banner").hidden = !metadata.sample;
  addAttribution(metadata.attribution ?? []);
  if (styleReady) addSegmentLayers();
  const [west, south, east, north] = metadata.bounds ?? [];
  if (metadata.bounds && !initial.position && !initial.id) {
    map.fitBounds([[west, south], [east, north]], { padding: 40, duration: 0 });
  }
  if (initial.position) {
    setPosition(initial.position, "lien");
    if (!initial.id) map.jumpTo({ center: initial.position, zoom: 14 });
  } else {
    $("position-status").textContent = NO_POSITION_STATUS;
    refreshResults();
  }
  if (initial.id) await focusSegment(initial.id);
}

function addAttribution(dataAttribution) {
  // Basemap attributions come from the style sources; segments derive from OSM.
  // Same wording as the pipeline export (export.OSM_ATTRIBUTION), so that the
  // Set drops the duplicate.
  const custom = [...new Set(["© les contributeurs d'OpenStreetMap (ODbL)", ...dataAttribution])];
  map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: custom }));
  // On phones the credits start folded (ⓘ shows them): unfolded, they hide the segment sheet.
  if (SHEET_QUERY.matches) {
    const attribution = map.getContainer().querySelector(".maplibregl-ctrl-attrib");
    attribution?.classList.remove("maplibregl-compact-show");
    attribution?.removeAttribute("open");
  }
}

const initial = parseUrlState(location.search);
if (initial.kind) $(`kind-${initial.kind}`).checked = true;
readControls();
loadData().catch((error) => {
  console.error(error);
  addAttribution([]);
  const banner = $("error-banner");
  banner.textContent = `Impossible de charger les segments (${error.message}).`;
  banner.hidden = false;
});
