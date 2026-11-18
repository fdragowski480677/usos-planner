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
pytest                               # 27 tests

usos-planner plan tests/fixtures/sample.in            # 5 best plans in the terminal
usos-planner plan plan.in --max-plans 10              # anything from 1 to 10
usos-planner plan plan.html --prefs prefs.in --json   # from a saved USOSweb page
usos-planner serve                                    # GUI at http://127.0.0.1:8000
```

### Logging in (GUI)

Start `usos-planner serve`, open **Ustawienia** and do each of these once:

* **USOS:** register an application at `https://<your-usos-api>/developers/`
  (a student account is enough) and paste the consumer key and secret. From then
  on the **Zaloguj przez USOS** button in the header redirects you to your
  university's USOS login and straight back.
* **Google:** in Google Cloud Console enable the *Google Calendar API*, create
  an OAuth client of type *Desktop app*, download the JSON and paste its content
  into the Google box. From then on **Zaloguj przez Google** is a normal Google
  sign-in; the calendar sync button needs nothing else.

Both keys identify the *application*, not you; Google and USOS require them for
any third-party login. Everything is stored locally in `config.yaml` and
`data/` (git-ignored).

After logging in, **Pobierz z USOS** lists your registration rounds; one click
pulls all courses of a round with their groups and times and computes the plans.
The **Liczba planów** selector (1–10) on the plan page says how many of the best
alternatives to show; "Przelicz" re-solves with the current preferences and count.

### USOS API from the CLI

The same works without the GUI: `usos-planner login` (PIN flow),
`usos-planner rounds`, `usos-planner fetch --round <id>` (writes `plan.in` and
`plan.meta.json`), `usos-planner register plan.in --round <id>` (dry run;
add `--live` and type `YES` to really register). Failures from live runs appear
under "Rejestracje i podania".

### Google Calendar

Set `calendar.term_start` / `term_end` in `config.yaml` (or pass `--start` /
`--end`; with a USOS login the dates come from `terms/term` automatically) and
list holidays under `calendar.holidays` so those weeks are skipped.

* **Import file:** `usos-planner export-ics plan.in --prefs prefs.in --out plan.ics`,
  then in Google Calendar: Settings → Import & export → Import. Works for any
  calendar app. The GUI has a "Pobierz .ics" button for each plan.
* **Direct sync:** log in with Google in the GUI (see above) and press
  "Utwórz / odśwież kalendarz" next to a plan. A calendar "Plan zajęć" appears
  under *My calendars* in your Google account. Re-running replaces only the
  events the tool created, so your own entries in that calendar survive.
  CLI equivalent: `usos-planner gcal plan.in --prefs prefs.in` (opens a browser
  for consent on first use).

**"Google Calendar API nie jest włączone"** after a successful login means the
OAuth client works but the Calendar API is switched off in that Google Cloud
project. Open *APIs & Services → Library → Google Calendar API → Enable* (the
error message carries a direct link), wait a minute and press the button again.
Logging in never needs the API, which is why the sign-in itself succeeds.

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

## License

MIT, see [LICENSE](LICENSE). usos-planner is based on
[CollegePlanner](https://github.com/rutek6/CollegePlanner) by Jan Rutecki: it keeps
that project's `.in` format and penalty model. [NOTICE](NOTICE) lists what was
taken from it, plus the third-party libraries and services the tool uses.
