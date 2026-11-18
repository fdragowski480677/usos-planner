# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

"""Build a Catalog straight from the USOS API (no HTML scraping).

For each course edition we read `tt/course_edition`, which lists every class
meeting of every group in the term.  Meetings are collapsed into weekly slots
(day, start, end); a group that meets at two different times gets two slots.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from ..models import Catalog, ClassType, Slot
from ..usos.registrations import Registrations

# USOS classtype ids are installation specific; the names are more stable.
_CLASSTYPE_NAMES = {
    "wykład": ClassType.WYK,
    "ćwiczenia": ClassType.CW,
    "laboratorium": ClassType.LAB,
    "seminarium": ClassType.SEM,
    "konwersatorium": ClassType.KON,
    "warsztaty": ClassType.WAR,
    "lektorat": ClassType.LEK,
    "wychowanie fizyczne": ClassType.WF,
}


def classtype_from_api(classtype_id: str | None, name) -> ClassType:
    label = ""
    if isinstance(name, dict):
        label = (name.get("pl") or name.get("en") or "").lower()
    elif name:
        label = str(name).lower()
    for key, ctype in _CLASSTYPE_NAMES.items():
        if key in label:
            return ctype
    if classtype_id:
        return ClassType.parse(classtype_id)
    return ClassType.OTHER


def _minutes(ts: str) -> int:
    dt = datetime.fromisoformat(ts)
    return dt.hour * 60 + dt.minute


def _weekday(ts: str) -> int:
    return datetime.fromisoformat(ts).weekday()


def catalog_from_api(reg: Registrations, course_terms: list[tuple[str, str]], with_coordinators: bool = True) -> Catalog:
    cat = Catalog()
    for course_id, term_id in course_terms:
        activities = reg.course_timetable(course_id, term_id)
        if not activities:
            continue
        name = course_id
        coordinators: list[dict] = []
        if with_coordinators:
            try:
                ed = reg.course_edition(course_id, term_id)
                name = _pl(ed.get("course_name")) or course_id
                for person in ed.get("coordinators") or []:
                    coordinators.append(
                        {
                            "user_id": person.get("id"),
                            "name": f"{person.get('first_name', '')} {person.get('last_name', '')}".strip(),
                            "email": person.get("email"),
                        }
                    )
            except Exception:  # the course page may be restricted; timetable still works
                pass
        else:
            name = _pl(activities[0].get("course_name")) or course_id
        cid = cat.add_course(name, usos_course_id=course_id, term_id=term_id, coordinators=coordinators)

        # (ctype, group_number) -> set of slots, plus the USOS unit id
        groups: dict[tuple[ClassType, int], set[Slot]] = defaultdict(set)
        unit_ids: dict[tuple[ClassType, int], str | None] = {}
        for act in activities:
            if act.get("type") not in (None, "classgroup", "classgroup2"):
                continue
            ctype = classtype_from_api(act.get("classtype_id"), act.get("classtype_name"))
            number = int(act.get("group_number") or 0)
            try:
                slot = Slot(_weekday(act["start_time"]), _minutes(act["start_time"]), _minutes(act["end_time"]))
            except (KeyError, ValueError):
                continue
            groups[(ctype, number)].add(slot)
            unit_ids.setdefault((ctype, number), str(act.get("unit_id")) if act.get("unit_id") else None)

        units: dict[ClassType, int] = {}
        for (ctype, number), slots in sorted(groups.items(), key=lambda kv: (kv[0][0].value, kv[0][1])):
            uid = units.get(ctype)
            if uid is None:
                uid = units[ctype] = cat.add_unit(cid, ctype)
            cat.add_group(uid, number, sorted(slots, key=lambda s: (s.day, s.start)), usos_unit_id=unit_ids[(ctype, number)])
    return cat


def _pl(value) -> str:
    if isinstance(value, dict):
        return value.get("pl") or value.get("en") or ""
    return value or ""
