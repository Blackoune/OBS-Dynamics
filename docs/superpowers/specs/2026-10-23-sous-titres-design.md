# Sous-titres automatiques : conception

Date : 2026-10-23. Statut : à valider avant code.

## But

Un nouvel onglet **Sous-titres** dans la barre latérale. Il transcrit en direct
le son d'un périphérique choisi (micro du streamer, ou son du PC : vidéo,
musique, appel) et l'affiche de deux façons, au choix ou ensemble :

1. **Widget incrusté** : source navigateur OBS, style entièrement réglable.
   Tout le monde le voit sur le stream.
2. **Sous-titres fermés (CC)** : texte glissé dans le flux vidéo par OBS.
   Chaque spectateur les active ou non avec le bouton CC de Twitch ou YouTube.

Les deux sorties sont deux interrupteurs indépendants dans l'onglet, pas un
choix exclusif.

## Ce que voit l'utilisateur dans l'onglet

De haut en bas :

- **Carte d'état** : bouton Démarrer / Arrêter, pastille (arrêté, chargement du
  modèle, à l'écoute, erreur), vu-mètre du périphérique, latence mesurée.
- **Source audio** : liste déroulante des périphériques, en deux groupes :
  « Micros » (entrées) et « Son du PC » (sorties en loopback WASAPI). Bouton
  « Actualiser ». Le choix est mémorisé par nom, pas par index (les index
  changent quand on branche un casque).
- **Langue** : langue parlée (liste des modèles disponibles), bouton
  « Télécharger » si le modèle n'est pas encore sur le disque, avec taille et
  barre de progression.
- **Transcription en direct** : zone de texte en lecture seule qui affiche ce
  que le moteur entend, mots provisoires en gris, mots validés en blanc. C'est
  ce qui permet de « voir » sans ouvrir OBS. Bouton « Effacer ».
- **Sortie 1, Widget** : interrupteur, URL à copier (bouton « Copier »),
  bouton « Ouvrir dans le navigateur », bouton « Phrase test », puis le panneau
  de style (voir plus bas).
- **Sortie 2, CC** : interrupteur, état OBS (connecté, en direct ou non),
  réglages CC (voir plus bas), bouton « Phrase test ».

## Moteur de reconnaissance

- **sherpa-onnx**, modèles « streaming » (Zipformer). Local, gratuit, sans clé
  API, CPU léger, résultats partiels toutes les ~100 ms. Latence visée à
  l'écran : 0,3 à 0,6 s.
- Langues : celles pour lesquelles un modèle streaming existe (au moins anglais
  et français, liste exacte à confirmer au lot 1). **Vosk** en secours pour les
  autres langues, même interface.
- Modèles téléchargés au premier usage depuis les releases officielles
  k2-fsa / alphacephei, **somme SHA-256 vérifiée** avant chargement, rangés dans
  `DATA_DIR/models/`. L'installeur reste léger.
- Thread dédié, jamais dans le thread Tk. Le résultat passe à l'interface par
  `post_ui`, comme la musique.
- Aucun son enregistré sur le disque, aucun son envoyé sur internet.

Limite connue, à écrire dans l'aide : la musique chantée se transcrit mal,
quel que soit le moteur.

## Capture audio

- Bibliothèque `soundcard` (WASAPI, loopback natif sous Windows), ou
  `PyAudioWPatch` si `soundcard` pose problème avec PyInstaller.
- Blocs de 100 ms, conversion en mono 16 kHz avant le moteur.
- Si le périphérique disparaît : arrêt propre, pastille d'erreur, reprise
  automatique quand il revient.

## Sortie 1 : widget incrusté

- Nouvelles routes sur `overlay_server.py`, sur le modèle du chat :
  `/subtitles/<token>` (page) et `/subevents/<token>` (EventSource). Même
  serveur local, même jeton. Le SSE déjà en place suffit pour la latence.
- Événements envoyés : `partial` (ligne en cours, remplaçable), `final`
  (ligne figée), `style` (le style change en direct sans recharger la source),
  `clear`.

### Style du widget (complet)

| Groupe | Réglages |
|---|---|
| Texte | police (liste de polices Google intégrées à la page + polices installées sous Windows), taille, graisse, italique, majuscules, interlettrage, alignement (gauche, centre, droite) |
| Couleurs | couleur du texte, couleur des mots provisoires (ou même couleur), couleur de surlignage du dernier mot |
| Lisibilité | contour (couleur, épaisseur), ombre (couleur, flou, décalage) |
| Fond | aucun / bandeau / boîte par ligne ; couleur, opacité, arrondi, marge intérieure |
| Mise en page | nombre de lignes affichées (1 à 4), largeur max, durée avant disparition d'une ligne, animation (aucune, fondu, mot par mot) |
| Préréglages | 4 ou 5 styles prêts (TV classique, gros contour, boîte noire, minimal), puis « Personnalisé » |

