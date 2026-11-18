# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

"""Pairwise conflict classification between groups.

Overlapping groups conflict.  If either group is a lecture the conflict is SOFT
(lectures are rarely compulsory), otherwise HARD.  Groups of the same unit never
need checking because a plan picks only one of them.
"""

from __future__ import annotations

from enum import IntEnum

from .models import Catalog


class Conflict(IntEnum):
    NONE = 0
    SOFT = 1
    HARD = 2


def conflict_between(cat: Catalog, a: int, b: int) -> Conflict:
    ga, gb = cat.groups[a], cat.groups[b]
    if ga.unit_id == gb.unit_id or not ga.conflicts_with(gb):
        return Conflict.NONE
    if cat.unit_of_group(a).class_type.is_lecture or cat.unit_of_group(b).class_type.is_lecture:
        return Conflict.SOFT
    return Conflict.HARD


def conflict_pairs(cat: Catalog, candidate_ids: list[int] | None = None) -> dict[tuple[int, int], Conflict]:
    """Sparse map of (a, b) with a < b -> conflict kind, only for conflicting pairs."""
    ids = candidate_ids if candidate_ids is not None else list(range(len(cat.groups)))
    out: dict[tuple[int, int], Conflict] = {}
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            k = conflict_between(cat, a, b)
            if k != Conflict.NONE:
                out[(min(a, b), max(a, b))] = k
    return out
