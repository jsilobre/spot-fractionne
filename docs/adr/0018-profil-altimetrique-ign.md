# 0018 — Profil altimétrique d'un segment, demandé au service d'altimétrie de l'IGN

- **Statut** : Proposée
- **Date** : 2026-10-15

## Contexte

Sur la fiche d'un segment, le coureur veut voir, s'il le souhaite, le profil
altimétrique : où la côte se raidit, si un plat descend un peu. Les données
publiées ne portent que l'altitude de départ et d'arrivée, le D+ / D- et les
pentes résumées ; les altitudes échantillonnées tous les 5 m restent dans
`profiles.parquet`, côté pipeline, par voie et non par segment.

## Décision

- Le profil est demandé **à la demande** au service d'altimétrie de la
  **Géoplateforme IGN** (`https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevationLine.json`),
  en POST (les sommets d'une longue boucle ne tiennent pas dans une URL) :
  - ressource `ign_lidar_hd_mnt_mono_wld` (MNT LiDAR HD à 50 cm, comme le
    pipeline), puis `ign_rge_alti_wld` (RGE ALTI) si plus de 20 % des points
    n'ont pas d'altitude ;
  - un point tous les 5 m, au plus 401 (le pas s'élargit au-delà de 2 km) ;
  - vérifié le 15/10/2026 : gratuit, sans clé, CORS ouvert.
- Le navigateur lisse les altitudes comme le pipeline (médiane glissante de
  15 m puis gaussienne de 10 m, `params.py`), puis dessine un petit graphique
  SVG : distance en abscisse, altitude en ordonnée (au moins 10 m d'écart pour
  qu'un plat reste plat), aire colorée par tranche de pente (< 2, 2–5, 5–8,
  8–12, > 12 %). Le doigt ou la souris sur le graphique affiche distance,
  altitude et pente locale, et place un point sur la carte.
- Le sens suit l'itinéraire ([ADR 0016](0016-itineraire-ign.md)) : une côte
  du bas vers le haut, un plat depuis l'extrémité la plus proche de la
  position, une boucle depuis son point le plus proche.
- Section « Profil altimétrique » repliable de la fiche, fermée par défaut,
  état retenu d'un segment à l'autre ; pas de profil pour une piste
  d'athlétisme. Fonctions pures dans `web/profile.js`, testées avec
  `node --test` sur une réponse réelle enregistrée.

## Conséquences

- Rien ne change dans le pipeline ni dans les données publiées : pas de
  production à relancer, et les tuiles ne grossissent pas.
- Seuls les sommets du segment partent vers l'IGN, pas la position.
- Le profil est celui du sol brut lissé, sans la correction des ponts et
  tunnels du pipeline : sur un pont, la courbe plonge vers le sol en dessous
  (la fiche le signale). Les altitudes peuvent différer de quelques
  décimètres de celles de la fiche.
- Si le service est indisponible, la fiche le dit et réessaie à la
  réouverture.

## Alternatives considérées

- **Publier les altitudes dans les tuiles** (un tableau compact par
  segment, depuis `profiles.parquet`) : profil identique à celui du calcul,
  ponts compris, et sans service tiers ; mais une production à relancer et
  des tuiles plus lourdes alors que la liste de résultats les lit toutes dans
  un rayon de 10 km. À reconsidérer si les ponts gênent.
- **Fichiers de profils à part** (comme l'index `ids/`) : pas de poids dans
  les tuiles, mais un format et une étape de publication de plus.
