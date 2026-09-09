"""
overlay_server.py — Serveur HTTP local pour les sources navigateur OBS.

Une source navigateur OBS a besoin d'une URL : c'est la raison d'être de ce
module. Il expose, par règle de déclenchement, une page qui reste connectée
en SSE et affiche le média quand l'application signale l'appui du raccourci.

Uniquement de la bibliothèque standard (`http.server`) — aucune dépendance
supplémentaire, et rien n'écoute en dehors de 127.0.0.1.

Le même serveur porte l'overlay du chat (onglet Chat Twitch) : un
second serveur sur un second port doublerait les risques de conflit et
donnerait une deuxième URL à surveiller, alors que celui-ci démarre déjà
avec l'application et écoute sur un port stable.

Routes :
    GET /overlay/<rule_id>   page HTML à coller dans OBS
    GET /events/<rule_id>    flux SSE (une connexion par source ouverte)
    GET /media/<rule_id>     le fichier média lui-même
    GET /chat/<token>        page HTML du chat
    GET /chatevents/<token>  flux SSE du chat
    GET /health              sonde de vivacité

Toute requête est refusée (403) si son en-tête `Host` ne désigne pas la
boucle locale, ou si elle porte une `Origin` étrangère : voir
`_LOOPBACK_HOSTNAMES`. C'est ce qui empêche un site web visité par
l'utilisateur de lire ces routes par rebinding DNS.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import queue
import secrets
import threading
import time

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote, urlparse

logger = logging.getLogger("obs_dynamics.overlay")

DEFAULT_PORT = 4466            # 4455 est pris par OBS WebSocket
HOST = "127.0.0.1"

# Noms d'hôte acceptés dans `Host` et dans `Origin`. Tout le reste est refusé.
#
# Écouter sur 127.0.0.1 NE SUFFIT PAS à rester privé : un site malveillant
# peut faire pointer son propre domaine sur 127.0.0.1 (rebinding DNS). Le
# navigateur de la victime considère alors `http://evil.example/...` comme
# same-origin avec la page de l'attaquant, et le laisse LIRE nos réponses.
# Le seul indice qui distingue cette requête d'une vraie est l'en-tête
# `Host` : le navigateur y met le nom demandé, jamais 127.0.0.1. Le comparer
# ferme la porte, pour TOUTES les routes à la fois.
#
# Le port n'entre pas dans le contrôle : le serveur bascule sur un port libre
# quand 4466 est occupé, et le rebinding se joue sur le nom, pas sur le port.
_LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})

# Politique de sécurité du contenu des pages servies (overlay et chat).
#
# Ces pages n'ont besoin de RIEN d'extérieur : tout le CSS et tout le JS sont
# écrits en ligne dans le gabarit, et les seuls médias viennent de nos propres
# routes. L'écrire noir sur blanc coupe d'avance ce qu'une injection future
# chercherait à faire : charger un script distant, ou renvoyer le contenu du
# chat vers un tiers. `'unsafe-inline'` est ici obligatoire — les scripts sont
# justement en ligne — donc la protection porte sur l'origine des ressources,
# pas sur l'exécution.
_CSP = ("default-src 'none'; "
        "script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; "
        "img-src 'self'; "
        "media-src 'self'; "
        "connect-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'")


def _bare_hostname(authority: str) -> str:
    """Nom d'hôte nu d'une autorité `hôte[:port]` : sans port ni crochets.

    Gère la forme IPv6 littérale (`[::1]`, `[::1]:4466`), où découper
    bêtement sur le premier `:` ne donnerait que `[`.
    """
    authority = authority.strip()
    if authority.startswith("["):
        fin = authority.find("]")
        return authority[1:fin].lower() if fin > 0 else ""
    return authority.split(":", 1)[0].lower()

# Laps au-delà duquel une source navigateur inactive reçoit un commentaire
# SSE de maintien : sans trafic, OBS/Chromium finit par couper la connexion.
_KEEPALIVE_SECONDS = 15.0


class _Broker:
    """Distribue les événements de déclenchement aux pages connectées."""

    def __init__(self) -> None:
        self._subs: dict[str, list["queue.Queue[str]"]] = {}
        self._lock = threading.Lock()

    def subscribe(self, rule_id: str) -> "queue.Queue[str]":
        q: "queue.Queue[str]" = queue.Queue(maxsize=16)
        with self._lock:
            self._subs.setdefault(rule_id, []).append(q)
        return q

    def unsubscribe(self, rule_id: str, q: "queue.Queue[str]") -> None:
        with self._lock:
            subs = self._subs.get(rule_id)
            if not subs:
                return
            if q in subs:
                subs.remove(q)
            if not subs:
                self._subs.pop(rule_id, None)

    def publish(self, rule_id: str, payload: dict[str, Any]) -> int:
        """Retourne le nombre de pages notifiées (0 = source non ouverte)."""
        data = json.dumps(payload)
        with self._lock:
            subs = list(self._subs.get(rule_id, ()))
        sent = 0
        for q in subs:
            try:
                q.put_nowait(data)
                sent += 1
                continue
            except queue.Full:
                pass
            # File saturée : on évince le PLUS ANCIEN pour faire place au plus
            # récent. Jeter le nouveau serait dangereux en mode maintien —
            # un "hide" perdu laisserait l'overlay collé à l'écran. L'état le
            # plus récent est toujours celui qui compte.
            try:
                q.get_nowait()
                q.put_nowait(data)
                sent += 1
                logger.debug("File SSE saturée pour %s : plus ancien événement évincé.", rule_id)
            except (queue.Empty, queue.Full):
                logger.debug("Impossible de publier vers %s.", rule_id)
        return sent

    def listener_count(self, rule_id: str) -> int:
        with self._lock:
            return len(self._subs.get(rule_id, ()))


# Substitution par jetons __XXX__ volontairement, et pas via % ni .format() :
# le CSS contient "100%" (que % interpréterait comme un format) et le JS est
# plein d'accolades (que .format() consommerait). Les jetons ci-dessous
# n'existent dans aucune syntaxe web.
_OVERLAY_HTML = """<!doctype html>
<meta charset="utf-8">
<title>OBS Dynamics — overlay</title>
<style>
  /* Fond transparent : OBS compose la page par-dessus la scène. */
  html,body{margin:0;height:100%;background:transparent;overflow:hidden}
  #stage{width:100%;height:100%;display:flex;align-items:center;justify-content:center}
  /* visibility plutot que display:none -> l'element garde sa taille et sa
     texture GPU, donc l'affichage est un simple changement de compositing,
     sans relayout ni redecodage. C'est ce qui rend le masquage instantane. */
  #img,#video{max-width:100%;max-height:100%;position:absolute;visibility:hidden}
  .on{visibility:visible !important}
