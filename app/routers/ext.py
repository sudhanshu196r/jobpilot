"""API for the browser extension. Every call needs the extension token."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from ..db import get_session
from ..models import Application, Job, Resume
from ..schemas import JobIn
from ..services import answers as answers_svc
from ..services import matcher, profile_utils, settings_store, store
from ..services.llm import get_llm


def require_token(x_jobpilot_token: str = Header(default=""), session: Session = Depends(get_session)) -> None:
    expected = settings_store.extension_token(session)
    if not x_jobpilot_token or not secrets.compare_digest(x_jobpilot_token, expected):
        raise HTTPException(401, "Invalid or missing extension token. Copy it from JobPilot → Settings.")


router = APIRouter(prefix="/api/ext", dependencies=[Depends(require_token)])


@router.get("/ping")
def ping(session: Session = Depends(get_session)):
    profile = store.load_profile(session)
    return {"ok": True, "name": profile.contact.name, "profile_ready": store.has_profile(session)}


class CapturedJob(BaseModel):
    source: Literal["naukri", "indeed", "linkedin", "other"]
    external_id: str = Field(min_length=1, max_length=200)
    url: str = ""
    title: str = Field(min_length=1)
    company: str = ""
    location: str = ""
    description: str = ""
    salary: str = ""
    experience_text: str = ""
    posted_at: str = ""
    remote: bool | None = None
    apply_type: Literal["easy", "external", "unknown"] = "unknown"


class CaptureBody(BaseModel):
    jobs: list[CapturedJob] = Field(max_length=200)


@router.post("/jobs")
def capture_jobs(body: CaptureBody, session: Session = Depends(get_session)):
    """Jobs read from a Naukri/Indeed page you are viewing. Returns scores for badges."""
    items = [JobIn(**j.model_dump()) for j in body.jobs]
    touched, created = store.upsert_jobs(session, items)
    matcher.rescore(session, store.load_profile(session), touched)
    by_key = {(j.source, j.external_id): j for j in touched}
    results = []
    for j in body.jobs:
        job = by_key.get((j.source, j.external_id))
        if job is None:  # merged into a duplicate from another source
            key = store.dedupe_key(j.company, j.title, j.location)
            job = session.exec(select(Job).where(Job.dedupe_key == key)).first()
        if job is not None:
            app_row = session.exec(select(Application).where(Application.job_id == job.id)).first()
            results.append({"external_id": j.external_id, "job_id": job.id, "score": job.score,
                            "application_status": app_row.status if app_row else None})
    return {"created": created, "results": results}


class QueueAdd(BaseModel):
    job_id: int
    use_llm: bool = True


@router.post("/queue/add")
def queue_add(body: QueueAdd, session: Session = Depends(get_session)):
    """Queue a single job from the page you are on (resume prepared synchronously)."""
    apps = store.queue_applications(session, [body.job_id])
    if not apps:
        raise HTTPException(404, "Job not found")
    if apps[0].status == "preparing":
        store.prepare_applications([apps[0].id], body.use_llm, False)
    session.expire_all()
    return store.application_dict(session, session.get(Application, apps[0].id))


@router.get("/queue")
def queue(session: Session = Depends(get_session)):
    apps = session.exec(
        select(Application).where(col(Application.status).in_(["queued", "in_progress"]))
        .order_by(col(Application.queue_position), col(Application.created_at))
    ).all()
    out = []
    for a in apps:
        job = session.get(Job, a.job_id)
        if job is None or not job.url:
            continue
        out.append({"application_id": a.id, "status": a.status, "resume_id": a.resume_id,
                    "job": {"id": job.id, "title": job.title, "company": job.company, "url": job.url,
                            "source": job.source, "apply_type": job.apply_type}})
    preparing = len(session.exec(select(Application).where(Application.status == "preparing")).all())
    return {"items": out, "preparing": preparing}


class StatusBody(BaseModel):
    status: Literal["in_progress", "applied", "skipped", "failed", "queued"]
    note: str = ""


@router.post("/applications/{app_id}/status")
def set_status(app_id: int, body: StatusBody, session: Session = Depends(get_session)):
    a = session.get(Application, app_id)
    if a is None:
        raise HTTPException(404, "Application not found")
    store.set_application_status(session, a, body.status, body.note)
    return {"ok": True, "status": a.status}


@router.post("/jobs/{job_id}/applied")
def mark_job_applied(job_id: int, session: Session = Depends(get_session)):
    """The extension saw a success message for a job that was not in the queue."""
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    a = session.exec(select(Application).where(Application.job_id == job_id)).first()
    if a is None:
        a = Application(job_id=job_id, status="applied")
    store.set_application_status(session, a, "applied", "Detected by extension")
    return {"ok": True}


@router.get("/resumes/{resume_id}/pdf")
def resume_pdf(resume_id: int, session: Session = Depends(get_session)):
    r = session.get(Resume, resume_id)
    if r is None:
        raise HTTPException(404, "Resume not found")
    if not r.pdf_path or not Path(r.pdf_path).exists():
        store.render_resume(r, session.get(Job, r.job_id) if r.job_id else None)
        session.add(r)
        session.commit()
    return FileResponse(r.pdf_path, media_type="application/pdf", filename=Path(r.pdf_path).name.split("_", 1)[-1])


@router.get("/resumes/{resume_id}/cover-letter")
def resume_cover_letter(resume_id: int, session: Session = Depends(get_session)):
    r = session.get(Resume, resume_id)
    if r is None:
        raise HTTPException(404, "Resume not found")
    return {"cover_letter": r.cover_letter}


@router.get("/autofill")
def autofill(session: Session = Depends(get_session)):
    """Flat profile facts for filling standard form fields."""
    p = store.load_profile(session)
    first, last = profile_utils.split_name(p.contact.name)
    current = next((e for e in p.experience if e.current), p.experience[0] if p.experience else None)
    latest_edu = p.education[0] if p.education else None
    return {
        "first_name": first, "last_name": last, "full_name": p.contact.name,
        "email": p.contact.email, "phone": p.contact.phone, "location": p.contact.location,
        "city": p.contact.location.split(",")[0].strip() if p.contact.location else "",
        "linkedin": p.contact.linkedin, "github": p.contact.github, "website": p.contact.website,
        "headline": p.headline, "summary": p.summary,
        "current_title": current.title if current else "", "current_company": current.company if current else "",
        "total_years": profile_utils.total_years(p),
        "notice_period_days": p.preferences.notice_period_days,
        "current_ctc": p.preferences.current_ctc, "expected_ctc": p.preferences.expected_ctc,
        "degree": latest_edu.degree if latest_edu else "", "school": latest_edu.institution if latest_edu else "",
        "skills": profile_utils.profile_skills(p)[:30],
    }


class Question(BaseModel):
    label: str
    type: str = ""
    options: list[str] = Field(default_factory=list)


class SuggestBody(BaseModel):
    questions: list[Question] = Field(max_length=80)
    job_id: int | None = None
    use_llm: bool = True


@router.post("/answers/suggest")
def suggest(body: SuggestBody, session: Session = Depends(get_session)):
    profile = store.load_profile(session)
    job = session.get(Job, body.job_id) if body.job_id else None
    ctx = f"{job.title} at {job.company}\n{job.description}" if job else ""
    llm = get_llm(session) if body.use_llm else None
    return {"answers": answers_svc.suggest(session, profile, [q.model_dump() for q in body.questions], llm, ctx)}


class SaveBody(BaseModel):
    answers: list[dict] = Field(max_length=120)


@router.post("/answers/save")
def save(body: SaveBody, session: Session = Depends(get_session)):
    return {"saved": answers_svc.save_answers(session, body.answers, store.load_profile(session))}
