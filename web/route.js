// Walking route from the position to the start of a segment, with the IGN
// Géoplateforme route service (see docs/adr/0016-itineraire-ign.md), and a
// link handing the trip to a GPS app. Pure helpers (no DOM, no fetch): tested
// with `node --test`. Coordinates are [lon, lat] in WGS84.

import { distanceToLineMeters, haversineMeters, lineParts } from "./filters.js";

export const ROUTE_URL = "https://data.geopf.fr/navigation/itineraire";
// A piece end this close to another piece is a cut by a tile edge.
const JOIN_TOLERANCE_M = 5;

/** URL of a walking route between two [lon, lat] points. */
export function routeUrl(from, to) {
  const point = ([lon, lat]) => `${lon.toFixed(6)},${lat.toFixed(6)}`;
  const params = new URLSearchParams({
    resource: "bdtopo-valhalla",
    profile: "pedestrian",
    optimization: "shortest",
    start: point(from),
    end: point(to),
    geometryFormat: "geojson",
    getSteps: "false",
    distanceUnit: "meter",
    timeUnit: "second",
  });
  return `${ROUTE_URL}?${params}`;
}

/**
 * The route of a service answer.
 * @returns {{coordinates: [number, number][], distanceM: number, durationS: number} | null}
 *   null when the answer holds no route (e.g. "No path found").
 */
export function parseRoute(json) {
  const coordinates = json?.geometry?.coordinates;
  const distanceM = Number(json?.distance);
  const durationS = Number(json?.duration);
  if (!Array.isArray(coordinates) || coordinates.length < 2) return null;
  if (!Number.isFinite(distanceM) || !Number.isFinite(durationS)) return null;
  return { coordinates, distanceM, durationS };
}

/**
 * Where to join a segment coming from `position`:
 * - a loop has no start: its point nearest to the position;
 * - a climb starts at its bottom, the first point of its line (the pipeline
 *   orients climbs uphill);
 * - a flat can be run either way: its end nearest to the position.
 * The line may come in pieces cut by tile edges (segmentsFromTiles); a piece
 * end lying on another piece is a cut, not an end of the segment.
 * @returns {[number, number]}
 */
export function segmentStart(feature, position) {
  const parts = lineParts(feature.geometry).filter((line) => line.length);
  const nearest = (points) =>
    points.reduce((a, b) => (haversineMeters(position, b) < haversineMeters(position, a) ? b : a));
  if (feature.properties.kind === "loop") return nearest(parts.flat());
  const onOtherPiece = (point, self) =>
    parts.some((line, i) => i !== self && distanceToLineMeters(point, line) < JOIN_TOLERANCE_M);
  const firsts = parts.map((line) => line[0]).filter((point, i) => !onOtherPiece(point, i));
  const lasts = parts.map((line) => line.at(-1)).filter((point, i) => !onOtherPiece(point, i));
  if (feature.properties.kind !== "flat" && firsts.length) return nearest(firsts);
  const ends = [...firsts, ...lasts];
  return nearest(ends.length ? ends : parts.flat());
}

/**
 * Link opening a walk to `to` in the phone's GPS app: on Android a geo: URI
 * (the system offers the installed apps), on iPhone / iPad Apple Plans,
 * elsewhere Google Maps. GPS apps start from where the phone is.
 */
export function navigationUrl([lon, lat], userAgent, maxTouchPoints = 0) {
  const point = `${lat.toFixed(6)},${lon.toFixed(6)}`;
  if (/Android/i.test(userAgent)) return `geo:${point}?q=${point}`;
  const ios = /iPhone|iPad|iPod/.test(userAgent) || (/Macintosh/.test(userAgent) && maxTouchPoints > 1);
  if (ios) return `https://maps.apple.com/?daddr=${point}&dirflg=w`;
  return `https://www.google.com/maps/dir/?api=1&destination=${point}&travelmode=walking`;
}
