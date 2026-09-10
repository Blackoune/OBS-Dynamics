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
4. Choisir la **durée** d'affichage.
5. Copier l'URL affichée sous la ligne et la coller dans OBS :
   **Sources → + → Navigateur → URL**.

Presser la combinaison déclenche alors le média sur cette source.

### Durée et mode maintien

| Réglage | Comportement |
|---|---|
| **Maintien** | Affiché tant que la touche reste enfoncée, masqué au relâchement |
| 150 ms → 10 s | Affiché puis masqué automatiquement après ce délai |

Pour masquer une minimap, **Maintien** est le bon réglage : on appuie, on
consulte, on relâche — aucune minuterie à calibrer, et le retour suit
exactement le doigt.

Le média est préchargé au chargement de la page : un déclenchement ne coûte
qu'un basculement CSS, sans requête réseau ni redécodage. C'est ce qui rend
l'affichage et le masquage immédiats, même sur des appuis rapides et répétés.

> L'application ouvre pour cela un petit serveur HTTP local
> (`http://127.0.0.1:4466` par défaut, réglable via `OBS_OVERLAY_PORT`), lié
> à la boucle locale uniquement. C'est la seule façon d'alimenter une source
> navigateur OBS.
>
> Si le port est occupé, l'application réessaie brièvement puis se replie sur
> un port libre — et **le signale en jaune dans l'onglet**, car les URL déjà
> collées dans OBS pointent vers l'ancien port et ne répondent plus. L'URL
> affichée sous chaque ligne est toujours la bonne : il suffit de la recopier.
>
> Le serveur ne répond qu'aux requêtes dont l'hôte est `127.0.0.1`,
> `localhost` ou `::1` ; tout le reste reçoit un `403`. C'est ce qui empêche
> un site web ouvert dans ton navigateur de lire tes overlays en faisant
> pointer son domaine sur ta machine. Colle donc l'URL **telle qu'affichée**
> dans l'application : remplacer `127.0.0.1` par le nom de ton PC ne
> marchera pas.
>
> Le glisser-déposer nécessite `tkinterdnd2` (`pip install tkinterdnd2`).
> Sans lui, la zone reste cliquable et ouvre le sélecteur de fichier.

## Chat Twitch

L'onglet **Chat Twitch** affiche le chat de ta chaîne dans OBS, par une source
navigateur.

1. Carte Twitch → **Connexion** → tape le nom de ta chaîne → *Valider*.
2. Copie le lien affiché en bas de l'onglet.
3. Dans OBS : **Sources → + → Navigateur → URL**.

C'est tout. Aucun compte, aucune clé, aucune application à déclarer : le chat
public d'une chaîne Twitch se lit en **IRC anonyme**. Un compte ne serait
nécessaire que pour écrire ou modérer, ce que cette version ne fait pas.

L'interrupteur de la carte masque le chat dans l'overlay **sans couper la
connexion** : le réafficher est instantané.

> **Pourquoi seulement Twitch ?** L'onglet a porté un temps YouTube, Kick et
> TikTok. Tout a été retiré le 2026-09-08 : Twitch était la seule plateforme à
> fonctionner de façon fiable. Le détail des impasses est dans
> [HANDOVER.md](HANDOVER.md) §6.

### Le lien overlay

En bas de l'onglet, une URL de la forme
`http://127.0.0.1:4466/chat/<jeton>`. Copie-la comme source navigateur dans
OBS. Elle **reste valide après un redémarrage** : le jeton est créé une seule
fois puis relu dans `%APPDATA%\OBS Dynamics\data\multistream.json`, et le serveur démarre avec
l'application, sans action manuelle.

*Régénérer le lien* fabrique un nouveau jeton et **tue l'ancien** : la source
déjà configurée dans OBS cessera de répondre et devra être recollée. À
n'utiliser que si tu penses que l'URL a fuité.

Si l'application est fermée, la page affiche « En attente de connexion » et se
reconnecte d'elle-même dès son redémarrage. Une source ouverte **avant** le
lancement de l'application affichera en revanche l'erreur d'OBS jusqu'à un
rafraîchissement : coche « Actualiser le navigateur quand la scène devient
active » dans les propriétés de la source.

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

