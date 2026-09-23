"""
twitch_chat.py — Chat Twitch diffusé vers une source navigateur OBS.

Le connecteur Twitch tourne dans son propre thread et normalise ses messages
vers `ChatMessage`. Le `ChatHub` les distribue aux pages overlay connectées
(voir `overlay_server.py`).

> **Twitch uniquement.** La structure reste multi-plateforme (`PLATFORMS`,
> filtre et couleur par plateforme) : elle ne coûte rien, mais Twitch est la
> seule plateforme prise en charge.

Deux garanties structurelles :

1. **Isolation** — un connecteur qui plante ne remonte jamais son exception :
   elle est journalisée, l'état passe en `ERROR`, et une reconnexion est
   planifiée avec un backoff exponentiel bruité. Le serveur HTTP ne voit rien.
2. **Permanence** — le jeton de l'URL overlay est généré UNE fois puis
   persisté dans `data/multistream.json`. Un redémarrage de l'application
   relit ce jeton : la source navigateur configurée dans OBS reste valide.
   Le nom de ce fichier ne change pas, précisément pour ne pas invalider les
   URL déjà collées dans OBS.
"""
from __future__ import annotations

import codecs
import json
import logging
import queue
import random
import re
import secrets
import socket
import ssl
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import secret_store

logger = logging.getLogger("obs_dynamics.twitch_chat")

# Ordre d'affichage des cartes dans l'onglet. C'est aussi l'ordre canonique
# utilisé partout ailleurs (persistance, statuts, tests).
PLATFORMS: tuple[str, ...] = ("twitch",)

# Couleur d'accent par plateforme, reprise de leurs chartes respectives.
PLATFORM_COLORS: dict[str, str] = {
    "twitch": "#9146FF",
}

# Nombre de messages conservés pour réamorcer une page overlay qui vient de
# se (re)connecter : sans historique, une source navigateur rouverte affiche
# un chat vide jusqu'au message suivant.
HISTORY_SIZE = 80

# Taille de la file par abonné SSE. Même politique d'éviction que le broker
# des déclencheurs : on jette le PLUS ANCIEN, jamais le plus récent.
_SUB_QUEUE_SIZE = 200


class Status:
    """États possibles d'un connecteur (chaînes, pas Enum : elles transitent
    telles quelles en JSON vers la page overlay et l'interface)."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    NEEDS_CONFIG = "needs_config"   # nom de chaîne manquant


# ============================================================================
# MODÈLE
# ============================================================================
@dataclass(frozen=True)
class ChatMessage:
    """Message normalisé, tel qu'il transite vers la page overlay."""

    id: str
    platform: str
    author: str
    text: str
    color: str = ""
    badges: tuple[str, ...] = ()
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "platform": self.platform,
            "author": self.author,
            "text": self.text,
            "color": self.color or PLATFORM_COLORS.get(self.platform, "#FFFFFF"),
            "badges": list(self.badges),
            "timestamp": self.timestamp,
        }


@dataclass
class PlatformConfig:
    """Réglages persistés d'une plateforme.

    `enabled` ne coupe PAS le connecteur : il masque les messages dans le flux
    diffusé. C'est ce que demande l'usage — masquer un chat bruyant quelques
    minutes sans perdre la session ni payer une reconnexion au retour.
    """

    enabled: bool = True
    channel: str = ""        # nom de chaîne à suivre
    account: str = ""        # identité affichée dans l'interface

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "channel": self.channel, "account": self.account}

    @staticmethod
    def from_dict(data: Any) -> "PlatformConfig":
        if not isinstance(data, dict):
            return PlatformConfig()
        return PlatformConfig(
            enabled=bool(data.get("enabled", True)),
            channel=str(data.get("channel", "") or "").strip(),
            account=str(data.get("account", "") or "").strip(),
        )


@dataclass
class TwitchChatConfig:
    overlay_token: str = ""
    platforms: dict[str, PlatformConfig] = field(default_factory=dict)

    def platform(self, name: str) -> PlatformConfig:
        return self.platforms.setdefault(name, PlatformConfig())

    def to_dict(self) -> dict[str, Any]:
        # Le jeton part chiffre au repos (DPAPI), comme les identifiants du
        # .env : c'est le seul controle d'acces de la page de chat, il n'a pas
        # de raison de rester lisible dans un fichier JSON.
        return {
            "overlay_token": secret_store.encrypt(self.overlay_token),
            "platforms": {name: self.platform(name).to_dict() for name in PLATFORMS},
        }

    @staticmethod
    def from_dict(data: Any) -> "TwitchChatConfig":
        raw = data if isinstance(data, dict) else {}
        raw_platforms = raw.get("platforms")
        raw_platforms = raw_platforms if isinstance(raw_platforms, dict) else {}
        return TwitchChatConfig(
            overlay_token=secret_store.decrypt(str(raw.get("overlay_token", "") or "")),
            platforms={name: PlatformConfig.from_dict(raw_platforms.get(name))
                       for name in PLATFORMS},
        )


