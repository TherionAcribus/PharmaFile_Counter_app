"""Rendu HTML du fil de messagerie (pur : ni Qt ni réseau, testable seul).

Sorti de ``MessagingController._render_messages`` : la transformation
« liste de messages → HTML » ne dépend que des messages et de quelques
repères calculés par le contrôleur (qui suis-je, où poser le repère
« Nouveaux messages », quels accusés afficher). Le contrôleur garde la
partie Qt : affichage dans le fil et position de l'ascenseur.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime

#: Ancre posée sur chaque bulle du fil : retrouve le message sous un clic
#: droit (une bulle = un bloc du document, cf. _message_in_block).
MESSAGE_ANCHOR = "msg-"

#: Token de mention « @XX » dans un corps de message : initiales métier
#: (1 à 10 caractères de mot), match insensible à la casse — la même
#: forme que l'extraction côté serveur.
MENTION_PATTERN = re.compile(r"@(\w{1,10})\b", re.IGNORECASE)

EMPTY_THREAD_HTML = "<p>Aucun message aujourd’hui.</p>"

_WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi",
             "dimanche")
_MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre")


def normalize(text):
    """Forme de comparaison tolérante : minuscules, accents retirés — un
    « hel » tapé retrouve « Hélène »."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)).lower()


def day_label(day, today):
    """Libellé d'un séparateur de date du fil, en français et sans dépendre
    de la langue du système : « Aujourd'hui », « Hier », « Jeudi 29
    octobre » (année en cours), « 29 octobre 2025 » (année passée)."""
    delta = (today - day).days
    if delta == 0:
        return "Aujourd'hui"
    if delta == 1:
        return "Hier"
    if day.year == today.year:
        return (f"{_WEEKDAYS[day.weekday()].capitalize()} {day.day} "
                f"{_MONTHS[day.month - 1]}")
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def message_day(value):
    """Jour LOCAL d'un horodatage ISO (None s'il est illisible)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return (parsed.astimezone() if parsed.tzinfo else parsed).date()


def format_time(value):
    """Heure locale « HH:MM » d'un horodatage ISO (vide s'il est illisible)."""
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone().strftime("%H:%M") if parsed.tzinfo else parsed.strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def highlight_mention(match, known_initials, self_initials):
    """Mise en valeur d'un token « @XX » si c'est une initiale connue :
    fond orange quand il s'agit de la mienne — elle doit sauter aux
    yeux dans un fil d'équipe — simple gras orange pour les autres."""
    token = normalize(match.group(1))
    if token not in known_initials:
        return match.group(0)
    if token == normalize(self_initials or ""):
        return (f'<b style="color:#fff;background:#b45309">'
                f'{match.group(0)}</b>')
    return f'<b style="color:#b45309">{match.group(0)}</b>'


