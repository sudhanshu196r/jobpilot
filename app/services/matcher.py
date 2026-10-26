"""Ranks jobs against the profile.

Score (0-100) is a weighted blend of:
  skills      45  weighted share of the JD's skills that the profile evidences
  text        20  TF-IDF cosine between profile and description
  title       15  overlap between the job title and target/held titles
  experience  15  stated years requirement vs. the profile's total years
  location     5  remote / preferred-location fit
Hard exclusions (blocked companies or keywords) force the score to 0.
"""

from __future__ import annotations

import json
import re

from sqlmodel import Session, select

from ..models import Job, utcnow
from ..schemas import ProfileData, ScoreDetail
from . import jd_analyzer, profile_utils
from .text import TfIdf, norm_key, tokenize

WEIGHTS = {"skills": 45, "text": 20, "title": 15, "experience": 15, "location": 5}
SKILL_PRIOR, SKILL_PRIOR_WEIGHT = 0.3, 2.0

_TITLE_SYNONYMS = {
    "sr": "senior", "snr": "senior", "jr": "junior", "eng": "engineer", "engg": "engineer",
    "dev": "developer", "developers": "developer", "engineers": "engineer", "mgr": "manager",
    "swe": "software engineer", "sde": "software engineer", "fe": "frontend", "be": "backend",
    "front-end": "frontend", "back-end": "backend", "full-stack": "fullstack", "full": "fullstack",
    "ml": "machine learning", "ai": "artificial intelligence", "qa": "quality assurance",
}
_TITLE_NOISE = {"stack", "i", "ii", "iii", "iv", "1", "2", "3", "level", "l1", "l2", "l3", "-", "&", "and", "the", "of"}
_SENIORITY = {"intern": 0, "trainee": 0, "junior": 1, "associate": 1, "senior": 3, "lead": 4, "staff": 4,
              "principal": 5, "architect": 5, "manager": 4, "head": 6, "director": 6, "vp": 7}


def _title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9\-]+", title.lower())
    out: set[str] = set()
    for w in words:
        out.update(_TITLE_SYNONYMS.get(w, w).split())
    return out - _TITLE_NOISE


def _title_score(job_title: str, titles: list[str]) -> float:
    jt = _title_tokens(job_title)
    if not jt or not titles:
        return 0.5 if not titles else 0.0
    best = 0.0
    for t in titles:
        tt = _title_tokens(t)
        if not tt:
            continue
        core_j, core_t = jt - set(_SENIORITY), tt - set(_SENIORITY)
        overlap = len(core_j & core_t) / max(1, len(core_t | core_j))
        if core_t and core_t <= core_j:
            overlap = max(overlap, 0.85)
        best = max(best, overlap)
    return min(1.0, best)


def _experience_score(years: float, lo: int | None, hi: int | None) -> tuple[float, str | None]:
    if lo is None:
        return 0.7, None
    if years >= lo and (hi is None or years <= hi + 3):
        return 1.0, f"Your {years:g} yrs fits the {lo}{'-' + str(hi) if hi else '+'} yrs ask"
    if years < lo:
        gap = lo - years
        return max(0.0, 1 - gap / 3), f"Asks {lo}+ yrs; you have {years:g}"
    return 0.75, f"You may be overqualified ({years:g} yrs vs {lo}-{hi})"


def _location_score(job: Job, profile: ProfileData, jd_remote: bool) -> float:
    prefs = profile.preferences
    remote = bool(job.remote) or jd_remote or "remote" in job.location.lower()
    if remote and prefs.remote_ok:
        return 1.0
    if not prefs.locations:
        return 0.7
    loc = norm_key(job.location)
    for p in prefs.locations:
        pk = norm_key(p)
        if pk and (pk in loc or (loc and loc in pk)):
            return 1.0
    return 0.15