</style>
<div id="stage">
  <img id="img" alt="">
  <video id="video" muted playsinline></video>
</div>
<audio id="audio"></audio>
<script>
const RULE = __RULE_ID__;
const MEDIA_URL = "/media/" + RULE;

// Etat COURANT de la regle. Ces valeurs sont initialisees au chargement puis
// reactualisees a chaque evenement : OBS garde la page ouverte des heures, et
// figer la configuration au chargement rendait tout changement de reglage
// invisible sur une source deja ouverte.
let kind = __MEDIA_TYPE__;
let duration = __DURATION__;      // 0 = mode maintien (masque au relachement)
let mediaStamp = __MEDIA_STAMP__;

const img = document.getElementById("img");
const video = document.getElementById("video");
const audio = document.getElementById("audio");
let hideTimer = null;

// --- Prechargement -------------------------------------------------------
// Le media est telecharge et decode UNE SEULE FOIS, puis reutilise. Afficher
// ne coute alors qu'un toggle CSS. On ne recharge que si l'empreinte du
// fichier a change (media remplace dans l'application).
function loadMedia() {
  const url = MEDIA_URL + (mediaStamp ? "?v=" + encodeURIComponent(mediaStamp) : "");
  if (kind === "image") {
    img.src = url;
  } else if (kind === "video") {
    video.src = url; video.preload = "auto"; video.load();
  } else {
    audio.src = url; audio.preload = "auto"; audio.load();
  }
}
loadMedia();

