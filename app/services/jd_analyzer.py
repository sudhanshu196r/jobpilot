"""Structured facts extracted from a job description, without a model."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from . import skills as skills_mod
from .text import tokenize

_OPTIONAL_HEAD = re.compile(
    r"\b(nice[\s\-]to[\s\-]have|good[\s\-]to[\s\-]have|preferred|bonus|plus|desirable|optional|added advantage)\b", re.I)
_REQUIRED_HEAD = re.compile(
    r"\b(requirements?|qualifications?|must[\s\-]have|what you(?:'|’)ll need|you have|skills|required|key skills|what we(?:'|’)re looking for|responsibilit)", re.I)
_YEARS = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|—|to)\s*(\d{1,2})\s*)?\+?\s*(?:years?|yrs?)", re.I)
_REMOTE = re.compile(r"\b(remote|work from home|wfh|anywhere|distributed team)\b", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)


@dataclass
class JDAnalysis:
    skills: dict[str, float] = field(default_factory=dict)  # canonical -> weight (1 required, 0.5 optional)
    min_years: int | None = None
    max_years: int | None = None
    remote: bool = False
    hybrid: bool = False
    keywords: list[str] = field(default_factory=list)

    @property
    def required(self) -> list[str]:
        return [s for s, w in self.skills.items() if w >= 1]

    @property
    def optional(self) -> list[str]:
        return [s for s, w in self.skills.items() if w < 1]


def parse_years(text: str) -> tuple[int | None, int | None]:
    """Smallest stated experience requirement, e.g. "3-5 years" -> (3, 5)."""
    best: tuple[int, int | None] | None = None
    for m in _YEARS.finditer(text or ""):
        lo = int(m[1])
        hi = int(m[2]) if m[2] else None
        if lo > 30 or (hi is not None and hi < lo):
            continue
        # Ignore "company founded 25 years ago"-style numbers in prose.
        window = text[max(0, m.start() - 40): m.end() + 40].lower()
        if not any(k in window for k in ("experience", "exp", "work", "industry", "professional", "relevant", "yrs", "years of")):
            continue
        if best is None or lo < best[0]:
            best = (lo, hi)
    return best if best else (None, None)


def analyze(title: str, description: str, experience_text: str = "", extra_skills: list[str] | None = None) -> JDAnalysis:
    text = f"{title}\n{description}"
    result = JDAnalysis()

    # Weight skills by the section they appear in.
    mode = 1.0
    for line in description.splitlines():
        stripped = line.strip()
        is_heading = len(stripped) < 80 and (stripped.endswith(":") or not stripped.startswith(("•", "-", "*")))
        if is_heading and _OPTIONAL_HEAD.search(stripped):
            mode = 0.5
        elif is_heading and _REQUIRED_HEAD.search(stripped):
            mode = 1.0
        line_weight = 0.5 if _OPTIONAL_HEAD.search(stripped) else mode
        for s in skills_mod.extract(stripped):
            result.skills[s] = max(result.skills.get(s, 0.0), line_weight)
    for s in skills_mod.extract(title):
        result.skills[s] = 1.0
    if extra_skills:
        matcher = skills_mod.SkillMatcher(extra_skills)
        for s in matcher.present(text):
            result.skills.setdefault(s, 1.0)

    lo, hi = parse_years(experience_text)
    if lo is None:
        lo, hi = parse_years(description)
    result.min_years, result.max_years = lo, hi
    result.remote = bool(_REMOTE.search(text))
    result.hybrid = bool(_HYBRID.search(text))

    counts = Counter(t for t in tokenize(description) if len(t) > 2)
    skill_words = {w.lower() for s in result.skills for w in s.split()}
    result.keywords = [w for w, _ in counts.most_common(40) if w not in skill_words][:25]
    return result
