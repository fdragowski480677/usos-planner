# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

"""Sends the formal e-mails from your own mailbox.

Two methods (config `mail.method`):
  * "gmail" (default): through the Gmail API with the Google login you already
    have.  No password is stored anywhere; the permission only allows sending.
  * "smtp": any mail server, with an app password.  The server certificate is
    verified, so nobody on the network can pose as the server and catch the password.
A copy of every message is also written to `<data_dir>/sent/` as .eml for your records.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from ..config import MailConfig, write_private


class MailError(RuntimeError):
    pass


def build_message(cfg: MailConfig, to: str, subject: str, body: str, cc: list[str] | None = None) -> EmailMessage:
    msg = EmailMessage()
    sender = cfg.from_address or (cfg.username if cfg.method == "smtp" else "")
    if sender:  # for Gmail an empty From is filled in with the signed-in account
        msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(body, charset="utf-8")
    return msg


def send(cfg: MailConfig, to: str, subject: str, body: str, cc: list[str] | None = None,
         archive_dir: Path | None = None, google_creds=None) -> str:
    msg = build_message(cfg, to, subject, body, cc)
    if cfg.method == "gmail":
        _send_gmail(msg, google_creds)
    elif cfg.method == "smtp":
        _send_smtp(cfg, msg, [to] + list(cc or []))
    else:
        raise MailError(f"unknown mail.method {cfg.method!r}: use 'gmail' or 'smtp'")
    if archive_dir is not None:
        archive_dir.mkdir(parents=True, exist_ok=True)
        name = msg["Message-ID"].strip("<>").replace("@", "_at_") + ".eml"
        write_private(archive_dir / name, bytes(msg).decode("utf-8", errors="replace"))
    return msg["Message-ID"]


def _send_gmail(msg: EmailMessage, creds) -> None:
    from ..calendar.google_sync import explain_api_error, send_gmail

    if creds is None:
        raise MailError("Wysyłka przez Gmail wymaga zalogowania przez Google (przycisk w nagłówku).")
    try:
        send_gmail(creds, bytes(msg))
    except Exception as e:  # HttpError (API disabled, missing permission), RefreshError, network
        raise MailError(explain_api_error(e)) from e


def _send_smtp(cfg: MailConfig, msg: EmailMessage, recipients: list[str]) -> None:
    if not cfg.username or not cfg.password:
        raise MailError("mail.username / mail.password missing (SMTP needs an app password)")
    if cfg.bcc_self and msg["From"]:
        recipients = recipients + [msg["From"]]
    context = ssl.create_default_context()  # verifies the certificate and the host name
    try:
        if cfg.smtp_port == 465:
            smtp = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=30, context=context)
        else:
            smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30)
        with smtp:
            if cfg.smtp_port != 465:
                smtp.starttls(context=context)
            smtp.login(cfg.username, cfg.password)
            smtp.send_message(msg, to_addrs=recipients)
    except (smtplib.SMTPException, OSError) as e:  # ssl.SSLError is an OSError
        raise MailError(f"SMTP failure: {e}") from e
