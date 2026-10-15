# 0017 — Site hébergé sur Cloudflare, à l'adresse spot-fractionne.fr

- **Statut** : Proposée
- **Date** : 2026-10-15

## Contexte

Le site était servi par GitHub Pages, à l'adresse
`https://jsilobre.github.io/spot-fractionne/`. Les conditions de GitHub Pages
interdisent d'en faire un site commercial, ce qui ferme la porte à la plupart
des pistes de financement (publicité, abonnement). Les données sont déjà chez
Cloudflare, sur R2 ([ADR 0013](0013-donnees-sur-r2.md)). Une adresse qui ne
dépend pas de l'hébergeur est aussi nécessaire pour publier l'appli sur le
Play Store (`assetlinks.json` à la racine du domaine).

Des liens vers l'ancienne adresse circulent déjà (posts Reddit et LinkedIn,
segments partagés avec `?id=`), et des téléphones ont installé l'appli
depuis cette adresse.

## Décision

- Le site a son propre domaine, **`spot-fractionne.fr`**, géré dans
  Cloudflare.
- Il est servi par **Cloudflare Workers, en fichiers statiques seulement**
  (`wrangler.jsonc`, sans code côté serveur). Le workflow *Pages* assemble le
  site comme avant (liste de fichiers explicite, tests, `segments.json` de
  R2) puis le déploie avec `wrangler`, quand la variable
  `CLOUDFLARE_ACCOUNT_ID` et le secret `CLOUDFLARE_API_TOKEN` existent.
  - Offre gratuite : fichiers statiques servis sans limite de requêtes ni de
    bande passante.
  - Workers plutôt que Cloudflare Pages : Cloudflare oriente les nouveaux
    projets vers Workers, et les deux se valent pour un site statique.
- **L'ancienne adresse redirige.** GitHub Pages ne sait pas faire de
  redirection côté serveur : quand la variable `SITE_URL` est définie, le
  workflow publie sur GitHub Pages, à la place du site, une page qui renvoie
  vers `SITE_URL` en gardant les paramètres (`?id=`, `?lat=&lon=`,
  `?kind=`). La même page sert de `404.html`. Un `sw.js` de remplacement vide
  le cache de l'ancienne appli et se désinstalle.
- Le contrôle préalable de la production vérifie le CORS de R2 pour les deux
  origines.

## Conséquences

- Bascule en deux temps, réversible : le site est d'abord publié sur les deux
  adresses ; une fois la nouvelle vérifiée, `SITE_URL` active la
  redirection. Supprimer la variable rétablit l'ancien site.
- La redirection est faite en JavaScript, pas en `301` : Google la suit, mais
  le référencement migre plus lentement.
- Ceux qui ont installé l'appli depuis l'ancienne adresse sont redirigés et
  doivent la réinstaller depuis la nouvelle.
- Le fond de carte mémorisé (stockage local, propre à chaque adresse) revient
  une fois au fond par défaut.
- Workers limite chaque fichier à 25 Mio : sans R2, le repli sur la release
  `data-latest` (tuiles copiées dans le site) ne passerait plus.
- Le dépôt GitHub doit garder GitHub Pages actif pour que les anciens liens
  restent valides.
- `www.spot-fractionne.fr` est redirigé vers le domaine nu par une règle de
  redirection Cloudflare (réglage du compte, pas du dépôt).

## Alternatives considérées

- **Rester sur GitHub Pages** : gratuit et sans changement, mais pas
  d'usage commercial possible et pas d'adresse propre pour le Play Store.
- **Adresse `*.pages.dev` / `*.workers.dev`** : gratuite, mais l'adresse
  aurait changé une deuxième fois le jour d'un domaine propre.
- **Domaine personnalisé sur GitHub Pages** : règle la question de l'adresse,
  pas celle de l'usage commercial.
