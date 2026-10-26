"""Builds a resume tailored to one job description.

Invariant: a tailored resume never claims anything the profile does not
support. The model may only *select, reorder and rephrase*; every bullet it
writes must cite the profile bullets it came from, may not introduce numbers
that are absent from those sources, and skills are restricted to the
profile's evidenced skill set. Violations are repaired by falling back to the
original wording, and reported in ``notes``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ..schemas import KeywordReport, ProfileData, ResumeDoc, ResumeExperience, ResumeProject
from . import jd_analyzer, profile_utils, skills as skills_mod
from .jd_analyzer import JDAnalysis
from .llm import LLM, LLMError
from .text import numbers_in, tokenize

log = logging.getLogger(__name__)

MAX_SKILLS = 24
RECENT_ROLE_BULLETS = 6
OLDER_ROLE_BULLETS = 3


@dataclass
class TailorResult:
    doc: ResumeDoc
    method: str
    report: KeywordReport
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers


def _bullet_score(text: str, analysis: JDAnalysis | None, keywords: set[str]) -> float:
    if analysis is None:
        return 1.0 if re.search(r"\d", text) else 0.0
    matcher = skills_mod.SkillMatcher(list(analysis.skills))
    score = sum(analysis.skills[s] for s in matcher.present(text)) * 2
    score += 0.3 * len(keywords & set(tokenize(text)))
    if re.search(r"\d", text):
        score += 0.4  # quantified impact reads well regardless of the JD
    return score


def _ordered_skills(profile: ProfileData, analysis: JDAnalysis | None) -> list[str]:
    owned = profile_utils.profile_skills(profile)
    if analysis is None:
        return owned[:MAX_SKILLS]
    owned_lower = {s.lower(): s for s in owned}
    first = [owned_lower[s.lower()] for s in sorted(analysis.skills, key=lambda s: -analysis.skills[s])
             if s.lower() in owned_lower]
    # Beyond JD matches, only list what the user declared; incidental mentions
    # ("an accounting library") are evidence for matching but not headline skills.
    declared = [skills_mod.canonical(s) for s in profile.skills] or owned
    rest = [s for s in declared if s not in first]
    return (first + rest)[:MAX_SKILLS]


def _summary(profile: ProfileData, analysis: JDAnalysis | None, skills: list[str]) -> str:
    years = profile_utils.total_years(profile)
    role = profile.headline or (profile.experience[0].title if profile.experience else "")
    focus = skills[:4]
    lead = ""
    if role and years >= 1:
        lead = f"{role} with {int(years)}+ years of experience"
    elif role:
        lead = role
    if focus:
        lead = f"{lead} specialising in {', '.join(focus[:-1])}{' and ' if len(focus) > 1 else ''}{focus[-1]}" if lead \
            else f"Professional skilled in {', '.join(focus)}"
    base = profile.summary.strip()
    if base:
        # Keep the user's own words; lead with the JD-relevant framing.
        first_sentences = " ".join(re.split(r"(?<=[.!?])\s+", base)[:2])
        return f"{lead}. {first_sentences}".strip() if lead else first_sentences
    return f"{lead}." if lead else ""


def keyword_report(doc: ResumeDoc, profile: ProfileData, analysis: JDAnalysis | None) -> KeywordReport:
    if analysis is None or not analysis.skills:
        return KeywordReport(coverage=1.0)
    text = resume_text(doc)
    matcher = skills_mod.SkillMatcher(list(analysis.skills))
    present = set(matcher.present(text))
    owned = {s.lower() for s in profile_utils.profile_skills(profile)}
    matched = [s for s in analysis.skills if s in present]
    missing = [s for s in analysis.skills if s not in present]
    total = sum(analysis.skills.values())
    return KeywordReport(
        coverage=round(sum(analysis.skills[s] for s in matched) / total, 3) if total else 1.0,
        matched=matched,
        missing_in_resume_but_in_profile=[s for s in missing if s.lower() in owned],
        missing_entirely=[s for s in missing if s.lower() not in owned],
    )


def resume_text(doc: ResumeDoc) -> str:
    parts = [doc.headline, doc.summary, ", ".join(doc.skills)]
    for e in doc.experience:
        parts += [e.title, e.company, *e.bullets]
    for p in doc.projects:
        parts += [p.name, p.description, *p.bullets]
    parts += [c.name for c in doc.certifications]
    return "\n".join(parts)


# --------------------------------------------------------------------------- heuristic


def tailor_heuristic(profile: ProfileData, analysis: JDAnalysis | None) -> ResumeDoc:
    keywords = set(analysis.keywords) if analysis else set()
    skills = _ordered_skills(profile, analysis)
    experiences = []
    for i, exp in enumerate(profile.experience):
        limit = RECENT_ROLE_BULLETS if i < 2 else OLDER_ROLE_BULLETS
        ranked = sorted(enumerate(exp.bullets), key=lambda t: (-_bullet_score(t[1].text, analysis, keywords), t[0]))
        chosen = [b.text for _, b in ranked[:limit]]
        experiences.append(ResumeExperience(
            source_id=exp.id, title=exp.title, company=exp.company, location=exp.location,
            start=exp.start, end=exp.end, current=exp.current, bullets=chosen))
    projects = []
    if profile.projects:
        scored = sorted(profile.projects, key=lambda p: -sum(
            _bullet_score(t, analysis, keywords) for t in [p.name, p.description, *[b.text for b in p.bullets]]))
        for p in scored[:3]:
            projects.append(ResumeProject(source_id=p.id, name=p.name, url=p.url, description=p.description,
                                          bullets=[b.text for b in p.bullets[:3]]))
    return ResumeDoc(
        contact=profile.contact,
        headline=profile.headline or (profile.experience[0].title if profile.experience else ""),
        summary=_summary(profile, analysis, skills),
        skills=skills,
        experience=experiences,
        projects=projects,
        education=profile.education,
        certifications=profile.certifications,
        languages=profile.languages,
    )


# --------------------------------------------------------------------------- LLM


class _LLMBullet(BaseModel):
    source_ids: list[str] = Field(description="ids of the profile bullets this line is derived from")
    text: str


class _LLMSection(BaseModel):
    source_id: str
    bullets: list[_LLMBullet]


class _LLMTailored(BaseModel):
    headline: str
    summary: str
    skills: list[str]
    experience: list[_LLMSection]
    projects: list[_LLMSection]


_TAILOR_SYSTEM = """You are an expert resume writer tailoring a candidate's resume to one job.

