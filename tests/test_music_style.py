"""Apparence de l'overlay : persistance, valeurs sûres, rendus.

Deux garanties comptent ici. D'abord qu'enregistrer un style n'efface pas le
jeton, qui vit dans le même fichier : le perdre changerait toutes les URL, donc
casserait les sources navigateur déjà configurées dans OBS. Ensuite qu'aucune
valeur douteuse ne ressorte du fichier, puisqu'elle finirait dans du CSS.
"""
from __future__ import annotations

import io
import json

import pytest

from PIL import Image

from dataclasses import replace

from itertools import product

from music_style import (CARD_H, CARD_W, COVER_KEYS, COVERS, GEOMETRIE,
                         LAYOUT_KEYS, LAYOUTS, TEMPLATES, Style,
                         StyleStore, cover_preview_png, css_variables,
                         is_light, layout_guide_png, max_overlay_size,
                         max_overlay_size_for,
                         overlay_size, preview_png, template)


@pytest.fixture
def store(tmp_path) -> StyleStore:
    chemin = tmp_path / "music_widget.json"
    chemin.write_text(json.dumps({"overlay_token": "JETON-EXISTANT"}),
                      encoding="utf-8")
    return StyleStore(chemin, backgrounds=tmp_path / "fonds")


# ----------------------------------------------------------------------------
# Persistance
# ----------------------------------------------------------------------------
def test_une_source_jamais_reglee_prend_le_style_par_defaut(store):
    assert store.get("spotify") == Style()


def test_un_style_enregistre_se_relit(store):
    store.save("spotify", Style(bg="#123456", border="#ABCDEF", opacity=40,
                                template="neon"))

    relu = store.get("spotify")

    assert relu.bg == "#123456"
    assert relu.border == "#ABCDEF"
    assert relu.opacity == 40
    assert relu.template == "neon"


def test_enregistrer_un_style_ne_perd_pas_le_jeton(store, tmp_path):
    # Le jeton vit dans le même document. L'écraser changerait toutes les URL
    # d'overlay, donc casserait les sources déjà configurées dans OBS.
    store.save("spotify", Style(opacity=10))

    data = json.loads((tmp_path / "music_widget.json").read_text(encoding="utf-8"))

    assert data["overlay_token"] == "JETON-EXISTANT"
    assert data["styles"]["spotify"]["opacity"] == 10


def test_deux_sources_ont_des_reglages_independants(store):
    store.save("spotify", Style(bg="#111111"))
    store.save("deezer", Style(bg="#222222"))

    assert store.get("spotify").bg == "#111111"
    assert store.get("deezer").bg == "#222222"


def test_la_reinitialisation_rend_le_style_par_defaut(store):
    store.save("spotify", Style(bg="#123456", opacity=5))

    store.reset("spotify")

    assert store.get("spotify") == Style()
    assert "spotify" not in store.all_keys()


def test_un_fichier_illisible_ne_fait_pas_tomber_la_lecture(tmp_path):
    chemin = tmp_path / "music_widget.json"
    chemin.write_text("ceci n'est pas du json", encoding="utf-8")

    assert StyleStore(chemin, backgrounds=tmp_path / "f").get("spotify") == Style()


# ----------------------------------------------------------------------------
# Valeurs sûres
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("couleur", ["rouge", "", "#12345", "#GGGGGG",
                                     "red; background:url(x)", None, 42])
def test_une_couleur_invalide_retombe_sur_le_defaut(couleur):
    # Ces valeurs partent dans du CSS : les laisser passer telles quelles
    # ouvrirait une injection depuis un fichier modifié à la main.
    assert Style(bg=couleur).sanitised().bg == "#0F0C1B"
    assert Style(border=couleur).sanitised().border == "#A855F7"


@pytest.mark.parametrize("valeur,attendu", [(-30, 0), (0, 0), (55, 55),
                                            (100, 100), (500, 100)])
def test_lopacite_reste_entre_0_et_100(valeur, attendu):
    assert Style(opacity=valeur).sanitised().opacity == attendu


def test_une_opacite_non_numerique_retombe_sur_le_defaut():
    assert Style(opacity="beaucoup").sanitised().opacity == 82


