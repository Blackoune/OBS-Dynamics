// Textes du site en français (langue par défaut). en.ts a exactement la même
// forme : une clé oubliée fait échouer `astro check`.
// Mise en forme autorisée dans les textes : **gras** et `code` (Texte.astro).

export const fr = {
  lang: 'fr',
  locale: 'fr_FR',
  dateLocale: 'fr-FR',
  autreLangue: { code: 'en', nom: 'English' },

  commun: {
    evitement: 'Aller au contenu',
    navAria: 'Pages',
    accueilAria: 'OBS Dynamics, accueil',
    nav: { presentation: 'Présentation', installation: 'Installation', contact: 'Nous contacter' },
    piedNote: 'Projet indépendant, non affilié à OBS Project.',
    pied: {
      comparatif: 'Comparatif', mentions: 'Mentions légales', confidentialite: 'Confidentialité',
      github: 'Code source sur GitHub', licence: 'Licence GPL-3.0',
    },
    copier: 'Copier',
    copie: 'Copié',
    telechargerWindows: 'Télécharger pour Windows',
    voirGithub: 'Voir sur GitHub',
  },

  accueil: {
    titre: 'OBS Dynamics — changer de scène OBS automatiquement selon le jeu',
    description: 'Logiciel gratuit pour Windows : détecte le jeu lancé, repère menu ou partie, et bascule vos scènes OBS sans intervention. Overlays, chat Twitch et widget musique inclus.',
    surtitre: 'Gratuit · Windows 10 et 11 · Open source',
    h1: 'Vos scènes OBS changent seules, selon le jeu.',
    chapo: "OBS Dynamics reconnaît le jeu lancé, repère s'il est au menu ou en partie, et bascule la bonne scène. De 720p à 4K, sans rien toucher pendant le live.",
    regie: {
      menu: ['Jouer', 'Carrière', 'Arsenal', 'Paramètres'],
      scenes: 'Scènes', sceneMenu: 'Valorant - Menu', sceneJeu: 'Valorant - En jeu', pause: 'Pause',
      statutLecture: "Lecture de l'écran…", statutMenu: 'Menu · score 0,91', statutJeu: 'En jeu · score 0,94',
    },
    mecanisme: {
      titre: 'Comment ça marche',
      temps: [
        ['Le jeu est reconnu', "Les jeux Steam par leur dossier d'installation, même si l'exécutable est renommé. Les autres par le nom de leur exécutable."],
        ["L'écran est lu", "Des fragments de vos captures de référence sont cherchés à l'écran. Il faut deux lectures concordantes, et un écart d'au moins 0,15 entre menu et partie, avant toute bascule."],
        ['La scène bascule', "OBS reçoit l'ordre par son WebSocket. Si OBS redémarre, la connexion se rétablit seule."],
      ],
      processus: 'Processus', reconnu: 'jeu reconnu', cadrage: 'Vérifier le cadrage',
      scoreMenu: 'Menu', scoreJeu: 'En jeu', marge: 'Marge', valeurs: ['0,31', '0,94', '0,63 ≥ 0,15'],
    },
    modules: {
      titre: "Tout ce qu'il faut pendant un live",
      biblio: ['Bibliothèque', "Vos jeux Steam importés d'un clic", "Scan de toutes vos bibliothèques Steam, ajout manuel de n'importe quel exécutable, jaquettes téléchargées et mises en cache. La grille s'adapte de 2 à 8 colonnes."],
      actif: 'Actif', inactif: 'Inactif',
      hotkeys: ['Hotkeys', 'Une touche pour reprendre la main', 'Si la détection se trompe, `F1` force « En jeu », `F2` « Menu », `F3` « Inactif ». Les combinaisons comme `Ctrl + Maj + F1` sont acceptées.'],
      touches: ['En jeu', 'Menu', 'Inactif'],
      etatForce: 'État forcé :',
      raccourcis: ['Raccourcis & overlays', 'Une combinaison, une image, un son', 'Associez une combinaison de touches à une image, une vidéo ou un son affichés dans OBS. En mode maintien, le média reste tant que la touche est enfoncée : idéal pour cacher une minimap.'],
      maj: 'Maj', maintien: 'Maintien', carteMasquee: 'Carte masquée',
      chat: ['Chat Twitch', 'Votre chat en overlay, sans compte', 'Lecture anonyme de votre chaîne : aucune clé, aucune autorisation. Un lien permanent à coller dans OBS, avec les couleurs et les emotes de vos viewers.'],
      messages: [
        ['Lyra_77', '#FF7F50', 'gg le clutch'],
        ['Kaze', '#1E90FF', 'la scène a changé toute seule ?'],
        ['minuitpile', '#9ACD32', 'oui, OBS Dynamics détecte le menu'],
        ['Brisk', '#DAA520', 'encore une partie !'],
        ['Pixelune', '#FF69B4', 'le widget musique est trop propre'],
        ['Tobiwan', '#00CED1', 'quelle playlist ?'],
        ['Saphir', '#B8860B', 'go ranked après'],
        ['Neko_', '#8A2BE2', 'salut tout le monde'],
      ],
      widget: ['Widget musique', "Ce que vous écoutez, à l'écran", "Pochette, titre, artiste et forme d'onde, depuis Spotify, Deezer, Apple Music et les autres lecteurs Windows. Cinq dispositions, huit thèmes, image de fond personnalisée."],
      langues: ['Interface', '39 langues, sans redémarrer', "L'interface change de langue immédiatement. Ci-contre, le même onglet dans chacune d'elles."],
    },
    confiance: {
      titre: 'Votre PC, vos données',
      items: [
        ['100 % local', 'Tout tourne sur votre PC. Aucun serveur, aucun compte à créer.'],
        ['Aucune télémétrie', 'Les seules connexions vont vers OBS, vers Twitch si vous utilisez le chat, et vers les jaquettes de jeux.'],
        ['Identifiants chiffrés', 'Le mot de passe OBS est chiffré par Windows et lié à votre compte.'],
        ['Code ouvert', 'Licence GPL-3.0 : le code entier est lisible sur GitHub.'],
        ['Exécutable vérifiable', 'Construit par GitHub, empreinte SHA-256 publiée.'],
      ],
      verifier: 'Vérifier un téléchargement',
    },
    questions: {
      titre: 'Questions courtes',
      items: [
        ['Comment changer de scène OBS automatiquement selon le jeu ?', "Installez OBS Dynamics, ajoutez votre jeu, choisissez une scène pour le menu et une pour la partie : l'application bascule seule. Elle reconnaît le jeu lancé, compare l'écran à vos captures de référence et commande OBS par son WebSocket."],
        ['OBS Dynamics est-il gratuit ?', 'Oui, entièrement. Le logiciel est open source sous licence GPL-3.0, sans publicité ni version payante.'],
        ['Faut-il créer un compte ou donner un accès à ma chaîne ?', 'Non. Tout tourne sur votre PC. Le chat Twitch est lu en anonyme : aucun compte, aucune clé.'],
        ['Est-ce que ça ralentit mes jeux ?', "L'écran est analysé toutes les 2 secondes par défaut (0,5 s au minimum), sur de petits fragments d'image. Aucune injection dans le jeu : l'application lit seulement l'écran et la liste des processus."],
        ['Quelles résolutions sont prises en charge ?', "De 720p à 4K. Les captures de référence s'adaptent à la résolution de l'écran."],
      ],
      toutes: 'Toutes les questions et le dépannage',
    },
    appel: 'Lancez votre prochain live sans toucher à OBS.',
  },

  installation: {
    titre: 'Installer OBS Dynamics sur Windows',
    description: "Télécharger OBS Dynamics pour Windows, vérifier l'authenticité du fichier et configurer OBS en trois étapes.",
    surtitre: 'Installation',
    h1: 'Installer OBS Dynamics',
    chapo: 'Un seul fichier, rien à installer. Téléchargez, vérifiez, lancez.',
    osAria: "Système d'exploitation",
    windowsTitre: 'Windows 10 et 11',
    telecharger: 'Télécharger Dynamics.exe',
    version: 'Version', publiee: 'Publiée le', taille: 'Taille', mo: 'Mo',
    rapportVt: 'Rapport VirusTotal de cette version',
    bientot: 'Première version bientôt disponible.',
    enAttendant: ['En attendant, OBS Dynamics se lance ', 'depuis les sources', '.'],
    prerequis: ['Prérequis : Windows 10 ou 11, ', 'OBS Studio', ' 28 ou plus.'],
    smartscreen: {
      titre: 'Avertissement de Windows au premier lancement',
      intro: "Le fichier n'est pas encore signé par un certificat. Windows SmartScreen affiche donc « Windows a protégé votre ordinateur ».",
      etapes: ['Cliquez sur **Informations complémentaires**.', 'Vérifiez que le nom indiqué est bien `Dynamics.exe`.', 'Cliquez sur **Exécuter quand même**.'],
      apres: 'Avant cela, vous pouvez vérifier que le fichier est authentique : voir ci-dessous.',
    },
    verifier: {
      titre: 'Vérifier votre téléchargement',
      empreinteTitre: '1. Même empreinte',
      empreinteIntro: 'Dans PowerShell, dans le dossier du téléchargement :',
      empreinteApres: 'Le résultat doit être identique au SHA-256 affiché plus haut. Sinon, supprimez le fichier.',
      attestationTitre: '2. Construit par GitHub, à partir du code public',
      attestationIntro: ['Avec la ', 'CLI GitHub', ' :'],
      attestationApres: 'La commande indique le workflow et le commit exacts qui ont produit ce fichier.',
    },
    premiersPas: {
      titre: 'Premiers pas en 3 étapes',
      etapes: [
        ["Activez le serveur WebSocket d'OBS", "Dans OBS Studio : **Outils → Paramètres du serveur WebSocket**. Cochez **Activer le serveur WebSocket**, gardez le port `4455`, cochez **Activer l'authentification**, puis copiez le mot de passe depuis **Afficher les informations de connexion**."],
        ['Saisissez le mot de passe', 'Dans OBS Dynamics, onglet **Paramètres**, section **Connexion OBS WebSocket** : adresse `localhost`, port `4455`, le mot de passe copié, puis **Enregistrer**. Il est chiffré sur votre PC.'],
        ['Ajoutez un jeu', 'Onglet **Bibliothèque** : **Scanner Steam** importe vos jeux Steam, **+ Ajouter** ajoute un jeu hors Steam.'],
      ],
      howto: 'Configurer OBS Dynamics avec OBS Studio',
    },
    sources: { titre: 'Depuis les sources', intro: 'Python 3.12 ou plus est nécessaire.', voirCode: 'Voir le code sur GitHub' },
    mac: {
      titre: 'Version Mac en préparation',
      texte: "OBS Dynamics utilise aujourd'hui des fonctions propres à Windows. Aucune version Mac n'est disponible pour l'instant, et aucun fichier Mac n'est proposé au téléchargement.",
      prevenir: 'Être prévenu sur Discord',
    },
  },

  contact: {
    titre: 'Nous contacter — OBS Dynamics',
    description: "Réponses aux problèmes courants d'OBS Dynamics (connexion à OBS, détection des jeux, overlays), puis Discord ou mail si besoin.",
    surtitre: 'Nous contacter',
    h1: 'Une question, un problème ?',
    chapo: 'OBS Dynamics est développé par Blackoune, streamer sur Twitch. Projet indépendant et non commercial : gratuit, sans publicité, code ouvert.',
    mail: { titre: 'Par mail', delai: 'Réponse sous quelques jours.', bientot: 'Adresse bientôt disponible.' },
    discord: { titre: 'Sur Discord', texte: 'Le plus rapide : la communauté et Blackoune y répondent.', rejoindre: 'Rejoindre le Discord', bientot: 'Invitation bientôt disponible.' },
    assistant: {
      surtitre: 'Trouver la réponse', filAria: "Fil d'Ariane",
      quelProbleme: 'Quel est votre problème ?', quoiExactement: 'Que se passe-t-il exactement ?',
      satisfait: 'Êtes-vous satisfait ?', oui: 'Oui', non: 'Non',
      merci: 'Nous sommes heureux de vous avoir aidé.', retour: '← Retour',
      autre: 'Autre question', discordTitre: 'Posez votre question sur Discord',
      encartTitre: 'Continuons sur Discord', encartTexte: "Un membre de l'équipe reprend votre problème là où la FAQ s'arrête.",
      ticket: 'Ouvrir un ticket', recommande: 'recommandé', vocal: "Rejoindre le vocal d'aide",
      inviteBientot: 'Invitation Discord bientôt disponible.', resume: 'À coller dans votre ticket :',
      reponseConsultee: 'réponse consultée',
    },
    faqTitre: 'Questions fréquentes',
  },

  comparatif: {
    titre: 'OBS Dynamics, Advanced Scene Switcher ou changement manuel ? Comparatif',
    description: 'Comparatif factuel entre OBS Dynamics, le plugin Advanced Scene Switcher et le changement de scène manuel : fonctionnement, forces et limites de chacun.',
    h1: 'Quel outil pour changer de scène OBS ?',
    chapo: 'Trois façons de changer de scène pendant un live. Chacune a ses forces : ce tableau aide à choisir celle qui vous convient.',
    colonnes: ['', 'OBS Dynamics', 'Advanced Scene Switcher', 'Changement manuel'],
    lignes: [
      ['Type', 'Application Windows séparée', 'Plugin intégré à OBS', 'Raccourcis clavier ou Stream Deck'],
      ['Systèmes', 'Windows 10 et 11', 'Windows, macOS, Linux', 'Tous'],
      ['Prix', 'Gratuit, open source (GPL-3.0)', 'Gratuit, open source (GPL-2.0)', 'Gratuit (Stream Deck payant)'],
      ['Menu ou partie', "Détection visuelle prête à l'emploi, réglée depuis vos captures", 'Possible avec une condition vidéo, à configurer règle par règle', 'À la main, à chaque fois'],
      ['Mise en place', 'Un jeu, deux captures, deux scènes', 'Macros à construire : très souple, plus long à régler', 'Aucune'],
      ['Overlays inclus', 'Raccourcis médias, chat Twitch, widget musique', "Non, l'outil est centré sur l'automatisation", 'Non'],
      ['Automatisations générales', 'Centré sur le jeu et le live', 'Très large : fenêtres, audio, horaires, médias, et bien plus', 'Aucune'],
    ],
    forcesTitre: 'Forces et limites',
    outils: [
      ['OBS Dynamics', "Pensé pour un cas précis : basculer entre menu et partie sans écrire de règle pour chaque jeu. Overlays fournis. Limite : Windows uniquement, application à lancer à côté d'OBS."],
      ['Advanced Scene Switcher', "Le plugin d'automatisation le plus complet pour OBS, sur tous les systèmes. Limite : la détection menu / partie demande de construire et régler ses propres macros."],
      ['Changement manuel', 'Aucun outil à installer, contrôle total. Limite : une action à chaque bascule, facile à oublier en pleine partie.'],
    ],
    note: 'Advanced Scene Switcher est un projet indépendant, sans lien avec OBS Dynamics. Une information est inexacte ? Signalez-la sur GitHub.',
  },

  mentions: {
    titre: 'Mentions légales — OBS Dynamics',
    description: 'Éditeur, hébergeur et licence du site OBS Dynamics.',
    h1: 'Mentions légales',
    sections: [
      ['Éditeur', 'Ce site est édité par Blackoune, à titre personnel et non commercial. Contact : page « Nous contacter ».'],
      ['Hébergeur', 'GitHub, Inc., 88 Colin P. Kelly Jr. Street, San Francisco, CA 94107, États-Unis.'],
      ['Licence', 'Le logiciel OBS Dynamics et le code de ce site sont distribués sous licence GPL-3.0.'],
      ['Marques', 'OBS Dynamics est un projet indépendant, non affilié à OBS Project. OBS Studio, Twitch, Steam, Spotify, Deezer, Apple Music et les noms de jeux cités appartiennent à leurs propriétaires respectifs.'],
    ],
  },

  confidentialite: {
    titre: 'Confidentialité — OBS Dynamics',
    description: 'Ce site ne dépose aucun cookie et ne collecte aucune donnée.',
    h1: 'Confidentialité',
    sections: [
      ['Ce site', "Aucun cookie, aucune mesure d'audience, aucun formulaire, aucune ressource chargée depuis un autre site. Le questionnaire de la FAQ ne transmet rien : vos réponses restent dans votre navigateur."],
      ['Hébergement', 'Le site est hébergé par GitHub Pages. Comme tout serveur web, GitHub enregistre les adresses IP des visiteurs, selon sa propre politique de confidentialité.'],
      ['Le logiciel', "OBS Dynamics fonctionne en local et ne contient aucune télémétrie. Il se connecte seulement à OBS, à Twitch si vous utilisez le chat, et aux services de jaquettes (Steam, RAWG)."],
    ],
    lienGithub: 'Politique de confidentialité de GitHub',
  },
};

export type Textes = typeof fr;
