"""Chemins, journalisation et éveil DPI — socle de tous les autres modules.

Ce module ne dépend que de la bibliothèque standard : c'est ce qui garantit
qu'aucun cycle d'import ne peut naître ici, et qu'il peut être importé avant
customtkinter pour régler l'échelle DPI à temps.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import shutil
import sys
from pathlib import Path


def enable_dpi_awareness() -> None:
    """Déclare le processus « DPI aware » AVANT tout import de customtkinter.

    Sans cela CTk calcule ses tailles de widgets sur un facteur d'échelle
    incorrect et la fenêtre s'ouvre plus petite que son contenu : sidebar qui
    déborde sur le dashboard, texte de bouton coupé. Sans effet hors Windows.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass  # plateforme/version Windows sans cette API — dégradation silencieuse


# ============================================================================
# CHEMINS / CONSTANTES
# ============================================================================
def get_base_path() -> Path:
    """Racine des ressources EMPAQUETÉES, en lecture seule : i18n.json, assets/.

    En mode figé, PyInstaller (--onefile) extrait ces fichiers dans un dossier
    temporaire exposé par `sys._MEIPASS` — surtout PAS à côté du .exe. Renvoyer
    le dossier de l'exe faisait chercher i18n.json dans `dist_release/`, où il
    n'a jamais existé : le chargement échouait et l'interface repartait sur ses
    libellés de secours, alors que `python obs_dynamics.py` affichait les vrais.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parent


BASE_DIR = get_base_path()
ASSETS_DIR = BASE_DIR / "assets"
ICON_PATH = ASSETS_DIR / "icon.ico"
I18N_PATH = BASE_DIR / "i18n.json"


def get_user_config_dir() -> Path:
    """Dossier de configuration propre à l'utilisateur, HORS du dépôt.

    Le mot de passe OBS WebSocket, la clé RAWG et tout identifiant de compte
    ajouté plus tard n'ont rien à faire dans le dossier du projet : une purge
    de l'historique, un `git clean` ou une réinstallation les emportait, et il
    fallait tout resaisir. Ici ils survivent à n'importe quelle manipulation
    du dépôt et ne peuvent structurellement pas être committés.
    """
    if sys.platform == "win32":
        root = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    else:  # Linux/macOS : convention XDG, utile pour les tests hors Windows
        root = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    path = Path(root) / "OBS Dynamics"
    path.mkdir(parents=True, exist_ok=True)
    return path


USER_CONFIG_DIR = get_user_config_dir()
ENV_PATH = USER_CONFIG_DIR / ".env"

# Données utilisateur : UN SEUL emplacement, partagé par le .exe et par
# `python obs_dynamics.py`. Les faire vivre à côté du code donnait deux
# bibliothèques distinctes — celle du dépôt en mode script, celle de
# `dist_release/` en mode figé — donc un .exe qui s'ouvrait sans aucun jeu.
# Ici l'emplacement ne dépend plus de d'où le programme a été lancé.
DATA_DIR = USER_CONFIG_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
COVERS_DIR = DATA_DIR / "covers"



def migrate_legacy_env(legacy: Path, target: Path) -> bool:
    """Reprend un ancien `.env` resté à la racine du dépôt.

    Copie puis renomme l'ancien en `.env.old` : l'utilisateur garde ses
    réglages sans rien resaisir, et il n'existe plus qu'UNE source de vérité
    (éditer l'ancien fichier n'aurait plus aucun effet, ce qui serait pire
    qu'un fichier renommé et visible).
    """
    if target.exists() or not legacy.exists():
        return False
    try:
        target.write_bytes(legacy.read_bytes())
        # with_suffix() est piégeux sur un fichier commençant par un point :
        # Path(".env").with_suffix(".old") donne ".env.old" mais
        # with_suffix(".env.old") donnait ".env.env.old". On compose le nom.
        legacy.replace(legacy.with_name(legacy.name + ".old"))
    except OSError:
        logger.exception("Migration de %s vers %s impossible.", legacy, target)
        return False
    logger.info("Identifiants déplacés vers %s (l'ancien .env est devenu .env.old).", target)
    return True


def migrate_legacy_data(legacy: Path, target: Path) -> bool:
    """Reprend un dossier `data/` resté à côté du code ou du .exe.

    Même geste que migrate_legacy_env : copier puis renommer l'ancien en
    `data.old`, pour qu'il ne subsiste qu'UNE bibliothèque visible. Les
    journaux ne sont pas repris — sans valeur, et le fichier courant est déjà
    ouvert par le handler de rotation, donc Windows refuserait de l'écraser et
    ferait échouer toute la migration.

    `games.json` sert de sentinelle dans les deux sens : absent de l'ancien
    dossier, il n'y a rien qui vaille une reprise ; présent dans la cible, la
    migration a déjà eu lieu et écraser serait une perte de données.
    """
    if not legacy.is_dir() or not (legacy / "games.json").exists():
        return False
    if (target / "games.json").exists():
        return False
    try:
        if legacy.resolve() == target.resolve():
            return False
        shutil.copytree(legacy, target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("*.log", "*.log.*"))
        legacy.replace(legacy.with_name(legacy.name + ".old"))
    except OSError:
        logger.exception("Migration de %s vers %s impossible.", legacy, target)
        return False
    logger.info("Bibliothèque déplacée vers %s (l'ancien dossier est devenu data.old).", target)
    return True
GAMES_PATH = DATA_DIR / "games.json"
HOTKEYS_PATH = DATA_DIR / "hotkeys.json"
TRIGGERS_PATH = DATA_DIR / "triggers.json"
# Le NOM DU FICHIER reste "multistream.json" alors que tout le reste a été
# renommé en twitch_chat le 2026-09-09. Ce n'est pas un oubli : le jeton
# permanent de l'overlay de chat y est persisté. Pointer vers un fichier au
# nouveau nom en trouverait un vide, donc en régénérerait un — et la source
# navigateur déjà collée dans OBS cesserait de répondre.
TWITCH_CHAT_PATH = DATA_DIR / "multistream.json"
LOG_PATH = DATA_DIR / "obs_dynamics.log"

# Rotation des logs : sans elle obs_dynamics.log grossit indéfiniment (la
# boucle de scan écrit à chaque bascule de scène). 2 Mo x 3 fichiers = 6 Mo
# au maximum sur disque. Le handler fichier est best-effort : si le dossier
# est en lecture seule, on continue en console seule plutôt que de planter.
_log_handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
try:
    _log_handlers.insert(0, logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
except OSError:
    print(f"[warn] Journalisation fichier désactivée ({LOG_PATH} inaccessible).", file=sys.stderr)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("obs_dynamics")

# Reprise d'une installation antérieure où .env vivait dans le dépôt.
migrate_legacy_env(BASE_DIR / ".env", ENV_PATH)

# Reprise de la bibliothèque d'une installation antérieure. Deux emplacements
# historiques : à côté des sources (lancement `python obs_dynamics.py`) et à
# côté du .exe (lancement du binaire figé). Le premier qui porte un games.json
# gagne ; les autres sont laissés intacts.
for _legacy_data in (Path(__file__).parent / "data", Path(sys.executable).parent / "data"):
    if migrate_legacy_data(_legacy_data, DATA_DIR):
        break

# Le chemin exact est journalisé : il dépend de %APPDATA%, donc du compte et
# de l'environnement de lancement. Sans cette trace, un réglage « qui ne
# s'enregistre pas » est indiagnosticable — on ne sait même pas quel fichier
# regarder.
logger.info("Fichier de configuration : %s (existe : %s)", ENV_PATH, ENV_PATH.exists())