Hard rules (a resume that breaks them is rejected):
1. Use ONLY facts in the candidate profile. Never invent employers, titles, dates, skills, tools,
   metrics, team sizes or outcomes. If the job wants something the candidate lacks, leave it out.
2. Every bullet must list in `source_ids` the profile bullet id(s) it was written from, and may only
   restate what those bullets say. Do not introduce any number that is not in the cited bullets.
3. `skills` must be chosen from the candidate's skill list (exact names), most relevant first,
   at most 24.
4. `experience` must contain one entry per profile role (by `source_id`), in the original order.
   Pick the 3-6 most relevant bullets for recent roles and 1-3 for older ones.
5. `projects`: up to 3 most relevant, by `source_id`; empty list if none are relevant.

Style: strong action verbs, concise (one line, ~25 words max), use the job description's terminology
where it truthfully describes the candidate's work, keep quantified results. Summary: 2-3 sentences,
no first person, no clichés. Headline: the candidate's real role title, optionally with 2-3 core skills."""


def _profile_for_prompt(profile: ProfileData, skills: list[str]) -> str:
    lines = [f"Name: {profile.contact.name}", f"Headline: {profile.headline}",
             f"Total experience: {profile_utils.total_years(profile):g} years",
             f"Summary: {profile.summary}", f"Skill list: {', '.join(skills)}", "", "Roles:"]
    for e in profile.experience:
        lines.append(f"- role source_id={e.id}: {e.title} at {e.company} ({e.start} to {e.end or 'present'})")
        for b in e.bullets:
            lines.append(f"    [{b.id}] {b.text}")
    if profile.projects:
        lines.append("\nProjects:")
        for p in profile.projects:
            lines.append(f"- project source_id={p.id}: {p.name}. {p.description}")
            for b in p.bullets:
                lines.append(f"    [{b.id}] {b.text}")
    return "\n".join(lines)


