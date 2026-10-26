"""Turns an uploaded resume (PDF / DOCX / text) into a structured profile."""

from __future__ import annotations

import io
import logging
import re

from pydantic import BaseModel, Field

from ..schemas import Bullet, Certification, Contact, Education, Experience, ProfileData, Project
from . import profile_utils, skills as skills_mod
from .llm import LLM, LLMError
from .text import clean_whitespace

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- file -> text


def extract_text(filename: str, content: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf") or content[:5] == b"%PDF-":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages = [(p.extract_text() or "") for p in reader.pages]
        return clean_whitespace("\n".join(pages))
    if name.endswith(".docx"):
        import docx

        document = docx.Document(io.BytesIO(content))
        lines = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                lines.append(" | ".join(c.text for c in row.cells))
        return clean_whitespace("\n".join(lines))
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return clean_whitespace(content.decode(enc))
        except UnicodeDecodeError:
            continue
    raise ValueError("Unsupported file encoding")


# --------------------------------------------------------------------------- LLM parse


class _LLMBulletless(BaseModel):
    title: str
    company: str
    location: str
    start: str = Field(description="YYYY-MM, or YYYY if month unknown")
    end: str = Field(description="YYYY-MM, YYYY, or empty string if this is the current role")
    current: bool
    bullets: list[str]
    skills: list[str]


class _LLMProject(BaseModel):
    name: str
    url: str
    description: str
    bullets: list[str]
    skills: list[str]


class _LLMProfile(BaseModel):
    contact: Contact
    headline: str
    summary: str
    skills: list[str]
    experience: list[_LLMBulletless]
    education: list[Education]
    projects: list[_LLMProject]
    certifications: list[Certification]
    languages: list[str]


_PARSE_SYSTEM = """You convert resumes into structured data for a job-search tool.
Copy facts exactly as written; never infer, embellish or invent anything (no new skills, numbers,
titles or dates). Keep every achievement bullet, verbatim apart from removing bullet glyphs.
Dates: use YYYY-MM when the month is known, otherwise YYYY. Leave a field as an empty string when
the resume does not state it. `languages` means spoken languages. `skills` is the explicit skills
list plus tools clearly named in the resume."""


def parse_with_llm(llm: LLM, text: str) -> ProfileData:
    out = llm.structured(system=_PARSE_SYSTEM, prompt=f"<resume>\n{text}\n</resume>", schema=_LLMProfile)
    data = ProfileData(
        contact=out.contact,
        headline=out.headline,
        summary=out.summary,
        skills=out.skills,
        experience=[
            Experience(**e.model_dump(exclude={"bullets"}), bullets=[Bullet(text=b) for b in e.bullets])
            for e in out.experience
        ],
        education=out.education,
        projects=[
            Project(**p.model_dump(exclude={"bullets"}), bullets=[Bullet(text=b) for b in p.bullets])
            for p in out.projects
        ],
        certifications=out.certifications,
        languages=out.languages,
    )
    return data.normalized()


# --------------------------------------------------------------------------- heuristic parse

_SECTION_ALIASES = {
    "summary": ["summary", "profile", "professional summary", "about me", "objective", "career objective", "profile summary"],
    "skills": ["skills", "technical skills", "key skills", "core competencies", "technologies", "skills & tools", "tech stack"],
    "experience": ["experience", "work experience", "professional experience", "employment history", "work history", "employment"],
    "education": ["education", "academic background", "academics", "qualifications", "educational qualifications"],
    "projects": ["projects", "personal projects", "key projects", "academic projects"],
    "certifications": ["certifications", "certificates", "licenses & certifications", "courses"],
    "languages": ["languages", "languages known"],
}
_HEADING_LOOKUP = {a: k for k, v in _SECTION_ALIASES.items() for a in v}

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = rf"(?:{_MONTH}\s*,?\s*'?\d{{2,4}}|\d{{1,2}}[/.\-]\d{{4}}|\d{{4}}[/.\-]\d{{1,2}}|\d{{4}})"
_RANGE_RE = re.compile(rf"({_DATE})\s*(?:-|–|—|to|until)\s*({_DATE}|present|current|now|till date|today)", re.I)
_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s\-]?)?(?:\(?\d{2,5}\)?[\s\-]?)?\d{3,5}[\s\-]?\d{4,5}")
_URL_RE = re.compile(r"(?:https?://)?(?:www\.)?[\w\-]+\.[a-z]{2,}(?:/[\w\-./?=&%#]*)?", re.I)
_TRIM = " ,-–—|•·"
_BULLET_RE = re.compile(r"^\s*(?:[•●▪◦\-*–·]|\d+[.)])\s+")


