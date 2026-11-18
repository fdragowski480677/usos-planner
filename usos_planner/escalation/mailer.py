# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

"""SMTP sender.  Uses STARTTLS and an app password from config.yaml.  A copy of
every message is also written to `<data_dir>/sent/` as .eml for your records."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from ..config import MailConfig


class MailError(RuntimeError):
    pass


def build_message(cfg: MailConfig, to: str, subject: str, body: str, cc: list[str] | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = cfg.from_address or cfg.username
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(body, charset="utf-8")
    return msg


def send(cfg: MailConfig, to: str, subject: str, body: str, cc: list[str] | None = None, archive_dir: Path | None = None) -> str:
    if not cfg.username or not cfg.password:
        raise MailError("mail.username / mail.password missing in config.yaml")
    msg = build_message(cfg, to, subject, body, cc)
    recipients = [to] + list(cc or [])
    if cfg.bcc_self:
        recipients.append(msg["From"])
    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(cfg.username, cfg.password)
            smtp.send_message(msg, to_addrs=recipients)
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"SMTP failure: {e}") from e
    if archive_dir is not None:
        archive_dir.mkdir(parents=True, exist_ok=True)
        name = msg["Message-ID"].strip("<>").replace("@", "_at_") + ".eml"
        (archive_dir / name).write_bytes(bytes(msg))
    return msg["Message-ID"]
