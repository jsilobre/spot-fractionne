# 0015 — Boucles du réseau : tours de parcs, de lacs et de quartiers

- **Statut** : Acceptée
- **Date** : 2026-10-06

## Contexte

La catégorie Boucles ([ADR 0014](0014-categorie-boucles.md)) ne contient que
les pistes d'athlétisme, souvent fermées au public. Les coureurs tournent
surtout autour d'un parc ou d'un lac. Ces boucles ne sont pas des objets
OSM : il faut les trouver dans le réseau. Un essai sur la Haute-Garonne a
montré que les cellules du réseau (faces) ne suffisent pas : celles d'un
parc ou d'une forêt sont un dédale d'allées, et le tour du parc n'est pas
une cellule.

Choix de Jérémy (06/10/2026) : tours de parcs et de lacs et boucles de
quartier sans voitures, de 200 m à 2 km, plates seulement.

## Décision

- Un module pur `circuits.py` et une étape `circuits` à part, après
  `loops` ([`algorithm.md` § 16](../algorithm.md#16-boucles-du-réseau)).
- Deux sources : le contour des chemins de chaque parc ou plan d'eau (le
  tour), et les faces du réseau, gardées seulement autour de l'eau ou en
  quartier sans voitures.
- Plat : pente locale maximale sous `flat.max_local_grade_pct`, mesurée sur
  le MNT des dalles qui touchent les boucles.
- Une boucle par lieu et par classe de taille.
- Même table `loops` et même dataclass `Loop`, avec trois champs facultatifs
  (`setting`, `n_crossings`, `grade_max_pct`), dans un fichier à part
  (`circuits.parquet`).

## Conséquences

- L'étape lit de nouveau les voies de l'extrait (avec le tag `footway`, pour
  écarter les trottoirs) et les surfaces `leisure`, `landuse`, `natural`.
- Le MNT est gardé jusqu'à la fin de l'étape `circuits`. Un département
  déjà calculé, sans MNT, ne retélécharge que les dalles qui touchent ses
  boucles.
- Les seuils sont des constantes : les paramètres enregistrés des
  départements ne changent pas.
- `export-pmtiles` publie le `circuits.parquet` voisin de chaque fichier de
  segments ; les identifiants restent stables comme ceux des pistes.

## Alternatives considérées

- **Toutes les faces du réseau** : 4 912 boucles en Haute-Garonne, surtout
  des cellules de forêt et de parc sans intérêt.
- **Des cycles quelconques du graphe** (pas seulement les faces) : leur
  nombre explose et la plupart se recouvrent ; le tri par lieu ne suffirait
  pas.
- **Mesurer le relief sur les profils des strokes** : une boucle mélange des
  morceaux de plusieurs strokes, coupés aux virages francs ; reprendre le MNT
  sur l'anneau est plus simple et plus juste.
