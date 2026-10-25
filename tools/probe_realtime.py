"""Sonde temps réel : qu'émet VRAIMENT le serveur déployé ?

Écoute en parallèle les deux namespaces Socket.IO (``/socket_app_counter``,
celui de l'App, et ``/socket_update_patient``, celui de l'Admin) et relit la
révision serveur toutes les 5 s via ``/api/counter/<id>/state``.

À chaque action (ajout borne, ajout admin, appel du suivant...), on voit :
- si la révision serveur avance (la base a bien changé) ;
- si l'évènement arrive sur le namespace Admin ;
- si l'évènement arrive sur le namespace App — et avec quelle révision.

Usage (depuis App_Comptoir, avec le venv de l'App) :
    .venv/Scripts/python.exe tools/probe_realtime.py https://serveur [counter_id]
"""

import json
import sys
import threading
import time

import requests
import socketio

sys.path.insert(0, ".")
from PySide6.QtCore import QCoreApplication, QSettings  # noqa: E402

from app_identity import APPLICATION_NAME, ORGANIZATION_NAME  # noqa: E402
from counter_id_utils import coerce_counter_id  # noqa: E402
from secret_store import load_secret  # noqa: E402

t0 = time.monotonic()
lock = threading.Lock()


def log(tag, msg):
    with lock:
        print(f"[{time.monotonic() - t0:7.1f}s] {tag:<12} {msg}", flush=True)


qapp = QCoreApplication(sys.argv[:1])
qapp.setOrganizationName(ORGANIZATION_NAME)
qapp.setApplicationName(APPLICATION_NAME)
settings = QSettings()
base = (sys.argv[1] if len(sys.argv) > 1 else str(settings.value("web_url", ""))).rstrip("/")
counter_id = int(sys.argv[2]) if len(sys.argv) > 2 else coerce_counter_id(settings.value("counter_id", 1))
log("INFO", f"serveur={base} counter_id={counter_id}")
secret = load_secret(settings)
token = None
if secret:
    r = requests.post(f"{base}/api/get_app_token", data={"app_secret": secret}, timeout=10)
    token = r.json().get("token") if r.status_code == 200 else None
    log("AUTH", f"jeton: {'OK' if token else f'REFUSÉ ({r.status_code})'}")
else:
    log("AUTH", "aucun secret applicatif sur ce poste")
headers = {"X-App-Token": token} if token else {}


def listen(ns, event, tag):
    sio = socketio.Client(reconnection=True)

    @sio.on("connect", namespace=ns)
    def _c():
        log(tag, f"connecté ({ns})")

    @sio.on("disconnect", namespace=ns)
    def _d():
        log(tag, "DÉCONNECTÉ")

    @sio.on(event, namespace=ns)
    def _e(data):
        rev = data.get("revision") if isinstance(data, dict) else None
        payload = data.get("data") if isinstance(data, dict) else data
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                pass
        n = len(payload) if isinstance(payload, list) else "-"
        nums = [p.get("call_number") for p in payload] if isinstance(payload, list) else ""
        log(tag, f"{event} rev={rev} patients={n} {nums}")

    @sio.on("*", namespace=ns)
    def _any(ev, data):
        if ev != event:
            log(tag, f"(autre) {ev}")

    sio.connect(base, headers=headers, namespaces=[ns])
    return sio


app_sio = listen("/socket_app_counter", "update_patient_list", "APP")
admin_sio = listen("/socket_update_patient", "update", "ADMIN")


def poll_state():
    last = None
    while True:
        try:
            r = requests.get(f"{base}/api/counter/{counter_id}/state", headers=headers, timeout=10)
            if r.status_code == 200:
                s = r.json()
                rev = s.get("revision")
                nums = [p.get("call_number") for p in s.get("standing_list", [])]
                if rev != last:
                    log("HTTP/state", f"rev={rev} standing={nums}")
                    last = rev
            else:
                log("HTTP/state", f"statut {r.status_code}")
        except Exception as e:
            log("HTTP/state", f"erreur {e}")
        time.sleep(5)


threading.Thread(target=poll_state, daemon=True).start()
log("INFO", "Sonde active — faites vos actions (borne / admin / App). Ctrl+C pour quitter.")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    app_sio.disconnect()
    admin_sio.disconnect()