def tailor_with_llm(llm: LLM, profile: ProfileData, job_title: str, company: str, description: str,
                    analysis: JDAnalysis) -> tuple[ResumeDoc, list[str]]:
    allowed_skills = profile_utils.profile_skills(profile)
    prompt = (
        f"<job>\nTitle: {job_title}\nCompany: {company}\n\n{description}\n</job>\n\n"
        f"<candidate_profile>\n{_profile_for_prompt(profile, allowed_skills)}\n</candidate_profile>"
    )
    out = llm.structured(system=_TAILOR_SYSTEM, prompt=prompt, schema=_LLMTailored)
    return _validate(out, profile, analysis, allowed_skills)


def _validate(out: _LLMTailored, profile: ProfileData, analysis: JDAnalysis,
              allowed_skills: list[str]) -> tuple[ResumeDoc, list[str]]:
    notes: list[str] = []
    fallback = tailor_heuristic(profile, analysis)
    allowed_lower = {s.lower(): s for s in allowed_skills}

    skills = []
    for s in out.skills:
        c = skills_mod.canonical(s)
        if c.lower() in allowed_lower:
            if allowed_lower[c.lower()] not in skills:
                skills.append(allowed_lower[c.lower()])
        else:
            notes.append(f"Removed skill not evidenced in your profile: {s}")
    if not skills:
        skills = fallback.skills

    def rebuild(sections, sources, kind):
        by_id = {x.id: x for x in sources}
        result = {}
        for sec in sections:
            src = by_id.get(sec.source_id)
            if src is None:
                notes.append(f"Ignored unknown {kind} reference '{sec.source_id}'")
                continue
            bullets_by_id = {b.id: b.text for b in src.bullets}
            lines = []
            for b in sec.bullets:
                cited = [bullets_by_id[i] for i in b.source_ids if i in bullets_by_id]
                if not cited:
                    notes.append(f"Dropped an uncited bullet in {kind} '{getattr(src, 'title', '') or getattr(src, 'name', '')}'")
                    continue
                if not numbers_in(b.text) <= numbers_in(" ".join(cited)):
                    notes.append("Restored original wording where a rewrite introduced new numbers")
                    lines.append(cited[0])
                    continue
                lines.append(b.text.strip())
            result[src.id] = lines
        return result

    exp_bullets = rebuild(out.experience, profile.experience, "role")
    experience = []
    for i, e in enumerate(profile.experience):
        bullets = exp_bullets.get(e.id) or fallback.experience[i].bullets
        experience.append(ResumeExperience(source_id=e.id, title=e.title, company=e.company, location=e.location,
                                           start=e.start, end=e.end, current=e.current, bullets=bullets))

    proj_bullets = rebuild(out.projects, profile.projects, "project")
    projects = []
    for sec in out.projects:
        p = next((x for x in profile.projects if x.id == sec.source_id), None)
        if p and p.id in proj_bullets:
            projects.append(ResumeProject(source_id=p.id, name=p.name, url=p.url, description=p.description,
                                          bullets=proj_bullets[p.id] or [b.text for b in p.bullets[:3]]))

    # Headline and summary may not claim skills or numbers the profile lacks.
    headline, summary = out.headline.strip(), out.summary.strip()
    profile_numbers = numbers_in(profile_utils.profile_text(profile)) | numbers_in(
        f"{profile_utils.total_years(profile):g} {int(profile_utils.total_years(profile))}")
    for label, value in (("headline", headline), ("summary", summary)):
        bad_skills = [s for s in skills_mod.extract(value) if s.lower() not in allowed_lower]
        bad_numbers = numbers_in(value) - profile_numbers
        if bad_skills or bad_numbers:
            notes.append(f"Replaced AI {label}: it mentioned unsupported "
                         f"{', '.join(bad_skills + sorted(bad_numbers))}")
            if label == "headline":
                headline = fallback.headline
            else:
                summary = fallback.summary

    doc = ResumeDoc(contact=profile.contact, headline=headline or fallback.headline, summary=summary or fallback.summary,
                    skills=skills[:MAX_SKILLS], experience=experience, projects=projects,
                    education=profile.education, certifications=profile.certifications, languages=profile.languages)
    return doc, sorted(set(notes), key=notes.index)


