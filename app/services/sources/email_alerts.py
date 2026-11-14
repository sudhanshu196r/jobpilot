"""Jobs from the job-alert emails LinkedIn, Naukri, Indeed and others send you.

These platforms offer no public API and forbid scraping, but they already
email you new matching jobs. JobPilot reads those emails (read-only, over
IMAP with an app password) and turns every job link in them into a posting.

Extraction is link-based, so it survives template changes: any link that
points at a job on a known platform is a job; the title is the link text and
the company/location come from the lines printed next to it.
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.message import Message
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

from ...schemas import JobIn
from .base import SourceError

log = logging.getLogger(__name__)

# Sender domains searched for alerts. IMAP FROM matching is a substring match.
SENDERS = {
    "linkedin": ["linkedin.com"],
    "naukri": ["naukri.com"],
    "indeed": ["indeed.com"],
    "glassdoor": ["glassdoor.com", "glassdoor.co.in"],
    "foundit": ["foundit.in", "monsterindia.com"],
    "instahyre": ["instahyre.com"],
    "iimjobs": ["iimjobs.com"],
    "hirist": ["hirist.tech", "hirist.com"],
    "wellfound": ["wellfound.com", "angel.co"],
    "cutshort": ["cutshort.io"],
}

# (platform, pattern over a URL, how to build the canonical URL from the id)
_JOB_LINKS: list[tuple[str, re.Pattern[str], str]] = [
    ("linkedin", re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[\w-]*?-)?(\d{6,})"), "https://www.linkedin.com/jobs/view/{id}"),
    ("naukri", re.compile(r"naukri\.com/job-listings-[\w-]*?-(\d{9,})"), "https://www.naukri.com/job-listings-{id}"),
    ("naukri", re.compile(r"naukri\.com/.*?[?&](?:jobId|jobid|jid)=(\d{9,})"), "https://www.naukri.com/job-listings-{id}"),
    ("indeed", re.compile(r"indeed\.[a-z.]+/(?:rc/clk|viewjob|pagead/clk|m/viewjob|applystart)\b.*?[?&](?:jk|vjk)=([0-9a-f]{12,20})"), "https://www.indeed.com/viewjob?jk={id}"),
    ("glassdoor", re.compile(r"glassdoor\.[a-z.]+/.*?(?:jl=|jobListingId=)(\d{6,})"), "https://www.glassdoor.com/job-listing/x.htm?jl={id}"),
    ("foundit", re.compile(r"(?:foundit\.in|monsterindia\.com)/job/[\w-]*?-(\d{6,})"), "https://www.foundit.in/job/{id}"),
    ("instahyre", re.compile(r"instahyre\.com/job-(\d+)"), "https://www.instahyre.com/job-{id}"),
    ("iimjobs", re.compile(r"iimjobs\.com/j/[\w-]*?-(\d{5,})\.html"), "https://www.iimjobs.com/j/x-{id}.html"),
    ("hirist", re.compile(r"hirist\.(?:tech|com)/j/[\w-]*?-(\d{5,})\.html"), "https://www.hirist.tech/j/x-{id}.html"),
    ("wellfound", re.compile(r"(?:wellfound\.com|angel\.co)/(?:company/[\w-]+/)?jobs/(\d+)"), "https://wellfound.com/jobs/{id}"),
    ("cutshort", re.compile(r"cutshort\.io/job/([\w-]+-[A-Za-z0-9]{6,})"), "https://cutshort.io/job/{id}"),
]

# Link texts that are buttons, not job titles.
_NOT_TITLE = re.compile(
    r"^(view|see|apply|easy apply|apply now|view job|view details|see job|see all|see more|more jobs|unsubscribe|"
    r"manage|search|jobs? for you|check (it )?out|learn more|job details|view all|click here|\d+ ?(new )?jobs?)\b", re.I)
# Lines next to a title that are noise, not company/location.
_NOISE = re.compile(
    r"^(easy apply|apply|actively (recruiting|hiring)|promoted|new|be an early applicant|\d+ (applicants?|connections?|"
    r"alumni)|.*\bago\b|posted.*|via .*|view|save|share|dismiss|\W*)$", re.I)


def _unwrap(url: str, depth: int = 0) -> list[str]:
    """A tracking link plus every URL nested in its query string."""
    url = unescape(url)
    out = [url]
    if depth > 3:
        return out
    try:
        qs = parse_qs(urlparse(url).query)
    except ValueError:
        return out
    for values in qs.values():
        for v in values:
            v = unquote(v)
            if v.startswith(("http://", "https://")):
                out += _unwrap(v, depth + 1)
    return out


def match_job_link(url: str) -> tuple[str, str, str] | None:
    """(platform, id, canonical url) if the link points at a job posting."""
    for candidate in _unwrap(url):
        for platform, pat, canon in _JOB_LINKS:
            if m := pat.search(candidate):
                return platform, m[1], canon.format(id=m[1])
    return None


# --------------------------------------------------------------------------- tiny HTML tree


@dataclass
class _Node:
    tag: str
    attrs: dict
    parent: "_Node | None" = None
    children: list = field(default_factory=list)  # _Node | str

    def text_lines(self) -> list[str]:
        out: list[str] = []
        buf: list[str] = []

        def flush():
            line = " ".join("".join(buf).split())
            if line:
                out.append(line)
            buf.clear()

        def walk(n):
            for c in n.children:
                if isinstance(c, str):
                    buf.append(c)
                elif c.tag in ("script", "style", "head", "title"):
                    continue
                else:
                    block = c.tag in _BLOCK
                    if block:
                        flush()
                    walk(c)
                    if block:
                        flush()
        walk(self)
        flush()
        return out


_BLOCK = {"p", "div", "br", "tr", "td", "th", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6", "section", "ul", "ol"}
_VOID = {"br", "img", "hr", "meta", "link", "input", "wbr", "col", "source"}


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.cur = self.root
        self.anchors: list[_Node] = []

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        if tag == "a":
            self.anchors.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(_Node(tag, dict(attrs), self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


# --------------------------------------------------------------------------- extraction


def _split_company_location(lines: list[str]) -> tuple[str, str]:
    lines = [ln for ln in lines if not _NOISE.match(ln)]
    if not lines:
        return "", ""
    first = lines[0]
    if re.match(r"^(remote|hybrid|on-?site|work from home|wfh)\b", first, re.I):
        return "", first
    for sep in (" · ", " • ", " | ", " - "):
        if sep in first:
            company, location = first.split(sep, 1)
            return company.strip(), location.strip()
    return first, (lines[1] if len(lines) > 1 and len(lines[1]) < 80 else "")


def extract_jobs(html: str, received: str = "") -> list[JobIn]:
    """Every job linked from one alert email."""
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    groups: dict[tuple[str, str], list[_Node]] = {}
    canon: dict[tuple[str, str], str] = {}
    for a in builder.anchors:
        found = match_job_link(a.attrs.get("href") or "")
        if found:
            key = (found[0], found[1])
            groups.setdefault(key, []).append(a)
            canon[key] = found[2]
    jobs: list[JobIn] = []
    for (platform, job_id), anchors in groups.items():
        titled = [(" ".join(a.text_lines()), a) for a in anchors]
        titled = [(t, a) for t, a in titled if t and not _NOT_TITLE.match(t) and 3 <= len(t) <= 140]
        if not titled:
            continue
        title, anchor = max(titled, key=lambda x: len(x[0]))
        # Climb to the smallest block that also holds the company/location lines.
        block, lines = anchor, []
        for _ in range(6):
            block = block.parent or block
            lines = block.text_lines()
            if len(lines) >= 3 or block.tag in ("table", "root"):
                break
        try:
            after = lines[[i for i, ln in enumerate(lines) if title in ln][0] + 1:]
        except IndexError:
            after = lines
        company, location = _split_company_location(after[:4])
        experience = ""
        if m := re.search(r"\b(\d+\s*(?:-|to)\s*\d+\s*Yrs?|\d+\+?\s*(?:years?|yrs?))\b\s*\|?\s*", location, re.I):
            experience, location = m[1], (location[: m.start()] + location[m.end():]).strip(" |·-")
        snippet = " ".join(ln for ln in after if not _NOISE.match(ln))[:600]
        jobs.append(JobIn(
            source=platform,
            external_id=job_id,
            url=canon[(platform, job_id)],
            title=title,
            company=company[:120],
            location=location[:120],
            description=f"{title}\n{company}\n{location}\n{snippet}\n\n(From a {platform.title()} job-alert email; "
                        f"open the posting with the extension to capture the full description.)",
            posted_at=received,
            experience_text=experience,
            remote=True if re.search(r"\bremote\b", f"{location} {title}", re.I) else None,
        ))
    return jobs


def _html_of(msg: Message) -> str:
    parts = [p for p in msg.walk() if p.get_content_type() == "text/html"]
    if parts:
        payload = parts[0].get_payload(decode=True) or b""
        return payload.decode(parts[0].get_content_charset() or "utf-8", "replace")
    # Plain-text alert: each job is a paragraph (title, company/location lines, link).
    # Rebuild it as HTML so the same extractor works.
    texts = [p for p in msg.walk() if p.get_content_type() == "text/plain"]
    if not texts:
        return ""
    body = (texts[0].get_payload(decode=True) or b"").decode(texts[0].get_content_charset() or "utf-8", "replace")
    out = []
    for para in re.split(r"\n\s*\n", body):
        lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
        urls = [u.strip("<>()") for ln in lines for u in re.findall(r"https?://\S+", ln)]
        words = [ln for ln in lines if not re.search(r"https?://", ln)]
        if urls and words:
            title, rest = escape_html(words[0]), "<br>".join(escape_html(w) for w in words[1:])
            out += [f'<div><a href="{escape_html(u)}">{title}</a><br>{rest}</div>' for u in urls]
    return "\n".join(out)


def escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# --------------------------------------------------------------------------- IMAP


class EmailAlertSource:
    """Reads recent job-alert emails over IMAP. Never modifies the mailbox."""

    name = "email"

    def __init__(self, host: str, username: str, password: str, port: int = 993, folder: str = "INBOX",
                 days: int = 3, platforms: list[str] | None = None, imap_factory=None):
        if not (host and username and password):
            raise SourceError("email: set the IMAP server, username and app password")
        self.host, self.port, self.username, self.password = host, int(port or 993), username, password
        self.folder, self.days = folder or "INBOX", max(1, min(int(days or 3), 60))
        self.platforms = [p for p in (platforms or list(SENDERS)) if p in SENDERS]
        self._imap_factory = imap_factory or imaplib.IMAP4_SSL
        self.stats: dict[str, int] = {}

    def fetch(self) -> list[JobIn]:
        since = (date.today() - timedelta(days=self.days)).strftime("%d-%b-%Y")
        try:
            imap = self._imap_factory(self.host, self.port)
        except OSError as exc:
            raise SourceError(f"email: couldn't connect to {self.host}: {exc}") from exc
        jobs: dict[tuple[str, str], JobIn] = {}
        try:
            try:
                imap.login(self.username, self.password)
            except imaplib.IMAP4.error as exc:
                raise SourceError("email: login failed. Use an App Password (Gmail: Google Account → Security → "
                                  "App passwords) and make sure IMAP is enabled.") from exc
            status, _ = imap.select(f'"{self.folder}"', readonly=True)
            if status != "OK":
                raise SourceError(f"email: folder '{self.folder}' not found")
            for platform in self.platforms:
                count = 0
                for domain in SENDERS[platform]:
                    status, data = imap.search(None, "SINCE", since, "FROM", f'"{domain}"')
                    if status != "OK" or not data or not data[0]:
                        continue
                    for num in data[0].split()[-200:]:  # newest 200 per sender
                        status, parts = imap.fetch(num, "(BODY.PEEK[])")
                        raw = next((p[1] for p in parts if isinstance(p, tuple)), None) if status == "OK" else None
                        if not raw:
                            continue
                        msg = email.message_from_bytes(raw)
                        received = _iso_date(msg.get("Date", ""))
                        for job in extract_jobs(_html_of(msg), received):
                            key = (job.source, job.external_id)
                            if key not in jobs:
                                jobs[key] = job
                                count += 1
                self.stats[platform] = count
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001 - best effort
                pass
        return list(jobs.values())


def _iso_date(header: str) -> str:
    try:
        return email.utils.parsedate_to_datetime(header).isoformat()
    except (TypeError, ValueError):
        return ""
