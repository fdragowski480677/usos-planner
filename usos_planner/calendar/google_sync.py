# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franciszek Drągowski

"""Google side of usos-planner: sign-in, a dedicated calendar for the plan, and
sending the formal e-mails through Gmail.

Permissions are kept as narrow as Google allows (see SCOPES):
  * calendar.app.created - only calendars this app creates, not your other calendars;
  * gmail.send           - send mail as you, with no access to read your mailbox;
  * openid + email       - your address, shown in the header after logging in.
Older versions asked for full calendar access; `migrate_legacy_token` revokes such a
token at Google and deletes it, so the next login grants only the narrow set.

The id of the calendar we created is remembered in `<data_dir>/google_calendar.json`
because the narrow scope cannot list your calendars to find it by name.  Events we
write carry the private extended property `usos_planner=1`, so a re-sync removes only
our events and leaves anything you added by hand.
"""

from __future__ import annotations

import base64
import json
import os
import re
from datetime import date
from pathlib import Path

from ..config import write_private
from ..models import Catalog, Plan
from .ics import DEFAULT_TZ, ICAL_DAYS, plan_events

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
SCOPES = [CALENDAR_SCOPE, GMAIL_SEND_SCOPE, "openid", "https://www.googleapis.com/auth/userinfo.email"]
LEGACY_SCOPE = "https://www.googleapis.com/auth/calendar"  # full access, asked for before 0.2
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
MARKER = "usos_planner"

# Google's consent screen lets the user untick single permissions.  oauthlib would then
# reject the whole login ("Scope has changed"); we accept it and let the feature that
# lacks its permission explain itself instead (see explain_api_error).
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class GoogleCalendarError(RuntimeError):
    pass


def http_status(exc: BaseException) -> int | None:
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


_API_HOSTS = {"Google Calendar API": "calendar-json.googleapis.com", "Gmail API": "gmail.googleapis.com"}


def explain_api_error(exc: BaseException) -> str:
    """Turn a googleapiclient/google-auth exception into one actionable sentence (Polish, for the GUI)."""
    text = str(exc)
    m = re.search(r"((?:Google Calendar|Gmail) API) has not been used in project (\d+) before or it is disabled", text)
    if m:
        api, project = m.group(1), m.group(2)
        link = re.search(r"https://console\.developers\.google\.com/apis/api/[\w.-]+/overview\?project=\d+", text)
        url = link.group(0) if link else f"https://console.developers.google.com/apis/api/{_API_HOSTS[api]}/overview?project={project}"
        return f"{api} nie jest włączone w Twoim projekcie Google Cloud. Włącz je tutaj: {url} i po chwili spróbuj ponownie."
    low = text.lower()
    if "insufficient" in low and ("scope" in low or "permission" in low):
        return ("Brakuje uprawnienia Google do tej czynności. Wyloguj się z Google i zaloguj ponownie, "
                "zaznaczając na ekranie zgody wszystkie pola (Kalendarz i wysyłanie poczty).")
    if "invalid_grant" in text or "RefreshError" in type(exc).__name__:
        return "Sesja Google wygasła lub została unieważniona. Wyloguj się z Google i zaloguj ponownie."
    reason = re.search(r'returned "([^"]+)"', text)
    return f"Google: {reason.group(1) if reason else text[:300]}"


def _save_token(token_file: Path, creds) -> None:
    write_private(token_file, creds.to_json())


def get_service(credentials_file: Path, token_file: Path, calendar_store: Path | None = None, calendar_name: str = "Plan zajęć"):
    """Calendar service for the CLI; the first run opens a browser for consent."""
    try:
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as e:  # pragma: no cover
        raise GoogleCalendarError("install the google extra: pip install -e '.[google]'") from e

    migrate_legacy_token(token_file, calendar_store, calendar_name)
    creds = load_credentials(token_file, refresh=False)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_file.exists():
                raise GoogleCalendarError(
                    f"{credentials_file} not found: download an OAuth 'Desktop app' client JSON from Google Cloud Console"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)
        _save_token(token_file, creds)
    return service_from_credentials(creds)


