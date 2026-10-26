"""Persistence operations shared by routers, the scheduler and the extension API."""

from __future__ import annotations

import json
import logging
import threading
from datetime import timedelta
from pathlib import Path

from sqlmodel import Session, select

from ..config import config
from ..models import Application, Job, Profile, Resume, utcnow
from ..schemas import JobIn, KeywordReport, ProfileData, ResumeDoc
from . import matcher, render, settings_store, tailor
from .llm import get_llm
from .sources import build_sources
from .text import norm_key

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- profile


def load_profile(session: Session) -> ProfileData:
    row = session.get(Profile, 1)
    if row is None:
        return ProfileData()
    return ProfileData.model_validate_json(row.data or "{}")


def save_profile(session: Session, data: ProfileData, raw_text: str | None = None) -> ProfileData:
    data = data.normalized()
    row = session.get(Profile, 1) or Profile(id=1)
    row.data = data.model_dump_json()
    if raw_text is not None:
        row.raw_text = raw_text
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    matcher.rescore(session, data)
    return data


def has_profile(session: Session) -> bool:
    p = load_profile(session)
    return bool(p.experience or p.skills or p.contact.name)


# --------------------------------------------------------------------------- jobs


def dedupe_key(company: str, title: str, location: str) -> str:
    return "|".join((norm_key(company), norm_key(title), norm_key(location.split(",")[0] if location else "")))


def upsert_jobs(session: Session, items: list[JobIn]) -> tuple[list[Job], int]:
    """Insert new postings and refresh known ones. Returns (touched jobs, new count)."""
    touched: list[Job] = []
    created = 0
    for item in items:
        if not item.title.strip():
            continue
        existing = session.exec(
            select(Job).where(Job.source == item.source, Job.external_id == item.external_id)
        ).first()
        key = dedupe_key(item.company, item.title, item.location)
        if existing is None:
            dup = session.exec(select(Job).where(Job.dedupe_key == key, Job.source != item.source)).first()
            if dup is not None and key.strip("|"):
                # Same role seen on another source: enrich it rather than duplicate.
                if len(item.description) > len(dup.description):
                    dup.description = item.description
                touched.append(dup)
                session.add(dup)
                continue
            job = Job(**item.model_dump(), dedupe_key=key)
            session.add(job)
            touched.append(job)
            created += 1
        else:
            for field in ("url", "title", "company", "location", "remote", "salary", "experience_text", "posted_at"):
                val = getattr(item, field)
                if val not in ("", None):
                    setattr(existing, field, val)
            if item.apply_type != "unknown":
                existing.apply_type = item.apply_type
            # Listing pages give snippets; never overwrite a full description with one.
            if len(item.description) > len(existing.description):
                existing.description = item.description
            existing.dedupe_key = key
            existing.updated_at = utcnow()
            session.add(existing)
            touched.append(existing)
    session.commit()
    for j in touched:
        session.refresh(j)
    return touched, created


def job_dict(job: Job, include_description: bool = False) -> dict:
    d = job.model_dump(exclude={"description", "score_detail", "dedupe_key"})
    d["score_detail"] = json.loads(job.score_detail or "{}")
    if include_description:
        d["description"] = job.description
    else:
        d["snippet"] = job.description[:280]
    return d


_ingest_lock = threading.Lock()


def run_ingest(session: Session) -> dict:
    """Pull every configured source, store new postings, rescore."""
    if not _ingest_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        cfg = settings_store.get(session, "sources")
        sources, errors = build_sources(cfg)
        counts: dict[str, int] = {}
        fetched_total = new_total = 0
        all_touched: list[Job] = []
        for src in sources:
            label = f"{src.name}:{getattr(src, 'board', None) or getattr(src, 'company', None) or getattr(src, 'query', '')}"
            try:
                items = src.fetch()
            except Exception as exc:  # each source fails independently
                log.warning("Source %s failed: %s", label, exc)
                errors.append(str(exc))
                continue
            touched, created = upsert_jobs(session, items)
            all_touched += touched
            counts[label] = created
            fetched_total += len(items)
            new_total += created
        purged = _purge_stale(session)
        profile = load_profile(session)
        matcher.rescore(session, profile)
        result = {"status": "ok", "at": utcnow().isoformat(), "fetched": fetched_total, "new": new_total,
                  "per_source": counts, "errors": errors, "purged": purged, "sources": len(sources)}
        settings_store.put(session, "last_ingest", result)
        return result
    finally:
        _ingest_lock.release()


def _purge_stale(session: Session) -> int:
    """Drop old, untouched postings (never ones you shortlisted or applied to)."""
    days = int(settings_store.get(session, "ingest").get("max_age_days") or 0)
    if days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    applied = {a.job_id for a in session.exec(select(Application)).all()}
    with_resume = {r.job_id for r in session.exec(select(Resume)).all() if r.job_id}
    stale = session.exec(select(Job).where(Job.updated_at < cutoff, Job.status != "shortlisted")).all()
    n = 0
    for j in stale:
        if j.id not in applied and j.id not in with_resume:
            session.delete(j)
            n += 1
    session.commit()
    return n


# --------------------------------------------------------------------------- resumes


def resume_files_dir() -> Path:
    d = config.files_dir / "resumes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file_stem(resume: Resume, doc: ResumeDoc, job: Job | None) -> str:
    import re

    name = re.sub(r"[^A-Za-z0-9]+", "_", doc.contact.name or "Resume").strip("_")
    suffix = re.sub(r"[^A-Za-z0-9]+", "_", job.company)[:30].strip("_") if job else "General"
    return f"{resume.id}_{name}_{suffix or 'Job'}"