- La position et la taille de la zone se règlent dans OBS (source navigateur),
  comme pour les autres widgets.
- Réutiliser le sélecteur de couleur et les briques de `ui_music_style.py`.
- `prefers-reduced-motion` respecté par la page.

## Sortie 2 : sous-titres fermés (CC)

- Envoi par OBS WebSocket v5, requête `SendStreamCaption`, ajoutée à
  `obs_client.py`. OBS glisse le texte au format CEA-608 dans le flux.
- Ne fonctionne que **pendant un live**. Hors live : état « en attente du
  live », rien n'est envoyé.
- Plateformes : Twitch et YouTube Live affichent le bouton CC. Kick et autres :
  non garanti, à indiquer dans l'interface.
- Encodeur : H.264 sûr. HEVC et AV1 à vérifier au lot 3.

### Ce qui se règle pour le CC, et ce qui ne se règle pas

**À dire clairement dans l'onglet :** en CC, **la police, la taille et la
couleur des lettres sont choisies par le spectateur** dans les réglages de
sous-titres de Twitch ou YouTube. Le format CEA-608 n'envoie que du texte ; le
streamer ne peut pas imposer un style. Si on veut un style imposé, c'est le
widget.

Réglages CC proposés :

- langue reconnue (partagée avec le widget, ou une langue propre au CC si on
  ajoute la traduction au lot 4) ;
- longueur des lignes (32 caractères au plus, limite du format) et nombre de
  lignes (2 ou 3) ;
- envoi : à la fin de chaque phrase (stable) ou tous les N mots (plus rapide,
  mais le CC ne peut pas corriger un mot déjà envoyé) ;
- majuscules forcées (meilleure lisibilité sur certains lecteurs) ;
- filtre de gros mots (remplace par ***).

Le CC aura environ 0,5 à 1 s de retard de plus que le widget : il n'envoie que
les mots validés, jamais les mots provisoires.

Accents français : le CEA-608 en contient une partie. À tester au lot 3 sur
Twitch et YouTube ; sinon, translittération (é vers e) en option.

## Découpage en fichiers

| Fichier | Rôle |
|---|---|
| `subtitles_audio.py` | liste des périphériques, capture, conversion 16 kHz |
| `subtitles_engine.py` | moteur sherpa-onnx / Vosk, téléchargement et vérification des modèles, événements partial/final |
| `subtitles_style.py` | dataclass du style, préréglages, CSS de la page |
| `subtitles_hub.py` | relie moteur, widget (SSE) et CC ; découpe en lignes de 32 caractères |
| `ui_subtitles.py` | l'onglet |
| `overlay_server.py` | routes `/subtitles` et `/subevents` |
| `obs_client.py` | `send_stream_caption()` |
| `i18n.json` | textes FR et EN |

## Tests

- découpe CC : lignes de 32 caractères, coupure aux espaces, mots trop longs ;
- style vers CSS : chaque réglage produit la bonne règle, valeurs hors bornes
  refusées ;
- hub : un `partial` puis un `final` arrivent bien au SSE, le CC ne reçoit que
  le `final` ;
- liste des périphériques avec `soundcard` simulé, mémorisation par nom ;
- vérification SHA-256 : un modèle corrompu est refusé ;
- onglet : construction sans erreur (comme `test_music_ui.py`).

## Sécurité

- Serveur d'overlay toujours en local, avec jeton, comme le chat.
- Modèles : sources fixes dans le code, HTTPS, SHA-256 vérifié.
- Réglages de style validés à la lecture (couleurs `#RRGGBB`, nombres bornés,
  police dans la liste ou échappée) : rien n'est injecté tel quel dans la page.

## Lots

1. **Capture et moteur** : périphériques, modèle FR et EN, transcription dans
   l'onglet, latence mesurée. Preuve que l'instantané tient.
2. **Widget** : routes, page, style complet, préréglages, phrase test.
3. **CC** : `SendStreamCaption`, réglages, test réel sur Twitch et YouTube
   (accents, encodeur).
4. **Options** : traduction locale (Argos Translate, latence en plus),
   langues Vosk supplémentaires.
5. **Livraison** : PyInstaller (dépendances natives), installeur, README,
   aide dans l'onglet.

## Questions ouvertes

- Traduction (parler français, sous-titres en anglais) : nécessaire dès la
  première version, ou lot 4 ?
- Une seule source audio à la fois, ou deux (micro + son du PC mélangés) ?
- Plateformes à tester en priorité : Twitch seul, ou Twitch et YouTube ?

---

## État au 2026-10-23 (fin de journée) et reprise

Rien n'est commité. Fait et testé (871 tests) :

- **Lot 1** : capture micro / son du PC, moteur en flux Kroko (fr, en, de, es),
  onglet, avertissement « aucun son reçu ».
