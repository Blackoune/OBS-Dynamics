"""Moteur des sous-titres : modèles, reconnaissance en flux, réglages.

Reconnaissance : sherpa-onnx, modèles Zipformer « streaming » de Kroko
(Banafo, licence CC-BY-SA). Le son arrive par blocs de 100 ms ; chaque bloc
peut faire évoluer la phrase en cours (`partial`), et une pause la fige
(`final`). C'est ce flux qui permet un affichage quasi instantané : un mot
apparaît dès qu'il est reconnu, puis peut encore être corrigé tant que la
phrase n'est pas terminée.

Les modèles ne sont pas livrés avec l'application (55 à 125 Mo chacun) : ils
se téléchargent au premier usage depuis les releases officielles de
sherpa-onnx, et leur SHA-256 est vérifié avant toute extraction.

Tout tourne hors du thread Tk : l'appelant reçoit les callbacks dans le
thread de capture et les repasse à l'interface par `post_ui`.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import queue
import shutil
import tarfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional

import numpy as np

import subtitles_audio as audio
from app_paths import DATA_DIR, logger

try:
    import sherpa_onnx as _sherpa
    IMPORT_ERROR = ""
except Exception as exc:
    _sherpa = None
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

MODELS_DIR = DATA_DIR / "models"
SETTINGS_PATH = DATA_DIR / "subtitles.json"
_RELEASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
_MODEL_FILES = ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt")

PARTIAL = "partial"
FINAL = "final"
#: Texte corrigé par le second passage, pour une phrase déjà affichée.
REVISE = "revise"

PRECISION_FAST = "fast"
PRECISION_ACCURATE = "accurate"
#: Pause qui fige une phrase (secondes) : court = sous-titre plus réactif mais
#: phrases coupées plus souvent ; long = phrases entières, affichage figé plus tard.
END_PAUSE_RANGE = (0.3, 1.5)


def available() -> bool:
    return _sherpa is not None and audio.available()


def import_error() -> str:
    return IMPORT_ERROR or audio.IMPORT_ERROR


@dataclass(frozen=True)
class Model:
    lang: str
    name: str                 # nom de l'archive sans extension, = dossier extrait
    size: int                 # octets de l'archive
    sha256: str
    files: tuple[str, ...] = _MODEL_FILES


# Empreintes relevées sur l'API GitHub de la release `asr-models`
# (champ `digest`) le 2026-10-23.
MODELS: dict[str, Model] = {m.lang: m for m in (
    Model("fr", "sherpa-onnx-streaming-zipformer-fr-kroko-2025-08-06", 57_220_361,
          "e6ffd3dc43725cd6c8137b05c739f15607d0df946b9b90eb141e10059efca024"),
    Model("en", "sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06", 57_267_600,
          "c8676e5ff9ac2a85296e53ee0fd4d5fb1db6770e7a7647166eeafe349ade6834"),
    Model("de", "sherpa-onnx-streaming-zipformer-de-kroko-2025-08-06", 57_565_698,
          "9e27b783c20e67b0d0f13a258c1861fce199917c969d9176a438bee38df64962"),
    Model("es", "sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06", 124_394_665,
          "31b2230a95d23290b308b393da930015a4b2105cb3abb9367aed35f7fcf29cf1"),
)}

#: Second passage, mode « Précis » : NVIDIA Parakeet TDT 0.6B v3 (CC-BY-4.0),
#: un seul modèle pour les 4 langues. Il relit chaque phrase terminée en
#: entier. Mesuré le 2026-10-23 sur trois phrases françaises difficiles : sans
#: faute là où le modèle de flux en faisait (« Elden Rin », « resté »,
#: « qui l'ont obtenu »), en 0,3 à 0,4 s par phrase de 5 à 7 s sur 4 cœurs.
#: Canary 180M, essayé aussi, faisait moins bien que le modèle de flux.
REFINER = Model("multi", "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8", 487_170_055,
                "5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf",
                ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"))


class DownloadCancelled(Exception):
    pass


# --------------------------------------------------------------------------- #
# Modèles sur le disque
# --------------------------------------------------------------------------- #

def model_dir(model: Model, root: Path = MODELS_DIR) -> Path:
    return root / model.name


def is_installed(model: Model, root: Path = MODELS_DIR) -> bool:
    folder = model_dir(model, root)
    return all((folder / f).is_file() for f in model.files)


def download(model: Model, root: Path = MODELS_DIR,
             on_progress: Callable[[int, int], None] = lambda _d, _t: None,
             cancel: Optional[threading.Event] = None,
             chunks: Optional[Callable[[str], Iterable[bytes]]] = None) -> Path:
    """Télécharge, vérifie et extrait un modèle. Rien n'est extrait si
    l'empreinte ne correspond pas ; l'archive partielle est toujours effacée.

    `chunks` remplace le téléchargement réseau (tests).
    """
    root.mkdir(parents=True, exist_ok=True)
    part = root / (model.name + ".tar.bz2.part")
    digest = hashlib.sha256()
    done = 0
    try:
        with part.open("wb") as out:
            for chunk in (chunks or _http_chunks)(_RELEASE_URL + model.name + ".tar.bz2"):
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled()
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                on_progress(done, model.size)
        if digest.hexdigest() != model.sha256:
            raise ValueError(f"SHA-256 mismatch for {model.name}")
        _extract(part, model_dir(model, root), model.files)
    finally:
        part.unlink(missing_ok=True)
    logger.info("Sous-titres : modèle %s installé.", model.name)
    return model_dir(model, root)


def _http_chunks(url: str) -> Iterator[bytes]:
    import requests
    with requests.get(url, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        yield from resp.iter_content(chunk_size=1 << 16)


def _extract(archive: Path, dest: Path, wanted: tuple[str, ...]) -> None:
    """N'extrait QUE les fichiers du modèle, à plat dans `dest`.
    Aucun chemin de l'archive n'est réutilisé : pas d'écriture hors du dossier."""
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)      # reste d'un essai interrompu
    tmp.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            for member in tar:
                base = Path(member.name).name
                if member.isfile() and base in wanted:
                    src = tar.extractfile(member)
                    if src is not None:
                        # Par morceaux : l'encodeur de Parakeet pèse 620 Mo.
                        with src, (tmp / base).open("wb") as out:
                            shutil.copyfileobj(src, out, 1 << 20)
        missing = [f for f in wanted if not (tmp / f).is_file()]
        if missing:
            raise ValueError(f"archive incomplete, missing {missing}")
        shutil.rmtree(dest, ignore_errors=True)
        tmp.replace(dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


# --------------------------------------------------------------------------- #
# Reconnaissance
# --------------------------------------------------------------------------- #

class Recognizer:
    """Transforme des blocs audio en évènements (PARTIAL|FINAL, texte).

    `raw` est un `sherpa_onnx.OnlineRecognizer`, ou un double de test qui en
    imite les cinq méthodes utilisées.
    """

    #: Au plus 12 s par phrase (règle 3 du moteur) : la mémoire tampon de la
    #: phrase en cours ne dépasse donc jamais ~15 s de son.
    MAX_UTTERANCE_S = 12.0

    def __init__(self, raw: Any, end_pause: float = 0.8) -> None:
        self._raw = raw
        self._stream = raw.create_stream()
        self._end_pause = end_pause
        self._last = ""
        self._audio: list[np.ndarray] = []
        #: Son de la dernière phrase figée, pour le second passage.
        self.last_utterance: np.ndarray = np.zeros(0, np.float32)

    @classmethod
    def load(cls, folder: Path, threads: int = 2, end_pause: float = 0.8) -> "Recognizer":
        if _sherpa is None:
            raise RuntimeError(IMPORT_ERROR)
        raw = _sherpa.OnlineRecognizer.from_transducer(
            tokens=str(folder / "tokens.txt"), encoder=str(folder / "encoder.onnx"),
            decoder=str(folder / "decoder.onnx"), joiner=str(folder / "joiner.onnx"),
            num_threads=threads, sample_rate=audio.SAMPLE_RATE, feature_dim=80,
            # Recherche en faisceau essayée : aucun mot de mieux sur nos
            # phrases de test, 15 % de calcul en plus. La précision vient du
            # second passage, pas d'ici.
            decoding_method="greedy_search", enable_endpoint_detection=True,
            # Une phrase se fige après `end_pause` de silence une fois des mots
            # reconnus, et au plus tard après 12 s : au-delà, un sous-titre
            # devient illisible.
            rule1_min_trailing_silence=2.0, rule2_min_trailing_silence=end_pause,
            rule3_min_utterance_length=cls.MAX_UTTERANCE_S)
        return cls(raw, end_pause)

    def feed(self, samples: np.ndarray) -> list[tuple[str, str]]:
        raw, stream = self._raw, self._stream
        self._audio.append(samples)
        stream.accept_waveform(audio.SAMPLE_RATE, samples)
        while raw.is_ready(stream):
            raw.decode_stream(stream)
        text = raw.get_result(stream).strip()
        if not any(c.isalnum() for c in text):
            text = ""                    # un « . » seul après un bruit
        events: list[tuple[str, str]] = []
        if text != self._last:
            self._last = text
            if text:
                events.append((PARTIAL, text))
        if raw.is_endpoint(stream):
            carry: list[np.ndarray] = []
            if text:
                events.append((FINAL, text))
                head, tail = split_next_sentence(np.concatenate(self._audio), self._end_pause)
                self.last_utterance = head
                carry = [tail] if tail.size else []
            self.reset()
            self._audio = carry           # début de la phrase suivante, déjà prononcé
        elif not text and len(self._audio) > 20:
            # Silence sans aucun mot : inutile d'en garder plus de 2 s
            # d'avance pour le second passage.
            del self._audio[:-20]
        return events

    def reset(self) -> None:
        """Oublie la phrase en cours (après une coupure du son, par exemple)."""
        self._raw.reset(self._stream)
        self._last = ""
        self._audio = []


_FRAME = audio.SAMPLE_RATE // 100                        # 10 ms


def _loud_frames(samples: np.ndarray) -> np.ndarray:
    """Indices des tranches de 10 ms qui contiennent de la parole."""
    n = len(samples) // _FRAME
    if n == 0:
        return np.zeros(0, dtype=int)
    rms = np.sqrt(np.mean(np.square(samples[:n * _FRAME].reshape(n, _FRAME)), axis=1))
    # Seuil relatif à la parole ET au souffle du micro : un seuil fixe compte
    # le souffle comme de la parole quand on parle doucement.
    return np.flatnonzero(rms > max(rms.max() * 0.05, np.percentile(rms, 10) * 3, 1e-3))


#: Au-delà, ce qui suit la pause n'est plus « le début de la phrase
#: suivante » prononcé pendant que le moteur hésitait, mais un morceau de la
#: phrase elle-même : on ne coupe pas.
_MAX_CARRY_S = 1.5


def split_next_sentence(samples: np.ndarray,
                        gap_s: float = 0.8) -> tuple[np.ndarray, np.ndarray]:
    """Sépare la phrase figée du début de la suivante.

    Le moteur de flux ne fige une phrase qu'après la pause, parfois plus d'une
    seconde après : si l'on reprend la parole entre-temps, le début de la
    phrase suivante (« Mer… ») finit collé à la précédente. On coupe au
    dernier silence d'au moins `gap_s` (la pause qui fige une phrase) suivi
    d'au plus `_MAX_CARRY_S` de parole. Une respiration au milieu d'une phrase,
    plus courte que `gap_s`, n'est jamais une coupure.
    """
    loud = _loud_frames(samples)
    if loud.size < 2:
        return samples, samples[:0]
    gaps = np.flatnonzero(np.diff(loud) > gap_s * 100)
    if gaps.size == 0:
        return samples, samples[:0]
    resumed = loud[gaps[-1] + 1] * _FRAME                    # reprise de la parole
    if (len(samples) - resumed) / audio.SAMPLE_RATE > _MAX_CARRY_S:
        return samples, samples[:0]
    cut = (loud[gaps[-1]] + 1 + int(gap_s * 50)) * _FRAME   # dans le silence
    return samples[:cut], samples[cut:]


def trim_silence(samples: np.ndarray, margin_s: float = 0.3) -> np.ndarray:
    """Retire le silence avant et après la parole, en gardant `margin_s`.

    Nécessaire, pas cosmétique : Parakeet tronque des mots quand la phrase
    finit par plus de ~0,9 s de silence (« s'inscriront » devenait
    « s'inscrir », mesuré le 2026-10-23). Or en direct, une phrase n'est
    figée qu'APRÈS la pause : son audio finit toujours par du silence.
    """
    frame = _FRAME
    n = len(samples) // frame
    loud = _loud_frames(samples)
    if loud.size == 0:
        return samples
    margin = int(margin_s * 100)
    first = max(0, loud[0] - margin) * frame
    last = min(n, loud[-1] + 1 + margin) * frame
    return samples[first:last]


class Refiner:
    """Second passage : relit chaque phrase figée avec un modèle plus précis,
    dans son propre thread, pour ne jamais retarder la capture.

    `on_revise(uid, text)` est appelé dans ce thread. Si le moteur prend du
    retard (PC très chargé), les phrases les plus anciennes sont abandonnées :
    leur texte du premier passage reste affiché, rien n'est perdu.
    """

    BACKLOG = 3

    def __init__(self, raw: Any, on_revise: Callable[[int, str], None]) -> None:
        self._raw = raw
        self._on_revise = on_revise
        self._queue: "queue.Queue[Optional[tuple[int, np.ndarray]]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="subtitles-refine",
                                        daemon=True)
        self._thread.start()

    @classmethod
    def load(cls, folder: Path, on_revise: Callable[[int, str], None],
             threads: int = 4) -> "Refiner":
        if _sherpa is None:
            raise RuntimeError(IMPORT_ERROR)
        raw = _sherpa.OfflineRecognizer.from_transducer(
            encoder=str(folder / "encoder.int8.onnx"), decoder=str(folder / "decoder.int8.onnx"),
            joiner=str(folder / "joiner.int8.onnx"), tokens=str(folder / "tokens.txt"),
            model_type="nemo_transducer", num_threads=threads)
        return cls(raw, on_revise)

    def submit(self, uid: int, samples: np.ndarray) -> None:
        while self._queue.qsize() >= self.BACKLOG:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put((uid, samples))

    def close(self) -> None:
        """Abandonne les phrases en attente : après Arrêter, elles ne
        s'afficheraient plus, et occuperaient 4 cœurs pour rien."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put(None)

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            uid, samples = job
            try:
                stream = self._raw.create_stream()
                stream.accept_waveform(audio.SAMPLE_RATE, trim_silence(samples))
                self._raw.decode_stream(stream)
                text = stream.result.text.strip()
                if any(c.isalnum() for c in text):
                    self._on_revise(uid, text)
            except Exception:
                # Le thread doit survivre à une phrase ratée ou à une fenêtre
                # déjà fermée : la suivante sera corrigée normalement.
                logger.exception("Sous-titres : second passage impossible.")


# --------------------------------------------------------------------------- #
# Session : capture + reconnaissance dans un thread
# --------------------------------------------------------------------------- #

#: Numéros de phrase, uniques pour toute la durée de l'application : une
#: correction en retard d'une session arrêtée ne peut ainsi jamais désigner
#: une ligne de la session suivante.
_UIDS = itertools.count()

ST_STOPPED = "stopped"
ST_LOADING = "loading"
ST_LISTENING = "listening"
ST_WAITING_DEVICE = "waiting_device"
ST_ERROR = "error"


class Transcriber:
    """Écoute un périphérique et émet les sous-titres, jusqu'à `stop()`.

    Callbacks, appelés dans le thread de capture (REVISE : dans celui du
    second passage) :
      on_text(kind, text, uid)   kind = PARTIAL, FINAL ou REVISE ; `uid`
                                 numérote la phrase, pour que REVISE remplace
                                 la bonne ligne
      on_tick(level, compute_ms) ~10 fois par seconde
      on_state(state, detail)
    Si le périphérique disparaît, la session attend qu'il revienne (même nom,
    même type) et reprend seule.

    `refine` charge le second passage (mode Précis) ; None = mode Rapide.
    """

    RETRY_S = 2.0

    def __init__(self, device: audio.Device, model_folder: Path, *,
                 on_text: Callable[[str, str, int], None],
                 on_tick: Callable[[float, float], None] = lambda _l, _c: None,
                 on_state: Callable[[str, str], None] = lambda _s, _d: None,
                 load: Callable[[Path], Recognizer] = Recognizer.load,
                 refine: Optional[Callable[[Callable[[int, str], None]], Refiner]] = None,
                 blocks: Callable[[audio.Device], Iterable[np.ndarray]] = audio.blocks,
                 devices: Callable[[], list[audio.Device]] = audio.list_devices) -> None:
        self.device = device
        self._folder = model_folder
        self._on_text, self._on_tick, self._on_state = on_text, on_tick, on_state
        self._load, self._refine_load = load, refine
        self._blocks, self._devices = blocks, devices
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._refiner: Optional[Refiner] = None
        self._uid = next(_UIDS)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="subtitles", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if timeout and self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        self._on_state(ST_LOADING, "")
        try:
            rec = self._load(self._folder)
        except Exception as exc:
            logger.exception("Sous-titres : chargement du modèle impossible.")
            self._on_state(ST_ERROR, f"{type(exc).__name__}: {exc}")
            return
        if self._refine_load is not None:
            try:
                self._refiner = self._refine_load(self._revised)
            except Exception:
                # Le premier passage marche : mieux vaut des sous-titres non
                # corrigés que pas de sous-titres du tout.
                logger.exception("Sous-titres : second passage indisponible, "
                                 "on continue sans correction.")
        try:
            while not self._stop.is_set():
                try:
                    self._listen(rec)
                except Exception as exc:
                    logger.warning("Sous-titres : capture interrompue (%s).", exc)
                    self._on_state(ST_WAITING_DEVICE, f"{type(exc).__name__}: {exc}")
                    if not self._wait_for_device():
                        break
        finally:
            if self._refiner is not None:
                self._refiner.close()
        self._on_state(ST_STOPPED, "")

    def _revised(self, uid: int, text: str) -> None:
        if not self._stop.is_set():
            self._on_text(REVISE, text, uid)

    def _listen(self, rec: Recognizer) -> None:
        rec.reset()                      # pas de mots d'avant une coupure
        self._on_state(ST_LISTENING, "")
        source = iter(self._blocks(self.device))
        try:
            for block in source:
                if self._stop.is_set():
                    return
                t0 = time.perf_counter()
                events = rec.feed(block)
                compute_ms = (time.perf_counter() - t0) * 1000
                if self._stop.is_set():
                    return
                for kind, text in events:
                    self._on_text(kind, text, self._uid)
                    if kind == FINAL:
                        if self._refiner is not None:
                            self._refiner.submit(self._uid, rec.last_utterance)
                        self._uid = next(_UIDS)
                self._on_tick(audio.level(block), compute_ms)
        finally:
            # Rend le périphérique tout de suite : sinon la trace de
            # l'exception garde le générateur, donc l'enregistreur WASAPI,
            # ouvert pendant toute l'attente du rebranchement.
            close = getattr(source, "close", None)
            if close is not None:
                close()
        raise RuntimeError("audio stream ended")

    def _wait_for_device(self) -> bool:
        """Vrai quand le périphérique est revenu, faux si on a demandé l'arrêt."""
        while not self._stop.wait(self.RETRY_S):
            try:
                found = audio.find_device(self._devices(), self.device.name, self.device.kind)
            except Exception:
                found = None
            if found is not None:
                self.device = found      # son id a pu changer
                return True
        return False


# --------------------------------------------------------------------------- #
# Réglages persistés
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Settings:
    device_name: str = ""
    device_kind: str = audio.KIND_LOOPBACK
    lang: str = "fr"
    precision: str = PRECISION_ACCURATE
    end_pause: float = 0.8


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    name = data.get("device_name")
    kind = data.get("device_kind")
    lang = data.get("lang")
    precision = data.get("precision")
    pause = data.get("end_pause")
    lo, hi = END_PAUSE_RANGE
    default = Settings()
    return Settings(
        device_name=name if isinstance(name, str) else default.device_name,
        device_kind=kind if kind in (audio.KIND_MIC, audio.KIND_LOOPBACK) else default.device_kind,
        lang=lang if isinstance(lang, str) and lang in MODELS else default.lang,
        precision=precision if precision in (PRECISION_FAST, PRECISION_ACCURATE)
        else default.precision,
        end_pause=round(float(pause), 1)
        if isinstance(pause, (int, float)) and not isinstance(pause, bool) and lo <= pause <= hi
        else default.end_pause)


def save_settings(settings: Settings, path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
