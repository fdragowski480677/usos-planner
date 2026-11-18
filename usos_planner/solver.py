# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

"""Schedule solver built on OR-Tools CP-SAT.

Decision: one boolean per group, exactly one group chosen per unit.  Objective is a
weighted sum of hard conflicts, soft conflicts, time spent at university per day,
late ends and early starts - the same penalty model as the original C++ planner,
but solved exactly and with alternatives enumerated by excluding previous answers.
"""

from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model

from .conflicts import Conflict, conflict_pairs
from .models import Catalog, Plan, Preferences

DAY_MINUTES = 24 * 60
MAX_PLANS = 10  # the most alternatives the CLI and GUI will enumerate


def clamp_plans(n: int) -> int:
    """Number of plans to look for, kept between 1 and MAX_PLANS."""
    return max(1, min(int(n), MAX_PLANS))


@dataclass
class SolveReport:
    plans: list[Plan]
    infeasible_units: list[int]  # units with no allowed groups (plan impossible)
    dropped_groups: list[int]  # groups removed by preferences


def allowed_groups(cat: Catalog, prefs: Preferences) -> tuple[list[list[int]], list[int], list[int]]:
    """Per unit: the group ids the solver may use (pins win; then filters)."""
    allowed: list[list[int]] = []
    infeasible: list[int] = []
    dropped: list[int] = []
    for uid, unit in enumerate(cat.units):
        pinned = [g for g in unit.group_ids if g in prefs.pinned]
        candidates = pinned or unit.group_ids
        ok = []
        for g in candidates:
            if g in prefs.excluded or (g not in prefs.pinned and not prefs.group_allowed(cat.groups[g])):
                dropped.append(g)
            else:
                ok.append(g)
        if not ok:
            infeasible.append(uid)
        allowed.append(ok)
    return allowed, infeasible, dropped


def solve(cat: Catalog, prefs: Preferences, max_plans: int = 5, time_limit_s: float = 10.0) -> SolveReport:
    max_plans = clamp_plans(max_plans)
    allowed, infeasible, dropped = allowed_groups(cat, prefs)
    if infeasible or not cat.units:
        return SolveReport([], infeasible, dropped)

    candidate_ids = sorted({g for grp in allowed for g in grp})
    pairs = conflict_pairs(cat, candidate_ids)

    model = cp_model.CpModel()
    x = {g: model.NewBoolVar(f"g{g}") for g in candidate_ids}
    for grp in allowed:
        model.AddExactlyOne(x[g] for g in grp)

    terms: list[cp_model.LinearExpr] = []
    hard_vars, soft_vars = [], []
    for (a, b), kind in pairs.items():
        both = model.NewBoolVar(f"c{a}_{b}")
        model.AddBoolAnd([x[a], x[b]]).OnlyEnforceIf(both)
        model.AddBoolOr([x[a].Not(), x[b].Not()]).OnlyEnforceIf(both.Not())
        if kind == Conflict.HARD:
            hard_vars.append(both)
            terms.append(prefs.w_hard_conflict * both)
        else:
            soft_vars.append(both)
            terms.append(prefs.w_soft_conflict * both)

    # per-day span, late end, early start
    for day in range(7):
        day_groups = [g for g in candidate_ids if any(s.day == day for s in cat.groups[g].slots)]
        if not day_groups:
            continue
        used = model.NewBoolVar(f"used{day}")
        model.AddMaxEquality(used, [x[g] for g in day_groups])
        # first = earliest start of a chosen group (24h if none), last = latest end (0 if none)
        first = model.NewIntVar(0, DAY_MINUTES, f"first{day}")
        last = model.NewIntVar(0, DAY_MINUTES, f"last{day}")
        model.AddMinEquality(first, [_bound_if_chosen(model, cat, g, day, x[g], "start") for g in day_groups])
        model.AddMaxEquality(last, [_bound_if_chosen(model, cat, g, day, x[g], "end") for g in day_groups])

        span = model.NewIntVar(0, DAY_MINUTES, f"span{day}")
        model.Add(span == last - first).OnlyEnforceIf(used)
        model.Add(span == 0).OnlyEnforceIf(used.Not())
        late = model.NewIntVar(0, DAY_MINUTES, f"late{day}")
        model.AddMaxEquality(late, [last - prefs.late_after, 0])
        early_raw = model.NewIntVar(-DAY_MINUTES, DAY_MINUTES, f"earlyraw{day}")
        model.Add(early_raw == prefs.early_before - first).OnlyEnforceIf(used)
        model.Add(early_raw == 0).OnlyEnforceIf(used.Not())
        early = model.NewIntVar(0, DAY_MINUTES, f"early{day}")
        model.AddMaxEquality(early, [early_raw, 0])

        terms.append(prefs.w_time_spent * span)
        terms.append(prefs.w_late_end * late)
        terms.append(prefs.w_early_start * early)
        terms.append(prefs.w_day_used * used)

    model.Minimize(sum(terms) if terms else 0)

    plans: list[Plan] = []
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 8
    while len(plans) < max_plans:
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
        chosen = [next(g for g in grp if solver.Value(x[g])) for grp in allowed]
        plans.append(
            Plan(
                chosen=chosen,
                penalty=int(solver.ObjectiveValue()),
                hard_conflicts=sum(solver.Value(v) for v in hard_vars),
                soft_conflicts=sum(solver.Value(v) for v in soft_vars),
            )
        )
        # forbid exactly this selection and look for the next best
        model.AddBoolOr([x[g].Not() for g in chosen])
    return SolveReport(plans, infeasible, dropped)


def _bound_if_chosen(model: cp_model.CpModel, cat: Catalog, g: int, day: int, chosen, which: str):
    """Int expression: the group's earliest start / latest end on `day` when chosen,
    otherwise the neutral value (24h for starts, 0 for ends)."""
    slots = [s for s in cat.groups[g].slots if s.day == day]
    if which == "start":
        value, neutral = min(s.start for s in slots), DAY_MINUTES
    else:
        value, neutral = max(s.end for s in slots), 0
    v = model.NewIntVar(0, DAY_MINUTES, f"{which}{g}_{day}")
    model.Add(v == value).OnlyEnforceIf(chosen)
    model.Add(v == neutral).OnlyEnforceIf(chosen.Not())
    return v
