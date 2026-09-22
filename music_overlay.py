"""
music_overlay.py — État partagé et page d'overlay du widget musique.

Une source par lecteur détecté : chaque carte de l'onglet Widget Musique
affiche l'URL de SON overlay, à coller dans une source navigateur OBS. Deux
lecteurs ouverts donnent deux liens indépendants, et l'utilisateur choisit
lesquels mettre à l'écran plutôt que de subir un arbitrage automatique.

La page montre la pochette en vinyle à gauche, le titre, puis l'artiste suivi
de l'application d'où vient la lecture. Une miniature qui n'est pas carrée —
une vidéo — s'affiche en rectangle, sans rotation : un vinyle rectangulaire
qui tourne n'aurait aucun sens.

Le hub est la seule pièce que `overlay_server` connaît de la musique, et il
n'importe rien de `overlay_server` en retour. Avec `music_smtc.py` et
`ui_music.py`, ces trois fichiers forment la fonctionnalité entière.
"""
from __future__ import annotations

import base64
import hashlib
import json
import queue
import secrets
import threading

from pathlib import Path
from typing import Any, Optional

from app_paths import DATA_DIR, logger
from music_audio import AppLevels
from music_catalog import identify, is_valid_key, logo_path
from music_smtc import Session
from music_style import Style, StyleStore, css_variables, overlay_size

#: Jeton d'accès aux routes musique, persisté à côté des autres réglages.
MUSIC_WIDGET_PATH = DATA_DIR / "music_widget.json"

#: Profondeur de la file d'un abonné. Un overlay qui ne lit pas assez vite
#: perd les états intermédiaires, jamais le dernier : voir `_publish_one`.
_QUEUE_MAX = 8


def _stamp(data: Optional[bytes]) -> str:
    """Empreinte courte d'une pochette.

    Elle fait partie de l'URL de l'image : le navigateur garde la précédente
    en cache tant que le morceau ne change pas, et va chercher la nouvelle dès
    qu'il change. Sans elle, il faudrait soit recharger l'image à chaque
    événement, soit risquer d'afficher la pochette du morceau précédent.
    """
    if not data:
        return ""
    return hashlib.blake2s(data, digest_size=8).hexdigest()


