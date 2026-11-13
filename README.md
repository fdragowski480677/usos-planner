# usos-planner

Schedule planner, USOS API registration assistant and formal-request helper for
students at universities running USOSweb.  A Python rewrite and extension of
[rutek6/CollegePlanner](https://github.com/rutek6/CollegePlanner).

It does four things:

1. **Plans your timetable.** Reads every group of every course you need and finds
   the best conflict-free combinations with an exact CP-SAT solver (OR-Tools).
   Preferences: allowed hours per day, free days, pinned or excluded groups,
   weights for time spent at university, late ends and early starts.
2. **Talks to the USOS API.** OAuth 1.0a login, listing your registration
   rounds, pulling course timetables straight from the API (no HTML scraping),
   and calling `registrations/register` for the plan's courses during an open
   round. Dry-run by default.
3. **Tracks what failed.** Every refused registration becomes a *case* in a
   local SQLite database with the server's reason (limit reached, not entitled, …)
   and the course coordinators pulled from USOS.
4. **Drafts the paperwork.** A web GUI generates a formal Polish e-mail to the
   course coordinator asking for consent, then a *podanie* to the vice-dean for
   student affairs, lets you edit both, and sends them from your own mailbox
   over SMTP. Status of each case is tracked from "open" to "registered".

5. **Puts the plan in your Google Calendar.** Either download an `.ics` with
   weekly recurring events and import it, or let the tool create a dedicated
   calendar named "Plan zajęć" in your Google account and keep it in sync.

## What it deliberately does not do

* **No automation of PE (WF) or language (lektorat) token registrations.** The
  żetony system has no write API, and automating its website breaks most
  universities' registration rules. The planner still schedules around WF/LEK
  groups you pick; the registrar skips them.
* **No polling loops or retry storms.** One `register` call per course per run.
* **No sending without you.** Every message is shown for editing and confirmed
  before SMTP is touched.

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                               # 25 tests

usos-planner plan tests/fixtures/sample.in            # best plans in the terminal
usos-planner plan plan.html --prefs prefs.in --json   # from a saved USOSweb page
usos-planner serve                                    # GUI at http://127.0.0.1:8000
```

### USOS API

1. Copy `config.example.yaml` to `config.yaml`.
2. Register an application at `https://<your-usos-api>/developers/` and paste the
   consumer key and secret. The default `base_url` is the University of Warsaw
   installation; change it for your university.
3. `usos-planner login` opens the PIN flow. The token is stored in `data/`.
4. `usos-planner rounds` lists your rounds; `usos-planner fetch --round <id>`
   writes `plan.in` plus `plan.meta.json` (USOS ids and coordinators).
5. `usos-planner register plan.in --round <id>` is a dry run.
   Add `--live` and type `YES` to really register. Failures appear under
   "Rejestracje i podania" in the GUI.

### Google Calendar

Set `calendar.term_start` / `term_end` in `config.yaml` (or pass `--start` /
`--end`; with a USOS login the dates come from `terms/term` automatically) and
list holidays under `calendar.holidays` so those weeks are skipped.

* **Import file:** `usos-planner export-ics plan.in --prefs prefs.in --out plan.ics`,
  then in Google Calendar: Settings → Import & export → Import. Works for any
  calendar app. The GUI has a "Pobierz .ics" button for each plan.
* **Direct sync:** in [Google Cloud Console](https://console.cloud.google.com/)
  create a project, enable the *Google Calendar API*, create an OAuth client of
  type *Desktop app* and save the downloaded JSON as `google_credentials.json`
  in the project folder (git-ignored). Then
  `pip install -e ".[google]"` and `usos-planner gcal plan.in --prefs prefs.in`.
  The first run opens a browser for consent; afterwards a calendar "Plan zajęć"
  appears under *My calendars*. Re-running replaces only the events the tool
  created, so your own entries in that calendar survive. The GUI button
  "Utwórz / odśwież kalendarz" does the same.

### Input format

The `.in` format of the original project is kept, so existing files work:

```
PRZEDMIOT Analiza III
  ZAJECIA CW
    GRUPA 1
      Pn 08:00-10:00
```

Preferences (`prefs.in`): `PnGodziny 09:00-18:00`, `PtCzyWolne true`,
`Przypnij AnalizaIII CW 2`.

## Layout

```
usos_planner/
  models.py          catalog: courses -> units -> groups -> slots; preferences; plans
  conflicts.py       hard/soft conflict classification
  solver.py          CP-SAT model, returns the k best distinct plans
  inputs/            text_format (.in), usosweb_html (saved page), usos_api (live)
  usos/              OAuth client and registrations wrappers
  registrar.py       executes a plan against a round, records attempts, opens cases
  escalation/        SQLite store, Polish templates, SMTP mailer
  calendar/          ics export, Google Calendar sync, semester date resolution
  web/               FastAPI GUI
  cli.py
```

## Known limitations

* `registrations/register` is course-level; whether a round honours a group
  choice depends on the round type at your university. The first live run should
  be watched in USOSweb.
* Error classification uses keywords from USOS messages; unknown messages land in
  the generic bucket and still open a case.
* The HTML parser targets the `<timetable-entry>` web components of current
  USOSweb; older table-based pages are not supported.
