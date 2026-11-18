# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

"""The protections around logins and secrets."""

import json
import ssl
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from usos_planner.config import Config, MailConfig, UsosConfig, config_home, data_home, on_windows_drive, write_private
from usos_planner.web.app import create_app

BASE = "http://127.0.0.1:8000"
CLIENT_JSON = {"installed": {"client_id": "abc.apps.googleusercontent.com", "client_secret": "GSECRET-3",
                             "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                             "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}}


def make(tmp_path, base_url=BASE, **cfg_changes):
    cfg = Config(path=tmp_path / "config.yaml", data_dir=tmp_path)
    for k, v in cfg_changes.items():
        setattr(cfg, k, v)
    return cfg, TestClient(create_app(cfg), base_url=base_url)


# ----- the GUI refuses other websites ---------------------------------------
def test_foreign_host_header_is_refused(tmp_path):
    """DNS rebinding: the attacker's domain shows up in the Host header."""
    _, c = make(tmp_path, base_url="http://evil.example:8000")
    assert c.get("/settings").status_code == 400
    assert c.get("/api/state.json").status_code == 400
    _, ok = make(tmp_path, base_url="http://localhost:8000")
    assert ok.get("/").status_code == 200


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "null"},
    {"Origin": "http://127.0.0.1:3000"},  # another local app on a different port
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site", "Origin": "http://localhost:8000"},
])
def test_cross_site_posts_are_refused(tmp_path, headers):
    cfg, c = make(tmp_path)
    r = c.post("/settings/mail", data={"method": "smtp", "smtp_host": "evil.example"}, headers=headers)
    assert r.status_code == 403
    assert cfg.mail.smtp_host == "smtp.gmail.com"  # nothing changed


def test_same_origin_posts_and_plain_clients_pass(tmp_path):
    cfg, c = make(tmp_path)
    r = c.post("/settings/calendar", data={"name": "A"}, headers={"Origin": BASE, "Sec-Fetch-Site": "same-origin"},
               follow_redirects=False)
    assert r.status_code == 303 and cfg.calendar.name == "A"
    r = c.post("/settings/calendar", data={"name": "B"}, follow_redirects=False)  # curl-like, no browser headers
    assert r.status_code == 303 and cfg.calendar.name == "B"


