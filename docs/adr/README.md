# Décisions d'architecture (ADR)

Chaque décision structurante fait l'objet d'un *Architecture Decision Record*
court. Pour revenir sur une décision, on ne réécrit pas l'ADR : on en crée un
nouveau qui la **remplace**, et on passe l'ancien au statut « Remplacée par
ADR NNNN ». Les corrections mineures (précision, lien) se font sur place.

| N° | Décision | Statut |
|---|---|---|
| [0001](0001-pipeline-python-uv.md) | Pipeline hors ligne en Python ≥ 3.12, géré avec uv | Acceptée |
| [0002](0002-reseau-osm-pyosmium.md) | Réseau issu d'OpenStreetMap (Geofabrik), lu avec pyosmium | Acceptée |
| [0003](0003-altitude-rge-alti-1m.md) | Altitude : RGE ALTI® 1 m de l'IGN | Remplacée par ADR 0007 |
| [0004](0004-stockage-geoparquet.md) | Stockage prototype en GeoParquet, PostGIS plus tard | Acceptée |
| [0005](0005-front-statique-maplibre.md) | Front statique MapLibre d'abord, API ensuite | Acceptée ; API revue par ADR 0008 |
| [0006](0006-strokes-et-troncons-maximaux.md) | Chaînage par continuité (*strokes*) et tronçons maximaux | Acceptée |
| [0007](0007-altitude-lidar-hd.md) | Altitude : MNT LiDAR HD de l'IGN, RGE ALTI® en repli | Acceptée |
| [0008](0008-couverture-nationale-precalcul-statique.md) | Couverture nationale par précalcul statique (PMTiles), API facultative | Acceptée |
| [0009](0009-pmtiles-tippecanoe.md) | Tuiles vectorielles PMTiles produites avec tippecanoe | Acceptée |
| [0010](0010-geocodage-ign.md) | Recherche d'adresse avec le géocodeur de l'IGN | Acceptée |
| [0011](0011-production-github-actions.md) | Production sur GitHub Actions, données publiées hors de Git | Acceptée |
| [0012](0012-identifiants-stables.md) | Identifiants conservés d'une version publiée à l'autre | Acceptée |
| [0013](0013-donnees-sur-r2.md) | Données publiées sur Cloudflare R2 | Acceptée |
| [0014](0014-categorie-boucles.md) | Catégorie Boucles, en commençant par les pistes d'athlétisme | Acceptée |
| [0015](0015-boucles-du-reseau.md) | Boucles du réseau : tours de parcs, de lacs et de quartiers | Acceptée |

## Gabarit

```markdown
# NNNN — Titre court

- **Statut** : Proposée | Acceptée | Remplacée par ADR NNNN
- **Date** : AAAA-MM-JJ

## Contexte
Le problème, les contraintes, ce qui force à décider.

## Décision
Ce qu'on fait, en une ou deux phrases affirmatives, puis les précisions.

## Conséquences
Ce que ça implique, en bien et en moins bien.

## Alternatives considérées
Les options écartées et pourquoi.
```
