// Basemaps the user can switch between: free services usable from a static
// page without an API key. Pure data and helpers, tested without a browser.

const IGN_WMTS = "https://data.geopf.fr/wmts";
const IGN_ATTRIBUTION =
  '<a href="https://geoservices.ign.fr/" target="_blank" rel="noopener">© IGN – Géoplateforme</a>';

/** Raster tile URL of an IGN Géoplateforme WMTS layer (Web Mercator grid). */
export function ignTileUrl(layer, format) {
  const params = new URLSearchParams({
    SERVICE: "WMTS",
    REQUEST: "GetTile",
    VERSION: "1.0.0",
    LAYER: layer,
    STYLE: "normal",
    TILEMATRIXSET: "PM",
    FORMAT: format,
  });
  // MapLibre fills the placeholders; they must stay unescaped.
  return `${IGN_WMTS}?${params}&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}`;
}

/** MapLibre style made of a single raster tile layer. */
function rasterStyle({ tiles, attribution, maxzoom }) {
  return {
    version: 8,
    sources: { basemap: { type: "raster", tiles, tileSize: 256, maxzoom, attribution } },
    layers: [{ id: "basemap", type: "raster", source: "basemap" }],
  };
}

export const BASEMAPS = [
  {
    id: "plan",
    label: "Plan",
    // Vector style; it carries its own attributions.
    style: "https://tiles.openfreemap.org/styles/liberty",
  },
  {
    id: "ign",
    label: "Plan IGN",
    style: rasterStyle({
      tiles: [ignTileUrl("GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2", "image/png")],
      attribution: IGN_ATTRIBUTION,
      maxzoom: 19,
    }),
  },
  {
    id: "photo",
    label: "Photos aériennes",
    style: rasterStyle({
      tiles: [ignTileUrl("ORTHOIMAGERY.ORTHOPHOTOS", "image/jpeg")],
      attribution: IGN_ATTRIBUTION,
      maxzoom: 19,
    }),
  },
  {
    id: "topo",
    label: "Relief",
    style: rasterStyle({
      tiles: ["a", "b", "c"].map((s) => `https://${s}.tile.opentopomap.org/{z}/{x}/{y}.png`),
      attribution:
        '<a href="https://opentopomap.org/" target="_blank" rel="noopener">© OpenTopoMap</a> (CC-BY-SA)',
      maxzoom: 17,
    }),
  },
];

export const DEFAULT_BASEMAP = BASEMAPS[0].id;

/** The basemap with this id, or the default one for an unknown id. */
export function basemapById(id) {
  return BASEMAPS.find((b) => b.id === id) ?? BASEMAPS[0];
}