def render_thread_html(messages, *, my_staff_id, today, marker_id=None,
                       read_id=None, delivered_id=None, known_initials=(),
                       self_initials=None):
    """HTML du fil ``messages`` (du plus ancien au plus récent).

    - ``my_staff_id`` : mes bulles à droite, « Vous » ;
    - ``today`` : jour local (séparateurs de date) ;
    - ``marker_id`` : message devant lequel poser « Nouveaux messages » ;
    - ``read_id`` / ``delivered_id`` : où afficher « Lu à HH:MM » / « Remis »
      (une seule fois chacun) ;
    - ``known_initials`` / ``self_initials`` : mentions « @XX » à mettre en
      valeur (initiales normalisées).
    """
    blocks = []
    separator_done = False
    # Expéditeur de la bulle précédente (staff_id + kind) : les messages
    # consécutifs du même expéditeur se regroupent — le nom n'est répété
    # que sur la première et les suivantes se rapprochent (marge 2 px).
    previous_bubble = None
    # Séparateurs de date : seule l'heure figure sur les bulles. Un
    # séparateur à chaque changement de jour ; en tête du fil seulement
    # si ce n'est pas aujourd'hui (un fil du jour reste épuré).
    previous_day = today

    def _highlight(match):
        return highlight_mention(match, known_initials, self_initials)

    for message in messages:
        day = message_day(message.get("created_at"))
        if day is not None and day != previous_day:
            blocks.append(
                '<div style="text-align:center; color:#6b7280; '
                'font-size:small; margin:8px 0 4px">'
                f'<b>{html.escape(day_label(day, today))}</b></div>')
            previous_bubble = None   # nom réaffiché après le séparateur
        if day is not None:
            previous_day = day
        sender = message.get("sender") or {}
        mine = sender.get("staff_id") == my_staff_id
        align = "right" if mine else "left"
        if message.get("deleted"):
            # Trace d'un message retiré (le serveur n'en envoie jamais
            # le contenu) : ligne grise discrète, du côté de son
            # expéditeur — le destinataire comprend pourquoi un
            # message vu, ou notifié, n'est plus là.
            who = ("Vous avez retiré un message" if mine else
                   "Message retiré par "
                   + html.escape(str(sender.get("name") or "son auteur")))
            timestamp = format_time(message.get("created_at"))
            blocks.append(
                f'<div style="text-align:{align}; margin:5px 0; '
                f'color:#6b7280"><i>{who}</i> <small>{timestamp}</small>'
                f'</div>')
            # La trace coupe le regroupement des bulles.
            previous_bubble = None
            continue
        color = "#dbeafe" if mine else "#f1f3f5"
        if message.get("kind") == "broadcast":
            color = "#fff3cd"
        urgent = bool(message.get("urgent"))
        if urgent:
            # Prioritaire : fond rouge pâle et « ❗ » devant le nom —
            # repérable quelle que soit la bulle (direct, équipe,
            # sortante).
            color = "#fee2e2"
        name = "Vous" if mine else html.escape(str(sender.get("name") or "Inconnu"))
        if urgent:
            name = "❗ " + name
        timestamp = format_time(message.get("created_at"))
        body = html.escape(str(message.get("body") or "")).replace("\n", "<br>")
        # Mentions « @initiales » sur le corps DÉJÀ échappé (@ et
        # lettres ne sont jamais réécrits par html.escape) : seules les
        # initiales connues sont mises en valeur, un « @xx » quelconque
        # reste du texte ordinaire.
        body = MENTION_PATTERN.sub(_highlight, body)
        # Accusés en gris discret, une seule fois (cf. _receipt_targets) :
        # « Lu à HH:MM » sous mon dernier message lu, « Remis » sous mon
        # dernier message s'il n'est pas encore lu.
        receipt = ""
        message_id = message.get("id")
        if message_id is not None and message_id == read_id:
            read_time = format_time(message.get("read_at"))
            label = f"Lu à {read_time}" if read_time else "Lu"
            receipt = f' · <span style="color:#6b7280">{label}</span>'
        elif message_id is not None and message_id == delivered_id:
            receipt = ' · <span style="color:#6b7280">Remis</span>'
        # Repère visuel posé juste avant le message mémorisé (cf.
        # _update_unread_marker) — uniquement si des messages le
        # précèdent : un fil 100 % non lu se suffit. Il RESTE après
        # l'accusé « Lu », jusqu'au changement de conversation : on
        # garde le fil de sa lecture même après une réponse.
        if (marker_id is not None and message_id == marker_id
                and not separator_done):
            separator_done = True
            if blocks:
                blocks.append(
                    '<div style="text-align:center; color:#6b7280; '
                    'font-size:small; margin:6px 0">'
                    '—— Nouveaux messages ——</div>')
                # Le repère coupe le regroupement : la bulle qui le
                # suit réaffiche le nom de l'expéditeur.
                previous_bubble = None
        staff = sender.get("staff_id")
        bubble_key = ((staff, message.get("kind"))
                      if staff is not None else None)
        grouped = bubble_key is not None and bubble_key == previous_bubble
        margin = "2px 0 5px" if grouped else "5px 0"
        header = "" if grouped else f"<b>{name}</b> "
        content = f'{header}<small>{timestamp}{receipt}</small><br>{body}'
        if message_id is not None:
            # Ancre sans lien (aucun style, aucun clic) : sert seulement
            # à retrouver le message sous un clic droit.
            anchor = html.escape(f"{MESSAGE_ANCHOR}{message_id}")
            content = f'<a name="{anchor}">{content}</a>'
        blocks.append(
            f'<div style="text-align:{align}; margin:{margin}">'
            f'<span style="background:{color}; padding:6px">'
            f'{content}</span></div>'
        )
        previous_bubble = bubble_key
    return "".join(blocks) or EMPTY_THREAD_HTML
