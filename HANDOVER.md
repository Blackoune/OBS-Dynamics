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

Application Windows autonome, **un seul point d'entrée** : `obs_dynamics.py`.

```
obs_dynamics.py     point d'entrée : fenêtre, Sidebar, App, câblage
app_paths.py        chemins, journalisation, éveil DPI (stdlib seule)
env_config.py       lecture / écriture du .env utilisateur
games.py            scan Steam, modèle Game, persistance atomique
detection.py        processus + comparaison visuelle, SANS interface
obs_client.py       WebSocket OBS v5 (simpleobsws) + boucle de scan
screen_match.py     agent de comparaison écran / référence
ui_common.py        palette, police, libellés d'état, glisser-déposer
ui_dashboard.py     GameCard + DashboardView
ui_game_dialogs.py  GameModal + PatchReviewDialog
ui_settings.py      SettingsView + LanguageSegmentedControl
ui_triggers.py      TriggerRow + TriggersView
ui_twitch_chat.py   ConnectionDialog + TwitchChatCard + TwitchChatView
cover_service.py    jaquettes (Steam CDN / RAWG / Steam Store) + cache disque
hotkeys.py          hotkeys globales et combinaisons (pynput)
triggers.py         règles « raccourci -> média »
twitch_chat.py      connecteur de chat Twitch (IRC anonyme) + hub de diffusion
overlay_server.py   serveur HTTP + SSE des sources navigateur OBS
                    (overlays de déclencheurs ET overlay du chat Twitch)
i18n.py / i18n.json fr / en / es, changement de langue à chaud
build.py            validation i18n bloquante + PyInstaller
secret_store.py     chiffrement DPAPI des identifiants au repos
tests/              380 tests pytest, sans OBS ni Steam ni serveur graphique
```

> **`obs_dynamics.py` a été découpé le 2026-09-09.** C'était un monolithe de
> 4177 lignes et 26 classes, soit 57 % du code Python du projet. Il en reste
> 584 : le shell de l'application. Le plus gros module est désormais
> `ui_game_dialogs.py` (785 lignes). Le point d'entrée **ré-exporte** les noms
> publics de tous ces modules, donc `import obs_dynamics` donne toujours
> `Game`, `DashboardView`, `detect_game_state`… et aucun test n'a eu à changer
> d'import.

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
| i18n fr/en/es | ✅ | 180 clés, changement de langue à chaud |
| Rotation des logs | ✅ | 2 Mo × 3 fichiers |
| Tests (314) + CI GitHub Actions | ✅ | `python -m pytest tests/ -q` |
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
| Chat Twitch — onglet, carte, persistance | ✅ | `TwitchChatStore`, interrupteur, fenêtre de connexion |
| Chat Twitch — lien overlay permanent | ✅ | jeton créé une fois puis relu ; URL identique après redémarrage, vérifié |
| Chat Twitch — connecteur IRC anonyme | ✅ | sans identifiant ; connexion réelle vérifiée le 2026-09-06 |
| Chat Twitch — réception de messages | 🟡 | boucle de lecture couverte par 4 tests à socket simulée, pas encore observée sur un vrai chat animé |
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
- [ ] 🟡 **Chat Twitch — réception de messages** — la connexion elle-même est
      vérifiée (socket TLS, handshake, JOIN, état vert). La boucle de lecture
      est couverte par quatre tests à socket simulée qui reproduisent les cas
      pièges réels : ligne coupée entre deux paquets TCP, PING à ne pas
      republier, `366` de fin de JOIN. Il reste à confirmer sur un chat animé
      que les messages s'affichent bien dans l'overlay.

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

### Chiffrement au repos (depuis le 2026-09-09)

