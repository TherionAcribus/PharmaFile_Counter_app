"""Configuration pytest commune aux tests Qt d'App_Comptoir.

Plusieurs modules de test instancient une application Qt (QCoreApplication pour
le réseau/WebSocket, QGuiApplication/QApplication pour le modèle de liste et les
widgets). Or un seul objet application peut exister par processus, et mélanger les
types (créer d'abord un QCoreApplication non graphique puis instancier un widget)
fait planter l'interpréteur.

On crée donc UNE seule QApplication (le type le plus complet : QApplication est un
QGuiApplication, lui-même un QCoreApplication) pour toute la session, avant tout
test. Les fixtures ``qapp`` des modules réutilisent alors cette instance via
``*.instance()`` sans en recréer une. Backend « offscreen » : pas besoin d'un
affichage réel (fonctionne aussi en CI headless).

En fin de session, on NE DÉTRUIT RIEN : des objets Qt créés par les tests sans
parent (lecteurs audio QMediaPlayer/QAudioOutput, widgets, timers…) restent dans
des cycles de références (signal -> méthode liée -> self) et leur destruction —
pendant une collecte du GC ou la finalisation de l'interpréteur, avec ou sans
QApplication — plante dans le backend multimédia FFmpeg en CI headless
(segfault APRÈS le dernier test). On désactive donc le GC cyclique pour toute la
session, on garde la QApplication en vie, et on quitte le processus via
``os._exit`` dans ``pytest_unconfigure`` — après l'affichage du résumé, en
conservant le code de sortie. Aucun destructeur Qt ne s'exécute jamais.
"""

import gc
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Cf. docstring : les cycles de références ne doivent jamais être collectés.
gc.disable()

_qapp = None        # garde la QApplication en vie jusqu'à os._exit
# Sentinelle non nulle : si pytest_unconfigure s'exécute sans que la session
# ait fini (erreur d'usage, plantage interne), on ne doit PAS sortir en 0.
_exit_status = 3    # ExitCode.INTERNAL_ERROR


@pytest.fixture(scope="session", autouse=True)
def _shared_qapplication():
    from PySide6.QtWidgets import QApplication

    global _qapp
    app = QApplication.instance() or QApplication([])
    _qapp = app
    yield app


def pytest_sessionfinish(session, exitstatus):
    global _exit_status
    _exit_status = exitstatus


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    """Dernier hook avant la fin du processus : le résumé des tests est déjà
    affiché, les autres plugins sont déconfigurés. On sort sans finaliser
    l'interpréteur pour qu'aucun destructeur Qt ne s'exécute (segfault CI)."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(_exit_status))
