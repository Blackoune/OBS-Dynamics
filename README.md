<div align="center">

<img src="assets/logo.png" alt="Logo OBS Dynamics" width="128" height="128">

# OBS Dynamics

**Le pilote automatique de vos scènes OBS Studio.**

Détection du jeu en cours, bascule de scène selon ce qui est à l'écran,
overlays déclenchés au clavier, chat Twitch et widget musique — dans une
seule application Windows, sans compte et sans configuration serveur.

**Site officiel : [blackoune.github.io](https://blackoune.github.io/)**

![Plateforme](https://img.shields.io/badge/plateforme-Windows%2010%20%7C%2011-0B0F17?style=flat-square)
![Python](https://img.shields.io/badge/python-3.12%20%E2%80%93%203.14-0B0F17?style=flat-square&logo=python&logoColor=22D3EE)
![OBS](https://img.shields.io/badge/OBS%20WebSocket-v5-0B0F17?style=flat-square&logo=obsstudio&logoColor=22D3EE)
![Langues](https://img.shields.io/badge/langues-39-0B0F17?style=flat-square)
![Tests](https://img.shields.io/badge/tests-pytest-0B0F17?style=flat-square&logo=pytest&logoColor=22D3EE)

</div>

---

## Sommaire

1. [Présentation](#présentation)
2. [Fonctionnalités](#fonctionnalités)
3. [Prérequis](#prérequis)
4. [Installation](#installation)
5. [Premier démarrage](#premier-démarrage)
6. [Guide d'utilisation](#guide-dutilisation)
   - [Bibliothèque de jeux](#bibliothèque-de-jeux)
   - [Images de détection et contrôle du cadrage](#images-de-détection-et-contrôle-du-cadrage)
   - [États supplémentaires](#états-supplémentaires)
   - [Démarrer la surveillance](#démarrer-la-surveillance)
   - [Hotkeys de forçage](#hotkeys-de-forçage)
   - [Raccourcis & Overlays](#raccourcis--overlays)
   - [Chat Twitch](#chat-twitch)
   - [Widget Musique](#widget-musique)
   - [Paramètres et langue](#paramètres-et-langue)
7. [Configuration avancée](#configuration-avancée)
8. [Données, sécurité et confidentialité](#données-sécurité-et-confidentialité)
9. [Dépannage](#dépannage)
10. [Développement](#développement)
11. [Packaging](#packaging)
12. [Architecture](#architecture)
13. [Traductions](#traductions)
14. [Limites connues](#limites-connues)

---

## Présentation

OBS Dynamics est une application de bureau Windows qui fait le travail de
régie à votre place pendant un stream. Elle observe quel jeu tourne, regarde
l'écran pour savoir si vous êtes dans un **menu** ou **en jeu**, et demande à
OBS Studio d'afficher la scène correspondante.

Autour de ce cœur, elle fournit trois overlays prêts à coller dans OBS comme
sources navigateur : des médias déclenchés au clavier, le chat de votre chaîne
Twitch, et un widget « en cours de lecture » pour votre lecteur de musique.

Tout tient dans un seul processus : un `.exe` ou `python obs_dynamics.py`.
Aucun navigateur à ouvrir, aucun terminal à laisser tourner, aucun compte à
créer.

### Comment ça marche

```
  Processus Windows        Capture de l'écran         OBS Studio
  ─────────────────        ──────────────────         ──────────
  psutil repère le   ───►  OpenCV compare l'écran ───► WebSocket v5 :
  jeu en cours             à vos captures de           bascule sur la
                           référence (menu / jeu)      scène configurée
```

1. **Détection de processus** (`psutil`). Les jeux Steam sont reconnus par
   leur dossier d'installation, ce qui reste fiable même quand l'exécutable
   est renommé. Les jeux ajoutés à la main sont reconnus par leur nom d'exe.
2. **Confirmation visuelle** (OpenCV `matchTemplate`). L'écran est comparé à
   des fragments de vos captures de référence. Deux lectures concordantes
   sont exigées avant toute bascule, et le meilleur état doit devancer le
   second d'au moins **0,15** : une quasi-égalité ne change rien.
3. **Bascule de scène** (OBS WebSocket v5 via `simpleobsws`). La connexion
   est rétablie automatiquement si OBS redémarre.

---

## Fonctionnalités

| Module | Ce qu'il fait |
|---|---|
| **Bibliothèque** | Scan automatique des bibliothèques Steam, ajout manuel de n'importe quel exécutable, jaquettes officielles téléchargées et mises en cache. |
| **Détection visuelle** | Distingue menu, en jeu et autant d'états supplémentaires que nécessaire (carte, inventaire, pause…). Robuste de 720p à 4K. |
| **Création de scènes** | Crée dans OBS les scènes « `<jeu> - Menu` » et « `<jeu> - En jeu` » avec leur source de capture, en un clic. |
| **Hotkeys** | `F1` / `F2` / `F3` forcent l'état quand la détection se trompe. Combinaisons acceptées (`ctrl+shift+f1`). |
| **Raccourcis & Overlays** | Une combinaison de touches affiche une image, joue une vidéo ou un son dans OBS. Mode maintien pour masquer une minimap. |
| **Chat Twitch** | Chat de votre chaîne en overlay, en lecture anonyme : aucun compte, aucune clé. Lien permanent. |
| **Widget Musique** | Pochette, titre, artiste et forme d'onde, un overlay par lecteur (Spotify, Deezer, Apple Music…). Cinq dispositions, huit thèmes, image de fond personnalisée. |
| **Interface** | 39 langues, changement à chaud. Identifiants chiffrés au repos. |

---

## Prérequis

| Élément | Version | Remarque |
|---|---|---|
| Windows | 10 ou 11 | L'application utilise des API propres à Windows (registre, SMTC, DPAPI, `game_capture`). |
| OBS Studio | 28 ou plus | Le serveur WebSocket v5 y est intégré. |
| Python | 3.12 minimum | Uniquement pour lancer depuis les sources. `numpy 2.5.1` n'existe pas pour 3.11. Le `.exe` est construit sous 3.14. |

---

## Installation

### Option A — Exécutable

Si vous disposez de `dist_release/Dynamics.exe`, il n'y a rien à installer :
lancez-le. Pour créer un raccourci « Dynamics » sur le Bureau :

```bash
python make_shortcut.py
```

### Option B — Depuis les sources

```bash
git clone https://github.com/Blackoune/OBS-Dynamics.git
```

```bash
cd OBS-Dynamics
```

```bash
python -m venv venv
```

```bash
venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

```bash
python obs_dynamics.py
```

Toutes les dépendances sont **épinglées** dans `requirements.txt` : deux
installations faites à des dates différentes donnent exactement le même
logiciel.

---

## Premier démarrage

### 1. Activer le serveur WebSocket dans OBS

Dans OBS Studio : **Outils → Paramètres du serveur WebSocket**.

- Cocher **Activer le serveur WebSocket**.
- Port : `4455` (valeur par défaut).
- Cocher **Activer l'authentification** et cliquer sur **Afficher les
  informations de connexion** pour copier le mot de passe.

### 2. Renseigner la connexion dans OBS Dynamics

Onglet **Paramètres** → section **Connexion OBS WebSocket** : adresse
`localhost`, port `4455`, puis le mot de passe copié. **Enregistrer**.

Le mot de passe est écrit dans `%APPDATA%\OBS Dynamics\.env` puis chiffré
(voir [Données, sécurité et confidentialité](#données-sécurité-et-confidentialité)).

### 3. Ajouter un premier jeu

Onglet **Bibliothèque** → **Scanner Steam** pour importer vos jeux Steam, ou
**+ Ajouter** pour un jeu hors Steam. La suite est détaillée ci-dessous.

---

## Guide d'utilisation

### Bibliothèque de jeux

La bibliothèque affiche une carte par jeu, avec sa jaquette et une pastille
d'état **Actif** (le jeu tourne) ou **Inactif**. La grille s'adapte à la
largeur de la fenêtre, de 2 à 8 colonnes.

| Action | Comment |
|---|---|
| Importer les jeux Steam | **Scanner Steam** lit le registre et toutes les bibliothèques Steam de la machine. Les jeux déjà présents ne sont pas dupliqués. |
| Ajouter un jeu hors Steam | **+ Ajouter** → **Manuel (exe)** → nom du jeu et nom exact de l'exécutable, par exemple `VALORANT-Win64-Shipping.exe`. |
| Modifier un jeu | **Éditer** sur sa carte. |
| Retirer un jeu | **Supprimer** sur sa carte, puis confirmer. |

Pour chaque jeu, la fiche permet de choisir :

- la **scène OBS — Menu** et la **scène OBS — En jeu** dans des menus
  déroulants alimentés par OBS ;
- ou de cocher **Créer automatiquement les scènes dans OBS** : l'application
  crée « `<jeu> - Menu` » et « `<jeu> - En jeu` » avec une source de capture
  de jeu, puis les sélectionne. Une scène déjà choisie à la main n'est jamais
  écrasée.

> Le nom de l'exécutable se trouve dans le **Gestionnaire des tâches** →
> onglet **Détails**, pendant que le jeu tourne.

### Images de détection et contrôle du cadrage

La détection visuelle a besoin de **captures d'écran de référence** au
format PNG, en plein écran :

- **Images de détection — Menu** : une ou plusieurs captures du menu du jeu ;
- **Images de détection — En jeu** : une ou plusieurs captures en partie.

Bonnes pratiques pour des captures fiables :

- Capturer à la **résolution habituelle** du jeu, en plein écran.
- Choisir des écrans où l'**interface** est bien visible : HUD, barre de vie,
  minimap, logo du menu. Le décor change sans cesse, l'interface non.
- Fournir **les deux** captures (menu et en jeu) : l'application vérifie
  qu'elles se distinguent assez l'une de l'autre.

Après le choix des images, **Vérifier le cadrage** ouvre la fenêtre de
contrôle. Elle montre, numérotés, les fragments que le logiciel comparera à
l'écran :

| Élément | Rôle |
|---|---|
| Cadres numérotés | Chaque fragment doit tomber sur un élément **stable** (icône, cadre, texte d'interface). Cocher le numéro des cadres mal placés puis **Recalculer sans les cochés**. |
| Zones manuelles | Choisir une zone dans la liste pour la repositionner aux curseurs (position X, Y, largeur, hauteur), ou **+** pour en ajouter une. Les zones réglées à la main apparaissent en orange. |
| Marge de séparation | Indique si les captures menu et en jeu se distinguent nettement. Si elles se ressemblent trop, aucun réglage de cadrage n'y changera rien : il faut des captures plus différentes. |
| **Tester sur l'écran actuel** | Donne le score de l'écran affiché à cet instant. Pratique avec le jeu ouvert en fenêtré. |
| **Valider ce cadrage** | Mémorise le cadrage pour ces images. Il n'est redemandé que si une image change (le bouton devient alors **Cadrage à revalider**). |

### États supplémentaires

Au-delà de menu et en jeu, **+ État supplémentaire** crée un état nommé
(« Carte », « Inventaire », « Pause »…) avec ses propres images de référence
et sa propre scène OBS. Utile quand un écran précis doit avoir sa mise en
page dédiée. **Supprimer** le retire.

### Démarrer la surveillance

Le bouton **Démarrer** de la barre latérale lance la boucle de détection. Le
statut juste au-dessus indique en permanence l'état de la liaison :

| Statut | Signification |
|---|---|
| Surveillance arrêtée | Rien n'est détecté, aucune scène ne change. |
| Connexion à OBS... | Tentative de connexion au serveur WebSocket. |
| Surveillance active — OBS connecté | Tout fonctionne. |
| Surveillance active — OBS reconnecté | OBS avait été fermé ou redémarré ; la liaison est rétablie. |
| Surveillance active (OBS: …) | La détection tourne mais OBS refuse la connexion. Le motif est affiché. Voir [Dépannage](#dépannage). |

Le bouton **Dossier de données** ouvre directement
`%APPDATA%\OBS Dynamics\data\`.

### Hotkeys de forçage

Quand la détection visuelle se trompe, une touche force l'état de tous les
jeux actifs :

| Touche | État forcé |
|---|---|
| `F1` | En jeu |
| `F2` | Menu |
| `F3` | Inactif |

Ces associations se modifient dans `%APPDATA%\OBS Dynamics\data\hotkeys.json`
(voir [Configuration avancée](#configuration-avancée)).

### Raccourcis & Overlays

Cet onglet associe une combinaison de touches à un média affiché dans OBS :
masquer une carte en jeu, jouer un son, lancer une courte vidéo.

1. **+ Ajouter un raccourci** : une ligne s'insère en haut de la liste.
2. Cliquer sur le champ de gauche, puis presser la combinaison voulue
   (`Ctrl + Shift + A`, `F5`…). `Échap` annule.
3. Choisir le type — **Image**, **Vidéo** ou **Son** — puis glisser le
   fichier sur la zone de dépôt, ou cliquer pour parcourir.
4. Choisir la **Durée** d'affichage.
5. **Copier** l'URL affichée sous la ligne et la coller dans OBS :
   **Sources → + → Navigateur → URL**.

La pastille de la ligne indique **Prêt** quand tout est renseigné, ou
**Incomplet** s'il manque la combinaison ou le fichier. Une combinaison déjà
prise par un autre raccourci est refusée.

#### Durée et mode maintien

| Réglage | Comportement |
|---|---|
| **Maintien** | Affiché tant que la touche reste enfoncée, masqué au relâchement. |
| 150 ms, 300 ms, 500 ms, 1 s, 2 s, 3 s, 5 s, 10 s | Affiché puis masqué automatiquement après ce délai. 3 s par défaut. |

Pour masquer une minimap, **Maintien** est le bon réglage : on appuie, on
consulte, on relâche. Aucune minuterie à calibrer, et le retour suit
exactement le doigt.

Le média est **préchargé** à l'ouverture de la source : un déclenchement ne
coûte qu'un basculement d'affichage, sans requête réseau ni redécodage.
C'est ce qui rend l'apparition et la disparition immédiates, même sur des
appuis rapides et répétés.

#### Le serveur overlay local

Une source navigateur OBS consomme une URL. L'application ouvre donc un petit
serveur HTTP local, `http://127.0.0.1:4466` par défaut, qui sert les trois
familles d'overlays (raccourcis, chat, musique).

- Il n'écoute que sur la **boucle locale** : aucune autre machine ne peut
  l'atteindre.
- Il ne répond qu'aux requêtes dont l'hôte est `127.0.0.1`, `localhost` ou
  `::1`. Tout le reste reçoit un `403`. C'est ce qui empêche un site web
  ouvert dans votre navigateur de lire vos overlays. Collez donc l'URL **telle
  qu'affichée** : remplacer `127.0.0.1` par le nom de votre PC ne fonctionnera
  pas.
- Si le port est occupé, l'application réessaie brièvement puis se replie sur
  un port libre, et **le signale en jaune dans l'onglet**. Les URL déjà
  collées dans OBS pointent alors vers l'ancien port : recopiez celles
  affichées sous chaque ligne. Pour fixer un autre port durablement, voir
  `OBS_OVERLAY_PORT` dans [Configuration avancée](#configuration-avancée).

### Chat Twitch

1. Carte Twitch → **Connexion** → nom de votre chaîne → **Valider**.
2. **Copier le lien** en bas de l'onglet.
3. Dans OBS : **Sources → + → Navigateur → URL**.

Aucun compte, aucune clé, aucune application à déclarer : le chat public
d'une chaîne Twitch se lit en **IRC anonyme**. Un compte ne serait nécessaire
que pour écrire ou modérer, ce que cette version ne fait pas.

L'interrupteur **Afficher dans l'overlay** masque le chat **sans couper la
connexion** : le réafficher est instantané.

#### Le lien overlay

Le lien a la forme `http://127.0.0.1:4466/chat/<jeton>`. Il **reste valide
après un redémarrage** : le jeton est créé une seule fois, puis relu à chaque
lancement.

**Régénérer le lien** fabrique un nouveau jeton et invalide l'ancien : la
source déjà configurée dans OBS cessera de répondre et devra être recollée. À
n'utiliser que si l'URL a été montrée à l'écran ou a fuité.

Si l'application est fermée, la page affiche « En attente de connexion… » et
se reconnecte d'elle-même au redémarrage.

> Twitch est la seule plateforme de chat prise en charge. Kick et TikTok ont
> été essayés puis retirés : aucune API publique fiable ne permettait de lire
> leur chat durablement.

### Widget Musique

Un overlay « en cours de lecture » par lecteur : pochette, titre, artiste,
application, et une forme d'onde qui suit le son. Les informations viennent
de l'API multimédia de Windows (SMTC) : **aucun compte à connecter**, aucun
mot de passe, aucune clé d'API. L'onglet lit simplement ce que le lecteur
déjà ouvert publie au système.

#### Lecteurs pris en charge

| Lecteur | Titre, artiste, pochette | Son isolé |
|---|:---:|:---:|
| Spotify | Oui | Oui |
| Apple Music | Oui | Oui |
| iTunes | Oui | Oui |
| Deezer | Oui | Oui |
| Tidal | Oui | Oui |
| Amazon Music | Oui | Oui |
| SoundCloud | Oui | Oui |
| YouTube Music | Oui | Oui |
| Navigateur (Chrome, Edge, Firefox…) | Oui, voir ci-dessous | Partiel |

Les huit services sont **toujours listés**, même éteints, et leur lien
d'overlay ne change jamais. On prépare la source dans OBS une fois : elle
s'allume d'elle-même le jour où ce lecteur joue.

Chaque lecteur porte son logo, sur sa carte comme dans l'overlay. Une source
hors catalogue affiche ses initiales dans sa couleur. Les logos sont dans
`assets/music/` : remplacer un PNG suffit à changer le logo affiché.

> **Lecture dans un navigateur.** Un lecteur utilisé dans un onglet est bien
> détecté, mais Windows n'indique pas *quel site* joue, seulement le
> navigateur. La pastille affichera donc **CHROME**, **EDGE** ou **FIREFOX**,
> jamais « Spotify » ou « Deezer ». Même limite pour le son : la forme d'onde
> suit tout le son du navigateur, y compris celui d'un autre onglet. Pour
> obtenir le nom du service, installez son application.

#### Personnaliser l'apparence

Le bouton **Widget** d'une carte ouvre la fenêtre de style, avec un aperçu en
direct :

| Réglage | Options |
|---|---|
| Templates | Violet, Cassette néon, Ardoise, Clair, Sans cadre, Mocha, macOS sombre, macOS clair |
| Disposition du lecteur | **Compact** (pochette à gauche), **Blocs** (trois cases), **Galerie** (pochette au-dessus), **Minimal** (une ligne, sans pochette), **Bandeau** (pleine largeur) |
| Apparence de la pochette | **Auto**, **Vinyle** (disque qui tourne), **Carré**, **Large** (16:9), **Aucune** |
| Éléments affichés | Forme d'onde, artiste, application, pastilles de fenêtre, progression, boutons décoratifs |
| Couleurs | Couleur de fond, opacité du fond, contour et sa couleur |
| Image personnalisée | Enregistrer le gabarit, dessiner par-dessus dans votre éditeur, puis **Choisir mon image**. La couleur du texte est déduite de la luminosité de l'image. |

#### Taille de la source navigateur

Une source navigateur OBS ne se redimensionne pas toute seule : sa taille est
celle saisie dans ses propriétés.

- **Trop grand ne coûte rien.** Le fond est transparent et la carte est
  centrée : le surplus reste invisible. **Trop petit rogne.**
- **Taille universelle : 1132 × 383.** Elle couvre toutes les combinaisons
  possibles.
- Pour une scène plus serrée, l'application affiche la taille exacte de la
  combinaison en cours, sous l'aperçu et à côté du lien.

| Disposition | Taille maximale |
|---|---|
| Compact | 902 × 241 |
| Blocs | 1132 × 252 |
| Galerie | 670 × 383 |
| Minimal | 666 × 223 |
| Bandeau | 918 × 247 |

Ces valeurs sont des **maximums garantis** : la largeur du titre et de
l'artiste est plafonnée, aucun morceau au nom à rallonge ne peut faire
déborder la carte.

Après une mise à jour de l'application, les sources ouvertes dans OBS **se
rechargent seules** pour afficher la nouvelle version de la page.

#### Forme d'onde

Le niveau est lu sur le compteur de la session audio du lecteur ciblé, le
même que le mélangeur de volume de Windows. Chaque overlay ne réagit donc
qu'au son de **sa** source. Ce compteur donne une amplitude, pas un spectre :
la forme d'onde défile dans le temps. Le relevé ne tourne que tant qu'un
overlay est connecté à cette source.

### Paramètres et langue

| Réglage | Rôle |
|---|---|
| Adresse, port, mot de passe | Connexion au serveur WebSocket d'OBS. |
| Intervalle scan (s) | Période entre deux analyses. Plancher : 0,5 s. Défaut : 2 s. |
| Seuil détection visuelle (0-1) | Score minimal pour reconnaître un état. Défaut : 0,8. |
| Langue de l'interface | **FR**, **EN**, **ES** en boutons ; les 36 autres langues dans le menu **Autres langues** à droite. Le changement est immédiat, sans redémarrage. |

Langues disponibles : Français, English, Español, Bahasa Indonesia, Bahasa
Melayu, Čeština, Dansk, Deutsch, Eesti, Filipino, Hausa, Hrvatski, Italiano,
Kiswahili, Latviešu, Lietuvių, Magyar, Nederlands, Norsk, Polski, Português,
Română, Slovenčina, Slovenščina, Suomi, Svenska, Tiếng Việt, Türkçe, Русский,
Українська, اردو, العربية, فارسی, हिन्दी, বাংলা, ไทย, 中文, 日本語, 한국어.

---

## Configuration avancée

### Le fichier `.env`

Il vit dans `%APPDATA%\OBS Dynamics\.env`. L'onglet **Paramètres** l'écrit
pour vous ; l'éditer à la main n'est utile que pour les réglages sans
équivalent dans l'interface. Les clés reconnues sont commentées dans
[`.env.example`](.env.example) ; toute autre clé est ignorée.

| Clé | Rôle | Défaut |
|---|---|---|
| `OBS_WS_HOST` | Adresse du serveur OBS WebSocket | `localhost` |
| `OBS_WS_PORT` | Port du serveur OBS WebSocket | `4455` |
| `OBS_WS_PASSWORD` | Mot de passe WebSocket (chiffré automatiquement) | *(vide)* |
| `OBS_SCAN_INTERVAL_SECONDS` | Période de scan, plancher 0,5 s | `2.0` |
| `OBS_MATCH_THRESHOLD` | Score OpenCV minimal, entre 0,0 et 1,0 | `0.8` |
| `OBS_APP_LANG` | Code de langue (`fr`, `en`, `de`, `ja`…) | `fr` |
| `RAWG_API_KEY` | Jaquettes des jeux hors Steam, optionnel ([clé gratuite](https://rawg.io/apidocs)) | *(vide)* |
| `OBS_OVERLAY_PORT` | Port du serveur overlay local | `4466` |

> Changer `OBS_OVERLAY_PORT` invalide toutes les URL déjà collées dans OBS.
> Ne le modifier qu'en cas de conflit avec un autre logiciel.

### Les hotkeys de forçage

Fichier `%APPDATA%\OBS Dynamics\data\hotkeys.json` :

```json
{ "f1": "in_game", "f2": "menu", "f3": "inactive" }
```

- États valides : `inactive`, `active`, `menu`, `in_game`.
- Noms de touches au format pynput, **en minuscule** : `f1`, `f5`, `k`…
- Combinaisons acceptées, dans n'importe quel ordre : `ctrl+shift+f1`.
  `Ctrl + F1` ne déclenche pas l'action liée à `F1` seul.

Redémarrer l'application après modification.

---

## Données, sécurité et confidentialité

### Où vivent vos données

Tout est rangé dans votre profil Windows, **hors du dossier du programme** :

```
%APPDATA%\OBS Dynamics\
├── .env                      identifiants et réglages
└── data\
    ├── games.json            bibliothèque de jeux
    ├── covers\               jaquettes en cache
    ├── hotkeys.json          hotkeys de forçage
    ├── triggers.json         raccourcis & overlays
    ├── multistream.json      chaîne Twitch et jeton du lien chat
    ├── music_widget.json     styles du widget musique
    ├── music_backgrounds\    images de fond personnalisées
    └── obs_dynamics.log      journal (rotation : 3 × 2 Mo)
```

Cet emplacement ne dépend pas de l'endroit d'où le programme est lancé :
`Dynamics.exe` et `python obs_dynamics.py` lisent et écrivent exactement les
mêmes fichiers. Mettre à jour, déplacer ou recloner le programme ne fait rien
perdre.

Une bibliothèque restée dans `<dépôt>/data/` (installations antérieures au
2026-09-10) est reprise automatiquement au premier démarrage ; l'ancien
dossier est renommé `data.old`. Un ancien `.env` à la racine du dépôt est de
même déplacé, puis renommé `.env.old`.

### Chiffrement des identifiants

Le mot de passe OBS, la clé RAWG et le jeton du lien chat sont **chiffrés au
repos** avec DPAPI, dont la clé dérive de votre compte Windows. Le fichier
copié sur une clé USB, envoyé par mail, retrouvé dans une sauvegarde ou lu
par un autre compte de la machine ne donne rien.

Vous pouvez saisir une valeur en clair dans `.env` : elle est chiffrée au
démarrage suivant, sous la forme `enc:v2:…`.

Limite inhérente à DPAPI : un programme lancé **sous votre propre session** peut
déchiffrer ces valeurs. Aucun stockage local sans mot de passe maître ne fait
mieux.

### Journaux

Le journal masque automatiquement les secrets avant écriture. Il peut donc
être joint tel quel à un rapport de bug.

### Réseau

- Connexion sortante vers OBS (en local par défaut).
- Connexion sortante vers Twitch (IRC chiffré), uniquement si le chat est
  configuré.
- Téléchargement des jaquettes depuis le CDN Steam, le Steam Store et RAWG.
- Serveur overlay en écoute sur `127.0.0.1` uniquement.

Aucune télémétrie, aucun compte, aucune donnée envoyée ailleurs.

---

## Dépannage

### Connexion à OBS

**Le statut affiche « Surveillance active (OBS: …) » ou l'application ne se
connecte jamais.**

1. Vérifier qu'OBS Studio est lancé.
2. Dans OBS : **Outils → Paramètres du serveur WebSocket** → le serveur doit
   être **activé**.
3. Comparer le port avec celui de l'onglet **Paramètres** (4455 par défaut).
4. Recopier le mot de passe depuis **Afficher les informations de connexion**
   et l'enregistrer à nouveau dans **Paramètres**.
5. Un pare-feu tiers peut bloquer `localhost:4455` : autoriser OBS.

**Le mot de passe semble perdu après avoir copié `.env` depuis un autre PC.**
C'est normal : le chiffrement est lié au compte Windows d'origine. Ressaisir
le mot de passe dans **Paramètres**.

**Les menus déroulants de scène affichent « (non connecté à OBS) ».**
Démarrer la surveillance, attendre le statut « OBS connecté », puis rouvrir la
fiche du jeu.

### Détection des jeux

**Un jeu Steam n'est pas trouvé par le scan.**
Vérifier qu'il est bien installé (pas seulement possédé). Sinon, l'ajouter en
**Manuel (exe)**.

**Le jeu tourne mais sa carte reste « Inactif ».**
Pour un jeu manuel, le nom d'exécutable doit correspondre exactement à celui
du **Gestionnaire des tâches → Détails**, extension comprise. Certains jeux
passent par un lanceur : c'est l'exécutable du jeu lui-même qu'il faut
indiquer, pas celui du lanceur.

**La scène ne change pas entre menu et en jeu.**

1. La surveillance doit être démarrée et OBS connecté.
2. La fiche du jeu doit avoir une scène pour chaque état.
3. Ouvrir **Vérifier le cadrage** puis **Tester sur l'écran actuel** avec le
   jeu affiché. Un score sous le seuil (0,8 par défaut) explique le problème.
4. Si la marge de séparation est jugée insuffisante, reprendre des captures
   plus différentes : un menu plein écran contre un HUD de jeu, par exemple.

**La scène bascule au mauvais moment.**
Des fragments tombent probablement sur le décor. Dans **Vérifier le
cadrage**, cocher les cadres mal placés puis **Recalculer sans les cochés**,
ou placer des zones manuelles sur l'interface. En dernier recours, remonter
légèrement le seuil dans **Paramètres**.

**Plusieurs écrans : rien n'est détecté.**
La capture porte sur l'**écran principal** de Windows. Le jeu doit y être
affiché.

**Pas de jaquette pour un jeu hors Steam.**
Renseigner une clé `RAWG_API_KEY` gratuite dans `.env`.

### Raccourcis et overlays

**La combinaison ne déclenche rien.**

- La source navigateur doit être **ouverte dans OBS** : sans elle, le
  journal indique « aucune source navigateur ouverte ».
- Un autre logiciel peut intercepter la même combinaison : en choisir une
  autre.
- Le champ de combinaison affiche « pynput absent » : réinstaller les
  dépendances (`pip install -r requirements.txt`).

**La source navigateur reste vide ou affiche une erreur.**

- L'application doit être lancée : c'est elle qui sert la page.
- Vérifier que l'URL collée est **exactement** celle affichée, avec
  `127.0.0.1` et le bon port.
- Un bandeau jaune dans l'onglet signale un repli de port : recopier les URL.
- Une source ouverte **avant** le lancement de l'application affiche
  l'erreur d'OBS jusqu'à un rafraîchissement. Cocher **Actualiser le
  navigateur quand la scène devient active** dans les propriétés de la
  source.

**Le son d'un raccourci ne s'entend pas.**
Dans les propriétés de la source navigateur, cocher **Contrôler l'audio via
OBS**, puis vérifier le niveau de la source dans le mélangeur audio.

**Le glisser-déposer ne fonctionne pas.**
Il nécessite `tkinterdnd2`. Sans lui, la zone reste cliquable et ouvre le
sélecteur de fichier.

### Chat Twitch

**Le statut reste « Erreur — reconnexion… ».**
Vérifier l'orthographe de la chaîne (le nom, pas l'URL) et la connexion
Internet. L'application se reconnecte seule, avec une attente croissante.

**Le lien chat ne répond plus.**
Il a probablement été régénéré. Recopier le lien affiché et le recoller dans
OBS.

### Widget Musique

**« Interface multimédia de Windows indisponible ».**
Les paquets `winrt-*` manquent : `pip install -r requirements.txt`.

**« Windows ne répond pas ».**
Le service des sessions média de Windows est bloqué. Fermer puis rouvrir
l'application ; si le problème persiste, redémarrer Windows.

**Aucune session détectée alors que la musique joue.**
Mettre en pause puis relancer la lecture, et cliquer **Rafraîchir**.

**La forme d'onde reste plate.**
Le lecteur doit émettre du son (volume non coupé dans le mélangeur Windows).
En navigateur, elle suit tout le son du navigateur.

**La carte est rognée dans OBS.**
Agrandir la source navigateur à la taille indiquée par l'application, ou à
1132 × 383 pour couvrir tous les cas.

### Rapport de bug

**Dossier de données** → `obs_dynamics.log`. Les secrets y sont masqués : le
fichier peut être joint tel quel.

---

## Développement

```bash
pip install -r requirements-dev.txt
```

```bash
git config core.hooksPath .githooks
```

Cette seconde commande active les hooks versionnés. Elle est à exécuter **une
fois par clone** : git ne clone pas `.git/hooks/`.

| Hook | Rôle |
|---|---|
| `pre-commit` | Refuse tout commit contenant un identifiant (`check_secrets.py`). Contournement d'une fausse alerte : `git commit --no-verify`. |
| `post-commit`, `post-merge` | Reconstruisent `dist_release/Dynamics.exe` en arrière-plan dès qu'un commit touche un `.py`, `build.spec`, `i18n.json`, `requirements.txt` ou `assets/`. Verdict dans `build_auto.log`. |

### Tests

```bash
python -m pytest tests/ -q
```

La suite tourne **sans OBS, sans Steam et sans serveur graphique**. Elle
couvre la configuration, la persistance, le parsing Steam, la détection, les
hotkeys, le serveur overlay et sa sécurité, le chat, le widget musique et
l'i18n.

Audit des secrets sur tout le dépôt :

```bash
python check_secrets.py --all
```

### Intégration continue

GitHub Actions (`.github/workflows/ci.yml`) exécute sur Windows, avec Python
3.12 et 3.14 :

- la suite de tests ;
- la cohérence i18n (clés manquantes, langues incomplètes) ;
- l'import de l'application ;
- l'absence de secret dans le dépôt ;
- `pip-audit` sur toutes les dépendances épinglées.

---

## Packaging

```bash
python build.py
```

Le script vérifie d'abord que chaque clé `t("...")` du code existe dans
`i18n.json` — un build ne peut donc pas produire un `.exe` affichant des clés
brutes — puis lance PyInstaller et copie le binaire dans
`dist_release/Dynamics.exe` (icône : `assets/icon.ico`).

Ce build manuel n'est utile que pour forcer une reconstruction hors commit :
les hooks `post-commit` et `post-merge` le lancent déjà. Windows verrouille un
`.exe` en cours d'exécution : si Dynamics tourne, le build s'arrête avec un
message explicite et l'ancienne version reste en place.

---

## Architecture

```
obs_dynamics.py       point d'entrée : fenêtre, navigation, câblage
app_paths.py          chemins, journalisation, prise en charge DPI
env_config.py         lecture et écriture du .env utilisateur
secret_store.py       chiffrement DPAPI des identifiants
games.py              scan Steam, modèle Game, persistance
detection.py          processus et comparaison visuelle (sans interface)
screen_match.py       comparaison écran / référence par fragments
obs_client.py         WebSocket OBS v5 et boucle de scan
cover_service.py      téléchargement et cache des jaquettes
hotkeys.py            hotkeys globales et combinaisons (pynput)
triggers.py           règles « raccourci vers média »
twitch_chat.py        chat Twitch (IRC anonyme) et hub de diffusion
overlay_server.py     serveur HTTP local des sources navigateur OBS
music_smtc.py         sonde SMTC : titre, artiste, pochette
music_catalog.py      lecteurs connus, couleurs de marque, logos
music_audio.py        niveau audio par application
music_style.py        apparence du widget, templates, géométrie
music_overlay.py      état partagé par source, page servie à OBS
ui_common.py          palette, police, libellés d'état, glisser-déposer
ui_dashboard.py       vue Bibliothèque
ui_game_dialogs.py    fiche de jeu et contrôle du cadrage
ui_settings.py        vue Paramètres
ui_triggers.py        vue Raccourcis & Overlays
ui_twitch_chat.py     vue Chat Twitch
ui_music.py           vue Widget Musique
ui_music_style.py     fenêtre de style du widget
i18n.py, i18n.json    traductions (39 langues)
check_secrets.py      garde-fou anti-secret (hook et CI)
build.py, build.spec  packaging PyInstaller et validation i18n
make_shortcut.py      raccourci Bureau
tools/                outils hors exécution (logos des lecteurs)
tests/                suite pytest
```

Le graphe d'imports est acyclique : `app_paths` et `ui_common` ne dépendent
d'aucun autre module du projet, `detection` ne dépend pas de l'interface, et
les vues dépendent des modules métier, jamais l'inverse. `obs_dynamics`
ré-exporte les noms publics des modules : `import obs_dynamics` donne accès à
`Game`, `DashboardView`, `detect_game_state`, etc.

Pour les décisions de conception et l'historique, voir
[HANDOVER.md](HANDOVER.md).

---

## Traductions

**FR**, **EN** et **ES** sont des boutons dans **Paramètres** ; toutes les
autres langues de `i18n.json` apparaissent dans le menu **Autres langues** à
leur droite (10 lignes visibles, le reste défile), sous leur nom natif, par
ordre alphabétique.

Ajouter une langue :

1. Dupliquer un bloc de langue dans `i18n.json` sous le nouveau code.
2. Traduire toutes les valeurs, sans oublier `LANG_NAME` (nom natif de la
   langue) ni les `{placeholders}`.
3. Rien d'autre à modifier : la langue apparaît automatiquement.

`python build.py` signale les clés manquantes, et `pytest` échoue si une
langue est incomplète ou si un `{placeholder}` a disparu d'une traduction.

---

## Limites connues

- La détection visuelle capture l'**écran principal** uniquement.
- La source de capture créée automatiquement (`game_capture`) est propre à
  Windows.
- Le serveur overlay n'écoute que sur `127.0.0.1` : un OBS installé sur une
  autre machine ne peut pas l'atteindre.
- Twitch est la seule plateforme de chat, en lecture seule.
- Un lecteur de musique utilisé dans un navigateur apparaît sous le nom du
  navigateur, et sa forme d'onde suit tout le son de celui-ci.
- La forme d'onde montre une amplitude, pas un spectre de fréquences.
- Sans `pynput`, l'application démarre normalement, mais sans hotkeys ni
  raccourcis.

---

<div align="center">

<img src="assets/logo.png" alt="" width="32" height="32">

**OBS Dynamics** — pour les streamers qui préfèrent jouer que régler.

</div>
