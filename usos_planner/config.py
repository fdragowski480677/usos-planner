"""Configuration: `config.yaml` in the project directory (or USOS_PLANNER_CONFIG)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_DATA_DIR = Path(os.environ.get("USOS_PLANNER_DATA", "data"))


@dataclass
class UsosConfig:
    base_url: str = "https://usosapps.uw.edu.pl/"  # installation of your university
    consumer_key: str = ""
    consumer_secret: str = ""
    scopes: list[str] = field(default_factory=lambda: ["studies", "offline_access", "email"])


@dataclass
class MailConfig:
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    username: str = ""
    password: str = ""  # an app password; never your main account password
    from_address: str = ""
    bcc_self: bool = True


@dataclass
class StudentConfig:
    full_name: str = ""
    index_number: str = ""
    programme: str = ""  # e.g. "Informatyka, studia I stopnia"
    year: str = ""  # e.g. "II rok"
    faculty: str = ""
    email: str = ""
    phone: str = ""
    vice_dean_name: str = ""  # e.g. "dr hab. Jan Kowalski, prof. UW"
    vice_dean_email: str = ""
    dean_office_email: str = ""


@dataclass
class CalendarConfig:
    name: str = "Plan zajęć"  # the Google calendar that gets created in your account
    timezone: str = "Europe/Warsaw"
    term_start: str = ""  # ISO date of the first day of classes, e.g. 2025-10-01
    term_end: str = ""  # last day of classes
    holidays: list[str] = field(default_factory=list)  # ISO dates without classes (EXDATE)
    google_credentials_file: str = "google_credentials.json"  # OAuth "Desktop app" client from Google Cloud


@dataclass
class Config:
    usos: UsosConfig = field(default_factory=UsosConfig)
    mail: MailConfig = field(default_factory=MailConfig)
    student: StudentConfig = field(default_factory=StudentConfig)
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    data_dir: Path = DEFAULT_DATA_DIR

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        p = Path(path or os.environ.get("USOS_PLANNER_CONFIG", "config.yaml"))
        cfg = cls()
        if p.exists():
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            for section, klass in (("usos", UsosConfig), ("mail", MailConfig), ("student", StudentConfig), ("calendar", CalendarConfig)):
                if section in raw and raw[section]:
                    setattr(cfg, section, klass(**raw[section]))
            if "data_dir" in raw:
                cfg.data_dir = Path(raw["data_dir"])
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        return cfg
