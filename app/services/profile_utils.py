"""Derived facts about a profile: dates, total experience, searchable text."""

from __future__ import annotations

import re
from datetime import date

from ..schemas import Experience, ProfileData
from . import skills as skills_mod

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_PRESENT = {"present", "current", "now", "till date", "today", "ongoing", "date"}


def parse_month(value: str, today: date | None = None) -> tuple[int, int] | None:
    """Parse "2021-03", "03/2021", "Mar 2021", "March, 2021" or "2021" to (year, month)."""
    today = today or date.today()
    v = (value or "").strip().lower()
    if not v:
        return None
    if v in _PRESENT:
        return today.year, today.month
    if m := re.fullmatch(r"(\d{4})[-/.](\d{1,2})(?:[-/.]\d{1,2})?", v):
        return int(m[1]), max(1, min(12, int(m[2])))
    if m := re.fullmatch(r"(\d{1,2})[-/.](\d{4})", v):
        return int(m[2]), max(1, min(12, int(m[1])))
    if m := re.fullmatch(r"([a-z]{3})[a-z]*\.?,?\s*'?(\d{2,4})", v):
        if m[1] in _MONTHS:
            year = int(m[2])
            if year < 100:
                year += 2000 if year < 70 else 1900
            return year, _MONTHS[m[1]]
    if m := re.fullmatch(r"(\d{4})", v):
        return int(m[1]), 1
    return None


def to_iso_month(value: str) -> str:
    """Normalize a free-form date to "YYYY-MM" ("" if unparseable or present)."""
    if (value or "").strip().lower() in _PRESENT:
        return ""
    if re.fullmatch(r"\d{4}", (value or "").strip()):
        return value.strip()
    parsed = parse_month(value)
    return f"{parsed[0]:04d}-{parsed[1]:02d}" if parsed else (value or "").strip()


def display_month(value: str) -> str:
    parsed = parse_month(value)
    if not parsed:
        return value
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if re.fullmatch(r"\d{4}", value.strip()):
        return value.strip()
    return f"{names[parsed[1] - 1]} {parsed[0]}"


def _interval(exp: Experience, today: date) -> tuple[int, int] | None:
    start = parse_month(exp.start, today)
    end = parse_month(exp.end, today) if (exp.end and not exp.current) else (today.year, today.month)
    if not start or not end:
        return None
    a, b = start[0] * 12 + start[1], end[0] * 12 + end[1]
    return (a, b + 1) if b >= a else None


def total_years(profile: ProfileData, today: date | None = None, experiences: list[Experience] | None = None) -> float:
    """Total professional experience in years, overlapping roles counted once."""
    today = today or date.today()
    intervals = sorted(filter(None, (_interval(e, today) for e in (experiences if experiences is not None else profile.experience))))
    months, cur_a, cur_b = 0, None, None
    for a, b in intervals:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                months += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        months += cur_b - cur_a
    return round(months / 12, 1)


def years_with_skill(profile: ProfileData, skill: str) -> float:
    """Years across roles whose title, bullets or tags mention ``skill``."""
    matcher = skills_mod.SkillMatcher([skill])
    roles = [
        e for e in profile.experience
        if matcher.present(" ".join([e.title, " ".join(e.skills), " ".join(b.text for b in e.bullets)]))
    ]
    return total_years(profile, experiences=roles)


def profile_text(profile: ProfileData) -> str:
    parts = [profile.headline, profile.summary, " ".join(profile.skills)]
    for e in profile.experience:
        parts += [e.title, e.company, " ".join(e.skills)] + [b.text for b in e.bullets]
    for p in profile.projects:
        parts += [p.name, p.description, " ".join(p.skills)] + [b.text for b in p.bullets]
    for c in profile.certifications:
        parts.append(c.name)
    for ed in profile.education:
        parts.append(ed.degree)
    return "\n".join(x for x in parts if x)


def profile_skills(profile: ProfileData) -> list[str]:
    """Declared skills plus taxonomy skills evidenced anywhere in the profile.

    This is the set a tailored resume is allowed to claim.
    """
    out: list[str] = []
    seen: set[str] = set()
    for s in [*profile.skills, *(s for e in profile.experience for s in e.skills),
              *(s for p in profile.projects for s in p.skills), *skills_mod.extract(profile_text(profile))]:
        c = skills_mod.canonical(s)
        if c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


def split_name(full: str) -> tuple[str, str]:
    parts = full.strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]
