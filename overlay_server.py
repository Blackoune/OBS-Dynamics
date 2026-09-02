"""
overlay_server.py — Serveur HTTP local pour les sources navigateur OBS.

Une source navigateur OBS a besoin d'une URL : c'est la raison d'être de ce
module. Il expose, par règle de déclenchement, une page qui reste connectée
en SSE et affiche le média quand l'application signale l'appui du raccourci.

Uniquement de la bibliothèque standard (`http.server`) — aucune dépendance
supplémentaire, et rien n'écoute en dehors de 127.0.0.1.

Routes :
    GET /overlay/<rule_id>   page HTML à coller dans OBS
    GET /events/<rule_id>    flux SSE (une connexion par source ouverte)
    GET /media/<rule_id>     le fichier média lui-même
    GET /health              sonde de vivacité
"""
from __future__ import annotations

import json
import logging
import mimetypes
import queue
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote, urlparse

logger = logging.getLogger("obs_dynamics.overlay")

DEFAULT_PORT = 4466            # 4455 est pris par OBS WebSocket
HOST = "127.0.0.1"

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
            except queue.Full:
                # Page bloquée ou trop lente : on saute plutôt que de bloquer
                # le thread des hotkeys.
                logger.debug("File SSE saturée pour %s, événement ignoré.", rule_id)
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
  #media{max-width:100%;max-height:100%;display:none}
  #media.on{display:block}
</style>
<div id="stage"><img id="media" alt=""></div>
<video id="video" style="display:none;max-width:100%;max-height:100%"></video>
<audio id="audio"></audio>
<script>
const RULE = __RULE_ID__;
const KIND = __MEDIA_TYPE__;
const DURATION = __DURATION__;
const MEDIA_URL = "/media/" + RULE;

const img = document.getElementById("media");
const video = document.getElementById("video");
const audio = document.getElementById("audio");
const stage = document.getElementById("stage");
let hideTimer = null;

function show() {
  if (KIND === "sound") {
    audio.src = MEDIA_URL + "?t=" + Date.now();
    audio.play().catch(e => console.warn("lecture audio refusee", e));
    return;
  }
  if (KIND === "video") {
    stage.innerHTML = "";
    stage.appendChild(video);
    video.style.display = "block";
    video.src = MEDIA_URL + "?t=" + Date.now();
    video.currentTime = 0;
    video.play().catch(e => console.warn("lecture video refusee", e));
    video.onended = () => { video.style.display = "none"; };
    return;
  }
  img.src = MEDIA_URL + "?t=" + Date.now();
  img.classList.add("on");
  if (hideTimer) clearTimeout(hideTimer);
  hideTimer = setTimeout(() => img.classList.remove("on"), DURATION);
}

function connect() {
  const es = new EventSource("/events/" + RULE);
  es.addEventListener("trigger", show);
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
                 broker: _Broker, rule_getter: Callable[[str], Optional[Any]]) -> None:
        self.broker = broker
        self.rule_getter = rule_getter
        super().__init__(address, handler_cls)


class _Handler(BaseHTTPRequestHandler):
    server_version = "OBSDynamicsOverlay/1.0"

    @property
    def broker(self) -> _Broker:
        return self.server.broker  # type: ignore[attr-defined]

    @property
    def rule_getter(self) -> Callable[[str], Optional[Any]]:
        return self.server.rule_getter  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        # Le logger par défaut écrit sur stderr à chaque requête, ce qui
        # noierait la console : on redirige en DEBUG.
        logger.debug("overlay %s - %s", self.address_string(), fmt % args)

    # -- Helpers ---------------------------------------------------------- #

    def _send(self, code: int, body: bytes, content_type: str,
              extra: Optional[dict[str, str]] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _not_found(self) -> None:
        self._send(404, b"not found", "text/plain; charset=utf-8")

    # -- Routage ---------------------------------------------------------- #

    def do_GET(self) -> None:  # noqa: N802 (nom imposé par BaseHTTPRequestHandler)
        path = urlparse(self.path).path
        parts = [unquote(p) for p in path.strip("/").split("/") if p]

        if parts == ["health"]:
            self._send(200, b"ok", "text/plain; charset=utf-8")
            return
        if len(parts) != 2:
            self._not_found()
            return

        section, rule_id = parts
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
                .replace("__DURATION__", str(int(rule.duration_ms))))
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_media(self, rule: Any) -> None:
        path = Path(rule.media_path)
        if not rule.media_path or not path.is_file():
            self._not_found()
            return
        try:
            data = path.read_bytes()
        except OSError:
            logger.exception("Lecture du média impossible : %s", path)
            self._send(500, b"media unreadable", "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self._send(200, data, ctype)

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
                 port: int = DEFAULT_PORT) -> None:
        self._rule_getter = rule_getter
        self._requested_port = port
        self._broker = _Broker()
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

    def start(self) -> bool:
        """Démarre le serveur. Si le port demandé est occupé, bascule sur un
        port libre attribué par l'OS plutôt que d'échouer."""
        if self._httpd is not None:
            return True

        for port in (self._requested_port, 0):
            try:
                httpd = _OverlayHTTPServer((HOST, port), _Handler,
                                            self._broker, self._rule_getter)
            except OSError as exc:
                logger.warning("Port %s indisponible (%s).", port or "auto", exc)
                continue
            self._httpd = httpd
            self._thread = threading.Thread(target=httpd.serve_forever, daemon=True,
                                             name="overlay-http")
            self._thread.start()
            logger.info("Serveur overlay démarré sur %s", self.base_url())
            return True

        logger.error("Impossible de démarrer le serveur overlay.")
        return False

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

    def fire(self, rule_id: str) -> int:
        """Déclenche l'affichage. Retourne le nombre de sources notifiées :
        0 signifie que la source navigateur n'est pas ouverte dans OBS."""
        return self._broker.publish(rule_id, {"id": rule_id})

    def listener_count(self, rule_id: str) -> int:
        return self._broker.listener_count(rule_id)
