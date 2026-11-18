# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

"""Configuration and the private places where secrets are kept.

Secrets (USOS consumer secret, Google OAuth client, login tokens) live outside
the project folder, in directories only you can open:

    config   $XDG_CONFIG_HOME/usos-planner/config.yaml   (default ~/.config/usos-planner)
    data     $XDG_DATA_HOME/usos-planner/                 (default ~/.local/share/usos-planner)

Directories are created with mode 0700 and secret files with 0600.
USOS_PLANNER_CONFIG and USOS_PLANNER_DATA override the two paths, and a
config.yaml in the current directory is still read for backward compatibility.
On WSL, folders under /mnt/<drive> belong to Windows and ignore these modes;
`Config.insecure_paths()` reports such locations so the GUI can warn.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

APP = "usos-planner"


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP


def default_config_path() -> Path:
    env = os.environ.get("USOS_PLANNER_CONFIG")
    if env:
        return Path(env)
    legacy = Path("config.yaml")
    return legacy if legacy.exists() else config_home() / "config.yaml"


def default_data_dir() -> Path:
    env = os.environ.get("USOS_PLANNER_DATA")
    return Path(env) if env else data_home()


def on_windows_drive(path: Path) -> bool:
    """True for WSL mounts of Windows drives (/mnt/c/...), where chmod has no effect."""
    return bool(re.match(r"^/mnt/[a-z](/|$)", str(Path(path).expanduser().resolve())))


def private_dir(path: Path) -> Path:
    """Create `path` if needed and make it accessible to its owner only."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def write_private(path: Path, text: str) -> None:
    """Write `text` to a file only the owner can read.  The file is created with
    mode 0600 before anything is written, so the secret is never world-readable."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        os.chmod(path, 0o600)  # O_CREAT's mode does not apply to an existing file
    except OSError:
        pass


@dataclass
class UsosConfig:
    base_url: str = "https://usosapps.uw.edu.pl/"  # installation of your university
    consumer_key: str = ""
    consumer_secret: str = ""
    scopes: list[str] = field(default_factory=lambda: ["studies", "offline_access", "email"])


@dataclass
class MailConfig:
    method: str = "gmail"  # "gmail": Gmail API via the Google login, no password stored; "smtp": server + app password
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587  # 587 = STARTTLS, 465 = TLS from the start
    username: str = ""
    password: str = ""  # SMTP only: an app password, never your main account password
    from_address: str = ""
    bcc_self: bool = True  # SMTP only; Gmail keeps a copy in "Sent" anyway


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
    google_credentials_file: str = "google_credentials.json"  # OAuth "Desktop app" client; relative = next to config.yaml


@dataclass
class Config:
    usos: UsosConfig = field(default_factory=UsosConfig)
    mail: MailConfig = field(default_factory=MailConfig)
    student: StudentConfig = field(default_factory=StudentConfig)
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    data_dir: Path = field(default_factory=default_data_dir)
    path: Path | None = field(default=None, repr=False, compare=False)  # where config.yaml is read from / saved to

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        p = Path(path) if path else default_config_path()
        cfg = cls(path=p)
        if p.exists():
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            for section, klass in (("usos", UsosConfig), ("mail", MailConfig), ("student", StudentConfig), ("calendar", CalendarConfig)):
                if section in raw and raw[section]:
                    setattr(cfg, section, klass(**raw[section]))
            if raw.get("data_dir"):
                d = Path(raw["data_dir"]).expanduser()
                cfg.data_dir = d if d.is_absolute() else p.parent / d
        private_dir(cfg.data_dir)
        return cfg

    def config_dir(self) -> Path:
        return (self.path or default_config_path()).parent

    def google_client_path(self) -> Path:
        p = Path(self.calendar.google_credentials_file).expanduser()
        return p if p.is_absolute() else self.config_dir() / p

    def insecure_paths(self) -> list[Path]:
        """Secret-holding locations that sit on a Windows drive under WSL."""
        candidates = [self.path or default_config_path(), self.data_dir, self.google_client_path()]
        return [c for c in candidates if on_windows_drive(c)]

    def save(self, path: str | Path | None = None) -> Path:
        p = Path(path) if path else (self.path or default_config_path())
        if p.parent == config_home():
            private_dir(p.parent)
        data = {
            "usos": asdict(self.usos),
            "mail": asdict(self.mail),
            "student": asdict(self.student),
            "calendar": asdict(self.calendar),
            "data_dir": str(self.data_dir),
        }
        write_private(p, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        self.path = p
        return p
