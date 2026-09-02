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
| Détection visuelle OpenCV | ✅ | 1 capture/cycle, templates cachés, downscale ×2 |
| Client OBS WebSocket v5 | ✅ | `simpleobsws`, boucle asyncio dédiée |
| Scan bibliothèques Steam (registre + VDF/ACF) | ✅ | 8 tests sur le parsing |
| GUI (dashboard, paramètres, modals) | ✅ | grille responsive 2–8 colonnes |
| i18n fr/en/es | ✅ | 127 clés, changement de langue à chaud |
| Rotation des logs | ✅ | 2 Mo × 3 fichiers |
| Tests (87) + CI GitHub Actions | ✅ | `python -m pytest tests/ -q` |
| Packaging PyInstaller | ✅ | `python build.py`, icône incluse |
| Reconnexion OBS automatique | 🟡 | backoff 2s → 60s — jamais vu se déclencher en réel |
| Jaquettes de jeux | 🟡 | code en place — voir §3 |
| Hotkeys globales | 🟡 | code en place — voir §3 |
| Création auto scènes/sources OBS | 🟡 | code en place — voir §3 |
| Déclencheurs — modèle + persistance | ✅ | `triggers.py`, 8 tests |
| Déclencheurs — combinaisons clavier | ✅ | `ctrl+shift+a` normalisé, 7 tests |
| Déclencheurs — serveur overlay HTTP/SSE | ✅ | `overlay_server.py`, 8 tests bout en bout |
| Déclencheurs — onglet et interface | ✅ | validé en usage réel par l'utilisateur |
| Déclencheurs — durée + mode maintien | 🟡 | ajouté après retour utilisateur, à revalider |
| Déclencheurs — latence (préchargement) | 🟡 | média préchargé, bascule CSS — à ressentir en jeu |
| Déclencheurs — glisser-déposer | 🟡 | nécessite `tkinterdnd2`, non installé ici |
| Rotation du mot de passe OBS | ✅ | fait par l'utilisateur le 2026-09-02 |
| Purge de l'historique git | ⚠️ | voir §4 — décision du propriétaire du dépôt |

---

## 3. 🟡 À valider en conditions réelles

Ces fonctionnalités compilent et passent les tests unitaires, mais n'ont pas
été éprouvées contre un vrai environnement (OBS lancé, jeux installés,
réseau, clavier). Elles restent marquées 🟡 tant que la case n'est pas cochée.

**Cocher la case et passer la ligne du §2 en ✅ une fois le test concluant.**

- [ ] 🟡 **Jaquettes** — lancer avec des jeux Steam en bibliothèque. Attendu :
      `data/covers/` se remplit, les cartes affichent les images à la place de
      l'emoji 🎮. Sans `RAWG_API_KEY`, seuls les chemins Steam (appid +
      recherche Store) sont actifs, ce qui couvre la majorité des cas.
- [ ] 🟡 **Hotkeys** — F1/F2/F3 doivent forcer l'état des jeux actifs. Attendu :
      la pastille de la carte change au cycle suivant. Si `pynput` est absent,
      l'app démarre sans hotkeys et le journalise (pas de crash).
- [ ] 🟡 **Création de scènes** — cocher la case dans le modal d'ajout avec OBS
      connecté. Attendu : `<jeu> - Menu` et `<jeu> - En jeu` apparaissent dans
      OBS avec une source `game_capture`, et sont présélectionnées dans le
      formulaire.
- [ ] 🟡 **Reconnexion OBS** — démarrer la surveillance, fermer OBS, le rouvrir.
      Attendu : le bandeau latéral repasse en « reconnecté » sans Stop/Start
      manuel, et les logs montrent les tentatives avec backoff croissant.
- [x] ✅ **Onglet Raccourcis & Overlays** — validé en usage réel (2026-09-02).
- [ ] 🟡 **Mode maintien** — régler une règle sur « Maintien », presser et
      garder la touche. Attendu : le média reste affiché tant que la touche
      est enfoncée et disparaît instantanément au relâchement.
- [ ] 🟡 **Réactivité** — enchaîner des appuis brefs et répétés (cas minimap).
      Attendu : aucun délai perceptible ni à l'affichage ni au masquage. Le
      média est préchargé, un déclenchement ne fait plus qu'un toggle CSS.
- [ ] 🟡 **Glisser-déposer de média** — `pip install tkinterdnd2`, puis
      déposer un fichier sur la zone. Sans ce paquet, la zone ouvre le
      sélecteur de fichier (chemin garanti, celui-là fonctionne).

---

## 4. Sécurité

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
