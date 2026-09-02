# OBS Dynamics

Application Windows autonome qui bascule automatiquement les scènes OBS Studio
selon le jeu en cours et son état (menu ou en jeu).

Pas de serveur web, pas de navigateur, pas de PowerShell côté utilisateur :
un seul processus Python (ou un `.exe` packagé) avec une interface
CustomTkinter.

## Fonctionnement

1. **Détection de processus** (`psutil`) — repère qu'un jeu tourne. Les jeux
   Steam sont reconnus par leur dossier d'installation, ce qui reste fiable
   quand l'exécutable est renommé ; les jeux manuels par leur nom d'exe.
2. **Confirmation visuelle** (OpenCV `matchTemplate`) — compare l'écran à des
   captures de référence pour distinguer *menu* de *en jeu*.
3. **Bascule de scène** (OBS WebSocket v5 via `simpleobsws`) — applique la
   scène configurée pour l'état détecté.

Les hotkeys globales (`F1`/`F2`/`F3` par défaut) permettent de forcer un état
quand la détection visuelle se trompe.

## Raccourcis & Overlays

L'onglet **Raccourcis & Overlays** associe une combinaison de touches à un
média affiché dans OBS : masquer une carte en jeu, jouer un son, lancer une
courte vidéo.

1. **+ Ajouter un raccourci** → une ligne s'insère en haut de la liste.
2. Cliquer sur le champ de gauche, puis presser la combinaison voulue
   (`Ctrl + Shift + A`, `F5`…). `Échap` annule.
3. Choisir le type — Image, Vidéo ou Son — puis déposer ou parcourir le
   fichier.
4. Copier l'URL affichée sous la ligne et la coller dans OBS :
   **Sources → + → Navigateur → URL**.

Presser la combinaison déclenche alors le média sur cette source.

> L'application ouvre pour cela un petit serveur HTTP local
> (`http://127.0.0.1:4466` par défaut), lié à la boucle locale uniquement.
> C'est la seule façon d'alimenter une source navigateur OBS. Si le port est
> occupé, un port libre est choisi automatiquement — l'URL affichée dans
> l'onglet est toujours la bonne.
>
> Le glisser-déposer nécessite `tkinterdnd2` (`pip install tkinterdnd2`).
> Sans lui, la zone reste cliquable et ouvre le sélecteur de fichier.

## Installation

```bash
python -m venv venv
```

```bash
venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

Copier `.env.example` en `.env`, puis renseigner `OBS_WS_PASSWORD`.

Côté OBS Studio : **Outils → Paramètres du serveur WebSocket** → activer le
serveur, port `4455`, et reporter le mot de passe dans `.env`.

## Lancement

```bash
python obs_dynamics.py
```

## Configuration

Tout passe par `.env` à la racine. Les clés reconnues sont listées et
commentées dans [`.env.example`](.env.example) — toute autre clé est ignorée.

| Clé | Rôle | Défaut |
|---|---|---|
| `OBS_WS_HOST` / `OBS_WS_PORT` | Serveur OBS WebSocket | `localhost` / `4455` |
| `OBS_WS_PASSWORD` | Mot de passe WebSocket | *(vide)* |
| `OBS_SCAN_INTERVAL_SECONDS` | Période de scan, plancher 0.5 s | `2.0` |
| `OBS_MATCH_THRESHOLD` | Score OpenCV minimal, 0.0–1.0 | `0.8` |
| `OBS_APP_LANG` | Langue : `fr`, `en`, `es` | `fr` |
| `RAWG_API_KEY` | Jaquettes des jeux non-Steam (optionnel) | *(vide)* |

Les hotkeys se configurent dans `data/hotkeys.json` :

```json
{ "f1": "in_game", "f2": "menu", "f3": "inactive" }
```

Les états valides sont `inactive`, `active`, `menu` et `in_game`. Les noms de
touches suivent pynput, **en minuscule** (`f1`, `f5`, `k`…).

## Structure

```
obs_dynamics.py     application (GUI, scan, client OBS)
cover_service.py    téléchargement et cache des jaquettes
hotkeys.py          hotkeys globales et combinaisons (pynput)
triggers.py         règles « raccourci -> média »
overlay_server.py   serveur HTTP local des sources navigateur OBS
i18n.py / i18n.json traductions fr / en / es
build.py            packaging PyInstaller + validation i18n
build.spec          spécification PyInstaller
tests/              suite pytest
data/               runtime (games.json, hotkeys.json, triggers.json, covers/, logs) — gitignoré
```

## Développement

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest tests/ -q
```

Les tests couvrent la configuration `.env`, la persistance des jeux, le
parsing des bibliothèques Steam, la détection et l'i18n. Ils tournent sans
OBS, sans Steam et sans serveur graphique.

## Packaging

```bash
python build.py
```

Le script vérifie d'abord que chaque clé `t("...")` du code existe dans
`i18n.json` — un build ne peut donc pas produire un `.exe` affichant des clés
brutes à l'écran — puis lance PyInstaller et copie le binaire dans
`dist_release/`.

## Traductions

Ajouter une langue : dupliquer un bloc dans `i18n.json`, le traduire, et
ajouter son code à `SUPPORTED_LANGS` dans `i18n.py`. `python build.py` signale
les clés manquantes, et `pytest` échoue si une langue est incomplète ou si un
`{placeholder}` a disparu d'une traduction.

## Limites connues

- La détection visuelle compare l'écran **entier** : sur un setup multi-écrans,
  `ImageGrab.grab()` capture l'écran principal.
- La capture de jeu créée automatiquement utilise `game_capture`, source
  spécifique à Windows.
- Les hotkeys globales nécessitent `pynput` ; sans lui l'application démarre
  normalement, sans hotkeys.
