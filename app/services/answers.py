"""Answers for application-form questions.

Resolution order for each question:
  1. a saved answer you approved before (exact or near-identical wording)
  2. a fact from your profile/preferences (name, email, notice period, ...)
  3. an AI draft grounded in your profile (marked for review)
Sensitive questions (demographics, disability, criminal record, ...) and legal
attestations are only ever answered from your saved answers or explicit
preferences, never guessed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from pydantic import BaseModel
from sqlmodel import Session, select

from ..models import Answer, utcnow
from ..schemas import ProfileData
from . import profile_utils
from .llm import LLM, LLMError
from .text import tokenize

SENSITIVE = re.compile(
    r"\b(gender|sex|race|racial|ethnic|ethnicity|veteran|disab|religio|caste|sexual orientation|pronoun|"
    r"criminal|convict|felony|arrest|date of birth|dob|age\b|marital|pregnan|national origin|citizenship status)",
    re.I)
LEGAL = re.compile(r"\b(authori[sz]ed to work|work authori[sz]ation|sponsor|visa|right to work|legally)", re.I)


def normalize_question(q: str) -> str:
    q = q.lower().replace("*", " ")
    q = re.sub(r"\((?:required|optional)\)", " ", q)
    q = re.sub(r"\b(please|kindly)\b", " ", q)
    return re.sub(r"[^a-z0-9]+", " ", q).strip()


@dataclass
class Suggestion:
    label: str
    answer: str
    source: str  # saved | profile | ai | none
    confidence: float
    needs_review: bool


def _similar(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)) or set(a.split()), set(tokenize(b)) or set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def find_saved(session: Session, question: str) -> Answer | None:
    key = normalize_question(question)
    if not key:
        return None
    exact = session.exec(select(Answer).where(Answer.key == key)).first()
    if exact:
        return exact
    best, best_score = None, 0.0
    for row in session.exec(select(Answer)).all():
        s = _similar(key, row.key)
        if s > best_score:
            best, best_score = row, s
    return best if best_score >= 0.75 else None


def _pick_option(answer: str, options: list[str]) -> str:
    if not options:
        return answer
    low = answer.strip().lower()
    for o in options:
        if o.strip().lower() == low:
            return o
    for o in options:
        if low and (low in o.lower() or o.lower() in low):
            return o
    return answer


def _yes_no(flag: bool | None) -> str:
    return "" if flag is None else ("Yes" if flag else "No")


def profile_answer(profile: ProfileData, question: str) -> str:
    """Deterministic answers for the common, factual questions."""
    q = question.lower()
    c, prefs = profile.contact, profile.preferences
    first, last = profile_utils.split_name(c.name)
    current = next((e for e in profile.experience if e.current), profile.experience[0] if profile.experience else None)
    rules: list[tuple[str, str]] = [
        (r"first name|given name", first),
        (r"last name|surname|family name", last),
        (r"full name|^name$|your name", c.name),
        (r"^(?:your )?e-?mail(?: address| id)?$|e-?mail address|e-?mail id", c.email),
        (r"phone|mobile|contact number", c.phone),
        (r"linkedin", c.linkedin),
        (r"github", c.github),
        (r"portfolio|website|personal site", c.website or c.github),
        (r"current (?:city|location)|where are you (?:based|located)|^city$|^location$", c.location),
        (r"notice period", f"{prefs.notice_period_days} days" if prefs.notice_period_days is not None else ""),
        (r"current (?:ctc|salary|compensation)", prefs.current_ctc),
        (r"expected (?:ctc|salary|compensation)|salary expectation|desired salary", prefs.expected_ctc),
        (r"relocat", _yes_no(prefs.willing_to_relocate)),
        (r"sponsor", _yes_no(prefs.requires_sponsorship)),
        (r"authori[sz]ed to work|work authori[sz]ation|right to work", prefs.work_authorization),
        (r"current (?:company|employer|organi[sz]ation)", current.company if current else ""),
        (r"current (?:title|designation|role|position)|job title", current.title if current else ""),
        (r"headline", profile.headline),
    ]
    for pattern, value in rules:
        if re.search(pattern, q):
            return value or ""
    if re.search(r"(how many|total|overall).*(years|experience)|years of (?:total |overall |professional )?experience", q):
        m = re.search(r"experience (?:in|with|using|on) ([\w.+#/\- ]{1,40}?)(?:\?|$| and | in )", q)
        if m:
            years = profile_utils.years_with_skill(profile, m[1].strip())
            return f"{years:g}" if years else ""
        return f"{profile_utils.total_years(profile):g}"
    return ""


class _AIAnswer(BaseModel):
    index: int
    answer: str
    confidence: float


class _AIAnswers(BaseModel):
    answers: list[_AIAnswer]


def suggest(session: Session, profile: ProfileData, questions: list[dict], llm: LLM | None,
            job_context: str = "") -> list[dict]:
    out: list[Suggestion | None] = []
    pending: list[tuple[int, dict]] = []
    for i, q in enumerate(questions):
        label = (q.get("label") or "").strip()
        options = q.get("options") or []
        if not label:
            out.append(Suggestion(label, "", "none", 0, True))
            continue
        saved = find_saved(session, label)
        if saved:
            out.append(Suggestion(label, _pick_option(saved.answer, options), "saved", 0.95, False))
            continue
        fact = profile_answer(profile, label)
        if fact:
            out.append(Suggestion(label, _pick_option(fact, options), "profile", 0.9, False))
            continue
        if SENSITIVE.search(label) or LEGAL.search(label):
            out.append(Suggestion(label, "", "none", 0, True))
            continue
        out.append(None)
        pending.append((i, q))

    if pending and llm is not None:
        try:
            listing = "\n".join(
                f"{n}. {q.get('label')}" + (f"  [type: {q.get('type')}]" if q.get("type") else "")
                + (f"  options: {q.get('options')}" if q.get("options") else "")
                for n, (_, q) in enumerate(pending))
            ai = llm.structured(
                system=("You fill job application forms for a candidate using ONLY their profile. "
                        "For each question give a short, truthful answer in the candidate's voice. "
                        "If options are given, the answer must be exactly one of them. For long-form "
                        "questions write 2-4 sentences. If the profile does not contain the answer, "
                        "return an empty string with confidence 0 — never guess facts."),
                prompt=(f"<profile>\n{profile.model_dump_json(exclude={'preferences'})}\n</profile>\n"
                        f"<job>\n{job_context[:4000]}\n</job>\n<questions>\n{listing}\n</questions>"),
                schema=_AIAnswers, max_tokens=8000)
            by_index = {a.index: a for a in ai.answers}
            for n, (i, q) in enumerate(pending):
                a = by_index.get(n)
                if a and a.answer.strip():
                    out[i] = Suggestion(q["label"], _pick_option(a.answer.strip(), q.get("options") or []), "ai",
                                        max(0.0, min(1.0, a.confidence)), True)
        except LLMError:
            pass
    return [asdict(s) if s else asdict(Suggestion(q.get("label", ""), "", "none", 0, True))
            for s, q in zip(out, questions)]


def save_answers(session: Session, items: list[dict], profile: ProfileData | None = None) -> int:
    """Remember answers the user approved. Profile facts are not duplicated."""
    n = 0
    for item in items:
        question, answer = (item.get("label") or "").strip(), (item.get("answer") or "").strip()
        key = normalize_question(question)
        if not key or not answer or len(key) > 300:
            continue
        if profile is not None and profile_answer(profile, question) == answer:
            continue
        row = session.exec(select(Answer).where(Answer.key == key)).first()
        if row is None:
            row = Answer(key=key, question=question, answer=answer)
        else:
            row.answer = answer
            row.uses += 1
        row.updated_at = utcnow()
        session.add(row)
        n += 1
    session.commit()
    return n
