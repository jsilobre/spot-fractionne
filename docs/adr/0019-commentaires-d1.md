# 0019 — Commentaires des visiteurs, stockés dans Cloudflare D1

- **Statut** : Proposée
- **Date** : 2026-10-16

## Contexte

Des coureurs demandent à pouvoir laisser un commentaire sur un spot : accès,
état du sol après la pluie, éclairage, fréquentation, point d'eau. Ce que
les données OSM ne disent pas, ou disent mal. Le site est entièrement
statique ([ADR 0005](0005-front-statique-maplibre.md)) et servi par
Cloudflare Workers ([ADR 0017](0017-hebergement-cloudflare.md)) : il
n'existe ni serveur, ni base, ni comptes. La plupart des coureurs n'ont pas
de compte GitHub.

## Décision

- Un **petit Worker** (`worker/index.js`, logique dans `worker/comments.js`)
  est ajouté au déploiement Cloudflare existant (`wrangler.jsonc`, `main`).
  Les fichiers statiques restent servis en premier ; le Worker ne répond
  qu'à ce qu'aucun fichier ne couvre, c'est-à-dire `/api/`.
- Les commentaires sont rangés dans une base **Cloudflare D1** (SQLite géré,
  offre gratuite), une table `comments` (`worker/migrations/`, appliquées
  par le workflow *Pages* avant chaque déploiement) : identifiant du spot,
  pseudo, texte, date, état, nombre de signalements. **Ni compte, ni adresse
  IP, ni position.**
- **Rattachement au spot** par son identifiant publié (`?id=`), stable d'une
  version à l'autre ([ADR 0012](0012-identifiants-stables.md)).
- **Publication immédiate**, puis modération a posteriori :
  - anti-robot **Cloudflare Turnstile** (invisible le plus souvent) pour
    publier et pour signaler ; clé publique dans `wrangler.jsonc`
    (`TURNSTILE_SITEKEY`), clé secrète en secret du Worker
    (`TURNSTILE_SECRET`), envoyée au déploiement depuis le secret GitHub du
    même nom ;
  - au plus 5 envois par minute et par adresse IP (limiteur de débit de
    Cloudflare : l'adresse sert de clé de compteur, elle n'est pas gardée) ;
  - pseudo de 40 caractères, texte de 3 à 1 000 caractères, au plus deux
    liens ; le texte est affiché tel quel, sans lien cliquable ni balise ;
  - bouton « Signaler » : un commentaire signalé 3 fois est masqué ;
  - page `moderation.html` (non indexée) pour voir les commentaires récents,
    signalés ou masqués, et les masquer, valider ou supprimer ; elle
    s'ouvre avec le secret du Worker `ADMIN_TOKEN` (secret GitHub
    `COMMENTS_ADMIN_TOKEN`). Un commentaire validé
    n'est plus masqué par les signalements.
- Dans la fiche du spot, une section repliable « Commentaires (n) » : la
  liste est chargée à chaque sélection (le titre montre le nombre), le
  formulaire et Turnstile seulement à l'ouverture. Le pseudo est retenu
  dans le navigateur. Fonctions pures dans `web/comments.js`.

## Conséquences

- Le site n'est plus seulement statique : un Worker et une base à garder en
  état. Tout reste dans l'offre gratuite de Cloudflare à ce volume (D1 :
  5 millions de lectures par jour ; Workers : 100 000 requêtes par jour,
  seules les requêtes `/api/` comptent).
- Sans le Worker (serveur local `http-server`, données d'exemple), la
  section affiche « Commentaires indisponibles ici » ; le reste de la page
  n'en dépend pas. Le service worker ne met jamais `/api/` en cache.
- **Modération** : en hébergeant les commentaires, l'éditeur du site doit
  retirer promptement un contenu manifestement illicite qu'on lui signale
  (LCEN). La page de modération et le masquage automatique y servent ; il
  faut la consulter de temps en temps.
- **Données personnelles** : un pseudo et un texte publics, choisis par le
  visiteur ; la page « À propos » le dit. Turnstile est un service de
  Cloudflare, déjà hébergeur du site.
- Un identifiant **redirigé** vers un nouveau segment (ADR 0012) ne
  transporte pas ses commentaires : ils restent attachés à l'ancien
  identifiant. C'est rare (un segment qui garde sa forme garde son
  identifiant) ; on pourra les déplacer depuis l'index `ids/` si besoin.
- Mise en place côté Cloudflare, une fois : créer la base D1 et reporter son
  identifiant dans `wrangler.jsonc`, ajouter la permission *D1 : Edit* au
  jeton d'API du workflow, créer le widget Turnstile, définir les secrets
  GitHub `TURNSTILE_SECRET` et `COMMENTS_ADMIN_TOKEN` (le workflow les envoie
  au Worker à chaque déploiement ; Cloudflare refuse les secrets sur un
  Worker qui n'a encore que des fichiers statiques).

## Alternatives considérées

- **Giscus / utterances** (commentaires dans GitHub Discussions ou Issues) :
  rien à héberger, mais un compte GitHub obligatoire pour écrire.
- **Disqus** : clé en main, mais publicités et pistage, contraires au choix
  d'un comptage de visites sans cookie.
- **Commentaires validés avant publication** : moins de risque, mais un
  commentaire attend la modération, et la modération devient une corvée
  quotidienne.
- **Avis à cocher sans texte libre** (« éclairé », « boueux »…) : presque
  rien à modérer et facile à filtrer, mais couvre moins de cas. Peut venir
  en plus.