✅ `OBS_WS_PASSWORD` et `RAWG_API_KEY` ne sont plus écrits en clair. Ils
passent par `secret_store.encrypt()` à l'enregistrement et
`secret_store.decrypt()` à la lecture, sous la forme `enc:v2:<base64 du blob
DPAPI scellé avec entropie secondaire>`. `EnvConfigManager.encrypt_secrets_at_rest()`, appelée une fois dans
`App.__init__`, reprend un `.env` hérité — sinon un mot de passe déjà saisi
ne serait chiffré qu'au prochain passage dans l'onglet Paramètres, donc
peut-être jamais. Elle ne réécrit rien s'il n'y a rien à chiffrer.

**Ce que ça protège :** la clé DPAPI vient du compte Windows. Le fichier copié
sur une clé USB, envoyé par mail, remonté dans une sauvegarde ou lu par un
autre compte de la même machine ne donne rien.

**Ce que ça ne protège pas :** un programme lancé sous la session de
l'utilisateur peut appeler `CryptUnprotectData` exactement comme nous. C'est
une limite de DPAPI, pas un défaut d'implémentation — sans mot de passe maître
redemandé à chaque démarrage, aucun stockage local ne fait mieux. Depuis le
format `enc:v2:`, l'appel doit fournir l'entropie secondaire `_ENTROPY` : un
outil qui ratisse un profil Windows à l'aveugle ne déchiffre plus nos blobs,
mais un programme qui vise cette application la lit dans le `.exe`.

**À ne pas casser :** un blob illisible rend `""`, jamais le blob. Renvoyer le
blob l'enverrait tel quel à OBS comme mot de passe, et le vrai motif — « ce
fichier vient d'un autre compte » — n'apparaîtrait nulle part. Le préfixe est
versionné, et `enc:v1:` (sans entropie) reste **lu** : un `.env` d'avant la
mise à jour doit survivre, sinon le mot de passe de l'utilisateur disparaît.
`needs_rewrite()` dit ce qui reste à convertir ; la conversion se fait au
démarrage, une fois.

✅ **Le jeton de chat (`data/multistream.json`) est chiffré au repos lui
aussi** (2026-09-09), par le même `secret_store`. `TwitchChatStore` chiffre
dans `to_dict()`, déchiffre dans `from_dict()`, et `ensure_token()` convertit
un fichier hérité au démarrage — **sans changer la valeur du jeton**, sinon la
source navigateur déjà collée dans OBS mourrait à la mise à jour.

⬜ **Pas de rotation automatique du jeton, délibérément.** Elle casserait cette
même source OBS à chaque cycle, sans prévenir. *Régénérer le lien* reste le
geste manuel, pour le cas où l'URL a été montrée à l'écran.


> ✅ **ROTATION FAITE ET VÉRIFIÉE le 2026-09-09.** Le mot de passe publié
> n'ouvre plus rien.
>
> Historique du dossier : la section affirmait « Rotation effectuée le
> 2026-09-02 », ce qui était faux — le 2026-09-09, le `.env` portait encore
> le SHA-256 `e934dd0dec1c18ad…`, celui de `752b027`. La rotation réelle a eu
> lieu ce jour-là, côté OBS Studio, puis le `.env` a été resynchronisé sur
> cette nouvelle valeur.
>
> Vérifications faites, dans cet ordre :
>
> ```
> config obs-websocket : mot de passe != valeur fuitée
> .env                 : identique à OBS, != valeur fuitée, format enc:v2:
> connexion réelle     : OK — OBS 32.2.2 | websocket 5.7.4
> ```
>
> `obs_config.json` porte encore la valeur en clair dans **`752b027`**
> (2026-08-04) et **`2873229`**, sur un dépôt **public**
> (`github.com/tristanbest0802-beep/Interface-de-Gestion-OBS-Dynamics`). Elle
> y restera : purger l'historique n'atteint pas les clones et forks déjà
> faits. Ce qui la rend inoffensive, c'est qu'elle n'ouvre plus rien.
>
> *(Une version antérieure de cette section citait `b8b760c` : ce commit touche
> bien le fichier, mais son contenu n'y porte pas cette valeur. Les deux
> commits ci-dessus ont été retrouvés en comparant le SHA-256 de chaque
> chaîne de chaque révision du fichier.)*
>
> Son SHA-256 reste dans `check_secrets.py` : il n'y est pas comme alerte,
> mais comme garde-fou — si cette valeur repasse un jour dans un commit, elle
> est refusée. Ne jamais la réutiliser, ni ici ni ailleurs.

