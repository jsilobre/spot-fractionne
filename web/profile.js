// Elevation profile of a segment, fetched on demand from the IGN Géoplateforme
// altimetry service (LiDAR HD ground model, RGE ALTI where LiDAR is missing)
// and drawn as a small SVG chart. The published tiles hold only the start /
// end elevations and the gain / loss, not the samples. Pure helpers (no DOM,
// no fetch): tested with `node --test`. Coordinates are [lon, lat] in WGS84.

import { distanceToLineMeters, haversineMeters, lineParts } from "./filters.js";

export const ELEVATION_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevationLine.json";
/** Service resources, best first: LiDAR HD ground model (50 cm), then RGE ALTI (1 m to 5 m). */
export const RESOURCES = [
  { id: "ign_lidar_hd_mnt_mono_wld", label: "IGN LiDAR HD" },
  { id: "ign_rge_alti_wld", label: "IGN RGE ALTI" },
];
// Sampling: every STEP_M, at most MAX_SAMPLES points (longer climbs get a wider step).
const STEP_M = 5;
const MAX_SAMPLES = 401;
// Smoothing as in the pipeline (params.py, ProfileParams): rolling median, then Gaussian.
const MEDIAN_WINDOW_M = 15;
const GAUSSIAN_SIGMA_M = 10;
// The service's nodata value is -99999; anything this low is no ground.
const NODATA_BELOW = -1000;
// Above this share of missing samples, try the next resource.
const MAX_MISSING_SHARE = 0.2;
// Pieces of a line cut by tile edges overlap by a few metres.
const JOIN_TOLERANCE_M = 5;
/** Grade classes (absolute %, upper bounds) of the colored bands; the last one is open. */
export const GRADE_CLASSES = [2, 5, 8, 12];
// The vertical axis spans at least this much, so that a flat stays flat.
const MIN_SPAN_M = 10;

/**
 * The segment's line as one ordered list of points: pieces cut by tile edges
 * (segmentsFromTiles) are chained end to start, dropping their overlap.
 * A climb runs uphill (the pipeline orients it so); a flat starts from its end
 * nearest to `from`; a loop starts at its point nearest to `from`.
 * @returns {[number, number][]}
 */
export function orderedLine(feature, from = null) {
  const pieces = lineParts(feature.geometry).filter((line) => line.length);
  if (!pieces.length) return [];
  const touchesOther = (point, self) =>
    pieces.some((line, i) => i !== self && distanceToLineMeters(point, line) < JOIN_TOLERANCE_M);
  let first = pieces.findIndex((line, i) => !touchesOther(line[0], i));
  if (first < 0) first = 0;
  const left = pieces.map((_, i) => i).filter((i) => i !== first);
  let previous = pieces[first];
  const line = [...previous];
  while (left.length) {
    const end = line.at(-1);
    let best = 0;
    for (let k = 1; k < left.length; k++) {
      if (haversineMeters(end, pieces[left[k]][0]) < haversineMeters(end, pieces[left[best]][0])) best = k;
    }
    const next = pieces[left.splice(best, 1)[0]];
    // Skip the head of the next piece that runs over the previous one.
    let start = 0;
    while (start < next.length - 1 && distanceToLineMeters(next[start], previous) < JOIN_TOLERANCE_M) start++;
    line.push(...next.slice(start));
    previous = next;
  }
  const kind = feature.properties?.kind;
  if (!from || kind === "climb" || kind === "long_climb") return line;
  if (kind === "loop") {
    const ring = haversineMeters(line[0], line.at(-1)) < JOIN_TOLERANCE_M ? line.slice(0, -1) : line;
    let nearest = 0;
    ring.forEach((point, i) => {
      if (haversineMeters(from, point) < haversineMeters(from, ring[nearest])) nearest = i;
    });
    return [...ring.slice(nearest), ...ring.slice(0, nearest + 1)];
  }
  return haversineMeters(from, line.at(-1)) < haversineMeters(from, line[0]) ? line.reverse() : line;
}

/** Length of a line in metres. */
export function lineLengthM(line) {
  let total = 0;
  for (let i = 1; i < line.length; i++) total += haversineMeters(line[i - 1], line[i]);
  return total;
}

/** Number of samples asked of the service for a line of `lengthM`. */
export function sampleCount(lengthM) {
  return Math.max(2, Math.min(MAX_SAMPLES, Math.round(lengthM / STEP_M) + 1));
}

/** Request (POST, the vertices of a long loop would not fit in a URL) for the samples of a line. */
export function elevationRequest(line, resource = RESOURCES[0].id) {
  const join = (index) => line.map((point) => point[index].toFixed(6)).join("|");
  return {
    url: ELEVATION_URL,
    init: {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lon: join(0),
        lat: join(1),
        resource,
        delimiter: "|",
        indent: "false",
        measures: "false",
        zonly: "false",
        profile: "true",
        sampling: String(sampleCount(lineLengthM(line))),
      }),
    },
  };
}

/**
 * Samples of a service answer: [{lon, lat, z}], z NaN where the model has no
 * ground. null when the answer holds no samples or too many are missing.
 */
