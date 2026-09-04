# HANDOVER — OBS Dynamics

Document de reprise de projet : état réel du code, décisions prises, et ce
qui reste ouvert. Pour l'installation et l'usage, voir [README.md](README.md).

> Mis à jour après le refactor `db597e7` (2026-09-02). La version précédente
> de ce document décrivait un état antérieur devenu faux (i18n annoncé non
> câblé, hotkeys annoncées non implémentées, renvois vers des fichiers legacy
> supprimés depuis). Elle reste consultable :
> `git show dd8cccd:HANDOVER.md`

---

## 1. Architecture

Application Windows autonome, **un seul point d'entrée** : `obs_dynamics.py`
(monolithe CustomTkinter).

```
obs_dynamics.py     GUI + scan + client OBS WebSocket v5
cover_service.py    jaquettes (Steam CDN / RAWG / Steam Store) + cache disque
hotkeys.py          hotkeys globales et combinaisons (pynput)
triggers.py         règles « raccourci -> média »
overlay_server.py   serveur HTTP + SSE des sources navigateur OBS
i18n.py / i18n.json fr / en / es, changement de langue à chaud
build.py            validation i18n bloquante + PyInstaller
tests/              87 tests pytest, sans OBS ni Steam ni serveur graphique
```

> **Le « pas de serveur web » n'est plus vrai.** L'en-tête d'`obs_dynamics.py`
> l'a longtemps affirmé, mais une source navigateur OBS consomme une URL :
> l'onglet Raccourcis & Overlays impose donc un serveur HTTP local. C'est un
> choix assumé, limité à `127.0.0.1` et bâti sur la seule stdlib
> (`http.server`) — aucune dépendance ajoutée.

L'ancienne architecture FastAPI + frontend web a été **entièrement supprimée**
(`_legacy_archive/`, `backend/`, `core/`). Ne pas la ressusciter : elle est
dans l'historique git si besoin d'archéologie.

---

## 2. État d'avancement

**Légende** — ✅ terminé et vérifié · 🟡 écrit, non vérifié en réel · ⬜ à faire · ⚠️ action requise de ta part

| Composant | Statut | Détail |
|---|:---:|---|
| Détection processus (`psutil`) | ✅ | Steam par dossier d'install, manuel par nom d'exe |
| Détection visuelle OpenCV | ✅ | `screen_match.py` : fragments ciblés, robuste 720p→4K |
| Stabilité de l'état détecté | ✅ | 2 lectures concordantes avant bascule, anti-oscillation |
| Contrôle du cadrage (aperçu numéroté) | ✅ | cadres épousant la forme de l'élément, refus par numéro |
| Décision pilotée par l'écran | ✅ | marge de 0,15 exigée : une quasi-égalité ne bascule rien |
| Recadrage manuel aux curseurs | ✅ | zones indépendantes, créées à un emplacement libre, compte stable |
| Validation mémorisée par image | ✅ | empreinte date+taille : redemandée seulement si l'image change |
| Client OBS WebSocket v5 | ✅ | `simpleobsws`, boucle asyncio dédiée |
| Scan bibliothèques Steam (registre + VDF/ACF) | ✅ | 8 tests sur le parsing |
| GUI (dashboard, paramètres, modals) | ✅ | grille responsive 2–8 colonnes |
| Pastille d'état des cartes | ✅ | Actif / Inactif seulement, #D93025 / #508267, sans coins |
| i18n fr/en/es | ✅ | 127 clés, changement de langue à chaud |
| Rotation des logs | ✅ | 2 Mo × 3 fichiers |
| Tests (269) + CI GitHub Actions | ✅ | `python -m pytest tests/ -q` |
| Packaging PyInstaller | ✅ | `python build.py`, icône incluse |
| Reconnexion OBS automatique | ✅ | validé en usage réel le 2026-09-03 |
| Jaquettes de jeux | ✅ | jaquette verticale officielle Steam, 6 jeux réels vérifiés |
| Hotkeys globales | ✅ | combinaisons acceptées (`ctrl+shift+f1`), 17 tests |
| Création auto scènes/sources OBS | ✅ | les scènes des menus déroulants priment sur les créées |
| Bascule automatique de scène | ✅ | corrigée le 2026-09-03, 13 tests dont 5 bout en bout |
| Édition d'un jeu sans doublon | ✅ | l'identité d'un jeu Steam n'est plus réécrite |
| Déclencheurs — modèle + persistance | ✅ | `triggers.py`, 8 tests |
| Déclencheurs — combinaisons clavier | ✅ | `ctrl+shift+a` normalisé, 7 tests |
| Déclencheurs — serveur overlay HTTP/SSE | ✅ | `overlay_server.py`, 8 tests bout en bout |
| Déclencheurs — onglet et interface | ✅ | validé en usage réel par l'utilisateur |
| Déclencheurs — durée + mode maintien | ✅ | validé en usage réel le 2026-09-03 |
| Déclencheurs — latence (préchargement) | ✅ | validé en usage réel le 2026-09-03 |
| Déclencheurs — glisser-déposer | ✅ | `tkinterdnd2` en dépendance, sous-arbre enregistré |
| Rotation du mot de passe OBS | ✅ | fait par l'utilisateur le 2026-09-02 |
| Identifiants hors du dépôt | ✅ | `.env` déplacé dans `%APPDATA%`, migration auto — voir §4 |
| Défilement de la grille | ✅ | scrollregion réparée, 60 px/cran, 11 fenêtres Tk/carte |
| Netteté des jaquettes | ✅ | pré-réduites en LANCZOS : +24 % de détail |
| Réactivité de la grille | ✅ | ajout d'un jeu : 2932 ms → 155 ms |
| Purge de l'historique git | ⚠️ | voir §4 — décision du propriétaire du dépôt |