def test_un_champ_inconnu_dans_le_fichier_est_ignore(store, tmp_path):
    # Le fichier est éditable à la main : une clé en trop ne doit pas faire
    # tomber le chargement de toute l'apparence.
    chemin = tmp_path / "music_widget.json"
    data = json.loads(chemin.read_text(encoding="utf-8"))
    data["styles"] = {"spotify": {"bg": "#101010", "inconnu": "x"}}
    chemin.write_text(json.dumps(data), encoding="utf-8")

    assert store.get("spotify").bg == "#101010"


def test_la_conversion_rgba_suit_lopacite():
    assert Style(bg="#FFFFFF", opacity=100).rgba == (255, 255, 255, 255)
    assert Style(bg="#000000", opacity=0).rgba == (0, 0, 0, 0)
    assert Style(bg="#0F0C1B", opacity=50).rgba[3] == 128


# ----------------------------------------------------------------------------
# Templates
# ----------------------------------------------------------------------------
def test_chaque_template_a_une_cle_unique_et_un_libelle():
    cles = [tpl.key for tpl in TEMPLATES]
    assert len(cles) == len(set(cles))
    assert all(tpl.label for tpl in TEMPLATES)


def test_un_template_se_retrouve_par_sa_cle():
    for tpl in TEMPLATES:
        assert template(tpl.key) is tpl
    assert template("jamais-defini") is None


def test_chaque_template_porte_sa_propre_cle_dans_son_style():
    # Une retouche manuelle efface ce champ pour signaler que le style ne suit
    # plus de modèle ; un template qui annoncerait la clé d'un autre rendrait
    # ce repère faux.
    for tpl in TEMPLATES:
        assert tpl.style.template == tpl.key


def test_le_template_sans_cadre_na_ni_contour_ni_fond():
    assert template("nu").style.border_on is False
    assert template("nu").style.opacity == 0


def test_le_template_clair_est_reconnu_comme_clair():
    # Décide de la couleur du texte dans l'aperçu : se tromper le rendrait
    # illisible, blanc sur blanc.
    assert is_light(template("clair").style) is True
    assert is_light(template("violet").style) is False


# ----------------------------------------------------------------------------
# Rendus
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("cle", [tpl.key for tpl in TEMPLATES])
def test_chaque_template_produit_son_apercu(cle):
    # L'aperçu se dessine à partir des valeurs du modèle : ajouter un template
    # ne demande aucune image à fournir ni à regénérer.
    png = preview_png(template(cle).style, width=200, height=64)

    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (200, 64)
        assert image.format == "PNG"


def test_lapercu_montre_limage_de_fond_quand_il_y_en_a_une(store, tmp_path):
    dessin = tmp_path / "dessin.png"
    Image.new("RGB", (CARD_W, CARD_H), (220, 30, 90)).save(dessin)
    store.set_background("spotify", dessin)

    png = preview_png(Style(), width=120, height=40,
                      background=store.background_path("spotify"))

    with Image.open(io.BytesIO(png)) as image:
        rouge, vert, bleu = image.convert("RGB").getpixel((60, 6))
    # Le rose du dessin domine le haut de la vignette, pas le violet du fond.
    assert rouge > 150 and rouge > bleu


def test_un_fond_illisible_ne_fait_pas_tomber_lapercu(tmp_path):
    faux = tmp_path / "pas-une-image.png"
    faux.write_text("du texte", encoding="utf-8")

    png = preview_png(Style(), width=100, height=40, background=faux)

    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (100, 40)


def test_le_gabarit_donne_la_taille_de_reference():
    # C'est la base sur laquelle l'utilisateur dessine : si elle ne
    # correspondait pas à la carte, son dessin tomberait à côté.
    with Image.open(io.BytesIO(layout_guide_png())) as image:
        assert image.size == (CARD_W, CARD_H)


