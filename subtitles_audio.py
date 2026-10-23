"""Capture audio des sous-titres : micros et son du PC (loopback WASAPI).

`soundcard` ouvre aussi bien une entrée (micro) qu'une sortie en loopback :
c'est ce qui permet de ne sous-titrer QUE ce qui passe par un périphérique
précis — le casque où joue la vidéo, sans le micro, ou l'inverse.

Le son est demandé directement en mono 16 kHz, par blocs de 100 ms : c'est
le format du moteur, et WASAPI fait la conversion lui-même.

Un périphérique est retrouvé par son NOM et son type, jamais par son index :
les index changent dès qu'on branche ou débranche un casque.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional

import numpy as np

from app_paths import logger

try:
    import soundcard as _sc
    IMPORT_ERROR = ""
except Exception as exc:          # paquet absent, ou pas de pilote audio
    _sc = None
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

SAMPLE_RATE = 16_000
BLOCK = SAMPLE_RATE // 10           # 100 ms

KIND_MIC = "mic"
KIND_LOOPBACK = "loopback"


def available() -> bool:
    return _sc is not None


@dataclass(frozen=True)
class Device:
    name: str
    kind: str                     # KIND_MIC ou KIND_LOOPBACK
    id: str = ""


def list_devices() -> list[Device]:
    """Micros d'abord, puis sorties en loopback, chacun trié par nom."""
    if _sc is None:
        return []
    found = [Device(m.name, KIND_LOOPBACK if m.isloopback else KIND_MIC, m.id)
             for m in _sc.all_microphones(include_loopback=True)]
    return sorted(found, key=lambda d: (d.kind != KIND_MIC, d.name.lower()))


def default_input_name() -> str:
    """Nom du micro par défaut de Windows, "" si inconnu."""
    try:
        return _sc.default_microphone().name if _sc is not None else ""
    except Exception:
        return ""


def find_device(devices: list[Device], name: str, kind: str) -> Optional[Device]:
    return next((d for d in devices if d.name == name and d.kind == kind), None)


def level(block: np.ndarray) -> float:
    """Niveau RMS du bloc, ramené entre 0 et 1 pour un vu-mètre."""
    if block.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    return min(1.0, rms * 4.0)      # la parole tourne autour de 0,05 à 0,2 RMS


def blocks(device: Device) -> Iterator[np.ndarray]:
    """Blocs mono float32 de 100 ms, sans fin, jusqu'à ce que l'appelant
    arrête d'itérer. Lève une exception si le périphérique disparaît."""
    if _sc is None:
        raise RuntimeError(IMPORT_ERROR or "soundcard unavailable")
    mic = _sc.get_microphone(device.id, include_loopback=device.kind == KIND_LOOPBACK)
    logger.info("Sous-titres : capture ouverte sur %s (%s).", device.name, device.kind)
    with mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK) as rec:
        while True:
            yield rec.record(numframes=BLOCK)[:, 0]