def _heading(line: str) -> str | None:
    key = re.sub(r"[^a-z& ]", "", line.lower()).strip()
    if len(key) > 40:
        return None
    return _HEADING_LOOKUP.get(key)


def parse_heuristic(text: str) -> ProfileData:
    lines = [ln.strip() for ln in text.splitlines()]
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for ln in lines:
        if not ln:
            sections.setdefault(current, []).append("")
            continue
        sec = _heading(ln)
        if sec:
            current = sec
            sections.setdefault(sec, [])
        else:
            sections.setdefault(current, []).append(ln)

    profile = ProfileData()
    header = [ln for ln in sections.get("header", []) if ln]
    profile.contact = _contact(header, text)
    if len(header) > 1:
        cand = header[1]
        if not _EMAIL_RE.search(cand) and not _PHONE_RE.search(cand) and len(cand) < 90:
            profile.headline = cand
    profile.summary = " ".join(ln for ln in sections.get("summary", []) if ln)

    skill_lines = [ln for ln in sections.get("skills", []) if ln]
    skills: list[str] = []
    for ln in skill_lines:
        ln = re.sub(r"^[^:]{1,40}:\s*", "", _BULLET_RE.sub("", ln))
        for item in re.split(r"[,|;•·]| / | {2,}", ln):
            item = item.strip(" .")
            # Single letters only when they are a known language name ("R", "C").
            if (1 < len(item) < 40) or item in {"R", "C"}:
                skills.append(item)
    if not skills:  # no skills section: fall back to tools named anywhere
        skills = skills_mod.extract(text)
    profile.skills = skills

    profile.experience = _experience(sections.get("experience", []))
    profile.education = _education(sections.get("education", []))
    profile.projects = _projects(sections.get("projects", []))
    profile.certifications = [
        _certification(_BULLET_RE.sub("", ln)) for ln in sections.get("certifications", []) if ln
    ]
    langs = " ".join(sections.get("languages", []))
    profile.languages = [x.strip() for x in re.split(r"[,|;•]", langs) if x.strip()]
    if not profile.headline and profile.experience:
        profile.headline = profile.experience[0].title
    return profile.normalized()


def _certification(line: str) -> Certification:
    """"Name, Issuer, 2021" / "Name - Issuer (2021)" -> structured."""
    cert = Certification(name=line.strip())
    if m := re.search(r"[(,\s]*\b((?:19|20)\d{2})\)?\s*$", line):
        cert.year = m[1]
        line = line[: m.start()]
    parts = [p.strip(_TRIM) for p in re.split(r",\s+| \| ", line) if p.strip(_TRIM)]
    if parts:
        cert.name = parts[0]
        cert.issuer = ", ".join(parts[1:2])
    return cert


def _contact(header: list[str], text: str) -> Contact:
    c = Contact()
    head_text = "\n".join(header[:8]) or text[:600]
    if m := _EMAIL_RE.search(text):
        c.email = m[0]
    for m in _PHONE_RE.finditer(head_text):
        digits = re.sub(r"\D", "", m[0])
        if 10 <= len(digits) <= 13:
            c.phone = m[0].strip()
            break
    for m in _URL_RE.finditer(text):
        url = m[0]
        low = url.lower()
        if "@" in text[max(0, m.start() - 1):m.start()]:
            continue
        if "linkedin.com" in low and not c.linkedin:
            c.linkedin = url
        elif "github.com" in low and not c.github:
            c.github = url
    if header:
        first = header[0]
        if not _EMAIL_RE.search(first) and len(first.split()) <= 5:
            c.name = first.title() if first.isupper() else first
    for ln in header[1:6]:
        parts = [p.strip() for p in re.split(r"[|•·]", ln)]
        for p in parts:
            if (re.fullmatch(r"[A-Za-z .]+,\s*[A-Za-z .]+", p) and not _EMAIL_RE.search(p)) and not c.location:
                c.location = p
    return c


