# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

from datetime import date
from pathlib import Path

import pytest

from usos_planner.calendar.dates import TermDatesError, resolve_term_dates
from usos_planner.calendar.google_sync import clear_our_events, ensure_calendar, sync_plan
from usos_planner.calendar.ics import first_occurrence, plan_events, plan_to_ics
from usos_planner.config import CalendarConfig
from usos_planner.inputs.text_format import read_catalog
from usos_planner.models import Preferences
from usos_planner.solver import solve

FIX = Path(__file__).parent / "fixtures"


def best_plan():
    cat = read_catalog((FIX / "sample.in").read_text())
    cat.courses[0].usos_course_id, cat.courses[0].term_id = "1000-AM3", "2025Z"
    return cat, solve(cat, Preferences()).plans[0]


def test_first_occurrence():
    assert first_occurrence(date(2025, 10, 1), 0) == date(2025, 10, 6)  # Wed -> next Monday
    assert first_occurrence(date(2025, 10, 1), 2) == date(2025, 10, 1)  # same weekday


def test_ics_structure():
    cat, plan = best_plan()
    text = plan_to_ics(cat, plan, date(2025, 10, 1), date(2026, 1, 30), exdates=[date(2025, 11, 11)])  # Tuesday
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.count("BEGIN:VEVENT") == 3
    assert "RRULE:FREQ=WEEKLY;BYDAY=TU;UNTIL=20260130T235900" in text
    assert "DTSTART;TZID=Europe/Warsaw:20251007T120000" in text  # CW gr. 2 on Tuesday
    assert "EXDATE;TZID=Europe/Warsaw:20251111T120000" in text  # only on the Tuesday event
    assert text.count("EXDATE") == 1
    assert "SUMMARY:Analiza III (CW gr. 2)" in text
    assert "Kod USOS: 1000-AM3" in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    # same input -> same UIDs, so re-importing updates instead of duplicating
    assert plan_to_ics(cat, plan, date(2025, 10, 1), date(2026, 1, 30)).split("UID:")[1][:24] == text.split("UID:")[1][:24]


def test_events_outside_range_dropped():
    cat, plan = best_plan()
    evs = plan_events(cat, plan, date(2025, 10, 1), date(2025, 10, 2))  # Wed-Thu only
    assert [e["weekday"] for e in evs] == [2]


def test_resolve_term_dates():
    cfg = CalendarConfig(term_start="2025-10-01", term_end="2026-01-30")
    assert resolve_term_dates(cfg) == (date(2025, 10, 1), date(2026, 1, 30))
    assert resolve_term_dates(cfg, start="2025-10-06")[0] == date(2025, 10, 6)
    with pytest.raises(TermDatesError):
        resolve_term_dates(CalendarConfig())
    with pytest.raises(TermDatesError):
        resolve_term_dates(CalendarConfig(term_start="2026-01-01", term_end="2025-01-01"))

    class FakeClient:
        def call(self, method, **params):
            assert method == "terms/term" and params["term_id"] == "2025Z"
            return {"start_date": "2025-10-01", "end_date": "2026-01-25"}

    cat, _ = best_plan()
    assert resolve_term_dates(CalendarConfig(), cat, client=FakeClient()) == (date(2025, 10, 1), date(2026, 1, 25))


class FakeService:
    """Minimal stand-in for googleapiclient's chained resource objects."""

    def __init__(self):
        self.cals = [{"id": "other", "summary": "Other"}]
        self.events_by_cal: dict[str, list[dict]] = {}
        self.inserted = []

    class _Exec:
        def __init__(self, fn):
            self.execute = fn

    def calendarList(self):
        return type("L", (), {"list": lambda _s, **kw: self._Exec(lambda: {"items": list(self.cals)})})()

    def calendars(self):
        def insert(_s, body):
            cal = {"id": f"cal{len(self.cals)}", "summary": body["summary"]}
            self.cals.append(cal)
            return self._Exec(lambda: cal)

        return type("C", (), {"insert": insert})()

    def events(self):
        svc = self

        class E:
            def list(self, calendarId, **kw):
                return svc._Exec(lambda: {"items": [e for e in svc.events_by_cal.get(calendarId, []) if e["extendedProperties"]["private"].get("usos_planner") == "1"]})

            def delete(self, calendarId, eventId):
                def run():
                    svc.events_by_cal[calendarId] = [e for e in svc.events_by_cal[calendarId] if e["id"] != eventId]
                return svc._Exec(run)

            def insert(self, calendarId, body):
                def run():
                    stored = dict(body, id=f"e{len(svc.inserted)}")
                    svc.events_by_cal.setdefault(calendarId, []).append(stored)
                    svc.inserted.append(stored)
                    return stored
                return svc._Exec(run)

        return E()


def test_google_sync_is_idempotent_and_keeps_manual_events():
    svc = FakeService()
    cat, plan = best_plan()
    cal = ensure_calendar(svc, "Plan zajęć")
    assert cal["summary"] == "Plan zajęć" and ensure_calendar(svc, "Plan zajęć")["id"] == cal["id"]
    svc.events_by_cal[cal["id"]] = [{"id": "manual", "extendedProperties": {"private": {}}}]

    r1 = sync_plan(svc, cat, plan, date(2025, 10, 1), date(2026, 1, 30))
    assert r1["inserted"] == 3 and r1["removed"] == 0
    body = svc.inserted[0]
    assert body["recurrence"][0].startswith("RRULE:FREQ=WEEKLY;BYDAY=")
    assert body["start"]["timeZone"] == "Europe/Warsaw"
    assert body["extendedProperties"]["private"]["usos_planner"] == "1"

    r2 = sync_plan(svc, cat, plan, date(2025, 10, 1), date(2026, 1, 30))
    assert r2["removed"] == 3 and r2["inserted"] == 3
    ids = {e["id"] for e in svc.events_by_cal[cal["id"]]}
    assert "manual" in ids and len(ids) == 4
    assert clear_our_events(svc, cal["id"]) == 3
