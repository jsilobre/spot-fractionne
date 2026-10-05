# 0014 — Catégorie Boucles, en commençant par les pistes d'athlétisme

- **Statut** : Acceptée
- **Date** : 2026-10-05

## Contexte

Le site propose des plats et des côtes : des morceaux de voie qu'on parcourt
en aller-retour. Pour enchaîner des tours sans demi-tour, il manque les
boucles courtes (un tour de 400 m, une boucle de 1 km).

- Une piste d'athlétisme est la boucle la plus évidente, mais elle est
  cartographiée en `leisure=track`, sans `highway` : la lecture des voies
  l'ignore.
- Les boucles du réseau (tour de parc, de lac) demandent une détection à
  part : un stroke s'arrête aux virages francs qu'une boucle traverse
  forcément.
- Une piste n'a ni pente à mesurer ni sous-tronçon à chercher. Son accès,
  souvent réservé aux clubs ou aux écoles, compte davantage.

## Décision

Les boucles forment une troisième catégorie, `kind = "loop"`, avec un
sous-type `loop_type`. La première étape ne couvre que les pistes
d'athlétisme d'OSM (`loop_type = "track"`).

- Une étape `loops` à part, sans strokes ni MNT : lecture des surfaces OSM,
  puis logique pure dans `loops.py` ([`algorithm.md` § 15](../algorithm.md#15-boucles--pistes-dathlétisme)).
- Une table `loops` à part (dataclass `Loop`), sans les champs d'altitude et
  de pente de `segments`.
- Un identifiant `loop-…` fondé sur le centroïde et la longueur, puisqu'une
  boucle n'a pas d'extrémités.
- Pas de score : toutes les pistes se valent. L'accès est affiché tel quel,
  y compris inconnu, et une piste couverte est gardée avec une mention.
- Les boucles du réseau viendront plus tard dans la même catégorie
  (`loop_type = "circuit"`).

## Conséquences

- Les boucles sont publiées dans la couche `segments` des tuiles, avec leurs
  propres attributs : `export-pmtiles` prend le `loops.parquet` voisin de
  chaque fichier de segments, et le rapprochement des identifiants
  (ADR 0012) vaut pour elles aussi. Le site les affichera dans une étape
  suivante.
- L'étape `loops` demande l'extrait OSM complet : l'extrait pilote découpé
  ne garde que les voies.
- Un département déjà calculé ne refait que cette étape : les paramètres de
  détection n'ont pas changé.
- L'accès sera souvent inconnu : c'est la limite principale de la source.

## Alternatives considérées

- **Ranger les pistes dans les plats** : elles n'ont ni sous-tronçons de
  200 m / 400 m / 1 km, ni traversées, ni sinuosité qui aient un sens, et il
  aurait fallu les déplacer à l'arrivée des boucles du réseau.
- **Les lire comme des voies** (`highway`) : elles seraient découpées en
  morceaux par la détection, jamais proposées comme un tour entier.
- **Commencer par les boucles du réseau** : plus utile à terme, mais une
  détection nouvelle et plus risquée ; les pistes valident d'abord la
  catégorie de bout en bout.
