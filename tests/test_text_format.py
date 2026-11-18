# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

from pathlib import Path

import pytest

from usos_planner.inputs.text_format import InputError, read_catalog, read_prefs, write_catalog, write_prefs
from usos_planner.models import ClassType

FIX = Path(__file__).parent / "fixtures"


def test_roundtrip():
    text = (FIX / "sample.in").read_text()
    cat = read_catalog(text)
    assert [c.name for c in cat.courses] == ["Analiza III", "Bazy danych"]
    assert len(cat.units) == 3 and len(cat.groups) == 6
    assert cat.units[1].class_type is ClassType.CW
    again = read_catalog(write_catalog(cat))
    assert write_catalog(again) == write_catalog(cat)


@pytest.mark.parametrize("bad", ["ZAJECIA CW", "PRZEDMIOT X\nGRUPA 1", "PRZEDMIOT X\nZAJECIA FOO", "PRZEDMIOT X\nZAJECIA CW\nGRUPA 1\nPn 10:00-09:00"])
def test_errors(bad):
    with pytest.raises(InputError):
        read_catalog(bad)


def test_prefs():
    cat = read_catalog((FIX / "sample.in").read_text())
    prefs = read_prefs("PnGodziny 09:00-18:00\nPtCzyWolne true\nPrzypnij AnalizaIII CW 2\n", cat)
    assert prefs.hours[0] == (9 * 60, 18 * 60)
    assert prefs.free_days == {4}
    assert prefs.pinned == {cat.find_group("Analiza III", ClassType.CW, 2)}
    text = write_prefs(prefs, cat)
    assert "Przypnij AnalizaIII CW 2" in text
    assert read_prefs(text, cat).pinned == prefs.pinned
    with pytest.raises(InputError):
        read_prefs("Przypnij Nope CW 1", cat)