- **Lot 2** : widget OBS (`/subtitles/<jeton>`), fenêtre Style complète,
  5 préréglages, mise à jour en direct.
- **Précision (hors plan initial)** : second passage NVIDIA Parakeet TDT 0.6B v3
  qui corrige chaque phrase ~0,5 s après la pause (évènement `revise`), mode
  Rapide / Précis, réglage « Fin de phrase » 0,3 à 1,5 s. Coupure de la phrase
  suivante (`split_next_sentence`) et rognage du silence (`trim_silence`) :
  sans eux, Parakeet tronquait des mots.

Retour de l'utilisateur : « un peu mieux, encore à perfectionner ».

### Chantier A : précision (à faire d'abord)

Mesurer avant de changer : aujourd'hui tout a été mesuré sur la voix de
synthèse Windows, pas sur la vraie voix du streamer.

1. **Banc d'essai sur la vraie voix.** Demander à l'utilisateur d'enregistrer
   5 à 10 phrases de stream typiques (avec son accord, fichier local seulement)
   et d'en écrire le texte exact. Script qui calcule le taux d'erreur par mot
   (WER) pour chaque réglage. Toute idée ci-dessous se juge sur ce chiffre.
2. **Niveau du micro.** Le fifine SC3 arrive très bas (souffle ~0,002, voir
   `SILENCE_LEVEL`). Essayer une normalisation de gain avant les deux passages,
   et le débruitage GTCRN de sherpa-onnx (`OfflineSpeechDenoiser`).
3. **Découpage par VAD.** Remplacer la fin de phrase du modèle de flux par
   Silero VAD (inclus dans sherpa-onnx) : coupures plus nettes, donc meilleur
   second passage et latence plus prévisible.
4. **Liste de mots perso** dans l'onglet : remplacements « entendu → correct »
   pour pseudos, noms de jeux, emotes. Simple, et règle les noms propres que
   aucun modèle ne connaît. Voir aussi si les hotwords de sherpa-onnx marchent
   avec Parakeet (nécessite peut-être un `bpe.vocab`).
5. **Modèle de flux.** Comparer Kroko et `sherpa-onnx-streaming-zipformer-fr-2023-04-14`
   (380 Mo) sur le banc d'essai : les mots provisoires seraient plus justes.
6. Parakeet a mis un « ? » final sur une affirmation : à surveiller sur le banc.

### Chantier B : lot 3, sous-titres CC

Point de départ dans le code :

- `obs_client.py` : `OBSClient.call(request_type, data)` existe déjà (simpleobsws,
  OBS WebSocket v5). Ajouter `send_stream_caption(text)` → requête
  `SendStreamCaption` avec `{"captionText": text}`. Le client vit dans
  `App._obs_client`, piloté par `obs_loop` (`AsyncLoopThread.run_coro`).
- Le CC est un **abonné de plus** du flux de texte, comme le widget :
  brancher dans `SubtitlesView._on_text` (ou faire du hub un diffuseur
  générique). Le CC ne reçoit QUE du texte figé : le `revise` en mode Précis,
  sinon le `final`. En mode Précis, attendre le `revise` au plus ~1 s, puis
  envoyer le `final` s'il n'est pas venu (phrase abandonnée par le `Refiner`).
- Découpe en lignes de 32 caractères maximum (limite CEA-608), coupure aux
  espaces, 2 ou 3 lignes ; fonction pure, testée.
- Carte « Sous-titres CC » dans l'onglet, sous la carte Widget : interrupteur,
  état OBS (non connecté / connecté hors live / en direct, via `GetStreamStatus`),
  longueur de ligne, majuscules, filtre de gros mots, bouton « Phrase test ».
  Texte clair dans la carte : police, couleur et taille sont choisies par le
  spectateur dans Twitch / YouTube, pas par le streamer.
- Vérifications réelles avec l'utilisateur : Twitch (bouton CC), YouTube,
  accents français dans CEA-608 (sinon option de translittération), encodeur
  H.264 contre HEVC / AV1.
- Textes : clés `SUBS_CC_*`, 39 langues (sous-agent Haiku comme pour les lots
  1 et 2).

### Pièges rencontrés aujourd'hui

- Ne jamais réécrire un fichier source avec `Get-Content` / `Set-Content` de
  PowerShell 5.1 : lecture en ANSI, fichier abîmé (déjà réparé une fois).
  Passer par l'outil Edit ou par Python en UTF-8.
- Les tests qui construisent `App` doivent recevoir des chemins sous
  `DATA_DIR` : un test a écrit une fois dans les vrais réglages de
  l'utilisateur (source réglée sur l'écran DELL).
- Le cache des modèles de test est dans le dossier scratchpad de la session
  du 2026-10-23 ; il disparaîtra. Le vrai dossier des modèles est
  `%APPDATA%\OBS Dynamics\data\models`.
