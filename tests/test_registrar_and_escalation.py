from pathlib import Path

from usos_planner.config import StudentConfig
from usos_planner.escalation.store import CaseStore
from usos_planner.escalation.templates import CaseContext, coordinator_email, dean_petition
from usos_planner.inputs.text_format import read_catalog
from usos_planner.models import Preferences
from usos_planner.registrar import REASON_ENTITLEMENT, REASON_LIMIT, classify, register_plan
from usos_planner.solver import solve
from usos_planner.usos.registrations import RegistrationOutcome

FIX = Path(__file__).parent / "fixtures"


class FakeReg:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def register(self, round_id, course_id, term_id, **extra):
        self.calls.append((round_id, course_id, term_id))
        return self.answers.get(course_id, RegistrationOutcome(True, 200, "ok", {}))


def catalog_with_ids():
    cat = read_catalog((FIX / "sample.in").read_text())
    cat.courses[0].usos_course_id, cat.courses[0].term_id = "1000-AM3", "2025Z"
    cat.courses[0].coordinators = [{"name": "dr Anna Nowak", "email": "a.nowak@example.edu"}]
    cat.courses[1].usos_course_id, cat.courses[1].term_id = "1000-BD", "2025Z"
    return cat


def test_classify():
    assert classify("Limit miejsc w grupie został wyczerpany") == REASON_LIMIT
    assert classify("Nie masz uprawnień do tej rejestracji") == REASON_ENTITLEMENT


def test_dry_run_sends_nothing(tmp_path):
    cat = catalog_with_ids()
    plan = solve(cat, Preferences()).plans[0]
    reg = FakeReg({})
    attempts = register_plan(reg, cat, plan, "R1", store=CaseStore(tmp_path / "c.db"), live=False)
    assert reg.calls == []
    assert len(attempts) == 2 and all(a.reason == "dry_run" for a in attempts)


def test_live_failure_opens_case(tmp_path):
    cat = catalog_with_ids()
    plan = solve(cat, Preferences()).plans[0]
    reg = FakeReg({"1000-AM3": RegistrationOutcome(False, 400, "Limit miejsc został wyczerpany", {})})
    store = CaseStore(tmp_path / "c.db")
    attempts = register_plan(reg, cat, plan, "R1", store=store, live=True, pause_s=0)
    assert sorted(c for _, c, _ in reg.calls) == ["1000-AM3", "1000-BD"]  # one call per course, not per unit
    assert {a.usos_course_id: a.ok for a in attempts} == {"1000-AM3": False, "1000-BD": True}
    cases = store.cases()
    assert len(cases) == 1 and cases[0]["reason"] == REASON_LIMIT
    assert cases[0]["coordinators"][0]["email"] == "a.nowak@example.edu"
    # a second failed run updates the same case instead of duplicating it
    register_plan(reg, cat, plan, "R1", store=store, live=True, pause_s=0)
    assert len(store.cases()) == 1
    assert len(store.attempts()) == 4


def test_templates_contain_key_facts():
    student = StudentConfig(full_name="Jan Kowalski", index_number="123456", programme="Informatyka", year="II rok",
                            faculty="Wydział MIM", email="jk@student.uw.edu.pl", vice_dean_name="dr hab. X Y")
    ctx = CaseContext("Analiza III", "1000-AM3", "2025Z", "CW", 2, "limit", coordinator_name="dr Anna Nowak")
    subject, body = coordinator_email(student, ctx)
    assert "1000-AM3" in subject and "123456" in subject
    assert "Szanowna Pani Doktor" in body and "grupa nr 2" in body and "2025/2026" in body
    subject, body = dean_petition(student, ctx, coordinator_approved=True)
    assert "PODANIE" in body and "dr hab. X Y" in body and "dr Anna Nowak" in body


def test_case_store_messages(tmp_path):
    store = CaseStore(tmp_path / "c.db")
    cid = store.open_case(course="A", usos_course_id="X", term_id="2025Z", class_type="CW", group_number=1,
                          reason="limit", server_message="full", coordinators=[])
    mid = store.add_message(cid, "coordinator", "a@b.c", "s", "b")
    assert store.message(mid)["sent"] == 0
    store.mark_sent(mid)
    store.set_status(cid, "coordinator_sent")
    assert store.message(mid)["sent"] == 1
    assert store.case(cid)["status"] == "coordinator_sent"
