# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

"""The `.in` text format used by the original C++ CollegePlanner.

    PRZEDMIOT <name with spaces>
      ZAJECIA <WYK | CW | LAB | CWW | WF | SEM | OTHER>
        GRUPA <nr>
          <Pn|Wt|Sr|Cz|Pt|So|Nd> HH:MM-HH:MM

Preferences (`prefs.in`): `PnGodziny 08:00-16:00`, `PtCzyWolne true`,
`Przypnij AnalizaIII CW 7` (course name written without spaces).
"""

from __future__ import annotations

from ..models import DAY_NAMES, Catalog, ClassType, Preferences, Slot, fmt_time, parse_time_range


class InputError(ValueError):
    pass


def _day_index(token: str) -> int:
    for i, d in enumerate(DAY_NAMES):
        if token.lower() == d.lower():
            return i
    raise InputError(f"unknown day {token!r}")


def read_catalog(text: str) -> Catalog:
    cat = Catalog()
    course_id = unit_id = group_id = None
    for nr, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, rest = line.partition(" ")
        rest = rest.strip()
        try:
            if key == "PRZEDMIOT":
                if not rest:
                    raise InputError("missing course name")
                course_id = cat.add_course(rest)
                unit_id = group_id = None
            elif key == "ZAJECIA":
                if course_id is None:
                    raise InputError("ZAJECIA before PRZEDMIOT")
                if rest.upper() not in ClassType.__members__:
                    raise InputError(f"unknown class type {rest!r}")
                unit_id = cat.add_unit(course_id, ClassType(rest.upper()))
                group_id = None
            elif key == "GRUPA":
                if unit_id is None:
                    raise InputError("GRUPA before ZAJECIA")
                group_id = cat.add_group(unit_id, int(rest), [])
            else:
                if group_id is None:
                    raise InputError("time slot before GRUPA")
                day = _day_index(key)
                start, end = parse_time_range(rest)
                cat.groups[group_id].slots.append(Slot(day, start, end))
        except ValueError as e:  # includes InputError and int()/time parsing
            raise InputError(f"line {nr}: {e}") from None
    return cat


def write_catalog(cat: Catalog) -> str:
    out = []
    for c in cat.courses:
        out.append(f"PRZEDMIOT {c.name.replace('#', '♯')}")
        for uid in c.unit_ids:
            u = cat.units[uid]
            out.append(f"  ZAJECIA {u.class_type.value}")
            for gid in u.group_ids:
                g = cat.groups[gid]
                out.append(f"    GRUPA {g.number}")
                for s in sorted(set(g.slots), key=lambda s: (s.day, s.start)):
                    out.append(f"      {DAY_NAMES[s.day]} {fmt_time(s.start)}-{fmt_time(s.end)}")
    return "\n".join(out) + "\n"


def read_prefs(text: str, cat: Catalog, base: Preferences | None = None) -> Preferences:
    prefs = base or Preferences()
    prefs.hours = list(prefs.hours)
    for nr, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, value = line.partition(" ")
        value = value.strip()
        try:
            if key.endswith("Godziny"):
                prefs.hours[_day_index(key[:-7])] = parse_time_range(value)
            elif key.endswith("CzyWolne"):
                d = _day_index(key[:-8])
                if value.lower() in {"true", "tak", "1"}:
                    prefs.free_days.add(d)
                else:
                    prefs.free_days.discard(d)
            elif key == "Przypnij":
                name, ctype, number = value.rsplit(" ", 2)
                gid = cat.find_group(name, ClassType.parse(ctype), int(number))
                if gid is None:
                    raise InputError(f"unknown group {value!r}")
                prefs.pinned.add(gid)
            else:
                raise InputError(f"unknown key {key!r}")
        except ValueError as e:
            raise InputError(f"line {nr}: {e}") from None
    return prefs


def write_prefs(prefs: Preferences, cat: Catalog) -> str:
    out = []
    for d in range(5):
        lo, hi = prefs.hours[d]
        out.append(f"{DAY_NAMES[d]}Godziny {fmt_time(lo)}-{fmt_time(hi)}")
    out.append("")
    for d in range(5):
        out.append(f"{DAY_NAMES[d]}CzyWolne {'true' if d in prefs.free_days else 'false'}")
    if prefs.pinned:
        out.append("")
        for gid in sorted(prefs.pinned):
            g = cat.groups[gid]
            out.append(
                f"Przypnij {cat.course_of_group(gid).name.replace(' ', '')} "
                f"{cat.unit_of_group(gid).class_type.value} {g.number}"
            )
    return "\n".join(out) + "\n"
