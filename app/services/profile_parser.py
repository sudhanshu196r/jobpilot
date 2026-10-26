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
        skills += [s.strip(" .") for s in re.split(r"[,|;•·/]| {2,}", ln) if 1 < len(s.strip()) < 40]
    if not skills:  # no skills section: fall back to tools named anywhere
        skills = skills_mod.extract(text)
    profile.skills = skills

    profile.experience = _experience(sections.get("experience", []))
    profile.education = _education(sections.get("education", []))
    profile.projects = _projects(sections.get("projects", []))
    profile.certifications = [
        Certification(name=_BULLET_RE.sub("", ln)) for ln in sections.get("certifications", []) if ln
    ]
    langs = " ".join(sections.get("languages", []))
    profile.languages = [x.strip() for x in re.split(r"[,|;•]", langs) if x.strip()]
    if not profile.headline and profile.experience:
        profile.headline = profile.experience[0].title
    return profile.normalized()


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


def _experience(lines: list[str]) -> list[Experience]:
    out = []
    for block in _blocks(lines):
        exp = Experience()
        header_lines = []
        for ln in block:
            if _BULLET_RE.match(ln):
                exp.bullets.append(Bullet(text=_BULLET_RE.sub("", ln)))
            elif header_lines and len(ln) > 90 and exp.bullets is not None and not _RANGE_RE.search(ln):
                exp.bullets.append(Bullet(text=ln))
            else:
                header_lines.append(ln)
        head = " | ".join(header_lines)
        if m := _RANGE_RE.search(head):
            exp.start = profile_utils.to_iso_month(m[1])
            end = m[2].strip().lower()
            exp.current = end in {"present", "current", "now", "till date", "today"}
            exp.end = "" if exp.current else profile_utils.to_iso_month(m[2])
            head = (head[: m.start()] + head[m.end():]).strip(" |,-–")
        parts = [p.strip(" ,-–|") for p in re.split(r"\s*(?:\||,| at | @ | - | – )\s*", head) if p.strip(" ,-–|")]
        if parts:
            exp.title = parts[0]
        if len(parts) > 1:
            exp.company = parts[1]
        if len(parts) > 2:
            exp.location = ", ".join(parts[2:4])
        if exp.title or exp.bullets:
            out.append(exp)
    return out


def _education(lines: list[str]) -> list[Education]:
    out = []
    for block in _blocks(lines):
        edu = Education()
        joined = " | ".join(_BULLET_RE.sub("", x) for x in block)
        if m := _RANGE_RE.search(joined):
            edu.start, edu.end = profile_utils.to_iso_month(m[1]), profile_utils.to_iso_month(m[2])
            joined = (joined[: m.start()] + joined[m.end():])
        elif m := re.search(r"\b(19|20)\d{2}\b", joined):
            edu.end = m[0]
            joined = joined.replace(m[0], "")
        if m := re.search(r"(?:cgpa|gpa|percentage|grade)[:\s]*([\d.]+\s*(?:/\s*\d+)?%?)|(\d{2}(?:\.\d+)?\s*%)", joined, re.I):
            edu.grade = (m[1] or m[2]).strip()
            joined = joined.replace(m[0], "")
        parts = [p.strip(" ,-–|") for p in re.split(r"\s*\|\s*|,\s+", joined) if p.strip(" ,-–|()")]
        if parts:
            edu.degree = parts[0]
        if len(parts) > 1:
            edu.institution = parts[1]
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
