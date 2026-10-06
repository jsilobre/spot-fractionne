# Modèle de données

Ce document décrit le schéma des tables produites par le pipeline. La table
centrale est **`segments`** : c'est elle que consomme le front, et elle
deviendra une table PostGIS en phase 3. Les tables intermédiaires (`strokes`,
`profiles`) sont décrites en fin de document.

Version du schéma : **0.1**. Elle figure dans les métadonnées de l'export et
évolue à chaque changement incompatible.

## Conventions

- **Unités** : mètres (`_m`), pourcentages (`_pct`), degrés (`_deg`).
- **Pentes signées** dans le sens de la géométrie : positif = montée. Les côtes
  sont orientées vers le haut, leur `grade_mean_pct` est donc toujours > 0.
- **Géométrie** : `LineString`, en Lambert-93 (EPSG:2154) dans le GeoParquet
  et en WGS84 (EPSG:4326) dans le GeoJSON.
- **Valeurs inconnues** : `"unknown"` pour les catégories, `null` pour les
  nombres absents. Jamais de chaîne vide.
- **Listes** : tableaux JSON dans le GeoJSON, colonnes `list<…>` dans le
  GeoParquet.

## Table `segments`

Fichiers : `data/processed/segments.parquet` (GeoParquet) et
`web/data/segments.geojson` (export). En Python : dataclass
`flat_segments.detect.Segment`.

