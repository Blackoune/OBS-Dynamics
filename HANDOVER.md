# HANDOVER — OBS Dynamics

Document de reprise de projet. Lire intégralement avant toute modification.

---

## 1. Contexte & objectif

**OBS Dynamics** est une application Windows autonome (pas de serveur web, pas de navigateur) qui :
- surveille les processus actifs (jeux Steam ou exécutables manuels) via `psutil` ;
- confirme visuellement l'état du jeu (menu / en jeu) par template matching OpenCV ;
- bascule automatiquement les scènes OBS Studio via OBS WebSocket v5 (`simpleobsws`).

Le point d'entrée unique et à jour est **`obs_dynamics.py`** (monolithique, CustomTkinter).
Les fichiers `app.py`, `index.html`, `script.js`, `style.css`, `gui_launcher.py`, `config.json`,
`obs_config.json` sont des **reliquats de l'ancienne architecture FastAPI/web abandonnée**.
Ne pas les reprendre — voir §6.

---

## 2. État d'avancement actuel

| Composant | État |
|---|---|
| Détection processus (`psutil`) | ✅ Fonctionnel |
| Détection visuelle OpenCV (`_imread_unicode`, matchTemplate) | ✅ Fonctionnel |
| Client OBS WebSocket v5 (`simpleobsws`) | ✅ Fonctionnel, reconnexion manuelle via bouton Paramètres |
| Scan bibliothèques Steam (registre + VDF/ACF) | ✅ Fonctionnel |
| GUI CustomTkinter (Dashboard, Paramètres, modals) | ✅ Fonctionnel |
| Persistance `.env` (config OBS) + `data/games.json` (jeux) | ✅ Fonctionnel |
| Diff-based reconciliation des `GameCard` (anti-flicker) | ✅ Fonctionnel |
| Packaging PyInstaller (`build.spec`) | ✅ Spec présente, **script d'automatisation manquant** |
| **i18n (textes centralisés)** | 🔶 **Livré dans ce lot — pas encore câblé dans `obs_dynamics.py`** |
| Hotkeys dynamiques (cache-map, overlays) | ❌ Non implémenté (existait uniquement dans l'ancien frontend web) |
| Création auto de scènes/groupes/sources OBS à l'ajout d'un jeu | ❌ Non implémenté dans `obs_dynamics.py` (existait dans `app.py`/`script.js` legacy) |

---

## 3. Livrables de ce lot

```
i18n.json   → toutes les chaînes de caractères de l'UI, clé/valeur, structure {"fr": {...}}
i18n.py     → loader (classe I18n + fonction t(key, **kwargs)), fallback sûr si clé absente
HANDOVER.md → ce fichier
```

`i18n.json` couvre l'intégralité des textes actuellement codés en dur dans `obs_dynamics.py` :
sidebar, dashboard, cartes de jeu, modals (ajout/édition jeu), paramètres, messages d'erreur,
messages de log destinés à l'utilisateur. Convention de nommage des clés :
`SECTION_ELEMENT_ROLE` (ex: `GAME_MODAL_ERR_MISSING_NAME_EXE`).

Les placeholders utilisent `.format()` Python standard : `"{name}"`, `"{count}"`, `"{error}"`, etc.

---

## 4. Travail restant — PROCHAINE ÉTAPE OBLIGATOIRE

**`i18n.json` et `i18n.py` sont livrés mais `obs_dynamics.py` n'est pas encore modifié.**
Prochaine étape à réaliser dans l'ordre :

1. **Importer et initialiser** en tête de `obs_dynamics.py` (après les imports customtkinter) :
   ```python
   import i18n
   i18n.init(path=BASE_DIR / "i18n.json")
   from i18n import t
   ```
2. **Remplacer chaque chaîne littérale** par un appel `t("CLE")`. Repérer par recherche des
   patterns suivants dans le fichier actuel :
   - `text="..."` dans tous les `ctk.CTkLabel(...)`, `ctk.CTkButton(...)`
   - `self.title("...")` (App, GameModal)
   - `messagebox.askyesno("Confirmer", f"Supprimer '{game.name}' ?")` → `messagebox.askyesno(t("CONFIRM_DIALOG_TITLE"), t("CONFIRM_DELETE_GAME", name=game.name))`
   - toutes les f-strings de `status_lbl.configure(text=...)`, `msg_lbl.configure(text=...)`
   - `STATE_LABELS` dict → remplacer par des appels `t("GAME_STATE_...")` calculés à l'exécution
     (attention : ce dict est utilisé comme lookup statique, le convertir en fonction
     `def state_label(state): return t(f"GAME_STATE_{state.upper()}")` avec mapping explicite
     pour éviter les clés invalides)
   - `filedialog.askopenfilenames(title="Choisir des images PNG", filetypes=[("Images PNG", "*.png")])`
     → `title=t("GAME_MODAL_FILEDIALOG_TITLE")`, `filetypes=[(t("GAME_MODAL_FILEDIALOG_FILTER_LABEL"), "*.png")]`
3. **Ne PAS traduire les logs internes techniques** (`logger.exception(...)`, `logger.debug(...)`
   à visée développeur) — seuls les logs remontés à l'utilisateur via `LOG_*` dans `i18n.json`
   sont concernés.
4. **Copier `i18n.json` et `i18n.py` à la racine du projet** (même dossier que `obs_dynamics.py`),
   pas dans `data/` (qui est réservé aux données runtime).
5. **Mettre à jour `build.spec`** : ajouter `i18n.json` aux `datas` pour qu'il soit embarqué dans
   l'exécutable PyInstaller :
   ```python
   datas.append((str(ROOT / "i18n.json"), "."))
   ```
6. **Tester** : lancer `python obs_dynamics.py`, vérifier qu'aucune clé brute (ex: `GAME_MODAL_BTN_SAVE`
   au lieu de `💾 Enregistrer`) n'apparaît à l'écran — signe d'une clé manquante ou mal orthographiée.