---

## 3. 🟡 À valider en conditions réelles

Ces fonctionnalités compilent et passent les tests unitaires, mais n'ont pas
été éprouvées contre un vrai environnement (OBS lancé, jeux installés,
réseau, clavier). Elles restent marquées 🟡 tant que la case n'est pas cochée.

**Cocher la case et passer la ligne du §2 en ✅ une fois le test concluant.**

- [x] ✅ **Jaquettes** — validé le 2026-09-03 contre le vrai CDN Steam.
      Les 6 jeux testés (Portal 2, Hades, Elden Ring, Terraria, Hollow Knight,
      Factorio) récupèrent la jaquette verticale officielle `library_600x900_2x`
      en 600x900, donc redimensionnée sans aucun rognage. Hollow Knight et
      Factorio n'avaient pas d'appid : il est résolu par correspondance de nom
      exacte sur le Steam Store. Sans `RAWG_API_KEY`, les chemins Steam
      couvrent la majorité des cas.
      Deux défauts corrigés au passage :
      le `corner_radius=12` du label de jaquette faisait poser à CTkLabel un
      `padx` de 12 px — d'où deux bandes mortes sur les côtés et une image
      amputée d'autant ; et le service ne tournait qu'avec un seul thread de
      téléchargement, ce qui remplissait la grille jaquette par jaquette après
      un scan Steam (20 jaquettes : 0,84 s → 0,21 s avec 6 workers).
- [x] ✅ **Hotkeys** — validé le 2026-09-03. F1/F2/F3 forcent l'état des jeux
      actifs, et les **combinaisons** sont désormais acceptées
      (`{"ctrl+shift+f1": "in_game"}` dans `data/hotkeys.json`). L'ordre des
      modificateurs est normalisé à la lecture, une touche seule continue de
      marcher, et Ctrl+F1 ne déclenche plus l'action liée à F1 seul. Si
      `pynput` est absent, l'app démarre sans hotkeys et le journalise.
- [x] ✅ **Création de scènes** — validée par l'utilisateur le 2026-09-03.
      Les scènes sélectionnées dans les menus déroulants **priment** désormais
      sur celles créées automatiquement : la création ne remplit qu'un champ
      resté vide, elle n'écrase plus un choix explicite. Le libellé affiché
      quand OBS n'est pas joignable n'est plus enregistré comme nom de scène.

---

## 4. Sécurité

### Où vivent les identifiants (depuis le 2026-09-03)

`.env` **n'est plus dans le dossier du projet**. Il vit dans le profil de
l'utilisateur :

    %APPDATA%\OBS Dynamics\.env        (Windows)
    ~/.config/OBS Dynamics/.env         (Linux/macOS)