**Décision en suspens : purge de l'historique git.** Le `.git` pèse 75 Mo, dont
73,4 Mo pour un seul blob : `release/OBSDynamics.exe`. Il a été dégitté du HEAD
mais subsiste dans les commits passés, avec le mot de passe. La purge réécrit
tous les SHA et invalide tous les clones existants : elle revient au
propriétaire du dépôt, et vient **après** la rotation, jamais avant.

**Procédure répétée à blanc le 2026-09-09** sur un clone jetable, résultat
vérifié : 27 commits conservés, `obs_config.json` et `config.json` absents de
tous les arbres, et le SHA-256 du mot de passe introuvable dans
`git log --all -p`. Ce qui reste à faire le jour J :

```
pip install git-filter-repo
git filter-repo --invert-paths \
    --path obs_config.json --path config.json --path release/OBSDynamics.exe
git remote add origin https://github.com/tristanbest0802-beep/Interface-de-Gestion-OBS-Dynamics.git
git push --force --all && git push --force --tags
```

- `filter-repo` **retire le remote exprès**, pour qu'un force-push ne parte
  pas par accident : il faut le remettre à la main. Ce n'est pas un bug.
- Travailler à un chemin court. La répétition à blanc a échoué au `git gc`
  final sur `Filename too long` : le scratchpad fait ~150 caractères, alors
  que le dépôt réel en fait 72 — largement sous la limite Windows de 260.
  La réécriture elle-même avait réussi ; seul le repack a calé.
- Prévenir avant de pousser : tout clone ou fork existant devient
  irréconciliable et devra être recloné.
- La purge **ne rend pas le mot de passe inoffensif**. Le dépôt est public
  depuis le 2026-08-04 ; ce qui le neutralise est la rotation, pas l'oubli.

### Le garde-fou anti-secret (depuis le 2026-09-09)

✅ `check_secrets.py`, branché en hook `pre-commit` via `.githooks/`, refuse
tout commit qui embarquerait un identifiant. Il inspecte le contenu **indexé**
(`git show :<fichier>`), pas le fichier de travail : c'est ce qui part
réellement dans le commit. Il bloque trois choses :

1. les chemins qui ne se versionnent jamais (`.env`, `.env.old`,
   `obs_config.json`, `config.json`, tout `data/`) ;
2. les secrets connus comme ayant fuité, reconnus par leur **SHA-256** — la
   valeur n'est jamais écrite dans le dépôt, ce qui la republierait ;
3. les affectations en clair (`OBS_WS_PASSWORD=…`, `"password": "…"`), en
   laissant passer les valeurs vides, les gabarits (`changeme`) et tout ce qui
   commence par `enc:v2:` ou `enc:v1:`.

À activer une fois par clone — git ne clone pas `.git/hooks/` :

```
git config core.hooksPath .githooks
```

Audit ponctuel de tout le HEAD : `python check_secrets.py --all`.

`data/hotkeys.json` est exempté nommément : suivi depuis avant que `data/`
n'entre au `.gitignore`, il ne contient que les touches par défaut. Sans cette
exemption, `--all` sortirait en erreur à chaque audit — et un contrôle qui
crie au loup finit désactivé.

### Le serveur overlay (depuis le 2026-09-09)

✅ **Rebinding DNS fermé.** Écouter sur `127.0.0.1` ne suffit pas : un site
web visité par l'utilisateur peut faire pointer son propre domaine sur
`127.0.0.1`, et le navigateur traite alors nos réponses comme same-origin
avec la page de l'attaquant. `_Handler.do_GET` refuse désormais (403) toute
requête dont l'en-tête `Host` ne désigne pas la boucle locale, **avant** tout
routage — `/health` compris, sinon la sonde confirmerait à un tiers que
l'application tourne. Le contrôle porte sur le nom, jamais sur le port : le
serveur bascule sur un port libre quand 4466 est pris.

