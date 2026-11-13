"""Core data model.

A *catalog* is a flat structure: courses own units (one unit = one class type of a
course, e.g. the lab part of "Analiza matematyczna"), units own groups, groups own
time slots.  Objects refer to each other by index into the catalog lists; a *plan*
picks exactly one group per unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

DAY_NAMES = ["Pn", "Wt", "Sr", "Cz", "Pt", "So", "Nd"]
DAY_NAMES_PL = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]


class ClassType(str, Enum):
    WYK = "WYK"  # lecture
    CW = "CW"  # tutorial
    CWW = "CWW"
    LAB = "LAB"
    SEM = "SEM"
    WF = "WF"  # physical education
    LEK = "LEK"  # language course
    KON = "KON"
    WAR = "WAR"
    OTHER = "OTHER"

    @classmethod
    def parse(cls, text: str) -> "ClassType":
        t = text.strip().upper()
        try:
            return cls(t)
        except ValueError:
            return cls.OTHER

    @property
    def is_lecture(self) -> bool:
        return self is ClassType.WYK


@dataclass(frozen=True)
class Slot:
    day: int  # 0 = Monday ... 6 = Sunday
    start: int  # minutes from midnight
    end: int

    def overlaps(self, other: "Slot") -> bool:
        return self.day == other.day and self.start < other.end and other.start < self.end

    def __str__(self) -> str:
        return f"{DAY_NAMES[self.day]} {fmt_time(self.start)}-{fmt_time(self.end)}"


@dataclass
class Group:
    number: int  # USOS group number; a label, not an id
    unit_id: int  # index into Catalog.units
    slots: list[Slot] = field(default_factory=list)
    lecturer: str = ""
    usos_unit_id: str | None = None  # USOS "unit_id" if known (needed to register)
    limit: int | None = None
    registered: int | None = None

    def conflicts_with(self, other: "Group") -> bool:
        return any(a.overlaps(b) for a in self.slots for b in other.slots)

    @property
    def is_full(self) -> bool:
        return self.limit is not None and self.registered is not None and self.registered >= self.limit


@dataclass
class Unit:
    class_type: ClassType
    course_id: int  # index into Catalog.courses
    group_ids: list[int] = field(default_factory=list)


@dataclass
class Course:
    name: str
    unit_ids: list[int] = field(default_factory=list)
    usos_course_id: str | None = None  # e.g. "1000-211bAM1"
    term_id: str | None = None  # e.g. "2025Z"
    coordinators: list[dict] = field(default_factory=list)  # {"name", "email", "user_id"}


@dataclass
class Catalog:
    courses: list[Course] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)

    # ----- builders -------------------------------------------------------
    def add_course(self, name: str, **kw) -> int:
        self.courses.append(Course(name=name, **kw))
        return len(self.courses) - 1

    def add_unit(self, course_id: int, class_type: ClassType) -> int:
        self.units.append(Unit(class_type=class_type, course_id=course_id))
        uid = len(self.units) - 1
        self.courses[course_id].unit_ids.append(uid)
        return uid

    def add_group(self, unit_id: int, number: int, slots: Iterable[Slot], **kw) -> int:
        self.groups.append(Group(number=number, unit_id=unit_id, slots=list(slots), **kw))
        gid = len(self.groups) - 1
        self.units[unit_id].group_ids.append(gid)
        return gid

    # ----- lookups --------------------------------------------------------
    def course_of_group(self, gid: int) -> Course:
        return self.courses[self.units[self.groups[gid].unit_id].course_id]

    def unit_of_group(self, gid: int) -> Unit:
        return self.units[self.groups[gid].unit_id]

    def describe_group(self, gid: int) -> str:
        g = self.groups[gid]
        u = self.unit_of_group(gid)
        c = self.course_of_group(gid)
        return f"{c.name} {u.class_type.value} gr. {g.number}"

    def find_group(self, course_name: str, class_type: ClassType, number: int) -> int | None:
        key = course_name.replace(" ", "").lower()
        for gid, g in enumerate(self.groups):
            u = self.unit_of_group(gid)
            c = self.course_of_group(gid)
            if u.class_type == class_type and g.number == number and c.name.replace(" ", "").lower() == key:
                return gid
        return None


@dataclass
class Preferences:
    """What the student wants from a plan.

    hours[day] = (earliest acceptable start, latest acceptable end) in minutes.
    free_days   = days on which no classes are acceptable.
    pinned      = group ids that must be chosen (at most one per unit matters).
    excluded    = group ids that must not be chosen.
    """

    hours: list[tuple[int, int]] = field(default_factory=lambda: [(0, 24 * 60)] * 7)
    free_days: set[int] = field(default_factory=set)
    pinned: set[int] = field(default_factory=set)
    excluded: set[int] = field(default_factory=set)
    # objective weights; penalties are integers so CP-SAT can use them directly
    w_hard_conflict: int = 1_000_000
    w_soft_conflict: int = 100_000
    w_time_spent: int = 5  # per minute between first and last class of a day
    w_late_end: int = 5  # per minute after `late_after`
    w_early_start: int = 0  # per minute before `early_before`
    w_day_used: int = 0  # per day with any class (set > 0 to prefer compact weeks)
    late_after: int = 16 * 60
    early_before: int = 10 * 60

    def group_allowed(self, g: Group) -> bool:
        for s in g.slots:
            if s.day in self.free_days:
                return False
            lo, hi = self.hours[s.day]
            if s.start < lo or s.end > hi:
                return False
        return True


@dataclass
class Plan:
    chosen: list[int]  # group id per unit, ordered like Catalog.units
    penalty: int
    hard_conflicts: int = 0
    soft_conflicts: int = 0

    def slots(self, cat: Catalog) -> list[tuple[int, Slot]]:
        out = [(gid, s) for gid in self.chosen for s in cat.groups[gid].slots]
        out.sort(key=lambda p: (p[1].day, p[1].start, p[1].end))
        return out

    def to_dict(self, cat: Catalog) -> dict:
        return {
            "penalty": self.penalty,
            "hard_conflicts": self.hard_conflicts,
            "soft_conflicts": self.soft_conflicts,
            "groups": [
                {
                    "course": cat.course_of_group(gid).name,
                    "usos_course_id": cat.course_of_group(gid).usos_course_id,
                    "term_id": cat.course_of_group(gid).term_id,
                    "type": cat.unit_of_group(gid).class_type.value,
                    "number": cat.groups[gid].number,
                    "usos_unit_id": cat.groups[gid].usos_unit_id,
                    "slots": [str(s) for s in cat.groups[gid].slots],
                }
                for gid in self.chosen
            ],
        }


def fmt_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_time(text: str) -> int:
    h, m = text.strip().split(":")
    h, m = int(h), int(m)
    if not (0 <= h <= 24 and 0 <= m <= 59):
        raise ValueError(f"bad time {text!r}")
    return h * 60 + m


def parse_time_range(text: str) -> tuple[int, int]:
    a, b = text.replace("—", "-").replace("–", "-").split("-", 1)
    start, end = parse_time(a), parse_time(b)
    if end <= start:
        raise ValueError(f"end before start in {text!r}")
    return start, end
