"""Localisation des fichiers embarqués — images, sons, skins (point 10.4).

Un exécutable PyInstaller « onefile » décompresse ses données dans un dossier
temporaire dont le chemin est publié dans ``sys._MEIPASS`` : un chemin RELATIF
(« skins/Darkeum.qss ») n'y désigne rien. Il ne désigne rien non plus quand
l'application est lancée depuis un autre répertoire de travail (raccourci
Windows avec un « Démarrer dans » différent), y compris en développement.

Ce module est donc le SEUL endroit qui sait où trouver une ressource. Il évite
aussi deux effets de bord constatés :

* ``load_skin`` construisait « skins/<nom>.qss » en relatif : aucun skin ne
  s'appliquait dans un build onefile ;
* ``load_skins`` (préférences) faisait un ``os.makedirs("skins")`` : lancée
  depuis un autre dossier, l'application y créait un dossier « skins » vide.

Module pur (os/sys seulement) : testable sans Qt.
"""

import os
import sys

SKINS_DIRNAME = "skins"
SKIN_SUFFIX = ".qss"


def base_dir():
    """Racine des ressources : dossier temporaire PyInstaller si l'application
    est « gelée », sinon le dossier du code source (JAMAIS le répertoire de
    travail courant, qui dépend de la façon dont l'application est lancée)."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return bundled
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(relative_path):
    """Chemin absolu d'une ressource embarquée, décrite en style POSIX
    (« assets/images/pause.ico ») pour rester lisible côté appelant."""
    parts = [p for p in str(relative_path).replace("\\", "/").split("/") if p not in ("", ".")]
    return os.path.join(base_dir(), *parts)


def skins_dir():
    """Dossier des skins embarqués (peut ne pas exister)."""
    return os.path.join(base_dir(), SKINS_DIRNAME)


def is_valid_skin_name(name):
    """Un nom de skin vient des préférences (donc d'un fichier de configuration
    modifiable) : il ne doit désigner qu'un fichier du dossier des skins, jamais
    un chemin arbitraire."""
    if not name or not isinstance(name, str):
        return False
    if name != name.strip() or name in (".", ".."):
        return False
    return not any(c in name for c in ("/", "\\", ":", "\0"))


def skin_path(name):
    """Chemin absolu du fichier .qss d'un skin, ou None si le nom est invalide."""
    if not is_valid_skin_name(name):
        return None
    return os.path.join(skins_dir(), name + SKIN_SUFFIX)


def available_skins():
    """Noms des skins disponibles, triés. Liste vide si le dossier est absent —
    on ne le CRÉE pas : les skins sont livrés avec l'application, un dossier
    manquant est une anomalie d'installation, pas quelque chose à réparer en
    écrivant dans le répertoire courant."""
    directory = skins_dir()
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return sorted(os.path.splitext(n)[0] for n in names if n.endswith(SKIN_SUFFIX))


def read_skin(name):
    """Contenu QSS d'un skin, ou None si le nom est invalide, le fichier absent
    ou illisible. Lecture en UTF-8 explicite : sans encodage imposé, la feuille
    de style était décodée avec l'encodage local du poste (cp1252 en France),
    ce qui casse à la première ressource non ASCII."""
    path = skin_path(name)
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


#: Valeur enregistrée pour « aucun skin » (style Qt standard).
NO_SKIN = ""
#: Ancienne valeur enregistrée pour « aucun skin » : le libellé affiché du
#: sélecteur. Elle désignait un fichier « Pas de skin.qss » introuvable — le
#: skin précédent restait donc appliqué jusqu'au redémarrage.
LEGACY_NO_SKIN_LABEL = "Pas de skin"


def normalize_skin_name(value):
    """Nom de skin enregistré, ``NO_SKIN`` pour « aucun » (y compris l'ancien
    libellé et toute valeur non textuelle)."""
    if not isinstance(value, str):
        return NO_SKIN
    value = value.strip()
    return NO_SKIN if value == LEGACY_NO_SKIN_LABEL else value


#: Nom d'objet du widget central du mode comptoir (main_window_ui) : cible des
#: règles de ``PANEL_OVERRIDES_QSS``.
COUNTER_PANEL_OBJECT_NAME = "counterPanel"

#: Ajouté après chaque skin. Plusieurs skins imposent une largeur minimale à
#: TOUS les boutons (« QPushButton { min-width: 80px } ») : dans le panneau du
#: comptoir, cela empêchait l'interface de rétrécir et la faisait déborder des
#: petits écrans (mode horizontal surtout). Les dialogues gardent le style du
#: skin ; seul le panneau est concerné (sélecteur par nom d'objet, prioritaire
#: sur le sélecteur de type du skin).
PANEL_OVERRIDES_QSS = (
    "\n/* Ajouts de l'App : le panneau du comptoir doit pouvoir rétrécir. */\n"
    f"#{COUNTER_PANEL_OBJECT_NAME} QPushButton, "
    f"#{COUNTER_PANEL_OBJECT_NAME} QToolButton {{ min-width: 0px; }}\n"
)


def skin_stylesheet(name):
    """Feuille de style complète du skin ``name`` (fichier + ajouts de l'App),
    ``""`` pour « aucun », None si le fichier est introuvable ou illisible."""
    name = normalize_skin_name(name)
    if not name:
        return ""
    qss = read_skin(name)
    return None if qss is None else qss + PANEL_OVERRIDES_QSS


def apply_skin(name, window=None, app=None):
    """Applique le skin ``name`` à la fenêtre ET à toute l'application, ou retire
    tout skin si ``name`` désigne « aucun ».

    Point d'application UNIQUE (démarrage, enregistrement, aperçu et annulation
    des préférences) : appliquer l'aperçu à la seule fenêtre laissait les autres
    fenêtres (notifications, dialogues) sur l'ancien skin.

    Renvoie False — sans rien modifier — si le fichier du skin est introuvable ou
    illisible. ``window``/``app`` : tout objet exposant ``setStyleSheet``."""
    qss = skin_stylesheet(name)
    if qss is None:
        return False
    for target in (window, app):
        if target is not None:
            target.setStyleSheet(qss)
    return True