# ----------------------------------------------------------------------------
# Image de fond
# ----------------------------------------------------------------------------
def test_limage_choisie_est_recopiee_dans_les_donnees(store, tmp_path):
    # Copier plutôt que pointer : un dessin rangé sur le bureau finit déplacé,
    # et l'overlay tomberait en panne en pleine diffusion.
    source = tmp_path / "ailleurs.png"
    Image.new("RGB", (40, 20), (10, 200, 10)).save(source)

    assert store.set_background("spotify", source) is True

    copie = store.background_path("spotify")
    assert copie is not None and copie.is_file()
    assert copie.parent != source.parent

    source.unlink()
    assert store.background_path("spotify") is not None


def test_le_style_signale_la_presence_dune_image(store, tmp_path):
    assert store.get("spotify").background_image is False

    source = tmp_path / "dessin.png"
    Image.new("RGB", (10, 10), (0, 0, 0)).save(source)
    store.set_background("spotify", source)

    assert store.get("spotify").background_image is True


def test_retirer_limage_revient_au_fond_uni(store, tmp_path):
    source = tmp_path / "dessin.png"
    Image.new("RGB", (10, 10), (0, 0, 0)).save(source)
    store.set_background("spotify", source)

    store.clear_background("spotify")

    assert store.background_path("spotify") is None
    assert store.get("spotify").background_image is False
    assert store.background_bytes("spotify") is None


def test_un_fichier_qui_nest_pas_une_image_est_refuse(store, tmp_path):
    faux = tmp_path / "document.png"
    faux.write_text("pas une image", encoding="utf-8")

    assert store.set_background("spotify", faux) is False
    assert store.background_path("spotify") is None


# ----------------------------------------------------------------------------
# Briques : disposition, pochette, elements optionnels
# ----------------------------------------------------------------------------
def test_les_options_de_forme_ont_des_cles_uniques():
    for options in (LAYOUTS, COVERS):
        cles = [opt.key for opt in options]
        assert len(cles) == len(set(cles))
        assert all(opt.label for opt in options)


def test_les_valeurs_par_defaut_existent_bien_dans_les_options():
    assert Style().layout in {opt.key for opt in LAYOUTS}
    assert Style().cover in {opt.key for opt in COVERS}


@pytest.mark.parametrize("champ,valeur", [("layout", "nimporte-quoi"),
                                          ("cover", "carre-rond"),
                                          ("layout", 42), ("cover", None)])
def test_une_forme_inconnue_retombe_sur_le_defaut(champ, valeur):
    # Ces valeurs deviennent un attribut CSS dans la page : en laisser passer
    # une inconnue donnerait une carte sans disposition du tout.
    nettoye = replace(Style(), **{champ: valeur}).sanitised()
    assert getattr(nettoye, champ) == getattr(Style(), champ)


@pytest.mark.parametrize("layout", [opt.key for opt in LAYOUTS])
@pytest.mark.parametrize("cover", [opt.key for opt in COVERS])
def test_chaque_combinaison_produit_une_vignette(layout, cover):
    # Cinq dispositions fois cinq pochettes : aucune combinaison ne doit
    # tomber, y compris les degenerees comme « minimal » sans pochette.
    png = preview_png(replace(Style(), layout=layout, cover=cover),
                      width=128, height=54)

    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (128, 54)


@pytest.mark.parametrize("champ", ["show_wave", "show_artist", "show_app"])
def test_masquer_une_brique_ne_casse_pas_la_vignette(champ):
    png = preview_png(replace(Style(), **{champ: False}), width=200, height=64)

    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (200, 64)


def test_les_briques_masquees_se_relisent(store):
    store.save("spotify", replace(Style(), show_wave=False, show_artist=False,
                                  show_app=False, layout="minimal",
                                  cover="aucune"))

    relu = store.get("spotify")

    assert relu.show_wave is False
    assert relu.show_artist is False
    assert relu.show_app is False
    assert relu.layout == "minimal"
    assert relu.cover == "aucune"


def test_un_titre_trop_long_est_coupe():
    """Regression : le texte sortait du cadre de la vignette.

    L overlay coupe le texte en CSS ; une vignette qui le laisserait deborder
    montrerait un rendu que la page ne produit jamais.
    """
    from PIL import ImageDraw
    from music_style import POINTS, _police_grasse, _tronque

    dessin = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    police = _police_grasse(12)
    long = "Un titre vraiment beaucoup trop long pour cette vignette"

    court = _tronque(dessin, long, police, 80)

    assert court.endswith(POINTS)
    assert dessin.textlength(court, font=police) <= 80


