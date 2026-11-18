# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

from pathlib import Path

from usos_planner.inputs.usosweb_html import parse_usosweb_html
from usos_planner.models import ClassType, Slot

FIX = Path(__file__).parent / "fixtures"


def test_parse_usosweb():
    warnings = []
    cat = parse_usosweb_html(FIX / "usosweb.html", warn=warnings.append)
    assert [c.name for c in cat.courses] == ["Analiza III", "Bazy danych"]
    cw = cat.find_group("Analiza III", ClassType.CW, 1)
    assert cat.groups[cw].slots == [Slot(0, 8 * 60, 10 * 60)]
    assert cat.groups[cw].lecturer == "dr Anna Nowak"
    wyk = cat.find_group("Analiza III", ClassType.WYK, 1)
    assert cat.groups[wyk].slots == [Slot(0, 10 * 60 + 15, 12 * 60)]  # em-dash range
    assert cat.groups[wyk].lecturer == ""  # no leak from the previous entry
    lab = cat.find_group("Bazy danych", ClassType.LAB, 2)
    assert cat.groups[lab].slots == [Slot(2, 12 * 60, 14 * 60)]  # duplicate weeks collapsed
    assert cat.groups[lab].lecturer == "mgr Piotr Zieliński"
    assert any("Broken" in w for w in warnings)