function show() {
  if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
  if (kind === "sound") {
    audio.currentTime = 0;
    audio.play().catch(e => console.warn("lecture audio refusee", e));
    return;
  }
  if (kind === "video") {
    video.classList.add("on");
    video.currentTime = 0;
    video.play().catch(e => console.warn("lecture video refusee", e));
    if (duration > 0) hideTimer = setTimeout(hide, duration);
    return;
  }
  img.classList.add("on");
  if (duration > 0) hideTimer = setTimeout(hide, duration);
}

function hide() {
  if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
  img.classList.remove("on");
  video.classList.remove("on");
  try { video.pause(); } catch (e) {}
  try { audio.pause(); } catch (e) {}
}

function onEvent(ev) {
  let d = {};
  try { d = JSON.parse(ev.data) || {}; } catch (e) {}

  // Reprise de la configuration courante, envoyee avec chaque evenement.
  if (typeof d.duration === "number") duration = d.duration;
  let needsReload = false;
  if (d.kind && d.kind !== kind) { kind = d.kind; needsReload = true; }
  if (typeof d.media === "string" && d.media !== mediaStamp) {
    mediaStamp = d.media; needsReload = true;
  }
  if (needsReload) { hide(); loadMedia(); }

  // Le relachement ne masque qu'en mode maintien : sinon la duree fait foi.
  if (d.action === "hide") { if (duration === 0) hide(); return; }
  show();
}