def _excluded(job: Job, profile: ProfileData) -> str | None:
    prefs = profile.preferences
    company = norm_key(job.company)
    for c in prefs.excluded_companies:
        if norm_key(c) and norm_key(c) == company:
            return f"Company '{job.company}' is excluded"
    hay = f"{job.title}\n{job.description}".lower()
    for k in prefs.excluded_keywords:
        if k and re.search(rf"\b{re.escape(k.lower())}\b", hay):
            return f"Contains excluded keyword '{k}'"
    return None


class Scorer:
    """Scores many jobs against one profile with a shared IDF."""

    def __init__(self, profile: ProfileData, corpus: list[str]):
        self.profile = profile
        self.skills = profile_utils.profile_skills(profile)
        self.skill_set = {s.lower() for s in self.skills}
        self.years = profile_utils.total_years(profile)
        self.titles = [*profile.preferences.target_titles, profile.headline, *(e.title for e in profile.experience[:3])]
        self.titles = [t for t in self.titles if t]
        ptext = profile_utils.profile_text(profile)
        self.tfidf = TfIdf([tokenize(d) for d in corpus] + [tokenize(ptext)])
        self.pvec = self.tfidf.vector(tokenize(ptext))

    def score(self, job: Job) -> ScoreDetail:
        detail = ScoreDetail()
        analysis = jd_analyzer.analyze(job.title, job.description, job.experience_text, extra_skills=self.skills)
        detail.jd_years = [analysis.min_years, analysis.max_years]

        total_w = sum(analysis.skills.values())
        matched = [s for s in analysis.skills if s.lower() in self.skill_set]
        missing = [s for s in analysis.skills if s.lower() not in self.skill_set]
        # Shrink toward a neutral prior so a JD naming one or two skills can't
        # earn full marks from a single coincidental match.
        skill_score = (sum(analysis.skills[s] for s in matched) + SKILL_PRIOR * SKILL_PRIOR_WEIGHT) / \
            (total_w + SKILL_PRIOR_WEIGHT)
        detail.matched_skills, detail.missing_skills = matched, missing

        text_score = min(1.0, TfIdf.cosine(self.pvec, self.tfidf.vector(tokenize(f"{job.title} {job.description}"))) * 2.5)
        title_score = _title_score(job.title, self.titles)
        exp_score, exp_reason = _experience_score(self.years, analysis.min_years, analysis.max_years)
        loc_score = _location_score(job, self.profile, analysis.remote)

        comps = {"skills": skill_score, "text": text_score, "title": title_score,
                 "experience": exp_score, "location": loc_score}
        detail.components = {k: round(v, 3) for k, v in comps.items()}
        detail.score = round(sum(WEIGHTS[k] * v for k, v in comps.items()), 1)

        if total_w:
            detail.reasons.append(f"Matches {len(matched)}/{len(analysis.skills)} skills in the JD")
        if exp_reason:
            detail.reasons.append(exp_reason)
        if title_score >= 0.6:
            detail.reasons.append("Title aligns with your target roles")
        if loc_score >= 1.0:
            detail.reasons.append("Location/remote fits your preferences")
        elif loc_score < 0.5:
            detail.reasons.append("Outside your preferred locations")
        if why := _excluded(job, self.profile):
            detail.excluded = True
            detail.score = 0.0
            detail.reasons.insert(0, why)
        return detail


def rescore(session: Session, profile: ProfileData, jobs: list[Job] | None = None) -> int:
    """Recompute scores. IDF always uses the whole corpus for comparability."""
    all_jobs = session.exec(select(Job)).all()
    targets = all_jobs if jobs is None else jobs
    if not targets:
        return 0
    scorer = Scorer(profile, [f"{j.title} {j.description}" for j in all_jobs])
    for job in targets:
        detail = scorer.score(job)
        old = json.loads(job.score_detail or "{}")
        if old.get("llm"):
            detail.llm = old["llm"]
        job.score = detail.score
        job.score_detail = detail.model_dump_json()
        job.updated_at = utcnow()
        session.add(job)
    session.commit()
    return len(targets)
