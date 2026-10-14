# 0016 — Itinéraire à pied jusqu'au départ d'un segment, avec le service de l'IGN

- **Statut** : Acceptée
- **Date** : 2026-10-14

## Contexte

Une fois un segment choisi, le coureur veut savoir comment s'y rendre depuis
sa position (le repère sur la carte) : par où passer, à quelle distance est
le départ. Le front reste statique, sans serveur
([ADR 0005](0005-front-statique-maplibre.md)). Le service de calcul
d'itinéraire doit donc, comme le géocodeur ([ADR 0010](0010-geocodage-ign.md)) :

- répondre directement au navigateur (CORS ouvert) ;
- être gratuit, sans clé à cacher ;
- couvrir toute la France, avec un profil piéton.

## Décision

- L'itinéraire est calculé par le **service d'itinéraire de la Géoplateforme
  IGN** (`https://data.geopf.fr/navigation/itineraire`), ressource
  `bdtopo-valhalla` (réseau de la BD TOPO), profil `pedestrian`, trajet le
  plus court.
  - Vérifié le 14/10/2026 : gratuit, sans clé, CORS ouvert
    (`access-control-allow-origin: *`). Usage limité à 5 requêtes par seconde
    depuis une même adresse IP, d'après le
    [catalogue data.gouv.fr](https://www.data.gouv.fr/dataservices/api-geoplateforme-calcul-ditineraire) :
    largement assez pour une requête par clic.
  - Même fournisseur que le géocodeur et le relief.
- **À la demande** : la fiche d'un segment propose « Itinéraire depuis ma
  position » ; rien n'est envoyé tant qu'on ne clique pas. Le trajet est
  tracé sur la carte (pointillés de la couleur du repère), avec sa longueur
  et la durée de marche estimée par le service. Il s'efface quand la fiche se
  ferme ou que la position change.
- **Point d'arrivée** (`segmentStart`, dans `web/route.js`) :
  - côte et côte longue : le **bas**, premier point de la ligne (le pipeline
    oriente les côtes vers le haut) ;
  - plat : l'**extrémité la plus proche** de la position (il se court dans les
    deux sens) ;
  - boucle : son **point le plus proche** de la position (elle n'a pas de
    départ).
  - Le segment arrive en morceaux découpés par les tuiles : une extrémité de
    morceau posée sur un autre morceau est une coupure, pas une extrémité du
    segment.
- **Appli GPS** : un lien « Y aller avec une appli GPS » confie le trajet à
  l'appli du téléphone, qui part de la position réelle de l'appareil :
  - Android : une URI `geo:` (le système propose les applis installées :
    Google Maps, Organic Maps, OsmAnd, Waze…) ;
  - iPhone et iPad : Plans d'Apple, à pied ;
  - ordinateur : Google Maps, à pied.
- Les fonctions pures (URL, lecture de la réponse, point d'arrivée, lien GPS)
  sont dans `web/route.js`, testées avec `node --test` sur une réponse réelle
  enregistrée.

## Conséquences

- Au clic, la position et le point d'arrivée partent vers l'IGN. Le service
  est public et ne demande aucune identification.
- Le réseau piéton est celui de la BD TOPO de l'IGN, pas celui
  d'OpenStreetMap qui sert aux segments : un sentier présent dans OSM peut
  manquer à l'itinéraire, et inversement.
- Si le service est indisponible, la fiche le dit ; le lien vers l'appli GPS
  reste utilisable.
- Le lien GPS mène au point d'arrivée choisi pour la position, ou pour le
  centre de recherche quand il n'y en a pas.

## Alternatives considérées

- **Serveurs de démonstration OSRM** (`router.project-osrm.org`) : profil
  voiture seulement, et réservés aux essais par leur politique d'usage.
- **openrouteservice**, **GraphHopper** : clé obligatoire, visible dans un
  site statique, et quotas.
- **Lien GPS seul**, sans tracé : plus simple, mais on ne voit pas le trajet
  ni la distance sur la carte.
