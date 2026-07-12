"""Tests du masquage (redaction) des logs de l'App comptoir.

Vérifie qu'aucun jeton/secret ni donnée patient sensible ne subsiste dans un
message de log, que la valeur soit :
- une valeur exacte enregistrée à l'exécution (register_secret) ;
- un champ sensible dans un dict/JSON logué par mégarde
  (token, secret, password, initials, call_number, name...).
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import my_logger  # noqa: E402
from my_logger import RedactingFilter, APP_NAME, default_log_dir  # noqa: E402


def _redact(text, filt=None):
    """Passe `text` dans le filtre et renvoie le message éventuellement masqué."""
    filt = filt or RedactingFilter()
    record = logging.LogRecord("t", logging.INFO, __file__, 1, text, None, None)
    filt.filter(record)
    return record.getMessage()


@pytest.mark.parametrize("field,secret", [
    ("token", "abc123def456ghi"),
    ("app_token", "abc123def456ghi"),
    ("X-App-Token", "abc123def456ghi"),
    ("app_secret", "monsecret"),
    ("password", "hunter2xx"),
    ("initials", "JD"),
    ("call_number", "A-42"),
    ("name", "Jean Dupont"),
    ("firstname", "Jean"),
    ("phone", "0612345678"),
])
def test_sensitive_json_fields_are_masked(field, secret):
    text = f'Réponse: {{"{field}": "{secret}", "id": 3}}'
    out = _redact(text)
    assert secret not in out
    assert "***" in out
    # Un champ non sensible reste visible.
    assert '"id": 3' in out


def test_field_equals_form_is_masked():
    out = _redact("headers app_token=abc123def456 counter=1")
    assert "abc123def456" not in out
    assert "counter=1" in out


def test_registered_secret_is_masked_anywhere():
    filt = RedactingFilter()
    filt.register_secret("SUPER-TOKEN-XYZ")
    out = _redact("Token obtenu : SUPER-TOKEN-XYZ (installé)", filt)
    assert "SUPER-TOKEN-XYZ" not in out
    assert "***" in out


def test_bearer_header_is_masked():
    out = _redact("Authorization: Bearer eyJhbGciOiJI.payload.sig")
    assert "eyJhbGciOiJI.payload.sig" not in out


def test_non_sensitive_text_is_untouched():
    text = "Connexion réussie au comptoir 3 (revision 118)"
    assert _redact(text) == text


def test_module_level_register_secret_shared_filter():
    my_logger.register_secret("SHARED-SECRET-123")
    out = _redact("valeur=SHARED-SECRET-123", my_logger._redacting_filter)
    assert "SHARED-SECRET-123" not in out


def test_default_log_dir_uses_localappdata_on_windows(monkeypatch):
    monkeypatch.setattr(my_logger.platform, "system", lambda: "Windows")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")
    path = default_log_dir()
    assert APP_NAME in str(path)
    assert str(path).endswith("logs")


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


def test_child_logger_propagates_and_is_redacted():
    """Un logger enfant 'appcomptoir.<module>' propage vers un handler portant le
    filtre de masquage : le secret enregistré et les champs patient sont caviardés
    (câblage réel : propagation + redaction au niveau handler)."""
    parent = logging.getLogger("appcomptoir")
    handler = _Capture()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(RedactingFilter())
    parent.addHandler(handler)
    parent.setLevel(logging.DEBUG)
    try:
        child = logging.getLogger("appcomptoir.mymodule")
        child.info('patient {"call_number": "A-42", "initials": "ZZ"}')
        joined = "\n".join(handler.lines)
        assert "A-42" not in joined
        assert '"initials": "ZZ"' not in joined
        assert "***" in joined
    finally:
        parent.removeHandler(handler)
