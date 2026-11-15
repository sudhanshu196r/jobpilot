"""Applying from inside JobPilot.

Employers' job systems only accept applications through their own web forms
(or the employer's private API key), so the final submit always happens in the
browser, driven by the extension. What happens here:

* ``apply_url``: the application form itself, so the extension skips the listing;
* ``application_form``: for Greenhouse jobs, the real form questions (from the
  public job-board API), pre-answered from your profile and saved answers, so you
  can review and edit everything in JobPilot before anything opens.
"""

from __future__ import annotations

import logging
import re

import httpx
from sqlmodel import Session

from ..models import Job
from ..schemas import ProfileData
from . import answers as answers_svc
from .llm import LLM
from .sources.base import USER_AGENT

log = logging.getLogger(__name__)

# Company application forms the extension may submit for you once you approve.
# Job boards (Naukri, Indeed, LinkedIn) and account-gated Workday are never auto-submitted.
AUTO_SUBMIT_SOURCES = frozenset({"greenhouse", "lever", "ashby", "smartrecruiters"})


def apply_url(job: Job) -> str:
    """The page with the application form, falling back to the posting."""
    url = (job.url or "").rstrip("/")
    if job.source == "greenhouse" and ":" in job.external_id:
        board, job_id = job.external_id.split(":", 1)
        return f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}"
    if job.source == "lever" and "jobs.lever.co" in url:
        return url if url.endswith("/apply") else f"{url}/apply"
    if job.source == "ashby" and "jobs.ashbyhq.com" in url:
        return url if url.endswith("/application") else f"{url}/application"
    if job.source == "smartrecruiters" and url:
        return url + ("&" if "?" in url else "?") + "oga=true"
    if job.source == "workday" and url:
        return url if url.endswith("/apply") else f"{url}/apply"
    return job.url


def can_auto_submit(job: Job) -> bool:
    return job.source in AUTO_SUBMIT_SOURCES


# --------------------------------------------------------------------------- Greenhouse form

_TYPE = {
    "input_text": "text", "textarea": "textarea", "input_file": "file",
    "multi_value_single_select": "select", "multi_value_multi_select": "multiselect",
    "input_hidden": None,
}


def _normalize_questions(data: dict) -> list[dict]:
    fields: list[dict] = []
    for group in ("questions", "location_questions"):
        for q in data.get(group) or []:
            for f in q.get("fields") or []:
                kind = _TYPE.get(f.get("type"), "text")
                if kind is None:
                    continue
                name = f.get("name", "")
                if kind == "file" or name in ("resume_text", "cover_letter_text"):
                    continue  # the tailored resume and cover letter are attached separately
                options = [{"label": str(v.get("label", "")), "value": str(v.get("value", ""))}
                           for v in f.get("values") or []]
                # A single-option "multi select" is a consent checkbox.
                if kind == "multiselect" and len(options) == 1:
                    kind = "checkbox"
                fields.append({"name": name, "label": re.sub(r"\s+", " ", q.get("label", "")).strip(),
                               "type": kind, "required": bool(q.get("required")), "options": options,
                               "description": re.sub(r"<[^>]+>", "", q.get("description") or "")[:400]})
    return fields


def greenhouse_questions(job: Job, client: httpx.Client | None = None) -> list[dict] | None:
    if job.source != "greenhouse" or ":" not in job.external_id:
        return None
    board, job_id = job.external_id.split(":", 1)
    own = client is None
    client = client or httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT})
    try:
        resp = client.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}", params={"questions": "true"})
        if resp.status_code != 200:
            return None
        return _normalize_questions(resp.json())
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Greenhouse questions for %s failed: %s", job.external_id, exc)
        return None
    finally:
        if own:
            client.close()


def _option_for(answer: str, options: list[dict]) -> str:
    """Map a free-text answer onto one of a select's option labels."""
    a = answer.strip().lower()
    if not a:
        return ""
    for o in options:
        if o["label"].strip().lower() == a:
            return o["label"]
    for o in options:
        lab = o["label"].strip().lower()
        if lab.startswith(a) or a.startswith(lab):
            return o["label"]
    return ""


def _is_yes_no(options: list[dict]) -> bool:
    labels = {o["label"].strip().lower() for o in options}
    return bool(labels) and labels <= {"yes", "no"}


def prefill(session: Session, profile: ProfileData, fields: list[dict], job: Job, llm: LLM | None,
            saved: dict[str, str] | None = None) -> list[dict]:
    """Attach a suggested `value`, its `source` and `needs_review` to each field."""
    saved = saved or {}
    questions = [{"label": f["label"], "type": f["type"], "options": [o["label"] for o in f["options"]]} for f in fields]
    context = f"{job.title} at {job.company}\n{job.description[:4000]}"
    suggestions = answers_svc.suggest(session, profile, questions, llm, context) if questions else []
    out = []
    for f, s in zip(fields, suggestions):
        value, source = s.get("answer", ""), s.get("source", "none")
        if f["name"] in saved:  # what the user already reviewed for this application wins
            value, source = saved[f["name"]], "reviewed"
        elif f["type"] in ("select", "multiselect") and value:
            mapped = _option_for(value, f["options"])
            # "Authorized to work?" answered from a free-text work authorization ("Indian citizen").
            if not mapped and source == "profile" and _is_yes_no(f["options"]) and answers_svc.LEGAL.search(f["label"]) \
                    and re.search(r"authori[sz]", f["label"], re.I):
                mapped = next(o["label"] for o in f["options"] if o["label"].strip().lower().startswith("y"))
            value = mapped
        elif f["type"] == "checkbox" and not (source == "saved" and value.lower() in ("yes", "true")):
            value = ""  # consent boxes are only ticked if you ticked them before
        out.append({**f, "value": value, "source": source if value else "none",
                    "needs_review": bool(s.get("needs_review")) or not value})
    return out