✅ **`Origin` étrangère refusée.** Une `Origin` présente ne peut venir que
d'un fetch / XHR / EventSource ; les nôtres partent toujours d'une page servie
par ce serveur. Une navigation directe, un `<img>` ou une source navigateur
OBS n'envoient pas d'`Origin` — ce cas reste accepté.

✅ **`X-Content-Type-Options: nosniff` sur toutes les réponses.** `/media`
sert un fichier choisi par l'utilisateur avec un type deviné par
`mimetypes` ; sans nosniff, un fichier mal typé pourrait être interprété
comme du HTML par Chromium, donc exécuté dans l'origine de l'overlay, aux
côtés du chat et des déclencheurs.

⬜ **Pas de jeton séparé sur `/overlay`, `/events` et `/media`, et c'est
délibéré.** Le `rule_id` EST déjà la capacité : `uuid.uuid4().hex`, 122 bits.
Un second secret vivrait exactement aux mêmes endroits que lui — la même URL
collée dans OBS, le même `data/triggers.json`, le même écran partagé en
direct — donc il ne défendrait contre aucune fuite que l'`uuid` ne subit
déjà, et il invaliderait toutes les sources navigateur déjà configurées. Ce
qui protège réellement ces trois routes, c'est le contrôle `Host` ci-dessus,
qui s'applique à elles comme aux autres. Douze tests verrouillent l'ensemble
(`tests/test_triggers.py`, section « Rebinding DNS et origines étrangères »).

---

## 5. Décisions d'architecture à connaître

- **`App._on_close()` est réentrante et doit le rester.** Tk relaie
  `WM_DELETE_WINDOW` à chaque clic sur la croix, et l'arrêt prend quelques
  secondes (déconnexion OBS, arrêt des threads) : un second passage est
  possible, et le deuxième `self.destroy()` lèverait « application has been
  destroyed ». La garde `self._closing` vaut mieux qu'un `try/except` chez
  chaque appelant.
- **`_on_close()` désinscrit le listener i18n.** `i18n` garde ses listeners
  dans une liste de module, qui survit à la fenêtre : sans
  `off_change(self._on_lang_changed)`, chaque `App` fermée laisse un callback
  qui rappellera `refresh_labels()` sur des widgets détruits au prochain
  changement de langue. Découvert par `tests/test_app_shell.py`.
- **Le graphe d'imports des modules est un DAG, et doit le rester.**
  `app_paths` et `ui_common` ne dépendent d'aucun autre module du projet ;
  `detection` ne dépend pas de l'interface (il tourne sans serveur
  graphique) ; les vues dépendent des modules métier, jamais l'inverse. Un
  import ajouté à contre-sens créerait un cycle que Python signalerait au
  démarrage.
- **Les vues reçoivent `ctk` depuis `ui_common`, pas par `import
  customtkinter`.** `ui_common` appelle `enable_dpi_awareness()` juste avant
  d'importer CTk. Écrire `import customtkinter as ctk` directement dans une
  vue rendrait l'ordre dépendant de l'ordre alphabétique des imports, et
  ferait revenir la fenêtre trop petite pour son contenu.
- **`detection.py` n'importe pas `ui_common`.** Les libellés et couleurs
  d'état (`state_label`, `badge_text`, `STATE_BADGE_BG`) ont été déplacés
  dans `ui_common` exactement pour ça : sans cette séparation, tester la
  détection tirerait tout CustomTkinter.
- **Un test qui remplace `is_game_active`, `_capture_screen_bgr` ou
  `_best_match_score` doit viser `app_module.detection`, pas `app_module`.**
  `detect_game_state` résout ces noms dans les globales de `detection` ;
  patcher le ré-export d'`obs_dynamics` ne ferait rien, silencieusement.
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
  traduction. Ne pas contourner ces garde-fous. Les deux balaient **tous** les
  `*.py` de la racine depuis le découpage — ne scanner que `obs_dynamics.py`
  laisserait passer les libellés des vues, qui sont la majorité.
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
- **Le chat réutilise le serveur overlay existant**, il n'ouvre pas de
  second port. Un deuxième serveur doublerait les risques de conflit de port
  et donnerait une deuxième URL à surveiller, alors que celui-ci démarre déjà
  avec l'application sur un port stable — exactement ce qu'exige un lien
  permanent.
