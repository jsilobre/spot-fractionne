# Spot Fractionné

**Trouver, près de chez soi, les bouts de chemin parfaits pour une séance de course.**

**Spot Fractionné** (pipeline `flat-segments`) repère des tronçons courts exploitables pour l'entraînement
autour d'une position (adresse, coordonnées GPS, point placé sur la carte ou
position de l'appareil) :

1. **Segments plats et courts** pour le fractionné et le travail d'allure :
   longueurs cibles 200 m, 400 m et 1 km (paramétrables), pente maximale faible.
2. **Côtes** pour le travail en montée : longueur et pente moyenne cibles.
3. **Boucles** pour enchaîner des tours sans demi-tour : les pistes
   d'athlétisme d'OpenStreetMap (tour de 200 à 400 m, accès indiqué) et les
   boucles du réseau peu pentues (pente locale max réglable), de 200 m à 2 km (tour de parc, de lac, boucle de
   quartier sans voitures).

Contrairement à Strava ou Komoot, on ne cherche pas de parcours, mais des
**tronçons** courts : une ligne droite plate de 400 m sans traversée de route, ou une
montée régulière à 6 % sur 200 m.

## Ce qui fait un bon segment

| Critère | Plat | Côte |
|---|---|---|
| Longueur | ≥ longueur cible (200 / 400 / 1000 m) | ≥ longueur cible (100 à 800 m) |
| Pente | pente locale max faible (2 %), pente moyenne ≤ 1 % | pente moyenne dans une plage (3 à 15 %), sans replat |
| Traversées | aucune route importante, peu de routes secondaires | idem |
| Tracé | droit ou peu sinueux | sinuosité tolérée plus forte (lacets) |
| Revêtement / éclairage | asphalte, stabilisé, terre… ; éclairage si connu | idem |
| Accès | praticable en aller-retour (pas d'accès privé, pas de sens unique piéton) | idem |
| Proximité | distance à la position de l'utilisateur | idem |

Le détail des calculs est dans [`docs/algorithm.md`](docs/algorithm.md).

## Statut

✅ **Phase 1 terminée : prototype sur la zone pilote.**

Déjà en place :
- **Pipeline complet**, testé sur des données synthétiques puis lancé sur la
  zone pilote :
  - lecture OSM avec pyosmium et découpe de l'extrait ;
  - échantillonnage du MNT avec rasterio ;
  - détection, score et déduplication ;
  - export GeoParquet, GeoJSON et tuiles vectorielles (PMTiles).
- **Téléchargements automatisés** : extrait OSM (Geofabrik ou miroir) et dalles
  du MNT LiDAR HD via la Géoplateforme.
- **Outils de calibrage** : rapport, balayage de paramètres, profil d'un segment,
  fiche de validation terrain.
- **Front statique** :
  - liens directs vers un segment ;
  - déploiement GitHub Pages par workflow.
- **Segments réels de la zone pilote**, publiés en phase 1 : 778 plats et 2266
  côtes, tirés de l'extrait OSM du 29/09/2026 et du MNT LiDAR HD de l'IGN (pas
  de 2 m), avec des seuils calibrés sur ces données
  ([`algorithm.md` § 11](docs/algorithm.md#11-déduplication-inter-strokes)).
- **Validation terrain** ([fiche](docs/validation/pilot.md),
  [bilan](docs/validation/README.md#campagne-1--zone-pilote-octobre-2026)) :
  15 segments conformes sur 18 vérifiés, aucune erreur de pente ni de
  traversée.

🚧 **Phase 2 en cours : couvrir toute la France par précalcul statique.** Voir
l'[ADR 0008](docs/adr/0008-couverture-nationale-precalcul-statique.md) et le
[plan](docs/architecture.md#51-plan-de-la-phase-2).

- **Production par département**, avec reprise après interruption
  ([2.1](docs/phase-2/2.1-departement.md)).
- **Site sur tuiles vectorielles** (PMTiles) : publication de la
  **Haute-Garonne**, soit 14 532 plats et 30 809 côtes
  ([2.2](docs/phase-2/2.2-tuiles.md)).
- **Production automatisée** sur GitHub Actions, et identifiants conservés
  d'une version publiée à l'autre ([2.4](docs/phase-2/2.4-production.md)).
- **Ex-Midi-Pyrénées en ligne** : 8 départements, 48 444 plats et 301 601
  côtes, calculés en 57 min.
- **France métropolitaine en ligne** : 1 050 675 plats et 3 113 675 côtes,
  publiés sur Cloudflare R2 ([2.5](docs/phase-2/2.5-france.md)). Là où
  l'IGN n'a pas encore publié le MNT LiDAR HD (7,5 % du territoire, dont
  Lille), la production passe sur le RGE ALTI.

Zone pilote : **Labège / Caraman** (sud-est de Toulouse, Haute-Garonne),
emprise `1.48,43.48,1.80,43.59` (lon/lat WGS84).

## Démarrage rapide

Prérequis : [uv](https://docs.astral.sh/uv/) (installe Python ≥ 3.12 si besoin)
et, pour les tests du front, Node.js ≥ 20.

```bash
# Environnement et outils de développement
uv sync
uv run pre-commit install

# Qualité
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest

# CLI du pipeline
uv run flat-segments --help
```

### Pipeline

Les données brutes et intermédiaires vont dans `data/` (ignoré par Git). Voir
[`docs/data-sources.md`](docs/data-sources.md) pour le détail des sources.

```bash
uv run flat-segments download-osm   # extrait Geofabrik (MD5) + découpe → data/raw/pilot.osm.pbf
uv run flat-segments download-dem   # dalles MNT LiDAR HD de l'emprise → data/raw/dem/pilot.vrt
uv run flat-segments pipeline       # extract + elevation + detect + export → data/processed/
uv run flat-segments export-pmtiles # publication pour le site → web/data/segments.pmtiles (tippecanoe)
```

`export-pmtiles` demande [tippecanoe](https://github.com/felt/tippecanoe)
en version 2.55 ou plus (`brew install tippecanoe`, ou compilation depuis les
sources : le paquet Ubuntu, en 2.49, mélange des attributs, voir
[ADR 0009](docs/adr/0009-pmtiles-tippecanoe.md)). Il accepte plusieurs
fichiers de segments, par exemple ceux de plusieurs départements :
`export-pmtiles data/departments/*/segments.parquet`.

Production par département (phase 2) : le traitement reprend après une
interruption, et le MNT est supprimé une fois utilisé.

```bash
uv run flat-segments download-departments                      # contours (Admin Express)
uv run flat-segments department 31 --pbf data/raw/midi_pyrenees.osm.pbf
uv run flat-segments departments 09 31 82 --pbf data/raw/midi_pyrenees.osm.pbf
```

En production, le workflow `produce.yml` (lancé à la main sur GitHub) fait
tout cela en parallèle, un département par tâche, puis publie le jeu
([ADR 0011](docs/adr/0011-production-github-actions.md)). En local, ses
étapes sont :

```bash
uv run flat-segments cut-osm data/raw/france-latest.osm.pbf 31 81   # un extrait par département (osmium)
uv run flat-segments department 31 --pbf data/osm/31.osm.pbf
uv run flat-segments export-pmtiles data/departments/*/segments.parquet --previous web/data
```

`--previous` garde les identifiants du jeu déjà publié : les liens partagés
continuent de fonctionner ([ADR 0012](docs/adr/0012-identifiants-stables.md)).

Si Geofabrik est inaccessible, `download-osm --url` accepte le miroir
d'OpenStreetMap France (voir [`docs/data-sources.md`](docs/data-sources.md#téléchargement)).

Chaque étape existe aussi séparément : `extract`, `elevation`, `detect` et
`export`. Toutes acceptent un fichier de paramètres et des surcharges ponctuelles :

```bash
uv run flat-segments config > mes-parametres.toml          # paramètres effectifs (TOML)
uv run flat-segments detect --config mes-parametres.toml --set flat.max_local_grade_pct=1.5
```

Les valeurs par défaut sont dans [`configs/default.toml`](configs/default.toml).
Elles sont documentées dans [`docs/algorithm.md`](docs/algorithm.md#13-récapitulatif-des-paramètres).

### Calibrage

```bash
uv run flat-segments report                                   # résumé des segments détectés
uv run flat-segments sweep flat.max_local_grade_pct 1 1.5 2   # sensibilité à un paramètre
uv run flat-segments compare a.parquet b.parquet              # recouvrement de deux jeux de segments
uv run flat-segments inspect flat-3fa2b1c9d0e4                # profil d'un segment (PNG, matplotlib)
uv run flat-segments validation-sheet --count 20              # fiche terrain → docs/validation/pilot.md
```

`inspect` utilise matplotlib : il est installé par `uv sync` (outils de
développement) ou avec l'extra `viz` (`uv sync --extra viz`).

### Front

Site statique, sans étape de build :

```bash
npx http-server web -p 8000 -c-1
# puis ouvrir http://localhost:8000
```

Il faut un serveur qui accepte les requêtes partielles (`Range`), ce que ne
fait pas `python -m http.server`. La page lit les tuiles
`web/data/segments.pmtiles` ; si `web/data/segments.json` manque, elle prend
le jeu d'exemple `web/data/sample/` (données fictives, signalées par un
bandeau).

Le jeu publié n'est pas dans le dépôt
([ADR 0011](docs/adr/0011-production-github-actions.md)). Pour l'avoir en
local :

```bash
gh release download data-latest --dir web/data && tar -xzf web/data/ids.tar.gz -C web/data
```

Liens directs : `?id=<segment>` ouvre un segment, `?lat=…&lon=…` fixe la
position, `?kind=climb` affiche les côtes et `?kind=loop` les boucles. Le workflow *Pages* publie `web/` à
chaque modification sur `main`. Il faut d'abord activer GitHub Pages dans
*Settings → Pages → Source : GitHub Actions*.

Régénérer le jeu d'exemple (tippecanoe requis) : `uv run python scripts/make_sample_data.py`.

Tests du filtrage et du décodage des tuiles côté navigateur : `cd web && node --test`.

## Organisation du dépôt

```
docs/                 documentation (architecture, algorithme, données, ADR, validation)
configs/              paramètres par défaut (TOML)
src/flat_segments/    pipeline Python (package)
tests/                tests pytest (profils et réseaux synthétiques)
scripts/              scripts utilitaires (jeu d'exemple)
web/                  front statique MapLibre
data/                 données téléchargées et produites (non versionné)
```

## Documentation

- [Architecture](docs/architecture.md) : composants, flux de données, phases
- [Algorithme](docs/algorithm.md) : détection des plats et des côtes, paramètres
- [Sources de données](docs/data-sources.md) : OSM, MNT LiDAR HD et RGE ALTI, projections, licences
- [Modèle de données](docs/data-model.md) : schéma d'un segment
- [Décisions d'architecture (ADR)](docs/adr/README.md)

## Licences et attributions

- **Code** : licence MIT, voir [`LICENSE`](LICENSE).
- **Segments produits** : ils sont dérivés d'OpenStreetMap et constituent une
  base de données dérivée, diffusée sous
  [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
  © les contributeurs d'OpenStreetMap.
- **Altitudes** : IGN – MNT LiDAR HD (RGE ALTI® en repli), [Licence Ouverte 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence/).
- **Fonds de carte** : [OpenFreeMap](https://openfreemap.org/) (Plan par défaut, et fond Course
  dessiné par le site sur les mêmes tuiles), données © OpenStreetMap ;
  Plan IGN et photographies aériennes de la [Géoplateforme IGN](https://geoservices.ign.fr/)
  ([Licence Ouverte 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence/)) ;
  [OpenTopoMap](https://opentopomap.org/) (CC-BY-SA).
- **Recherche d'adresse** : service de géocodage de la Géoplateforme IGN
  (Base Adresse Nationale, [Licence Ouverte 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence/)).

Ces mentions, sauf la dernière, sont aussi affichées sur la carte du site.
