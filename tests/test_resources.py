"""Localisation des ressources embarquées (point 10.4).

Régression visée : ``load_skin`` construisait un chemin RELATIF, donc aucun skin
ne s'appliquait dans un exécutable PyInstaller onefile (les données y sont
décompressées dans ``sys._MEIPASS``) ni quand l'application était lancée depuis
un autre répertoire de travail.
"""

import logging
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import resources  # noqa: E402

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- racine des ressources --------------------------------------------------

def test_base_dir_est_le_dossier_du_code_en_developpement():
    assert resources.base_dir() == APP_DIR


def test_base_dir_ne_depend_pas_du_repertoire_courant(tmp_path, monkeypatch):
    """Un raccourci Windows avec un « Démarrer dans » différent ne doit rien casser."""
    monkeypatch.chdir(tmp_path)
    assert resources.base_dir() == APP_DIR
    assert os.path.isabs(resources.resource_path("assets/images/pause.ico"))


def test_base_dir_suit_meipass_en_build_onefile(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resources.base_dir() == str(tmp_path)
    assert resources.skin_path("Darkeum") == os.path.join(str(tmp_path), "skins", "Darkeum.qss")


def test_resource_path_accepte_le_style_posix():
    expected = os.path.join(APP_DIR, "assets", "images", "pause.ico")
    assert resources.resource_path("assets/images/pause.ico") == expected
    assert resources.resource_path(r"assets\images\pause.ico") == expected


def test_ressources_reellement_presentes():
    """Les chemins produits doivent désigner de vrais fichiers livrés."""
    for relative in ("assets/images/pause.ico", "assets/images/next.ico",
                     "assets/sounds/ding.mp3"):
        assert os.path.exists(resources.resource_path(relative)), relative


# --- skins ------------------------------------------------------------------

def test_available_skins_liste_les_skins_livres():
    skins = resources.available_skins()
    assert "Darkeum" in skins
    assert skins == sorted(skins)
    assert all(not s.endswith(".qss") for s in skins)


def test_available_skins_ne_cree_aucun_dossier(tmp_path, monkeypatch):
    """L'ancienne version faisait os.makedirs("skins") dans le répertoire courant."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "absent"), raising=False)
    assert resources.available_skins() == []
    assert list(tmp_path.iterdir()) == []


def test_read_skin_lit_le_contenu():
    qss = resources.read_skin("Darkeum")
    assert qss and "QWidget" in qss


def test_read_skin_absent_retourne_none():
    assert resources.read_skin("SkinInexistant") is None


@pytest.mark.parametrize("name", [
    "../secret", r"..\secret", "skins/../../etc/passwd", r"C:\Windows\win",
    "", None, "..", ".", " Darkeum", 42,
])
def test_nom_de_skin_invalide_refuse(name):
    """Le nom vient des préférences : il ne doit pas pouvoir désigner un fichier
    hors du dossier des skins."""
    assert resources.skin_path(name) is None
    assert resources.read_skin(name) is None


def test_nom_de_skin_valide_reste_dans_le_dossier():
    path = resources.skin_path("Darkeum")
    assert os.path.dirname(path) == resources.skins_dir()


# --- application par MainWindow.load_skin ----------------------------------

class FakeApp:
    def __init__(self):
        self.stylesheet = None

    def setStyleSheet(self, qss):
        self.stylesheet = qss


class FakeWindow:
    def __init__(self, skin):
        import main
        self.selected_skin = skin
        self.stylesheet = None
        self.logger = logging.getLogger("test.resources")
        self.load_skin = types.MethodType(main.MainWindow.load_skin, self)

    def setStyleSheet(self, qss):
        self.stylesheet = qss


@pytest.fixture
def fake_app(monkeypatch):
    import main
    app = FakeApp()
    monkeypatch.setattr(main.QApplication, "instance", staticmethod(lambda: app))
    return app


def test_load_skin_applique_le_style(fake_app):
    w = FakeWindow("Darkeum")
    w.load_skin()
    assert w.stylesheet and "QWidget" in w.stylesheet
    assert fake_app.stylesheet == w.stylesheet


def test_load_skin_fonctionne_depuis_un_autre_repertoire(tmp_path, monkeypatch, fake_app):
    """Le cœur du point 10.4 : le chemin ne dépend plus du répertoire courant."""
    monkeypatch.chdir(tmp_path)
    w = FakeWindow("Darkeum")
    w.load_skin()
    assert w.stylesheet, "le skin doit s'appliquer même lancé depuis un autre dossier"


def test_load_skin_absent_conserve_le_style_par_defaut(fake_app, caplog):
    w = FakeWindow("SkinInexistant")
    with caplog.at_level(logging.WARNING, logger="test.resources"):
        w.load_skin()
    assert w.stylesheet is None
    assert fake_app.stylesheet is None
    assert any("SkinInexistant" in r.getMessage() for r in caplog.records)


def test_load_skin_sans_skin_ne_touche_a_rien(fake_app):
    w = FakeWindow("")
    w.load_skin()
    assert w.stylesheet is None and fake_app.stylesheet is None


# --- cliquet : aucun chemin de ressource relatif ---------------------------

def test_aucun_chemin_de_ressource_relatif():
    """Toute ressource embarquée doit passer par resources/resource_path."""
    faults = []
    for name in ("main.py", "preferences.py", "notification.py", "buttons.py"):
        with open(os.path.join(APP_DIR, name), encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                code = line.split("#", 1)[0]
                if '"skins"' in code or "'skins'" in code or 'os.path.join("skins"' in code:
                    faults.append(f"{name}:{lineno}")
    assert not faults, "dossier « skins » référencé en relatif : " + ", ".join(faults)
