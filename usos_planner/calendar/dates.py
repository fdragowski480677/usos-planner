# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

"""Resolve the semester date range: explicit arguments > config.yaml > USOS API `terms/term`."""

from __future__ import annotations

from datetime import date

from ..config import CalendarConfig
from ..models import Catalog


class TermDatesError(ValueError):
    pass


def parse_iso(text: str | None) -> date | None:
    text = (text or "").strip()
    return date.fromisoformat(text) if text else None


def resolve_term_dates(cal_cfg: CalendarConfig, cat: Catalog | None = None, start: str | None = None,
                       end: str | None = None, client=None) -> tuple[date, date]:
    s, e = parse_iso(start) or parse_iso(cal_cfg.term_start), parse_iso(end) or parse_iso(cal_cfg.term_end)
    if (s is None or e is None) and client is not None and cat is not None:
        term_id = next((c.term_id for c in cat.courses if c.term_id), None)
        if term_id:
            try:
                info = client.call("terms/term", term_id=term_id, fields="id|start_date|end_date")
                s = s or parse_iso(info.get("start_date"))
                e = e or parse_iso(info.get("end_date"))
            except Exception:
                pass
    if s is None or e is None:
        raise TermDatesError("semester dates unknown: pass --start/--end or set calendar.term_start/term_end in config.yaml")
    if e < s:
        raise TermDatesError("term_end is before term_start")
    return s, e


def holidays(cal_cfg: CalendarConfig) -> list[date]:
    return [date.fromisoformat(d) for d in cal_cfg.holidays if d]
