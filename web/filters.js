// Pure filtering logic of the web page (no DOM, no map): tested with `node --test`.
// Coordinates are [lon, lat] in WGS84, as in GeoJSON.

export const EARTH_RADIUS_M = 6371008.8;

/** Default filter values, shown when the page opens. */
export const DEFAULT_CRITERIA = Object.freeze({
  kind: "flat",
  minLengthM: 200,
  maxLocalGradePct: 2,
  maxLoopGradePct: 5,
  minMeanGradePct: 3,
  maxMeanGradePct: 50,
  maxDistanceM: 5000,
  effortMin: 6,
  paceSecPerKm: 330,
  noCrossing: false,
  surface: "any", // "any", "paved" or "unpaved" (see SURFACE_GROUPS)
  publicOnly: false,
  loopTypes: Object.freeze(["track", "water", "park", "neighbourhood"]),
});

/** Segment surfaces selected by the surface filter (cobbles and unknown: "any" only). */
export const SURFACE_GROUPS = Object.freeze({
  paved: Object.freeze(["paved"]),
  unpaved: Object.freeze(["compacted", "gravel", "unpaved"]),
});

/**
 * Type of a loop shown by the type filter: `track` (running track), or the
 * setting of a network circuit (`water`, `park`, `neighbourhood`).
 */
export function loopType(properties) {
  return properties.loop_type === "circuit" ? properties.setting : "track";
}

/** Kinds of results: flat segments, climbs, long climbs and loops. */
export const KINDS = Object.freeze(["flat", "climb", "long_climb", "loop"]);

/**
 * Minimum length of a long climb for an effort of `minutes` at a pace of
 * `paceSecPerKm` (seconds per kilometre), rounded to 10 m.
 */
export function effortLengthM(minutes, paceSecPerKm) {
  return Math.round((minutes * 60 * 1000) / paceSecPerKm / 10) * 10;
}

/**
 * Above this sinuosity (length / distance between the ends), a segment is shown
 * as a loop: its ends are close, and the number itself means little.
 */
export const LOOP_SINUOSITY = 3;

/** Whether a segment is (nearly) a loop; `sinuosity` is null (or absent) for a closed one. */
export function isLoop(properties) {
  return properties.sinuosity == null || properties.sinuosity > LOOP_SINUOSITY;
}

/** The lines of a LineString or MultiLineString geometry. */
export function lineParts(geometry) {
  return geometry.type === "MultiLineString" ? geometry.coordinates : [geometry.coordinates];
}

const toRad = (deg) => (deg * Math.PI) / 180;

/** Great-circle distance in metres between two [lon, lat] points. */
export function haversineMeters([lon1, lat1], [lon2, lat2]) {
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
}

/**
 * Shortest distance in metres from a point to a polyline.
 * Uses a local equirectangular projection centred on the point, accurate to
 * well under 1 % within a few tens of kilometres.
 */
export function distanceToLineMeters(point, coords) {
  const [lon0, lat0] = point;
  const kx = toRad(1) * EARTH_RADIUS_M * Math.cos(toRad(lat0));
  const ky = toRad(1) * EARTH_RADIUS_M;
  const xy = coords.map(([lon, lat]) => [(lon - lon0) * kx, (lat - lat0) * ky]);
  if (xy.length === 1) return Math.hypot(xy[0][0], xy[0][1]);
  let best = Infinity;
  for (let i = 0; i < xy.length - 1; i += 1) {
    const [ax, ay] = xy[i];
    const [bx, by] = xy[i + 1];
    const dx = bx - ax;
    const dy = by - ay;
    const len2 = dx * dx + dy * dy;
    const t = len2 > 0 ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len2)) : 0;
    best = Math.min(best, Math.hypot(ax + t * dx, ay + t * dy));
  }
  return best;
}

/** Shortest distance in metres from a point to a (multi)line geometry. */
export function distanceToGeometryMeters(point, geometry) {
  return Math.min(...lineParts(geometry).map((line) => distanceToLineMeters(point, line)));
}

