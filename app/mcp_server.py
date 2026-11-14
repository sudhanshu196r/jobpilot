"""JobPilot as an MCP server, so an AI assistant (Claude Desktop, Claude Code,
or any MCP client) can browse your matches, tailor resumes and manage the
apply queue in conversation.

Run:  python jobpilot_mcp.py      (stdio transport; see README for client config)

It works on the same local database as the web app, which does not need to
be running. Submitting applications stays with you and the browser extension.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from sqlalchemy import or_
from sqlmodel import col, select

from . import __version__
from .db import init_db, session_scope
from .models import APPLICATION_STATUSES, Application, Job, Resume
from .services import liveness, profile_utils, store

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
NETWORK = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

server = MCPServer(
    name="jobpilot",
    title="JobPilot",
    version=__version__,
    instructions=(
        "JobPilot is the user's personal job-search database. Use search_jobs to find matches "
        "(score 0-100 against their profile), get_job for details, tailor_resume to build a resume "
        "for one job, and queue_applications to add jobs to the apply queue; the user submits "
        "applications themselves with the JobPilot browser extension. Resumes only ever use facts "
        "from the user's profile. Prefer open, high-scoring, recent jobs."
    ),
)


def _job_brief(job: Job) -> dict:
    detail = json.loads(job.score_detail or "{}")
    fresh = liveness.freshness(job)
    return {
        "job_id": job.id, "title": job.title, "company": job.company, "location": job.location,
        "remote": job.remote, "score": job.score, "source": job.source, "url": job.url,
        "salary": job.salary or None, "age_days": fresh["age_days"], "closed": fresh["closed"],
        "status": job.status, "matched_skills": detail.get("matched_skills", [])[:8],
        "missing_skills": detail.get("missing_skills", [])[:6],
    }


@server.tool(annotations=READ_ONLY)
def search_jobs(query: str = "", min_score: float = 0, remote_only: bool = False, new_within_days: int = 0,
                include_closed: bool = False, limit: int = 20) -> list[dict]:
    """Search the user's jobs, best matches first.

    query matches title, company, location or description. new_within_days limits to jobs found
    in the last N days (0 = any time). Closed postings are excluded unless include_closed.
    """
    with session_scope() as s:
        stmt = select(Job).where(Job.status != "hidden")
        if not include_closed:
            stmt = stmt.where(col(Job.closed_at).is_(None))
        if min_score:
            stmt = stmt.where(col(Job.score) >= min_score)
        if remote_only:
            stmt = stmt.where(col(Job.remote).is_(True))
        if new_within_days:
            from datetime import timedelta

            from .models import utcnow
            stmt = stmt.where(Job.fetched_at >= utcnow() - timedelta(days=new_within_days))
        if query:
            like = f"%{query}%"
            stmt = stmt.where(or_(col(Job.title).ilike(like), col(Job.company).ilike(like),
                                  col(Job.location).ilike(like), col(Job.description).ilike(like)))
        jobs = s.exec(stmt.order_by(col(Job.score).desc().nulls_last()).limit(max(1, min(limit, 100)))).all()
        return [_job_brief(j) for j in jobs]


@server.tool(annotations=READ_ONLY)
def get_job(job_id: int) -> dict:
    """Full details of one job: description, score breakdown and application status."""
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise ValueError(f"No job with id {job_id}")
        out = _job_brief(job)
        detail = json.loads(job.score_detail or "{}")
        out.update(description=job.description[:8000], experience_required=job.experience_text or None,
                   score_components=detail.get("components"), score_reasons=detail.get("reasons"),
                   ai_assessment=detail.get("llm"))
        app_row = s.exec(select(Application).where(Application.job_id == job_id)).first()
        out["application"] = {"application_id": app_row.id, "status": app_row.status} if app_row else None
        return out


@server.tool(annotations=READ_ONLY)
def get_profile() -> dict:
    """The user's profile summary: headline, experience, skills and job preferences."""
    with session_scope() as s:
        p = store.load_profile(s)
        return {
            "name": p.contact.name, "headline": p.headline, "location": p.contact.location,
            "total_years": profile_utils.total_years(p), "skills": profile_utils.profile_skills(p),
            "experience": [{"title": e.title, "company": e.company, "start": e.start, "end": e.end or "present",
                            "highlights": [b.text for b in e.bullets[:4]]} for e in p.experience],
            "education": [{"degree": e.degree, "institution": e.institution} for e in p.education],
            "preferences": p.preferences.model_dump(),
        }


