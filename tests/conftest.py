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
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def _shared_qapplication():
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app

    # Teardown déterministe : plusieurs tests créent des objets Qt sans parent
    # (lecteurs audio, widgets, timers) qui finissent dans des cycles de
    # références (signal -> méthode liée -> self). Sans collecte explicite, le
    # GC peut les détruire APRÈS la QApplication, dans un ordre indéfini — le
    # backend multimédia FFmpeg plante alors à la sortie du processus
    # (segfault en CI alors que tous les tests sont verts). On collecte donc
    # et on purge les suppressions différées TANT QUE l'application existe.
    import gc

    gc.collect()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
