"""Widget de sous-titres : hub SSE et page à coller dans OBS.

Le hub reçoit les évènements du moteur (`partial`, `final`) et les relaie à
chaque source navigateur ouverte. Il porte aussi le style et le jeton du
lien, persistés dans `subtitles_widget.json`.

Le serveur d'overlay ne connaît que ces méthodes : `token_matches`,
`subscribe`, `unsubscribe`, `initial_payload`, `page_html`. Retirer la
fonctionnalité revient à ne plus lui passer de hub.

Thread : `publish_*` est appelé depuis le thread Tk, les routes depuis les
threads du serveur HTTP ; tout l'état partagé passe sous `_lock`.
"""
from __future__ import annotations

import json
import queue
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

import subtitles_style as sstyle
from app_paths import DATA_DIR, logger

WIDGET_PATH = DATA_DIR / "subtitles_widget.json"
_QUEUE_MAX = 64
_RECENT_MAX = 8


class SubtitlesHub:

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or WIDGET_PATH
        self._lock = threading.Lock()
        self._subs: list["queue.Queue[str]"] = []
        self._recent: deque[tuple[float, int, str]] = deque(maxlen=_RECENT_MAX)
        self._partial = ""
        data = self._read()
        self._token: str = data.get("token") if isinstance(data.get("token"), str) else ""
        self._style = sstyle.from_dict(data.get("style"))
        self._enabled = data.get("enabled") is not False

    # -- fichier ------------------------------------------------------------ #

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self) -> None:
        """Écriture atomique ; appelée sous `_lock`."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps({"token": self._token, "enabled": self._enabled,
                                   "style": self._style.to_dict()}, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(self._path)

    # -- jeton -------------------------------------------------------------- #

    def ensure_token(self) -> str:
        with self._lock:
            if not self._token:
                self._token = secrets.token_urlsafe(24)
                try:
                    self._write()
                except OSError:
                    logger.warning("Jeton des sous-titres non persisté : le lien "
                                   "changera au prochain démarrage.")
            return self._token

    def regenerate_token(self) -> str:
        """Invalide le lien. Lève OSError si le nouveau ne peut être écrit :
        l'ancien reste alors en service."""
        with self._lock:
            old, self._token = self._token, secrets.token_urlsafe(24)
            try:
                self._write()
            except OSError:
                self._token = old
                raise
            return self._token

    def token_matches(self, candidate: str) -> bool:
        expected = self._token
        if not expected or not candidate:
            return False
        # En octets : sur deux `str`, compare_digest lève TypeError dès qu'un
        # caractère n'est pas ASCII (`/subevents/%C3%A9`).
        return secrets.compare_digest(expected.encode(), candidate.encode())

    # -- réglages ----------------------------------------------------------- #

    @property
    def style(self) -> sstyle.Style:
        return self._style

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_style(self, style: sstyle.Style, persist: bool = True) -> None:
        """Pousse le style aux pages ouvertes ; `persist=False` pendant qu'un
        curseur bouge, pour ne pas réécrire le fichier à chaque cran."""
        with self._lock:
            self._style = style
            if persist:
                self._save_quietly()
        self._broadcast({"type": "style", "style": style.to_dict()})

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = enabled
            self._save_quietly()
        if not enabled:
            self.clear()

    def _save_quietly(self) -> None:
        try:
            self._write()
        except OSError:
            logger.warning("Réglages du widget de sous-titres non enregistrés (%s).",
                           self._path)

    # -- texte -------------------------------------------------------------- #

    def publish_text(self, kind: str, text: str, uid: int = -1) -> None:
        """`kind` : partial, final, ou revise (texte corrigé d'une phrase
        déjà affichée, retrouvée par son `uid`)."""
        if not self._enabled or (kind != "partial" and not text.strip()):
            return
        with self._lock:
            if kind == "final":
                self._recent.append((time.monotonic(), uid, text))
                self._partial = ""
            elif kind == "revise":
                found = next((i for i, (_t, u, _x) in enumerate(self._recent) if u == uid), None)
                if found is None:
                    return            # phrase déjà sortie de l'écran
                t0, _u, _old = self._recent[found]
                self._recent[found] = (t0, uid, text)
            else:
                self._partial = text
        self._broadcast({"type": kind, "text": text, "id": uid})

    def clear(self) -> None:
        with self._lock:
            self._recent.clear()
            self._partial = ""
        self._broadcast({"type": "clear"})

    # -- abonnés ------------------------------------------------------------ #

    def subscribe(self) -> tuple["queue.Queue[str]", dict[str, Any]]:
        """Abonne une page et rend son premier message, sous le même verrou :
        une ligne publiée entre les deux serait sinon affichée deux fois."""
        sub: "queue.Queue[str]" = queue.Queue(maxsize=_QUEUE_MAX)
        with self._lock:
            self._subs.append(sub)
            return sub, self._initial_payload()

    def unsubscribe(self, sub: "queue.Queue[str]") -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    def listener_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def initial_payload(self) -> dict[str, Any]:
        with self._lock:
            return self._initial_payload()

    def _initial_payload(self) -> dict[str, Any]:
        """Premier message d'une page : le style, et les lignes encore à
        l'écran — une source rouverte en plein direct ne reste pas vide.
        Appelée sous `_lock`."""
        now = time.monotonic()
        hold = self._style.hold_s
        lines = [{"text": text, "id": uid, "age": round(now - t, 2)}
                 for t, uid, text in self._recent if now - t < hold]
        return {"type": "init", "style": self._style.to_dict(),
                "lines": lines if self._enabled else [],
                "partial": self._partial if self._enabled else ""}

    def _broadcast(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload)
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            try:
                sub.put_nowait(data)
            except queue.Full:
                # Page figée : plutôt que de bloquer le thread de l'interface
                # ou de jeter un message au hasard (un `style` ou un `clear`
                # perdu ne se rattrape pas), on vide sa file et on lui renvoie
                # l'état complet.
                with self._lock:
                    reset = json.dumps(self._initial_payload())
                while True:
                    try:
                        sub.get_nowait()
                    except queue.Empty:
                        break
                try:
                    sub.put_nowait(reset)
                except queue.Full:
                    pass

    def page_html(self, token: str) -> str:
        # `<` échappé : un jeton édité à la main ne peut pas fermer le script.
        return _PAGE.replace("__TOKEN__", json.dumps(token).replace("<", "\\u003c"))


