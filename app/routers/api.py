"""JSON API used by the web UI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from ..db import get_session
from ..models import APPLICATION_STATUSES, JOB_STATUSES, Answer, Application, Job, Profile, Resume, utcnow
from ..schemas import JobIn, ProfileData, ResumeDoc
from ..services import answers as answers_svc
from ..services import matcher, profile_parser, profile_utils, settings_store, store, tailor
from ..services.llm import LLMError, get_llm

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- profile


@router.get("/profile")
def get_profile(session: Session = Depends(get_session)):
    profile = store.load_profile(session)
    row = session.get(Profile, 1)
    return {
        "profile": profile.model_dump(),
        "derived": {
            "total_years": profile_utils.total_years(profile),
            "skills": profile_utils.profile_skills(profile),
        },
        "updated_at": row.updated_at if row else None,
        "exists": store.has_profile(session),
    }


@router.put("/profile")
def put_profile(data: ProfileData, session: Session = Depends(get_session)):
    saved = store.save_profile(session, data)
    return {"profile": saved.model_dump(), "derived": {
        "total_years": profile_utils.total_years(saved), "skills": profile_utils.profile_skills(saved)}}


RESUME_TYPES = (".pdf", ".docx", ".txt", ".md")


@router.post("/profile/import")
async def import_profile(
    file: UploadFile | None = File(default=None),
    text: str = Form(default=""),
    use_llm: bool = Form(default=True),
    mode: Literal["replace", "merge"] = Form(default="replace"),
    save: bool = Form(default=False),
    session: Session = Depends(get_session),
):
    """Fill the profile from a resume.

    ``mode=replace`` builds the profile from the resume alone (job preferences
    are kept); ``mode=merge`` adds the resume's details to the existing profile
    without overwriting anything. With ``save=true`` the result is stored right
    away and the previous profile is returned so the UI can offer undo;
    otherwise it is only a draft for review.
    """
    if file is not None and file.filename:
        name = file.filename.lower()
        if name.endswith(".doc"):
            raise HTTPException(400, "Old .doc files aren't supported. Save it as .docx or PDF and upload again.")
        if not name.endswith(RESUME_TYPES):
            raise HTTPException(400, "Upload a PDF, DOCX or TXT resume.")
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(413, "File too large (max 10 MB).")
        try:
            text = profile_parser.extract_text(file.filename, content)
        except Exception as exc:
            raise HTTPException(400, f"Could not read file: {exc}") from exc
    if not text.strip():
        raise HTTPException(400, "No text found. If the PDF is a scanned image, paste the resume text instead.")
    llm = get_llm(session) if use_llm else None
    try:
        draft, method, warnings = profile_parser.parse_resume_text(text, llm)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    current = store.load_profile(session)
    if mode == "merge" and store.has_profile(session):
        result = profile_parser.merge_profiles(current, draft)
    else:
        draft.preferences = current.preferences  # a resume never contains these
        result = draft
    result = profile_parser.suggest_preferences(result)

    previous = None
    if save:
        previous = current.model_dump()
        result = store.save_profile(session, result, raw_text=text)
    else:
        row = session.get(Profile, 1) or Profile(id=1, data=current.model_dump_json())
        row.raw_text = text
        session.add(row)
        session.commit()
    return {
        "profile": result.model_dump(),
        "method": method,
        "mode": mode,
        "warnings": warnings,
        "report": profile_parser.import_report(result),
        "saved": save,
        "previous": previous,
        "derived": {"total_years": profile_utils.total_years(result), "skills": profile_utils.profile_skills(result)},
    }


# --------------------------------------------------------------------------- jobs


class ManualJob(BaseModel):
    title: str
    company: str = ""
    location: str = ""
    url: str = ""
    description: str = Field(min_length=20)
    remote: bool | None = None


class JobPatch(BaseModel):
    status: Literal["new", "shortlisted", "hidden"] | None = None


@router.get("/jobs")
def list_jobs(
    q: str = "",
    source: str = "",
    status: str = Query(default="active"),
    min_score: float = 0,
    remote: bool | None = None,
    sort: Literal["score", "recent", "company"] = "score",
    limit: int = Query(default=50, le=500),
    offset: int = 0,
    session: Session = Depends(get_session),
):
    stmt = select(Job)
    if status == "active":
        stmt = stmt.where(Job.status != "hidden")
    elif status in JOB_STATUSES:
        stmt = stmt.where(Job.status == status)
    if source:
        stmt = stmt.where(Job.source == source)
    if min_score:
        stmt = stmt.where(Job.score >= min_score)
    if remote is not None:
        stmt = stmt.where(Job.remote == remote)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(col(Job.title).ilike(like), col(Job.company).ilike(like),
                              col(Job.location).ilike(like), col(Job.description).ilike(like)))
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    order = {"score": (col(Job.score).desc().nulls_last(), col(Job.fetched_at).desc()),
             "recent": (col(Job.fetched_at).desc(),),
             "company": (col(Job.company), col(Job.title))}[sort]
    jobs = session.exec(stmt.order_by(*order).offset(offset).limit(limit)).all()
    app_by_job = {a.job_id: a for a in session.exec(
        select(Application).where(col(Application.job_id).in_([j.id for j in jobs]))).all()} if jobs else {}
    items = []
    for j in jobs:
        d = store.job_dict(j)
        a = app_by_job.get(j.id)
        d["application"] = {"id": a.id, "status": a.status} if a else None
        items.append(d)
    sources = [r for r in session.exec(select(Job.source).distinct()).all()]
    return {"total": total, "items": items, "sources": sources}


@router.post("/jobs")
def add_job(body: ManualJob, session: Session = Depends(get_session)):
    import hashlib

    ext = hashlib.sha1(f"{body.url or ''}|{body.title}|{body.company}".encode()).hexdigest()[:16]
    touched, _ = store.upsert_jobs(session, [JobIn(source="manual", external_id=ext, apply_type="unknown",
                                                    **body.model_dump())])
    matcher.rescore(session, store.load_profile(session), touched)
    return store.job_dict(touched[0], include_description=True)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    d = store.job_dict(job, include_description=True)
    a = session.exec(select(Application).where(Application.job_id == job_id)).first()
    d["application"] = store.application_dict(session, a) if a else None
    d["resumes"] = [{"id": r.id, "title": r.title, "method": r.method, "created_at": r.created_at}
                    for r in session.exec(select(Resume).where(Resume.job_id == job_id)
                                          .order_by(col(Resume.created_at).desc())).all()]
    return d


@router.patch("/jobs/{job_id}")
def patch_job(job_id: int, body: JobPatch, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if body.status:
        job.status = body.status
    job.updated_at = utcnow()
    session.add(job)
    session.commit()
    return store.job_dict(job)


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    for a in session.exec(select(Application).where(Application.job_id == job_id)).all():
        session.delete(a)
    for r in session.exec(select(Resume).where(Resume.job_id == job_id)).all():
        r.job_id = None
        session.add(r)
    session.delete(job)
    session.commit()
    return {"ok": True}


class BulkJobAction(BaseModel):
    job_ids: list[int]
    status: Literal["new", "shortlisted", "hidden"]


@router.post("/jobs/bulk-status")
def bulk_status(body: BulkJobAction, session: Session = Depends(get_session)):
    jobs = session.exec(select(Job).where(col(Job.id).in_(body.job_ids))).all()
    for j in jobs:
        j.status = body.status
        session.add(j)
    session.commit()
    return {"updated": len(jobs)}


@router.post("/jobs/rescore")
def rescore(session: Session = Depends(get_session)):
    return {"rescored": matcher.rescore(session, store.load_profile(session))}


class _Assessment(BaseModel):
    fit_score: int = Field(description="0-100 overall fit")
    verdict: Literal["strong", "good", "stretch", "poor"]
    strengths: list[str]
    gaps: list[str]
    advice: str


@router.post("/jobs/{job_id}/assess")
def assess_job(job_id: int, session: Session = Depends(get_session)):
    """Ask the model for a recruiter-style fit assessment (needs an API key)."""
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    llm = get_llm(session)
    if llm is None:
        raise HTTPException(400, "Add an Anthropic API key in Settings to use AI assessment.")
    profile = store.load_profile(session)
    try:
        out = llm.structured(
            system=("You are a senior technical recruiter. Assess honestly how well this candidate fits the job, "
                    "based only on the profile. Strengths and gaps: 2-5 short items each. Advice: one or two "
                    "sentences on how to position the application (truthfully)."),
            prompt=(f"<job>\n{job.title} at {job.company} ({job.location})\n\n{job.description[:12000]}\n</job>\n"
                    f"<profile>\n{profile.model_dump_json(exclude={'preferences'})}\n</profile>"),
            schema=_Assessment, max_tokens=4000)
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
    detail = json.loads(job.score_detail or "{}")
    detail["llm"] = out.model_dump()
    job.score_detail = json.dumps(detail)
    session.add(job)
    session.commit()
    return detail["llm"]


# --------------------------------------------------------------------------- ingestion


@router.post("/ingest/run")
def ingest_run(session: Session = Depends(get_session)):
    return store.run_ingest(session)


@router.get("/ingest/status")
def ingest_status(session: Session = Depends(get_session)):
    return settings_store.get(session, "last_ingest") or {}


# --------------------------------------------------------------------------- resumes


class ResumeCreate(BaseModel):
    job_id: int | None = None
    use_llm: bool = True
    cover_letter: bool = False


@router.get("/resumes")
def list_resumes(job_id: int | None = None, session: Session = Depends(get_session)):
    stmt = select(Resume).order_by(col(Resume.created_at).desc())
    if job_id is not None:
        stmt = stmt.where(Resume.job_id == job_id)
    out = []
    for r in session.exec(stmt).all():
        d = store.resume_dict(r)
        d.pop("doc")
        out.append(d)
    return out


@router.post("/resumes")
def create_resume(body: ResumeCreate, session: Session = Depends(get_session)):
    job = None
    if body.job_id is not None:
        job = session.get(Job, body.job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
    try:
        resume = store.create_resume(session, job, use_llm=body.use_llm, with_cover_letter=body.cover_letter)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return store.resume_dict(resume)


def _resume_or_404(session: Session, resume_id: int) -> Resume:
    r = session.get(Resume, resume_id)
    if r is None:
        raise HTTPException(404, "Resume not found")
    return r


@router.get("/resumes/{resume_id}")
def get_resume(resume_id: int, session: Session = Depends(get_session)):
    return store.resume_dict(_resume_or_404(session, resume_id))


class ResumeUpdate(BaseModel):
    doc: ResumeDoc
    cover_letter: str | None = None


@router.put("/resumes/{resume_id}")
def update_resume(resume_id: int, body: ResumeUpdate, session: Session = Depends(get_session)):
    r = _resume_or_404(session, resume_id)
    if body.cover_letter is not None:
        r.cover_letter = body.cover_letter
    return store.resume_dict(store.update_resume_doc(session, r, body.doc))


@router.post("/resumes/{resume_id}/cover-letter")
def make_cover_letter(resume_id: int, session: Session = Depends(get_session)):
    r = _resume_or_404(session, resume_id)
    job = session.get(Job, r.job_id) if r.job_id else None
    profile = store.load_profile(session)
    doc = ResumeDoc.model_validate_json(r.data)
    r.cover_letter = tailor.cover_letter(profile, doc, job.title if job else "", job.company if job else "",
                                         job.description if job else "", get_llm(session))
    session.add(r)
    session.commit()
    return {"cover_letter": r.cover_letter}


@router.get("/resumes/{resume_id}/file/{kind}")
def download_resume(resume_id: int, kind: Literal["pdf", "docx"], inline: bool = False,
                    session: Session = Depends(get_session)):
    r = _resume_or_404(session, resume_id)
    path = r.pdf_path if kind == "pdf" else r.docx_path
    if not path or not Path(path).exists():
        store.render_resume(r, session.get(Job, r.job_id) if r.job_id else None)
        session.add(r)
        session.commit()
        path = r.pdf_path if kind == "pdf" else r.docx_path
    media = "application/pdf" if kind == "pdf" else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    name = Path(path).name.split("_", 1)[-1]
    return FileResponse(path, media_type=media, filename=name,
                        content_disposition_type="inline" if inline else "attachment")


@router.delete("/resumes/{resume_id}")
def delete_resume(resume_id: int, session: Session = Depends(get_session)):
    store.delete_resume(session, _resume_or_404(session, resume_id))
    return {"ok": True}


# --------------------------------------------------------------------------- applications


class BulkApply(BaseModel):
    job_ids: list[int] = Field(min_length=1, max_length=200)
    use_llm: bool = True
    cover_letter: bool = False


@router.post("/applications/bulk")
def bulk_apply(body: BulkApply, background: BackgroundTasks, session: Session = Depends(get_session)):
    """Queue many jobs: a tailored resume is prepared for each in the background."""
    if not store.has_profile(session):
        raise HTTPException(400, "Add your profile first (Profile tab).")
    apps = store.queue_applications(session, body.job_ids)
    to_prepare = [a.id for a in apps if a.status == "preparing"]
    background.add_task(store.prepare_applications, to_prepare, body.use_llm, body.cover_letter)
    return {"queued": len(to_prepare), "skipped_already_applied": len(apps) - len(to_prepare)}


@router.get("/applications")
def list_applications(status: str = "", session: Session = Depends(get_session)):
    stmt = select(Application).order_by(col(Application.queue_position), col(Application.created_at))
    if status:
        stmt = stmt.where(col(Application.status).in_(status.split(",")))
    return [store.application_dict(session, a) for a in session.exec(stmt).all()]


class ApplicationPatch(BaseModel):
    status: str | None = None
    notes: str | None = None
    resume_id: int | None = None


@router.patch("/applications/{app_id}")
def patch_application(app_id: int, body: ApplicationPatch, session: Session = Depends(get_session)):
    a = session.get(Application, app_id)
    if a is None:
        raise HTTPException(404, "Application not found")
    if body.status is not None:
        if body.status not in APPLICATION_STATUSES:
            raise HTTPException(400, f"Unknown status {body.status}")
        if body.status == "queued" and a.resume_id is None and body.resume_id is None:
            raise HTTPException(400, "Generate a resume before queueing.")
        a.status = body.status
        if body.status == "applied" and a.applied_at is None:
            a.applied_at = utcnow()
    if body.notes is not None:
        a.notes = body.notes
    if body.resume_id is not None:
        _resume_or_404(session, body.resume_id)
        a.resume_id = body.resume_id
    a.updated_at = utcnow()
    session.add(a)
    session.commit()
    return store.application_dict(session, a)


@router.delete("/applications/{app_id}")
def delete_application(app_id: int, session: Session = Depends(get_session)):
    a = session.get(Application, app_id)
    if a is None:
        raise HTTPException(404, "Application not found")
    session.delete(a)
    session.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- answers


class AnswerIn(BaseModel):
    question: str
    answer: str


@router.get("/answers")
def list_answers(session: Session = Depends(get_session)):
    return session.exec(select(Answer).order_by(col(Answer.updated_at).desc())).all()


@router.post("/answers")
def add_answer(body: AnswerIn, session: Session = Depends(get_session)):
    answers_svc.save_answers(session, [{"label": body.question, "answer": body.answer}])
    return session.exec(select(Answer).where(Answer.key == answers_svc.normalize_question(body.question))).first()


@router.put("/answers/{answer_id}")
def update_answer(answer_id: int, body: AnswerIn, session: Session = Depends(get_session)):
    row = session.get(Answer, answer_id)
    if row is None:
        raise HTTPException(404, "Answer not found")
    row.question, row.answer = body.question, body.answer
    row.key = answers_svc.normalize_question(body.question)
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    return row


@router.delete("/answers/{answer_id}")
def delete_answer(answer_id: int, session: Session = Depends(get_session)):
    row = session.get(Answer, answer_id)
    if row is None:
        raise HTTPException(404, "Answer not found")
    session.delete(row)
    session.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- settings / stats


@router.get("/settings")
def get_settings(session: Session = Depends(get_session)):
    out = settings_store.public_settings(session)
    out["extension_token"] = settings_store.extension_token(session)
    return out


@router.put("/settings")
def put_settings(body: dict, session: Session = Depends(get_session)):
    settings_store.update_settings(session, body)
    return get_settings(session)


@router.post("/settings/rotate-token")
def rotate_token(session: Session = Depends(get_session)):
    return {"extension_token": settings_store.extension_token(session, rotate=True)}


@router.post("/settings/test-llm")
def test_llm(session: Session = Depends(get_session)):
    llm = get_llm(session)
    if llm is None:
        raise HTTPException(400, "No API key configured, or AI features are disabled.")

    class _Ping(BaseModel):
        ok: bool

    try:
        llm.structured(system="Reply with ok=true.", prompt="ping", schema=_Ping, max_tokens=1000)
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, "model": llm.model}


@router.get("/stats")
def stats(session: Session = Depends(get_session)):
    jobs = session.exec(select(func.count()).select_from(Job).where(Job.status != "hidden")).one()
    strong = session.exec(select(func.count()).select_from(Job).where(Job.status != "hidden", Job.score >= 70)).one()
    by_status = dict(session.exec(select(Application.status, func.count()).group_by(Application.status)).all())
    resumes = session.exec(select(func.count()).select_from(Resume)).one()
    return {
        "jobs": jobs, "strong_matches": strong, "resumes": resumes, "applications": by_status,
        "profile_ready": store.has_profile(session),
        "ai_enabled": get_llm(session) is not None,
        "last_ingest": settings_store.get(session, "last_ingest"),
    }
