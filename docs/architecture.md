# Architecture

Ce document décrit l'organisation de `flat-segments` : ses composants, le
chemin des données et les phases prévues. Les choix structurants sont justifiés
dans les [ADR](adr/README.md), et le détail des calculs dans
[`algorithm.md`](algorithm.md).

## 1. Principes

1. **Tout calculer à l'avance, hors ligne.** Détecter les segments demande
   de croiser un réseau routier avec un modèle numérique de terrain (MNT)
   au mètre, ce qui est trop lourd pour un navigateur. Un pipeline Python
   produit donc, une fois pour une zone, la liste des segments candidats
   et leurs attributs ([ADR 0001](adr/0001-pipeline-python-uv.md)).
2. **Un front statique d'abord.** Le navigateur charge les segments
   précalculés et fait lui-même le filtrage (longueur, pente, distance,
   traversées). Pas de serveur à maintenir : un hébergement GitHub Pages
   suffit ([ADR 0005](adr/0005-front-statique-maplibre.md)).
3. **Des fichiers plutôt qu'une base.** Chaque étape du pipeline lit et écrit
   des fichiers (GeoParquet), ce qui les rend inspectables et rejouables
   séparément ([ADR 0004](adr/0004-stockage-geoparquet.md)). PostGIS
   n'arrive qu'avec l'API (phase 3).
4. **La logique pure séparée des entrées/sorties.** Les calculs (géométrie,
   profils, fenêtre glissante…) ne manipulent que des tableaux numpy et des
   dataclasses. On les teste sur des données synthétiques, sans fichier ni
   réseau.