| Champ | Type | Unité | Description |
|---|---|---|---|
| `id` | `str` | — | Identifiant stable `"{kind}-{12 hex}"`, voir [`algorithm.md` § 12](algorithm.md#12-identifiant-stable) |
| `kind` | `"flat"` \| `"climb"` | — | Type de segment |
| `geometry` | `LineString` | — | Sous-polyligne exacte du réseau OSM ; côtes orientées vers le haut |
| `length_m` | `float` | m | Longueur le long de la géométrie |
| `elev_start_m` | `float` | m | Altitude (lissée) au début |
| `elev_end_m` | `float` | m | Altitude (lissée) à la fin |
| `elev_gain_m` | `float` | m | Dénivelé positif cumulé (D+), hystérésis 0,5 m |
| `elev_loss_m` | `float` | m | Dénivelé négatif cumulé (D-), valeur positive |
| `grade_mean_pct` | `float` | % | Pente moyenne `(elev_end − elev_start) / length`, signée |
| `grade_max_pct` | `float` | % | Pente locale maximale en valeur absolue (base 20 m) |
| `sinuosity` | `float` \| `null` | — | Longueur / distance à vol d'oiseau entre extrémités (≥ 1) ; `null` dans le GeoJSON pour une boucle fermée. Le site affiche « boucle » au-delà de 3 (extrémités proches) |
| `n_crossings` | `int` | — | Intersections avec une route circulée (`MINOR`) à l'intérieur du segment |
| `n_junctions` | `int` | — | Carrefours avec d'autres chemins à l'intérieur du segment |
| `surface` | `str` | — | Revêtement majoritaire : `paved`, `compacted`, `gravel`, `cobbles`, `unpaved`, `unknown` |
| `lit` | `str` | — | Éclairage : `yes`, `partial`, `no`, `unknown` |
| `name` | `str` \| `null` | — | Nom OSM couvrant au moins la moitié du segment (tag `name`) |
| `highways` | `list[str]` | — | Valeurs `highway` rencontrées, par longueur décroissante |
| `osm_way_ids` | `list[int]` | — | Ways OSM traversées, dans l'ordre |
| `on_structure` | `bool` | — | Passe sur un pont ou dans un tunnel |
| `quality_flags` | `list[str]` | — | Voir ci-dessous |
| `fits_targets_m` | `list[int]` | m | Longueurs cibles réalisables dans le segment (§ 8 de l'algorithme) |
| `score` | `float` | 0–100 | Qualité globale, voir [`algorithm.md` § 10](algorithm.md#10-score) |
| `elevation_source` | `str` | — | Source d'altitude : `lidar_hd` (MNT LiDAR HD, défaut), `rge_alti_1m` (archive RGE ALTI), `rge_alti_wms` (RGE ALTI par WMS, ≈ 4 m), `copernicus_glo30`, `synthetic` |

Champs **internes**, présents seulement dans le GeoParquet :

| Champ | Type | Description |
|---|---|---|
| `stroke_id` | `str` | Stroke d'origine |
| `stroke_start_m` | `float` | Abscisse de début sur le stroke (sens du stroke) |
| `stroke_end_m` | `float` | Abscisse de fin sur le stroke |

### `quality_flags`

| Flag | Signification |
|---|---|
| `bridge_interpolated` | Altitude interpolée sur un pont |
| `tunnel_interpolated` | Altitude interpolée dans un tunnel ou un passage couvert |
| `gap_filled` | Trou *nodata* du MNT comblé par interpolation |
| `dem_coarse` | MNT de repli à 30 m : plats peu fiables (phase 3) |

### Ce qui n'est **pas** dans la table

- **Distance à l'utilisateur** : elle dépend de la position, le front la
  calcule.
- **Rang** : il dépend des filtres choisis ; le front trie.

## Table `loops`

Fichiers : `data/processed/loops.parquet` (commande `loops`) et
`data/departments/CODE/loops.parquet` (étape `loops`) pour les pistes ;
`circuits.parquet` (commande et étape `circuits`) pour les boucles du réseau,
avec les mêmes champs. GeoParquet en
Lambert-93, une ligne par boucle. En Python : dataclass
`flat_segments.loops.Loop`. Publiée dans les tuiles avec le fichier de
segments du même dossier (voir ci-dessous) ; `kind` y vaut `loop`.

| Champ | Type | Unité | Description |
|---|---|---|---|
| `id` | `str` | — | Identifiant stable `"loop-{12 hex}"`, voir [`algorithm.md` § 15](algorithm.md#15-boucles--pistes-dathlétisme) |
| `geometry` | `LineString` | — | Anneau fermé (premier point répété à la fin) : bord intérieur d'une piste en anneau, sinon son contour |
| `loop_type` | `str` | — | `track` (piste d'athlétisme) ; `circuit` (boucle du réseau, [`algorithm.md` § 16](algorithm.md#16-boucles-du-réseau)) |
| `length_m` | `float` | m | Longueur mesurée de l'anneau |
| `lap_m` | `float` \| `null` | m | Tour standard correspondant (200, 250, 300, 333,3 ou 400 m), `null` si aucun |
| `name` | `str` \| `null` | — | Nom de la piste, sinon de l'équipement qui l'entoure ; pour une boucle du réseau, nom du parc ou du lac |
| `surface` | `str` | — | Revêtement, mêmes valeurs que pour `segments` |
| `lit` | `str` | — | Éclairage : `yes`, `no`, `unknown` |
| `access` | `str` | — | `public`, `restricted`, `unknown` (toujours `public` pour une boucle du réseau) |
| `opening_hours` | `str` \| `null` | — | Tag OSM `opening_hours`, brut |
| `indoor` | `bool` | — | Piste couverte |
| `osm_id` | `str` | — | Objet OSM de la piste : `way/123` ou `relation/45` ; pour une boucle du réseau, le parc ou le lac dont elle fait le tour, sinon sa plus longue voie |
| `setting` | `str` \| `null` | — | Boucles du réseau : `water` (tour de lac ou d'étang), `park`, `neighbourhood` |
| `n_crossings` | `int` \| `null` | — | Boucles du réseau : routes qui rejoignent la boucle |
| `grade_max_pct` | `float` \| `null` | % | Boucles du réseau : pente locale maximale le long de la boucle |

## Publication en tuiles vectorielles (site)

Depuis l'[étape 2.2](phase-2/2.2-tuiles.md), le site lit trois éléments dans
`web/data/`, écrits par `flat-segments export-pmtiles`
([ADR 0009](adr/0009-pmtiles-tippecanoe.md)). En production, ils sont
publiés sur Cloudflare R2 ([ADR 0013](adr/0013-donnees-sur-r2.md)) :
`tiles/segments-<version>.pmtiles` et `ids/<version>/`, et `segments.json`
donne leurs adresses absolues (`tiles.url`, `tiles.index`). À défaut, ils
sont dans la release `data-latest`, l'index étant archivé dans `ids.tar.gz`
([ADR 0011](adr/0011-production-github-actions.md)).

**`segments.pmtiles`** : tuiles vectorielles (MVT, compressées en gzip,
projection Web Mercator) en deux couches.

| Couche | Zooms | Propriétés |
|---|---|---|
| `segments` | 12 à 14 (agrandies au-delà) | tous les champs publics de la table `segments`, sans perte ; pour une boucle (`kind = "loop"`), `id`, `kind` et les champs de la table `loops` |
| `overview` | 8 à 11 | `id`, `kind`, `length_m` ; segments éclaircis là où ils sont trop denses |

- Les tuiles vectorielles ne connaissent ni listes ni valeurs nulles :
  - les listes (`highways`, `osm_way_ids`, `quality_flags`, `fits_targets_m`)
    sont écrites en chaînes JSON (`"[200,400]"`) ;
  - les valeurs nulles (`name`, `sinuosity` d'une boucle fermée) sont omises.
- Un segment coupé par le bord d'une tuile apparaît dans chacune des tuiles
  qu'il traverse. Le front recolle les morceaux grâce à son `id`.

**`segments.json`** : description du jeu publié.

| Clé | Contenu |
|---|---|
| `schema_version`, `generated_at`, `sample`, `attribution`, `params` | comme le membre `metadata` du GeoJSON (ci-dessous) |
| `bounds` | emprise `[ouest, sud, est, nord]` (WGS84) |
| `counts` | nombre de plats (`flat`), de côtes (`climb`) et de boucles (`loop`) |
| `tiles` | fichier, nom et zooms des couches, dossier et longueur de préfixe de l'index |

**`ids/XX.json`** : `{"flat-3fa2b1c9d0e4": [lon, lat], …}`, la position (point
milieu) de chaque segment, ou d'un point de chaque boucle.
- Un identifiant d'une version précédente est noté `[lon, lat, cible]` :
  - `cible` est l'identifiant du segment qui le remplace, et la position est
    celle de ce segment ;
  - `cible` vaut `null` si le segment n'existe plus, et la position est celle
    qu'il avait ([ADR 0012](adr/0012-identifiants-stables.md)).
- Un fichier par valeur des premiers caractères hexadécimaux de
  l'identifiant (`3f.json`) : 2 caractères, soit 256 fichiers au plus,
  jusqu'à 512 000 identifiants, puis un de plus par palier
  (`tiles.index_prefix_length` dans `segments.json`).
- Le front s'en sert pour ouvrir un lien `?id=` sans charger tout le jeu.

## Export GeoJSON (inspection)

Écrit par `flat-segments export` (et par `pipeline`) dans `data/processed/`,
pour inspecter un jeu dans QGIS ou un éditeur. Le site ne le lit plus.

`FeatureCollection` conforme à la RFC 7946 : WGS84, coordonnées `[lon, lat]`
arrondies à 6 décimales. Les champs internes sont retirés. Un membre
`metadata` (membre étranger, toléré par la RFC) décrit l'export :

| Clé | Contenu |
|---|---|
| `schema_version` | Version de ce schéma |
| `generated_at` | Date de l'export (UTC) |
| `sample` | `true` pour des données fictives (bandeau sur la carte) |
| `attribution` | Mentions affichées sur la carte : OSM, puis chaque source d'altitude présente |
| `params` | Paramètres de détection utilisés, même structure que `configs/default.toml` (présent si `detect` les a enregistrés) |

```json
{
  "type": "FeatureCollection",
  "metadata": {
    "schema_version": "0.1",
    "generated_at": "2026-09-30T12:00:00Z",
    "sample": false,
    "attribution": [
      "© les contributeurs d'OpenStreetMap (ODbL)",
      "IGN – MNT LiDAR HD (Licence Ouverte 2.0)"
    ],
    "params": {"network": {"max_deflection_deg": 35.0, "…": "…"}, "…": "…"}
  },
  "features": [
    {
      "type": "Feature",
      "id": "flat-3fa2b1c9d0e4",
      "geometry": {
        "type": "LineString",
        "coordinates": [[1.534021, 43.531870], [1.538412, 43.532005]]
      },
      "properties": {
        "id": "flat-3fa2b1c9d0e4",
        "kind": "flat",
        "length_m": 412.5,
        "elev_start_m": 151.2,
        "elev_end_m": 151.9,
        "elev_gain_m": 0.7,
        "elev_loss_m": 0.0,
        "grade_mean_pct": 0.17,
        "grade_max_pct": 0.9,
        "sinuosity": 1.02,
        "n_crossings": 0,
        "n_junctions": 2,
        "surface": "paved",
        "lit": "unknown",
        "name": "Voie verte",
        "highways": ["cycleway"],
        "osm_way_ids": [123456789],
        "on_structure": false,
        "quality_flags": [],
        "fits_targets_m": [200, 400],
        "score": 86.4,
        "elevation_source": "lidar_hd"
      }
    }
  ]
}
```

Précision des valeurs numériques à l'export : longueurs et altitudes à 0,1 m,
pentes à 0,01 %, sinuosité à 0,01, score à 0,1. Si `metadata.sample` vaut
`true`, les données sont **fictives** et le front affiche un bandeau.

## Tables intermédiaires

### `strokes` (`data/interim/strokes.parquet`)

Produite par `extract`. GeoParquet en Lambert-93, une ligne par stroke.
En Python : `flat_segments.network.Stroke`.

| Champ | Type | Description |
|---|---|---|
| `stroke_id` | `str` | `"s000001"`… (numérotation propre à un run) |
| `geometry` | `LineString` | Polyligne complète |
| `length_m` | `float` | Longueur |
| `is_ring` | `bool` | Stroke fermé (anneau) |
| `parts` | `str` (JSON) | Liste de `StrokePart` : `way_id`, `start_m`, `end_m`, `highway`, `road_class`, `surface`, `tracktype`, `lit`, `structure`, `name` |
| `events` | `str` (JSON) | Liste de `StrokeEvent` : `offset_m`, `kind` (`crossing` \| `junction`), `node_id` |

### Paramètres de détection (`data/processed/segments.params.toml`)

Écrit par `detect` à côté de `segments.parquet`. C'est le TOML complet des
paramètres utilisés, au format de `configs/default.toml`. `export` le recopie
dans `metadata.params`, et `inspect` le relit pour tracer les seuils qui ont
réellement servi.

### `profiles` (`data/interim/profiles.parquet`)

Produite par `elevation`. Parquet simple (sans géométrie), une ligne par
stroke. La grille des abscisses se recalcule à partir de la longueur du stroke
et du pas.

| Champ | Type | Description |
|---|---|---|
| `stroke_id` | `str` | Clé vers `strokes` |
| `step_m` | `float` | Pas demandé (`profile.step_m`) ; le pas effectif est `length_m / ceil(length_m / step_m)` |
| `z_raw` | `list<float64>` | Altitudes brutes aux points de la grille, `NaN` = *nodata* |
| `elevation_source` | `str` | Source d'altitude du stroke : `lidar_hd`, ou `rge_alti_wms` pour un stroke relu dans le RGE ALTI là où le LiDAR HD manque ([`algorithm.md` § 3](algorithm.md#3-échantillonnage-de-laltitude)) |

## Évolution vers PostGIS (phase 3)

- `segments` devient une table avec `geometry(LineString, 2154)`, un index GiST,
  et `id` en clé primaire. `highways`, `osm_way_ids`, `quality_flags` et
  `fits_targets_m` deviennent des tableaux PostgreSQL.
- On ajoute une colonne `region` et une date de calcul, pour les mises à jour
  incrémentales.
- L'API sert le même schéma en JSON, ou en tuiles vectorielles (MVT).