class TwitchChatStore:
    """Persistance JSON locale — même contrat que `TriggerStore` : écriture
    atomique par fichier temporaire, cache invalidé sur (mtime, taille),
    `load()` retourne une copie que l'appelant peut muter librement."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._cached: Optional[TwitchChatConfig] = None
        self._cache_stamp: Optional[tuple[float, int]] = None

    def _stamp(self) -> Optional[tuple[float, int]]:
        try:
            st = self._path.stat()
            return (st.st_mtime, st.st_size)
        except OSError:
            return None

    @staticmethod
    def _copy(cfg: TwitchChatConfig) -> TwitchChatConfig:
        return TwitchChatConfig(
            overlay_token=cfg.overlay_token,
            platforms={name: replace(pcfg) for name, pcfg in cfg.platforms.items()},
        )

    @staticmethod
    def _defaults() -> TwitchChatConfig:
        return TwitchChatConfig(platforms={name: PlatformConfig() for name in PLATFORMS})

    def load(self) -> TwitchChatConfig:
        with self._lock:
            stamp = self._stamp()
            if self._cached is not None and stamp is not None and stamp == self._cache_stamp:
                return self._copy(self._cached)
            if not self._path.exists():
                return self._defaults()
            try:
                cfg = TwitchChatConfig.from_dict(
                    json.loads(self._path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                logger.exception("multistream.json invalide, réglages par défaut utilisés.")
                return self._defaults()
            self._cached, self._cache_stamp = self._copy(cfg), stamp
            return cfg

    def save(self, cfg: TwitchChatConfig) -> bool:
        with self._lock:
            try:
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False),
                               encoding="utf-8")
                tmp.replace(self._path)
                self._cached, self._cache_stamp = None, None
                return True
            except OSError:
                logger.exception("Échec sauvegarde multistream.json.")
                return False

    def _token_needs_rewrite(self) -> bool:
        """Le jeton sur disque est-il hors du format de chiffrement courant —
        en clair, ou blob d'une version antérieure ? Lu sur le fichier brut :
        `load()` déchiffre, donc il ne peut pas répondre à cette question."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return False
        jeton = str(raw.get("overlay_token", "") or "") if isinstance(raw, dict) else ""
        return secret_store.needs_rewrite(jeton)

    def ensure_token(self) -> str:
        """Retourne le jeton overlay, en le créant au tout premier appel.

        C'est le point qui rend le lien permanent : régénérer un jeton à
        chaque démarrage casserait la source navigateur déjà collée dans OBS.
        """
        cfg = self.load()
        if cfg.overlay_token:
            # Fichier hérité (clair, ou blob d'une version antérieure) :
            # réécrit une fois au format courant. Sans ça, un jeton déjà créé
            # ne serait converti qu'au prochain changement de réglage du chat
            # — donc peut-être jamais.
            if secret_store.available() and self._token_needs_rewrite():
                self.save(cfg)
                logger.info("Jeton overlay du chat chiffré au repos (DPAPI).")
            return cfg.overlay_token
        cfg.overlay_token = secrets.token_urlsafe(24)
        self.save(cfg)
        logger.info("Jeton overlay du chat créé et persisté.")
        return cfg.overlay_token

    def regenerate_token(self) -> str:
        """Invalide l'ancien lien. À n'utiliser que si l'utilisateur pense
        que l'URL a fuité : toute source OBS existante devra être recollée."""
        cfg = self.load()
        cfg.overlay_token = secrets.token_urlsafe(24)
        self.save(cfg)
        logger.warning("Jeton overlay régénéré : les anciennes URL sont mortes.")
        return cfg.overlay_token

    def set_platform(self, name: str, pcfg: PlatformConfig) -> bool:
        cfg = self.load()
        cfg.platforms[name] = pcfg
        return self.save(cfg)


