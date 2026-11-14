"""Is a posting still open, and how fresh is it?

Two signals mark a job closed:
  * board sync: a Greenhouse/Lever/Ashby posting that no longer appears in its
    company's board feed has been taken down;
  * page check: the posting URL returns 404/410, or its page says applications
    are closed.
A site that blocks the check (403, captcha, timeout) leaves the job "unknown",
never closed: we only close what we can prove.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from sqlmodel import Session, col, select

from ..models import Job, utcnow
from .sources.base import USER_AGENT

BOARD_SOURCES = ("greenhouse", "lever", "ashby")
STALE_DAYS = 30

CLOSED_RE = re.compile(
    r"no longer (?:accepting applications|available|active|open)|"
    r"(?:job|position|posting|vacancy|role) (?:has )?(?:expired|been filled|been closed|is closed|is no longer)|"
    r"this job has expired|applications? (?:are |is )?(?:now )?closed|"
    r"job (?:you are looking for|you're looking for) (?:is no longer|doesn't exist|does not exist)",
    re.I,
)

Status = Literal["open", "closed", "unknown"]


# --------------------------------------------------------------------------- freshness


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_posted(value: str, now: datetime) -> datetime | None:
    """`now` is when the text was captured: "3 days ago" is relative to that."""
    v = (value or "").strip().lower()
    if not v:
        return None
    if re.fullmatch(r"\d{12,13}", v):  # Lever: epoch milliseconds
        return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc)
    if m := re.match(r"(\d{4}-\d{2}-\d{2})(?:[t ](\d{2}:\d{2}(?::\d{2})?))?", v):
        try:
            dt = datetime.fromisoformat(m[1] + (f"T{m[2]}" if m[2] else ""))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if re.search(r"just now|today|few hours|\bhours? ago|active today|just posted", v):
        return now
    if "yesterday" in v:
        return now - timedelta(days=1)
    # "3 days ago", "30+ days ago", "Posted 2 weeks ago", "1 month ago"
    if m := re.search(r"(\d+)\+?\s*(day|week|month)s?\s*ago", v):
        n = int(m[1]) * {"day": 1, "week": 7, "month": 30}[m[2]]
        return now - timedelta(days=n)
    return None


def age_days(job: Job, now: datetime | None = None) -> int:
    """Days since the posting went up. A job first seen N days ago is at least N days old."""
    now = now or utcnow()
    posted = _parse_posted(job.posted_at, _aware(job.updated_at))
    seen = _aware(job.fetched_at)
    ref = min(posted, seen) if posted else seen
    return max(0, (now - ref).days)


def freshness(job: Job, now: datetime | None = None) -> dict:
    days = age_days(job, now)
    return {"age_days": days, "stale": days > STALE_DAYS, "closed": job.closed_at is not None}


# --------------------------------------------------------------------------- board sync


def sync_board(session: Session, source: str, prefix: str, seen_ids: set[str]) -> int:
    """Close this board's open jobs that are missing from its latest feed."""
    if source not in BOARD_SOURCES:
        return 0
    jobs = session.exec(select(Job).where(
        Job.source == source, col(Job.external_id).startswith(f"{prefix}:"), col(Job.closed_at).is_(None))).all()
    closed = 0
    for job in jobs:
        if job.external_id not in seen_ids:
            job.closed_at = utcnow()
            job.last_checked_at = utcnow()
            session.add(job)
            closed += 1
    session.commit()
    return closed


# --------------------------------------------------------------------------- page check


def check_url(url: str, client: httpx.Client) -> Status:
    if not url.startswith(("http://", "https://")):
        return "unknown"
    try:
        resp = client.get(url)
    except httpx.HTTPError:
        return "unknown"
    if resp.status_code in (404, 410):
        return "closed"
    if resp.status_code >= 400:
        return "unknown"  # blocked or erroring: can't tell
    if CLOSED_RE.search(resp.text[:300_000]):
        return "closed"
    # Lever and Greenhouse bounce closed postings to the board's index page.
    final = str(resp.url).rstrip("/")
    if final != url.rstrip("/") and re.search(r"(jobs\.lever\.co|greenhouse\.io)/[^/]+$", final):
        return "closed"
    return "open"


def check_jobs(session: Session, jobs: list[Job], client: httpx.Client | None = None) -> dict[int, Status]:
    """Check many postings in parallel and record the result."""
    own = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=True,
                                    headers={"User-Agent": USER_AGENT.replace("JobPilot/1.0", "Mozilla/5.0")})
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(pool.map(lambda j: check_url(j.url, client), jobs))
    finally:
        if own:
            client.close()
    out: dict[int, Status] = {}
    for job, status in zip(jobs, statuses):
        job.last_checked_at = utcnow()
        if status == "closed" and job.closed_at is None:
            job.closed_at = utcnow()
        elif status == "open":
            job.closed_at = None
        session.add(job)
        out[job.id] = status
    session.commit()
    return out