- **Le jeton du lien chat est créé une seule fois** puis relu dans
  `data/multistream.json`. Le régénérer à chaque démarrage tuerait la source
  navigateur déjà collée dans OBS à chaque lancement.
- **`data/multistream.json` garde son nom alors que tout le code s'appelle
  désormais `twitch_chat`.** Le renommage du 2026-09-09 s'est arrêté à la
  porte du fichier de données, volontairement : pointer vers un fichier au
  nouveau nom en trouverait un vide, donc en régénérerait le jeton, et la
  source navigateur déjà collée dans OBS cesserait de répondre. Le seul cas
  où le renommer serait acceptable est une migration qui recopie l'ancien
  fichier avant de lire le nouveau — et elle ne vaut pas son risque pour un
  nom de fichier que l'utilisateur ne voit jamais.
- **`/media` est servi par morceaux de 64 Kio, pas par `read_bytes()`.** Un
  chargement complet en mémoire demandait autant de RAM que la taille du
  fichier, et rien n'empêche l'utilisateur de choisir une vidéo de plusieurs
  gigaoctets. La boucle n'envoie jamais plus que la taille annoncée par
  `stat()` : un fichier qui grossirait pendant le transfert produirait sinon
  plus d'octets que ne le dit `Content-Length`.
- **Le filtre d'affichage par plateforme est appliqué côté serveur**, dans
  `ChatHub.publish`. Masquer côté page laisserait les messages filtrés
  atteindre le DOM de la source navigateur — visible dans l'inspecteur, et
  inutilement transmis.
- **L'interrupteur d'une carte ne coupe pas le connecteur.** `apply_config`
  ne recrée un connecteur que si sa configuration a changé : basculer
  l'affichage garde la session ouverte, donc le retour est instantané et ne
  paie pas une reconnexion.
- **Le chat s'affiche par `textContent`, jamais par `innerHTML`.** Un message
  de chat est du texte hostile par nature ; un test verrouille l'absence
  d'`innerHTML` dans la page servie.
- **Une session revenue SANS exception ne compte pas comme un échec** :
  le backoff repart de zéro et l'état posé par la session est conservé. Avant,
  chaque cycle de 30 s repassait par « Non connecté » puis « Connexion en
  cours », et l'attente grimpait à plus d'une minute pour une chaîne
  simplement hors antenne.

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
- **Kick et TikTok ont été retirés le 2026-09-08.** L'onglet les a
  portés deux jours ; aucun n'a fonctionné de façon fiable, et le coût de les
  garder dépassait leur apport. Ce qui a été essayé, et pourquoi ça a échoué :
  - **Kick** — lecture du chat public par WebSocket Pusher, sans compte. Le
    chemin fonctionnait en test mais n'est PAS documenté par Kick : rien ne
    garantissait qu'il tienne.
  - **TikTok** — aucune API officielle de chat en direct. Les bibliothèques
    existantes rejouent le protocole interne de l'application mobile et
    cassent à chaque changement côté TikTok. Jamais implémenté.
  La structure du hub reste multi-plateforme (`PLATFORMS`, filtre et couleur
  par plateforme) : rebrancher une plateforme ne demanderait qu'un connecteur,
  pas un redécoupage.
- **Une source navigateur ouverte dans OBS avant le lancement de
  l'application** affiche l'erreur d'OBS jusqu'à un rafraîchissement : rien ne
  peut servir une page de repli quand aucun serveur n'écoute. Une fois la page
  chargée, en revanche, elle survit à un redémarrage de l'application — elle
  bascule sur « En attente de connexion » et se reconnecte seule.

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
