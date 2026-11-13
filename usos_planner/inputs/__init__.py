"""Catalog sources: the legacy .in text format, saved USOSweb HTML, and the USOS API."""

from pathlib import Path

from ..models import Catalog


def load_catalog(path: str | Path) -> Catalog:
    p = Path(path)
    if p.suffix.lower() in {".html", ".htm"}:
        from .usosweb_html import parse_usosweb_html

        return parse_usosweb_html(p)
    from .text_format import read_catalog

    return read_catalog(p.read_text(encoding="utf-8"))