---

## 5. Autres tâches en attente (hors périmètre de ce lot)

- **`build.py` / `build.bat`** : script d'automatisation PyInstaller (actuellement seul `build.spec`
  existe ; il faut le wrapper avec `py -m PyInstaller build.spec --noconfirm --clean` + gestion
  d'erreurs + copie du `.exe` final vers un dossier de sortie prévisible).
- **Hotkeys dynamiques** (cache-map Rust, overlays médias) : à réimplémenter nativement dans
  `obs_dynamics.py` (probablement via `pynput` pour l'écoute globale de touches + `simpleobsws`
  pour toggler des sources OBS). N'existe actuellement dans aucun fichier actif.
- **Création auto scène/groupe/sources OBS** à l'ajout d'un jeu (existait dans l'ancien `script.js`
  côté UI mais jamais implémentée côté `obs_dynamics.py`).

---

## 6. Fichiers legacy — NE PAS REPRENDRE

Ces fichiers appartiennent à l'ancienne architecture FastAPI + frontend web, abandonnée au profit
du monolithe CustomTkinter. Ils sont conservés dans le repo par accident/historique mais ne sont
plus exécutés :

- `app.py` (FastAPI, imports `core.config`/`core.obs_client` qui n'existent plus)
- `index.html`, `script.js`, `style.css`, `assets/`
- `gui_launcher.py` (lançait l'ancien serveur uvicorn)
- `config.json`, `obs_config.json` (remplacés par `.env`, désormais dans `.gitignore` avec mention
  explicite "legacy config files that historically held plaintext credentials")
- `install-pyenv-win.ps1` (utilitaire d'environnement, sans lien avec l'app)

**Recommandation** : les supprimer du repo une fois `obs_dynamics.py` confirmé stable en prod,
après avoir vérifié qu'aucun script de build/CI n'y fait référence.

---

## 7. Secrets & config

- Source de vérité unique : `.env` à la racine (clés `OBS_WS_HOST`, `OBS_WS_PORT`,
  `OBS_WS_PASSWORD`, `OBS_SCAN_INTERVAL_SECONDS`, `OBS_MATCH_THRESHOLD`).
- `.env` est gitignored. Ne jamais committer de mot de passe réel.
- Le mot de passe OBS WebSocket actuel en environnement de dev est visible dans
  `obs_automation.log` (log historique) — **à faire tourner (régénérer dans OBS) avant tout
  partage public du repo**, ce log ne devrait de toute façon pas être versionné (déjà dans
  `.gitignore` sous `obs_automation.log`).

---

## 8. Démarrage rapide pour reprise de dev

```powershell
cd C:\Users\Utilisateur\Documents\GitHub\Interface-de-Gestion-OBS-Dynamics
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # puis éditer OBS_WS_PASSWORD
python obs_dynamics.py
```

Prérequis OBS Studio : WebSocket Server activé (Outils > Paramètres WebSocket OBS), port 4455,
authentification activée avec le mot de passe reporté dans `.env`.
