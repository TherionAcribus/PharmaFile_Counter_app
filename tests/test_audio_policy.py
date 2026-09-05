"""Politique des sons rapprochés (point 3) — tests purs, sans Qt.

Le problème corrigé : un seul QMediaPlayer, donc toute nouvelle demande
remplaçait la source et coupait la lecture en cours (un « ding » de nouveau
patient tuait une alerte parlée). ``SoundScheduler`` arbitre à la place.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_policy import (  # noqa: E402
    BEEP,
    DROP,
    PLAY,
    PREEMPT,
    QUEUE,
    VOICE,
    SoundScheduler,
    priority,
)


class FakeClock:
    """Horloge pilotée : les délais se testent sans attendre."""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def scheduler(clock):
    return SoundScheduler(clock=clock)


# --- barème de priorité -----------------------------------------------------

def test_le_ding_est_secondaire_les_alertes_sont_prioritaires():
    assert priority("ding") == BEEP
    assert priority("patient_taken") == VOICE
    assert priority("please_validate") == VOICE


def test_son_inconnu_traite_comme_une_alerte():
    """Mieux vaut faire attendre un son inattendu que le sacrifier."""
    assert priority("nouveau_son_du_serveur") == VOICE


# --- lecture immédiate ------------------------------------------------------

def test_rien_en_cours_le_son_part_tout_de_suite(scheduler):
    decision = scheduler.request("ding")
    assert decision.action == PLAY
    assert scheduler.current == "ding"


def test_apres_la_fin_le_son_suivant_repart(scheduler, clock):
    scheduler.request("ding")
    clock.advance(1.0)
    assert scheduler.finished().sound is None
    assert scheduler.current is None
    assert scheduler.request("ding").action == PLAY


# --- priorité aux alertes parlées ------------------------------------------

def test_une_alerte_coupe_un_ding_en_cours(scheduler):
    scheduler.request("ding")
    decision = scheduler.request("patient_taken")
    assert decision.action == PREEMPT
    assert decision.replaced == "ding"
    assert scheduler.current == "patient_taken"


def test_un_ding_ne_coupe_jamais_une_alerte(scheduler):
    """Le défaut d'origine : le « ding » écrasait la phrase vocale en cours."""
    scheduler.request("please_validate")
    decision = scheduler.request("ding")
    assert decision.action == DROP
    assert scheduler.current == "please_validate"
    assert scheduler.pending == ()


def test_une_alerte_ne_coupe_pas_une_autre_alerte_elle_attend(scheduler):
    scheduler.request("patient_taken")
    decision = scheduler.request("please_validate")
    assert decision.action == QUEUE
    assert scheduler.current == "patient_taken"
    assert scheduler.pending == ("please_validate",)
    assert scheduler.finished().sound == "please_validate"


# --- regroupement des « ding » rapprochés -----------------------------------

def test_deux_ding_rapproches_sont_regroupes(scheduler, clock):
    scheduler.request("ding")
    clock.advance(0.1)
    scheduler.finished()                     # le premier « ding » s'est achevé
    assert scheduler.request("ding").action == DROP


def test_un_ding_plus_tardif_rejoue(scheduler, clock):
    scheduler.request("ding")
    scheduler.finished()
    clock.advance(1.0)
    assert scheduler.request("ding").action == PLAY


def test_une_rafale_de_ding_ne_donne_quun_seul_son(scheduler, clock):
    """Cinq patients coup sur coup : un seul « ding », pas cinq sons hachés."""
    actions = []
    for _ in range(5):
        actions.append(scheduler.request("ding").action)
        clock.advance(0.05)
    assert actions.count(PLAY) == 1
    assert scheduler.pending == ()


def test_les_alertes_ne_sont_pas_regroupees(scheduler, clock):
    """Aucun délai minimum sur les alertes parlées : elles comptent toutes."""
    scheduler.request("patient_taken")
    scheduler.finished()
    clock.advance(0.01)
    assert scheduler.request("patient_taken").action == PLAY


# --- file d'attente bornée --------------------------------------------------

def test_la_file_est_bornee(clock):
    scheduler = SoundScheduler(max_pending=2, clock=clock)
    scheduler.request("patient_taken")
    assert scheduler.request("please_validate").action == QUEUE
    assert scheduler.request("alerte_a").action == QUEUE
    assert scheduler.request("alerte_b").action == DROP
    assert scheduler.pending == ("please_validate", "alerte_a")


def test_pas_de_doublon_dans_la_file(scheduler):
    scheduler.request("patient_taken")
    scheduler.request("please_validate")
    assert scheduler.request("please_validate").action == DROP
    assert scheduler.pending == ("please_validate",)


def test_une_alerte_deja_en_cours_nest_pas_remise_en_file(scheduler):
    scheduler.request("patient_taken")
    assert scheduler.request("patient_taken").action == DROP
    assert scheduler.pending == ()


# --- alertes périmées -------------------------------------------------------

def test_une_alerte_trop_vieille_est_jetee(clock):
    """Une alerte qui commenterait une situation périmée ne doit pas sortir."""
    scheduler = SoundScheduler(max_age=5.0, clock=clock)
    scheduler.request("patient_taken")
    scheduler.request("please_validate")
    clock.advance(6.0)
    following = scheduler.finished()
    assert following.sound is None
    assert following.expired == ("please_validate",)
    assert scheduler.current is None


def test_seules_les_alertes_perimees_sont_jetees(clock):
    scheduler = SoundScheduler(max_age=5.0, clock=clock)
    scheduler.request("patient_taken")
    scheduler.request("please_validate")
    clock.advance(6.0)
    scheduler.request("alerte_recente")
    following = scheduler.finished()
    assert following.expired == ("please_validate",)
    assert following.sound == "alerte_recente"


# --- demande explicite (bouton « Tester un son ») ---------------------------

def test_force_passe_devant_tout(scheduler, clock):
    scheduler.request("please_validate")
    decision = scheduler.request("ding", force=True)
    assert decision.action == PREEMPT
    assert decision.replaced == "please_validate"
    assert scheduler.current == "ding"


def test_force_ignore_le_regroupement(scheduler, clock):
    """Deux clics rapprochés sur « Tester un son » : deux sons."""
    assert scheduler.request("ding", force=True).action == PLAY
    clock.advance(0.05)
    assert scheduler.request("ding", force=True).action == PREEMPT


def test_force_vide_la_file(scheduler):
    scheduler.request("patient_taken")
    scheduler.request("please_validate")
    scheduler.request("ding", force=True)
    assert scheduler.pending == ()
    assert scheduler.finished().sound is None


# --- remise à zéro ----------------------------------------------------------

def test_reset_oublie_tout(scheduler):
    scheduler.request("patient_taken")
    scheduler.request("please_validate")
    scheduler.reset()
    assert scheduler.current is None
    assert scheduler.pending == ()
    assert scheduler.request("ding").action == PLAY