# Page servie à OBS. Le texte n'est JAMAIS inséré en HTML : uniquement par
# `textContent`. Le style n'arrive que validé par `subtitles_style.from_dict`.
_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Subtitles</title>
<style>
html,body{margin:0;height:100%;background:transparent;overflow:hidden}
#wrap{position:fixed;inset:0;display:flex;flex-direction:column;
  justify-content:flex-end;align-items:center;padding:0 0 3vh}
#band{display:flex;justify-content:var(--justify);box-sizing:border-box}
#subs{overflow:hidden;display:flex;flex-direction:column;justify-content:flex-end;
  font-family:var(--font);font-size:var(--size);font-weight:var(--weight);
  font-style:var(--italic);text-transform:var(--case);letter-spacing:var(--spacing);
  text-align:var(--align);line-height:var(--lh);max-height:var(--maxh);
  text-shadow:var(--shadow);color:var(--text)}
.t{padding:0 var(--padx);border-radius:var(--radius);background:var(--box);
  -webkit-box-decoration-break:clone;box-decoration-break:clone}
.p{color:var(--partial)}
.hl{color:var(--hl)}
.fade-in{animation:in .18s ease-out}
.w-in{animation:in .12s ease-out}
.gone{opacity:0;transition:opacity .4s ease}
@keyframes in{from{opacity:0;transform:translateY(.15em)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.fade-in,.w-in{animation:none}.gone{transition:none}}
</style></head>
<body><div id="wrap"><div id="band"><div id="subs"></div></div></div>
<script>
const TOKEN = __TOKEN__;
const subs = document.getElementById("subs"), band = document.getElementById("band");
let S = null, finals = [], partialEl = null, prevWords = 0;

function rgba(hex, pct){
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n>>16},${(n>>8)&255},${n&255},${pct/100})`;
}
function applyStyle(s){
  S = s;
  const r = document.documentElement.style, box = s.bg_mode === "box";
  const lh = s.font_size * 1.25 + (box ? s.bg_padding * 0.8 : 0);
  r.setProperty("--font", `"${s.font_family}", "Segoe UI", sans-serif`);
  r.setProperty("--size", s.font_size + "px");
  r.setProperty("--weight", s.font_weight);
  r.setProperty("--italic", s.italic ? "italic" : "normal");
  r.setProperty("--case", s.uppercase ? "uppercase" : "none");
  r.setProperty("--spacing", s.letter_spacing + "px");
  r.setProperty("--align", s.align);
  r.setProperty("--justify", {left:"flex-start", center:"center", right:"flex-end"}[s.align]);
  r.setProperty("--lh", lh + "px");
  r.setProperty("--maxh", (lh * s.lines) + "px");
  r.setProperty("--text", s.text_color);
  r.setProperty("--partial", s.partial_color);
  r.setProperty("--hl", s.highlight_color);
  r.setProperty("--box", box ? rgba(s.bg_color, s.bg_opacity) : "transparent");
  r.setProperty("--radius", s.bg_radius + "px");
  r.setProperty("--padx", (box ? s.bg_padding : 0) + "px");
  const sh = [];
  for (let i = 0; i < 16 && s.outline_width > 0; i++) {
    const a = i * Math.PI / 8, w = s.outline_width;
    sh.push(`${(Math.cos(a)*w).toFixed(2)}px ${(Math.sin(a)*w).toFixed(2)}px 0 ${s.outline_color}`);
  }
  if (s.shadow_blur > 0 || s.shadow_offset > 0)
    sh.push(`${s.shadow_offset}px ${s.shadow_offset}px ${s.shadow_blur}px ${s.shadow_color}`);
  r.setProperty("--shadow", sh.length ? sh.join(",") : "none");
  const isBand = s.bg_mode === "band";
  band.style.width = isBand ? "100%" : "auto";
  band.style.background = isBand ? rgba(s.bg_color, s.bg_opacity) : "transparent";
  band.style.borderRadius = isBand ? s.bg_radius + "px" : "0";
  band.style.padding = isBand ? `${s.bg_padding}px` : "0";
  subs.style.width = (s.max_width) + "vw";
  subs.style.maxWidth = (s.max_width) + "vw";
  fit();
}
function lineEl(){
  const d = document.createElement("div"), t = document.createElement("span");
  t.className = "t"; d.appendChild(t);
  if (S.animation !== "none") d.classList.add("fade-in");
  return d;
}
function fillWords(span, text, animateFrom){
  span.textContent = "";
  const words = text.split(/\\s+/).filter(Boolean);
  words.forEach((w, i) => {
    if (i) span.appendChild(document.createTextNode(" "));
    const e = document.createElement("span");
    e.textContent = w;
    if (S.highlight_on && i === words.length - 1) e.className = "hl";
    if (S.animation === "word" && i >= animateFrom) e.classList.add("w-in");
    span.appendChild(e);
  });
  return words.length;
}
function showPartial(text){
  if (!text) { if (partialEl) { partialEl.remove(); partialEl = null; prevWords = 0; } return; }
  if (!partialEl) { partialEl = lineEl(); partialEl.classList.add("p"); subs.appendChild(partialEl); prevWords = 0; }
  prevWords = fillWords(partialEl.firstChild, text, prevWords);
}
function addFinal(text, id, age){
  let el = partialEl;
  if (el) { partialEl = null; prevWords = 0; el.classList.remove("p", "fade-in"); }
  else { el = lineEl(); subs.appendChild(el); }
  el.firstChild.textContent = text;
  finals.push({el, id, t: performance.now() - (age || 0) * 1000});
  while (finals.length > 8) finals.shift().el.remove();
}
// Phrase corrigée par le second passage : remplacée sur place, sans
// relancer son minuteur ni son animation.
function revise(text, id){
  const f = finals.find(x => x.id === id);
  if (f) f.el.firstChild.textContent = text;
}
// Cache les lignes ENTIÈRES qui ne tiennent plus : rogner seulement par
// overflow laissait dépasser le contour des lettres de la ligne coupée. La
// ligne la plus récente reste toujours visible, rognée si elle est trop haute.
function fit(){
  if (!S) return;
  const max = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--maxh"));
  const kids = Array.from(subs.children);
  let used = 0;
  for (let i = kids.length - 1; i >= 0; i--) {
    kids[i].style.display = "";
    const h = kids[i].offsetHeight;
    if (i < kids.length - 1 && used + h > max + 1) {
      for (let j = i; j >= 0; j--) kids[j].style.display = "none";
      break;
    }
    used += h;
  }
}
new MutationObserver(fit).observe(subs, {childList: true, subtree: true, characterData: true});
window.addEventListener("resize", fit);

function clearAll(){
  finals.forEach(f => f.el.remove()); finals = [];
  showPartial("");
}
setInterval(() => {
  if (!S) return;
  const now = performance.now(), hold = S.hold_s * 1000;
  finals = finals.filter(f => {
    if (now - f.t < hold) return true;
    if (S.animation === "none") f.el.remove();
    else { f.el.classList.add("gone"); setTimeout(() => f.el.remove(), 450); }
    return false;
  });
}, 250);

let retry = 2000;
function connect(){
  const es = new EventSource("/subevents/" + TOKEN);
  es.onopen = () => { retry = 2000; };
  es.addEventListener("subs", ev => {
    const m = JSON.parse(ev.data);
    if (m.type === "init") { applyStyle(m.style); clearAll();
      m.lines.forEach(l => addFinal(l.text, l.id, l.age)); showPartial(m.partial); }
    else if (!S) return;
    else if (m.type === "style") applyStyle(m.style);
    else if (m.type === "partial") showPartial(m.text);
    else if (m.type === "final") addFinal(m.text, m.id, 0);
    else if (m.type === "revise") revise(m.text, m.id);
    else if (m.type === "clear") clearAll();
  });
  // Lien révoqué ou application fermée : on réessaie de plus en plus rarement.
  es.onerror = () => { es.close(); setTimeout(connect, retry); retry = Math.min(retry * 2, 30000); };
}
connect();
</script></body></html>"""
