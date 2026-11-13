"""FastAPI GUI: plans, registration attempts, escalation cases and e-mail drafting.

Run with `usos-planner serve` and open http://127.0.0.1:8000.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..config import Config
from ..escalation import mailer
from ..escalation.store import STATUSES, CaseStore
from ..escalation.templates import CaseContext, coordinator_email, dean_petition
from ..inputs import load_catalog
from ..inputs.text_format import InputError, read_prefs
from ..models import DAY_NAMES, Preferences, fmt_time
from ..solver import solve

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config.load()
    app = FastAPI(title="usos-planner")
    store = CaseStore(cfg.data_dir / "cases.db")
    state: dict = {"catalog": None, "plans": [], "source": None, "prefs": Preferences(), "errors": [], "notice": ""}
    app.state.cfg = cfg
    app.state.store = store
    app.state.planner = state

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, name, {"cfg": cfg, "fmt_time": fmt_time, "DAY_NAMES": DAY_NAMES, **ctx})

    # ----- plans ----------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return render(request, "plans.html", state=state, cat=state["catalog"], plans=state["plans"])

    @app.post("/upload")
    async def upload(file: UploadFile, prefs_text: str = Form("")):
        uploads = cfg.data_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        dest = uploads / Path(file.filename or "plan.in").name
        dest.write_bytes(await file.read())
        state["errors"] = []
        try:
            cat = load_catalog(dest)
            prefs = read_prefs(prefs_text, cat) if prefs_text.strip() else Preferences()
        except InputError as e:
            state["errors"] = [str(e)]
            return RedirectResponse("/", status_code=303)
        state.update(catalog=cat, source=dest.name, prefs=prefs)
        report = solve(cat, prefs, max_plans=5)
        state["plans"] = report.plans
        if report.infeasible_units:
            state["errors"] = ["No plan possible: units without any allowed group: " + ", ".join(
                f"{cat.courses[cat.units[u].course_id].name} {cat.units[u].class_type.value}" for u in report.infeasible_units)]
        return RedirectResponse("/", status_code=303)

    @app.post("/replan")
    def replan(prefs_text: str = Form("")):
        cat = state["catalog"]
        if cat is None:
            return RedirectResponse("/", status_code=303)
        try:
            prefs = read_prefs(prefs_text, cat) if prefs_text.strip() else Preferences()
        except InputError as e:
            state["errors"] = [str(e)]
            return RedirectResponse("/", status_code=303)
        state["prefs"] = prefs
        state["errors"] = []
        state["plans"] = solve(cat, prefs, max_plans=5).plans
        return RedirectResponse("/", status_code=303)

    @app.get("/plans/{index}.json")
    def plan_json(index: int):
        cat, plans = state["catalog"], state["plans"]
        if cat is None or not 0 <= index < len(plans):
            return {"error": "no such plan"}
        return plans[index].to_dict(cat)

    # ----- calendar -------------------------------------------------------
    def _dates(start: str, end: str):
        from ..calendar.dates import resolve_term_dates

        return resolve_term_dates(cfg.calendar, state["catalog"], start, end)

    @app.get("/plans/{index}.ics")
    def plan_ics(index: int, start: str = "", end: str = ""):
        from ..calendar.dates import TermDatesError, holidays
        from ..calendar.ics import plan_to_ics

        cat, plans = state["catalog"], state["plans"]
        if cat is None or not 0 <= index < len(plans):
            return Response("no such plan", status_code=404)
        try:
            s, e = _dates(start, end)
        except TermDatesError as err:
            return Response(str(err), status_code=400)
        text = plan_to_ics(cat, plans[index], s, e, name=cfg.calendar.name, tz=cfg.calendar.timezone, exdates=holidays(cfg.calendar))
        return Response(text, media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="plan-{index + 1}.ics"'})

    @app.post("/plans/{index}/gcal")
    def plan_gcal(index: int, start: str = Form(""), end: str = Form("")):
        from ..calendar.dates import TermDatesError, holidays
        from ..calendar.google_sync import GoogleCalendarError, get_service, sync_plan

        cat, plans = state["catalog"], state["plans"]
        if cat is None or not 0 <= index < len(plans):
            return RedirectResponse("/", status_code=303)
        try:
            s, e = _dates(start, end)
            service = get_service(Path(cfg.calendar.google_credentials_file), cfg.data_dir / "google_token.json")
            result = sync_plan(service, cat, plans[index], s, e, calendar_name=cfg.calendar.name,
                               tz=cfg.calendar.timezone, exdates=holidays(cfg.calendar))
        except (TermDatesError, GoogleCalendarError) as err:
            state["errors"] = [str(err)]
            return RedirectResponse("/", status_code=303)
        state["errors"] = []
        state["notice"] = (f"Kalendarz Google „{result['calendar_name']}”: usunięto {result['removed']} starych, "
                           f"dodano {result['inserted']} cotygodniowych wydarzeń.")
        return RedirectResponse("/", status_code=303)

    # ----- cases ----------------------------------------------------------
    @app.get("/cases", response_class=HTMLResponse)
    def cases(request: Request, all: int = 0):
        return render(request, "cases.html", cases=store.cases(include_closed=bool(all)), attempts=store.attempts(50))

    @app.post("/cases/new")
    def new_case(course: str = Form(...), usos_course_id: str = Form(""), term_id: str = Form(""),
                 class_type: str = Form("CW"), group_number: int = Form(1), reason: str = Form("limit"),
                 coordinator_name: str = Form(""), coordinator_email: str = Form("")):
        coords = [{"name": coordinator_name, "email": coordinator_email}] if coordinator_email or coordinator_name else []
        cid = store.open_case(course=course, usos_course_id=usos_course_id, term_id=term_id, class_type=class_type,
                              group_number=group_number, reason=reason, server_message="(manual case)", coordinators=coords)
        return RedirectResponse(f"/cases/{cid}", status_code=303)

    @app.get("/cases/{case_id}", response_class=HTMLResponse)
    def case_view(request: Request, case_id: int):
        case = store.case(case_id)
        if case is None:
            return RedirectResponse("/cases", status_code=303)
        return render(request, "case.html", case=case, messages=store.messages(case_id), statuses=STATUSES, student=cfg.student)

    @app.post("/cases/{case_id}/status")
    def case_status(case_id: int, status: str = Form(...), notes: str = Form("")):
        store.set_status(case_id, status, notes or None)
        return RedirectResponse(f"/cases/{case_id}", status_code=303)

    @app.post("/cases/{case_id}/draft")
    def draft(case_id: int, kind: str = Form(...), recipient: str = Form(""), coordinator_name: str = Form(""),
              justification: str = Form(""), coordinator_approved: int = Form(1)):
        case = store.case(case_id)
        if case is None:
            return RedirectResponse("/cases", status_code=303)
        ctx = CaseContext(case["course"], case["usos_course_id"], case["term_id"], case["class_type"], case["group_number"],
                          case["reason"], coordinator_name=coordinator_name, justification=justification)
        if kind == "coordinator":
            subject, body = coordinator_email(cfg.student, ctx)
            if not recipient and case["coordinators"]:
                recipient = case["coordinators"][0].get("email") or ""
            store.set_status(case_id, "coordinator_drafted")
        else:
            subject, body = dean_petition(cfg.student, ctx, coordinator_approved=bool(coordinator_approved))
            recipient = recipient or cfg.student.vice_dean_email or cfg.student.dean_office_email
            store.set_status(case_id, "dean_drafted")
        mid = store.add_message(case_id, kind, recipient, subject, body)
        return RedirectResponse(f"/messages/{mid}", status_code=303)

    # ----- messages -------------------------------------------------------
    @app.get("/messages/{message_id}", response_class=HTMLResponse)
    def message_view(request: Request, message_id: int):
        msg = store.message(message_id)
        if msg is None:
            return RedirectResponse("/cases", status_code=303)
        return render(request, "message.html", msg=msg, case=store.case(msg["case_id"]), error=request.query_params.get("error"))

    @app.post("/messages/{message_id}/send")
    def message_send(message_id: int, recipient: str = Form(...), subject: str = Form(...), body: str = Form(...), cc: str = Form("")):
        msg = store.message(message_id)
        if msg is None:
            return RedirectResponse("/cases", status_code=303)
        store.update_message(message_id, recipient, subject, body)
        try:
            mailer.send(cfg.mail, recipient, subject, body, cc=[c.strip() for c in cc.split(",") if c.strip()],
                        archive_dir=cfg.data_dir / "sent")
        except mailer.MailError as e:
            return RedirectResponse(f"/messages/{message_id}?error={e}", status_code=303)
        store.mark_sent(message_id)
        store.set_status(msg["case_id"], "coordinator_sent" if msg["kind"] == "coordinator" else "dean_sent")
        return RedirectResponse(f"/cases/{msg['case_id']}", status_code=303)

    @app.get("/api/state.json")
    def api_state():
        cat = state["catalog"]
        return {"source": state["source"], "plans": [p.to_dict(cat) for p in state["plans"]] if cat else [],
                "cases": store.cases(include_closed=True)}

    return app


app = create_app
