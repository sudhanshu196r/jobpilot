"""Numbers behind the dashboard charts."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, col, select

from ..models import Application, Job, utcnow

FOLLOW_UP_AFTER_DAYS = 7
APPLIED_STATES = ("applied", "interview", "offer", "rejected")


def _day(dt: datetime | None) -> date | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone().date()


def dashboard(session: Session) -> dict:
    now = utcnow()
    today = now.astimezone().date()
    jobs = session.exec(select(Job).where(Job.status != "hidden")).all()
    open_jobs = [j for j in jobs if j.closed_at is None]
    apps = session.exec(select(Application)).all()
    by_status = Counter(a.status for a in apps)

    # Pipeline: each stage counts everything that reached it.
    applied = sum(by_status[s] for s in APPLIED_STATES)
    funnel = [
        {"stage": "Queued", "count": by_status["preparing"] + by_status["queued"] + by_status["in_progress"] + applied},
        {"stage": "Applied", "count": applied},
        {"stage": "Interview", "count": by_status["interview"] + by_status["offer"]},
        {"stage": "Offer", "count": by_status["offer"]},
    ]

    # Daily activity, last 30 days.
    days = [today - timedelta(days=i) for i in range(29, -1, -1)]
    applied_per_day = Counter(_day(a.applied_at) for a in apps if a.applied_at)
    found_per_day = Counter(_day(j.fetched_at) for j in jobs)
    activity = [{"date": d.isoformat(), "applied": applied_per_day.get(d, 0), "found": found_per_day.get(d, 0)}
                for d in days]

    # Match-score distribution of open jobs.
    bins = [0] * 10
    for j in open_jobs:
        if j.score is not None:
            bins[min(9, int(j.score // 10))] += 1
    scores = [{"range": f"{i * 10}–{i * 10 + 9 if i < 9 else 100}", "count": n} for i, n in enumerate(bins)]

    sources = Counter(j.source for j in open_jobs)
    strong = [j for j in open_jobs if (j.score or 0) >= 70]
    good = sorted((j for j in open_jobs if (j.score or 0) >= 50), key=lambda j: -(j.score or 0))[:300]

    # Skills that keep coming up as missing in your better matches.
    gaps: Counter[str] = Counter()
    for j in good:
        gaps.update(json.loads(j.score_detail or "{}").get("missing_skills", [])[:8])
    companies = Counter(j.company for j in good if j.company)

    # Week over week.
    week, prev = now - timedelta(days=7), now - timedelta(days=14)

    def aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    applied_week = sum(1 for a in apps if a.applied_at and aware(a.applied_at) >= week)
    applied_prev = sum(1 for a in apps if a.applied_at and prev <= aware(a.applied_at) < week)
    new_matches_week = sum(1 for j in open_jobs if (j.score or 0) >= 70 and aware(j.fetched_at) >= week)
    new_matches_prev = sum(1 for j in open_jobs if (j.score or 0) >= 70 and prev <= aware(j.fetched_at) < week)

    responded = by_status["interview"] + by_status["offer"] + by_status["rejected"]
    follow_ups = []
    for a in apps:
        if a.status != "applied" or not a.applied_at:
            continue
        last_touch = max(aware(a.applied_at), aware(a.followed_up_at) if a.followed_up_at else aware(a.applied_at))
        waited = (now - last_touch).days
        if waited >= FOLLOW_UP_AFTER_DAYS:
            job = session.get(Job, a.job_id)
            follow_ups.append({"application_id": a.id, "job_id": a.job_id, "days": waited,
                               "title": job.title if job else "", "company": job.company if job else "",
                               "url": job.url if job else ""})
    follow_ups.sort(key=lambda f: -f["days"])

    return {
        "kpis": {
            "open_jobs": len(open_jobs), "strong_matches": len(strong),
            "new_matches_week": new_matches_week, "new_matches_prev_week": new_matches_prev,
            "applied_total": applied, "applied_week": applied_week, "applied_prev_week": applied_prev,
            "in_queue": by_status["preparing"] + by_status["queued"] + by_status["in_progress"],
            "interviews": by_status["interview"] + by_status["offer"], "offers": by_status["offer"],
            "response_rate": round(responded / applied, 3) if applied else None,
        },
        "funnel": funnel,
        "activity": activity,
        "scores": scores,
        "sources": [{"source": s, "count": n} for s, n in sources.most_common(8)],
        "skill_gaps": [{"skill": s, "jobs": n} for s, n in gaps.most_common(8)],
        "top_companies": [{"company": c, "jobs": n} for c, n in companies.most_common(8)],
        "follow_ups": follow_ups[:10],
    }