def _blocks(lines: list[str]) -> list[list[str]]:
    """Split a section into entries; a new entry starts at a non-bullet line
    containing a date range, or after a blank line followed by a non-bullet."""
    blocks: list[list[str]] = []
    cur: list[str] = []
    prev_blank = False
    for ln in lines:
        if not ln:
            prev_blank = True
            continue
        is_bullet = bool(_BULLET_RE.match(ln))
        has_range = bool(_RANGE_RE.search(ln))
        cur_has_range = any(_RANGE_RE.search(x) for x in cur)
        cur_has_bullets = any(_BULLET_RE.match(x) for x in cur)
        if cur and not is_bullet and ((has_range and cur_has_range) or (prev_blank and cur_has_bullets)
                                       or (cur_has_bullets and not has_range and len(ln) < 90)):
            blocks.append(cur)
            cur = []
        cur.append(ln)
        prev_blank = False
    if cur:
        blocks.append(cur)
    return blocks


_SMALL_WORDS = {"of", "and", "in", "at", "for", "the", "to", "on", "with", "&", "a", "an"}


def _header_like(line: str) -> bool:
    """Short, mostly Title Case and not a sentence: "Data Analyst", "Flipkart, Bengaluru"."""
    if not line or _BULLET_RE.match(line) or len(line) > 90 or line.rstrip().endswith("."):
        return False
    words = [w for w in re.findall(r"[A-Za-z][\w&.'’-]*", line) if w.lower() not in _SMALL_WORDS]
    if not words:
        return False
    return sum(w[0].isupper() for w in words) / len(words) >= 0.6


def _entries(lines: list[str]) -> list[tuple[list[str], list[str]]] | None:
    """Split a section into (header lines, body lines), anchored on date ranges.

    Works whether the dates sit on the title line, below it, or above it, and
    whether or not bullets kept their glyphs (PDF/DOCX list formatting usually
    loses them).
    """
    anchors = [i for i, ln in enumerate(lines) if ln and not _BULLET_RE.match(ln) and _RANGE_RE.search(ln)]
    if not anchors:
        return None
    starts, floor = [], 0
    for a in anchors:
        s = a
        while s - 1 >= floor and a - (s - 1) <= 3 and _header_like(lines[s - 1]):
            s -= 1
        starts.append(s)
        floor = a + 1
    entries = []
    for k, (s, a) in enumerate(zip(starts, anchors)):
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        header = [ln for ln in lines[s:a + 1] if ln]
        body = lines[a + 1:end]
        # Date-only anchor with the title below it: "Jan 2020 - Present" / "Engineer, Acme".
        if not _RANGE_RE.sub("", " ".join(header)).strip(_TRIM):
            while body and (not body[0] or (_header_like(body[0]) and len(header) < 3)):
                if body[0]:
                    header.append(body[0])
                body = body[1:]
        entries.append((header, body))
    return entries


def _join_bullets(body: list[str]) -> list[str]:
    """Body lines -> bullets, re-joining lines that PDF extraction wrapped."""
    has_glyphs = any(_BULLET_RE.match(ln) for ln in body if ln)
    out: list[str] = []
    for ln in body:
        if not ln:
            continue
        if _BULLET_RE.match(ln):
            out.append(_BULLET_RE.sub("", ln).strip())
            continue
        continuation = out and (has_glyphs or ln[0].islower() or ln[0] in "(&"
                                or out[-1].endswith((",", "-", "–", "&", "/", " and", " or", " to", " of", " the")))
        if continuation:
            out[-1] = out[-1] + ln if out[-1].endswith("-") and not out[-1].endswith(" -") else f"{out[-1]} {ln}"
        else:
            out.append(ln)
    return out


