"""Identité de l'application Qt et migration des réglages (point 10.5).

L'application s'annonçait encore avec les valeurs du squelette d'exemple ayant
servi de base au projet :

    app.setApplicationName("PySide6 Web Browser Example2")
    app.setOrganizationName("MyCompany2")
    app.setOrganizationDomain("mycompany.com")

Ce n'est pas cosmétique : ``QSettings()`` construit son emplacement à partir de
ces deux noms. Toute la configuration des postes (URL du serveur, comptoir,
raccourcis, notifications, géométrie de fenêtre…) vit donc sous
``HKCU/Software/MyCompany2/PySide6 Web Browser Example2``. Renommer sans plus
attendre reviendrait à faire redémarrer chaque poste avec une configuration
vierge — URL et comptoir perdus, application inutilisable tant qu'un humain n'a
pas ressaisi les préférences.

Ce module porte donc les DEUX faces du renommage :
  * la nouvelle identité (``apply_identity``) ;
  * la reprise, une fois pour toutes, des réglages de l'ancienne identité
    (``migrate_legacy_settings``), avant toute lecture de configuration.

La copie est volontairement conservatrice : elle n'écrase jamais une valeur déjà
présente à la nouvelle adresse, et l'ancienne clé de registre est laissée
INTACTE (retour arrière possible vers une version antérieure du client).
"""

import logging

logger = logging.getLogger(__name__)

#: Identité courante. ``QSettings()`` s'appuie dessus : la changer déplace la
#: configuration de tous les postes — ajouter alors l'ancienne valeur à
#: LEGACY_IDENTITIES plutôt que de la remplacer.
ORGANIZATION_NAME = "PharmaFile"
APPLICATION_NAME = "AppComptoir"

#: Identités historiques, de la plus récente à la plus ancienne. Chaque entrée
#: est un couple (organisation, application) tel qu'il a été utilisé par une
#: version précédente du client.
LEGACY_IDENTITIES = (
    ("MyCompany2", "PySide6 Web Browser Example2"),
)

#: Clé témoin écrite à la nouvelle adresse une fois la reprise faite. Empêche de
#: ressusciter des réglages que l'utilisateur aurait effacés depuis.
MIGRATION_MARKER_KEY = "settings_migrated_from"


def apply_identity(app):
    """Pose l'identité de l'application sur ``QApplication``.

    Le domaine d'organisation n'est PAS défini : l'ancienne valeur
    (« mycompany.com ») était un domaine fictif, et sous macOS un domaine
    l'emporte sur le nom d'organisation pour situer les réglages. Ne rien
    déclarer laisse Qt utiliser le nom d'organisation partout.
    """
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setApplicationName(APPLICATION_NAME)


def migrate_legacy_settings(target, sources):
    """Reprend les réglages d'une identité historique vers ``target``.

    ``target`` est le QSettings de l'identité courante ; ``sources`` une séquence
    de couples ``(libellé, settings)`` explorés dans l'ordre. Seuls
    ``value``/``setValue``/``allKeys``/``contains`` sont utilisés : n'importe
    quel objet équivalent convient en test.

    Retourne le libellé de la source reprise, ou ``None`` si rien n'a été fait
    (reprise déjà effectuée, ou aucune configuration héritée).
    """
    if target.value(MIGRATION_MARKER_KEY):
        return None
    for label, source in sources:
        keys = [k for k in source.allKeys() if k != MIGRATION_MARKER_KEY]
        if not keys:
            continue
        copied = 0
        for key in keys:
            # On n'écrase jamais une valeur déjà saisie à la nouvelle adresse.
            if target.contains(key):
                continue
            target.setValue(key, source.value(key))
            copied += 1
        target.setValue(MIGRATION_MARKER_KEY, label)
        logger.info("Réglages repris depuis l'identité héritée « %s » (%d clé(s)). "
                    "L'ancien emplacement est conservé.", label, copied)
        return label
    # Aucune configuration héritée : première installation. On pose quand même le
    # témoin pour ne pas réinterroger les anciens emplacements à chaque démarrage.
    target.setValue(MIGRATION_MARKER_KEY, "")
    return None


def legacy_sources(settings_factory):
    """Construit les ``(libellé, settings)`` des identités historiques.

    ``settings_factory`` est appelée avec ``(organisation, application)`` — en
    production ``QSettings``, un double en test.
    """
    return [(f"{org}/{app}", settings_factory(org, app))
            for org, app in LEGACY_IDENTITIES]
