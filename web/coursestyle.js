// "Course" basemap: a sober vector style on the OpenFreeMap tiles (OpenMapTiles
// schema), built for runners. Paths, parks and water stand out, roads stay
// quiet and colour is left to the segments drawn on top. Pure data, tested
// without a browser.

const TILES = "https://tiles.openfreemap.org/planet";
const GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
const FONT = ["Noto Sans Regular"];
const FONT_ITALIC = ["Noto Sans Italic"];

/** Colour sets the style can be drawn with; `light` is the one in use. */
export const COURSE_PALETTES = {
  // Light and calm, close to the sports-tracking apps.
  light: {
    background: "#f4f2ee",
    residential: "#efece6",
    park: "#d8ead0",
    wood: "#cfe3c4",
    grass: "#e1eed9",
    pitch: "#cfe6c6",
    water: "#b9d7ea",
    waterLine: "#a3c8e0",
    building: "#e4e0d9",
    road: "#ffffff",
    roadCasing: "#d9d5ce",
    majorRoad: "#ffffff",
    majorCasing: "#c9c4bb",
    path: "#7a6a58",
    cycleway: "#5f7f9c",
    track: "#9a8a72",
    rail: "#c4c0b9",
    label: "#6b6762",
    labelHalo: "#f4f2ee",
    waterLabel: "#5a8fb3",
  },
  // Dark: the segments glow, the town fades.
  dark: {
    background: "#1f2124",
    residential: "#232528",
    park: "#26352a",
    wood: "#25332a",
    grass: "#2a362c",
    pitch: "#2d3d30",
    water: "#1d3447",
    waterLine: "#284459",
    building: "#2b2e32",
    road: "#3a3d42",
    roadCasing: "#1f2124",
    majorRoad: "#4a4e54",
    majorCasing: "#1f2124",
    path: "#c9b99f",
    cycleway: "#8fb2cf",
    track: "#a39782",
    rail: "#45484d",
    label: "#a7a9ad",
    labelHalo: "#1f2124",
    waterLabel: "#6e9cbd",
  },
  // Green: parks and woods first, roads barely there.
  nature: {
    background: "#f6f5ef",
    residential: "#f2f0e9",
    park: "#bfe0b0",
    wood: "#a9d39a",
    grass: "#cfe8c2",
    pitch: "#b8dca8",
    water: "#a6d1ef",
    waterLine: "#8cc0e6",
    building: "#ebe8e1",
    road: "#ffffff",
    roadCasing: "#e3dfd7",
    majorRoad: "#fbfaf7",
    majorCasing: "#d6d1c8",
    path: "#8a4f1d",
    cycleway: "#8a4f1d",
    track: "#8a4f1d",
    rail: "#d0ccc4",
    label: "#5f5b55",
    labelHalo: "#f6f5ef",
    waterLabel: "#3f83b5",
  },
  // Greyscale: every colour on screen is a segment.
  grey: {
    background: "#f3f3f3",
    residential: "#efefef",
    park: "#e2e6e0",
    wood: "#dbe0d9",
    grass: "#e6e9e4",
    pitch: "#dde2db",
    water: "#d6dde3",
    waterLine: "#c9d2da",
    building: "#e6e6e6",
    road: "#ffffff",
    roadCasing: "#dcdcdc",
    majorRoad: "#ffffff",
    majorCasing: "#cfcfcf",
    path: "#6e6e6e",
    cycleway: "#6e6e6e",
    track: "#8c8c8c",
    rail: "#cccccc",
    label: "#777777",
    labelHalo: "#f3f3f3",
    waterLabel: "#8a99a6",
  },
};

// Line width growing with the zoom: [zoom, width] stops.
const widthBy = (...stops) => ["interpolate", ["exponential", 1.5], ["zoom"], ...stops.flat()];
const classIn = (...classes) => ["match", ["get", "class"], classes, true, false];
const isLine = ["match", ["geometry-type"], ["LineString", "MultiLineString"], true, false];
const notTunnel = ["!=", ["get", "brunnel"], "tunnel"];
const isCycleway = ["any", ["==", ["get", "subclass"], "cycleway"], ["==", ["get", "bicycle"], "designated"]];

const MINOR = ["minor", "service"];
const MAJOR = ["motorway", "trunk", "primary", "secondary", "tertiary"];

