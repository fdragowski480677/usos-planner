# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

"""Thin wrappers over `services/registrations/*` and the timetable methods used to
build a catalog.  Field lists follow the USOS API reference (registrations and tt
modules); unknown fields are simply absent from the response."""

from __future__ import annotations

from dataclasses import dataclass

from .client import UsosClient

ROUND_COURSES_FIELDS = "course|term_id|status|limits|registrations_count|user_registration_status|is_registration_valid"
CART_FIELDS = (
    "course_id|term_id|course_name|registration_status|user_registration_status|"
    "is_registration_valid|active_registration_round_id"
)
COURSE_EDITION_FIELDS = "course_id|term_id|course_name|coordinators|course_units_ids"
TT_FIELDS = "type|start_time|end_time|name|classtype_id|classtype_name|group_number|unit_id|course_id|course_name|lecturer_ids"


@dataclass
class RegistrationOutcome:
    ok: bool
    status: int
    message: str
    payload: dict


class Registrations:
    def __init__(self, client: UsosClient):
        self.c = client

    # ----- reading --------------------------------------------------------
    def my_registrations(self, active_only: bool = True) -> list[dict]:
        return self.c.call("registrations/user_registrations", active_only=active_only, fields="id|description|rounds|faculty")

    def cart(self) -> list[dict]:
        return self.c.call("registrations/courses_cart", fields=CART_FIELDS)

    def round(self, round_id: str) -> dict:
        return self.c.call("registrations/registration_round", id=round_id)

    def round_courses(self, round_id: str) -> list[dict]:
        return self.c.call("registrations/registration_round_courses", registration_round_id=round_id, fields=ROUND_COURSES_FIELDS)

    def search_rounds(self, start_date: str, end_date: str, user_related: bool = True) -> list[dict]:
        return self.c.call("registrations/search_rounds", start_date=start_date, end_date=end_date, user_related=user_related)

    def course_edition(self, course_id: str, term_id: str) -> dict:
        return self.c.call("courses/course_edition", course_id=course_id, term_id=term_id, fields=COURSE_EDITION_FIELDS)

    def course_timetable(self, course_id: str, term_id: str) -> list[dict]:
        return self.c.call("tt/course_edition", course_id=course_id, term_id=term_id, fields=TT_FIELDS)

    def user_info(self, user_id: str) -> dict:
        return self.c.call("users/user", user_id=user_id, fields="id|first_name|last_name|titles|email|employment_positions")

    # ----- writing --------------------------------------------------------
    def register(self, round_id: str, course_id: str, term_id: str, **extra) -> RegistrationOutcome:
        return self._write("registrations/register", round_id=round_id, course_id=course_id, term_id=term_id, **extra)

    def unregister(self, round_id: str, course_id: str, term_id: str) -> RegistrationOutcome:
        return self._write("registrations/unregister", round_id=round_id, course_id=course_id, term_id=term_id)

    def _write(self, method: str, **params) -> RegistrationOutcome:
        from .client import UsosError

        try:
            payload = self.c.call(method, **params)
            return RegistrationOutcome(True, 200, "ok", payload if isinstance(payload, dict) else {})
        except UsosError as e:
            return RegistrationOutcome(False, e.status, e.user_message, e.payload)