def test_logout_links_cannot_be_triggered_from_another_site(tmp_path):
    _, c = make(tmp_path)
    assert c.get("/auth/google/logout", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.get("/auth/google/logout", headers={"Sec-Fetch-Site": "same-origin"}, follow_redirects=False).status_code == 303
    # the OAuth callbacks are reached by redirects from USOS/Google and must stay open
    assert c.get("/auth/google/callback?error=access_denied", headers={"Sec-Fetch-Site": "cross-site"},
                 follow_redirects=False).status_code == 303


# ----- secrets never go back to the browser ---------------------------------
def test_settings_page_does_not_contain_secrets(tmp_path):
    (tmp_path / "google_credentials.json").write_text(json.dumps(CLIENT_JSON))
    _, c = make(tmp_path, usos=UsosConfig(consumer_key="KEY-1", consumer_secret="USOSSECRET-1"),
                mail=MailConfig(method="smtp", username="me@x.pl", password="MAILSECRET-2"))
    html = c.get("/settings").text
    for secret in ("USOSSECRET-1", "MAILSECRET-2", "GSECRET-3"):
        assert secret not in html
    assert "abc.apps.googleusercontent.com" in html and "KEY-1" in html
    assert "zapisane (puste = bez zmian)" in html and "usuń zapisane hasło" in html


def test_empty_secret_fields_keep_the_saved_value_and_clearing_works(tmp_path):
    cfg, c = make(tmp_path, usos=UsosConfig(consumer_key="K", consumer_secret="S1"),
                  mail=MailConfig(method="smtp", username="u", password="P1"))
    c.post("/settings/usos", data={"base_url": "https://usosapps.uw.edu.pl/", "consumer_key": "K", "consumer_secret": ""})
    assert cfg.usos.consumer_secret == "S1"
    c.post("/settings/mail", data={"method": "smtp", "smtp_host": "smtp.x.pl", "username": "u", "password": ""})
    assert cfg.mail.password == "P1" and cfg.mail.smtp_host == "smtp.x.pl"
    c.post("/settings/mail", data={"method": "gmail", "username": "u", "password": "", "clear_password": "1"})
    assert cfg.mail.password == "" and cfg.mail.method == "gmail"
    assert "P1" not in (tmp_path / "config.yaml").read_text()


def test_usos_address_must_be_https(tmp_path):
    from usos_planner.usos.client import UsosClient, UsosError

    cfg, c = make(tmp_path)
    r = c.post("/settings/usos", data={"base_url": "http://usosapps.uw.edu.pl/", "consumer_key": "K", "consumer_secret": "S"},
               follow_redirects=True)
    assert "https://" in r.text and cfg.usos.base_url.startswith("https://")
    with pytest.raises(UsosError):
        UsosClient(UsosConfig(base_url="http://usos.example/", consumer_key="K", consumer_secret="S"), tmp_path / "t.json")


# ----- files: private locations and modes -----------------------------------
def test_default_locations_are_private_dirs_outside_the_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no legacy config.yaml in the current directory
    cfg = Config.load()
    assert cfg.path == config_home() / "config.yaml" and cfg.data_dir == data_home()
    assert cfg.data_dir.stat().st_mode & 0o777 == 0o700
    cfg.usos.consumer_secret = "S"
    cfg.save()
    assert cfg.path.stat().st_mode & 0o777 == 0o600 and cfg.path.parent.stat().st_mode & 0o777 == 0o700
    assert cfg.google_client_path() == config_home() / "google_credentials.json"


def test_legacy_config_in_current_directory_is_still_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("calendar:\n  name: Stary\ndata_dir: dane\n")
    cfg = Config.load()
    assert cfg.path == Path("config.yaml") and cfg.calendar.name == "Stary"
    assert cfg.data_dir == Path("dane") and cfg.google_client_path() == Path("google_credentials.json")


def test_write_private_tightens_an_existing_file(tmp_path):
    f = tmp_path / "token.json"
    f.write_text("old")
    f.chmod(0o644)
    write_private(f, "new")
    assert f.read_text() == "new" and f.stat().st_mode & 0o777 == 0o600


def test_windows_drive_detection():
    assert on_windows_drive(Path("/mnt/c/Users/x/config.yaml"))
    assert not on_windows_drive(Path("/home/x/.config/usos-planner/config.yaml"))
    assert not on_windows_drive(Path("/mnt/data/x"))  # a Linux mount point, not a drive letter


# ----- mail ---------------------------------------------------------------
def test_smtp_verifies_the_server_certificate(tmp_path, monkeypatch):
    from usos_planner.escalation import mailer

    seen = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            seen["host"] = host
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def starttls(self, context=None):
            seen["ctx"] = context
        def login(self, u, p):
            seen["login"] = u
        def send_message(self, msg, to_addrs):
            seen["to"] = to_addrs

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    cfg = MailConfig(method="smtp", smtp_host="smtp.x.pl", username="me@x.pl", password="app", bcc_self=False)
    mailer.send(cfg, "a@x.pl", "s", "b", archive_dir=tmp_path / "sent")
    ctx = seen["ctx"]
    assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert seen["to"] == ["a@x.pl"]
    (eml,) = (tmp_path / "sent").iterdir()
    assert eml.stat().st_mode & 0o777 == 0o600


def test_gmail_sending_needs_login_and_sends_the_raw_message(tmp_path, monkeypatch):
    import base64
    import email

    from usos_planner.calendar import google_sync
    from usos_planner.escalation import mailer

    cfg = MailConfig(method="gmail", from_address="")
    with pytest.raises(mailer.MailError, match="zalogowania przez Google"):
        mailer.send(cfg, "a@x.pl", "Prośba", "Treść", google_creds=None)

    sent = {}

    class FakeGmail:
        def users(self):
            return self
        def messages(self):
            return self
        def send(self, userId, body):
            sent.update(userId=userId, raw=body["raw"])
            return type("X", (), {"execute": lambda _s: {"id": "1"}})()

    monkeypatch.setattr(google_sync, "gmail_service", lambda creds: FakeGmail())
    mailer.send(cfg, "a@x.pl", "Prośba", "Treść", cc=["b@x.pl"], google_creds=object())
    msg = email.message_from_bytes(base64.urlsafe_b64decode(sent["raw"]))
    assert sent["userId"] == "me" and msg["To"] == "a@x.pl" and msg["Cc"] == "b@x.pl" and msg["From"] is None


def test_gmail_disabled_api_is_explained():
    from usos_planner.calendar.google_sync import explain_api_error

    text = explain_api_error(Exception('returned "Gmail API has not been used in project 42 before or it is disabled."'))
    assert text.startswith("Gmail API nie jest włączone") and "gmail.googleapis.com/overview?project=42" in text


# ----- Google token: narrow scopes, old broad token revoked -----------------
def test_legacy_full_calendar_token_is_revoked_and_deleted(tmp_path, monkeypatch):
    from usos_planner.calendar import google_sync

    revoked = []
    monkeypatch.setattr(google_sync, "revoke_token", lambda t: revoked.append(t) or True)
    monkeypatch.setattr(google_sync, "load_credentials", lambda f, refresh=True: None)
    token = tmp_path / "google_token.json"
    token.write_text(json.dumps({"token": "A", "refresh_token": "R", "scopes": [google_sync.LEGACY_SCOPE]}))
    assert google_sync.migrate_legacy_token(token, tmp_path / "cal.json") is True
    assert not token.exists() and revoked == ["R"]

    token.write_text(json.dumps({"token": "A", "refresh_token": "R2", "scopes": google_sync.SCOPES}))
    assert google_sync.migrate_legacy_token(token, tmp_path / "cal.json") is False
    assert token.exists() and revoked == ["R"]


def test_gui_start_migrates_legacy_token_and_logout_revokes(tmp_path, monkeypatch):
    from usos_planner.calendar import google_sync

    revoked = []
    monkeypatch.setattr(google_sync, "revoke_token", lambda t: revoked.append(t) or True)
    monkeypatch.setattr(google_sync, "load_credentials", lambda f, refresh=True: None)
    (tmp_path / "google_token.json").write_text(json.dumps({"token": "A", "refresh_token": "OLD", "scopes": [google_sync.LEGACY_SCOPE]}))
    _, c = make(tmp_path)
    assert revoked == ["OLD"] and "Zaloguj się przez Google ponownie" in c.get("/").text

    (tmp_path / "google_token.json").write_text(json.dumps({"token": "A", "refresh_token": "NEW", "scopes": google_sync.SCOPES}))
    c.get("/auth/google/logout")
    assert revoked == ["OLD", "NEW"] and not (tmp_path / "google_token.json").exists()


def test_deleted_calendar_is_recreated_and_rename_is_applied(tmp_path):
    from test_calendar import FakeService

    from usos_planner.calendar.google_sync import ensure_calendar

    svc, store = FakeService(), tmp_path / "cal.json"
    store.write_text(json.dumps({"id": "gone"}))
    cal = ensure_calendar(svc, "Plan zajęć", store)
    assert cal["id"] != "gone" and json.loads(store.read_text())["id"] == cal["id"]
    renamed = ensure_calendar(svc, "Plan 2026L", store)
    assert renamed["id"] == cal["id"] and renamed["summary"] == "Plan 2026L"
