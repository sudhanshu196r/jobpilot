"""Domain schemas shared by the API, the services and the LLM layer."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- profile


class Contact(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""


class Bullet(BaseModel):
    id: str = ""
    text: str


class Experience(BaseModel):
    id: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    start: str = ""  # "YYYY-MM" or "YYYY"
    end: str = ""  # empty when current
    current: bool = False
    bullets: list[Bullet] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class Education(BaseModel):
    id: str = ""
    degree: str = ""
    institution: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    grade: str = ""


class Project(BaseModel):
    id: str = ""
    name: str = ""
    url: str = ""
    description: str = ""
    bullets: list[Bullet] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class Certification(BaseModel):
    name: str = ""
    issuer: str = ""
    year: str = ""


class Preferences(BaseModel):
    target_titles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_ok: bool = True
    min_experience_fit: bool = True
    excluded_companies: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    # Facts that application forms ask for. Only ever used verbatim.
    notice_period_days: int | None = None
    current_ctc: str = ""
    expected_ctc: str = ""
    work_authorization: str = ""
    requires_sponsorship: bool | None = None
    willing_to_relocate: bool | None = None


class ProfileData(BaseModel):
    contact: Contact = Field(default_factory=Contact)
    headline: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    preferences: Preferences = Field(default_factory=Preferences)

    def normalized(self) -> "ProfileData":
        """Return a copy with stable, unique ids and cleaned lists.

        Bullet ids are what tailored resumes cite as their source, so they must
        survive edits: existing ids are kept, only missing/duplicate ones are
        regenerated.
        """
        data = self.model_copy(deep=True)
        seen: set[str] = set()

        def fresh(current: str, prefix: str) -> str:
            if current and current not in seen:
                seen.add(current)
                return current
            nid = new_id(prefix)
            seen.add(nid)
            return nid

        def clean_bullets(bullets: list[Bullet]) -> list[Bullet]:
            out = []
            for b in bullets:
                text = b.text.strip().lstrip("•-*–· ").strip()
                if text:
                    out.append(Bullet(id=fresh(b.id, "b"), text=text))
            return out

        for exp in data.experience:
            exp.id = fresh(exp.id, "x")
            exp.bullets = clean_bullets(exp.bullets)
            exp.skills = _dedupe(exp.skills)
            if exp.current:
                exp.end = ""
        for proj in data.projects:
            proj.id = fresh(proj.id, "p")
            proj.bullets = clean_bullets(proj.bullets)
            proj.skills = _dedupe(proj.skills)
        for edu in data.education:
            edu.id = fresh(edu.id, "e")
        data.skills = _dedupe(data.skills)
        data.languages = _dedupe(data.languages)
        prefs = data.preferences
        prefs.target_titles = _dedupe(prefs.target_titles)
        prefs.locations = _dedupe(prefs.locations)
        prefs.excluded_companies = _dedupe(prefs.excluded_companies)
        prefs.excluded_keywords = _dedupe(prefs.excluded_keywords)
        return data


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        s = " ".join(str(item).split())
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


# --------------------------------------------------------------------------- resume


class ResumeExperience(BaseModel):
    source_id: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    current: bool = False
    bullets: list[str] = Field(default_factory=list)


class ResumeProject(BaseModel):
    source_id: str = ""
    name: str = ""
    url: str = ""
    description: str = ""
    bullets: list[str] = Field(default_factory=list)


class ResumeDoc(BaseModel):
    """The rendered, tailored resume. Editable by the user before export."""

    contact: Contact = Field(default_factory=Contact)
    headline: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    experience: list[ResumeExperience] = Field(default_factory=list)
    projects: list[ResumeProject] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


class KeywordReport(BaseModel):
    coverage: float = 0.0  # 0..1 share of JD skills present in the resume
    matched: list[str] = Field(default_factory=list)
    missing_in_resume_but_in_profile: list[str] = Field(default_factory=list)
    missing_entirely: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- jobs


class JobIn(BaseModel):
    """Normalized job posting as produced by every source."""

    source: str
    external_id: str
    url: str = ""
    title: str
    company: str = ""
    location: str = ""
    remote: bool | None = None
    description: str = ""
    salary: str = ""
    experience_text: str = ""
    posted_at: str = ""
    apply_type: Literal["easy", "external", "unknown"] = "unknown"


class ScoreDetail(BaseModel):
    score: float = 0.0
    components: dict[str, float] = Field(default_factory=dict)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    excluded: bool = False
    jd_years: list[int | None] = Field(default_factory=lambda: [None, None])
    llm: dict | None = None