Le fichier vit dans `%APPDATA%\OBS Dynamics\`, **hors du dépôt** : il ne peut
structurellement pas partir sur GitHub. Le mot de passe OBS et la clé RAWG y
sont en plus **chiffrés** (DPAPI, clé dérivée de ton compte Windows) — le
fichier copié ailleurs ou lu par un autre compte ne donne rien. Tu peux
saisir la valeur en clair : elle est chiffrée au démarrage suivant.

### Où vivent tes données

Bibliothèque de jeux, jaquettes, hotkeys, déclencheurs et journaux vivent tous
dans `%APPDATA%\OBS Dynamics\data\`, à côté du `.env`. Cet emplacement ne
dépend pas d'où le programme est lancé : `Dynamics.exe` et
`python obs_dynamics.py` lisent et écrivent exactement les mêmes fichiers.

Une bibliothèque restée dans `<dépôt>/data/` (installations d'avant le
2026-09-10) est reprise automatiquement au premier démarrage ; l'ancien
dossier est renommé `data.old` pour qu'il ne subsiste qu'une seule source.

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

Les hotkeys se configurent dans `%APPDATA%\OBS Dynamics\data\hotkeys.json` :

```json
{ "f1": "in_game", "f2": "menu", "f3": "inactive" }
```

Les états valides sont `inactive`, `active`, `menu` et `in_game`. Les noms de
touches suivent pynput, **en minuscule** (`f1`, `f5`, `k`…).

## Structure

```
obs_dynamics.py     point d'entrée : fenêtre, navigation, câblage
app_paths.py        chemins, journalisation, éveil DPI
env_config.py       lecture / écriture du .env utilisateur
games.py            scan Steam, modèle Game, persistance
detection.py        processus + comparaison visuelle (sans interface)
obs_client.py       WebSocket OBS v5 et boucle de scan
screen_match.py     agent de comparaison écran / référence
ui_common.py        palette, police, libellés d'état, glisser-déposer
ui_dashboard.py     grille de cartes de jeu
ui_game_dialogs.py  fiche de jeu et relecture des patchs
ui_settings.py      vue Paramètres
ui_triggers.py      vue Raccourcis & Overlays
ui_twitch_chat.py   vue Chat Twitch
cover_service.py    téléchargement et cache des jaquettes
hotkeys.py          hotkeys globales et combinaisons (pynput)
triggers.py         règles « raccourci -> média »
twitch_chat.py      connecteur de chat Twitch (IRC anonyme) + hub de diffusion
overlay_server.py   serveur HTTP local des sources navigateur OBS
i18n.py / i18n.json traductions fr / en / es
build.py            packaging PyInstaller + validation i18n
build.spec          spécification PyInstaller
tests/              suite pytest
make_shortcut.py    raccourci « Dynamics » sur le Bureau
```

Le point d'entrée était un fichier unique de 4177 lignes jusqu'au 2026-09-09.
Il ré-exporte les noms publics des modules ci-dessus : `import obs_dynamics`
donne toujours accès à `Game`, `DashboardView`, `detect_game_state`, etc.

## Développement

```bash
pip install -r requirements-dev.txt
```

```bash
git config core.hooksPath .githooks
```

Cette seconde commande active les hooks versionnés. À faire **une fois par
clone** : git ne clone pas `.git/hooks/`.

- `pre-commit` refuse tout commit contenant un identifiant
  (`check_secrets.py`). Audit ponctuel : `python check_secrets.py --all`.
  Contourner une fausse alerte : `git commit --no-verify`.
- `post-commit` et `post-merge` reconstruisent `dist_release/Dynamics.exe` en
  arrière-plan dès qu'un commit ou un pull touche un `.py`, `build.spec`,
  `i18n.json`, `requirements.txt` ou `assets/`. Le raccourci du Bureau pointe
  vers ce fichier : il reste valide sans rien refaire. Verdict du rebuild dans
  `build_auto.log` (gitignoré).

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
`dist_release/Dynamics.exe` (icône : `assets/icon.ico`).

Ce build manuel n'est utile que pour forcer une reconstruction hors commit :
les hooks `post-commit` / `post-merge` ci-dessus le lancent déjà tout seuls.
Windows verrouille un `.exe` en cours d'exécution — si Dynamics tourne, le
build s'arrête avec un message explicite et l'ancienne version reste en place.

Raccourci Bureau : `python make_shortcut.py` crée « Dynamics » sur le Bureau,
pointant vers `dist_release/Dynamics.exe`. À faire une seule fois.

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
