"""Chemins, journalisation et éveil DPI — socle de tous les autres modules.

Ce module ne dépend que de la bibliothèque standard : c'est ce qui garantit
qu'aucun cycle d'import ne peut naître ici, et qu'il peut être importé avant
customtkinter pour régler l'échelle DPI à temps.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
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
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


BASE_DIR = get_base_path()
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
COVERS_DIR = DATA_DIR / "covers"


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
GAMES_PATH = DATA_DIR / "games.json"
HOTKEYS_PATH = DATA_DIR / "hotkeys.json"
TRIGGERS_PATH = DATA_DIR / "triggers.json"
# Le NOM DU FICHIER reste "multistream.json" alors que tout le reste a été
# renommé en twitch_chat le 2026-09-09. Ce n'est pas un oubli : le jeton
# permanent de l'overlay de chat y est persisté. Pointer vers un fichier au
# nouveau nom en trouverait un vide, donc en régénérerait un — et la source
# navigateur déjà collée dans OBS cesserait de répondre.
TWITCH_CHAT_PATH = DATA_DIR / "multistream.json"
ASSETS_DIR = BASE_DIR / "assets"
ICON_PATH = ASSETS_DIR / "icon.ico"
LOG_PATH = DATA_DIR / "obs_dynamics.log"
I18N_PATH = BASE_DIR / "i18n.json"

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

# Le chemin exact est journalisé : il dépend de %APPDATA%, donc du compte et
# de l'environnement de lancement. Sans cette trace, un réglage « qui ne
# s'enregistre pas » est indiagnosticable — on ne sait même pas quel fichier
# regarder.
logger.info("Fichier de configuration : %s (existe : %s)", ENV_PATH, ENV_PATH.exists())
