"""Executes a plan against an open registration round.

Safety rules baked in:
* dry-run is the default; nothing is sent unless `live=True`,
* one attempt per course per run, no tight polling loops (USOS rate-limits and
  universities forbid hammering registration endpoints),
* every attempt is recorded so the escalation module can pick up failures.

Note: the API's `register` works on course level (round_id, course_id, term_id).
Whether a round lets you choose a group depends on the round type; this module
passes the plan's group as a hint and records what the server answered.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .escalation.store import CaseStore
from .models import Catalog, Plan
from .usos.registrations import RegistrationOutcome, Registrations

# classification of the server's refusal; drives what the escalation GUI proposes
REASON_LIMIT = "limit"  # group/course full -> ask coordinator, then vice-dean
REASON_ENTITLEMENT = "not_entitled"  # not on the entitled list -> ask vice-dean (podanie)
REASON_CLOSED = "closed"  # round not active -> wait / retry later
REASON_ALREADY = "already_registered"
REASON_OTHER = "other"

_PATTERNS = {
    REASON_LIMIT: ("limit", "brak miejsc", "pełna", "full", "wyczerpan"),
    REASON_ENTITLEMENT: ("uprawni", "entitled", "nie masz prawa", "not allowed", "etap", "program"),
    REASON_CLOSED: ("zamkni", "closed", "nieaktyw", "not active", "tura", "round"),
    REASON_ALREADY: ("już zarejestr", "already registered", "already enrolled"),
}


def classify(message: str) -> str:
    low = message.lower()
    for reason, needles in _PATTERNS.items():
        if any(n in low for n in needles):
            return reason
    return REASON_OTHER


@dataclass
class Attempt:
    course: str
    usos_course_id: str
    term_id: str
    class_type: str
    group_number: int
    live: bool
    ok: bool
    status: int
    message: str
    reason: str
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def register_plan(
    reg: Registrations | None,
    cat: Catalog,
    plan: Plan,
    round_id: str,
    store: CaseStore | None = None,
    live: bool = False,
    pause_s: float = 1.5,
    skip_types: tuple[str, ...] = ("WF", "LEK"),
) -> list[Attempt]:
    """Registers for every course in `plan` once. WF/LEK are skipped on purpose:
    token registrations have no write API and automating the żetony website
    violates most universities' rules."""
    attempts: list[Attempt] = []
    done_courses: set[str] = set()
    for gid in plan.chosen:
        course = cat.course_of_group(gid)
        unit = cat.unit_of_group(gid)
        group = cat.groups[gid]
        if unit.class_type.value in skip_types:
            continue
        if not course.usos_course_id or not course.term_id:
            attempts.append(_attempt(course, unit, group, live, False, 0, "catalog has no USOS course id/term (load it from the API)", REASON_OTHER))
            continue
        key = f"{course.usos_course_id}/{course.term_id}"
        if key in done_courses:  # one `register` per course covers all its units
            continue
        done_courses.add(key)

        if not live or reg is None:
            attempts.append(_attempt(course, unit, group, live, True, 0, "dry-run: would call registrations/register", "dry_run"))
            continue

        outcome: RegistrationOutcome = reg.register(round_id, course.usos_course_id, course.term_id)
        reason = "ok" if outcome.ok else classify(outcome.message)
        attempts.append(_attempt(course, unit, group, live, outcome.ok, outcome.status, outcome.message, reason))
        time.sleep(pause_s)

    if store is not None:
        for a in attempts:
            store.record_attempt(a.to_dict())
            if a.live and not a.ok and a.reason in (REASON_LIMIT, REASON_ENTITLEMENT, REASON_OTHER):
                store.open_case(
                    course=a.course,
                    usos_course_id=a.usos_course_id,
                    term_id=a.term_id,
                    class_type=a.class_type,
                    group_number=a.group_number,
                    reason=a.reason,
                    server_message=a.message,
                    coordinators=_coordinators_for(cat, a.usos_course_id),
                )
    return attempts


def _coordinators_for(cat: Catalog, usos_course_id: str) -> list[dict]:
    for c in cat.courses:
        if c.usos_course_id == usos_course_id:
            return c.coordinators
    return []


def _attempt(course, unit, group, live, ok, status, message, reason) -> Attempt:
    return Attempt(
        course=course.name,
        usos_course_id=course.usos_course_id or "",
        term_id=course.term_id or "",
        class_type=unit.class_type.value,
        group_number=group.number,
        live=live,
        ok=ok,
        status=status,
        message=message,
        reason=reason,
    )
