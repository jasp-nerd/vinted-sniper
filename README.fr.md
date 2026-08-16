<div align="center">

[English](README.md) · **Français**

<img src="docs/media/banner.svg" alt="vinted-sniper — être prévenu dès qu'une annonce correspond" width="820">

[![CI](https://github.com/jasp-nerd/vinted-sniper/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/jasp-nerd/vinted-sniper/actions/workflows/ci.yml)
[![Image du conteneur](https://img.shields.io/badge/ghcr.io-vinted--sniper-2496ED?logo=docker&logoColor=white)](https://github.com/jasp-nerd/vinted-sniper/pkgs/container/vinted-sniper)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MIT](https://img.shields.io/badge/licence-MIT-green)](LICENSE)

Surveillez une recherche Vinted et recevez un message dès qu'une nouvelle annonce<br>
correspond — sur Telegram, Discord, ou ailleurs.

</div>

> La version anglaise fait référence. Cette traduction peut avoir un temps de retard.

Vous collez l'URL d'une recherche que vous avez déjà faite sur Vinted. L'outil vérifie cette
recherche environ une fois par minute et vous signale les annonces qui n'y étaient pas avant.
C'est tout.

<div align="center">
  <img src="docs/media/demo.gif" width="820" alt="Ajout d'une recherche en collant une URL Vinted, l'état de chaque recherche, et les annonces qui arrivent dans Discord et Telegram">
</div>

## Démarrage rapide

Il vous faut Docker, et rien d'autre. Jamais utilisé ? [Le guide
d'hébergement](docs/self-hosting.md) part de zéro, installation de Docker comprise, sur un
Raspberry Pi ou un vieux portable. Deux commandes :

```bash
curl -O https://raw.githubusercontent.com/jasp-nerd/vinted-sniper/main/docker-compose.yml
docker compose up -d
```

(Sous Windows PowerShell, écrivez `curl.exe` au lieu de `curl`, car `curl` y désigne autre
chose.)

Ouvrez ensuite **http://localhost:8000** et donnez-lui deux choses :

1. **Où envoyer les alertes.** Le plus simple est un webhook Discord : dans votre serveur,
   Paramètres → Intégrations → Webhooks → Nouveau webhook → Copier l'URL. Collez-la.
2. **Une recherche à surveiller.** Cherchez sur Vinted avec les filtres qui vous vont, puis
   copiez la barre d'adresse. Collez-la aussi.

C'est terminé. L'outil reste silencieux sur ce qui est déjà en ligne et vous écrit quand
quelque chose de nouveau apparaît. Pas de connexion, pas de fichier à éditer : le tableau de
bord n'écoute que sur votre propre machine. (Vous comptez l'exposer à d'autres machines ?
Définissez d'abord `VINTED_SNIPER_WEB_AUTH_TOKEN`, car il affiche vos URL de webhook.
[docs/configuration.md](docs/configuration.md) donne les détails.)

### Sans Docker

C'est une application Python ordinaire, si vous préférez la lancer directement. Il faut
Python 3.13+ et [uv](https://docs.astral.sh/uv/) :

```bash
git clone https://github.com/jasp-nerd/vinted-sniper.git && cd vinted-sniper
uv sync --extra web
uv run vinted-sniper run
```

Même tableau de bord, même adresse : http://localhost:8000.

### Si rien n'arrive

Une commande vérifie que Vinted répond depuis votre machine :

```bash
docker compose exec vinted-sniper vinted-sniper check --url "https://www.vinted.fr/catalog?search_text=nike"
```

Elle fait une vraie requête et affiche le résultat. Plus de pistes dans
[docs/troubleshooting.md](docs/troubleshooting.md).

## Ce qui change par rapport aux autres

**Le filtre de prix porte sur ce que vous payez vraiment.** Le filtre de Vinted s'applique au
prix demandé : une recherche plafonnée à 30 € vous montrera sans problème un article qui vous
coûtera 33 € une fois la protection acheteurs ajoutée. Ici, le plafond s'applique au total.

**Il vous prévient quand il ne fonctionne plus.** Vinted continue parfois à répondre
normalement tout en servant un catalogue qui ne se met plus à jour. Vu de l'extérieur, cela
ressemble exactement à une soirée calme. L'outil repère qu'une recherche se tait alors que vos
autres recherches sur le même site trouvent encore des choses, ouvre une nouvelle session et
vous le dit. Chaque recherche affiche sa dernière vérification réussie, sa dernière erreur, et
si elle semble bloquée.

**Il ne touche pas à votre compte.** Pas de connexion, pas de mot de passe, aucun cookie
récupéré dans votre navigateur. Il lit les annonces publiques comme le ferait un visiteur non
connecté. Vinted restreint les comptes soupçonnés d'automatisation : ici, il n'y a aucun
compte à restreindre. Cela veut aussi dire qu'il ne peut rien acheter à votre place, et c'est
volontaire.

**Vous pouvez construire la recherche sans quitter le tableau de bord.** Coller une URL
fonctionne, mais le tableau de bord sait aussi la composer avec vous : l'arbre de catégories
de Vinted, l'autocomplétion des marques et les mêmes filtres, assemblés en URL à votre place.

**Il reste silencieux au premier passage.** Une nouvelle recherche enregistre ce qui existe
déjà sans vous notifier quatre-vingt-seize articles que vous n'avez pas demandés.

## Où arrivent les notifications

| Canal | Mise en place |
|---|---|
| Discord | Collez une URL de webhook. Rien à inviter, rien à héberger. |
| Telegram | Créez un bot avec [@BotFather](https://t.me/BotFather), mettez son jeton dans `.env`, puis lancez `vinted-sniper pair-telegram` et touchez le lien affiché. L'identifiant de conversation est trouvé pour vous. |
| ntfy | Choisissez un nom de sujet, installez l'application. Sans compte. |
| Autre chose | Un simple POST JSON vers l'URL de votre choix — n8n, Home Assistant, un script. |

Chaque recherche peut viser ses propres destinations : un salon Discord pour l'une, votre
téléphone pour l'autre.

Un flux RSS par recherche est également disponible.

### La même annonce, telle qu'elle arrive dans chacun

<div align="center">
  <img src="docs/media/discord.png" width="820" alt="Une notification Discord : titre, prix protection acheteurs comprise, taille, marque, état, vendeur et photo, avec les boutons pour ouvrir l'annonce, contacter le vendeur ou acheter">
  <br><sub><b>Discord</b> — un message par annonce, regroupés en encarts quand plusieurs arrivent d'un coup</sub>
  <br><br>
  <img src="docs/media/telegram.png" width="820" alt="La même notification dans Telegram : aperçu photo, prix protection acheteurs comprise, marque, taille, état, vendeur et les trois mêmes boutons">
  <br><sub><b>Telegram</b> — la photo en aperçu, pour que le message garde ses boutons</sub>
</div>

## Récupérer l'URL de recherche

Ouvrez Vinted, cherchez ce que vous voulez, réglez les filtres — catégorie, taille, marque,
prix, état. Une fois les résultats affichés, copiez la barre d'adresse. C'est cette URL qu'il
faut fournir.

Ou évitez le copier-coller : **Build a search instead** dans le tableau de bord propose les
mêmes filtres sur place — descendez dans une catégorie, tapez deux lettres d'une marque,
cochez un état ou une couleur — et écrit l'URL pour vous, avec un lien pour vérifier d'abord
les résultats sur Vinted.

<div align="center">
  <img src="docs/media/builder.png" width="820" alt="Le constructeur de recherche du tableau de bord : site, texte et prix, l'arbre de catégories de Vinted, l'autocomplétion des marques, et les cases état et couleur avec le nombre d'articles">
</div>

Tous les sites nationaux fonctionnent : `vinted.fr`, `.de`, `.nl`, `.co.uk`, `.com` et les
autres. Le site que vous avez copié est celui qui sera surveillé, et les liens reçus pointent
vers lui.

Les paramètres de suivi sont retirés : coller deux fois la même recherche est donc reconnu
comme une seule recherche.

## La vitesse, honnêtement

Par défaut, une vérification par minute et par recherche, avec un plancher de dix secondes. Ce
plancher n'est pas de la prudence gratuite : l'API de Vinted a elle-même du retard sur ce que
les gens publient, parfois de plusieurs minutes. Vérifier toutes les deux secondes ne trouve
rien plus tôt et vous fait bloquer.

Quiconque vous promet des alertes Vinted sans délai a quelque chose à vendre. Chaque
notification affiche l'heure indiquée par Vinted et celle où nous l'avons trouvée, pour que
vous voyiez l'écart vous-même.

## Ce qui casse, et à quelle fréquence

Vinted n'a pas d'API publique et aucune obligation de maintenir l'API privée. Trois choses
arrivent en pratique :

**403, bloqué.** C'est en général l'adresse depuis laquelle vous vous connectez, pas ce que
vous avez demandé. Les connexions résidentielles sont rarement concernées ; certaines plages
de VPS bon marché le sont. L'outil ralentit, ouvre une nouvelle session et continue. Si cela
persiste, [le guide de dépannage](docs/troubleshooting.md) donne un test en une ligne qui
indique si le problème vient de votre adresse ou de l'application.

**Le catalogue se fige.** Voir plus haut. C'est le rôle de la surveillance.

**Vinted change quelque chose.** Quelques fois par an. Une tâche planifiée fait une vraie
requête chaque semaine, pour que la panne apparaisse ici avant d'apparaître chez vous.

## Où l'héberger

Une machine chez vous est le meilleur endroit : un Raspberry Pi, un vieux portable, un NAS.
Cela ne coûte rien, et les connexions résidentielles sont bien moins souvent bloquées que
celles des datacenters. Un VPS à 5 € par mois fonctionne aussi. [Le guide
d'hébergement](docs/self-hosting.md) couvre les deux et explique quelles offres gratuites
éviter.

Il n'existe pas de version hébergée, volontairement : un seul serveur émettant les requêtes de
tout le monde concentrerait exactement le risque que l'on évite en répartissant l'usage sur
des connexions ordinaires.

## Configuration

Au quotidien, le tableau de bord *est* la configuration : recherches, destinations, plafonds
de prix et mots interdits par recherche, tout se règle là. Les mêmes actions existent en ligne
de commande si vous préférez :

```
vinted-sniper watch <url>       ajouter une recherche
vinted-sniper searches          les lister
vinted-sniper status            leur état
vinted-sniper check --url <url> test ponctuel
vinted-sniper destination ...   ajouter une destination
```

Les réglages du processus (jeton du bot Telegram, fréquence des vérifications, proxies,
journalisation) sont des variables d'environnement. Aucune n'est nécessaire pour démarrer.
Elles sont documentées dans [`.env.example`](.env.example), ordonné pour que les rares
réglages utiles arrivent en premier, et en détail dans
[docs/configuration.md](docs/configuration.md).

## Développement

```bash
uv sync --all-extras
uv run pytest
uv run ruff check . && uv run mypy
```

`VINTED_SNIPER_FETCH_MODE=mock` rejoue des réponses enregistrées sur disque, ce qui permet de
travailler sans solliciter Vinted. Voir [docs/architecture.md](docs/architecture.md) et
[docs/REVIEW.md](docs/REVIEW.md).

## À propos des règles

Ce projet n'est pas affilié à Vinted. Il lit des pages publiques de façon anonyme, à un rythme
volontairement modéré, et stocke ce qu'il trouve sur votre machine. Les conditions de Vinted
interdisent l'accès automatisé ; utiliser cet outil, c'est accepter ce risque. Il ne se
connecte pas à votre compte, n'achète ni ne publie rien, et ne constitue aucun profil de
vendeur. [docs/legal.md](docs/legal.md) en dit plus, notamment sur les données personnelles
des autres.

Licence MIT. Les contributions sont bienvenues, en particulier les rapports de bug avec les
journaux joints.