def _apply_range(head: str, target) -> str:
    if m := _RANGE_RE.search(head):
        target.start = profile_utils.to_iso_month(m[1])
        end = m[2].strip().lower()
        current = end in {"present", "current", "now", "till date", "today"}
        if hasattr(target, "current"):
            target.current = current
        target.end = "" if current else profile_utils.to_iso_month(m[2])
        head = head[: m.start()] + head[m.end():]
    return head


def _experience(lines: list[str]) -> list[Experience]:
    entries = _entries(lines)
    if entries is None:  # no dates at all: fall back to blank-line/bullet blocks
        entries = []
        for block in _blocks(lines):
            head = [ln for ln in block if not _BULLET_RE.match(ln)][:2]
            entries.append((head, [ln for ln in block if ln not in head]))
    out = []
    for header, body in entries:
        exp = Experience()
        head = _apply_range(" | ".join(header), exp)
        parts = [p.strip(_TRIM) for p in re.split(r"\s*(?:\||,| at | @ | - | – | — |•)\s*", head) if p.strip(_TRIM)]
        if parts:
            exp.title = parts[0]
        if len(parts) > 1:
            exp.company = parts[1]
        if len(parts) > 2:
            exp.location = ", ".join(parts[2:4])
        exp.bullets = [Bullet(text=b) for b in _join_bullets(body)]
        if exp.title or exp.bullets:
            out.append(exp)
    return out


_GRADE_RE = re.compile(
    r"(?:cgpa|gpa|percentage|grade|score)[:\s]*([\d.]+\s*(?:/\s*\d+)?%?)|\b(\d{1,2}(?:\.\d+)?\s*/\s*(?:10|4|5))\b|(\d{2}(?:\.\d+)?\s*%)",
    re.I)


def _education(lines: list[str]) -> list[Education]:
    entries = _entries(lines)
    blocks = [h + [b for b in body if b] for h, body in entries] if entries else _blocks(lines)
    out = []
    for block in blocks:
        edu = Education()
        joined = " | ".join(_BULLET_RE.sub("", x) for x in block)
        if _RANGE_RE.search(joined):
            joined = _apply_range(joined, edu)
        elif m := re.search(r"\b(19|20)\d{2}\b", joined):
            edu.end = m[0]
            joined = joined.replace(m[0], "")
        if m := _GRADE_RE.search(joined):
            edu.grade = next(g for g in m.groups() if g).strip()
            joined = joined.replace(m[0], "")
        parts = [p.strip(_TRIM) for p in re.split(r"\s*\|\s*|,\s+| — | – | - | at ", joined) if p.strip(_TRIM + "()")]
        if parts:
            edu.degree = parts[0]
        if len(parts) > 1:
            edu.institution = parts[1]
        if len(parts) > 2 and not edu.location:
            edu.location = parts[2]
        if edu.degree:
            out.append(edu)
    return out


def _projects(lines: list[str]) -> list[Project]:
    out = []
    for block in _blocks(lines):
        proj = Project()
        for ln in block:
            if _BULLET_RE.match(ln):
                proj.bullets.append(Bullet(text=_BULLET_RE.sub("", ln)))
            elif not proj.name:
                name = ln
                if m := _URL_RE.search(ln):
                    proj.url = m[0]
                    name = ln.replace(m[0], "").strip(" |-–")
                proj.name = name
            else:
                proj.description = (proj.description + " " + ln).strip()
        proj.skills = skills_mod.extract(" ".join([proj.name, proj.description, *[b.text for b in proj.bullets]]))
        if proj.name:
            out.append(proj)
    return out


# --------------------------------------------------------------------------- entry point


def parse_resume_text(text: str, llm: LLM | None) -> tuple[ProfileData, str, list[str]]:
    """Returns (profile, method, warnings)."""
    warnings: list[str] = []
    if not text.strip():
        raise ValueError("No text could be extracted. If this is a scanned PDF, paste the text instead.")
    if llm is not None:
        try:
            return parse_with_llm(llm, text), "llm", warnings
        except LLMError as exc:
            warnings.append(f"AI parsing failed ({exc}); used the built-in parser instead.")
            log.warning("LLM parse failed: %s", exc)
    else:
        warnings.append("Parsed without AI (no API key). Review every section carefully.")
    return parse_heuristic(text), "heuristic", warnings


