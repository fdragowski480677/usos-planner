# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

import pytest


@pytest.fixture(autouse=True)
def isolated_locations(tmp_path_factory, monkeypatch):
    """Never let a test read or write the real ~/.config or ~/.local/share."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    monkeypatch.delenv("USOS_PLANNER_CONFIG", raising=False)
    monkeypatch.delenv("USOS_PLANNER_DATA", raising=False)
