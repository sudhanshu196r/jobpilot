"""Work out which job-board system a careers URL uses.

Accepts board URLs (boards.greenhouse.io/stripe, jobs.lever.co/paytm,
jobs.ashbyhq.com/ramp, jobs.smartrecruiters.com/ServiceNow,
nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite) and also a company's
own careers page, by finding the board it embeds or links to.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

import httpx

from .base import USER_AGENT, SourceError
from .providers import AshbySource, GreenhouseSource, LeverSource, SmartRecruitersSource, WorkdaySource

_RESERVED = {"embed", "jobs", "job", "v1", "api", "boards", "postings", "posting-api", "job-board", "careers"}

# Order matters: the first match wins. Each pattern captures the board id.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)", re.I)),
    ("lever", re.compile(r"api\.lever\.co/v0/postings/([\w.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)", re.I)),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([\w.-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)", re.I)),
    ("smartrecruiters", re.compile(r"api\.smartrecruiters\.com/v1/companies/([\w-]+)", re.I)),
    ("workday", re.compile(r"(https?://[\w-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[\w-]+)")),
]


@dataclass
class Detected:
    type: str
    id: str
    name: str
    jobs: int  # open roles found when checked (Workday/SmartRecruiters: a sample)


def _match(text: str) -> tuple[str, str] | None:
    for kind, pat in _PATTERNS:
        for m in pat.finditer(text):
            ident = m[1].rstrip("/.")
            if kind == "workday":
                ident = re.sub(r"/[a-z]{2}-[A-Z]{2}/", "/", ident)
            if ident.lower() not in _RESERVED:
                return kind, ident
    return None


def _probe(kind: str, ident: str, client: httpx.Client) -> Detected:
    if kind == "greenhouse":
        data = GreenhouseSource(ident, client).get_json(f"https://boards-api.greenhouse.io/v1/boards/{ident}/jobs")
        jobs = data.get("jobs", [])
        name = (jobs[0].get("company_name") if jobs else None) or ident.replace("-", " ").title()
        return Detected(kind, ident, name, len(jobs))
    if kind == "lever":
        data = LeverSource(ident, client).get_json(f"https://api.lever.co/v0/postings/{ident}", mode="json")
        if not isinstance(data, list):
            raise SourceError("lever: unexpected response")
        return Detected(kind, ident, ident.replace("-", " ").title(), len(data))
    if kind == "ashby":
        data = AshbySource(ident, client).get_json(f"https://api.ashbyhq.com/posting-api/job-board/{ident}")
        return Detected(kind, ident, ident.replace("-", " ").title(), len(data.get("jobs", [])))
    if kind == "smartrecruiters":
        src = SmartRecruitersSource(ident, client=client)
        data = src.get_json(f"{src.API}/{ident}/postings", limit="1")
        if not data.get("totalFound"):
            raise SourceError(f"smartrecruiters: no open jobs for '{ident}' (ids are case-sensitive)")
        return Detected(kind, ident, data["content"][0]["company"]["name"], int(data["totalFound"]))
    if kind == "workday":
        src = WorkdaySource(ident, client=client)
        data = src.post_json(f"https://{src.host}/wday/cxs/{src.tenant}/{src.site}/jobs",
                             {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
        return Detected(kind, f"https://{src.host}/{src.site}", src.tenant.title(), int(data.get("total") or 0))
    raise SourceError(f"Unsupported board type {kind}")


def detect(url: str, client: httpx.Client | None = None) -> dict:
    """Identify and verify the board behind ``url``. Raises SourceError when it can't."""
    url = url.strip()
    if not url:
        raise SourceError("Paste a careers page URL.")
    if not re.match(r"https?://", url):
        url = "https://" + url
    own = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=True,
                                    headers={"User-Agent": USER_AGENT.replace("JobPilot/1.0", "Mozilla/5.0")})
    try:
        found = _match(url)
        if found is None:
            # A company's own careers page: look for the board it embeds or links to.
            try:
                resp = client.get(url)
            except httpx.HTTPError as exc:
                raise SourceError(f"Couldn't open {url}: {exc}") from exc
            if resp.status_code >= 400:
                raise SourceError(f"{url} returned HTTP {resp.status_code}.")
            found = _match(str(resp.url)) or _match(resp.text[:2_000_000])
        if found is None:
            raise SourceError(
                "Couldn't find a supported job board on that page (Greenhouse, Lever, Ashby, SmartRecruiters "
                "or Workday). Try the page that lists the open roles, or a link to one job.")
        return asdict(_probe(*found, client))
    finally:
        if own:
            client.close()
