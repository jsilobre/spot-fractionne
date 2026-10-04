# Validation terrain — zone pilote

Générée le 2026-09-30 à partir de `data/processed/segments.parquet` (20 segments).

Pour chaque segment : ouvrir le lien **Carte**, aller sur place, puis remplir
**Verdict** avec un code ci-dessous. Ajouter une remarque si besoin (pente
ressentie, traversée manquée, meilleur point de départ…).

| Code | Signification |
| --- | --- |
| `OK` | Conforme à la description |
| `PENTE` | La pente ne correspond pas (pas plat, côte trop ou pas assez raide) |
| `TRAVERSEE` | Traversée ou intersection non signalée, ou signalée à tort |
| `ACCES` | Inaccessible : privé, fermé ou dangereux |
| `SURFACE` | Revêtement ou éclairage faux |
| `GEOMETRIE` | Tracé faux, ou segment coupé au mauvais endroit |
| `DOUBLON` | Doublon d'un autre segment |
| `AUTRE` | Autre problème (préciser en remarque) |

| # | Id | Type | Nom | Longueur | Pente | Traversées | Revêtement | Carte | Verdict | Remarques |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `flat-641dc017c07b` | Plat | — | 335 m | max 0,3 % | 0 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=flat-641dc017c07b) | ACCES | PARKING |
| 2 | `flat-de19ff233b57` | Plat | — | 352 m | max 1,1 % | 0 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=flat-de19ff233b57) | DOUBLON | |
| 3 | `flat-07ec5519d3ea` | Plat | — | 299 m | max 1,2 % | 0 | terre | [voir](https://jsilobre.github.io/spot-fractionne/?id=flat-07ec5519d3ea) | OK | |
| 4 | `flat-74dbaf428176` | Plat | — | 693 m | max 1,1 % | 1 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=flat-74dbaf428176) | OK | |
| 5 | `flat-efcccfe7e4b4` | Plat | Chemin de la Gouffie | 288 m | max 1,8 % | 0 | terre | [voir](https://jsilobre.github.io/spot-fractionne/?id=flat-efcccfe7e4b4) | OK | |
| 6 | `flat-212d857016a5` | Plat | Chemin de Noubel | 699 m | max 1,9 % | 0 | terre | [voir](https://jsilobre.github.io/spot-fractionne/?id=flat-212d857016a5) | OK | |
| 7 | `flat-1dfdab7641e2` | Plat | Rue du Commerce | 311 m | max 0,9 % | 2 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=flat-1dfdab7641e2) | OK | |
| 8 | `flat-c9aed3911ee9` | Plat | Avenue Bernard Maris | 1403 m | max 1,1 % | 7 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=flat-c9aed3911ee9) | OK | |
| 9 | `flat-25ed9d3fb183` | Plat | — | 1217 m | max 1,7 % | 9 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=flat-25ed9d3fb183) | OK | |
| 10 | `flat-1d76b1b15a57` | Plat | Avenue de Sénaous | 198 m | max 1,8 % | 2 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=flat-1d76b1b15a57) | OK | |
| 11 | `climb-0e7cfa8982e5` | Côte | Allée Campferran | 119 m | moy. 3,0 % | 0 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=climb-0e7cfa8982e5) | AUTRE | lien KO |
| 12 | `climb-455136389d91` | Côte | — | 129 m | moy. 3,3 % | 0 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=climb-455136389d91) | OK | |
| 13 | `climb-6b5326e4cc22` | Côte | Chemin de Bordeneuve | 349 m | moy. 4,4 % | 0 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=climb-6b5326e4cc22) | OK | |
| 14 | `climb-9e90da37f40f` | Côte | — | 129 m | moy. 3,8 % | 0 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=climb-9e90da37f40f) | OK | |
| 15 | `climb-1d8cc908ca15` | Côte | Chemin de la Rivière | 445 m | moy. 4,7 % | 0 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=climb-1d8cc908ca15) | OK | |
| 16 | `climb-7483d47960e9` | Côte | Chemin des Cinq Coins | 1334 m | moy. 5,7 % | 1 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=climb-7483d47960e9) | OK | |
| 17 | `climb-9a4a6f0c091a` | Côte | — | 379 m | moy. 9,9 % | 0 | terre | [voir](https://jsilobre.github.io/spot-fractionne/?id=climb-9a4a6f0c091a) | OK | |
| 18 | `climb-eaae15bb1fd4` | Côte | Rue Edmond Casse | 99 m | moy. 3,9 % | 1 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=climb-eaae15bb1fd4) | AUTRE | lien KO |
| 19 | `climb-f522b19ad87b` | Côte | Allée de la Durante | 140 m | moy. 5,6 % | 1 | revêtu | [voir](https://jsilobre.github.io/spot-fractionne/?id=climb-f522b19ad87b) | OK | |
| 20 | `climb-35ee7ffc67d8` | Côte | — | 168 m | moy. 5,9 % | 1 | terre | [voir](https://jsilobre.github.io/spot-fractionne/?id=climb-35ee7ffc67d8) | ACCES | |
