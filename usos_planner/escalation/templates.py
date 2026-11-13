"""Formal Polish messages: e-mail to the course coordinator and a petition
(podanie) to the vice-dean for student affairs.  Both are plain text so the
student can edit them in the GUI before sending."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..config import StudentConfig

TYPE_NAMES_PL = {
    "WYK": "wykład",
    "CW": "ćwiczenia",
    "CWW": "ćwiczenia",
    "LAB": "laboratorium",
    "SEM": "seminarium",
    "KON": "konwersatorium",
    "WAR": "warsztaty",
    "LEK": "lektorat",
    "WF": "zajęcia WF",
    "OTHER": "zajęcia",
}

REASON_TEXT_PL = {
    "limit": "limit miejsc w grupie został wyczerpany",
    "not_entitled": "nie posiadam uprawnień do rejestracji na ten przedmiot w systemie USOS",
    "other": "system USOS odrzucił moją próbę rejestracji",
    "closed": "tura rejestracji była już zamknięta",
}


@dataclass
class CaseContext:
    course: str
    usos_course_id: str
    term_id: str
    class_type: str
    group_number: int
    reason: str
    coordinator_name: str = ""
    justification: str = ""  # student's own sentence(s): why this course / group


def _greeting(name: str) -> str:
    low = name.lower()
    if "prof" in low:
        return "Szanowny Panie Profesorze / Szanowna Pani Profesor"
    if "dr" in low:
        return "Szanowny Panie Doktorze / Szanowna Pani Doktor"
    return "Szanowni Państwo"


def _term_pl(term_id: str) -> str:
    if len(term_id) >= 5 and term_id[-1] in "ZL":
        year = term_id[:4]
        return f"semestr {'zimowy' if term_id[-1] == 'Z' else 'letni'} roku akademickiego {year}/{int(year) + 1}"
    return f"semestr {term_id}"


def coordinator_email(student: StudentConfig, ctx: CaseContext) -> tuple[str, str]:
    subject = f"Prośba o zgodę na zapis na przedmiot {ctx.course} ({ctx.usos_course_id}) – {student.full_name}, nr albumu {student.index_number}"
    reason = REASON_TEXT_PL.get(ctx.reason, REASON_TEXT_PL["other"])
    group = f"grupa nr {ctx.group_number} ({TYPE_NAMES_PL.get(ctx.class_type, 'zajęcia')})"
    justification = ctx.justification.strip() or (
        "Przedmiot ten jest istotny dla mojego toku studiów, a wskazana grupa jako jedyna nie koliduje "
        "z pozostałymi zajęciami w moim planie."
    )
    body = f"""{_greeting(ctx.coordinator_name)},

zwracam się z uprzejmą prośbą o wyrażenie zgody na zapisanie mnie na przedmiot
„{ctx.course}” (kod USOS: {ctx.usos_course_id}), {group}, w {_term_pl(ctx.term_id)}.

Podczas rejestracji w systemie USOSweb {reason}, w związku z czym nie mogłem/mogłam
samodzielnie dokonać zapisu. {justification}

Jeżeli wyrażą Państwo zgodę, będę wdzięczny/wdzięczna za potwierdzenie jej w odpowiedzi
na tę wiadomość – przekażę je następnie do dziekanatu wraz z podaniem o dopisanie do grupy.

Z wyrazami szacunku,
{student.full_name}
nr albumu: {student.index_number}
{student.programme}, {student.year}
{student.faculty}
e-mail: {student.email}{(chr(10) + 'tel.: ' + student.phone) if student.phone else ''}
"""
    return subject, body


def dean_petition(student: StudentConfig, ctx: CaseContext, coordinator_approved: bool = True) -> tuple[str, str]:
    today = date.today().strftime("%d.%m.%Y")
    subject = f"Podanie o dopisanie do grupy – {ctx.course} ({ctx.usos_course_id}) – {student.full_name}, nr albumu {student.index_number}"
    reason = REASON_TEXT_PL.get(ctx.reason, REASON_TEXT_PL["other"])
    group = f"grupa nr {ctx.group_number} ({TYPE_NAMES_PL.get(ctx.class_type, 'zajęcia')})"
    approval = (
        f"Koordynator przedmiotu{(' ' + ctx.coordinator_name) if ctx.coordinator_name else ''} wyraził(a) zgodę na mój udział w zajęciach – potwierdzenie w załączeniu."
        if coordinator_approved
        else "Jednocześnie zwróciłem/zwróciłam się o zgodę do koordynatora przedmiotu; przekażę ją niezwłocznie po otrzymaniu."
    )
    justification = ctx.justification.strip() or (
        "Przedmiot ten jest niezbędny do realizacji programu studiów w bieżącym semestrze, a wskazana grupa "
        "jako jedyna nie koliduje z pozostałymi zajęciami w moim planie."
    )
    addressee = student.vice_dean_name or "Prodziekan ds. studenckich"
    body = f"""{student.full_name}                                              {today}
nr albumu: {student.index_number}
{student.programme}, {student.year}
{student.faculty}
e-mail: {student.email}{(chr(10) + 'tel.: ' + student.phone) if student.phone else ''}

                                        {addressee}
                                        {student.faculty}

                                   PODANIE

Zwracam się z uprzejmą prośbą o dopisanie mnie do przedmiotu „{ctx.course}”
(kod USOS: {ctx.usos_course_id}), {group}, w {_term_pl(ctx.term_id)}.

Uzasadnienie:
Podczas rejestracji w systemie USOSweb {reason}, przez co nie mogłem/mogłam dokonać
zapisu samodzielnie. {justification} {approval}

Proszę o pozytywne rozpatrzenie mojego podania.

Z wyrazami szacunku,

{student.full_name}

Załączniki:
1. Zgoda koordynatora przedmiotu (korespondencja e-mail).
"""
    return subject, body
