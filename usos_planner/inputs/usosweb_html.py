# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

"""Parser for a saved USOSweb timetable page (the `<timetable-entry>` web components).

Fixes relative to the original parser.py: no values leak between entries, the
lecturer is taken only from the person slot, SEM/LEK are recognised, and the
class type is read from the `info` slot without substring collisions.
"""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from ..models import DAY_NAMES_PL, Catalog, ClassType, Slot

_TYPE_PATTERNS = [
    (re.compile(r"\bCWW\b", re.I), ClassType.CWW),
    (re.compile(r"\b(CW|ĆW|ćwiczenia)\b", re.I), ClassType.CW),
    (re.compile(r"\b(WYK|wykład)\b", re.I), ClassType.WYK),
    (re.compile(r"\b(LAB|laboratorium)\b", re.I), ClassType.LAB),
    (re.compile(r"\b(SEM|seminarium)\b", re.I), ClassType.SEM),
    (re.compile(r"\b(WF|wychowanie fizyczne)\b", re.I), ClassType.WF),
    (re.compile(r"\b(LEK\w*|lektorat)\b", re.I), ClassType.LEK),
    (re.compile(r"\b(KON|konwersatorium)\b", re.I), ClassType.KON),
    (re.compile(r"\b(WAR|warsztaty?)\b", re.I), ClassType.WAR),
]
_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})")
_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_DAY_INDEX = {name: i for i, name in enumerate(DAY_NAMES_PL)}


def _class_type(label: str) -> ClassType:
    for pattern, ctype in _TYPE_PATTERNS:
        if pattern.search(label):
            return ctype
    return ClassType.OTHER


def _text(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


def _day_of(entry) -> int | None:
    day_el = entry.find_parent("timetable-day")
    if day_el is None:
        return None
    # the day name is in the component's attribute on newer USOSweb, or the h4 before it
    for attr in ("day", "name", "data-day"):
        if day_el.get(attr) and day_el.get(attr).lower() in _DAY_INDEX:
            return _DAY_INDEX[day_el.get(attr).lower()]
    h4 = day_el.find_previous("h4")
    if h4:
        return _DAY_INDEX.get(_text(h4).lower())
    return None


def parse_usosweb_html(path: str | Path, warn=None) -> Catalog:
    warn = warn or (lambda msg: None)
    soup = BeautifulSoup(Path(path).read_text(encoding="utf-8", errors="replace"), "html.parser")

    # (course, type, number) -> {"slots": set, "lecturer": str}
    collected: dict[tuple[str, ClassType, int], dict] = {}
    order: list[str] = []
    for entry in soup.find_all("timetable-entry"):
        course = (entry.get("name") or "").strip()
        if not course:
            continue
        label = _text(entry.find("div", {"slot": "info"}))
        if not label:
            warn(f"{course}: entry without group label skipped")
            continue
        ctype = _class_type(label)
        m = re.search(r"\d+", label)
        number = int(m.group()) if m else 0

        dialog = _text(entry.find("span", {"slot": "dialog-event"}))
        rng = _RANGE.search(dialog)
        if rng:
            h1, m1, h2, m2 = map(int, rng.groups())
            start, end = h1 * 60 + m1, h2 * 60 + m2
        else:
            t = _TIME.search(_text(entry.find("span", {"slot": "time"})))
            if not t:
                warn(f"{course} {label}: no time found, skipped")
                continue
            start = int(t.group(1)) * 60 + int(t.group(2))
            end = None
        day = _day_of(entry)
        if day is None or end is None or end <= start:
            warn(f"{course} {label}: incomplete day/time, skipped")
            continue

        person = _text(entry.find("div", {"slot": "dialog-person"})).split(",")[0].strip()
        key = (course, ctype, number)
        if course not in order:
            order.append(course)
        rec = collected.setdefault(key, {"slots": set(), "lecturer": ""})
        rec["slots"].add(Slot(day, start, end))
        if person:
            rec["lecturer"] = person

    cat = Catalog()
    for course in order:
        cid = cat.add_course(course)
        by_type: dict[ClassType, int] = {}
        for (cname, ctype, number), rec in collected.items():
            if cname != course:
                continue
            uid = by_type.setdefault(ctype, None) or cat.add_unit(cid, ctype)
            by_type[ctype] = uid
            cat.add_group(uid, number, sorted(rec["slots"], key=lambda s: (s.day, s.start)), lecturer=rec["lecturer"])
    return cat
