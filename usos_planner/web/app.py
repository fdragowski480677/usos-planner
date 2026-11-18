"""FastAPI GUI: plans, registration attempts, escalation cases and e-mail drafting.

Run with `usos-planner serve` and open http://127.0.0.1:8000.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
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
from ..solver import MAX_PLANS, clamp_plans, solve

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config.load()
    app = FastAPI(title="usos-planner")
    store = CaseStore(cfg.data_dir / "cases.db")
    state: dict = {"catalog": None, "plans": [], "source": None, "prefs": Preferences(), "errors": [], "notice": "", "max_plans": 5,
                   "usos_request_token": None, "usos_user": "", "google_user": "", "google_flow": None, "google_flow_state": ""}
    app.state.cfg = cfg
    app.state.store = store
    app.state.planner = state
    google_token = cfg.data_dir / "google_token.json"

    # ----- auth helpers ---------------------------------------------------
    def usos_client():
        from ..usos.client import UsosClient

        return UsosClient(cfg.usos, cfg.data_dir / "usos_token.json")

    def refresh_usos_user() -> str:
        try:
            client = usos_client()
            if client.logged_in:
                me = client.whoami()
                state["usos_user"] = f"{me.get('first_name', '')} {me.get('last_name', '')}".strip() or "zalogowano"
            else:
                state["usos_user"] = ""
        except Exception:
            state["usos_user"] = ""
        return state["usos_user"]

    def google_creds():
        from ..calendar.google_sync import load_credentials

        try:
            return load_credentials(google_token)
        except Exception:
            return None

    def refresh_google_user() -> str:
        from ..calendar.google_sync import account_email

        creds = google_creds()
        state["google_user"] = account_email(creds) or ("zalogowano" if creds else "") if creds else ""
        return state["google_user"]

    refresh_usos_user()
    refresh_google_user()

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        auth = {"usos_user": state["usos_user"], "google_user": state["google_user"]}
        return TEMPLATES.TemplateResponse(request, name, {"cfg": cfg, "fmt_time": fmt_time, "DAY_NAMES": DAY_NAMES, "auth": auth, "MAX_PLANS": MAX_PLANS, **ctx})

    def set_catalog(cat, prefs: Preferences, source: str) -> None:
        state.update(catalog=cat, source=source, prefs=prefs, errors=[])
        report = solve(cat, prefs, max_plans=state["max_plans"])
        state["plans"] = report.plans
        if report.infeasible_units:
            state["errors"] = ["Brak planu: zajęcia bez żadnej dozwolonej grupy: " + ", ".join(
                f"{cat.courses[cat.units[u].course_id].name} {cat.units[u].class_type.value}" for u in report.infeasible_units)]

    # ----- USOS login (OAuth 1.0a with callback) ---------------------------
    @app.get("/auth/usos")
    def auth_usos(request: Request):
        from ..usos.client import UsosError

        try:
            client = usos_client()
            url, req = client.begin_login(callback_uri=str(request.url_for("auth_usos_callback")))
        except UsosError as e:
            state["errors"] = [f"USOS: {e.message}. Wpisz klucz aplikacji w Ustawieniach."]
            return RedirectResponse("/settings", status_code=303)
        state["usos_request_token"] = req
        return RedirectResponse(url, status_code=303)

    @app.get("/auth/usos/callback", name="auth_usos_callback")
    def auth_usos_callback(oauth_verifier: str = "", oauth_token: str = ""):
        req = state.get("usos_request_token")
        if not req or not oauth_verifier:
            state["errors"] = ["USOS: logowanie przerwane (brak weryfikatora)."]
            return RedirectResponse("/settings", status_code=303)
        try:
            usos_client().finish_login(req, oauth_verifier)
        except Exception as e:  # oauthlib raises its own error types
            state["errors"] = [f"USOS: logowanie nie powiodło się: {e}"]
            return RedirectResponse("/settings", status_code=303)
        state["usos_request_token"] = None
        refresh_usos_user()
        return RedirectResponse("/usos", status_code=303)

    @app.get("/auth/usos/logout")
    def auth_usos_logout():
        try:
            usos_client().logout()
        except Exception:
            pass
        state["usos_user"] = ""
        return RedirectResponse("/settings", status_code=303)

    # ----- Google login (OAuth 2 redirect flow) ----------------------------
    @app.get("/auth/google")
    def auth_google(request: Request):
        from ..calendar.google_sync import GoogleCalendarError, web_flow

        try:
            flow = web_flow(Path(cfg.calendar.google_credentials_file), str(request.url_for("auth_google_callback")))
        except GoogleCalendarError as e:
            state["errors"] = [str(e)]
            return RedirectResponse("/settings", status_code=303)
        url, flow_state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
        # PKCE: the flow holds the code_verifier that must be sent back with the code,
        # so the very same object has to finish the login in the callback.
        state["google_flow"] = flow
        state["google_flow_state"] = flow_state
        return RedirectResponse(url, status_code=303)

    @app.get("/auth/google/callback", name="auth_google_callback")
    def auth_google_callback(request: Request, code: str = "", error: str = ""):
        if error or not code:
            state["errors"] = [f"Google: logowanie przerwane ({error or 'brak kodu'})."]
            return RedirectResponse("/settings", status_code=303)
        flow = state.get("google_flow")
        if flow is None or request.query_params.get("state") != state.get("google_flow_state"):
            state["errors"] = ["Google: logowanie przerwane (sesja wygasła lub niezgodny parametr state). Spróbuj ponownie."]
            return RedirectResponse("/settings", status_code=303)
        try:
            flow.fetch_token(code=code)
        except Exception as e:
            state["errors"] = [f"Google: logowanie nie powiodło się: {e}"]
            return RedirectResponse("/settings", status_code=303)
        state["google_flow"] = None
        google_token.parent.mkdir(parents=True, exist_ok=True)
        google_token.write_text(flow.credentials.to_json())
        google_token.chmod(0o600)
        refresh_google_user()
        return RedirectResponse("/", status_code=303)

    @app.get("/auth/google/logout")
    def auth_google_logout():
        if google_token.exists():
            google_token.unlink()
        state["google_user"] = ""
        return RedirectResponse("/settings", status_code=303)

    # ----- settings --------------------------------------------------------
    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        cred = Path(cfg.calendar.google_credentials_file)
        client_json = cred.read_text(encoding="utf-8") if cred.exists() else ""
        errors, state["errors"] = state["errors"], []
        notice, state["notice"] = state["notice"], ""
        return render(request, "settings.html", usos_user=state["usos_user"], google_user=state["google_user"],
                      google_client_json=client_json, google_configured=cred.exists(),
                      error="; ".join(errors) if errors else "", notice=notice)

    @app.post("/settings/usos")
    def settings_usos(base_url: str = Form(...), consumer_key: str = Form(""), consumer_secret: str = Form("")):
        base_url = base_url.strip()
        cfg.usos.base_url = base_url if base_url.endswith("/") else base_url + "/"
        cfg.usos.consumer_key, cfg.usos.consumer_secret = consumer_key.strip(), consumer_secret.strip()
        cfg.save()
        state["notice"] = "Zapisano ustawienia USOS."
        return RedirectResponse("/settings", status_code=303)

    @app.post("/settings/google")
    def settings_google(client_json: str = Form("")):
        text = client_json.strip()
        if text:
            try:
                data = json.loads(text)
                if not any(k in data for k in ("installed", "web")):
                    raise ValueError("expected an OAuth client JSON with an 'installed' or 'web' section")
            except ValueError as e:
                state["errors"] = [f"Google: niepoprawny JSON klienta: {e}"]
                return RedirectResponse("/settings", status_code=303)
            dest = Path(cfg.calendar.google_credentials_file)
            dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
            try:
                dest.chmod(0o600)
            except OSError:
                pass
            state["notice"] = "Zapisano klienta Google. Możesz się zalogować."
        return RedirectResponse("/settings", status_code=303)

    @app.post("/settings/calendar")
    def settings_calendar(name: str = Form("Plan zajęć"), term_start: str = Form(""), term_end: str = Form(""), holidays: str = Form("")):
        cfg.calendar.name = name.strip() or "Plan zajęć"
        cfg.calendar.term_start, cfg.calendar.term_end = term_start.strip(), term_end.strip()
        cfg.calendar.holidays = [h.strip() for h in holidays.split(",") if h.strip()]
        cfg.save()
        state["notice"] = "Zapisano ustawienia kalendarza."
        return RedirectResponse("/settings", status_code=303)

    @app.post("/settings/student")
    async def settings_student(request: Request):
        form = await request.form()
        for key in cfg.student.__dataclass_fields__:
            if key in form:
                setattr(cfg.student, key, str(form[key]).strip())
        cfg.save()
        state["notice"] = "Zapisano dane studenta."
        return RedirectResponse("/settings", status_code=303)

    @app.post("/settings/mail")
    def settings_mail(smtp_host: str = Form(""), smtp_port: int = Form(587), username: str = Form(""), password: str = Form(""), from_address: str = Form("")):
        cfg.mail.smtp_host, cfg.mail.smtp_port = smtp_host.strip(), smtp_port
        cfg.mail.username, cfg.mail.password, cfg.mail.from_address = username.strip(), password, from_address.strip()
        cfg.save()
        state["notice"] = "Zapisano ustawienia poczty."
        return RedirectResponse("/settings", status_code=303)

    # ----- fetch from USOS -------------------------------------------------
    @app.get("/usos", response_class=HTMLResponse)
    def usos_page(request: Request):
        rounds, error = [], ""
        errors, state["errors"] = state["errors"], []
        if errors:
            error = "; ".join(errors)
        if state["usos_user"]:
            from ..usos.client import UsosError
            from ..usos.registrations import Registrations

            try:
                start = date.today() - timedelta(days=7)
                rounds = Registrations(usos_client()).search_rounds(start.isoformat(), (start + timedelta(days=30)).isoformat())
            except UsosError as e:
                error = f"USOS: {e.user_message}"
        return render(request, "usos.html", state=state, usos_user=state["usos_user"], rounds=rounds, error=error)

    @app.post("/usos/fetch")
    def usos_fetch(round_id: str = Form(""), courses: str = Form(""), term_id: str = Form(""), max_plans: int = Form(0)):
        if max_plans:
            state["max_plans"] = clamp_plans(max_plans)
        from ..inputs.text_format import write_catalog
        from ..inputs.usos_api import catalog_from_api
        from ..usos.client import UsosError
        from ..usos.registrations import Registrations

        try:
            reg = Registrations(usos_client())
            pairs: list[tuple[str, str]] = []
            if round_id:
                for entry in reg.round_courses(round_id):
                    course = entry.get("course") or {}
                    cid = course.get("id") or entry.get("course_id")
                    term = entry.get("term_id") or term_id
                    if cid and term:
                        pairs.append((cid, term))
            for code in courses.split(","):
                if code.strip() and term_id.strip():
                    pairs.append((code.strip(), term_id.strip()))
            if not pairs:
                state["errors"] = ["Nie wybrano tury ani przedmiotów (dla kodów podaj też cykl)."]
                return RedirectResponse("/usos", status_code=303)
            cat = catalog_from_api(reg, pairs)
        except UsosError as e:
            state["errors"] = [f"USOS: {e.user_message}"]
            return RedirectResponse("/usos", status_code=303)
        if not cat.groups:
            state["errors"] = ["USOS nie zwrócił żadnych terminów zajęć dla tych przedmiotów."]
            return RedirectResponse("/usos", status_code=303)
        uploads = cfg.data_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        label = f"usos-{round_id or term_id.strip()}"
        (uploads / f"{label}.in").write_text(write_catalog(cat), encoding="utf-8")
        meta = {c.usos_course_id: {"term_id": c.term_id, "name": c.name, "coordinators": c.coordinators} for c in cat.courses}
        (uploads / f"{label}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        set_catalog(cat, state["prefs"], label)
        return RedirectResponse("/", status_code=303)

    # ----- plans ----------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return render(request, "plans.html", state=state, cat=state["catalog"], plans=state["plans"])

    @app.post("/upload")
    async def upload(file: UploadFile, prefs_text: str = Form(""), max_plans: int = Form(5)):
        state["max_plans"] = clamp_plans(max_plans)
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
        set_catalog(cat, prefs, dest.name)
        return RedirectResponse("/", status_code=303)

    @app.post("/replan")
    def replan(prefs_text: str = Form(""), max_plans: int = Form(5)):
        state["max_plans"] = clamp_plans(max_plans)
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
        state["plans"] = solve(cat, prefs, max_plans=state["max_plans"]).plans
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
        from ..calendar.google_sync import GoogleCalendarError, service_from_credentials, sync_plan

        cat, plans = state["catalog"], state["plans"]
        if cat is None or not 0 <= index < len(plans):
            return RedirectResponse("/", status_code=303)
        try:
            s, e = _dates(start, end)
            creds = google_creds()
            if creds is None:
                raise GoogleCalendarError("Najpierw zaloguj się przez Google (przycisk w nagłówku).")
            service = service_from_credentials(creds)
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
