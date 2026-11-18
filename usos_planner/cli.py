"""Command line interface.  `usos-planner --help`."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from .config import Config
from .inputs import load_catalog
from .inputs.text_format import InputError, read_prefs, write_catalog
from .models import Preferences
from .solver import MAX_PLANS, solve


def _client(cfg: Config):
    from .usos.client import UsosClient

    return UsosClient(cfg.usos, cfg.data_dir / "usos_token.json")


def _plans_count(value: str) -> int:
    n = int(value)
    if not 1 <= n <= MAX_PLANS:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_PLANS}")
    return n


def cmd_plan(args, cfg):
    cat = load_catalog(args.source)
    prefs = Preferences()
    if args.prefs:
        prefs = read_prefs(Path(args.prefs).read_text(encoding="utf-8"), cat)
    report = solve(cat, prefs, max_plans=args.max_plans)
    if report.infeasible_units:
        for u in report.infeasible_units:
            unit = cat.units[u]
            print(f"no allowed group for {cat.courses[unit.course_id].name} {unit.class_type.value}", file=sys.stderr)
        return 2
    if args.json:
        json.dump([p.to_dict(cat) for p in report.plans], sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    for i, p in enumerate(report.plans, 1):
        print(f"=== Plan {i}: penalty {p.penalty}, hard conflicts {p.hard_conflicts}, soft {p.soft_conflicts}")
        day = None
        for gid, s in p.slots(cat):
            if s.day != day:
                day = s.day
                print(f"  {str(s).split()[0]}")
            print(f"      {str(s).split(' ', 1)[1]}  {cat.describe_group(gid)}")
    return 0


def cmd_convert(args, cfg):
    cat = load_catalog(args.source)
    sys.stdout.write(write_catalog(cat))
    return 0


def cmd_login(args, cfg):
    client = _client(cfg)
    url, req = client.begin_login()
    print("Open this URL, log in to USOS, allow access, and paste the PIN:\n  " + url)
    pin = input("PIN: ")
    client.finish_login(req, pin)
    me = client.whoami()
    print(f"Logged in as {me.get('first_name')} {me.get('last_name')} ({me.get('student_number')})")
    return 0


def cmd_rounds(args, cfg):
    from .usos.registrations import Registrations

    reg = Registrations(_client(cfg))
    start = date.today() - timedelta(days=7)
    end = start + timedelta(days=30)
    rounds = reg.search_rounds(start.isoformat(), end.isoformat(), user_related=True)
    for r in rounds:
        print(json.dumps(r, ensure_ascii=False))
    if not rounds:
        print("no registration rounds for you in the next month")
    return 0


def cmd_fetch(args, cfg):
    from .inputs.usos_api import catalog_from_api
    from .usos.registrations import Registrations

    reg = Registrations(_client(cfg))
    pairs: list[tuple[str, str]] = []
    if args.round:
        for entry in reg.round_courses(args.round):
            course = entry.get("course") or {}
            cid = course.get("id") or entry.get("course_id")
            term = entry.get("term_id") or args.term
            if cid and term:
                pairs.append((cid, term))
    for c in args.course or []:
        pairs.append((c, args.term))
    if not pairs:
        print("give --round ROUND_ID and/or --course CODE --term 2025Z", file=sys.stderr)
        return 2
    cat = catalog_from_api(reg, pairs)
    text = write_catalog(cat)
    Path(args.out).write_text(text, encoding="utf-8")
    meta = {c.usos_course_id: {"term_id": c.term_id, "name": c.name, "coordinators": c.coordinators} for c in cat.courses}
    Path(args.out).with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out} ({len(cat.courses)} courses, {len(cat.groups)} groups) and {Path(args.out).with_suffix('.meta.json')}")
    return 0


def cmd_register(args, cfg):
    from .escalation.store import CaseStore
    from .registrar import register_plan

    cat = load_catalog(args.source)
    _restore_meta(cat, Path(args.source))  # USOS ids written by `fetch`
    prefs = read_prefs(Path(args.prefs).read_text(encoding="utf-8"), cat) if args.prefs else Preferences()
    report = solve(cat, prefs, max_plans=1)
    if not report.plans:
        print("no feasible plan", file=sys.stderr)
        return 2
    plan = report.plans[0]
    reg = None
    if args.live:
        from .usos.registrations import Registrations

        reg = Registrations(_client(cfg))
        print("LIVE mode: this will call registrations/register for each course. Type YES to continue.")
        if input("> ").strip() != "YES":
            return 1
    store = CaseStore(cfg.data_dir / "cases.db")
    for a in register_plan(reg, cat, plan, args.round, store=store, live=args.live):
        flag = "OK " if a.ok else "ERR"
        print(f"{flag} {a.course} {a.class_type} gr.{a.group_number}: {a.message}")
    return 0


def _plan_from_args(args, cfg):
    cat = load_catalog(args.source)
    _restore_meta(cat, Path(args.source))
    prefs = read_prefs(Path(args.prefs).read_text(encoding="utf-8"), cat) if args.prefs else Preferences()
    report = solve(cat, prefs, max_plans=args.plan)
    if len(report.plans) < args.plan:
        print(f"only {len(report.plans)} plan(s) available", file=sys.stderr)
        sys.exit(2)
    return cat, report.plans[args.plan - 1]


def _restore_meta(cat, source: Path) -> None:
    meta_path = source.with_suffix(".meta.json")
    if not meta_path.exists():
        return
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    by_name = {v["name"]: (k, v) for k, v in meta.items()}
    for c in cat.courses:
        if c.name in by_name:
            c.usos_course_id, info = by_name[c.name]
            c.term_id, c.coordinators = info["term_id"], info.get("coordinators", [])


def _term_dates(args, cfg, cat):
    from .calendar.dates import TermDatesError, resolve_term_dates

    client = None
    try:
        client = _client(cfg) if cfg.usos.consumer_key else None
    except Exception:
        client = None
    try:
        return resolve_term_dates(cfg.calendar, cat, args.start, args.end, client)
    except TermDatesError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)


def cmd_export_ics(args, cfg):
    from .calendar.dates import holidays
    from .calendar.ics import plan_to_ics

    cat, plan = _plan_from_args(args, cfg)
    start, end = _term_dates(args, cfg, cat)
    text = plan_to_ics(cat, plan, start, end, name=cfg.calendar.name, tz=cfg.calendar.timezone, exdates=holidays(cfg.calendar))
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out}: {text.count('BEGIN:VEVENT')} weekly events from {start} to {end}")
    print("Google Calendar: Settings -> Import & export -> Import, pick this file and a target calendar.")
    return 0


def cmd_gcal(args, cfg):
    from .calendar.dates import holidays
    from .calendar.google_sync import GoogleCalendarError, get_service, sync_plan

    cat, plan = _plan_from_args(args, cfg)
    start, end = _term_dates(args, cfg, cat)
    try:
        service = get_service(Path(cfg.calendar.google_credentials_file), cfg.data_dir / "google_token.json")
        result = sync_plan(service, cat, plan, start, end, calendar_name=args.calendar or cfg.calendar.name,
                           tz=cfg.calendar.timezone, exdates=holidays(cfg.calendar))
    except GoogleCalendarError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"calendar '{result['calendar_name']}' ({result['calendar_id']}): removed {result['removed']} old events, inserted {result['inserted']}")
    print("It is now in your Google account under 'My calendars'; share or subscribe from its settings page.")
    return 0


def cmd_serve(args, cfg):
    import uvicorn

    from .web.app import create_app

    uvicorn.run(create_app(cfg), host=args.host, port=args.port)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="usos-planner")
    ap.add_argument("--config", help="path to config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="compute best schedules from a .in or USOSweb .html file")
    p.add_argument("source")
    p.add_argument("--prefs")
    p.add_argument("--max-plans", type=_plans_count, default=5, help=f"how many best plans to list, 1-{MAX_PLANS} (default 5)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("convert", help="convert a USOSweb .html timetable into the .in format")
    p.add_argument("source")
    p.set_defaults(fn=cmd_convert)

    p = sub.add_parser("login", help="OAuth login to the USOS API (PIN flow)")
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("rounds", help="list your registration rounds in the coming month")
    p.set_defaults(fn=cmd_rounds)

    p = sub.add_parser("fetch", help="build a .in catalog from the USOS API")
    p.add_argument("--round", help="registration round id: fetch all its courses")
    p.add_argument("--course", action="append", help="course code (repeatable)")
    p.add_argument("--term", help="term id, e.g. 2025Z")
    p.add_argument("--out", default="plan.in")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("register", help="register for the best plan's courses (dry-run unless --live)")
    p.add_argument("source")
    p.add_argument("--round", required=True)
    p.add_argument("--prefs")
    p.add_argument("--live", action="store_true")
    p.set_defaults(fn=cmd_register)

    for name, fn, help_text in (("export-ics", cmd_export_ics, "write the plan as an .ics file (import into Google Calendar)"),
                                ("gcal", cmd_gcal, "create/refresh a dedicated Google Calendar with the plan")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("source")
        p.add_argument("--prefs")
        p.add_argument("--plan", type=_plans_count, default=1, help=f"which of the best plans, 1-{MAX_PLANS} (1 = best)")
        p.add_argument("--start", help="first day of classes, ISO date")
        p.add_argument("--end", help="last day of classes, ISO date")
        if name == "export-ics":
            p.add_argument("--out", default="plan.ics")
        else:
            p.add_argument("--calendar", help="calendar name (default: config calendar.name)")
        p.set_defaults(fn=fn)

    p = sub.add_parser("serve", help="start the web GUI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    cfg = Config.load(args.config)
    try:
        return args.fn(args, cfg)
    except InputError as e:
        print(f"input error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
