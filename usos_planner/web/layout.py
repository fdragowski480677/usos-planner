"""Place a plan's slots on the weekly grid: overlapping events share the width of
their column instead of hiding each other, and every event knows how bad its
overlap is (none / soft = a lecture is involved / hard)."""

from __future__ import annotations

from dataclasses import dataclass

from ..conflicts import Conflict, conflict_between
from ..models import Catalog, Plan, Slot

SEVERITY = {Conflict.NONE: "", Conflict.SOFT: "soft", Conflict.HARD: "hard"}


@dataclass
class Placed:
    gid: int
    slot: Slot
    col: int  # 0-based column inside its cluster of overlapping events
    cols: int  # how many columns the cluster needs
    severity: str  # "", "soft" or "hard"

    @property
    def left_pct(self) -> float:
        return 100.0 * self.col / self.cols

    @property
    def width_pct(self) -> float:
        return 100.0 / self.cols


def layout_plan(cat: Catalog, plan: Plan) -> dict[int, list[Placed]]:
    """Per weekday (0-6): the plan's events with column positions and conflict severity."""
    by_day: dict[int, list[tuple[int, Slot]]] = {}
    for gid, slot in plan.slots(cat):
        by_day.setdefault(slot.day, []).append((gid, slot))

    out: dict[int, list[Placed]] = {}
    for day, items in by_day.items():
        items.sort(key=lambda p: (p[1].start, p[1].end))
        placed: list[Placed] = []
        cluster: list[Placed] = []
        column_ends: list[int] = []  # last end time per column of the current cluster
        cluster_end = -1
        for gid, slot in items:
            if slot.start >= cluster_end and cluster:
                _finish(cluster, len(column_ends))
                cluster, column_ends = [], []
            col = next((i for i, end in enumerate(column_ends) if end <= slot.start), None)
            if col is None:
                col = len(column_ends)
                column_ends.append(slot.end)
            else:
                column_ends[col] = slot.end
            cluster_end = max(cluster_end, slot.end)
            p = Placed(gid, slot, col, 1, _severity(cat, gid, slot, items))
            cluster.append(p)
            placed.append(p)
        if cluster:
            _finish(cluster, len(column_ends))
        out[day] = placed
    return out


def _finish(cluster: list[Placed], cols: int) -> None:
    for p in cluster:
        p.cols = cols


def _severity(cat: Catalog, gid: int, slot: Slot, items: list[tuple[int, Slot]]) -> str:
    worst = Conflict.NONE
    for other_gid, other in items:
        if other_gid != gid and slot.overlaps(other):
            worst = max(worst, conflict_between(cat, gid, other_gid))
    return SEVERITY[worst]