/** Whether a segment's properties satisfy the criteria (distance excluded). */
export function matches(properties, criteria) {
  const p = properties;
  if (p.kind !== criteria.kind) return false;
  // A loop is whole: length, crossings, surface and mean grade do not apply.
  // Only circuits have a local grade (tracks are flat by construction).
  if (p.kind === "loop") {
    return (
      criteria.loopTypes.includes(loopType(p)) &&
      (!criteria.publicOnly || p.access === "public") &&
      (p.loop_type !== "circuit" || p.grade_max_pct <= criteria.maxLoopGradePct)
    );
  }
  if (p.length_m < criteria.minLengthM) return false;
  if (criteria.noCrossing && p.n_crossings > 0) return false;
  const surfaces = SURFACE_GROUPS[criteria.surface];
  if (surfaces && !surfaces.includes(p.surface)) return false;
  if (p.kind === "flat") return p.grade_max_pct <= criteria.maxLocalGradePct;
  return (
    steepGrade(p, criteria.minLengthM) >= criteria.minMeanGradePct &&
    p.grade_mean_pct <= criteria.maxMeanGradePct
  );
}

/**
 * Steepest mean grade of a climb over a stretch of `lengthM` (the minimum
 * length): a long climb whose steep end meets the minimum grade is kept.
 * The mean grade of the whole climb when the tiles predate these fields.
 */
export function steepGrade(properties, lengthM) {
  return properties[`steep_${lengthM}_pct`] ?? properties.grade_mean_pct;
}

/**
 * MapLibre expression true for the segments whose id is in `ids`.
 * `match` looks the id up in a table, where `in` would scan the whole list.
 */
export function idsFilter(ids) {
  const labels = [...new Set(ids)];
  return labels.length ? ["match", ["get", "id"], labels, true, false] : ["literal", false];
}

/**
 * MapLibre filter expression equivalent to `matches` (map layer of the tiles).
 * @param pinnedId id of a segment always shown (see filterSegments).
 * @param ids if given, only these segments are shown (the result list, which
 *   holds the distance limit that an expression cannot compute).
 */
export function mapFilter(criteria, pinnedId = null, ids = null) {
  const get = (key) => ["get", key];
  const conditions = [["==", get("kind"), criteria.kind]];
  if (criteria.kind === "loop") {
    const type = ["case", ["==", get("loop_type"), "circuit"], get("setting"), "track"];
    conditions.push(criteria.loopTypes.length ? ["match", type, [...criteria.loopTypes], true, false] : false);
    if (criteria.publicOnly) conditions.push(["==", get("access"), "public"]);
    const gentle = ["<=", get("grade_max_pct"), criteria.maxLoopGradePct];
    conditions.push(["case", ["==", get("loop_type"), "circuit"], gentle, true]);
  } else {
    conditions.push([">=", get("length_m"), criteria.minLengthM]);
    if (criteria.noCrossing) conditions.push(["==", get("n_crossings"), 0]);
    const surfaces = SURFACE_GROUPS[criteria.surface];
    if (surfaces) conditions.push(["match", get("surface"), [...surfaces], true, false]);
    if (criteria.kind === "flat") {
      conditions.push(["<=", get("grade_max_pct"), criteria.maxLocalGradePct]);
    } else {
      const steep = ["coalesce", get(`steep_${criteria.minLengthM}_pct`), get("grade_mean_pct")];
      conditions.push([">=", steep, criteria.minMeanGradePct]);
      conditions.push(["<=", get("grade_mean_pct"), criteria.maxMeanGradePct]);
    }
  }
  if (ids !== null) conditions.push(idsFilter(ids));
  const filter = ["all", ...conditions];
  return pinnedId === null ? filter : ["any", ["==", get("id"), pinnedId], filter];
}

/**
 * Filter of the overview layer (low zooms): only the kind, length and id are
 * there (all loops are shown: their access is not in that layer).
 */
export function overviewFilter(criteria, ids = null) {
  const filter = ["all", ["==", ["get", "kind"], criteria.kind]];
  if (criteria.kind !== "loop") filter.push([">=", ["get", "length_m"], criteria.minLengthM]);
  if (ids !== null) filter.push(idsFilter(ids));
  return filter;
}

/**
 * Circle of `radiusM` metres around [lon, lat], as a GeoJSON polygon of
 * `steps` sides (points at the exact great-circle distance).
 */
export function circlePolygon([lon, lat], radiusM, steps = 64) {
  const angular = radiusM / EARTH_RADIUS_M;
  const lat1 = toRad(lat);
  const ring = [];
  for (let i = 0; i <= steps; i += 1) {
    const bearing = (2 * Math.PI * (i % steps)) / steps;
    const lat2 = Math.asin(
      Math.sin(lat1) * Math.cos(angular) + Math.cos(lat1) * Math.sin(angular) * Math.cos(bearing),
    );
    const lon2 =
      toRad(lon) +
      Math.atan2(
        Math.sin(bearing) * Math.sin(angular) * Math.cos(lat1),
        Math.cos(angular) - Math.sin(lat1) * Math.sin(lat2),
      );
    ring.push([(lon2 * 180) / Math.PI, (lat2 * 180) / Math.PI]);
  }
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } };
}

