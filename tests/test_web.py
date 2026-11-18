# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

from pathlib import Path

from fastapi.testclient import TestClient

from usos_planner.config import Config
from usos_planner.web.app import create_app

FIX = Path(__file__).parent / "fixtures"


def make_client(tmp_path):
    cfg = Config()
    cfg.data_dir = tmp_path
    return TestClient(create_app(cfg))


def test_upload_and_plan(tmp_path):
    c = make_client(tmp_path)
    assert c.get("/").status_code == 200
    r = c.post("/upload", files={"file": ("sample.in", (FIX / "sample.in").read_bytes())},
               data={"prefs_text": "PtCzyWolne true"}, follow_redirects=True)
    assert r.status_code == 200 and "Plan 1" in r.text
    j = c.get("/plans/0.json").json()
    assert j["hard_conflicts"] == 0 and len(j["groups"]) == 3


def test_case_flow(tmp_path):
    c = make_client(tmp_path)
    r = c.post("/cases/new", data={"course": "Analiza III", "usos_course_id": "1000-AM3", "term_id": "2025Z",
                                   "class_type": "CW", "group_number": 2, "reason": "limit",
                                   "coordinator_name": "dr Anna Nowak", "coordinator_email": "a@x.pl"}, follow_redirects=False)
    assert r.status_code == 303
    case_url = r.headers["location"]
    r = c.post(case_url + "/draft", data={"kind": "coordinator", "recipient": "", "coordinator_name": "dr Anna Nowak",
                                          "justification": ""}, follow_redirects=True)
    assert "a@x.pl" in r.text and "Szanowna Pani Doktor" in r.text
    # sending without SMTP credentials must fail gracefully, not crash
    msg_id = r.url.path.rsplit("/", 1)[1]
    r = c.post(f"/messages/{msg_id}/send", data={"recipient": "a@x.pl", "subject": "s", "body": "b"}, follow_redirects=True)
    assert "missing" in r.text
    assert c.get("/cases").status_code == 200


def test_ics_download_and_gcal_without_credentials(tmp_path):
    c = make_client(tmp_path)
    c.post("/upload", files={"file": ("sample.in", (FIX / "sample.in").read_bytes())}, data={"prefs_text": ""})
    r = c.get("/plans/0.ics?start=2025-10-01&end=2026-01-30")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert r.text.count("BEGIN:VEVENT") == 3
    assert c.get("/plans/0.ics").status_code == 400  # no dates anywhere
    r = c.post("/plans/0/gcal", data={"start": "2025-10-01", "end": "2026-01-30"}, follow_redirects=True)
    assert "zaloguj się przez Google" in r.text  # friendly error, no crash


