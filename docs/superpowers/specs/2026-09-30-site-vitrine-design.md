# Site vitrine OBS Dynamics — Design

- **Date** : 2026-09-30
- **Statut** : design validé en conversation (vision d'ensemble). Mise en œuvre à une date
  ultérieure choisie par Blackoune ; aucune décision en attente.
- **Périmètre** : site public (présentation, téléchargement, support) et chaîne de publication sécurisée de `Dynamics.exe`

---

## 1. Objectif

Un site statique, gratuit, hébergé hors du PC de Blackoune, qui :

1. présente OBS Dynamics avec un motion design de niveau professionnel ;
2. permet de télécharger `Dynamics.exe` en confiance, et indique l'état de la version Mac ;
3. mène l'utilisateur à la bonne réponse (FAQ questionnaire) ou, à défaut, au Discord ;
4. maximise les chances d'apparaître dans Google et dans les réponses des IA.

**Exigence non négociable** : personne ne doit pouvoir substituer un fichier malveillant au
téléchargement, ni injecter du code dans le site.

---

## 2. Décisions actées

| Sujet | Décision |
|---|---|
| Hébergement | GitHub Pages, adresse `*.github.io`, 0 €. Nom de domaine seulement si le projet devient commercial. |
| Binaire | GitHub Releases. Construit **uniquement** par GitHub Actions, jamais sur un PC. |
| Ton | Vouvoiement partout sur le site. |
| Identité publique | « Blackoune » (pseudo Discord et Twitch). |
| Langues | Français (par défaut) et anglais. |
| Licence | GPL-3.0. Compatible avec toutes les dépendances (MIT, BSD, Apache-2.0, LGPL-3.0 pour `pynput`). |
| Mac | Un bouton Mac est présent. L'application étant Windows uniquement, il ouvre une carte « Version Mac en préparation » avec un bouton « Être prévenu sur Discord ». Aucun faux fichier. |
| Discord | Toujours un bouton, jamais une URL brute affichée. Invitations permanentes. |
| Mail de contact | Adresse gratuite dédiée au projet, créée par Blackoune. |
| Visibilité du dépôt | Le dépôt devient public (Pages gratuit, signature SignPath, bouton GitHub), **après** l'audit de l'historique (§7.1). |

### Adresse et identités

| # | Sujet | Décision |
|---|---|---|
| D1 | Adresse GitHub publique | **Validé.** Organisation GitHub gratuite « Blackoune », dépôt transféré dedans : site sur `blackoune.github.io/OBS-Dynamics/`. |
| D2a | Identité de Blackoune dans l'historique (`tristanbest0802-beep`, `Blackoune`, `tristanbest0802@gmail.com`) | **Validé.** Tous ses commits réécrits avec le nom `Blackoune` et l'adresse `noreply` GitHub de son compte (visible dans Settings → Emails). |
| D2b | Identité de Maxence Torchin (2 commits, adresse universitaire) | **Validé.** Conservée telle quelle : Maxence accepte que son nom figure dans les commits. |

Règles d'exécution de D2 :

- Une seule réécriture de l'historique, limitée aux commits de Blackoune, faite avant le
  passage en public.
- La réécriture change les empreintes des commits réécrits et de tous ceux qui suivent :
  chaque copie locale du dépôt (Blackoune, Maxence) doit ensuite être reclonée.

Dans ce document, `<compte>` désigne l'organisation `Blackoune`.

---

## 3. Architecture

```
OBS-Dynamics/
├── site/                         nouveau : le site
│   ├── astro.config.mjs          site, base, i18n, build.inlineStylesheets = 'never'
│   ├── package.json
│   ├── package-lock.json         obligatoire (npm ci)
│   ├── public/                   polices woff2, favicon, image Open Graph, llms.txt, médias
│   ├── scripts/check-dist.mjs    contrôle de sécurité du site compilé (§7.3)
│   └── src/
│       ├── layouts/Base.astro    <head>, CSP, navigation, pied de page, JSON-LD commun
│       ├── components/           Nav, Button, OsPicker, FaqWizard, FaqStatic, sections de présentation
│       ├── data/faq.fr.json      source unique de la FAQ (§5)
│       ├── data/faq.en.json
│       ├── data/release.ts       lit la dernière release GitHub au moment du build
│       ├── i18n/fr.json          textes du site
│       ├── i18n/en.json
│       ├── scripts/motion.ts     animations GSAP
│       ├── scripts/faq.ts        questionnaire
│       ├── scripts/os.ts         détection du système
│       └── pages/
│           ├── index.astro               Présentation
│           ├── installation.astro
│           ├── contact.astro
│           ├── comparatif.astro
│           ├── mentions-legales.astro
│           ├── confidentialite.astro
│           └── en/                       mêmes pages en anglais
├── .github/
│   ├── workflows/ci.yml          existant, actions épinglées par SHA
│   ├── workflows/site.yml        nouveau : build et déploiement Pages
│   ├── workflows/release.yml     nouveau : build, signature, attestation, publication du .exe
│   ├── workflows/codeql.yml      nouveau
│   ├── workflows/history-scan.yml nouveau : gitleaks sur tout l'historique (manuel)
│   ├── dependabot.yml            nouveau : npm, pip, github-actions
│   └── rulesets/                 nouveau : règles JSON à importer dans GitHub
│       ├── main.json
│       └── tags-release.json
├── requirements-release.txt      nouveau : dépendances + PyInstaller, avec empreintes
├── LICENSE                       nouveau : GPL-3.0
└── SECURITY.md                   nouveau : signaler une faille, vérifier un .exe
```

**Stack** : Astro (sortie 100 % statique), GSAP avec ScrollTrigger et SplitText, Lenis.
Aucune autre dépendance d'exécution. Polices auto-hébergées. Aucun appel réseau depuis le
navigateur du visiteur, hors clic sur un lien.

**Navigation** : trois vraies pages (`/`, `/installation`, `/contact`), pas des onglets
JavaScript, pour que chacune soit indexée avec son propre titre. La barre du haut a l'aspect
d'onglets, avec un indicateur qui glisse ; transitions entre pages par les View Transitions
d'Astro. Pied de page : mentions légales, confidentialité, comparatif, GitHub, Discord,
mention « Projet indépendant, non affilié à OBS Project ».

---

## 4. Pages

### 4.1 Présentation (`/`)

1. **Hero** : écran de jeu stylisé qui passe de « menu » à « en jeu » ; à côté, une liste de
   scènes OBS qui bascule en direct (« Valorant - Menu » → « Valorant - En jeu »). Titre
   révélé mot par mot. Boutons « Télécharger pour Windows » et « Voir sur GitHub ».
2. **Comment ça marche** : section épinglée au scroll, trois temps : processus détecté →
   cadres numérotés qui scannent l'écran (repris de « Vérifier le cadrage ») → scène qui bascule.
3. **Modules**, un par écran :
   - Bibliothèque : jaquettes qui se rangent, grille de 2 à 8 colonnes.
   - Hotkeys : touches F1 / F2 / F3 qui s'enfoncent, état qui change.
   - Raccourcis & overlays : combinaison pressée → image ou son qui apparaît ; mode maintien.
   - Chat Twitch : messages qui défilent, avec le rendu réel de l'overlay.
   - Widget musique : rendu réel du widget, qui parcourt les 8 thèmes et 5 dispositions,
     forme d'onde animée.
   - 39 langues : un titre qui change de langue en boucle.
4. **Confiance** : 100 % local, aucun compte, aucune télémétrie, identifiants chiffrés,
   code ouvert, exécutable vérifiable (lien vers §4.2 « Vérifier »).
5. **Questions courtes** : 4 à 6 questions formulées comme les recherches réelles, réponse
   directe en première phrase.
6. **Appel final** au téléchargement.

Les démos du chat et du widget musique reprennent le HTML et le CSS réels servis par
`twitch_chat.py` / `music_overlay.py`, copiés dans des composants du site et alimentés par
des données fictives.

### 4.2 Installation (`/installation`)

- **Sélecteur Windows | Mac**, présélectionné d'après `navigator.userAgentData.platform`
  (repli : `navigator.userAgent`). Sans JavaScript, les deux panneaux s'affichent l'un sous l'autre.
- **Panneau Windows**
  - Bouton vers `https://github.com/<compte>/OBS-Dynamics/releases/latest/download/Dynamics.exe`.
  - Version, date, taille et SHA-256, lus au build depuis l'API GitHub (`data/release.ts`).
  - Prérequis : Windows 10 ou 11, OBS Studio 28 ou plus.
  - Lien vers le rapport VirusTotal de la version.
  - Encart SmartScreen : captures et explication de « Informations complémentaires →
    Exécuter quand même ». Affiché tant que la signature SignPath n'est pas active ; conservé
    ensuite sous forme réduite (la réputation d'un certificat neuf se construit avec le temps).
- **Vérifier votre téléchargement**
  - `Get-FileHash .\Dynamics.exe -Algorithm SHA256`, à comparer à l'empreinte affichée.
  - `gh attestation verify Dynamics.exe --repo <compte>/OBS-Dynamics` : prouve que le fichier
    a été construit par le workflow officiel à partir du code public.
  - Une fois SignPath actif : Propriétés du fichier → Signatures numériques.
- **Panneau Mac** : carte « Version Mac en préparation » et bouton « Être prévenu sur Discord ».
- **Premiers pas en 3 étapes** (activer le WebSocket d'OBS, saisir le mot de passe, ajouter
  un jeu), repris du README en vouvoiement.
- **Depuis les sources** : bouton « Voir le code sur GitHub » et commandes `git clone`,
  `pip install -r requirements.txt`, `python obs_dynamics.py`, chacune avec un bouton copier.

### 4.3 Nous contacter (`/contact`)

- **Présentation** : Blackoune, projet indépendant et non commercial, liens Twitch et Discord.
- **Carte mail** : adresse assemblée par JavaScript (protection simple contre les robots
  collecteurs), bouton copier, lien `mailto:`. Sans JavaScript : adresse écrite avec `[at]`.
- **Carte Discord** : bouton « Rejoindre le Discord ».
- **FAQ questionnaire** (§5), dans un bloc bien visible, suivi de la **FAQ complète**
  dépliable.

### 4.4 Pages annexes

- **Comparatif** : OBS Dynamics, Advanced Scene Switcher, changement de scène manuel.
  Tableau factuel, forces et limites de chacun, sans dénigrement.
- **Mentions légales** : éditeur (Blackoune, moyen de contact), hébergeur (GitHub, Inc.),
  licence, mention de non-affiliation à OBS Project. Texte final relu par Blackoune.
- **Confidentialité** : aucun cookie, aucune mesure d'audience, aucune donnée collectée ;
  l'hébergeur GitHub journalise les adresses IP selon sa propre politique.

---

## 5. FAQ

### 5.1 Source unique

`src/data/faq.<langue>.json` alimente à la fois le questionnaire, la FAQ dépliable et le
JSON-LD `FAQPage`. Les deux langues ont exactement les mêmes identifiants.

```json
{
  "categories": [
    {
      "id": "connexion-obs",
      "title": "Connexion à OBS",
      "symptoms": [
        {
          "id": "statut-obs-erreur",
          "title": "Le statut affiche « Surveillance active (OBS: …) »",
          "steps": [
            "Vérifiez qu'OBS Studio est lancé.",
            "Dans OBS : Outils → Paramètres du serveur WebSocket, cochez « Activer le serveur WebSocket ».",
            "Comparez le port avec celui de l'onglet Paramètres (4455 par défaut).",
            "Recopiez le mot de passe depuis « Afficher les informations de connexion » et enregistrez-le à nouveau dans Paramètres."
          ],
          "media": {
            "type": "video",
            "src": "faq/obs-websocket.webm",
            "alt": "Activation du serveur WebSocket dans OBS Studio"
          }
        }
      ]
    }
  ]
}
```

`media` est facultatif : une réponse sans vidéo reste complète.

### 5.2 Contenu

Catégories : `installation`, `connexion-obs`, `detection-jeux`, `raccourcis-overlays`,
`chat-twitch`, `widget-musique`, `autre`.

- Les symptômes des six premières catégories reprennent la section **Dépannage** du README,
  réécrite en vouvoiement.
- `installation` ajoute : avertissement SmartScreen, antivirus qui bloque le fichier (faux
  positif fréquent des exécutables PyInstaller : vérifier l'empreinte et l'attestation,
  signaler le faux positif), utilisateur Mac, vérification de l'authenticité du fichier.
- `autre` n'a pas de symptôme : il mène directement à l'encart Discord.

### 5.3 Questionnaire

1. « Quel est votre problème ? » : une carte par catégorie.
2. Symptôme précis : 2 à 6 choix, fil d'Ariane, bouton retour.
3. Réponse : étapes numérotées, média éventuel.
4. « Êtes-vous satisfait ? »
   - **Oui** : message vert « Nous sommes heureux de vous avoir aidé. »
   - **Non** : encart Discord avec deux boutons, « Ouvrir un ticket » (recommandé) et
     « Rejoindre le vocal d'aide », chacun vers une invitation permanente qui cible le bon
     salon. En dessous, un résumé à copier dans le ticket :
     `Catégorie > Symptôme — réponse consultée : <titre du symptôme>`.

- Chaque réponse a son adresse (`/contact#faq/<categorie>/<symptome>`), partageable par les
  modérateurs ; ouvrir cette adresse affiche directement la réponse.
- Accessibilité : focus déplacé sur le titre de chaque nouvelle étape, zone `aria-live`,
  navigation complète au clavier.
- La réponse Oui / Non n'est envoyée nulle part (aucun serveur).
- Sans JavaScript : le questionnaire est masqué, la FAQ dépliable reste entièrement lisible.

---

## 6. Direction artistique et motion

### 6.1 Identité

- Couleurs : fond `#0B0F17`, accent `#22D3EE` (déjà utilisés par le README et le logo),
  neutres dérivés de ces deux teintes. Grain très léger en fond.
- Typographie : **Archivo** (variable, axe de largeur 62–125, licence OFL) pour les titres et
  le texte ; l'axe de largeur sert aux animations de titres. **JetBrains Mono** (OFL) pour les
  détails techniques. Fichiers woff2 auto-hébergés.
- Logo existant `assets/logo.png`, décliné en favicon et image Open Graph.

### 6.2 Règles pour ne pas « faire IA »

- Interdits : dégradés violet / bleu, taches floues en fond, emoji comme icônes, grilles de
  trois cartes identiques avec icône, formules creuses (« révolutionnez », « libérez la puissance »).
- Chaque animation démontre une fonctionnalité réelle ; aucune animation purement décorative.
- Mise en page éditoriale asymétrique, alignements francs, hiérarchie typographique forte.
- Textes concrets et chiffrés (720p à 4K, 39 langues, marge de 0,15), même précision que le README.
- Vraies vidéos de l'application, en courtes boucles.

### 6.3 Règles techniques

- Animations en `transform` et `opacity` uniquement.
- Trois sections épinglées au maximum sur la page Présentation.
- `prefers-reduced-motion: reduce` : animations remplacées par les états finaux statiques,
  Lenis désactivé.
- Mobile (moins de 768 px) : épinglages retirés, animations simplifiées.
- Vidéos : WebM et MP4, 2 Mo maximum chacune, image d'attente, chargement différé, lecture
  seulement quand la vidéo est visible.

---

## 7. Sécurité — réalisée par Claude

### 7.1 Avant le passage en public

1. **Audit de tout l'historique git**
   - Motifs de `check_secrets.py` appliqués à chaque blob de `git rev-list --all --objects`.
   - Seconde lecture par gitleaks, exécuté dans GitHub Actions (`history-scan.yml`,
     déclenchement manuel), sans rien installer sur le PC.
   - Rapport : chaque occurrence avec commit, fichier et nature. Si une fuite est trouvée :
     liste des secrets à changer et procédure de nettoyage de l'historique, exécutée
     seulement avec l'accord explicite de Blackoune.
2. **Identités de l'historique** : réécriture des commits de Blackoune (D2a) ; ceux de
   Maxence restent inchangés (D2b).
3. Ajout de `LICENSE` (GPL-3.0) et `SECURITY.md` (signalement privé des failles, procédure
   de vérification d'un `.exe`).

### 7.2 Chaîne de publication du `.exe` (`release.yml`)

- **Déclencheur** : push d'un tag `v*` uniquement.
- **Job `build`** (`windows-latest`, permissions `contents: read`) :
  1. checkout du commit tagué ;
  2. Python épinglé ;
  3. `pip install --require-hashes -r requirements-release.txt` (dépendances et PyInstaller
     avec empreintes, générées par `pip-compile --generate-hashes`) ;
  4. `python -m pytest tests/ -q` ;
  5. `python check_secrets.py --all` ;
  6. `python build.py`.
- **Job `sign`** : envoi du `.exe` à SignPath par son action officielle, retour du fichier
  signé. Tant que le projet n'est pas accepté par SignPath, ce job est ignoré et la release
  est marquée « non signée » sur le site.
- **Attestation** : `actions/attest-build-provenance` sur le `.exe` final
  (permissions `id-token: write`, `attestations: write` limitées à ce job).
- **Empreintes** : `SHA256SUMS.txt` produit et joint à la release.
- **VirusTotal** : envoi du `.exe` par l'API (secret `VT_API_KEY`), lien du rapport ajouté
  aux notes de release.
- **Job `publish`** : dans l'environnement protégé `release` (approbation manuelle de
  Blackoune) ; crée la release avec `Dynamics.exe` et `SHA256SUMS.txt`
  (permission `contents: write` limitée à ce job).
- **Fin** : déclenche `site.yml` pour que la page Installation affiche la nouvelle version.

Conséquence : le `.exe` construit localement par les hooks `post-commit` / `post-merge`
reste un outil de développement et n'est jamais publié.

### 7.3 Site

- **`site.yml`** : `npm ci`, `astro check`, `astro build`, `node scripts/check-dist.mjs`,
  `npm audit --audit-level=high`, puis `actions/upload-pages-artifact` et
  `actions/deploy-pages`. Environnement `github-pages` limité à la branche `main`.
  Déclenché par un push touchant `site/**`, par la fin de `release.yml`, ou manuellement.
- **Aucune ressource externe** : ni script, ni style, ni police, ni image, ni vidéo chargés
  depuis un autre domaine. Aucun formulaire, cookie, stockage local ou outil de mesure.
- **Content Security Policy** en `<meta http-equiv>` sur chaque page :
  `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; media-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; upgrade-insecure-requests`.
  Astro est configuré pour ne produire ni script ni feuille de style en ligne. Si une
  bibliothèque en injecte malgré tout (transitions de page par exemple), son empreinte est
  ajoutée à la CSP (`'sha256-…'`), jamais `'unsafe-inline'`. Les blocs
  `application/ld+json` sont des données non exécutées, non concernés par `script-src`.
- `<meta name="referrer" content="strict-origin-when-cross-origin">` ; liens externes en
  `rel="noopener noreferrer"`.
- **`check-dist.mjs`** fait échouer le build si : une balise `script`, `link`, `img`,
  `video`, `source` ou `iframe` pointe vers un autre domaine ; une page n'a pas la CSP ; un
  attribut `on*` ou un script en ligne exécutable apparaît ; un lien interne est cassé.
- **Limites connues** : GitHub Pages ne permet pas d'en-têtes HTTP personnalisés, donc pas
  de `frame-ancestors` ni de `X-Frame-Options`. Risque résiduel : le site peut être affiché
  dans un cadre sur un autre site ; faible, car le site ne propose aucune action sensible.
  HTTPS est forcé par GitHub Pages.

### 7.4 Dépôt

- Toutes les actions GitHub épinglées par SHA de commit, `ci.yml` existant compris.
- Permissions par défaut des workflows : `contents: read`.
- `dependabot.yml` : npm (`/site`), pip (`/`), github-actions ; hebdomadaire.
- `codeql.yml` : analyse JavaScript/TypeScript et Python.
- Rulesets JSON prêts à importer :
  - `main.json` : force-push et suppression de `main` interdits ;
  - `tags-release.json` : création, modification et suppression des tags `v*` réservées au
    propriétaire.
- CI existante conservée : tests, cohérence i18n, import, absence de secret, `pip-audit`.

---

## 8. Référencement Google et IA

- **Données structurées JSON-LD** :
  - `SoftwareApplication` : nom, `operatingSystem` « Windows 10, Windows 11 »,
    `applicationCategory` « MultimediaApplication », `offers` à 0 €, `downloadUrl`,
    `softwareVersion`, `license` ;
  - `FAQPage` sur `/contact`, généré depuis `faq.<langue>.json` ;
  - `HowTo` sur `/installation` pour les premiers pas.
- `sitemap.xml` généré au build, soumis dans Google Search Console et Bing Webmaster Tools.
- Balises `canonical`, `hreflang` FR / EN, Open Graph et Twitter Card avec image dédiée.
- `llms.txt` à la racine du site (`/OBS-Dynamics/llms.txt`) : résumé du logiciel, des fonctionnalités et des pages.
- Site de projet GitHub Pages : pas de `robots.txt` possible à la racine du domaine ; son
  absence autorise tous les robots, y compris ceux des IA, ce qui est le comportement voulu.
- Titres rédigés comme les recherches réelles, par exemple « Comment changer de scène OBS
  automatiquement selon le jeu ? », réponse directe dans la première phrase.
- Page comparatif (§4.4).
- Performance et accessibilité (§10), qui comptent dans le classement.

---

## 9. Données du build

- `data/release.ts` interroge `https://api.github.com/repos/<compte>/OBS-Dynamics/releases/latest`
  **au moment du build uniquement** (jamais depuis le navigateur du visiteur) : version,
  date, taille, contenu de `SHA256SUMS.txt`, lien VirusTotal.
- Aucune release publiée : la page Installation affiche « Première version bientôt
  disponible » à la place du bouton Windows, et le build réussit.
- API indisponible pendant le build : le build échoue, le site en ligne reste la version
  précédente. Jamais de page avec une empreinte absente ou fausse.

---

## 10. Qualité visée

- Lighthouse mobile ≥ 95 en performance, accessibilité, bonnes pratiques et SEO, sur les
  trois pages principales.
- Contrastes WCAG AA, navigation complète au clavier, textes alternatifs sur tous les médias.
- Poids de la page Présentation hors vidéos : 300 Ko maximum (compressé).

---

## 11. Vérification

- **Automatique (CI)** : `astro check`, build, `check-dist.mjs`, `npm audit`, CodeQL, et un
  test `node:test` qui vérifie la FAQ : identifiants uniques, mêmes identifiants en FR et EN,
  chaque symptôme a au moins une étape, chaque média référencé existe.
- **Manuelle, dans le navigateur intégré** : chaque parcours du questionnaire (Oui, Non,
  Autre, retour, adresse directe), sélecteur Windows / Mac, `prefers-reduced-motion`,
  largeur 375 px, absence d'erreur CSP dans la console, Lighthouse.
- **Release** : première release de test sur un tag `v0.0.1-test`, vérification du `.exe`
  téléchargé avec `Get-FileHash` et `gh attestation verify`, puis suppression de cette release.

---

## 12. Hors périmètre

- Version Mac de l'application.
- Nom de domaine, mesure d'audience, newsletter, formulaire de contact, blog.
- Langues du site au-delà de FR et EN.
- Passage du README au vouvoiement (tâche séparée).

---

## 13. Actions à faire par Blackoune

### 13.1 Sécurité

| # | Action | Où | Quand |
|---|---|---|---|
| 1 | Double authentification par clé de sécurité ou passkey ; retirer le SMS ; imprimer les codes de récupération et les ranger hors ligne. | GitHub → Settings → Password and authentication | Avant tout |
| 2 | Révoquer les applications OAuth et les tokens inutilisés. | GitHub → Settings → Applications ; Developer settings → Personal access tokens | Avant tout |
| 3 | Cocher « Keep my email addresses private » et « Block command line pushes that expose my email » ; configurer git avec l'adresse `noreply` fournie par GitHub. | GitHub → Settings → Emails ; `git config --global user.email` | Avant le passage en public |
| 4 | Créer l'organisation gratuite « Blackoune » et y transférer le dépôt (D1) ; prévenir Maxence qu'il devra recloner après la réécriture (D2). | GitHub → Your organizations → New organization ; Dépôt → Settings → Transfer | Avant le passage en public |
| 5 | Passer le dépôt en public, uniquement après le rapport d'audit propre de Claude. | Dépôt → Settings → General → Danger Zone | Après l'audit |
| 6 | Vérifier que secret scanning et push protection sont actifs ; activer Dependabot alerts, Dependabot security updates et Private vulnerability reporting. | Dépôt → Settings → Code security | Juste après le passage en public |
| 7 | Importer les deux rulesets fournis. | Dépôt → Settings → Rules → Rulesets → Import | Juste après le passage en public |
| 8 | Créer l'environnement `release` avec vous comme approbateur obligatoire. | Dépôt → Settings → Environments | Avant la première release |
| 9 | Pages : source « GitHub Actions ». | Dépôt → Settings → Pages | Avant le premier déploiement |
| 10 | Candidater à SignPath Foundation (signature gratuite pour l'open source). | signpath.org | Après le passage en public et l'ajout de la licence |
| 11 | Créer un compte VirusTotal gratuit et enregistrer la clé API comme secret `VT_API_KEY`. | virustotal.com ; Dépôt → Settings → Secrets and variables → Actions | Avant la première release |
| 12 | Signer vos commits avec une clé SSH et activer « Vigilant mode ». | GitHub → Settings → SSH and GPG keys | Recommandé |
| 13 | Adresse mail dédiée au projet, avec double authentification. | Gmail ou Proton | Avant la page Contact |
| 14 | Discord : double authentification obligatoire pour la modération, niveau de vérification au moins « Moyen », invitations permanentes créées par vous. | Paramètres du serveur → Sécurité | Avant la page Contact |
| 15 | PC : Windows à jour, Defender actif, aucun exécutable inconnu, gestionnaire de mots de passe. | — | En permanence |
| 16 | À chaque version : `git tag vX.Y.Z`, `git push origin vX.Y.Z`, puis approuver la publication dans l'onglet Actions. | Terminal ; GitHub → Actions | À chaque version |

### 13.2 Contenu et communauté

| # | Action | Quand |
|---|---|---|
| 1 | Enregistrer les vidéos de l'application selon la liste de plans fournie par Claude. | Avant la page Présentation |
| 2 | Discord : bot Ticket Tool, salon d'ouverture de ticket, salon vocal « Support », deux invitations permanentes (ticket, vocal) et une invitation générale. | Avant la page Contact |
| 3 | Rédiger ou valider le court texte de présentation de Blackoune. | Avant la page Contact |
| 4 | Google Search Console et Bing Webmaster Tools : ajouter le site, soumettre le sitemap. | Après la mise en ligne |
| 5 | Fiche sur le forum OBS (section Resources), AlternativeTo, Product Hunt ; posts utiles sur r/obs et r/Twitch ; vidéo de démonstration YouTube ; topics du dépôt GitHub. | Après la mise en ligne |

---

## 14. Ordre d'attaque

0. **Sécurité préalable** : audit de l'historique, `LICENSE`, `SECURITY.md`, épinglage des
   actions de `ci.yml`, `dependabot.yml`, `codeql.yml`, rulesets JSON.
   En parallèle : actions 13.1 n° 1 à 7 et 9 de Blackoune (le site ne peut être mis en
   ligne qu'une fois le dépôt public et Pages activé).
1. **Fondations du site** : projet Astro, identité (couleurs, polices), layout, navigation,
   CSP, `check-dist.mjs`, `site.yml`. Site vide en ligne.
2. **Chaîne de release** : `requirements-release.txt`, `release.yml`, release de test (§11).
3. **Page Installation**.
4. **Page Contact et FAQ** (données, questionnaire, FAQ dépliable, test `node:test`).
5. **Page Présentation et motion design**.
6. **Référencement, version anglaise, pages annexes**.
7. **Vérification finale** (§11) et Lighthouse.