def render_resume(resume: Resume, job: Job | None) -> None:
    doc = ResumeDoc.model_validate_json(resume.data)
    stem = _file_stem(resume, doc, job)
    for old in (resume.pdf_path, resume.docx_path):
        if old and Path(old).name.split("_", 1)[0] == str(resume.id) and Path(old).exists():
            Path(old).unlink()
    resume.pdf_path = str(render.render_pdf(doc, resume_files_dir() / f"{stem}.pdf"))
    resume.docx_path = str(render.render_docx(doc, resume_files_dir() / f"{stem}.docx"))


def create_resume(session: Session, job: Job | None, use_llm: bool = True, with_cover_letter: bool = False) -> Resume:
    profile = load_profile(session)
    if not has_profile(session):
        raise ValueError("Add your profile first (Profile tab).")
    llm = get_llm(session) if use_llm else None
    result = tailor.tailor(
        profile,
        job_title=job.title if job else "",
        company=job.company if job else "",
        description=job.description if job else "",
        experience_text=job.experience_text if job else "",
        llm=llm,
    )
    resume = Resume(
        job_id=job.id if job else None,
        title=f"{job.title} — {job.company}" if job else "General resume",
        method=result.method,
        data=result.doc.model_dump_json(),
        keyword_report=result.report.model_dump_json(),
        notes=json.dumps(result.notes),
    )
    session.add(resume)
    session.commit()
    session.refresh(resume)
    if with_cover_letter and job is not None:
        resume.cover_letter = tailor.cover_letter(profile, result.doc, job.title, job.company, job.description, llm)
    render_resume(resume, job)
    session.add(resume)
    session.commit()
    session.refresh(resume)
    return resume


def update_resume_doc(session: Session, resume: Resume, doc: ResumeDoc) -> Resume:
    job = session.get(Job, resume.job_id) if resume.job_id else None
    profile = load_profile(session)
    resume.data = doc.model_dump_json()
    if job is not None:
        from . import jd_analyzer, profile_utils

        analysis = jd_analyzer.analyze(job.title, job.description, job.experience_text,
                                       extra_skills=profile_utils.profile_skills(profile))
        resume.keyword_report = tailor.keyword_report(doc, profile, analysis).model_dump_json()
    else:
        resume.keyword_report = KeywordReport(coverage=1.0).model_dump_json()
    resume.updated_at = utcnow()
    render_resume(resume, job)
    session.add(resume)
    session.commit()
    session.refresh(resume)
    return resume


def resume_dict(resume: Resume) -> dict:
    d = resume.model_dump(exclude={"data", "keyword_report", "notes", "pdf_path", "docx_path"})
    d["doc"] = json.loads(resume.data)
    d["keyword_report"] = json.loads(resume.keyword_report or "{}")
    d["notes"] = json.loads(resume.notes or "[]")
    d["has_pdf"] = bool(resume.pdf_path and Path(resume.pdf_path).exists())
    return d


def delete_resume(session: Session, resume: Resume) -> None:
    for p in (resume.pdf_path, resume.docx_path):
        if p and Path(p).exists():
            Path(p).unlink()
    for app_row in session.exec(select(Application).where(Application.resume_id == resume.id)).all():
        app_row.resume_id = None
        session.add(app_row)
    session.delete(resume)
    session.commit()


# --------------------------------------------------------------------------- applications


def application_dict(session: Session, app_row: Application) -> dict:
    d = app_row.model_dump()
    job = session.get(Job, app_row.job_id)
    d["job"] = job_dict(job) if job else None
    return d


def queue_applications(session: Session, job_ids: list[int]) -> list[Application]:
    """Create (or re-queue) applications in 'preparing' state, in the order given."""
    existing_max = max((a.queue_position for a in session.exec(select(Application)).all()), default=0)
    out = []
    for i, jid in enumerate(job_ids, start=1):
        job = session.get(Job, jid)
        if job is None:
            continue
        app_row = session.exec(select(Application).where(Application.job_id == jid)).first()
        if app_row and app_row.status in ("applied", "interview", "offer", "rejected"):
            out.append(app_row)
            continue
        app_row = app_row or Application(job_id=jid)
        app_row.status = "preparing"
        app_row.error = ""
        app_row.queue_position = existing_max + i
        app_row.updated_at = utcnow()
        session.add(app_row)
        if job.status == "new":
            job.status = "shortlisted"
            session.add(job)
        out.append(app_row)
    session.commit()
    for a in out:
        session.refresh(a)
    return out


def prepare_applications(app_ids: list[int], use_llm: bool, with_cover_letter: bool) -> None:
    """Background worker: tailor a resume for each queued application."""
    from ..db import session_scope

    for app_id in app_ids:
        with session_scope() as session:
            app_row = session.get(Application, app_id)
            if app_row is None or app_row.status != "preparing":
                continue
            job = session.get(Job, app_row.job_id)
            try:
                existing = session.exec(
                    select(Resume).where(Resume.job_id == job.id).order_by(Resume.created_at.desc())
                ).first()
                resume = existing if existing and (existing.cover_letter or not with_cover_letter) else \
                    create_resume(session, job, use_llm=use_llm, with_cover_letter=with_cover_letter)
                app_row.resume_id = resume.id
                app_row.status = "queued"
            except Exception as exc:
                log.exception("Preparing application %s failed", app_id)
                app_row.status = "failed"
                app_row.error = str(exc)[:500]
            app_row.updated_at = utcnow()
            session.add(app_row)
            session.commit()


def set_application_status(session: Session, app_row: Application, status: str, note: str = "") -> Application:
    app_row.status = status
    if status == "applied" and app_row.applied_at is None:
        app_row.applied_at = utcnow()
    if note:
        app_row.notes = (app_row.notes + "\n" + note).strip()
    app_row.updated_at = utcnow()
    session.add(app_row)
    session.commit()
    session.refresh(app_row)
    return app_row
