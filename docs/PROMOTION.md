# Kit de promotion OBS Dynamics

Textes prêts à coller, dans l'ordre du plan. Règle commune : le GIF en premier,
le lien ensuite, et une réponse à chaque commentaire le jour même.

Tant que la signature SignPath n'est pas active, ne pas écrire « signé » :
dire « build public par GitHub Actions, empreinte SHA-256 publiée ».

---

## 1. Release

Fait. v1.0.3 publiée, et le bouton du site
(`/installation/`) pointe sur `releases/latest/download/OBS-Dynamics-Setup.exe` :
le site lit la dernière release à chaque build.

---

## 2. Vidéo de démo

Fait. Motion design de 23 s construit uniquement avec de vraies captures du
logiciel (jetons d'overlay et pseudo en jeu floutés) :

- `docs/promo/demo_fr.mp4` et `docs/promo/demo_en.mp4` (1080p, pour Reddit, YouTube Shorts, Discord) ;
- `docs/promo/demo.gif` (800 px, en tête du README).

Pas dans `assets/` : ce dossier est embarqué dans l'exe.

---|---|
| 0 à 3 s | Lancement du jeu. OBS Dynamics affiche le jeu détecté. |
| 3 à 7 s | Menu du jeu. OBS passe seul sur « `<jeu> - Menu` ». |
| 7 à 12 s | Lancement de la partie. OBS passe seul sur « `<jeu> - En jeu` ». |
| 12 à 16 s | Touche maintenue : la minimap est masquée dans le stream. |
| 16 à 20 s | Retour au menu, bascule automatique inverse. |

Enregistrer avec OBS en `.mp4`, puis convertir en GIF léger (moins de 10 Mo
pour GitHub) avec ffmpeg :

```bash
ffmpeg -i demo.mp4 -vf "fps=15,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse" -loop 0 assets/demo.gif
```

Ensuite, l'insérer sous le titre du README :

```markdown
![OBS Dynamics bascule les scènes OBS selon le jeu](assets/demo.gif)
```

Pour Reddit et les réseaux, garder le `.mp4` : la vidéo passe mieux qu'un GIF.

---

## 3. Description et topics du dépôt

Fait le 17 octobre 2026 (topics streamer-tools, stream-tools, opencv ajoutés).

Sur GitHub, roue dentée à côté de « About ».

Description :

> Automatic OBS scene switcher for Windows: detects the game and what is on screen, then switches scenes. Free, 100% local, no account.

Website : `https://blackoune.github.io/`

Topics :

```
obs obs-studio obs-websocket streaming streamer-tools stream-tools twitch scene-switcher opencv python windows game-detection overlay
```

Ajouter `valorant` (ou le jeu de la démo) si la démo l'utilise.

---

## 4. Reddit

Lire d'abord les règles de chaque sous-forum (jour autorisé pour l'auto-promo,
flair à choisir). Un seul post pour commencer : r/obs. Attendre les retours
avant r/Twitch, r/streaming, r/Twitch_startup, r/SideProject, r/Python.

### r/obs (anglais)

**Titre :**
I made a free tool that switches OBS scenes by itself based on what's on screen (no plugin, 100% local)

**Corps :**

> [vidéo de démo]
>
> I got tired of switching between my "Menu" and "In game" scenes by hand mid-stream, so I built OBS Dynamics.
>
> How it works:
> - It sees which game is running (Steam games are found automatically).
> - It compares the screen to a few reference screenshots you take (menu, in game, map...).
> - It tells OBS to switch scene through the built-in WebSocket v5. No plugin to install.
>
> It can also create the scenes for you, and it has a hold-to-hide hotkey to mask your minimap (handy against stream snipers), a Twitch chat overlay and a "now playing" music widget.
>
> Free, open source (GPL-3.0), Windows 10/11, no account, no telemetry, 39 languages.
>
> Download and source: https://github.com/Blackoune/OBS-Dynamics
>
> It's my first public release, so I'd really like to know what breaks or what's missing for your setup.

### r/Python (angle technique, plus tard)

**Titre :**
OBS Dynamics: detecting game states with OpenCV template matching to drive OBS scenes

**Corps :**

> Small Windows desktop app in Python: psutil finds the running game, OpenCV `matchTemplate` compares screen regions to user reference shots, and simpleobsws drives OBS through WebSocket v5. Two matching reads in a row and a 0.15 margin between the best and second state are required before a switch, so near-ties change nothing. Built into a single exe with PyInstaller in GitHub Actions, pytest + pip-audit in CI.
>
> Code: https://github.com/Blackoune/OBS-Dynamics. Feedback on the detection approach welcome.

---

## 5. Discord (bêta-test)

Poster dans le salon prévu pour les outils ou l'auto-promo, jamais en général.

### Français

> Bonjour à tous. Je cherche quelques streamers pour tester un logiciel gratuit que j'ai développé : OBS Dynamics. Il détecte votre jeu et ce qui est à l'écran (menu, partie, carte...) et change la scène OBS tout seul. Aucun plugin, aucun compte, tout reste sur votre PC.
>
> Je vous aide à configurer votre premier jeu (environ 15 minutes). En échange, j'aimerais votre avis honnête.
>
> Lien : https://blackoune.github.io/
> Répondez ici ou en message privé si ça vous intéresse.

### English

> Hi all. I'm looking for a few streamers to test a free tool I built: OBS Dynamics. It detects your game and what's on screen (menu, match, map...) and switches your OBS scene by itself. No plugin, no account, everything stays on your PC.
>
> I'll help you set up your first game (about 15 minutes). In return I'd love your honest feedback.
>
> Link: https://blackoune.github.io/en/
> Reply here or DM me if you're interested.