function connect() {
  const es = new EventSource("/events/" + RULE);
  es.addEventListener("trigger", onEvent);
  es.onerror = () => {
    // OBS garde la page ouverte en continu : on se reconnecte tout seul
    // plutot que de rester muet apres un redemarrage de l'application.
    es.close();
    setTimeout(connect, 2000);
  };
}
connect();
</script>
"""


# Page du chat. Même technique de substitution par jetons __XXX__ que
# ci-dessus, et pour la même raison (CSS avec des "%" et JS plein d'accolades).
_CHAT_HTML = """<!doctype html>
<meta charset="utf-8">
<title>OBS Dynamics — chat</title>
<style>
  /* Fond transparent : OBS compose la page par-dessus la scene. */
  html,body{margin:0;height:100%;background:transparent;overflow:hidden;
    font-family:"Segoe UI",Inter,system-ui,sans-serif}
  #wrap{position:absolute;inset:0;display:flex;flex-direction:column;
    justify-content:flex-end;padding:12px;gap:8px;box-sizing:border-box}
  .msg{
    /* Glassmorphism : fond translucide + flou de ce qui est derriere. Sur une
       source navigateur OBS, "derriere" est la scene elle-meme, donc le flou
       fait vraiment son effet par-dessus le jeu. */
    background:rgba(26,21,48,.55);
    -webkit-backdrop-filter:blur(14px) saturate(140%);
    backdrop-filter:blur(14px) saturate(140%);
    border:1px solid rgba(168,85,247,.28);
    border-left:3px solid var(--accent,#A855F7);
    border-radius:12px;padding:8px 12px;color:#F3F0FA;font-size:17px;
    line-height:1.35;box-shadow:0 6px 20px rgba(0,0,0,.35);
    animation:pop .18s ease-out;word-wrap:break-word;overflow-wrap:anywhere}
  @keyframes pop{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
  .who{font-weight:700;margin-right:6px}
  .tag{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.4px;
    text-transform:uppercase;padding:1px 7px;border-radius:999px;margin-right:7px;
    color:#0F0C1B;vertical-align:2px}
  /* Banniere d'attente : c'est ce que voit l'utilisateur quand l'application
     est fermee ou redemarre, plutot qu'une page blanche muette. */
  #wait{align-self:center;background:rgba(26,21,48,.6);
    -webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);
    border:1px solid rgba(168,85,247,.3);border-radius:999px;
    padding:8px 18px;color:#9B93B5;font-size:14px}
  [hidden]{display:none !important}
</style>
<div id="wrap"><div id="wait">__WAIT_TEXT__</div></div>
<script>
const TOKEN = __CHAT_TOKEN__;
const MAX_VISIBLE = __MAX_VISIBLE__;
const wrap = document.getElementById("wrap");
const wait = document.getElementById("wait");

function render(m) {
  const el = document.createElement("div");
  el.className = "msg";
  el.style.setProperty("--accent", m.color || "#A855F7");
  const tag = document.createElement("span");
  tag.className = "tag";
  tag.style.background = m.color || "#A855F7";
  tag.textContent = m.platform;
  const who = document.createElement("span");
  who.className = "who";
  who.style.color = m.color || "#A855F7";
  who.textContent = m.author;
  const txt = document.createElement("span");
  // Affectation par textContent uniquement : un message de chat est du texte
  // hostile, l'interpreter comme du balisage serait une injection directe.
  txt.textContent = m.text;
  el.append(tag, who, txt);
  wrap.appendChild(el);
  while (wrap.querySelectorAll(".msg").length > MAX_VISIBLE) {
    wrap.querySelector(".msg").remove();
  }
}

function connect() {
  const es = new EventSource("/chatevents/" + TOKEN);
  es.addEventListener("chat", (ev) => {
    wait.hidden = true;
    let m = null;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m) render(m);
  });
  es.onopen = () => { wait.hidden = true; };
  es.onerror = () => {
    // L'application peut redemarrer sous la source ouverte : on repasse en
    // attente et on retente, au lieu de rester fige sur un chat mort.
    es.close();
    wait.hidden = false;
    setTimeout(connect, 2000);
  };
}
connect();
</script>
"""


# Libellés de repli, utilisés quand aucun résolveur i18n n'est branché (tests,
# ou serveur monté seul). Ils ne remplacent pas le catalogue : ils évitent
# qu'une page parte avec des clés brutes à la place du texte.
_DEFAULT_TEXTS = {
    "TWITCH_CHAT_WAIT_TEXT": "En attente de connexion…",
}


def _escape_html(text: str) -> str:
    """Neutralise le texte injecté dans le corps de la page.

    Le libellé d'attente vient du catalogue i18n, donc du dépôt — mais une
    traduction contenant `<` casserait silencieusement la mise en page, et
    rien ne garantit que ce sera toujours la seule source.
    """
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


class _ChatBinding:
    """Ce que le serveur sait du chat, ou rien du tout.

    Regrouper les trois dépendances (hub, jeton, libellé d'attente) dans un
    seul objet évite de trimballer trois paramètres facultatifs à travers le
    serveur et le handler, et rend l'absence du chat représentable :
    `_ChatBinding()` sans hub désactive proprement les deux routes.
    """

    #: Messages affichés simultanément avant éviction du plus ancien.
    MAX_VISIBLE = 25

    def __init__(self, hub: Optional[Any] = None,
                 token_getter: Optional[Callable[[], str]] = None) -> None:
        self.hub = hub
        self._token_getter = token_getter

    @property
    def is_enabled(self) -> bool:
        return self.hub is not None and self._token_getter is not None

    def token(self) -> str:
        if self._token_getter is None:
            return ""
        return self._token_getter() or ""

    def token_matches(self, candidate: str) -> bool:
        """Comparaison à temps constant : le jeton EST le seul contrôle
        d'accès de la page, un test naïf donnerait un oracle de timing."""
        if self._token_getter is None:
            return False
        expected = self._token_getter() or ""
        if not expected or not candidate:
            return False
        return secrets.compare_digest(expected, candidate)


class _OverlayHTTPServer(ThreadingHTTPServer):
    """Porte le broker et l'accès aux règles.

    socketserver instancie le handler à CHAQUE requête ; l'état partagé doit
    donc vivre sur le serveur, pas sur le handler. (Tenter de le poser sur un
    functools.partial ne marche pas : les attributs restent sur l'objet
    partial et n'atteignent jamais les instances.)
    """

    daemon_threads = True

    # HTTPServer active SO_REUSEADDR par défaut. Sous Windows, cela ne sert
    # pas à contourner TIME_WAIT (inutile ici) mais autorise un second
    # processus à se lier à un port DÉJÀ écouté : deux instances de
    # l'application se partageraient alors les requêtes de façon
    # imprévisible, au lieu que la seconde bascule sur un port libre.
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], handler_cls: type,
                 broker: _Broker, rule_getter: Callable[[str], Optional[Any]],
                 chat: "_ChatBinding",
                 text_getter: Optional[Callable[[str], str]]) -> None:
        self.broker = broker
        self.rule_getter = rule_getter
        self.chat = chat
        self.text_getter = text_getter
        super().__init__(address, handler_cls)