@server.tool(annotations=LOCAL_WRITE)
def tailor_resume(job_id: int, use_ai: bool = True, cover_letter: bool = False) -> dict:
    """Build a resume tailored to one job (PDF + DOCX saved locally). Returns where the files are
    and how well the resume covers the job's skills."""
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise ValueError(f"No job with id {job_id}")
        r = store.create_resume(s, job, use_llm=use_ai, with_cover_letter=cover_letter)
        report = json.loads(r.keyword_report or "{}")
        return {"resume_id": r.id, "method": r.method, "pdf_path": r.pdf_path, "docx_path": r.docx_path,
                "coverage": report.get("coverage"), "matched_skills": report.get("matched", []),
                "skills_you_lack": report.get("missing_entirely", []),
                "cover_letter": r.cover_letter or None, "safety_notes": json.loads(r.notes or "[]")}


@server.tool(annotations=LOCAL_WRITE)
def queue_applications(job_ids: list[int], use_ai: bool = True) -> dict:
    """Add jobs to the apply queue, tailoring a resume for each (at most 10 per call).
    Closed and already-applied jobs are skipped. The user then applies with the extension."""
    if len(job_ids) > 10:
        raise ValueError("Queue at most 10 jobs per call.")
    with session_scope() as s:
        if not store.has_profile(s):
            raise ValueError("The user has no profile yet; ask them to upload a resume in JobPilot.")
        jobs = {j.id: j for j in s.exec(select(Job).where(col(Job.id).in_(job_ids))).all()}
        closed = [i for i in job_ids if i in jobs and jobs[i].closed_at is not None]
        missing = [i for i in job_ids if i not in jobs]
        apps = store.queue_applications(s, [i for i in job_ids if i in jobs and i not in closed])
        to_prepare = [a.id for a in apps if a.status == "preparing"]
    store.prepare_applications(to_prepare, use_ai, False)
    with session_scope() as s:
        rows = [s.get(Application, a) for a in to_prepare]
        return {"queued": [{"application_id": a.id, "job_id": a.job_id, "status": a.status, "error": a.error or None}
                           for a in rows if a],
                "skipped_closed": closed, "not_found": missing,
                "already_applied": [a.job_id for a in apps if a.id not in to_prepare]}


@server.tool(annotations=READ_ONLY)
def list_applications(status: str = "") -> list[dict]:
    """The application tracker. status filters by one or more comma-separated statuses
    (preparing, queued, in_progress, applied, skipped, failed, interview, offer, rejected)."""
    with session_scope() as s:
        stmt = select(Application).order_by(col(Application.updated_at).desc())
        if status:
            stmt = stmt.where(col(Application.status).in_([x.strip() for x in status.split(",")]))
        out = []
        for a in s.exec(stmt).all():
            job = s.get(Job, a.job_id)
            out.append({"application_id": a.id, "status": a.status, "job_id": a.job_id,
                        "title": job.title if job else None, "company": job.company if job else None,
                        "resume_id": a.resume_id, "applied_at": a.applied_at.isoformat() if a.applied_at else None,
                        "notes": a.notes or None})
        return out


@server.tool(annotations=LOCAL_WRITE)
def update_application(application_id: int,
                       status: Literal["queued", "applied", "skipped", "interview", "offer", "rejected"],
                       note: str = "") -> dict:
    """Record progress on an application, e.g. an interview invitation or a rejection."""
    assert status in APPLICATION_STATUSES
    with session_scope() as s:
        a = s.get(Application, application_id)
        if a is None:
            raise ValueError(f"No application with id {application_id}")
        if status == "queued" and a.resume_id is None:
            raise ValueError("This application has no resume yet; use queue_applications.")
        store.set_application_status(s, a, status, note)
        return {"application_id": a.id, "status": a.status}


@server.tool(annotations=NETWORK)
def fetch_new_jobs() -> dict:
    """Pull new postings from the user's configured job sources now and re-rank everything."""
    with session_scope() as s:
        r = store.run_ingest(s)
        return {k: r.get(k) for k in ("status", "fetched", "new", "closed", "errors")}


@server.tool(annotations=NETWORK)
def check_job_open(job_ids: list[int]) -> dict:
    """Re-check whether postings are still accepting applications (open / closed / unknown)."""
    with session_scope() as s:
        jobs = list(s.exec(select(Job).where(col(Job.id).in_(job_ids[:50]))).all())
        return {str(k): v for k, v in liveness.check_jobs(s, jobs).items()}


@server.tool(annotations=READ_ONLY)
def list_resumes(job_id: int | None = None) -> list[dict]:
    """Tailored resumes on file (newest first), optionally for one job."""
    with session_scope() as s:
        stmt = select(Resume).order_by(col(Resume.created_at).desc())
        if job_id is not None:
            stmt = stmt.where(Resume.job_id == job_id)
        return [{"resume_id": r.id, "job_id": r.job_id, "title": r.title, "method": r.method,
                 "pdf_path": r.pdf_path, "created_at": r.created_at.isoformat()} for r in s.exec(stmt.limit(50)).all()]


def main() -> None:
    # stdout carries the protocol; logs must go to stderr.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    init_db()
    server.run("stdio")


if __name__ == "__main__":
    main()