export function parseElevations(json) {
  const raw = json?.elevations;
  if (!Array.isArray(raw) || raw.length < 2) return null;
  const points = raw.map(({ lon, lat, z }) => ({
    lon: Number(lon),
    lat: Number(lat),
    z: Number.isFinite(Number(z)) && Number(z) > NODATA_BELOW ? Number(z) : NaN,
  }));
  if (points.some((p) => !Number.isFinite(p.lon) || !Number.isFinite(p.lat))) return null;
  const missing = points.filter((p) => Number.isNaN(p.z)).length;
  return missing > MAX_MISSING_SHARE * points.length ? null : points;
}

/** Fill NaN values linearly from their valid neighbours (ends: nearest valid value). */
function fillGaps(z) {
  const valid = z.map((v, i) => (Number.isNaN(v) ? -1 : i)).filter((i) => i >= 0);
  if (!valid.length) return z;
  return z.map((v, i) => {
    if (!Number.isNaN(v)) return v;
    const after = valid.find((j) => j > i);
    const before = valid.findLast((j) => j < i);
    if (before === undefined) return z[after];
    if (after === undefined) return z[before];
    return z[before] + ((z[after] - z[before]) * (i - before)) / (after - before);
  });
}

/** Pad by point reflection around each end (keeps a linear trend), as profile.py does. */
function reflectPad(z, width) {
  const n = z.length;
  const at = (i) => z[Math.min(n - 1, Math.max(0, i))];
  const head = Array.from({ length: width }, (_, k) => 2 * z[0] - at(width - k));
  const tail = Array.from({ length: width }, (_, k) => 2 * z[n - 1] - at(n - 2 - k));
  return [...head, ...z, ...tail];
}

function rollingMedian(z, window) {
  const half = Math.floor((Math.max(1, window) | 1) / 2);
  if (!half || z.length < 2) return z;
  const padded = reflectPad(z, half);
  return z.map((_, i) => {
    const sorted = padded.slice(i, i + 2 * half + 1).sort((a, b) => a - b);
    return sorted[half];
  });
}

function gaussianSmooth(z, sigma) {
  if (sigma <= 0 || z.length < 2) return z;
  const radius = Math.max(1, Math.ceil(3 * sigma));
  const kernel = Array.from({ length: 2 * radius + 1 }, (_, k) => Math.exp(-0.5 * ((k - radius) / sigma) ** 2));
  const total = kernel.reduce((a, b) => a + b, 0);
  const padded = reflectPad(z, radius);
  return z.map((_, i) => kernel.reduce((sum, w, k) => sum + w * padded[i + k], 0) / total);
}

/**
 * Profile from the samples: cumulative distance, smoothed elevation.
 * @returns {{d: number[], z: number[], points: [number, number][], lengthM: number, min: number, max: number}}
 */
export function buildProfile(samples) {
  const points = samples.map((s) => [s.lon, s.lat]);
  const d = [0];
  for (let i = 1; i < points.length; i++) d.push(d[i - 1] + haversineMeters(points[i - 1], points[i]));
  const lengthM = d.at(-1);
  const step = lengthM / Math.max(1, points.length - 1) || STEP_M;
  const filled = fillGaps(samples.map((s) => s.z));
  const z = gaussianSmooth(rollingMedian(filled, Math.round(MEDIAN_WINDOW_M / step)), GAUSSIAN_SIGMA_M / step);
  return { d, z, points, lengthM, min: Math.min(...z), max: Math.max(...z) };
}

/** Width of the grade bands: about a twentieth of the length, a round number of metres. */
export function bandLengthM(lengthM) {
  const target = lengthM / 20;
  return [10, 20, 25, 50, 100, 200, 250, 500].find((step) => step >= target) ?? 1000;
}

/** Elevation at distance `x` along the profile (linear interpolation). */
export function elevationAt(profile, x) {
  const { d, z } = profile;
  if (x <= 0) return z[0];
  if (x >= d.at(-1)) return z.at(-1);
  let i = 1;
  while (d[i] < x) i++;
  const t = (x - d[i - 1]) / (d[i] - d[i - 1] || 1);
  return z[i - 1] + t * (z[i] - z[i - 1]);
}

/**
 * Grade bands: [{from, to, grade}] in metres and %, over `bandLengthM` each
 * (the last one takes the remainder, merged with the previous if very short).
 */
export function gradeBands(profile) {
  const length = profile.lengthM;
  if (!(length > 0)) return [];
  const step = bandLengthM(length);
  const edges = [];
  for (let x = 0; x < length - step / 2; x += step) edges.push(x);
  edges.push(length);
  const bands = [];
  for (let i = 1; i < edges.length; i++) {
    const [from, to] = [edges[i - 1], edges[i]];
    bands.push({ from, to, grade: (100 * (elevationAt(profile, to) - elevationAt(profile, from))) / (to - from) });
  }
  return bands;
}