def test_un_texte_qui_tient_nest_pas_touche():
    from PIL import ImageDraw
    from music_style import _police, _tronque

    dessin = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    assert _tronque(dessin, "court", _police(12), 400) == "court"


# ----------------------------------------------------------------------------
# Vignettes de choix de pochette
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("forme", [opt.key for opt in COVERS])
def test_chaque_forme_de_pochette_a_sa_vignette(forme):
    png = cover_preview_png(Style(), forme, width=128, height=54)

    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (128, 54)
        assert image.format == "PNG"


def test_les_vignettes_de_pochette_sont_toutes_differentes():
    """Regression : « Auto » et « Vinyle » etaient identiques a l ecran.

    La grille montrait la carte entiere, ou la pochette n est qu un detail de
    coin : impossible de dire quelle option etait laquelle.
    """
    rendus = {opt.key: cover_preview_png(Style(), opt.key, 128, 54)
              for opt in COVERS}

    assert len(set(rendus.values())) == len(COVERS)


def test_la_vignette_de_pochette_suit_les_couleurs_reglees():
    # Elle se redessine quand on change le fond : sinon la grille resterait sur
    # l ancien habillage, donc sur un rendu faux.
    sombre = cover_preview_png(Style(bg="#101010"), "vinyle", 128, 54)
    clair = cover_preview_png(Style(bg="#F0F0F0"), "vinyle", 128, 54)

    assert sombre != clair


def test_une_forme_inconnue_ne_fait_pas_tomber_la_vignette():
    # La cle vient d une table, mais un fichier de reglages modifie a la main
    # peut en contenir une autre.
    with Image.open(io.BytesIO(
            cover_preview_png(Style(), "nimporte-quoi", 128, 54))) as image:
        assert image.size == (128, 54)


# ----------------------------------------------------------------------------
# Geometrie et taille de source
# ----------------------------------------------------------------------------
def test_toute_variable_css_de_la_page_existe_dans_la_geometrie():
    """Le garde-fou de tout le montage.

    La page et la taille annoncee lisent les MEMES constantes. Si quelqu un
    ajoute un `var(--truc)` au gabarit sans l ajouter a `GEOMETRIE`, la page
    tombe sur une valeur vide et le chiffre affiche devient faux. Ce test
    casse avant.
    """
    import re

    from music_overlay import _PAGE_HTML

    connues = {nom.replace("_", "-") for nom in GEOMETRIE}
    # Les variables de style (couleurs, accent) sont posees par le script, pas
    # par la geometrie : on ne verifie que celles qui portent une mesure.
    posees_par_js = {"fond", "contour", "accent", "large", "marque",
                     "encre", "attenue"}
    utilisees = set(re.findall(r"var\(--([a-z0-9-]+)", _PAGE_HTML))

    manquantes = utilisees - connues - posees_par_js
    assert not manquantes, manquantes


def test_le_bloc_css_declare_toutes_les_constantes():
    bloc = css_variables()
    for nom, valeur in GEOMETRIE.items():
        assert f"--{nom.replace('_', '-')}: {valeur}px;" in bloc


@pytest.mark.parametrize("layout", [opt.key for opt in LAYOUTS])
@pytest.mark.parametrize("cover", [opt.key for opt in COVERS])
def test_chaque_combinaison_annonce_une_taille_plausible(layout, cover):
    largeur, hauteur = overlay_size(replace(Style(), layout=layout,
                                            cover=cover))

    assert 200 < largeur <= 1400
    assert 80 < hauteur <= 800


BRIQUES = ("show_wave", "show_dots", "show_progress", "show_controls")


def _toutes_les_combinaisons():
    for layout in LAYOUT_KEYS:
        for cover in COVER_KEYS:
            for etats in product((False, True), repeat=len(BRIQUES)):
                yield replace(Style(), layout=layout, cover=cover,
                              **dict(zip(BRIQUES, etats)))


