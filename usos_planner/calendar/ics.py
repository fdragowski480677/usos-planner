"""Turn a plan into an iCalendar file with weekly recurring events.

Each time slot of each chosen group becomes one VEVENT recurring every week from the
first matching weekday on/after `start` until `end`.  Google Calendar imports this via
Settings -> Import & export, or "From URL" if the file is served publicly.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..models import Catalog, Plan, Slot

DEFAULT_TZ = "Europe/Warsaw"
ICAL_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def first_occurrence(start: date, weekday: int) -> date:
    return start + timedelta(days=(weekday - start.weekday()) % 7)


def _dt(d: date, minutes: int) -> str:
    return f"{d:%Y%m%d}T{minutes // 60:02d}{minutes % 60:02d}00"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545 line folding at 75 octets."""
    out, raw = [], line.encode("utf-8")
    while len(raw) > 75:
        cut = 75
        while cut > 0 and (raw[cut] & 0xC0) == 0x80:  # don't split a UTF-8 sequence
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = b" " + raw[cut:]
    out.append(raw.decode("utf-8"))
    return "\r\n".join(out)


def event_uid(cat: Catalog, gid: int, slot: Slot, term: str) -> str:
    c, u, g = cat.course_of_group(gid), cat.unit_of_group(gid), cat.groups[gid]
    key = f"{term}|{c.usos_course_id or c.name}|{u.class_type.value}|{g.number}|{slot.day}|{slot.start}|{slot.end}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:24] + "@usos-planner"


def plan_events(cat: Catalog, plan: Plan, start: date, end: date, exdates: list[date] | None = None) -> list[dict]:
    """Neutral event records used by both the .ics writer and the Google sync."""
    events = []
    term = cat.courses[0].term_id if cat.courses and cat.courses[0].term_id else f"{start:%Y%m%d}"
    for gid in plan.chosen:
        c, u, g = cat.course_of_group(gid), cat.unit_of_group(gid), cat.groups[gid]
        for slot in g.slots:
            first = first_occurrence(start, slot.day)
            if first > end:
                continue
            description = [f"{u.class_type.value} grupa {g.number}"]
            if c.usos_course_id:
                description.append(f"Kod USOS: {c.usos_course_id}")
            if g.lecturer:
                description.append(f"Prowadzący: {g.lecturer}")
            events.append(
                {
                    "uid": event_uid(cat, gid, slot, term),
                    "summary": f"{c.name} ({u.class_type.value} gr. {g.number})",
                    "description": "\n".join(description),
                    "first": first,
                    "start_min": slot.start,
                    "end_min": slot.end,
                    "weekday": slot.day,
                    "until": end,
                    "exdates": [d for d in (exdates or []) if d.weekday() == slot.day and first <= d <= end],
                }
            )
    return events


def plan_to_ics(cat: Catalog, plan: Plan, start: date, end: date, name: str = "Plan zajęć",
                tz: str = DEFAULT_TZ, exdates: list[date] | None = None) -> str:
    ZoneInfo(tz)  # validate
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//usos-planner//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
        f"X-WR-TIMEZONE:{tz}",
    ]
    for ev in plan_events(cat, plan, start, end, exdates):
        lines += [
            "BEGIN:VEVENT",
            f"UID:{ev['uid']}",
            f"DTSTAMP:{now}",
            f"DTSTART;TZID={tz}:{_dt(ev['first'], ev['start_min'])}",
            f"DTEND;TZID={tz}:{_dt(ev['first'], ev['end_min'])}",
            f"RRULE:FREQ=WEEKLY;BYDAY={ICAL_DAYS[ev['weekday']]};UNTIL={_dt(ev['until'], 23 * 60 + 59)}",
            f"SUMMARY:{_escape(ev['summary'])}",
            f"DESCRIPTION:{_escape(ev['description'])}",
        ]
        for d in ev["exdates"]:
            lines.append(f"EXDATE;TZID={tz}:{_dt(d, ev['start_min'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"