/**
 * Filter GeoJSON features.
 * @param pinnedId id of a segment always kept, whatever the criteria and the
 *   distance (a segment opened by a link stays visible even when it falls just
 *   outside the filters, e.g. a climb at 2.97 % with a 3 % minimum).
 * @returns {{feature: object, distanceM: number | null}[]} matching features with
 *   their distance to `position` ([lon, lat] or null: no distance filter).
 */
export function filterSegments(features, criteria, position = null, pinnedId = null) {
  const results = [];
  for (const feature of features) {
    const pinned = pinnedId !== null && feature.properties.id === pinnedId;
    if (!pinned && !matches(feature.properties, criteria)) continue;
    let distanceM = null;
    if (position) {
      distanceM = distanceToGeometryMeters(position, feature.geometry);
      if (!pinned && distanceM > criteria.maxDistanceM) continue;
    }
    results.push({ feature, distanceM });
  }
  return results;
}

/**
 * Sort results in place by "distance" (nearest first), "score" (best first)
 * or "gain" (biggest elevation gain first, for climbs).
 */
export function sortResults(results, sortBy) {
  // Loops have no score (all tracks are equal): they count as 0.
  const score = (r) => r.feature.properties.score ?? 0;
  const gain = (r) => r.feature.properties.elev_gain_m ?? 0;
  const byScore = (a, b) => score(b) - score(a);
  const byGain = (a, b) => gain(b) - gain(a);
  const byDistance = (a, b) => (a.distanceM ?? Infinity) - (b.distanceM ?? Infinity);
  if (sortBy === "distance") return results.sort((a, b) => byDistance(a, b) || byScore(a, b));
  if (sortBy === "gain") return results.sort((a, b) => byGain(a, b) || byDistance(a, b));
  return results.sort((a, b) => byScore(a, b) || byDistance(a, b));
}

/**
 * Parse a "lat, lon" string (decimal degrees, comma or space separated).
 * @returns {[number, number] | null} [lon, lat], or null if invalid.
 */
export function parseLatLon(text) {
  const match = String(text)
    .trim()
    .match(/^(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)$/);
  if (!match) return null;
  const lat = Number(match[1]);
  const lon = Number(match[2]);
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return [lon, lat];
}

/** Bounding box [[minLon, minLat], [maxLon, maxLat]] of features, or null if empty. */
export function featuresBounds(features) {
  let minLon = Infinity;
  let minLat = Infinity;
  let maxLon = -Infinity;
  let maxLat = -Infinity;
  for (const feature of features) {
    for (const line of lineParts(feature.geometry)) {
      for (const [lon, lat] of line) {
        minLon = Math.min(minLon, lon);
        minLat = Math.min(minLat, lat);
        maxLon = Math.max(maxLon, lon);
        maxLat = Math.max(maxLat, lat);
      }
    }
  }
  return Number.isFinite(minLon) ? [[minLon, minLat], [maxLon, maxLat]] : null;
}

/**
 * Read the page state from a query string: `?id=…`, `?kind=flat|climb|long_climb|loop`,
 * `?lat=…&lon=…`. Invalid values are ignored.
 * @returns {{id: string | null, kind: "flat" | "climb" | "long_climb" | "loop" | null, position: [number, number] | null}}
 */
export function parseUrlState(search) {
  const params = new URLSearchParams(search);
  const kind = params.get("kind");
  const lat = Number(params.get("lat"));
  const lon = Number(params.get("lon"));
  const hasPosition =
    params.has("lat") &&
    params.has("lon") &&
    Number.isFinite(lat) &&
    Number.isFinite(lon) &&
    Math.abs(lat) <= 90 &&
    Math.abs(lon) <= 180;
  return {
    id: params.get("id") || null,
    kind: KINDS.includes(kind) ? kind : null,
    position: hasPosition ? [lon, lat] : null,
  };
}

/** Query string for a page state (inverse of parseUrlState; "flat" is the default kind). */
export function buildUrlSearch({ id = null, kind = null, position = null } = {}) {
  const params = new URLSearchParams();
  if (id) params.set("id", id);
  if (kind && kind !== "flat") params.set("kind", kind);
  if (position) {
    params.set("lat", position[1].toFixed(5));
    params.set("lon", position[0].toFixed(5));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}