---

## 6. Prospection directe (20 à 30 petits streamers)

Cible : 10 à 200 viewers, changent visiblement de scène à la main. Écrire
après avoir regardé un de leurs streams, et citer un moment précis.

### Français

> Bonjour [pseudo], j'ai regardé votre stream de [jeu] hier. J'ai remarqué que vous changiez de scène à la main entre le menu et la partie.
>
> J'ai développé un logiciel gratuit qui le fait automatiquement : OBS Dynamics. Si ça vous tente, je vous le configure en 15 minutes sur votre premier jeu. En échange, je vous demanderais simplement un avis et, si vous êtes content, un court clip.
>
> Présentation : https://blackoune.github.io/

### English

> Hi [name], I watched your [game] stream yesterday and noticed you switch scenes by hand between the menu and the match.
>
> I built a free tool that does it automatically: OBS Dynamics. If you'd like, I can set it up with you in 15 minutes for your first game. In return I'd just ask for your feedback and, if you're happy with it, a short clip.
>
> More info: https://blackoune.github.io/en/

### Relance (une seule, 5 jours après)

> Petite relance au cas où le message serait passé inaperçu. Aucune obligation, bon stream !

Suivi : un tableau avec pseudo, date du premier message, réponse, date de
configuration, avis obtenu, clip obtenu.

---

## 7. Annuaires

| Annuaire | Où | Texte |
|---|---|---|
| AlternativeTo | Ajouter comme alternative à Advanced Scene Switcher et Streamer.bot | Voir fiche ci-dessous. |
| Listes « awesome » sur GitHub | Chercher `awesome obs` et `awesome streaming` sur GitHub, ouvrir une pull request qui ajoute une ligne | `- [OBS Dynamics](https://github.com/Blackoune/OBS-Dynamics) - Automatic scene switching based on the running game and what is on screen. Standalone Windows app, no plugin.` |
| Sites de téléchargement FR | Clubic, 01net, Logithèque : formulaire « proposer un logiciel » | Fiche ci-dessous, en français. |

Pull requests ouvertes le 17 octobre 2026 :

- [Pralhad-Nasane/awesome-obs#13](https://github.com/Pralhad-Nasane/awesome-obs/pull/13)
- [NextNextStep/awesome-live-streaming#5](https://github.com/NextNextStep/awesome-live-streaming/pull/5)
- awesome-creator-tools : pas encore. Ses règles refusent les dépôts récents
  sans utilisateurs ; revenir quand le dépôt aura des étoiles et quelques mois.

Listes repérées le 17 octobre 2026, de la plus active à la moins active :

| Liste | Section où ajouter la ligne |
|---|---|
| [Pralhad-Nasane/awesome-obs](https://github.com/Pralhad-Nasane/awesome-obs) (mise à jour déc. 2025) | Automatisation, à côté de Kruiz Control et Bitfocus Companion |
| [lucky-verma/awesome-creator-tools](https://github.com/lucky-verma/awesome-creator-tools) (fév. 2026) | Live Streaming, « Bots & Automation », à côté de Streamer.bot |
| [NextNextStep/awesome-live-streaming](https://github.com/NextNextStep/awesome-live-streaming) (2023, peu active) | Outils Windows |

Lire le `CONTRIBUTING.md` de chaque liste avant la pull request (ordre
alphabétique, format de ligne, nombre d'étoiles minimum parfois exigé).

Vérifier pour chacun qu'une application autonome est acceptée, pas seulement
des plugins OBS.

### Fiche courte

**FR :** OBS Dynamics change automatiquement vos scènes OBS Studio selon le jeu lancé et ce qui est à l'écran (menu, partie, carte). Il crée les scènes en un clic, masque la minimap au maintien d'une touche, et ajoute un chat Twitch et un widget musique en overlay. Gratuit, open source, 100 % local, sans compte.

**EN :** OBS Dynamics automatically switches your OBS Studio scenes based on the running game and what is on screen (menu, match, map). It creates the scenes in one click, hides your minimap while a key is held, and adds Twitch chat and music overlays. Free, open source, 100% local, no account.

Catégories : Streaming, Video, Gaming. Licence : GPL-3.0. Plateforme : Windows.

---

## 8. Show HN et Product Hunt (après les premiers retours)

### Show HN

**Titre :**
Show HN: OBS Dynamics – Switch OBS scenes from what's on screen, fully local

**Premier commentaire :**

> I stream a bit and kept forgetting to switch scenes between menus and matches. OBS Dynamics watches the running process (psutil), confirms the game state by template matching screen regions against reference shots (OpenCV), and drives OBS over WebSocket v5. Nothing leaves the machine: no account, no telemetry, no server.
>
> Details that might interest HN: a switch needs two matching reads and a 0.15 margin over the runner-up state, credentials are encrypted at rest with DPAPI, the exe is built only in GitHub Actions with published SHA-256 checksums, and CI runs pytest, pip-audit, CodeQL and gitleaks.
>
> Windows only for now (it relies on Windows APIs). Happy to answer questions about the detection.

### Product Hunt

- **Nom :** OBS Dynamics
- **Tagline (60 caractères max) :** Your OBS scenes switch by themselves, based on your game
- **Description :** reprendre la fiche courte EN.
- **Médias :** le GIF, 3 captures (bibliothèque, réglage d'une image de détection, overlays).
- **Premier commentaire :** reprendre le premier commentaire Show HN, sans la partie CI.

---

## 9. Mesure

Chaque lundi :

```bash
python tools/stats.py
```

Noter les chiffres dans un tableau avec la date et le canal posté la semaine.
Ajouter les requêtes de Google Search Console à la main.