# ============================================================================
# CONNECTEUR
# ============================================================================
class BaseConnector:
    """Squelette commun : thread, cycle de vie, reconnexion, isolation.

    Une sous-classe n'implémente que `_session()`, qui doit lire son flux et
    rendre la main (ou lever) quand la connexion est perdue. Tout le reste —
    backoff exponentiel bruité, transitions d'état, absorption des
    exceptions — est mutualisé ici.
    """

    #: Nom de plateforme, renseigné par la sous-classe.
    platform: str = ""

    BACKOFF_BASE = 2.0
    BACKOFF_MAX = 60.0
    BACKOFF_JITTER = 0.25   # +/- 25 % : évite des reconnexions toutes en phase

    def __init__(self, config: PlatformConfig,
                 on_message: Callable[[ChatMessage], None],
                 on_status: Callable[[str, str, str], None]) -> None:
        self._config = config
        self._on_message = on_message
        self._on_status = on_status
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._status = Status.DISCONNECTED
        self._detail = ""

    # -- État -------------------------------------------------------------- #

    @property
    def status(self) -> str:
        return self._status

    @property
    def detail(self) -> str:
        return self._detail

    @property
    def config(self) -> PlatformConfig:
        return self._config

    def _set_status(self, status: str, detail: str = "") -> None:
        if (status, detail) == (self._status, self._detail):
            return
        self._status, self._detail = status, detail
        try:
            self._on_status(self.platform, status, detail)
        except Exception:
            logger.exception("Callback de statut %s en erreur (ignoré).", self.platform)

    # -- Cycle de vie ------------------------------------------------------ #

    def is_configured(self) -> bool:
        """Vrai si le connecteur a de quoi travailler."""
        # Un nom mal formé n'est pas « configuré » : la carte affiche alors
        # « à configurer » au lieu de tenter une connexion vouée à l'échec.
        return bool(normalise_channel(self._config.channel))

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not self.is_configured():
            self._set_status(Status.NEEDS_CONFIG)
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_forever, daemon=True,
                                        name=f"chat-{self.platform}")
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._set_status(Status.DISCONNECTED)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _backoff_delay(self, attempt: int) -> float:
        raw = min(self.BACKOFF_MAX, self.BACKOFF_BASE * (2 ** min(attempt, 6)))
        return max(1.0, raw * (1.0 + random.uniform(-self.BACKOFF_JITTER, self.BACKOFF_JITTER)))

    def _run_forever(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            # « Connexion en cours » n'est annoncé que si l'état ne dit pas
            # déjà quelque chose de plus juste : repasser par CONNECTING à
            # chaque tour ferait clignoter la carte.
            if self._status != Status.CONNECTED:
                self._set_status(Status.CONNECTING)
            try:
                self._session()
            except Exception as exc:
                # Isolation : l'exception meurt ici. Le hub et le serveur HTTP
                # ne doivent jamais tomber parce qu'une plateforme a coupé sa
                # socket.
                logger.warning("Connecteur %s interrompu : %s", self.platform, exc)
                logger.debug("Détail du connecteur %s", self.platform, exc_info=True)
                self._set_status(Status.ERROR, str(exc)[:120])
            else:
                # Sortie propre : ce n'est PAS un échec, le backoff repart
                # de zéro.
                attempt = 0
                self._set_status(Status.DISCONNECTED)
            if self._stop.is_set():
                break
            attempt += 1
            delay = self._backoff_delay(attempt)
            logger.info("Reconnexion %s dans %.1f s (tentative %d).",
                        self.platform, delay, attempt)
            if self._stop.wait(delay):
                break
        self._set_status(Status.DISCONNECTED)

    def _session(self) -> None:
        raise NotImplementedError

    def _emit(self, author: str, text: str, color: str = "",
              badges: Iterable[str] = ()) -> None:
        if not text:
            return
        self._on_message(ChatMessage(
            id=uuid.uuid4().hex, platform=self.platform, author=author or "?",
            text=text, color=color, badges=tuple(badges), timestamp=time.time()))


# --- Twitch ---------------------------------------------------------------- #

#: Forme d'un nom de chaîne Twitch : lettres, chiffres, souligné, 25 au plus.
#:
#: Le nom part tel quel dans `JOIN #<chaîne>` : un retour à la ligne collé
#: avec lui y aurait ajouté une commande IRC de plus. Sans conséquence sur une
#: session anonyme en lecture seule, mais rien ne justifie de l'envoyer.
_CHAINE = re.compile(r"[a-z0-9_]{1,25}")


def normalise_channel(saisie: str) -> str:
    """Le nom de chaîne prêt pour `JOIN`, ou "" s'il n'en a pas la forme."""
    chaine = (saisie or "").strip().lstrip("#").lower()
    return chaine if _CHAINE.fullmatch(chaine) else ""


_IRC_HOST = "irc.chat.twitch.tv"
_IRC_TLS_PORT = 6697
# Twitch envoie un PING toutes les ~5 min. Au-delà de ce silence la connexion
# est considérée morte : on sort pour laisser le backoff reconnecter.
_IRC_SILENCE_LIMIT = 400.0
_IRC_LINE_RE = re.compile(
    r"^(?:@(?P<tags>[^ ]*) )?:(?P<nick>[^!]+)![^ ]+ PRIVMSG #(?P<chan>[^ ]+) :(?P<text>.*)$")


def parse_irc_tags(raw: str) -> dict[str, str]:
    """Découpe la section `@k=v;k2=v2` d'une ligne IRCv3.

    Les valeurs sont échappées par le protocole (`\\s` code un espace) ; on
    dés-échappe les séquences réellement utilisées par Twitch.
    """
    tags: dict[str, str] = {}
    for chunk in raw.split(";"):
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        tags[key] = (value.replace(r"\s", " ").replace(r"\:", ";")
                          .replace("\\\\", "\\").replace(r"\r", "").replace(r"\n", ""))
    return tags


def parse_privmsg(line: str) -> Optional[dict[str, Any]]:
    """Extrait auteur, texte, couleur et badges d'une ligne PRIVMSG Twitch.

    Retourne None pour toute autre ligne (PING, JOIN, NOTICE, USERSTATE...) —
    c'est le filtre qui évite de publier du bruit protocolaire dans le chat.
    """
    match = _IRC_LINE_RE.match(line.strip())
    if match is None:
        return None
    tags = parse_irc_tags(match.group("tags") or "")
    badges = tuple(b.split("/")[0] for b in tags.get("badges", "").split(",") if b)
    return {
        "author": tags.get("display-name") or match.group("nick"),
        "text": match.group("text"),
        "color": tags.get("color", ""),
        "badges": badges,
    }


class TwitchConnector(BaseConnector):
    """Lecture du chat Twitch en IRC anonyme (TLS), sans aucun identifiant.

    Twitch autorise n'importe quel pseudo `justinfan<n>` à rejoindre un salon
    public en lecture seule. C'est pour cette raison que la carte fonctionne
    dès la saisie du nom de chaîne : aucun compte, aucune application à
    déclarer. Un compte ne serait nécessaire que pour écrire ou modérer.
    """

    platform = "twitch"

    def _session(self) -> None:
        channel = normalise_channel(self._config.channel)
        if not channel:
            raise ValueError("nom de chaîne Twitch invalide")
        context = ssl.create_default_context()
        with socket.create_connection((_IRC_HOST, _IRC_TLS_PORT), timeout=15) as raw_sock:
            with context.wrap_socket(raw_sock, server_hostname=_IRC_HOST) as sock:
                sock.settimeout(1.0)   # réveil régulier pour tester `_stop`
                self._handshake(sock, channel)
                self._read_loop(sock)

    def _handshake(self, sock: Any, channel: str) -> None:
        nick = f"justinfan{random.randint(10_000, 99_999)}"
        for command in (
            "CAP REQ :twitch.tv/tags twitch.tv/commands",
            "PASS SCHMOOPIIE",          # mot de passe ignoré en anonyme
            f"NICK {nick}",
            f"JOIN #{channel}",
        ):
            sock.sendall((command + "\r\n").encode("utf-8"))

    def _read_loop(self, sock: Any) -> None:
        buffer = ""
        # Incrémental : TCP peut couper un « é » ou un emoji entre deux
        # paquets. Décoder chaque paquet seul changeait chaque moitié en « � ».
        decodeur = codecs.getincrementaldecoder("utf-8")(errors="replace")
        last_data = time.monotonic()
        joined = False
        while not self._stop.is_set():
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                if time.monotonic() - last_data > _IRC_SILENCE_LIMIT:
                    raise ConnectionError("silence prolongé du serveur IRC")
                continue
            if not chunk:
                raise ConnectionError("connexion IRC fermée par le serveur")
            last_data = time.monotonic()
            buffer += decodeur.decode(chunk)
            *lines, buffer = buffer.split("\r\n")
            for line in lines:
                if line.startswith("PING"):
                    sock.sendall(b"PONG :tmi.twitch.tv\r\n")
                    continue
                if not joined and " 366 " in line:   # END OF NAMES = salon rejoint
                    joined = True
                    self._set_status(Status.CONNECTED, self._config.channel)
                parsed = parse_privmsg(line)
                if parsed is not None:
                    if not joined:
                        joined = True
                        self._set_status(Status.CONNECTED, self._config.channel)
                    self._emit(parsed["author"], parsed["text"],
                               parsed["color"], parsed["badges"])


# ============================================================================
# HUB
# ============================================================================
class ChatHub:
    """Fusionne les connecteurs et distribue le flux aux pages overlay.

    Le filtre `enabled` est appliqué ICI, côté serveur : une plateforme
    désactivée n'atteint jamais le réseau, même si son connecteur reste
    connecté en arrière-plan. Masquer côté page laisserait fuiter les
    messages dans le DOM de la source navigateur.
    """

    def __init__(self, store: TwitchChatStore,
                 on_status_change: Optional[Callable[[str, str, str], None]] = None) -> None:
        self._store = store
        self._on_status_change = on_status_change
        self._lock = threading.Lock()
        self._subs: list["queue.Queue[str]"] = []
        self._history: list[ChatMessage] = []
        self._connectors: dict[str, BaseConnector] = {}
        self._enabled: dict[str, bool] = {name: True for name in PLATFORMS}

    # -- Connecteurs ------------------------------------------------------- #

    def _build(self, name: str, pcfg: PlatformConfig) -> BaseConnector:
        return TwitchConnector(pcfg, self.publish, self._status_changed)

    def _status_changed(self, platform: str, status: str, detail: str) -> None:
        logger.info("Chat %s : %s%s", platform, status, f" ({detail})" if detail else "")
        if self._on_status_change is None:
            return
        try:
            self._on_status_change(platform, status, detail)
        except Exception:
            logger.exception("Notification de statut refusée (ignorée).")

    def apply_config(self, cfg: TwitchChatConfig) -> None:
        """Aligne les connecteurs actifs sur la configuration enregistrée.

        Appelée au démarrage et après chaque édition dans l'interface. Un
        connecteur n'est recréé que si sa configuration a changé : basculer
        l'interrupteur d'affichage ne coupe donc jamais la session.
        """
        for name in PLATFORMS:
            pcfg = cfg.platform(name)
            self._enabled[name] = pcfg.enabled
            existing = self._connectors.get(name)
            if existing is not None and existing.config == pcfg:
                continue
            if existing is not None:
                existing.stop()
            connector = self._build(name, pcfg)
            self._connectors[name] = connector
            connector.start()

    def stop_all(self) -> None:
        for connector in self._connectors.values():
            try:
                connector.stop()
            except Exception:
                logger.exception("Arrêt du connecteur %s imparfait (ignoré).",
                                 connector.platform)

    def set_enabled(self, platform: str, enabled: bool) -> None:
        """Bascule l'affichage sans toucher à la connexion sous-jacente."""
        self._enabled[platform] = enabled

    def status(self, platform: str) -> tuple[str, str]:
        connector = self._connectors.get(platform)
        if connector is None:
            return (Status.DISCONNECTED, "")
        return (connector.status, connector.detail)

    # -- Diffusion --------------------------------------------------------- #

    def publish(self, message: ChatMessage) -> int:
        """Appelée depuis le thread du connecteur. Retourne le nombre de
        pages notifiées (0 = aucune source navigateur ouverte)."""
        if not self._enabled.get(message.platform, True):
            return 0
        data = json.dumps(message.to_dict())
        with self._lock:
            self._history.append(message)
            if len(self._history) > HISTORY_SIZE:
                del self._history[:-HISTORY_SIZE]
            subs = list(self._subs)
        sent = 0
        for sub in subs:
            try:
                sub.put_nowait(data)
                sent += 1
                continue
            except queue.Full:
                pass
            # Même arbitrage que le broker des déclencheurs : on évince le
            # plus ANCIEN. Perdre un message vieux de 200 messages est sans
            # conséquence ; perdre le plus récent fige visiblement le chat.
            try:
                sub.get_nowait()
                sub.put_nowait(data)
                sent += 1
            except (queue.Empty, queue.Full):
                logger.debug("File SSE du chat inutilisable, message ignoré.")
        return sent

    def subscribe(self) -> "queue.Queue[str]":
        sub: "queue.Queue[str]" = queue.Queue(maxsize=_SUB_QUEUE_SIZE)
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: "queue.Queue[str]") -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    def listener_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def history(self) -> list[dict[str, Any]]:
        """Messages récents des plateformes actuellement affichées."""
        with self._lock:
            messages = list(self._history)
        return [m.to_dict() for m in messages if self._enabled.get(m.platform, True)]