# --------------------------------------------------------------------------- merge & report


def _k(*values: str) -> str:
    return "|".join(re.sub(r"[^a-z0-9]+", " ", v.lower()).strip() for v in values)


def _union(current: list[str], incoming: list[str]) -> list[str]:
    out, seen = list(current), {x.lower() for x in current}
    for x in incoming:
        if x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def _fill_empty(target, source) -> None:
    for name, value in source:
        if isinstance(value, str) and value and not getattr(target, name):
            setattr(target, name, value)


def merge_profiles(current: ProfileData, incoming: ProfileData) -> ProfileData:
    """Add what a newly imported resume knows to the existing profile.

    Nothing already in the profile is overwritten: empty fields get filled,
    new roles/education/projects are appended, and matching roles gain any
    achievements they did not have yet.
    """
    out = current.model_copy(deep=True)
    _fill_empty(out.contact, incoming.contact)
    out.headline = out.headline or incoming.headline
    out.summary = out.summary or incoming.summary
    out.skills = _union(out.skills, incoming.skills)
    out.languages = _union(out.languages, incoming.languages)

    for exp in incoming.experience:
        match = next((e for e in out.experience
                      if _k(e.company) == _k(exp.company) and (_k(e.title) == _k(exp.title) or (e.start and e.start == exp.start))), None)
        if match is None:
            out.experience.append(exp)
            continue
        _fill_empty(match, exp)
        match.current = match.current or exp.current
        have = {_k(b.text) for b in match.bullets}
        match.bullets += [b for b in exp.bullets if _k(b.text) not in have]
        match.skills = _union(match.skills, exp.skills)
    # Most recent first: current roles, then by start date.
    out.experience.sort(key=lambda e: (e.current, profile_utils.to_iso_month(e.start)), reverse=True)

    for edu in incoming.education:
        if not any(_k(e.institution, e.degree) == _k(edu.institution, edu.degree) for e in out.education):
            out.education.append(edu)
    for proj in incoming.projects:
        match = next((p for p in out.projects if _k(p.name) == _k(proj.name)), None)
        if match is None:
            out.projects.append(proj)
        else:
            _fill_empty(match, proj)
            have = {_k(b.text) for b in match.bullets}
            match.bullets += [b for b in proj.bullets if _k(b.text) not in have]
    for cert in incoming.certifications:
        if not any(_k(c.name) == _k(cert.name) for c in out.certifications):
            out.certifications.append(cert)
    return out.normalized()


def suggest_preferences(profile: ProfileData) -> ProfileData:
    """Pre-fill job preferences the resume implies, only where still empty."""
    prefs = profile.preferences
    if not prefs.target_titles:
        titles = [profile.headline] + [e.title for e in profile.experience[:2]]
        prefs.target_titles = [t for t in dict.fromkeys(t.strip() for t in titles if t and len(t) < 60)][:3]
    if not prefs.locations and profile.contact.location:
        prefs.locations = [profile.contact.location.split(",")[0].strip()]
    return profile


def import_report(profile: ProfileData) -> dict:
    """What was filled, and what still needs the user's attention."""
    c = profile.contact
    missing = [label for label, value in (
        ("Full name", c.name), ("Email", c.email), ("Phone", c.phone), ("Location", c.location),
        ("LinkedIn", c.linkedin), ("Headline", profile.headline), ("Summary", profile.summary),
        ("Skills", profile.skills), ("Experience", profile.experience), ("Education", profile.education),
    ) if not value]
    for e in profile.experience:
        name = e.title or e.company or "a role"
        if not e.start:
            missing.append(f"Start date for {name}")
        if not e.company:
            missing.append(f"Company for {name}")
        if not e.bullets:
            missing.append(f"Achievements for {name}")
    return {
        "counts": {
            "experience": len(profile.experience),
            "achievements": sum(len(e.bullets) for e in profile.experience),
            "skills": len(profile.skills),
            "education": len(profile.education),
            "projects": len(profile.projects),
            "certifications": len(profile.certifications),
            "languages": len(profile.languages),
        },
        "total_years": profile_utils.total_years(profile),
        "missing": missing,
    }
