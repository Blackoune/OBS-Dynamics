"""Refuse tout commit qui embarquerait un identifiant.

Le dépôt est **public**. Un mot de passe committé y reste : le retirer du
HEAD ne le retire pas de l'historique, et une purge n'atteint jamais les
clones et forks déjà faits. Le seul moment où c'est réparable est *avant* le
commit — d'où ce contrôle.

Branché en tant que hook `pre-commit` (voir `.githooks/pre-commit`), il
inspecte le contenu INDEXÉ, pas le fichier de travail : c'est ce qui part
réellement dans le commit.

Utilisation :
    python check_secrets.py --staged   # ce qui est indexé (le hook)
    python check_secrets.py --all      # tout le HEAD, pour un audit
    python check_secrets.py --history  # chaque blob de tout l'historique

Sort avec 1 dès qu'une trouvaille bloque le commit.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys

# --- 1. Fichiers qui n'ont RIEN à faire dans un commit -------------------- #
# Motifs de chemin, pas de contenu : même vides, ces fichiers ne se versionnent
# jamais. `.env.example` est l'exception voulue — c'est un gabarit sans valeur.
CHEMINS_INTERDITS = (
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)\.env\.(old|backup|bak|local)$"),
    re.compile(r"(^|/)obs_config\.json$"),
    re.compile(r"(^|/)config\.json$"),
    # Sauf `site/src/data/` : code source du site (FAQ, lecture des releases),
    # aucune donnée d'utilisateur. Son contenu reste analysé comme le reste.
    re.compile(r"(^|/)(?<!site/src/)data/"),
    # Journaux : un message d'erreur `requests` recopie l'URL appelée, clé
    # comprise. C'est ainsi qu'une clé de stream est arrivée dans
    # `data.old/obs_dynamics.log`, puis dans le commit 833d1df.
    re.compile(r"(^|/)data\.old/"),
    re.compile(r"\.log(\.\d+)?$"),
)

# `data/hotkeys.json` est suivi depuis avant que `data/` n'entre au
# .gitignore. Il ne contient que les touches par défaut ({"f1": "in_game"}…),
# aucun identifiant. On l'exempte explicitement plutôt que d'assouplir la
# règle `data/` : sans ça `--all` sortirait en erreur à chaque audit, et un
# contrôle qui crie au loup finit désactivé.
EXEMPTIONS = frozenset({"data/hotkeys.json"})

# --- 2. Secrets connus comme ayant fuité ---------------------------------- #
# Stockés en SHA-256 : écrire la valeur ici la republierait, ce qui serait
# exactement le problème qu'on essaie de résoudre. Un condensat suffit à
# reconnaître la valeur si elle repasse.
#
# e934dd... = mot de passe OBS WebSocket publié dans `752b027` le 2026-08-04.
# Rotation faite le 2026-09-09 : cette valeur n'ouvre plus rien. Le condensat
# RESTE ici quand même — il ne coûte rien, et il refuse le commit si la
# vieille valeur reparaissait un jour dans un fichier ou un exemple.
CONDENSATS_INTERDITS = {
    "e934dd0dec1c18ad2b255889391b0ffa74451448529c675fe0e9391b99447264":
        "mot de passe OBS WebSocket publié dans le commit 752b027",
    # d85f4c... = clé de stream recopiée dans un journal versionné
    # (`data.old/obs_dynamics.log`, commit 833d1df). Réinitialisée le
    # 2026-09-24 : elle n'ouvre plus rien. Le condensat reste, pour la même
    # raison que celui du mot de passe OBS.
    "d85f4c7a8012bbbf1c010944db5d6d56d1383accac8610c7c1a335d539d625f3":
        "clé de stream publiée dans le commit 833d1df",
}

# Ce qui ressemble à une valeur de secret dans un texte quelconque : on hache
# chaque candidat et on compare. 8 caractères au minimum, sinon on hacherait
# la moitié du code source pour rien.
CANDIDATS = re.compile(r"[A-Za-z0-9+/=_\-]{8,128}")

# --- 3. Affectations en clair --------------------------------------------- #
# `enc:vN:` = déjà chiffré par secret_store, donc inoffensif. Une valeur vide
# ou un gabarit explicite passent aussi : c'est ce que contient .env.example.
GABARITS = {"changeme", "votre_mot_de_passe", "xxx", "your_password_here",
            "secret", "motdepasse", "password"}
AFFECTATIONS = (
    # `[ \t]`, pas `\s` : `\s` avale le saut de ligne, et une clé laissée vide
    # (`OBS_WS_PASSWORD=`) prenait la ligne SUIVANTE pour sa valeur.
    re.compile(r"^[ \t]*(OBS_WS_PASSWORD|RAWG_API_KEY)[ \t]*=[ \t]*(?P<valeur>\S+)", re.M),
    re.compile(r'"(?:password|client_secret|api_key|token)"\s*:\s*"(?P<valeur>[^"]+)"'),
)

# --- 4. Secrets reconnaissables à leur FORME ------------------------------ #
# Les règles précédentes cherchent une affectation (`CLE=valeur`). Un secret
# recopié AILLEURS — dans l'URL d'un message d'erreur, par exemple — leur
# échappait : c'est exactement comme ça que la clé de stream est passée.
# Ces formes-là se reconnaissent seules, où qu'elles apparaissent.
MOTIFS_DE_SECRETS = (
    # Clé de stream en cinq groupes de quatre caractères, en minuscules,
    # séparés par des tirets — la forme de celle qui a fuité. Les bornes
    # excluent un UUID (8-4-4-4-12), qui n'a jamais cette forme.
    (re.compile(r"(?<![a-z0-9-])[a-z0-9]{4}(?:-[a-z0-9]{4}){4}(?![a-z0-9-])"),
     "clé de stream"),
    # Clé de stream Twitch : `live_<identifiant>_<suite aléatoire>`. C'est la
    # plateforme sur laquelle on diffuse : la sienne mérite la même garde.
    (re.compile(r"live_[0-9]{5,}_[A-Za-z0-9]{20,}"), "clé de stream Twitch"),
    # Adresse d'ingestion RTMP, en clair ou encodée dans une URL : elle porte
    # la clé de stream en dernier segment.
    (re.compile(r"rtmps?(?::|%3A)(?://|%2F%2F)", re.I),
     "adresse de diffusion RTMP (porte la clé de stream)"),
    # Clé d'API Google.
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "clé d'API Google"),
)


def _git(*args: str) -> str:
    resultat = subprocess.run(("git",) + args, capture_output=True, text=True)
    if resultat.returncode != 0:
        print(f"[check-secrets] git {' '.join(args)} a échoué :\n{resultat.stderr}",
              file=sys.stderr)
        sys.exit(1)
    return resultat.stdout


def fichiers_indexes() -> list[str]:
    sortie = _git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    return [ligne for ligne in sortie.splitlines() if ligne.strip()]


def fichiers_du_head() -> list[str]:
    """Tout ce qui est versionnable : suivi + non suivi non ignoré.

    `git ls-files` seul ne rend que les fichiers DÉJÀ suivis — donc un audit
    lancé avant le premier commit de nouveaux modules ne les regardait pas,
    et laissait passer ce que le hook allait refuser une minute plus tard.
    `--others --exclude-standard` ajoute les nouveaux fichiers en respectant
    le .gitignore.
    """
    suivis = _git("ls-files").splitlines()
    nouveaux = _git("ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({ligne for ligne in suivis + nouveaux if ligne.strip()})


def contenu_indexe(chemin: str) -> str:
    resultat = subprocess.run(("git", "show", f":{chemin}"),
                              capture_output=True, text=True, errors="replace")
    return resultat.stdout if resultat.returncode == 0 else ""


def contenu_disque(chemin: str) -> str:
    try:
        with open(chemin, encoding="utf-8", errors="replace") as fichier:
            return fichier.read()
    except OSError:
        return ""


#: En dessous, ce n'est pas un mot de passe mais une illustration : `…`, `x`,
#: `***`. La doc en contient — et un contrôle qui refuse le fichier
#: expliquant le contrôle finit désactivé. Les mots de passe OBS WebSocket
#: font 16 caractères, une clé RAWG 32 : le plancher ne masque rien de réel.
LONGUEUR_MINIMALE = 8

#: Un NOM de variable d'environnement, jamais sa valeur. `env_config.py` fait
#: correspondre les champs d'OBSConfig aux clés du .env :
#:
#:     "password": "OBS_WS_PASSWORD"
#:
#: ce que la règle JSON prenait pour un identifiant de 15 caractères. Exiger
#: MAJUSCULES + au moins un souligné distingue `OBS_WS_PASSWORD` d'un vrai
#: mot de passe, qui n'a aucune raison d'avoir cette forme.
NOM_DE_VARIABLE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")


def _valeur_inoffensive(valeur: str) -> bool:
    valeur = valeur.strip().strip('"').strip("'")
    return (len(valeur) < LONGUEUR_MINIMALE
            or NOM_DE_VARIABLE.match(valeur) is not None
            or valeur.startswith(("enc:v2:", "enc:v1:"))
            or valeur.lower() in GABARITS
            or valeur.startswith("{")          # placeholder de gabarit
            or valeur.startswith("$"))         # référence d'environnement


def analyser(chemin: str, texte: str) -> list[str]:
    """Trouvailles bloquantes pour ce fichier."""
    trouvailles = []

    for candidat in set(CANDIDATS.findall(texte)):
        motif = CONDENSATS_INTERDITS.get(
            hashlib.sha256(candidat.encode("utf-8")).hexdigest())
        if motif:
            trouvailles.append(f"{chemin} : contient un secret connu — {motif}")

    # Les tests et le gabarit contiennent des valeurs factices assumées ; le
    # contrôle par condensat ci-dessus, lui, s'y applique quand même.
    if chemin.startswith("tests/") or chemin.endswith(".env.example"):
        return trouvailles

    for motif in AFFECTATIONS:
        for correspondance in motif.finditer(texte):
            valeur = correspondance.group("valeur")
            if not _valeur_inoffensive(valeur):
                ligne = texte[:correspondance.start()].count("\n") + 1
                trouvailles.append(
                    f"{chemin}:{ligne} : identifiant en clair "
                    f"({len(valeur.strip(chr(34)))} caractères)")

    for motif, nature in MOTIFS_DE_SECRETS:
        correspondance = motif.search(texte)
        if correspondance:
            # La position seulement, jamais la valeur : ce message s'affiche
            # dans un terminal, et part parfois dans un rapport de CI.
            ligne = texte[:correspondance.start()].count("\n") + 1
            trouvailles.append(f"{chemin}:{ligne} : {nature}")
    return trouvailles


def auditer_historique() -> int:
    """Chaque blob de chaque commit, pas seulement le HEAD.

    Avant de rendre le dépôt public : un secret retiré du HEAD reste lisible
    dans l'historique. Les fuites déjà connues (condensats ci-dessus) sortent
    aussi — le rapport sert à vérifier qu'il n'y en a PAS d'autres.
    """
    objets = [ligne.split(" ", 1) for ligne in _git("rev-list", "--all", "--objects").splitlines()
              if " " in ligne]
    types = dict(ligne.split() for ligne in subprocess.run(
        ("git", "cat-file", "--batch-check=%(objectname) %(objecttype)"),
        input="\n".join(sha for sha, _ in objets), capture_output=True, text=True,
    ).stdout.splitlines())

    trouvailles: dict[str, list[str]] = {}
    for sha, chemin in objets:
        if types.get(sha) != "blob" or chemin in EXEMPTIONS:
            continue
        if any(motif.search(chemin) for motif in CHEMINS_INTERDITS):
            trouvailles.setdefault(sha, []).append(f"{chemin} : ce fichier ne se versionne jamais")
            # Pas de `continue` : on veut savoir ce que le fichier contenait.
        brut = subprocess.run(("git", "cat-file", "blob", sha), capture_output=True).stdout
        if b"\0" in brut[:8000]:
            continue  # binaire (images, .ico) : pas de texte à lire
        resultats = analyser(chemin, brut.decode("utf-8", errors="replace"))
        if resultats:
            trouvailles.setdefault(sha, []).extend(resultats)

    for sha, lignes in trouvailles.items():
        commits = _git("log", "--all", "--format=%h", f"--find-object={sha}").split()
        for ligne in lignes:
            print(f"  - [{', '.join(commits) or '?'}] {ligne}")
    print(f"[check-secrets] historique : {len(objets)} objet(s), "
          f"{len(trouvailles)} blob(s) signalé(s).")
    return 1 if trouvailles else 0


def main() -> int:
    if "--history" in sys.argv:
        return auditer_historique()
    mode_complet = "--all" in sys.argv
    chemins = fichiers_du_head() if mode_complet else fichiers_indexes()
    lire = contenu_disque if mode_complet else contenu_indexe

    trouvailles: list[str] = []
    for chemin in chemins:
        normalise = chemin.replace("\\", "/")
        if normalise in EXEMPTIONS:
            continue
        if any(motif.search(normalise) for motif in CHEMINS_INTERDITS):
            trouvailles.append(f"{chemin} : ce fichier ne se versionne jamais")
            continue
        trouvailles.extend(analyser(normalise, lire(chemin)))

    if not trouvailles:
        print(f"[check-secrets] {len(chemins)} fichier(s) inspecté(s), rien à signaler.")
        return 0

    print("[check-secrets] COMMIT REFUSÉ — ce dépôt est public.", file=sys.stderr)
    for trouvaille in trouvailles:
        print(f"  - {trouvaille}", file=sys.stderr)
    print("\nUn secret committé reste dans l'historique, et une purge n'atteint",
          file=sys.stderr)
    print("jamais les clones déjà faits. Retire-le de l'index, puis recommence :",
          file=sys.stderr)
    print("    git restore --staged <fichier>", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