# --------------------------------------------------------------------------- entry point


def tailor(profile: ProfileData, *, job_title: str = "", company: str = "", description: str = "",
           experience_text: str = "", llm: LLM | None = None) -> TailorResult:
    """Tailor to a job, or produce a general resume when no description is given."""
    analysis = None
    if description.strip():
        analysis = jd_analyzer.analyze(job_title, description, experience_text,
                                       extra_skills=profile_utils.profile_skills(profile))
    notes: list[str] = []
    if llm is not None and analysis is not None:
        try:
            doc, notes = tailor_with_llm(llm, profile, job_title, company, description, analysis)
            return TailorResult(doc=doc, method="llm", report=keyword_report(doc, profile, analysis), notes=notes)
        except LLMError as exc:
            notes.append(f"AI tailoring unavailable ({exc}); used the built-in tailoring.")
            log.warning("LLM tailoring failed: %s", exc)
    doc = tailor_heuristic(profile, analysis)
    return TailorResult(doc=doc, method="heuristic" if analysis else "base",
                        report=keyword_report(doc, profile, analysis), notes=notes)


# --------------------------------------------------------------------------- cover letter


class _Letter(BaseModel):
    body: str


def cover_letter(profile: ProfileData, doc: ResumeDoc, job_title: str, company: str, description: str,
                 llm: LLM | None) -> str:
    name = profile.contact.name or "Candidate"
    if llm is not None and description.strip():
        try:
            out = llm.structured(
                system=("Write a concise, specific cover letter (180-250 words, 3 short paragraphs) for this job. "
                        "Use only facts from the resume; never invent experience, numbers or skills. "
                        "No placeholders, no address block, start with 'Dear Hiring Team,' and end with "
                        f"'Sincerely,\\n{name}'."),
                prompt=f"<job>\n{job_title} at {company}\n\n{description}\n</job>\n<resume>\n{resume_text(doc)}\n</resume>",
                schema=_Letter,
                max_tokens=4000,
            )
            if numbers_in(out.body) <= numbers_in(resume_text(doc)) | numbers_in(description):
                return out.body.strip()
        except LLMError as exc:
            log.warning("Cover letter LLM failed: %s", exc)
    years = int(profile_utils.total_years(profile))
    role = doc.headline or (doc.experience[0].title if doc.experience else "professional")
    top = doc.skills[:3]
    strengths = f", with deep expertise in {', '.join(top[:-1])} and {top[-1]}" if len(top) > 1 else (
        f", with deep expertise in {top[0]}" if top else "")
    opening = f"I am excited to apply for the {job_title or 'open'} role{f' at {company}' if company else ''}."
    if years >= 1:
        opening += f" I bring {years}+ years of experience as a {role}{strengths}."
    elif top:
        opening += f" My background as a {role}{strengths} makes me a strong fit."
    paras = ["Dear Hiring Team,", opening]
    recent = doc.experience[0] if doc.experience else None
    if recent and recent.bullets:
        middle = f"Most recently, as {recent.title} at {recent.company}, I {_as_clause(recent.bullets[0])}."
        if len(recent.bullets) > 1:
            middle += f" I also {_as_clause(recent.bullets[1])}."
        paras.append(middle)
    paras.append("I would welcome the chance to discuss how I can contribute to your team. "
                 "Thank you for your time and consideration.")
    paras.append(f"Sincerely,\n{name}")
    return "\n\n".join(paras)


def _as_clause(bullet: str) -> str:
    """'Led migration of X.' -> 'led migration of X' for use mid-sentence."""
    text = bullet.strip().rstrip(".")
    if len(text) > 1 and text[0].isupper() and not text[1].isupper():
        text = text[0].lower() + text[1:]
    return text