5. **Des calculs en mètres.** Le pipeline travaille en Lambert-93
   (EPSG:2154) et ne passe en WGS84 (EPSG:4326) qu'à l'export pour le web
   (voir [`data-sources.md`](data-sources.md#projections)).

## 2. Vue d'ensemble

```mermaid
flowchart LR
    subgraph Sources
        OSM[("Extrait OSM<br/>Geofabrik<br/>.osm.pbf")]
        DEM[("MNT LiDAR HD<br/>IGN (WMS)<br/>dalles GeoTIFF / VRT")]
    end

    subgraph Pipeline["Pipeline hors ligne (Python, uv)"]
        EX[extract]
        EL[elevation]
        DE[detect]
        XP[export]
    end

    subgraph Stockage["Stockage fichiers (data/)"]
        ST[("strokes.parquet")]
        PR[("profiles.parquet")]
        SG[("segments.parquet")]
    end

    subgraph Front["Site statique (web/)"]
        GJ[("segments.geojson")]
        UI["Carte MapLibre<br/>+ filtres JS"]
    end

    OSM --> EX --> ST
    ST --> EL
    DEM --> EL --> PR
    ST --> DE
    PR --> DE --> SG --> XP --> GJ --> UI
```

## 3. Composants

### 3.1 Pipeline Python (`src/flat_segments/`)

Chaque étape est une commande de la CLI Typer `flat-segments` :

| Commande | Entrée | Sortie | Modules |
|---|---|---|---|
| `download-osm` | URL de l'extrait (Geofabrik ou miroir OSM France) + emprise | `data/raw/midi-pyrenees-latest.osm.pbf` (ou `midi_pyrenees.osm.pbf`), `data/raw/pilot.osm.pbf` (découpé) | `download.py`, `osm.py` |
| `download-dem` | emprise + service WMS de la Géoplateforme | `data/raw/dem/tiles/*.tif`, `data/raw/dem/pilot.vrt` | `download.py` |
| `extract` | extrait OSM `.osm.pbf` + emprise | `data/interim/strokes.parquet` | `osm.py`, `network.py` |
| `elevation` | strokes + MNT (GeoTIFF/VRT en Lambert-93) | `data/interim/profiles.parquet` | `elevation.py` |
| `detect` | strokes + profils | `data/processed/segments.parquet` | `profile.py`, `detect.py` |
| `loops` | extrait OSM complet (pas l'extrait pilote découpé) + emprise | `data/processed/loops.parquet` (pistes d'athlétisme) | `osm.py`, `loops.py` |
| `export` | segments | `data/processed/segments.geojson` (inspection) | `export.py` |
| `export-pmtiles` | un ou plusieurs fichiers de segments ; avec `--previous`, le jeu déjà publié | `web/data/segments.pmtiles`, `segments.json`, `ids/` (identifiants de la version publiée conservés, [ADR 0012](adr/0012-identifiants-stables.md)) | `tiles.py` (tippecanoe), `lineage.py` |
| `pipeline` | extrait découpé + MNT | les quatre sorties ci-dessus | `pipeline.py` |

Le découpage en quatre étapes permet de régler les seuils de détection
(`detect`) sans relire le PBF ni rééchantillonner le MNT.

**Production par département** (phase 2, [étape 2.1](phase-2/2.1-departement.md)) :

| Commande | Entrée | Sortie | Modules |
|---|---|---|---|
| `download-departments` | WFS de la Géoplateforme (Admin Express) | `data/raw/departements.geojson` | `departments.py` |
| `department CODE` | extrait OSM régional + contours | `data/departments/CODE/` : `strokes.parquet`, `profiles.parquet`, `segments.parquet`, `loops.parquet`, `state.json` | `batch.py`, `departments.py` |
| `departments CODE…` | idem, plusieurs départements | idem, plus un récapitulatif | `batch.py` |
| `renumber-osm PBF OUT` | extrait OSM | copie dont les nœuds sont numérotés à partir de 1, voies inchangées (mémoire d'osmium pour `cut-osm`) | `osm_extracts.py` (osmium) |
| `cut-osm PBF [CODE…]` | extrait OSM national + contours | `data/osm/CODE.osm.pbf` : un extrait par département (contour élargi, voies entières), par lots | `osm_extracts.py` (osmium) |
| `department-codes [CODE…]` | contours | liste JSON des codes (toute la métropole par défaut), pour le workflow | `departments.py` |
| `department-summary STATE…` | `state.json` de départements traités | tableau récapitulatif (Markdown) | `batch.py` |

Un département est traité sur son contour élargi de 2 km. On garde les
segments dont le milieu est dans le département, si bien que chaque segment
appartient à un seul département. Chaque étape terminée est notée dans
`state.json`, et une relance reprend après la dernière.

**Paramètres.** Les étapes utilisent les valeurs par défaut de `params.py`,
remplacées par un fichier TOML (`--config`, modèle dans
`configs/default.toml`) puis par des surcharges `--set cle=valeur`. `detect`
enregistre les paramètres utilisés à côté de ses segments
(`segments.params.toml`), et `export` les recopie dans les métadonnées du
GeoJSON : un jeu publié dit toujours comment il a été produit.

**Calibrage** (phase 1) :

| Commande | Rôle |
|---|---|
| `report` | Résumé d'un fichier de segments : nombres, longueurs, longueurs cibles, traversées, revêtements, *flags* |
| `sweep` | Relance la détection pour plusieurs valeurs d'un paramètre et compare les résultats |
| `compare` | Compare deux fichiers de segments (deux MNT, deux réglages, deux passages) : nombres, km, part des km de chacun retrouvée dans l'autre à 10 m près, segments manquants |
| `inspect` | Profil brut / lissé et pente locale autour d'un segment (PNG, matplotlib) |
| `validation-sheet` | Échantillon représentatif de segments sous forme de fiche terrain à remplir ([`validation/`](validation/README.md)) |
| `config` | Affiche les paramètres effectifs en TOML |

Les modules :

| Module | Rôle | Nature |
|---|---|---|
| `params.py` | Paramètres de l'algorithme (dataclasses figées, invariants vérifiés) avec leurs valeurs par défaut | pur |
| `config.py` | Paramètres en TOML : lecture, écriture, surcharges `cle=valeur` | pur + E/S |
| `geometry.py` | Longueurs, rééchantillonnage, sous-polyligne, sinuosité, caps, distances point-polyligne, id stable | pur |
| `osm.py` | Classement des voies selon leurs tags OSM ; lecture du PBF (pyosmium) et reprojection ; lecture des surfaces sportives | pur + E/S |
| `network.py` | Graphe des voies, chaînage en *strokes* (polylignes continues), événements (traversées, carrefours) | pur |
| `elevation.py` | Échantillonnage du MNT le long des strokes (interpolation bilinéaire), accès raster | pur + E/S |
| `profile.py` | Profil en long : bouche-trous, interpolation sous ponts et tunnels, lissage, pente locale, D+/D- | pur |
| `detect.py` | Fenêtre glissante, fusion en tronçons maximaux, attributs, score, déduplication | pur |
| `loops.py` | Boucles : pistes d'athlétisme retenues, tour standard, accès, doublons, id stable ([ADR 0014](adr/0014-categorie-boucles.md)) | pur |
| `export.py` | Lecture/écriture GeoParquet, export GeoJSON (WGS84) | E/S |
| `download.py` | Téléchargements : extrait OSM (MD5), dalles MNT par WMS, assemblage en VRT | E/S |
| `pipeline.py` | Les quatre étapes, et la lecture des boucles, sous forme de fonctions partagées par les commandes | E/S |
| `departments.py` | Contours des départements (Admin Express), règle du milieu pour rattacher un segment ou une boucle | pur + E/S |
| `batch.py` | Production par département : étapes avec reprise, état, récapitulatif | E/S |
| `osm_extracts.py` | Découpe d'un extrait OSM national par département (osmium) | E/S |
| `tiles.py` | Publication en tuiles vectorielles (tippecanoe, tile-join), métadonnées, index des identifiants | E/S |
| `lineage.py` | Rapprochement avec la version publiée : identifiants conservés, redirigés ou retirés | pur + E/S |
| `calibration.py` | Rapport, balayage de paramètres, graphique de profil, fiche de validation | pur + E/S |
| `cli.py` | CLI Typer, câblage des étapes | E/S |

Dépendances : numpy (calcul), pyosmium (OSM), pyproj (projections), rasterio
(MNT), geopandas/pyarrow/shapely (GeoParquet), Typer (CLI).

### 3.2 Stockage

Au stade prototype, tout le stockage est fait de fichiers dans `data/`
(non versionné) :

```
data/
├── raw/
│   ├── midi-pyrenees-latest.osm.pbf   # extrait régional (Geofabrik ou miroir)
│   ├── pilot.osm.pbf                  # extrait découpé sur la zone pilote
│   ├── departements.geojson           # contours des départements (Admin Express)
│   └── dem/
│       ├── tiles/*.tif                # dalles MNT LiDAR HD (GeoTIFF compressés)
│       └── pilot.vrt                  # mosaïque virtuelle GDAL
├── interim/      # strokes.parquet, profiles.parquet
├── processed/    # segments.parquet (GeoParquet, Lambert-93), segments.params.toml, inspect/*.png
└── departments/
    └── 31/       # strokes, profiles, segments (+ params), state.json ; dem/ supprimé après usage
```

Le schéma de chaque table est décrit dans [`data-model.md`](data-model.md).
Le GeoJSON destiné au web est écrit dans `web/data/`.

### 3.3 Front statique (`web/`)

- `index.html`, `style.css` : une page, sans framework ni étape de build.
- `app.js` : carte MapLibre GL JS (modules ES chargés depuis un CDN par une
  *import map*, versions figées et empreintes SRI : `maplibre-gl`, `pmtiles`,
  `fflate`), fond vectoriel OpenFreeMap avec repli sur un fond uni, panneau de
  filtres, liste des résultats.
- **Position** :
  - géolocalisation de l'appareil ;
  - point placé sur la carte : bouton « Placer sur la carte », puis un clic.
    Un clic sans ce bouton ne déplace pas le point ; on peut aussi le faire
    glisser ;
  - bouton « Rechercher ici », affiché sur la carte quand son centre
    s'éloigne du cercle de recherche : il place la position au centre de la
    carte. Le cercle ne suit jamais la carte d'elle-même ; il ne bouge que par
    l'un de ces gestes ;
  - saisie, dans un même champ, de « latitude, longitude » ou d'une adresse,
    avec suggestions pendant la frappe (géocodeur de l'IGN,
    [ADR 0010](adr/0010-geocodage-ign.md), fonctions pures dans
    `geocode.js`).
- **Données en tuiles vectorielles** ([ADR 0009](adr/0009-pmtiles-tippecanoe.md),
  [étape 2.2](phase-2/2.2-tuiles.md)) : le fichier PMTiles à l'adresse donnée
  par `data/segments.json` (sur Cloudflare R2, [ADR 0013](adr/0013-donnees-sur-r2.md)),
  lu par le
  protocole `pmtiles://` (requêtes partielles, sans serveur).
  - Couche `segments` (zooms 12 à 14), avec tous les attributs ; couche
    `overview` (zooms 8 à 11), allégée, pour la vue d'ensemble.
  - La **liste des résultats** est construite à partir des tuiles du zoom 12
    qui couvrent le cercle de recherche, autour de la position ou, à défaut,
    du centre de la carte au chargement (ou du segment ouvert par un lien). La distance maximale est de 10 km, soit au plus
    4 × 4 tuiles.
  - La **carte** montre en couleur vive les segments de la liste, et trace le
    cercle de recherche en pointillés. Ses filtres sont des expressions
    MapLibre (`mapFilter`) : les critères, qui sélectionnent exactement ce que
    sélectionne `matches`, et les identifiants de la liste, qui portent la
    limite de distance (une expression ne sait pas mesurer la distance à une
    ligne).
  - Sous ces couches, des couches de **contexte** (`context`,
    `overview-context`) montrent en trait fin et pâle tous les segments qui
    satisfont les critères, au-delà du cercle, avec les mêmes filtres mais
    sans les identifiants. Un clic sur l'un d'eux ouvre sa fiche, sans
    distance.
- `data/segments.json` : attribution, date, paramètres, emprise et nombres du
  jeu publié. `data/ids/XX.json` : position de chaque segment, pour les liens
  directs.
- `data/sample/` : le même trio pour le jeu d'exemple fictif, utilisé quand
  `data/segments.json` manque.
- `filters.js` : module ES **pur** (distances, filtrage, expressions de
  filtre, tri, état de l'URL). `tiles.js` : module **pur** (décodeur de tuiles
  vectorielles, calcul des tuiles couvrant un cercle, recollage des morceaux
  d'un segment). Tous deux sont testés avec `node --test` (`web/tests/`, avec
  une tuile produite par tippecanoe en fixture ; `web/package.json` ne sert
  qu'aux tests).
- **Liens directs** : `?id=<segment>` ouvre un segment, `?lat=…&lon=…` fixe la
  position et `?kind=climb` affiche les côtes.
  - La position du segment est lue dans `data/ids/`, puis on lit les tuiles
    autour d'elle.
  - Un identifiant d'une version précédente ouvre le segment qui le remplace,
    ou montre l'endroit où était un segment disparu, et la page le signale
    ([ADR 0012](adr/0012-identifiants-stables.md)).
  - Le segment d'un lien reste affiché même s'il sort des filtres (une côte à
    2,97 % avec un minimum de 3 %, par exemple), jusqu'à la fermeture de sa
    fiche.
  - L'URL suit la sélection, ce qui sert à partager un segment et à la fiche
    de validation terrain.
- **Publication** : le workflow `.github/workflows/pages.yml` déploie `web/`
  (sans les tests) sur GitHub Pages à chaque modification sur `main`, avec
  les données de la release `data-latest` quand elle existe
  ([ADR 0011](adr/0011-production-github-actions.md)).

```mermaid
sequenceDiagram
    actor U as Coureur
    participant B as Navigateur (app.js)
    participant T as tiles.js / filters.js
    participant S as Hébergement statique
    participant G as Géocodeur IGN

    B->>S: GET data/segments.json
    alt 404
        B->>S: GET data/sample/segments.json
        B->>U: bandeau « données d'exemple »
    end
    B->>S: GET data/segments.pmtiles (plages d'octets : en-tête, tuiles visibles)
    U->>B: « Me localiser » / « Placer sur la carte » + clic / saisie lat,lon ou adresse
    opt adresse
        B->>G: recherche (suggestions)
        G-->>B: adresses et positions
    end
    U->>B: choisit type, longueur min, pente, distance max…
    B->>S: tuiles z12 couvrant le cercle de recherche (plages d'octets)
    B->>T: decodeTile, segmentsFromTiles, filterSegments
    T-->>B: segments retenus + distance
    B->>B: liste triée (distance ou score)
    B->>B: filtre de la carte (critères + identifiants de la liste), cercle
    U->>B: clic sur un segment
    B->>U: popup (longueur, pente, revêtement, traversées…)
```

Le site est hébergeable sur GitHub Pages tel quel (dossier `web/`), qui
répond aux requêtes partielles.

### 3.4 Future API (phase 3)

Facultative depuis l'[ADR 0008](adr/0008-couverture-nationale-precalcul-statique.md) :
la France est couverte par précalcul statique. L'API ne servirait qu'à ce que
le statique ne sait pas faire (étranger, fraîcheur des données, critères
personnalisés, retours des utilisateurs) :

```mermaid
flowchart LR
    subgraph Batch["Pipeline (même code)"]
        P[flat_segments]
    end
    subgraph Serveur
        API["API FastAPI<br/>/segments?lat&lon&radius&kind…<br/>/tiles/{z}/{x}/{y}"]
        DB[("PostGIS<br/>segments, strokes")]
        Q["Worker<br/>calcul à la demande"]
    end
    W[Front web]

    P -->|chargement| DB
    W -->|HTTP JSON / MVT| API --> DB
    API -->|zone non couverte| Q --> P
    Q --> DB
```

- La table PostGIS `segments` reprend le schéma de
  [`data-model.md`](data-model.md). Les requêtes de proximité utilisent
  `ST_DWithin` sur un index GiST.
- Les modules purs du pipeline sont réutilisés tels quels. Seule la couche
  d'E/S change (lecture depuis PostGIS, écriture en base).
- Le front garde la même logique d'affichage ; seule la source des données
  change (API plutôt que fichier statique).

## 4. Flux de données détaillé

```mermaid
flowchart TD
    A["PBF OSM (WGS84)"] -->|"pyosmium : ways highway=*<br/>+ coordonnées des nœuds"| B["Ways classées<br/>PATH / MINOR / MAJOR"]
    B -->|"reprojection EPSG:2154"| C["Ways en Lambert-93"]
    C -->|"découpage aux nœuds partagés<br/>chaînage par continuité"| D["Strokes<br/>+ tronçons OSM + événements"]
    D -->|"rééchantillonnage tous les 5 m"| E["Points (x, y)"]
    F["MNT LiDAR HD, 2 m"] -->|bilinéaire| G["z brut"]
    E --> G
    G -->|"ponts/tunnels : interpolation<br/>trous courts : interpolation<br/>médiane + gaussienne"| H["Profil lissé + pente locale"]
    H -->|"fenêtre glissante<br/>critères plat / côte"| I["Fenêtres valides"]
    I -->|"union → tronçons maximaux"| J["Segments candidats"]
    J -->|"attributs, score"| K["Segments"]
    K -->|"déduplication inter-strokes"| L["segments.parquet"]
    L -->|"EPSG:4326, arrondi 1e-6°"| M["segments.geojson"]
```

## 5. Phases

| Phase | Contenu | Données | Livrable |
|---|---|---|---|
| **0 — Squelette** *(terminée)* | Documents d'architecture, logique pure testée, E/S testées sur fichiers synthétiques, front sur données fictives, CI | synthétiques | ce dépôt |
| **1 — Prototype pilote** *(terminée)* | Téléchargements automatisés, configuration TOML, outils de calibrage, liens directs et déploiement Pages, exécution sur les vraies données, calibrage des seuils, publication, validation terrain (Labège / Caraman : 15 segments conformes sur 18, aucune erreur de pente ni de traversée) | OSM + MNT LiDAR HD de la zone pilote | site statique en ligne |
| **2 — Couverture nationale par précalcul** *(à venir)* | Précalcul par département, export PMTiles, front sur tuiles ; d'abord l'ex-Midi-Pyrénées sur GitHub Pages, puis la France sur un stockage d'objets ([ADR 0008](adr/0008-couverture-nationale-precalcul-statique.md)) | OSM + MNT LiDAR HD (RGE ALTI où il manque), par département | site statique + PMTiles |
| **3 — API** *(facultative)* | Seulement pour ce que le statique ne sait pas faire : étranger (MNT 30 m), mises à jour OSM au fil de l'eau, critères personnalisés, retours des utilisateurs | multi-sources | API + front |

Critère de passage de la phase 1 à la phase 2 : sur un échantillon de segments
vérifiés à pied, la précision est jugée suffisante (segments annoncés plats
réellement plats, traversées correctement comptées). Il est rempli depuis la
[campagne 1](validation/README.md#campagne-1--zone-pilote-octobre-2026).

### 5.1 Plan de la phase 2

Objectif : toute la France, réponse instantanée, hébergement statique
([ADR 0008](adr/0008-couverture-nationale-precalcul-statique.md)). Les
ordres de grandeur ci-dessous sont extrapolés de la zone pilote et seront
recalés à l'échelle régionale.

| Étape | Contenu | Point à vérifier |
|---|---|---|
| **2.0 Mesures sur le pilote** *(faite, [rapport](phase-2/2.0-mesures.md))* | MNT au pas de 2 m : 98 à 99 % des km retrouvés, les 20 segments de la fiche terrain inchangés, téléchargement 3,5 fois plus rapide en dalles de 4 km. Outil PMTiles : tippecanoe ([ADR 0009](adr/0009-pmtiles-tippecanoe.md)) | Pas de 2 m adopté (amendement de l'ADR 0007) |
| **2.1 Pipeline par département** *(faite, [rapport](phase-2/2.1-departement.md))* | Commandes `department` et `departments` : contour Admin Express élargi de 2 km, MNT limité aux dalles utiles, rattachement au département qui contient le milieu, reprise. Haute-Garonne : 31 min 31 s, 45 341 segments, résultats identiques à ceux du pilote | Volume des PMTiles régionaux par rapport à la limite de 100 Mo de GitHub (étape 2.3) |
| **2.2 PMTiles et front sur tuiles** *(faite, [rapport](phase-2/2.2-tuiles.md))* | `export-pmtiles` (détail z12–14, vue d'ensemble z8–11, index des identifiants) ; front sur tuiles (décodeur MVT, liste à partir des tuiles z12, liens directs par index) ; Haute-Garonne publiée | Service par GitHub Pages vérifié (les navigateurs ne demandent pas de gzip sur les requêtes partielles) |
| **2.3 Ex-Midi-Pyrénées** *(fusionnée dans 2.4, en ligne)* | 8 départements (09, 12, 31, 32, 46, 65, 81, 82) publiés sur GitHub Pages : 48 444 plats et 301 601 côtes. Reste la campagne de validation 2 en zones rurales et en montagne | 155 Mo de PMTiles ; couverture LiDAR HD complète sur les 8 départements |
| **2.4 Production automatisée** *(faite, [rapport](phase-2/2.4-production.md))* | Workflow GitHub Actions ([ADR 0011](adr/0011-production-github-actions.md)) : renumérotation et découpe OSM, une tâche par département, assemblage, publication dans la release `data-latest` déployée par Pages, données hors de Git. Identifiants conservés d'une version à l'autre ([ADR 0012](adr/0012-identifiants-stables.md)). Ex-Midi-Pyrénées en 57 min | Débit du service de l'IGN variable (1,3 à 5,3 s par dalle) ; régénération à la main |
| **2.5 France métropolitaine** *(en ligne, [rapport](phase-2/2.5-france.md))* | Publication sur Cloudflare R2 ([ADR 0013](adr/0013-donnees-sur-r2.md)) ; essai sur l'Occitanie ; assemblage département par département (mémoire bornée) ; 96 départements en 3 h 25 ; repli sur le RGE ALTI là où le LiDAR HD manque (7,5 % du territoire, 27 départements relancés) : 1 050 675 plats et 3 113 675 côtes, 1,81 Go de PMTiles | Qualité moindre sur RGE ALTI (8 % des km de plats manqués sur la zone pilote) ; latence de `r2.dev` (domaine à soi à prévoir) |

## 6. Qualité et outillage

- **Tests** : pytest sur des profils et des réseaux synthétiques (pente
  constante, bosse, bruit, zigzag, pont…) ; `node --test` pour le filtrage JS.
- **Lint et types** : ruff (lint + format), mypy en mode strict.
- **pre-commit** : ruff, mypy, hygiène des fichiers, refus des fichiers de plus
  de 1 Mo (aucune donnée volumineuse dans Git). Le jeu publié n'est plus dans
  le dépôt : il est dans la release `data-latest`
  ([ADR 0011](adr/0011-production-github-actions.md)).
- **CI GitHub Actions** : lint, types, tests Python (3.12 et 3.13, avec
  tippecanoe et osmium), tests JS ; déploiement GitHub Pages du front depuis
  `main`.
- **Production** : workflow `produce.yml`, lancé à la main
  ([ADR 0011](adr/0011-production-github-actions.md)).
- **Réseau** : les téléchargements passent par une fonction d'ouverture d'URL
  injectable, ce qui permet de tout tester hors ligne (faux serveur WMS).

## 7. Pièges connus

| Piège | Effet | Parade |
|---|---|---|
| Ponts, passerelles | Le MNT donne l'altitude du sol **sous** l'ouvrage (rivière, route) : fausse descente puis remontée | Tags `bridge=*` : altitude interpolée linéairement entre les culées, segment marqué `bridge_interpolated` |
| Tunnels, passages souterrains | Le MNT donne l'altitude **au-dessus** : fausse bosse | Tags `tunnel=*` / `covered=yes` : même interpolation, marquage `tunnel_interpolated` |
| Talus, remblais, digues | Un décalage de la géométrie OSM de 2 à 5 m fait tomber les points sur le flanc du talus : bruit de profil | Lissage ; échantillonnage transversal (médiane sur ±2 m) en option ; `layer` et `embankment` notés |
| Qualité variable du MNT | RGE ALTI : précision décimétrique en zone LiDAR, métrique en zone de corrélation, et résolution effective d'environ 4 m par le WMS | MNT LiDAR HD par défaut ([ADR 0007](adr/0007-altitude-lidar-hd.md)) ; source du MNT publiée par segment (`elevation_source`) |
| Qualité OSM variable | Revêtement ou éclairage souvent absents ; traversées non modélisées si les voies ne partagent pas de nœud | Valeur `unknown` explicite ; validation terrain en phase 1 |
| Trottoirs cartographiés en double | Un trottoir `footway=sidewalk` et sa rue donnent deux segments quasi identiques | Déduplication géométrique (§ 9 de l'algorithme) |
| Routes ≥ `tertiary` avec trottoir non cartographié séparément | Tronçon ignoré, alors qu'il serait praticable | Limite assumée au prototype |
| Voies de service dans les parkings | Une voie `highway=service` sans `service=parking_aisle` qui traverse un parking donne un segment non praticable, et le cheminement piéton qui la longe passe pour un doublon (validation terrain, campagne 1) | Limite connue. Piste : exclure les voies `service` situées dans une zone `amenity=parking` |
| Données figées | Chantier récent, nouvelle voie verte… | Date de l'extrait OSM et du MNT dans les métadonnées de l'export |
