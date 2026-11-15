"""Interview preparation for one job: likely questions and your best evidence."""

from __future__ import annotations

import logging

from pydantic import BaseModel

from ..models import Job
from ..schemas import ProfileData
from . import jd_analyzer, profile_utils, skills as skills_mod
from .llm import LLM, LLMError

log = logging.getLogger(__name__)


class PrepItem(BaseModel):
    question: str
    why_they_ask: str
    your_angle: str  # how to answer from the candidate's real experience


class InterviewPrep(BaseModel):
    summary: str
    technical: list[PrepItem]
    behavioral: list[PrepItem]
    questions_to_ask: list[str]
    gaps_to_prepare: list[str]


_SYSTEM = """You are an interview coach. Prepare the candidate for an interview for this job.
Base every "your_angle" strictly on the candidate's profile: cite their real roles and achievements;
never invent experience. For skills the job wants that the profile lacks, list them in gaps_to_prepare
with a short, honest way to address them. 5-7 technical and 4-5 behavioral questions, 4 questions to ask."""


def prepare(job: Job, profile: ProfileData, llm: LLM | None) -> tuple[InterviewPrep, str]:
    """Returns (prep, method)."""
    if llm is not None:
        try:
            out = llm.structured(
                system=_SYSTEM,
                prompt=(f"<job>\n{job.title} at {job.company}\n\n{job.description[:12000]}\n</job>\n"
                        f"<profile>\n{profile.model_dump_json(exclude={'preferences'})}\n</profile>"),
                schema=InterviewPrep, max_tokens=8000)
            return out, "llm"
        except LLMError as exc:
            log.warning("Interview prep LLM failed: %s", exc)
    return _heuristic(job, profile), "heuristic"


def _evidence(profile: ProfileData, skill: str) -> str:
    matcher = skills_mod.SkillMatcher([skill])
    for e in profile.experience:
        for b in e.bullets:
            if matcher.present(b.text):
                return f"At {e.company}: {b.text}"
    return ""


def _heuristic(job: Job, profile: ProfileData) -> InterviewPrep:
    analysis = jd_analyzer.analyze(job.title, job.description, job.experience_text,
                                   extra_skills=profile_utils.profile_skills(profile))
    owned = {s.lower() for s in profile_utils.profile_skills(profile)}
    wanted = sorted(analysis.skills, key=lambda s: -analysis.skills[s])
    have = [s for s in wanted if s.lower() in owned][:6]
    lack = [s for s in wanted if s.lower() not in owned][:5]
    technical = [PrepItem(
        question=f"Walk us through a project where you used {s}. What trade-offs did you make?",
        why_they_ask=f"The role lists {s} as {'a core requirement' if analysis.skills[s] >= 1 else 'a plus'}.",
        your_angle=_evidence(profile, s) or f"Pick a concrete example with {s}, the problem, your decision and the result.",
    ) for s in have]
    recent = profile.experience[0] if profile.experience else None
    top = sorted((b.text for e in profile.experience[:2] for b in e.bullets), key=lambda t: -sum(c.isdigit() for c in t))
    behavioral = [
        PrepItem(question="Tell me about yourself.", why_they_ask="Opener; tests focus and relevance.",
                 your_angle=(f"{profile.headline or (recent.title if recent else 'Your role')} — "
                             f"{profile_utils.total_years(profile):g} years; lead with: {top[0]}" if top else
                             "Two minutes: current role, one big win, why this job.")),
        PrepItem(question="Describe your biggest achievement.", why_they_ask="Shows impact and ownership.",
                 your_angle=top[0] if top else "Use STAR: situation, task, action, measurable result."),
        PrepItem(question="Tell me about a disagreement with a teammate and how you resolved it.",
                 why_they_ask="Collaboration and maturity.", your_angle="Use STAR; end with what changed afterwards."),
        PrepItem(question=f"Why {job.company or 'this company'}, and why now?", why_they_ask="Motivation and research.",
                 your_angle="Connect their product or mission to work you've done; be specific."),
    ]
    return InterviewPrep(
        summary=(f"{job.title} at {job.company}: you match {len(have)} of the top {len(have) + len(lack)} skills "
                 f"they emphasise. Prepare stories for {', '.join(have[:3]) or 'your core skills'}."),
        technical=technical,
        behavioral=behavioral,
        questions_to_ask=["What does success look like in the first 90 days?",
                          "What are the biggest technical challenges the team faces right now?",
                          "How is work planned and prioritised on the team?",
                          "What do you like most about working here?"],
        gaps_to_prepare=[f"{s}: be honest; mention related experience and how you'd ramp up." for s in lack],
    )
