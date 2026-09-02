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
import os
import queue
import threading
import time

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
                .replace("__DURATION__", str(int(rule.duration_ms)))
                .replace("__MEDIA_STAMP__", json.dumps(OverlayServer.media_stamp(rule))))
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
                                                  self._broker, self._rule_getter)
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
                                                  self._broker, self._rule_getter)
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