def load_credentials(token_file: Path, refresh: bool = True):
    """Stored credentials, refreshed if needed; None when not logged in."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as e:  # pragma: no cover
        raise GoogleCalendarError("install the google extra: pip install -e '.[google]'") from e
    if not token_file.exists():
        return None
    # no scopes argument: keep exactly what was granted, so a refresh never asks for more
    creds = Credentials.from_authorized_user_file(str(token_file))
    if not refresh:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:  # revoked or client changed: treat as logged out
            return None
        _save_token(token_file, creds)
    return creds if creds.valid else None


def web_flow(credentials_file: Path, redirect_uri: str):
    """OAuth flow for the browser GUI (redirect instead of local server)."""
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError as e:  # pragma: no cover
        raise GoogleCalendarError("install the google extra: pip install -e '.[google]'") from e
    if not credentials_file.exists():
        raise GoogleCalendarError("Google OAuth client is not configured yet (Settings -> Google)")
    return Flow.from_client_secrets_file(str(credentials_file), scopes=SCOPES, redirect_uri=redirect_uri)


def service_from_credentials(creds):
    from googleapiclient.discovery import build

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def gmail_service(creds):
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def account_email(creds) -> str:
    """Best effort: the signed-in address, from the OpenID userinfo endpoint."""
    try:
        from google.auth.transport.requests import AuthorizedSession

        r = AuthorizedSession(creds).get(USERINFO_URL, timeout=10)
        return r.json().get("email", "") if r.ok else ""
    except Exception:
        return ""


def revoke_token(token: str) -> bool:
    """Ask Google to invalidate a token and the whole grant behind it.  Best effort."""
    if not token:
        return False
    try:
        import requests

        r = requests.post(REVOKE_URL, data={"token": token}, timeout=10,
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        return r.status_code == 200
    except Exception:
        return False


def send_gmail(creds, raw_message: bytes) -> dict:
    """Send an RFC 822 message as the signed-in user (needs the gmail.send permission)."""
    raw = base64.urlsafe_b64encode(raw_message).decode("ascii")
    return gmail_service(creds).users().messages().send(userId="me", body={"raw": raw}).execute()


def migrate_legacy_token(token_file: Path, calendar_store: Path | None, calendar_name: str = "Plan zajęć") -> bool:
    """If `token_file` holds a full-calendar-access token from an older version:
    remember the id of the plan calendar (so it keeps being reused), revoke the
    token at Google and delete it.  Returns True when a migration happened."""
    if not token_file.exists():
        return False
    try:
        data = json.loads(token_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if LEGACY_SCOPE not in (data.get("scopes") or []):
        return False
    if calendar_store is not None and not calendar_store.exists():
        try:
            creds = load_credentials(token_file)
            if creds is not None:
                svc = service_from_credentials(creds)
                page = None
                while True:
                    resp = svc.calendarList().list(pageToken=page, minAccessRole="owner").execute()
                    found = next((c for c in resp.get("items", []) if c.get("summary") == calendar_name), None)
                    if found:
                        write_private(calendar_store, json.dumps({"id": found["id"], "summary": calendar_name}))
                        break
                    page = resp.get("nextPageToken")
                    if not page:
                        break
        except Exception:
            pass  # not fatal: the next sync simply creates a fresh calendar
    revoke_token(data.get("refresh_token") or data.get("token") or "")
    token_file.unlink(missing_ok=True)
    return True


def _stored_calendar_id(store: Path | None) -> str:
    if store is None or not store.exists():
        return ""
    try:
        return json.loads(store.read_text(encoding="utf-8")).get("id", "")
    except (OSError, ValueError):
        return ""


def ensure_calendar(service, name: str, store: Path | None, tz: str = DEFAULT_TZ) -> dict:
    """The calendar this app created (id remembered in `store`), created on first use.
    Renamed to `name` if the setting changed; recreated if you deleted it."""
    cal_id = _stored_calendar_id(store)
    if cal_id:
        try:
            cal = service.calendars().get(calendarId=cal_id).execute()
            if cal.get("summary") != name:
                cal = service.calendars().patch(calendarId=cal_id, body={"summary": name}).execute()
            return cal
        except Exception as e:
            if http_status(e) not in (403, 404, 410):
                raise
    created = service.calendars().insert(body={"summary": name, "timeZone": tz}).execute()
    if store is not None:
        write_private(store, json.dumps({"id": created["id"], "summary": name}))
    return created


def clear_our_events(service, calendar_id: str) -> int:
    removed, page = 0, None
    while True:
        resp = service.events().list(
            calendarId=calendar_id, privateExtendedProperty=f"{MARKER}=1", pageToken=page,
            showDeleted=False, singleEvents=False, maxResults=250,
        ).execute()
        for ev in resp.get("items", []):
            service.events().delete(calendarId=calendar_id, eventId=ev["id"]).execute()
            removed += 1
        page = resp.get("nextPageToken")
        if not page:
            return removed


def _gcal_body(ev: dict, tz: str) -> dict:
    def ts(d: date, minutes: int) -> str:
        return f"{d.isoformat()}T{minutes // 60:02d}:{minutes % 60:02d}:00"

    rrule = f"RRULE:FREQ=WEEKLY;BYDAY={ICAL_DAYS[ev['weekday']]};UNTIL={ev['until']:%Y%m%d}T235959Z"
    recurrence = [rrule]
    for d in ev["exdates"]:
        recurrence.append(f"EXDATE;TZID={tz}:{d:%Y%m%d}T{ev['start_min'] // 60:02d}{ev['start_min'] % 60:02d}00")
    return {
        "summary": ev["summary"],
        "description": ev["description"],
        "start": {"dateTime": ts(ev["first"], ev["start_min"]), "timeZone": tz},
        "end": {"dateTime": ts(ev["first"], ev["end_min"]), "timeZone": tz},
        "recurrence": recurrence,
        "extendedProperties": {"private": {MARKER: "1", "uid": ev["uid"]}},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 15}]},
    }


def sync_plan(service, cat: Catalog, plan: Plan, start: date, end: date, *, calendar_store: Path | None,
              calendar_name: str = "Plan zajęć", tz: str = DEFAULT_TZ, exdates: list[date] | None = None) -> dict:
    """Idempotent: wipes our previous events in the calendar and inserts the plan."""
    cal = ensure_calendar(service, calendar_name, calendar_store, tz)
    removed = clear_our_events(service, cal["id"])
    inserted = 0
    for ev in plan_events(cat, plan, start, end, exdates):
        service.events().insert(calendarId=cal["id"], body=_gcal_body(ev, tz)).execute()
        inserted += 1
    return {"calendar_id": cal["id"], "calendar_name": cal.get("summary", calendar_name), "removed": removed, "inserted": inserted}
