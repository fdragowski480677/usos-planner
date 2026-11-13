"""Create (or reuse) a dedicated Google Calendar in the user's account and fill it
with the plan's recurring events.

Setup (once): in Google Cloud Console create a project, enable the *Google Calendar
API*, create an OAuth client of type "Desktop app", download the JSON and save it as
`google_credentials.json` next to config.yaml.  The first sync opens a browser for
consent; the refresh token is kept in `<data_dir>/google_token.json`.

Events written by us carry the private extended property `usos_planner=1`, so a
re-sync removes only our events and leaves anything the user added by hand.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ..models import Catalog, Plan
from .ics import DEFAULT_TZ, ICAL_DAYS, plan_events

SCOPES = ["https://www.googleapis.com/auth/calendar"]
MARKER = "usos_planner"


class GoogleCalendarError(RuntimeError):
    pass


def get_service(credentials_file: Path, token_file: Path):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:  # pragma: no cover
        raise GoogleCalendarError("install the google extra: pip install -e '.[google]'") from e

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
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
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json())
        token_file.chmod(0o600)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def ensure_calendar(service, name: str, tz: str = DEFAULT_TZ) -> dict:
    """Return the calendar named `name` owned by the user, creating it if missing."""
    page = None
    while True:
        resp = service.calendarList().list(pageToken=page, minAccessRole="owner").execute()
        for item in resp.get("items", []):
            if item.get("summary") == name:
                return item
        page = resp.get("nextPageToken")
        if not page:
            break
    created = service.calendars().insert(body={"summary": name, "timeZone": tz}).execute()
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


def sync_plan(service, cat: Catalog, plan: Plan, start: date, end: date, calendar_name: str = "Plan zajęć",
              tz: str = DEFAULT_TZ, exdates: list[date] | None = None) -> dict:
    """Idempotent: wipes our previous events in the calendar and inserts the plan."""
    cal = ensure_calendar(service, calendar_name, tz)
    removed = clear_our_events(service, cal["id"])
    inserted = 0
    for ev in plan_events(cat, plan, start, end, exdates):
        service.events().insert(calendarId=cal["id"], body=_gcal_body(ev, tz)).execute()
        inserted += 1
    return {"calendar_id": cal["id"], "calendar_name": cal.get("summary", calendar_name), "removed": removed, "inserted": inserted}
