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
    assert "google_credentials.json" in r.text  # friendly error, no crash
