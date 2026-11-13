from pathlib import Path

from usos_planner.inputs.text_format import read_catalog
from usos_planner.models import ClassType, Preferences
from usos_planner.solver import solve

FIX = Path(__file__).parent / "fixtures"


def load():
    return read_catalog((FIX / "sample.in").read_text())


def names(cat, plan):
    return sorted(cat.describe_group(g) for g in plan.chosen)


def test_best_plan_has_no_conflicts():
    cat = load()
    report = solve(cat, Preferences(), max_plans=5)
    assert report.plans
    best = report.plans[0]
    assert best.hard_conflicts == 0 and best.soft_conflicts == 0
    assert all(p.penalty >= best.penalty for p in report.plans)
    assert len({tuple(p.chosen) for p in report.plans}) == len(report.plans), "alternatives are distinct"


def test_free_day_is_respected():
    """The original C++ solver ignored free days (iterated over a copy); make sure we don't."""
    cat = load()
    prefs = Preferences(free_days={0})
    report = solve(cat, prefs)
    # the lecture only exists on Monday -> its unit becomes infeasible
    assert report.infeasible_units == [0]
    # pinning the lecture overrides the free-day filter for that group only
    prefs.pinned = {0}
    report = solve(cat, prefs)
    assert report.plans
    for p in report.plans:
        for gid in p.chosen:
            if gid != 0:
                assert all(s.day != 0 for s in cat.groups[gid].slots)


def test_hours_filter_and_exclusion():
    cat = load()
    prefs = Preferences()
    prefs.hours[0] = (9 * 60, 24 * 60)  # nothing before 9 on Monday
    report = solve(cat, prefs)
    best = report.plans[0]
    assert "Analiza III CW gr. 1" not in names(cat, best)
    assert "Bazy danych LAB gr. 1" not in names(cat, best)
    prefs.excluded = {cat.find_group("Bazy danych", ClassType.LAB, 2)}
    report = solve(cat, prefs)
    assert "Bazy danych LAB gr. 2" not in names(cat, report.plans[0])


def test_soft_conflict_cheaper_than_hard():
    cat = load()
    prefs = Preferences()
    # force the Monday 10:15 lab (soft conflict with the lecture) and the Monday CW
    prefs.pinned = {cat.find_group("Bazy danych", ClassType.LAB, 3)}
    soft = solve(cat, prefs).plans[0]
    assert soft.soft_conflicts == 1 and soft.hard_conflicts == 0
    prefs.pinned = {cat.find_group("Bazy danych", ClassType.LAB, 1), cat.find_group("Analiza III", ClassType.CW, 1)}
    hard = solve(cat, prefs).plans[0]
    assert hard.hard_conflicts == 1
    assert hard.penalty > soft.penalty


def test_empty_catalog():
    from usos_planner.models import Catalog

    assert solve(Catalog(), Preferences()).plans == []