/** MapLibre style of the "Course" basemap drawn with one of `COURSE_PALETTES`. */
export function courseStyle(p = COURSE_PALETTES.light) {
  const fill = (id, layer, filter, color, extra = {}) => ({
    id,
    type: "fill",
    source: "openmaptiles",
    "source-layer": layer,
    ...(filter && { filter }),
    paint: { "fill-color": color, ...extra },
  });
  const line = (id, filter, paint, extra = {}) => ({
    id,
    type: "line",
    source: "openmaptiles",
    "source-layer": "transportation",
    filter: ["all", isLine, notTunnel, filter],
    layout: { "line-cap": "round", "line-join": "round" },
    paint,
    ...extra,
  });
  const label = (id, layer, filter, layout, paint) => ({
    id,
    type: "symbol",
    source: "openmaptiles",
    "source-layer": layer,
    ...(filter && { filter }),
    layout: { "text-font": FONT, ...layout },
    paint: { "text-color": p.label, "text-halo-color": p.labelHalo, "text-halo-width": 1.5, ...paint },
  });
  const name = ["coalesce", ["get", "name:fr"], ["get", "name"]];

  return {
    version: 8,
    glyphs: GLYPHS,
    sources: { openmaptiles: { type: "vector", url: TILES } },
    layers: [
      { id: "background", type: "background", paint: { "background-color": p.background } },
      fill("residential", "landuse", classIn("residential", "suburb", "neighbourhood"), p.residential),
      fill("wood", "landcover", classIn("wood", "forest"), p.wood),
      fill("grass", "landcover", classIn("grass", "meadow", "scrub"), p.grass),
      fill("park", "park", null, p.park),
      fill("leisure", "landuse", classIn("park", "pitch", "track", "playground", "cemetery", "stadium"), p.pitch),
      fill("water", "water", ["!=", ["get", "brunnel"], "tunnel"], p.water),
      {
        id: "waterway",
        type: "line",
        source: "openmaptiles",
        "source-layer": "waterway",
        filter: notTunnel,
        paint: { "line-color": p.waterLine, "line-width": widthBy([10, 0.5], [16, 3]) },
      },
      fill("building", "building", null, p.building, { "fill-opacity": ["interpolate", ["linear"], ["zoom"], 14, 0, 15, 1] }),
      {
        id: "rail",
        type: "line",
        source: "openmaptiles",
        "source-layer": "transportation",
        filter: ["all", notTunnel, classIn("rail", "transit")],
        paint: { "line-color": p.rail, "line-width": widthBy([12, 0.5], [16, 1.5]) },
      },
      // Roads: white with a thin casing, never coloured.
      line("minor-casing", classIn(...MINOR), { "line-color": p.roadCasing, "line-width": widthBy([13, 1.2], [18, 16]) }, { minzoom: 13 }),
      line("major-casing", classIn(...MAJOR), { "line-color": p.majorCasing, "line-width": widthBy([8, 0.8], [18, 24]) }),
      line("minor", classIn(...MINOR), { "line-color": p.road, "line-width": widthBy([12, 0.4], [18, 13]) }, { minzoom: 12 }),
      line("major", classIn(...MAJOR), { "line-color": p.majorRoad, "line-width": widthBy([8, 0.5], [18, 20]) }),
      // Where one runs: paths, tracks and cycleways, dashed and on top of the roads.
      line(
        "track",
        classIn("track"),
        { "line-color": p.track, "line-width": widthBy([13, 0.8], [18, 3]), "line-dasharray": [2, 1.5] },
        { minzoom: 13 },
      ),
      line(
        "path",
        ["all", classIn("path", "pedestrian"), ["!", isCycleway], ["!=", ["get", "subclass"], "steps"]],
        { "line-color": p.path, "line-width": widthBy([13, 0.8], [18, 3]), "line-dasharray": [2, 1.2] },
        { minzoom: 13 },
      ),
      line(
        "cycleway",
        ["all", classIn("path", "pedestrian"), isCycleway],
        { "line-color": p.cycleway, "line-width": widthBy([12, 0.8], [18, 3.5]) },
        { minzoom: 12 },
      ),
      label("water-name", "water_name", null, { "text-field": name, "text-size": 12, "text-font": FONT_ITALIC }, {
        "text-color": p.waterLabel,
      }),
      label(
        "road-name",
        "transportation_name",
        classIn(...MAJOR, "minor"),
        { "text-field": name, "text-size": 11, "symbol-placement": "line" },
        {},
      ),
      label(
        "park-name",
        "park",
        null,
        { "text-field": name, "text-size": 11, "text-font": FONT_ITALIC },
        { "text-color": p.label },
      ),
      label(
        "place",
        "place",
        classIn("city", "town", "village", "suburb", "neighbourhood", "quarter"),
        {
          "text-field": name,
          "text-size": ["match", ["get", "class"], "city", 16, "town", 14, 12],
          "text-transform": ["match", ["get", "class"], ["suburb", "neighbourhood", "quarter"], "uppercase", "none"],
          "text-letter-spacing": ["match", ["get", "class"], ["suburb", "neighbourhood", "quarter"], 0.1, 0],
        },
        {},
      ),
    ],
  };
}