class _Handler(BaseHTTPRequestHandler):
    server_version = "OBSDynamicsOverlay/1.0"

    @property
    def broker(self) -> _Broker:
        return self.server.broker  # type: ignore[attr-defined]

    @property
    def rule_getter(self) -> Callable[[str], Optional[Any]]:
        return self.server.rule_getter  # type: ignore[attr-defined]

    @property
    def chat(self) -> "_ChatBinding":
        return self.server.chat  # type: ignore[attr-defined]

    def text(self, key: str) -> str:
        """Libellé traduit, avec repli sur le français si rien n'est branché."""
        getter = self.server.text_getter  # type: ignore[attr-defined]
        if getter is not None:
            try:
                return getter(key)
            except Exception:
                logger.debug("Résolution i18n de %s impossible.", key, exc_info=True)
        return _DEFAULT_TEXTS.get(key, key)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Le logger par défaut écrit sur stderr à chaque requête, ce qui
        # noierait la console : on redirige en DEBUG.
        logger.debug("overlay %s - %s", self.address_string(), fmt % args)

    # -- Helpers ---------------------------------------------------------- #

    def _send_headers(self, code: int, content_type: str, length: int,
                      extra: Optional[dict[str, str]] = None) -> None:
        """En-têtes communs à toutes les réponses, corps en mémoire ou non."""
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        # /media renvoie un fichier choisi par l'utilisateur, avec un type
        # deviné par mimetypes. Sans nosniff, un fichier mal typé pourrait
        # être interprété comme du HTML par Chromium — donc exécuté dans
        # l'origine de l'overlay, aux côtés du chat et des déclencheurs.
        self.send_header("X-Content-Type-Options", "nosniff")
        # Uniquement sur les pages : un CSP sur /media n'aurait aucun sens, le
        # fichier n'est pas un document et ne charge rien.
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy", _CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _send(self, code: int, body: bytes, content_type: str,
              extra: Optional[dict[str, str]] = None) -> None:
        self._send_headers(code, content_type, len(body), extra)
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _not_found(self) -> None:
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def _forbidden(self) -> None:
        self._send(403, b"forbidden", "text/plain; charset=utf-8")

    # -- Contrôle d'origine ------------------------------------------------ #

    def _host_is_loopback(self) -> bool:
        """L'en-tête `Host` désigne-t-il bien la boucle locale ?

        Un `Host` absent est refusé : HTTP/1.1 l'impose, et ni OBS ni un
        navigateur ne l'omettent. Ce qui l'omet est un client brut, donc pas
        le trafic qu'on sert.
        """
        host = self.headers.get("Host", "")
        if not host:
            return False
        return _bare_hostname(host) in _LOOPBACK_HOSTNAMES

    def _origin_is_loopback(self) -> bool:
        """`Origin` étranger = requête émise par une page tierce.

        Absent, c'est le cas normal : une navigation directe, un `<img>` ou
        une source navigateur OBS n'en envoient pas. Présent, il ne peut
        venir que d'un fetch/XHR/EventSource — et les nôtres partent
        toujours d'une page servie par ce serveur.
        """
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return (urlparse(origin).hostname or "").lower() in _LOOPBACK_HOSTNAMES

    def _request_is_local(self) -> bool:
        if not self._host_is_loopback():
            logger.warning("Requête overlay refusée : en-tête Host inattendu (%r). "
                           "Colle l'URL telle qu'affichée dans l'application.",
                           self.headers.get("Host", ""))
            return False
        if not self._origin_is_loopback():
            logger.warning("Requête overlay refusée : Origin étrangère (%r).",
                           self.headers.get("Origin", ""))
            return False
        return True

    # -- Routage ---------------------------------------------------------- #

    def do_GET(self) -> None:  # noqa: N802 (nom imposé par BaseHTTPRequestHandler)
        # Avant tout routage, y compris /health : une sonde qui répond à
        # n'importe quel Host confirme à un site tiers que l'application
        # tourne sur la machine.
        if not self._request_is_local():
            self._forbidden()
            return

        parsed = urlparse(self.path)
        parts = [unquote(p) for p in parsed.path.strip("/").split("/") if p]

        if parts == ["health"]:
            self._send(200, b"ok", "text/plain; charset=utf-8")
            return

        if len(parts) != 2:
            self._not_found()
            return

        section, identifier = parts

        # Les routes du chat sont résolues AVANT rule_getter : leur
        # second segment est un jeton d'accès, pas un identifiant de règle.
        if section in ("chat", "chatevents"):
            if not self.chat.token_matches(identifier):
                self._not_found()
                return
            if section == "chat":
                self._serve_chat_page()
            else:
                self._serve_chat_events()
            return

        rule_id = identifier
        rule = self.rule_getter(rule_id)
        if rule is None:
            self._not_found()
            return

        if section == "overlay":
            self._serve_overlay(rule)
        elif section == "events":
            self._serve_events(rule_id)
        elif section == "media":
            self._serve_media(rule)
        else:
            self._not_found()

    def _serve_overlay(self, rule: Any) -> None:
        # json.dumps produit des littéraux JS sûrs (guillemets et échappements
        # corrects), ce qui évite toute injection via un id ou un type.
        html = (_OVERLAY_HTML
                .replace("__RULE_ID__", json.dumps(rule.id))
                .replace("__MEDIA_TYPE__", json.dumps(rule.media_type))
                .replace("__DURATION__", str(int(rule.duration_ms)))
                .replace("__MEDIA_STAMP__", json.dumps(OverlayServer.media_stamp(rule))))
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    #: Taille des morceaux envoyés par _serve_media. 64 Kio : assez grand pour
    #: que le coût par appel système reste marginal, assez petit pour que la
    #: mémoire occupée ne dépende pas de la taille du fichier.
    MEDIA_CHUNK = 64 * 1024

    def _serve_media(self, rule: Any) -> None:
        """Sert le fichier média **par morceaux**.

        Un `read_bytes()` chargeait tout le fichier en mémoire avant le
        premier octet envoyé : une vidéo de 4 Go demandait 4 Go de RAM, et
        rien n'empêche un utilisateur de choisir un gros fichier. La taille
        vient de `stat()`, et la boucle n'envoie jamais plus que cette
        taille — un fichier qui grossit pendant le transfert produirait
        sinon plus d'octets que ne l'annonce Content-Length.
        """
        path = Path(rule.media_path)
        if not rule.media_path or not path.is_file():
            self._not_found()
            return
        try:
            taille = path.stat().st_size
            fichier = path.open("rb")
        except OSError:
            logger.exception("Lecture du média impossible : %s", path)
            self._send(500, b"media unreadable", "text/plain; charset=utf-8")
            return

        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        with fichier:
            self._send_headers(200, ctype, taille)
            restant = taille
            try:
                while restant > 0:
                    morceau = fichier.read(min(self.MEDIA_CHUNK, restant))
                    if not morceau:
                        break        # fichier tronqué en cours de route
                    self.wfile.write(morceau)
                    restant -= len(morceau)
            except (BrokenPipeError, ConnectionResetError):
                pass                 # OBS a fermé la source pendant le transfert

    # -- Chat --------------------------------------------------------------- #

    def _serve_chat_page(self) -> None:
        # json.dumps produit des littéraux JS sûrs : ni le jeton ni le libellé
        # traduit ne peuvent casser le script ou y injecter du code.
        html = (_CHAT_HTML
                .replace("__CHAT_TOKEN__", json.dumps(self.chat.token()))
                .replace("__MAX_VISIBLE__", str(int(_ChatBinding.MAX_VISIBLE)))
                .replace("__WAIT_TEXT__", _escape_html(self.text("TWITCH_CHAT_WAIT_TEXT"))))
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_chat_events(self) -> None:
        hub = self.chat.hub
        if hub is None:
            self._not_found()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        sub = hub.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            # Réamorçage : une source navigateur rouverte (ou l'application
            # redémarrée sous elle) afficherait sinon un chat vide jusqu'au
            # message suivant, ce qui ressemble à une panne.
            for message in hub.history():
                self.wfile.write(b"event: chat\ndata: "
                                 + json.dumps(message).encode("utf-8") + b"\n\n")
            self.wfile.flush()
            while True:
                try:
                    data = sub.get(timeout=_KEEPALIVE_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(b"event: chat\ndata: " + data.encode("utf-8") + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # la source OBS a été fermée : sortie normale
        finally:
            hub.unsubscribe(sub)

    def _serve_events(self, rule_id: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q = self.broker.subscribe(rule_id)
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    data = q.get(timeout=_KEEPALIVE_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")   # évite la coupure
                    self.wfile.flush()
                    continue
                self.wfile.write(b"event: trigger\ndata: " + data.encode("utf-8") + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # la source OBS a été fermée : sortie normale
        finally:
            self.broker.unsubscribe(rule_id, q)


class OverlayServer:
    """Cycle de vie du serveur HTTP local.

    `rule_getter(rule_id)` doit retourner la règle correspondante ou None —
    l'objet est relu à chaque requête, donc éditer une règle dans l'interface
    est pris en compte sans redémarrer le serveur.
    """

    def __init__(self, rule_getter: Callable[[str], Optional[Any]],
                 port: int = DEFAULT_PORT,
                 chat_hub: Optional[Any] = None,
                 chat_token_getter: Optional[Callable[[], str]] = None,
                 text_getter: Optional[Callable[[str], str]] = None) -> None:
        self._rule_getter = rule_getter
        self._requested_port = port
        self._broker = _Broker()
        self._chat = _ChatBinding(chat_hub, chat_token_getter)
        self._text_getter = text_getter
        self._httpd: Optional[_OverlayHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> int:
        if self._httpd is None:
            return self._requested_port
        return int(self._httpd.server_address[1])

    def base_url(self) -> str:
        return f"http://{HOST}:{self.port}"

    def overlay_url(self, rule_id: str) -> str:
        return f"{self.base_url()}/overlay/{rule_id}"

    def chat_url(self) -> str:
        """URL de l'overlay du chat, vide tant qu'aucun jeton n'existe."""
        token = self._chat.token()
        return f"{self.base_url()}/chat/{token}" if token else ""

    def attach_chat(self, hub: Any, token_getter: Callable[[], str]) -> None:
        """Branche le chat après coup.

        Le serveur démarre très tôt (les sources OBS doivent le trouver dès
        l'ouverture de l'application) ; le hub, lui, dépend de la
        configuration lue plus tard. Le rebrancher ici évite de retarder le
        démarrage du serveur — ou de le redémarrer, ce qui coupe les sources
        déjà connectées.
        """
        self._chat = _ChatBinding(hub, token_getter)
        if self._httpd is not None:
            self._httpd.chat = self._chat

    @property
    def requested_port(self) -> int:
        return self._requested_port

    @property
    def using_fallback_port(self) -> bool:
        """Vrai si le port demandé était pris et qu'on écoute ailleurs.

        Important à signaler : les URL déjà collées comme sources navigateur
        dans OBS pointent vers le port demandé et ne fonctionneront plus.
        """
        return self.is_running and self.port != self._requested_port

    def start(self, retries: int = 5, retry_delay: float = 0.3) -> bool:
        """Démarre le serveur.

        Le port demandé est réessayé quelques fois avant de basculer sur un
        port libre : au redémarrage de l'application, l'instance précédente
        met un court instant à relâcher la socket, et céder trop vite
        changerait l'URL — donc casserait toutes les sources navigateur déjà
        configurées dans OBS.
        """
        if self._httpd is not None:
            return True

        for attempt in range(1, retries + 1):
            try:
                self._httpd = _OverlayHTTPServer((HOST, self._requested_port), _Handler,
                                                  self._broker, self._rule_getter, self._chat,
                                                  self._text_getter)
                break
            except OSError as exc:
                if attempt == retries:
                    logger.warning("Port %d toujours occupé après %d tentatives (%s).",
                                    self._requested_port, retries, exc)
                else:
                    time.sleep(retry_delay)

        if self._httpd is None:
            try:
                self._httpd = _OverlayHTTPServer((HOST, 0), _Handler,
                                                  self._broker, self._rule_getter, self._chat,
                                                  self._text_getter)
            except OSError:
                logger.exception("Impossible de démarrer le serveur overlay.")
                return False
            logger.warning(
                "Serveur overlay replié sur le port %d : les URL déjà "
                "configurées dans OBS sur le port %d ne répondront plus.",
                self.port, self._requested_port)

        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True,
                                         name="overlay-http")
        self._thread.start()
        logger.info("Serveur overlay démarré sur %s", self.base_url())
        return True

    def stop(self) -> None:
        if self._httpd is None:
            return
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:
            logger.debug("Arrêt du serveur overlay imparfait (ignoré).", exc_info=True)
        self._httpd = None
        self._thread = None
        logger.info("Serveur overlay arrêté.")

    @staticmethod
    def media_stamp(rule: Any) -> str:
        """Empreinte du fichier média (mtime + taille).

        Permet à la page de détecter qu'on a changé le fichier et de le
        recharger — sans cette empreinte, elle continuerait d'afficher le
        média préchargé au premier chargement.
        """
        path = getattr(rule, "media_path", "") or ""
        try:
            st = os.stat(path)
            return f"{int(st.st_mtime)}-{st.st_size}"
        except OSError:
            return ""

    def fire(self, rule_id: str, action: str = "show") -> int:
        """Déclenche l'affichage (`show`) ou le masquage (`hide`).

        L'événement transporte la configuration COURANTE de la règle (durée,
        type, empreinte du média). C'est indispensable : OBS charge la page
        une seule fois et la garde ouverte des heures. Tant que ces valeurs
        étaient figées dans le HTML au chargement, modifier la durée dans
        l'application n'avait aucun effet sur une source déjà ouverte — elle
        restait bloquée sur l'ancien réglage.

        Retourne le nombre de sources notifiées : 0 signifie que la source
        navigateur n'est pas ouverte dans OBS.
        """
        payload: dict[str, Any] = {"id": rule_id, "action": action}
        rule = self._rule_getter(rule_id)
        if rule is not None:
            payload["duration"] = int(getattr(rule, "duration_ms", 0) or 0)
            payload["kind"] = getattr(rule, "media_type", "image")
            payload["media"] = self.media_stamp(rule)
        return self._broker.publish(rule_id, payload)

    def listener_count(self, rule_id: str) -> int:
        return self._broker.listener_count(rule_id)