def test_la_taille_maximale_couvre_toutes_les_combinaisons():
    """Regression : le maximum ne variait que la disposition et la pochette.

    Les briques ajoutees ensuite (pastilles, progression, transport)
    agrandissent la carte. Le chiffre annonce a qui ne veut pas reflechir
    devenait donc trop petit, et la source rognait dans OBS.
    """
    large_max, haut_max = max_overlay_size()

    for style in _toutes_les_combinaisons():
        largeur, hauteur = overlay_size(style)
        assert largeur <= large_max and hauteur <= haut_max, style


def test_le_maximum_dune_disposition_couvre_ses_combinaisons():
    # Le tableau du README donne une taille par disposition.
    for layout in LAYOUT_KEYS:
        large_max, haut_max = max_overlay_size_for(layout)
        for style in _toutes_les_combinaisons():
            if style.layout != layout:
                continue
            largeur, hauteur = overlay_size(style)
            assert largeur <= large_max and hauteur <= haut_max, style


def test_chaque_modele_tient_dans_la_taille_annoncee():
    large_max, haut_max = max_overlay_size()

    for tpl in TEMPLATES:
        largeur, hauteur = overlay_size(tpl.style)
        assert largeur <= large_max and hauteur <= haut_max, tpl.key


def test_couper_une_brique_ne_grandit_jamais_la_carte():
    complet = overlay_size(Style())
    sans_onde = overlay_size(replace(Style(), show_wave=False))
    sans_pochette = overlay_size(replace(Style(), cover="aucune"))

    assert sans_onde[1] <= complet[1]
    assert sans_pochette[0] <= complet[0]


def test_une_pochette_large_elargit_la_carte():
    # « Large » impose un 16:9 plus large que le carre : la source doit suivre.
    carre = overlay_size(replace(Style(), cover="carre"))
    large = overlay_size(replace(Style(), cover="large"))

    assert large[0] > carre[0]


def test_les_deux_presets_macos_portent_leur_disposition():
    """Leur identite tient a l agencement autant qu aux couleurs.

    Pastilles de fenetre, pochette carree, boutons de transport et barre de
    progression font partie du dessin : les appliquer a moitie ne donnerait
    pas le modele.
    """
    for cle in ("macos_sombre", "macos_clair"):
        tpl = template(cle)
        assert tpl is not None and tpl.full is True
        assert tpl.style.show_dots and tpl.style.show_progress
        assert tpl.style.show_controls
        assert tpl.style.cover == "carre"


def test_un_fond_clair_impose_une_encre_sombre():
    """Regression : la page ecrivait son titre en blanc quoi qu il arrive.

    Le preset Clair affichait donc du blanc sur blanc, et macOS clair aurait
    fait pareil.
    """
    for cle in ("clair", "macos_clair"):
        assert template(cle).style.as_dict()["ink"] == "#1C1C1E"
    for cle in ("violet", "macos_sombre"):
        assert template(cle).style.as_dict()["ink"] == "#F3F0FA"


def test_les_briques_decoratives_agrandissent_la_source():
    nu = overlay_size(Style())
    avec = overlay_size(replace(Style(), show_dots=True, show_progress=True,
                                show_controls=True))

    assert avec[0] > nu[0]
    assert avec[1] > nu[1]


def test_les_vignettes_ont_les_coins_arrondis():
    """Le damier derriere la carte suivait un rectangle a angles droits.

    Dans la grille des modeles, chaque vignette montrait donc quatre equerres
    autour d une carte arrondie.
    """
    for png in (preview_png(Style(), width=200, height=64),
                cover_preview_png(Style(), "carre", 128, 54)):
        with Image.open(io.BytesIO(png)) as image:
            image = image.convert("RGBA")
            largeur, hauteur = image.size
            coins = [(0, 0), (largeur - 1, 0), (0, hauteur - 1),
                     (largeur - 1, hauteur - 1)]
            for coin in coins:
                assert image.getpixel(coin)[3] == 0, coin
            assert image.getpixel((largeur // 2, hauteur // 2))[3] == 255