def test_settings_save_and_login_buttons(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = make_client(tmp_path)
    r = c.get("/settings")
    assert r.status_code == 200 and "Zaloguj przez USOS" in r.text  # header button always present
    # no consumer key yet -> login redirects to settings with a hint
    r = c.get("/auth/usos", follow_redirects=True)
    assert "Wpisz klucz aplikacji" in r.text
    r = c.post("/settings/usos", data={"base_url": "https://usosapps.uw.edu.pl", "consumer_key": "K", "consumer_secret": "S"}, follow_redirects=True)
    assert "Zapisano ustawienia USOS" in r.text
    saved = (tmp_path / "config.yaml").read_text()
    assert "consumer_key: K" in saved and "https://usosapps.uw.edu.pl/" in saved
    # Google: invalid JSON rejected, valid JSON stored, then login redirects to Google
    r = c.post("/settings/google", data={"client_json": "not json"}, follow_redirects=True)
    assert "niepoprawny JSON" in r.text
    client = {"installed": {"client_id": "x.apps.googleusercontent.com", "client_secret": "y",
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
                            "redirect_uris": ["http://localhost"]}}
    import json

    r = c.post("/settings/google", data={"client_json": json.dumps(client)}, follow_redirects=True)
    assert "Zapisano klienta Google" in r.text
    r = c.get("/auth/google", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("https://accounts.google.com/o/oauth2/auth")
    assert "redirect_uri=http%3A%2F%2Ftestserver%2Fauth%2Fgoogle%2Fcallback" in r.headers["location"]
    # student + calendar settings
    r = c.post("/settings/student", data={"full_name": "Jan Kowalski", "index_number": "1"}, follow_redirects=True)
    assert "Zapisano dane studenta" in r.text
    r = c.post("/settings/calendar", data={"name": "Plan", "term_start": "2025-10-01", "term_end": "2026-01-30", "holidays": "2025-11-11, 2025-11-01"}, follow_redirects=True)
    assert "holidays:\n- '2025-11-11'" in (tmp_path / "config.yaml").read_text() or "2025-11-11" in (tmp_path / "config.yaml").read_text()


def test_usos_login_callback_flow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = make_client(tmp_path)
    c.post("/settings/usos", data={"base_url": "https://usosapps.uw.edu.pl/", "consumer_key": "K", "consumer_secret": "S"})

    from usos_planner.usos import client as client_mod

    captured = {}

    def fake_begin(self, callback_uri="oob"):
        captured["cb"] = callback_uri
        return "https://usosapps.uw.edu.pl/services/oauth/authorize?oauth_token=RT", {"oauth_token": "RT", "oauth_token_secret": "RS"}

    def fake_finish(self, req, pin):
        captured["pin"] = pin
        self.token = {"oauth_token": "AT", "oauth_token_secret": "AS"}
        self.token_path.write_text("{}")

    monkeypatch.setattr(client_mod.UsosClient, "begin_login", fake_begin)
    monkeypatch.setattr(client_mod.UsosClient, "finish_login", fake_finish)
    monkeypatch.setattr(client_mod.UsosClient, "whoami", lambda self: {"first_name": "Jan", "last_name": "Kowalski"})

    r = c.get("/auth/usos", follow_redirects=False)
    assert r.status_code == 303 and "oauth_token=RT" in r.headers["location"]
    assert captured["cb"].endswith("/auth/usos/callback")
    r = c.get("/auth/usos/callback?oauth_token=RT&oauth_verifier=1234", follow_redirects=False)
    assert captured["pin"] == "1234" and r.headers["location"] == "/usos"
    r = c.get("/")
    assert "USOS: Jan Kowalski" in r.text
    r = c.get("/auth/usos/logout", follow_redirects=True)
    assert "USOS: Jan Kowalski" not in r.text


def test_google_callback_reuses_flow_with_code_verifier(tmp_path, monkeypatch):
    import json
    from urllib.parse import parse_qs, urlparse

    monkeypatch.chdir(tmp_path)
    c = make_client(tmp_path)
    client = {"installed": {"client_id": "x.apps.googleusercontent.com", "client_secret": "y",
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
                            "redirect_uris": ["http://localhost"]}}
    c.post("/settings/google", data={"client_json": json.dumps(client)})
    r = c.get("/auth/google", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"]
    flow_state = q["state"][0]

    from google_auth_oauthlib import flow as flow_mod

    seen = {}

    def fake_fetch_token(self, **kw):
        seen["code"] = kw["code"]
        seen["verifier"] = self.code_verifier
        from datetime import datetime, timedelta, timezone

        from google.oauth2.credentials import Credentials

        self._credentials = Credentials("A", refresh_token="R", token_uri="https://oauth2.googleapis.com/token",
                                        client_id="x", client_secret="y", scopes=self.oauth2session.scope,
                                        expiry=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1))

    monkeypatch.setattr(flow_mod.Flow, "fetch_token", fake_fetch_token)
    monkeypatch.setattr(flow_mod.Flow, "credentials", property(lambda self: self._credentials))
    monkeypatch.setattr("usos_planner.calendar.google_sync.account_email", lambda creds: "me@gmail.com")

    # wrong state -> rejected, flow kept
    r = c.get("/auth/google/callback?code=abc&state=wrong", follow_redirects=True)
    assert "niezgodny parametr state" in r.text
    r = c.get(f"/auth/google/callback?code=abc&state={flow_state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert seen["code"] == "abc" and seen["verifier"]  # the verifier from the login step was sent along
    assert (tmp_path / "google_token.json").exists()
    assert "Google: me@gmail.com" in c.get("/").text


def test_plan_count_is_selectable(tmp_path):
    c = make_client(tmp_path)
    files = {"file": ("sample.in", (FIX / "sample.in").read_bytes())}
    r = c.post("/upload", files=files, data={"prefs_text": "", "max_plans": "1"}, follow_redirects=True)
    assert "Plan 1 z 1" in r.text and "Plan 2" not in r.text
    assert '<option value="1" selected>' in r.text
    # the sample has 2 x 3 = 6 combinations, so asking for 10 yields all six
    r = c.post("/replan", data={"prefs_text": "", "max_plans": "10"}, follow_redirects=True)
    assert "Plan 6 z 6" in r.text and "Plan 7" not in r.text
    assert c.get("/plans/5.json").status_code == 200
    # out-of-range values are clamped, never rejected
    r = c.post("/replan", data={"prefs_text": "", "max_plans": "99"}, follow_redirects=True)
    assert "Plan 6 z 6" in r.text
    r = c.post("/replan", data={"prefs_text": "", "max_plans": "0"}, follow_redirects=True)
    assert "Plan 1 z 1" in r.text


def test_gcal_reports_disabled_calendar_api(tmp_path, monkeypatch):
    import httplib2
    from googleapiclient.errors import HttpError

    from usos_planner.calendar import google_sync

    body = (b'{"error": {"code": 403, "message": "Google Calendar API has not been used in project 123 before or it is '
            b'disabled. Enable it by visiting https://console.developers.google.com/... then retry.", "status": "PERMISSION_DENIED"}}')

    class FakeService:
        def calendarList(self):
            raise HttpError(httplib2.Response({"status": 403}), body, uri="https://www.googleapis.com/calendar/v3/users/me/calendarList")

    monkeypatch.setattr(google_sync, "load_credentials", lambda token_file: object())
    monkeypatch.setattr(google_sync, "service_from_credentials", lambda creds: FakeService())
    c = make_client(tmp_path)
    c.post("/upload", files={"file": ("sample.in", (FIX / "sample.in").read_bytes())}, data={"prefs_text": ""})
    r = c.post("/plans/0/gcal", data={"start": "2025-10-01", "end": "2026-01-30"}, follow_redirects=True)
    assert r.status_code == 200
    assert "Google Calendar API nie jest włączone" in r.text and "project=123" in r.text


def test_layout_places_overlaps_side_by_side_with_severity():
    from usos_planner.inputs import load_catalog
    from usos_planner.models import Plan
    from usos_planner.web.layout import layout_plan

    cat = load_catalog(FIX / "sample.in")
    # groups in file order: 0 WYK g1 (Pn 10:15-12), 1 CW g1 (Pn 8-10), 2 CW g2, 3 LAB g1 (Pn 8-10), 4 LAB g2, 5 LAB g3 (Pn 10:15-12)
    soft = layout_plan(cat, Plan(chosen=[0, 1, 5], penalty=0))
    monday = soft[0]
    cw = next(p for p in monday if p.gid == 1)
    assert (cw.col, cw.cols, cw.severity) == (0, 1, "")  # alone at 8:00, full width
    lecture, lab = (next(p for p in monday if p.gid == g) for g in (0, 5))
    assert {lecture.col, lab.col} == {0, 1} and lecture.cols == lab.cols == 2
    assert lecture.severity == lab.severity == "soft"
    assert lab.width_pct == 50.0 and {lecture.left_pct, lab.left_pct} == {0.0, 50.0}

    hard = layout_plan(cat, Plan(chosen=[0, 1, 3], penalty=0))
    assert {p.severity for p in hard[0] if p.gid in (1, 3)} == {"hard"}
    assert {p.severity for p in hard[0] if p.gid == 0} == {""}


def test_plan_page_marks_soft_conflicts_yellow_not_red(tmp_path):
    c = make_client(tmp_path)
    # pin the colliding lecture-time lab so a soft conflict is on the page
    r = c.post("/upload", files={"file": ("sample.in", (FIX / "sample.in").read_bytes())},
               data={"prefs_text": "Przypnij BazyDanych LAB 3", "max_plans": "1"}, follow_redirects=True)
    assert r.status_code == 200
    assert 'class="block soft"' in r.text and 'class="block hard"' not in r.text
    assert "width:calc(50.0% - 4px)" in r.text
