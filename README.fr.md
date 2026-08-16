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

1. Installez [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Linux : [Docker Engine](https://docs.docker.com/engine/install/)).
2. Téléchargez [docker-compose.yml](https://raw.githubusercontent.com/jasp-nerd/vinted-sniper/main/docker-compose.yml) (clic droit, « Enregistrer le lien sous... ») dans un dossier.
3. Ouvrez un terminal dans ce dossier et tapez `docker compose up -d`.
4. Ouvrez **http://localhost:8000**. Collez un webhook Discord et une URL de recherche Vinted, ou construisez la recherche sur place.

C'est tout. Pas de connexion, pas de fichier de configuration. Les étapes 2 et 3 en version
terminal :

```bash
curl -O https://raw.githubusercontent.com/jasp-nerd/vinted-sniper/main/docker-compose.yml
docker compose up -d
```

Sans Docker ? C'est une application Python ordinaire (3.13+, [uv](https://docs.astral.sh/uv/)) :

```bash
git clone https://github.com/jasp-nerd/vinted-sniper.git && cd vinted-sniper
uv sync --extra web && uv run vinted-sniper run
```

Les alertes arrivent pendant que votre ordinateur est allumé et éveillé. Pour des alertes en
continu, installez-le sur un Raspberry Pi, un NAS ou un petit VPS : voir [le guide
d'hébergement](docs/self-hosting.md).

## Fonctionnalités

- **Filtre sur ce que vous payez vraiment**, protection acheteurs comprise. Le filtre de prix de Vinted l'ignore.
- **Vous prévient quand il ne fonctionne plus.** Une surveillance repère une recherche qui se tait et répare la session elle-même.
- **Aucun compte Vinted, aucune connexion, aucun cookie.** Rien que Vinted puisse restreindre.
- **Constructeur de recherche dans le tableau de bord** : les catégories de Vinted, l'autocomplétion des marques et les filtres, avec les compteurs en direct.
- **Silencieux au premier passage.** Pas de déluge initial de quatre-vingt-seize vieilles annonces.
- **Discord, Telegram, ntfy, RSS ou webhook JSON**, avec un routage par recherche.
- **Tous les sites nationaux** : `vinted.fr`, `.de`, `.nl`, `.co.uk`, `.com` et les autres.

## Captures d'écran

<div align="center">
  <img src="docs/media/builder.png" width="820" alt="Le constructeur de recherche du tableau de bord : site, texte et prix, l'arbre de catégories de Vinted, l'autocomplétion des marques, et les cases état et couleur avec le nombre d'articles">
  <br><sub>Le constructeur de recherche</sub>
  <br><br>
  <img src="docs/media/discord.png" width="820" alt="Une notification Discord : titre, prix protection acheteurs comprise, taille, marque, état, vendeur et photo, avec les boutons pour ouvrir l'annonce, contacter le vendeur ou acheter">
  <br><sub><b>Discord</b> — un message par annonce, regroupés en encarts quand plusieurs arrivent d'un coup</sub>
  <br><br>
  <img src="docs/media/telegram.png" width="820" alt="La même notification dans Telegram : aperçu photo, prix protection acheteurs comprise, marque, taille, état, vendeur et les trois mêmes boutons">
  <br><sub><b>Telegram</b> — la photo en aperçu, pour que le message garde ses boutons</sub>
</div>

## Documentation

- [Configuration](docs/configuration.md) : tous les réglages, la ligne de commande, les canaux de notification et le mot de passe du tableau de bord pour l'accès distant. Rien n'est nécessaire pour démarrer.
- [Hébergement](docs/self-hosting.md) : Raspberry Pi, NAS, VPS, mises à jour, sauvegardes.
- [Dépannage](docs/troubleshooting.md) : rien n'arrive, les 403, et le test en une ligne qui dit si votre adresse IP est bloquée.
- [Architecture](docs/architecture.md) et [REVIEW](docs/REVIEW.md) : pour travailler sur le code.

Sans affiliation avec Vinted. L'outil lit des annonces publiques de façon anonyme et ne se
connecte jamais, n'achète rien, ne publie rien ; les conditions de Vinted interdisent l'accès
automatisé, à vous de juger. Plus de détails dans [docs/legal.md](docs/legal.md).

Licence MIT. Les contributions sont bienvenues, en particulier les rapports de bug avec les
journaux joints.