Motif : le mot de passe OBS WebSocket, la clé RAWG et tout identifiant de
compte ajouté plus tard disparaissaient à chaque purge d'historique, `git
clean` ou réinstallation, et il fallait tout resaisir. À cet emplacement ils
survivent à n'importe quelle manipulation du dépôt et ne peuvent
structurellement plus être committés.

La migration est automatique au démarrage : un `.env` trouvé à la racine du
dépôt est copié vers le nouvel emplacement puis renommé `.env.old` (rien n'est
supprimé). Toute clé de compte ajoutée à l'avenir (Twitch, etc.) doit passer
par `EnvConfigManager`, donc par ce fichier.


> ✅ **Rotation effectuée le 2026-09-02.** Le mot de passe ci-dessous a été
> remplacé ; l'alerte est conservée pour mémoire.
>
> ⚠️ **L'ancien mot de passe OBS WebSocket reste dans l'historique.**
> `obs_config.json` a été committé en clair aux commits `752b027` et
> `b8b760c`. Il a été retiré du HEAD, mais reste lisible dans l'historique
> d'un dépôt **public**.
>
> **Fait.** Ne jamais réutiliser l'ancien.

**Décision en suspens : purge de l'historique git.** Le `.git` pèse ~75 Mo à
cause d'un exécutable de 74 Mo committé en `dd8cccd`. Il a été dégitté du HEAD
mais subsiste dans les commits passés, avec le mot de passe. Le purger
demande `git filter-repo` + force-push, ce qui invalide tous les clones
existants. Non fait : cette décision revient au propriétaire du dépôt.

---

## 5. Décisions d'architecture à connaître

- **Le cache mtime** (`EnvConfigManager`, `GameStore`) existe parce que la
  boucle de scan appelait `load()` à chaque cycle, soit une relecture disque
  toutes les 2 s. Les deux retournent une **copie** : muter l'objet reçu ne
  pollue pas le cache. Deux tests verrouillent ce comportement.
- **Le contrat de threading de `cover_service`** : tous les callbacks passent
  par le `dispatch` fourni au constructeur (`App.post_ui`), cache hit compris.
  Ne jamais rappeler un callback en ligne — ça remettrait du Tk sur un thread
  non-UI, bug qui existait avant.
- **`GameCard.set_state()` doit rester O(1) et sans destruction de widget.**
  `apply_scan_results()` ne reconstruit la grille que si le *set* d'IDs
  change. Casser cette règle réintroduit le flicker et le reset du scroll.
- **Les noms de touches pynput sont en minuscule** (`Key.f1.name == "f1"`).
  L'implémentation précédente indexait sur `"F1"` et ne matchait jamais.
- **i18n** : `build.py` et `pytest` échouent si une clé `t("...")` manque, si
  une langue est incomplète, ou si un `{placeholder}` a disparu d'une
  traduction. Ne pas contourner ces garde-fous.
- **La page overlay précharge son média** et n'affiche que par bascule CSS
  (`visibility`). Réintroduire un `src` réassigné à chaque déclenchement (ou
  un paramètre anti-cache `?t=`) ramènerait un aller-retour HTTP + un
  redécodage avant le premier pixel : c'est exactement le délai que
  l'utilisateur a signalé sur le cas minimap.
- **La file SSE évince le plus ANCIEN événement quand elle sature.** Jeter le
  plus récent pourrait perdre un `hide` et laisser un overlay collé à l'écran
  par-dessus le jeu.
- **Le port du serveur doit rester stable** : il est écrit en dur dans les URL
  que l'utilisateur colle dans OBS. D'où le `OBS_OVERLAY_PORT` configurable,
  les tentatives répétées avant repli, et l'avertissement jaune dans l'onglet
  quand un repli a eu lieu.
- **Les icônes de navigation sont des widgets séparés** (colonne de largeur
  fixe), pas un préfixe dans la chaîne traduite : les emoji n'ont pas tous la
  même chasse et les libellés ne s'alignaient pas.

---

## 6. Limites connues

- La détection visuelle capture l'écran **principal** uniquement
  (`ImageGrab.grab()`), pas les configurations multi-écrans.
- `game_capture` est une source spécifique à Windows.
- `DETECT_SCALE = 0.5` est un compromis vitesse/précision. Si la détection
  rate des menus très fins, monter à `0.75` avant de toucher au seuil.
- Le glisser-déposer requiert `tkinterdnd2`, non déclaré en dépendance. Sans
  lui la zone reste cliquable et ouvre le sélecteur de fichier.
- Le serveur overlay n'écoute que sur `127.0.0.1` : une source navigateur sur
  une autre machine ne pourrait pas l'atteindre.

---

## 7. Démarrage rapide

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env   # puis renseigner OBS_WS_PASSWORD
python -m pytest tests/ -q
python obs_dynamics.py
```