def _escape_html(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def cover_data_uri(data: bytes) -> str:
    """Pochette en URI de données.

    La page servie passe par la route `/musiccover` — la politique de sécurité
    du contenu n'autorise que `img-src 'self'`. Cette fonction sert aux tests
    et à l'aperçu hors serveur, pas au gabarit.
    """
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


class MusicHub:
    """Dernier état connu par source, et abonnés SSE correspondants.

    Alimenté par la sonde SMTC via `publish()`, lu par les routes de
    `overlay_server`. Toutes les méthodes sont appelables depuis n'importe quel
    thread : le serveur HTTP en sert une par requête, et la sonde publie depuis
    le thread de l'interface.
    """

    def __init__(self, path: Optional[Path] = None,
                 backgrounds: Optional[Path] = None,
                 with_audio: bool = False) -> None:
        self._path = path or MUSIC_WIDGET_PATH
        self.styles = StyleStore(self._path, backgrounds=backgrounds)
        self._lock = threading.Lock()
        # Le relèvement audio est OPTIONNEL et désactivé par défaut : toucher
        # les sessions audio comme effet de bord d'un abonnement le ferait
        # faire à n'importe quel appelant, tests compris. L'application le
        # demande explicitement.
        #
        # UN relèvement PAR SOURCE, et seulement tant qu'un overlay l'écoute :
        # chacun ne suit que le son de SON application.
        self._with_audio = with_audio
        self._audio: dict[str, AppLevels] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._covers: dict[str, bytes] = {}
        self._subs: dict[str, list["queue.Queue[str]"]] = {}
        self._token: str = ""

    # -- jeton -------------------------------------------------------------- #

    def ensure_token(self) -> str:
        """Retourne le jeton, en le créant au tout premier appel.

        C'est lui qui rend le lien permanent : un jeton régénéré à chaque
        démarrage casserait la source navigateur déjà collée dans OBS.
        """
        if self._token:
            return self._token
        # Par le magasin de styles, et non en réécrivant le fichier ici : il
        # porte aussi les styles, sous SON verrou. Écrire `{"overlay_token":
        # …}` à la place du document effaçait tous les réglages dès que le
        # fichier était illisible, puis changeait le lien au démarrage suivant.
        neuf = secrets.token_urlsafe(24)
        try:
            token = self.styles.ensure_token(lambda: neuf)
            if token == neuf:
                logger.info("Jeton overlay du widget musique créé et persisté.")
        except OSError:
            token = neuf
            logger.warning("Jeton overlay musique non persisté (%s) : le lien "
                           "changera au prochain démarrage.", self._path)
        self._token = token
        return token

    def regenerate_token(self) -> str:
        """Invalide le lien de TOUTES les sources musique.

        À utiliser si l'URL a été vue : dans les propriétés d'une source OBS
        montrée à l'écran, par exemple. Chaque source devra être recollée.
        Les flux déjà ouverts avec l'ancien jeton se ferment d'eux-mêmes au
        réveil suivant — le serveur relit le jeton à chaque tranche.

        Lève `OSError` si le nouveau jeton ne peut pas être écrit : l'ancien
        reste alors en service, rien n'est révoqué à moitié.
        """
        neuf = secrets.token_urlsafe(24)
        self.styles.replace_token(neuf)
        self._token = neuf
        logger.warning("Jeton overlay musique régénéré : les anciennes URL "
                       "ne répondent plus.")
        return neuf

    #: Une `?source=` d'URL a-t-elle la forme d'une clé de lecteur ? Exposée
    #: par le hub pour que le serveur HTTP n'importe rien du widget musique :
    #: l'onglet doit rester supprimable d'un bloc.
    accepts_source = staticmethod(is_valid_key)

    def token_matches(self, candidate: str) -> bool:
        """Comparaison à temps constant : le jeton est le seul contrôle d'accès
        de ces routes, un test naïf donnerait un oracle de timing."""
        expected = self._token
        if not expected or not candidate:
            return False
        return secrets.compare_digest(expected, candidate)

    # -- publication -------------------------------------------------------- #

    def publish(self, sessions: list[Session]) -> None:
        """Remplace l'état connu par celui que la sonde vient de lire.

        Les sessions sont rangées par CLÉ DE CATALOGUE, pas par
        AppUserModelId : c'est cette clé qui figure dans l'URL de l'overlay, et
        elle doit rester la même d'une exécution à l'autre pour que la source
        navigateur déjà configurée dans OBS continue de répondre.

        Une source disparue reçoit un dernier événement `gone` : sans lui,
        l'overlay resterait figé sur un morceau qui ne joue plus — pire qu'un
        overlay vide, puisque rien à l'écran ne le signale. Le lien, lui, reste
        valable : c'est le contenu qui s'efface, pas la page.
        """
        retenues: dict[str, Session] = {}
        for session in sessions:
            app = identify(session.app_id)
            # Deux fenêtres du même lecteur partagent une clé. Celle qui joue
            # l'emporte : afficher la session en pause serait faux à l'écran.
            precedente = retenues.get(app.key)
            if precedente is None or (session.is_playing
                                      and not precedente.is_playing):
                retenues[app.key] = session

        for key, session in retenues.items():
            self._publish_one(key, self._payload(session), session.thumbnail)
        with self._lock:
            disparues = [key for key in self._states if key not in retenues]
        for key in disparues:
            self._publish_one(key, {"gone": True}, None)

    def _payload(self, session: Session) -> dict[str, Any]:
        app = identify(session.app_id)
        return {
            "style": self.styles.get(app.key).as_dict(),
            "title": session.title,
            "artist": session.artist,
            "album": session.album,
            "app": app.label,
            "color": app.color,
            # La taille annoncee a l utilisateur voyage avec le style : la
            # page s y contraint, donc le chiffre affiche est vrai PAR
            # CONSTRUCTION, et non parce qu un calcul serait juste.
            "size": list(overlay_size(self.styles.get(app.key))),
            "logo": bool(logo_path(app.key)),
            "playing": session.is_playing,
            "square": session.is_square_art,
            "cover": _stamp(session.thumbnail),
            "position_ms": session.position_ms,
            "duration_ms": session.duration_ms,
            # Empreinte de la page servie. Une source restee ouverte pendant
            # une mise a jour continue de tourner sur l ANCIEN script : elle
            # recevrait les nouveaux reglages sans savoir les afficher.
            "page": page_version(),
        }

    def _publish_one(self, app_id: str, payload: dict[str, Any],
                     cover: Optional[bytes]) -> None:
        data = json.dumps(payload)
        with self._lock:
            if payload.get("gone"):
                self._states.pop(app_id, None)
                self._covers.pop(app_id, None)
            else:
                self._states[app_id] = payload
                if cover is not None:
                    self._covers[app_id] = cover
            subs = list(self._subs.get(app_id, ()))
        for sub in subs:
            try:
                sub.put_nowait(data)
                continue
            except queue.Full:
                pass
            # File saturée : on évince le PLUS ANCIEN. Jeter le nouveau
            # laisserait l'overlay sur un morceau périmé, alors que seul le
            # dernier état compte.
            try:
                sub.get_nowait()
                sub.put_nowait(data)
            except (queue.Empty, queue.Full):
                logger.debug("Publication musique impossible vers %s.", app_id)

    # -- lecture ------------------------------------------------------------ #

    def initial_payload(self, key: str) -> dict[str, Any]:
        """Ce qu un overlay recoit a la seconde ou il se connecte.

        Le style est TOUJOURS joint, meme si rien ne joue. Sans cela, une
        source posee dans OBS avant de lancer la musique restait sur
        l habillage par defaut jusqu au premier morceau : on croyait ses
        reglages perdus alors qu ils attendaient simplement une lecture.
        """
        reglages = self.styles.get(key)
        style = reglages.as_dict()
        taille = list(overlay_size(reglages))
        with self._lock:
            etat = self._states.get(key)
            if etat is None:
                return {"style": style, "size": taille, "style_only": True,
                        "page": page_version()}
            charge = dict(etat)
        charge["style"] = style
        charge["size"] = taille
        charge["page"] = page_version()
        return charge

    def publish_style(self, key: str) -> None:
        """Pousse le style d'une source, lecture en cours ou non.

        Sans cela, changer une couleur n'apparaîtrait dans OBS qu'au morceau
        suivant : l'utilisateur règle en regardant sa scène, il doit voir le
        résultat pendant qu'il règle.
        """
        reglages = self.styles.get(key)
        style = reglages.as_dict()
        taille = list(overlay_size(reglages))
        with self._lock:
            etat = self._states.get(key)
            if etat is not None:
                etat["style"] = style
                etat["size"] = taille
                charge = dict(etat)
            else:
                charge = {"style": style, "size": taille, "style_only": True}
            subs = list(self._subs.get(key, ()))
        data = json.dumps(charge)
        for sub in subs:
            try:
                sub.put_nowait(data)
            except queue.Full:
                logger.debug("Style musique non publié vers %s.", key)

    def _start_audio(self, key: str) -> None:
        if not self._with_audio or key in self._audio:
            return
        releve = AppLevels(key, lambda bars, k=key: self._broadcast_levels(k, bars))
        self._audio[key] = releve
        releve.start()

    def _stop_audio(self, key: str) -> None:
        releve = self._audio.pop(key, None)
        if releve is not None:
            releve.stop()

    def _broadcast_levels(self, key: str, bars: list[float]) -> None:
        """Diffuse les niveaux aux overlays de CETTE source, et d'elle seule.

        C'est le point de toute la chaîne : l'overlay Spotify ne doit réagir
        qu'au son de Spotify, même si un navigateur joue autre chose à côté.

        Appelée depuis le thread de relèvement. Une file pleine est ignorée
        sans éviction : un niveau périmé de 30 ms n'a aucune valeur,
        contrairement à un changement de morceau.
        """
        data = json.dumps({"levels": bars})
        with self._lock:
            abonnes = list(self._subs.get(key, ()))
        for sub in abonnes:
            try:
                sub.put_nowait(data)
            except queue.Full:
                pass

    def stop(self) -> None:
        """Arrête tous les relèvements. Appelée à la fermeture de la fenêtre."""
        for releve in list(self._audio.values()):
            releve.stop()
        self._audio.clear()

    def background(self, key: str) -> Optional[bytes]:
        """Image de fond dessinée par l'utilisateur, servie par /musicbg."""
        return self.styles.background_bytes(key)

    def sources(self) -> list[str]:
        with self._lock:
            return sorted(self._states)

    def snapshot(self, app_id: str) -> Optional[dict[str, Any]]:
        """Dernier état d'une source, pour réamorcer un overlay qui se
        connecte : sans ça, une source navigateur rouverte resterait vide
        jusqu'au prochain changement de morceau."""
        with self._lock:
            state = self._states.get(app_id)
            return dict(state) if state is not None else None

    def cover(self, app_id: str) -> Optional[bytes]:
        with self._lock:
            return self._covers.get(app_id)

    @staticmethod
    def logo(key: str) -> Optional[bytes]:
        """Logo déposé par l'utilisateur pour cette clé, s'il existe.

        Relu à chaque requête plutôt que gardé en mémoire : déposer un
        fichier dans `assets/music/` doit se voir au rechargement de la source
        navigateur, sans redémarrer l'application.
        """
        chemin = logo_path(key)
        if chemin is None:
            return None
        try:
            return chemin.read_bytes()
        except OSError:
            logger.warning("Logo musique illisible : %s", chemin)
            return None

    def subscribe(self, app_id: str) -> "queue.Queue[str]":
        sub: "queue.Queue[str]" = queue.Queue(maxsize=_QUEUE_MAX)
        with self._lock:
            self._subs.setdefault(app_id, []).append(sub)
            premier = len(self._subs.get(app_id, ())) == 1
        if premier:
            self._start_audio(app_id)
        return sub

    def unsubscribe(self, app_id: str, sub: "queue.Queue[str]") -> None:
        with self._lock:
            subs = self._subs.get(app_id)
            if not subs:
                return
            if sub in subs:
                subs.remove(sub)
            if not subs:
                self._subs.pop(app_id, None)
            dernier = app_id not in self._subs
        if dernier:
            self._stop_audio(app_id)

    def listener_count(self, app_id: str) -> int:
        with self._lock:
            return len(self._subs.get(app_id, ()))

    # -- page --------------------------------------------------------------- #

    def page_html(self, token: str, app_id: str, wait_text: str = "") -> str:
        """Gabarit de la page, jeton et source déjà inscrits.

        `json.dumps` produit des littéraux JS sûrs : ni le jeton ni
        l'identifiant de l'application ne peuvent casser le script ni y
        injecter du code.
        """
        return (_PAGE_HTML
                .replace("__GEOMETRIE__", css_variables())
                .replace("__TOKEN__", json.dumps(token))
                .replace("__SOURCE__", json.dumps(app_id))
                .replace("__WAIT_TEXT__", _escape_html(wait_text))
                .replace("__PAGE__", json.dumps(page_version())))


def page_version() -> str:
    """Empreinte du gabarit de page, recalculee a chaque modification.

    Elle sert a un seul usage : une source navigateur ouverte depuis avant une
    mise a jour tourne encore sur l ancien script. Elle recevrait alors les
    nouveaux reglages sans savoir les rendre — un modele macOS qui s affiche
    sans ses pastilles ni sa barre de progression, par exemple. En comparant
    cette empreinte, la page se recharge d elle-meme.
    """
    global _PAGE_VERSION
    if _PAGE_VERSION is None:
        _PAGE_VERSION = hashlib.sha256(
            _PAGE_HTML.encode("utf-8")).hexdigest()[:12]
    return _PAGE_VERSION


_PAGE_VERSION: Optional[str] = None


# Substitution par jetons __XXX__, pas par % ni .format() : le CSS contient
# « 100% » et le JS est plein d'accolades.
_PAGE_HTML = """<!doctype html>
<meta charset="utf-8">
<title>OBS Dynamics - musique</title>
<style>
  /* Fond transparent : OBS compose la page par-dessus la scene. */
  __GEOMETRIE__
  html,body{margin:0;height:100%;background:transparent;overflow:hidden;
    font-family:"Segoe UI",system-ui,sans-serif}
  /* La carte est CENTREE dans la source, pas calee en haut a gauche : une
     source plus grande que la taille annoncee laisse alors du vide tout
     autour, au lieu de coller le widget dans un coin. */
  body{display:flex;align-items:center;justify-content:center}
  #card{display:flex;align-items:center;gap:var(--gap);padding:var(--pad-y) var(--pad-r) var(--pad-y) var(--pad-l);
    background:var(--fond,rgba(15,12,27,.82));
    border:var(--contour,1px solid rgba(168,85,247,.35));
    border-radius:18px;backdrop-filter:blur(12px);
    background-size:cover;background-position:center;
    width:max-content;max-width:calc(100vw - 8px);min-width:var(--large,0);
    opacity:0;transition:opacity .35s ease}
  #card.on{opacity:1}

  /* Vinyle : disque noir sillonne, pochette en label central, rotation
     continue pendant la lecture et arret net a la pause. */
  /* Enveloppe NON tournante : en mode Blocs, le cadre se pose ici. Sur le
     disque lui-meme, il tournerait avec lui. */
  #discbox{flex:0 0 auto;line-height:0}
  #disc{position:relative;width:var(--cover);height:var(--cover);
    border-radius:50%;background:
      repeating-radial-gradient(circle at 50% 50%,#050509 0 2px,#15151f 2px 4px);
    box-shadow:0 8px 26px rgba(0,0,0,.55);
    animation:spin 4s linear infinite;animation-play-state:paused}
  #disc.spinning{animation-play-state:running}
  #disc::after{content:"";position:absolute;top:50%;left:50%;
    width:11px;height:11px;margin:-5.5px 0 0 -5.5px;border-radius:50%;
    background:#0F0C1B;z-index:2}
  #disc img{position:absolute;top:50%;left:50%;
    width:62%;height:62%;transform:translate(-50%,-50%);
    border-radius:50%;object-fit:cover}
  @keyframes spin{to{transform:rotate(360deg)}}

  /* Vignette non carree, donc une video : rectangle a son propre rapport,
     pose a plat. La faire tourner donnerait un vinyle rectangulaire. */
  #frame{flex:0 0 auto;width:var(--cover);border-radius:12px;overflow:hidden;
    box-shadow:0 8px 26px rgba(0,0,0,.55);line-height:0}
  #frame.wide{width:var(--cover-wide)}
  /* L image est RECADREE, jamais deformee, et sa hauteur est deterministe :
     sans rapport impose, une pochette carree affichee en « Large » donnait un
     bloc de 210x210 au lieu d un 16:9, ce qui rendait la taille de source
     annoncee fausse. */
  #frame img{display:block;width:100%;object-fit:cover}
  #frame:not(.wide) img{aspect-ratio:1/1;height:var(--cover)}
  #frame.wide img{aspect-ratio:16/9;height:auto}

  /* La pile porte le texte et l onde. Une brique masquee est en display:none
     donc elle ne prend AUCUNE place et n ajoute pas d ecart : c est ce qui
     evite les cases vides quand on coupe la pochette ou la forme d onde. */
  /* L onde est une soeur du texte dans la pile, et s etire sur sa largeur :
     son bord gauche tombe donc toujours sur celui du titre et de l artiste.
     C est ce qui evite qu elle parte sous la pochette. */
  #stack{display:flex;flex-direction:column;align-items:stretch;min-width:0}
  #text{min-width:0}
  #title{color:var(--encre,#F3F0FA);font-size:var(--title-size);font-weight:700;line-height:1.2;
    max-width:var(--title-max);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  #line{display:flex;align-items:center;gap:10px;margin-top:var(--line-gap);min-width:0}
  #artist{color:var(--attenue,#C9C1E0);font-size:17px;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap;max-width:var(--artist-max)}
  #app{display:flex;align-items:center;gap:7px;flex:0 0 auto;
    color:var(--marque,#A855F7);font-size:12px;font-weight:700;
    letter-spacing:.06em;text-transform:uppercase;
    border:1px solid currentColor;border-radius:999px;padding:3px 11px}
  #applogo{width:15px;height:15px;object-fit:contain}
  /* Forme d'onde : 48 barres, hauteur pilotee par scaleY. Une transform
     ne declenche ni layout ni repaint, contrairement a height : c'est ce qui
     tient 45 images par seconde sans faire chauffer OBS. */
  #wave{display:flex;align-items:flex-end;gap:2px;height:var(--wave-h);margin-top:var(--wave-gap)}
  #wave i{display:block;flex:0 0 auto;height:100%;border-radius:2px;
    background:var(--accent,#A855F7);opacity:.85;
    transform:scaleY(.04);transform-origin:bottom}
  /* Dispositions. Chacune ne fait que replacer les memes briques : aucune
     n'ajoute d'element, ce qui garde une seule source de verite dans le DOM. */
  /* Blocs : `display:contents` fait remonter le texte et l onde au rang des
     freres de la pochette, donc chaque brique devient sa propre case. Une
     brique masquee disparait de la rangee, sans laisser de cadre vide. */
  #card[data-layout="blocs"]{gap:var(--bloc-gap);align-items:stretch}
  #card[data-layout="blocs"] #stack{display:contents}
  #card[data-layout="blocs"] #discbox,
  #card[data-layout="blocs"] #frame,
  #card[data-layout="blocs"] #text,
  #card[data-layout="blocs"] #wave{padding:var(--bloc-pad);border-radius:12px;
    border:1px solid var(--accent,#A855F7);
    display:flex;flex-direction:column;justify-content:center}
  #card[data-layout="blocs"] #wave{flex-direction:row;align-items:flex-end;
    height:auto;width:var(--bloc-wave);margin:0}

  /* Galerie : la pochette est centree, mais la PILE se dimensionne sur son
     contenu. L onde fait alors exactement la largeur du texte, donc son bord
     gauche tombe sur celui du nom de l artiste. Une pile a 100 % de la carte
     aurait deborde a gauche du texte centre. */
  #card[data-layout="galerie"]{flex-direction:column;align-items:center;
    gap:var(--galerie-gap)}
  #card[data-layout="galerie"] #stack{align-items:flex-start;
    width:max-content;max-width:100%}

  #card[data-layout="minimal"]{padding:var(--minimal-pad-y) var(--minimal-pad-x)}
  #card[data-layout="minimal"] #title{font-size:var(--minimal-title)}

  /* Bandeau : une onde plus haute, pas une carte plus large. Forcer
     `width:100%` la faisait occuper toute la source OBS et deborder du cadre.
     L onde reste dans la colonne de texte pour rester alignee, et ne passe
     donc jamais sous la pochette. */
  #card[data-layout="bandeau"]{padding:var(--pad-y) var(--bandeau-pad-x)}
  #card[data-layout="bandeau"] #wave{height:var(--bandeau-wave-h);margin-top:var(--bandeau-wave-gap)}

  /* Pastilles de fenetre : posees en absolu pour ne pas restructurer la
     carte. Quand elles sont la, le haut de la carte leur fait de la place. */
  #card{position:relative}
  #card.avec-pastilles{padding-top:44px}
  #dots{position:absolute;top:16px;left:18px;display:flex;gap:8px}
  #dots i{width:12px;height:12px;border-radius:50%;display:block}
  #dots i:nth-child(1){background:#FF5F57}
  #dots i:nth-child(2){background:#FEBC2E}
  #dots i:nth-child(3){background:#28C840}

  /* Barre de progression : vraies donnees SMTC, interpolees pendant la
     lecture pour avancer entre deux evenements. */
  #progress{margin-top:14px}
  #bar{height:4px;border-radius:2px;background:color-mix(in srgb,
    var(--accent,#A855F7) 28%, transparent);overflow:hidden}
  #fill{display:block;height:100%;width:0;border-radius:2px;
    background:var(--encre,#F3F0FA)}
  #times{display:flex;justify-content:space-between;margin-top:8px;
    font-size:13px;color:var(--attenue,#A39BBD)}

  /* Transport : DECORATIF. Une source navigateur OBS n est pas cliquable a
     l ecran ; ces icones montrent l etat, elles ne commandent rien. */
  #controls{display:flex;align-items:center;gap:18px;flex:0 0 auto;
    margin-left:26px}
  #controls svg{width:26px;height:26px;fill:var(--encre,#F3F0FA)}
  #controls svg:first-child,#controls svg:last-child{width:22px;height:22px;
    opacity:.9}

  #wait{color:#9B93B5;font-size:15px;padding:14px 20px}
  [hidden]{display:none !important}
</style>
<div id="wait">__WAIT_TEXT__</div>
<div id="card" hidden>
  <div id="dots" hidden><i></i><i></i><i></i></div>
  <div id="discbox" hidden><div id="disc"><img id="discimg" alt=""></div></div>
  <div id="frame" hidden><img id="frameimg" alt=""></div>
  <div id="stack">
    <div id="text">
      <div id="title"></div>
      <div id="line">
        <span id="artist"></span>
        <span id="app" hidden><img id="applogo" alt="" hidden><span id="appname"></span></span>
      </div>
    </div>
    <div id="wave" hidden></div>
    <div id="progress" hidden>
      <div id="bar"><i id="fill"></i></div>
      <div id="times"><span id="elapsed">0:00</span><span id="total">0:00</span></div>
    </div>
  </div>
  <div id="controls" hidden>
    <svg viewBox="0 0 24 24"><path d="M11 5v14L4 12zM20 5v14l-7-7z"/></svg>
    <svg id="playpause" viewBox="0 0 24 24"><path d="M7 4h4v16H7zM13 4h4v16h-4z"/></svg>
    <svg viewBox="0 0 24 24"><path d="M13 5v14l7-7zM4 5v14l7-7z"/></svg>
  </div>
</div>
<script>
const TOKEN = __TOKEN__;
const SOURCE = __SOURCE__;
const PAGE = __PAGE__;
const card = document.getElementById("card");
const wait = document.getElementById("wait");
const disc = document.getElementById("disc");
const discBox = document.getElementById("discbox");
const frame = document.getElementById("frame");
const discImg = document.getElementById("discimg");
const frameImg = document.getElementById("frameimg");
const elTitle = document.getElementById("title");
const elArtist = document.getElementById("artist");
const elApp = document.getElementById("app");
const dots = document.getElementById("dots");
const controls = document.getElementById("controls");
const progress = document.getElementById("progress");
const fill = document.getElementById("fill");
const elapsed = document.getElementById("elapsed");
const total = document.getElementById("total");
const playpause = document.getElementById("playpause");

// Position de lecture. SMTC ne l envoie qu aux changements : on l avance
// nous-memes entre deux evenements, sinon la barre resterait figee pendant
// tout un morceau.
let posMs = 0, dureeMs = 0, joue = false, posHorloge = 0;

function mmss(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

function majProgression() {
  if (progress.hidden) { return; }
  let courant = posMs;
  if (joue && posHorloge) { courant += performance.now() - posHorloge; }
  if (dureeMs > 0) { courant = Math.min(courant, dureeMs); }
  fill.style.width = dureeMs > 0 ? (100 * courant / dureeMs) + "%" : "0%";
  elapsed.textContent = mmss(courant);
  total.textContent = dureeMs > 0 ? mmss(dureeMs) : "--:--";
}
const elAppName = document.getElementById("appname");
const elAppLogo = document.getElementById("applogo");
let coverStamp = null;
let styleStamp = null;
// Les evenements de niveaux ne portent pas le style : on garde le dernier
// applique pour que render() sache toujours quelle forme de pochette utiliser.
let styleCourant = null;

// --- Forme d'onde ------------------------------------------------------- //
const BARS = 48;
const wave = document.getElementById("wave");
const barres = [];
let cibles = new Float32Array(BARS);
let courants = new Float32Array(BARS);
let derniereFrame = 0;
let rafId = null;

// Montee rapide, descente lente : une attaque suivie a la milliseconde et une
// retombee douce, c'est ce qui donne l'impression que la barre "suit" le son.
const TAU_MONTEE = 0.045;
const TAU_DESCENTE = 0.16;

// Generateur deterministe (mulberry32) : les largeurs varient d'une barre a
// l'autre, comme un trace a la main, mais restent IDENTIQUES a chaque
// chargement. Un aleatoire par image ferait vibrer la largeur des barres.
function suite(graine) {
  return function () {
    graine |= 0; graine = (graine + 0x6D2B79F5) | 0;
    let t = Math.imul(graine ^ (graine >>> 15), 1 | graine);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

(function construireBarres() {
  const tirage = suite(20260916);
  for (let i = 0; i < BARS; i++) {
    const barre = document.createElement("i");
    barre.style.width = (2 + Math.round(tirage() * 2)) + "px";
    wave.appendChild(barre);
    barres.push(barre);
  }
})();

function animer(ts) {
  rafId = requestAnimationFrame(animer);
  majProgression();
  // Lissage indexe sur le TEMPS ecoule, pas sur le nombre d'images : si OBS
  // ralentit le rendu, la forme d'onde garde la meme vitesse de reponse.
  const dt = derniereFrame ? Math.min((ts - derniereFrame) / 1000, 0.1) : 0.016;
  derniereFrame = ts;
  for (let i = 0; i < BARS; i++) {
    const cible = cibles[i];
    const tau = cible > courants[i] ? TAU_MONTEE : TAU_DESCENTE;
    courants[i] += (cible - courants[i]) * (1 - Math.exp(-dt / tau));
    barres[i].style.transform = "scaleY(" + (0.04 + courants[i] * 0.96).toFixed(3) + ")";
  }
}
rafId = requestAnimationFrame(animer);

function appliquerNiveaux(valeurs) {
  if (!Array.isArray(valeurs) || valeurs.length !== BARS) { return; }
  if (styleCourant && styleCourant.show_wave === false) { return; }
  wave.hidden = false;
  for (let i = 0; i < BARS; i++) {
    const v = valeurs[i];
    cibles[i] = typeof v === "number" && v >= 0 && v <= 1 ? v : 0;
  }
}

function applyStyle(s) {
  // Recue a chaque evenement : regler une couleur dans l'application doit se
  // voir dans OBS tout de suite, sans attendre le morceau suivant.
  if (!s) { return; }
  styleCourant = s;
  const cle = JSON.stringify(s);
  if (cle === styleStamp) { return; }
  styleStamp = cle;
  const o = Math.max(0, Math.min(100, s.opacity)) / 100;
  const r = parseInt(s.bg.slice(1, 3), 16);
  const g = parseInt(s.bg.slice(3, 5), 16);
  const b = parseInt(s.bg.slice(5, 7), 16);
  card.style.setProperty("--fond", "rgba(" + r + "," + g + "," + b + "," + o + ")");
  card.style.setProperty("--contour", s.border_on
    ? (s.border_dashed ? "2px dashed " : "1px solid ") + s.border
    : "1px solid transparent");
  card.style.setProperty("--accent", s.accent || s.border);
  // L encre vient du style : un fond clair impose un texte sombre, sinon la
  // carte serait blanche sur blanc.
  card.style.setProperty("--encre", s.ink || "#F3F0FA");
  card.style.setProperty("--attenue", s.muted || "#A39BBD");
  card.dataset.layout = s.layout || "compact";
  card.classList.toggle("avec-pastilles", s.show_dots === true);
  dots.hidden = s.show_dots !== true;
  if (s.background_image) {
    // L'image passe par une route dediee : la politique de securite du
    // contenu n'autorise que les images de cette origine.
    card.style.backgroundImage = "url(/musicbg/" + TOKEN + "?source="
      + encodeURIComponent(SOURCE) + ")";
    card.style.setProperty("--large", "560px");
  } else {
    card.style.backgroundImage = "none";
    card.style.setProperty("--large", "0");
  }
}

function coverUrl(stamp) {
  // L'empreinte fait partie de l'URL : le navigateur garde l'image en cache
  // tant que le morceau ne change pas, et la recharge des qu'il change.
  return "/musiccover/" + TOKEN + "?source=" + encodeURIComponent(SOURCE)
         + "&v=" + encodeURIComponent(stamp);
}

function hide() {
  card.classList.remove("on");
  disc.classList.remove("spinning");
  coverStamp = null;
}

function render(m) {
  if (!m) { return; }
  if (m.levels) { appliquerNiveaux(m.levels); return; }
  applyStyle(m.style);
  if (m.style_only) { return; }
  if (m.gone) { hide(); return; }
  wait.hidden = true;
  card.hidden = false;

  // textContent, jamais innerHTML : un titre de morceau est du texte
  // quelconque, l'interpreter comme du balisage serait une injection.
  elTitle.textContent = m.title || "";
  elArtist.textContent = m.artist || "";
  elAppName.textContent = m.app || "";
  elApp.hidden = !m.app;
  // La couleur de marque passe par une variable CSS : la bordure du badge
  // suit `currentColor`, donc une seule affectation les accorde.
  elApp.style.setProperty("--marque", m.color || "#A855F7");
  elAppLogo.hidden = !m.logo;
  if (m.logo) {
    elAppLogo.src = "/musiclogo/" + TOKEN + "?source=" + encodeURIComponent(SOURCE);
  }

  const reglages = styleCourant || {};
  // « Auto » suit l'image recue : disque pour une pochette carree, rectangle
  // pour une miniature de video. Les autres modes forcent la forme.
  let forme = reglages.cover || "auto";
  if (forme === "auto") { forme = m.square !== false ? "vinyle" : "large"; }
  if (reglages.layout === "minimal") { forme = "aucune"; }

  const visible = !!m.cover && forme !== "aucune";
  const disque = visible && forme === "vinyle";
  discBox.hidden = !disque;
  frame.hidden = !visible || disque;
  frame.classList.toggle("wide", forme === "large");
  if (!m.cover) {
    coverStamp = null;
  } else if (m.cover !== coverStamp) {
    coverStamp = m.cover;
    const url = coverUrl(m.cover);
    if (disque) { discImg.src = url; } else { frameImg.src = url; }
  }
  disc.classList.toggle("spinning", !!m.playing && disque);

  // Progression et transport. La position vient de SMTC ; l horloge sert a
  // l avancer entre deux evenements.
  posMs = typeof m.position_ms === "number" ? m.position_ms : 0;
  dureeMs = typeof m.duration_ms === "number" ? m.duration_ms : 0;
  joue = !!m.playing;
  posHorloge = performance.now();
  progress.hidden = reglages.show_progress !== true;
  majProgression();

  controls.hidden = reglages.show_controls !== true;
  // Lecture en cours : on montre le bouton qu on presserait, donc pause.
  playpause.innerHTML = joue
    ? '<path d="M7 4h4v16H7zM13 4h4v16h-4z"></path>'
    : '<path d="M8 5v14l11-7z"></path>';

  // Elements optionnels : masques, ils ne prennent plus de place du tout.
  elArtist.hidden = reglages.show_artist === false;
  if (reglages.show_app === false) { elApp.hidden = true; }
  if (reglages.show_wave === false) { wave.hidden = true; }
  card.classList.add("on");
}

let flux = null;
let relance = null;
let recharge = false;

function connect() {
  const es = new EventSource("/musicevents/" + TOKEN
                             + "?source=" + encodeURIComponent(SOURCE));
  flux = es;
  es.addEventListener("music", (ev) => {
    let m = null;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    // L application a ete mise a jour sous la source restee ouverte : ce
    // script ne sait pas afficher ce qu elle envoie maintenant.
    //
    // UNE seule fois : si le navigateur resservait sa copie en cache malgre
    // `Cache-Control: no-store`, rechargerait a chaque message et la source
    // clignoterait sans fin. Mieux vaut un habillage en retard qu une page
    // qui se recharge en boucle.
    if (m.page && m.page !== PAGE) {
      if (!recharge) { recharge = true; location.reload(); }
      return;
    }
    render(m);
  });
  es.onerror = () => {
    // L'application peut redemarrer sous la source deja ouverte : on retente
    // au lieu de rester fige sur un morceau mort.
    es.close();
    hide();
    relance = setTimeout(connect, 2000);
  };
}
connect();

// OBS garde le processus du navigateur en vie entre deux scenes : sans ca, une
// source fermee laisserait derriere elle un flux SSE, un timer de relance et
// une boucle d'animation qui tournent dans le vide.
window.addEventListener("pagehide", () => {
  if (relance !== null) { clearTimeout(relance); relance = null; }
  if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
  if (flux !== null) { flux.close(); flux = null; }
});
</script>
"""
