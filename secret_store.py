"""Chiffrement au repos des identifiants rangés dans `.env`.

Sous Windows, DPAPI (`CryptProtectData`) dérive sa clé du **compte
utilisateur**. Conséquences concrètes :

- le fichier `.env` copié sur une clé USB, envoyé par mail ou remonté dans
  une sauvegarde ne donne rien : sans la session Windows d'origine, le blob
  est indéchiffrable ;
- un autre compte sur la même machine ne peut pas le lire non plus ;
- nous n'avons **aucune clé à stocker**. Une clé en dur dans le code ou dans
  le `.exe` ne protégerait de rien, puisqu'elle serait livrée avec.

Ce que ça ne protège pas, et il faut le savoir : un programme qui tourne
**sous ta propre session** peut appeler `CryptUnprotectData` exactement comme
nous. C'est une limite de DPAPI, pas un défaut d'implémentation — sans un mot
de passe maître redemandé à chaque démarrage, aucun stockage local ne fait
mieux.

Hors Windows, les valeurs restent en clair : mieux vaut un fichier
manifestement en clair qu'un encodage qui **ressemble** à du chiffrement.

Format d'une valeur chiffrée :

    enc:v1:<base64 url-safe du blob DPAPI>

Le préfixe rend la migration transparente : une valeur sans préfixe est une
valeur héritée, lue telle quelle et rechiffrée au prochain enregistrement.
"""
from __future__ import annotations

import base64
import sys

from app_paths import logger

#: Préfixe des valeurs chiffrées. Versionné : changer d'algorithme un jour
#: demandera de lire l'ancien format sans casser les `.env` existants.
PREFIX = "enc:v1:"


def available() -> bool:
    """DPAPI est-il utilisable sur cette plateforme ?"""
    return sys.platform == "win32"


def is_encrypted(value: str) -> bool:
    return value.startswith(PREFIX)


def _blob_type():
    """Structure DATA_BLOB, construite à l'appel.

    Définie ici et pas au niveau module : `ctypes.wintypes` n'existe pas hors
    Windows, et l'importer à la racine ferait échouer l'import du module sur
    Linux/macOS — donc aussi les tests qui y tournent.
    """
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    return ctypes, _Blob


def _dpapi(fonction: str, data: bytes) -> bytes:
    """Appelle CryptProtectData ou CryptUnprotectData sur `data`."""
    ctypes, blob_type = _blob_type()
    tampon = ctypes.create_string_buffer(data, len(data))
    source = blob_type(len(data), ctypes.cast(tampon, ctypes.POINTER(ctypes.c_char)))
    resultat = blob_type()
    appel = getattr(ctypes.windll.crypt32, fonction)
    if not appel(ctypes.byref(source), None, None, None, None, 0,
                 ctypes.byref(resultat)):
        raise OSError(ctypes.GetLastError(), f"{fonction} a échoué")
    try:
        return ctypes.string_at(resultat.pbData, resultat.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(resultat.pbData)


def encrypt(value: str) -> str:
    """Valeur chiffrée prête à écrire dans `.env`.

    Une chaîne vide reste vide — chiffrer « rien » produirait un blob qui
    laisserait croire au lecteur qu'un mot de passe est configuré. Une valeur
    déjà chiffrée est rendue telle quelle, pour que rechiffrer deux fois soit
    sans effet.
    """
    if not value or is_encrypted(value) or not available():
        return value
    try:
        blob = _dpapi("CryptProtectData", value.encode("utf-8"))
    except (OSError, AttributeError):
        logger.exception("Chiffrement DPAPI impossible : la valeur reste en clair.")
        return value
    return PREFIX + base64.urlsafe_b64encode(blob).decode("ascii")


def decrypt(value: str) -> str:
    """Valeur en clair.

    Sans préfixe, c'est une valeur héritée d'avant le chiffrement : elle est
    rendue telle quelle, et `EnvConfigManager.save()` la chiffrera au premier
    enregistrement.

    Un blob illisible (fichier venu d'un autre compte ou d'une autre machine)
    rend **une chaîne vide**, jamais le blob : renvoyer le blob l'enverrait
    tel quel à OBS comme mot de passe, et le vrai motif de l'échec — « ce
    fichier n'est pas déchiffrable ici » — n'apparaîtrait nulle part.
    """
    if not value or not is_encrypted(value):
        return value
    charge = value[len(PREFIX):]
    try:
        blob = base64.urlsafe_b64decode(charge.encode("ascii"))
        return _dpapi("CryptUnprotectData", blob).decode("utf-8")
    except (OSError, ValueError, AttributeError, UnicodeDecodeError):
        logger.warning(
            "Un identifiant chiffré du .env n'a pas pu être déchiffré : il a "
            "été créé sous un autre compte Windows ou sur une autre machine. "
            "Ressaisis-le dans l'onglet Paramètres.")
        return ""