/** Index in GRADE_CLASSES (0 to GRADE_CLASSES.length) of a grade, by its absolute value. */
export function gradeClass(grade) {
  const steepness = Math.abs(grade);
  const index = GRADE_CLASSES.findIndex((limit) => steepness < limit);
  return index < 0 ? GRADE_CLASSES.length : index;
}

/** Vertical range of the chart: the profile's, widened to MIN_SPAN_M and a margin. */
export function verticalRange(profile) {
  const span = Math.max(MIN_SPAN_M, (profile.max - profile.min) * 1.15);
  const middle = (profile.max + profile.min) / 2;
  return [middle - span / 2, middle + span / 2];
}

/** Profile sample nearest to distance `x`: its index. */
export function nearestSample(profile, x) {
  let best = 0;
  profile.d.forEach((v, i) => {
    if (Math.abs(v - x) < Math.abs(profile.d[best] - x)) best = i;
  });
  return best;
}

/** Local grade (%) around sample `i`, over about 20 m (the pipeline's grade base). */
export function gradeAt(profile, i) {
  const { d } = profile;
  const x = d[i];
  const from = Math.max(0, x - 10);
  const to = Math.min(profile.lengthM, x + 10);
  if (to - from < 1) return 0;
  return (100 * (elevationAt(profile, to) - elevationAt(profile, from))) / (to - from);
}

const round1 = (v) => Math.round(v * 10) / 10;

/**
 * SVG chart (a string) of a profile: bands filled by grade class (CSS classes
 * grade-0 to grade-4), the elevation line, min / max elevation and length
 * labels. Coordinates in a `width` × `height` viewBox, scaled by CSS.
 */
export function profileSvg(profile, { width = 300, height = 110, left = 34, bottom = 16, top = 6, right = 6 } = {}) {
  const [low, high] = verticalRange(profile);
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const sx = (x) => round1(left + (plotW * x) / (profile.lengthM || 1));
  const sy = (z) => round1(top + (plotH * (high - z)) / (high - low));
  const base = sy(low);
  const bands = gradeBands(profile)
    .map((band) => {
      const inside = profile.d
        .map((x, i) => [x, profile.z[i]])
        .filter(([x]) => x > band.from && x < band.to);
      const outline = [[band.from, elevationAt(profile, band.from)], ...inside, [band.to, elevationAt(profile, band.to)]];
      const path = outline.map(([x, z]) => `${sx(x)},${sy(z)}`).join(" L");
      return `<path class="grade-${gradeClass(band.grade)}" d="M${sx(band.from)},${base} L${path} L${sx(band.to)},${base} Z"/>`;
    })
    .join("");
  const line = profile.d.map((x, i) => `${sx(x)},${sy(profile.z[i])}`).join(" L");
  // Min / max labels beside their level, kept apart on a flat.
  let [yMax, yMin] = [sy(profile.max) + 4, sy(profile.min) + 4];
  if (yMin - yMax < 11) [yMax, yMin] = [round1((yMax + yMin) / 2 - 5.5), round1((yMax + yMin) / 2 + 5.5)];
  const label = (x, y, text, anchor = "end") => `<text x="${x}" y="${y}" text-anchor="${anchor}">${text}</text>`;
  const metres = (z) => `${Math.round(z)} m`;
  const length = profile.lengthM >= 1000 ? `${(profile.lengthM / 1000).toFixed(2).replace(".", ",")} km` : metres(profile.lengthM);
  return `<svg class="profile-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Profil altimétrique : de ${metres(profile.z[0])} à ${metres(profile.z.at(-1))}, entre ${metres(profile.min)} et ${metres(profile.max)}">
<g class="profile-bands">${bands}</g>
<path class="profile-line" d="M${line}"/>
<line class="profile-axis" x1="${left}" y1="${base}" x2="${width - right}" y2="${base}"/>
<line class="profile-axis" x1="${left}" y1="${top}" x2="${left}" y2="${base}"/>
${label(left - 3, yMax, metres(profile.max))}
${label(left - 3, yMin, metres(profile.min))}
${label(left, height - 3, "0", "start")}
${label(width - right, height - 3, length)}
<line class="profile-cursor" x1="0" y1="${top}" x2="0" y2="${base}" visibility="hidden"/>
<circle class="profile-dot" r="3" cx="0" cy="0" visibility="hidden"/>
</svg>`;
}

/** Inverse of profileSvg's horizontal scale: distance (m) at viewBox abscissa `vx`. */
export function distanceAtX(profile, vx, { width = 300, left = 34, right = 6 } = {}) {
  const t = (vx - left) / (width - left - right);
  return Math.min(1, Math.max(0, t)) * profile.lengthM;
}

/** Point of the chart (viewBox units) of sample `i`, with profileSvg's defaults. */
export function chartPoint(profile, i, { width = 300, height = 110, left = 34, bottom = 16, top = 6, right = 6 } = {}) {
  const [low, high] = verticalRange(profile);
  const x = left + ((width - left - right) * profile.d[i]) / (profile.lengthM || 1);
  const y = top + ((height - top - bottom) * (high - profile.z[i])) / (high - low);
  return [round1(x), round1(y)];
}
