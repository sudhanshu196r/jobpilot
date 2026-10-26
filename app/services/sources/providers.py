"""Job sources backed by public, documented APIs.

* Greenhouse  https://developers.greenhouse.io/job-board.html
* Lever       https://github.com/lever/postings-api
* Ashby       https://developers.ashbyhq.com/docs/public-job-posting-api
* Remotive    https://remotive.com/api-documentation
* Adzuna      https://developer.adzuna.com/  (aggregator; covers India via country "in")

Naukri and Indeed have no public job APIs; their postings come in through the
browser extension, which reads the pages you are already viewing.
"""

from __future__ import annotations

import httpx

from ...schemas import JobIn
from ..text import html_to_text
from .base import JobSource, SourceError


def _is_remote(*values: str | None) -> bool | None:
    joined = " ".join(v for v in values if v).lower()
    if not joined:
        return None
    return any(k in joined for k in ("remote", "anywhere", "work from home"))


class GreenhouseSource(JobSource):
    name = "greenhouse"

    def __init__(self, board: str, client: httpx.Client | None = None):
        super().__init__(client)
        self.board = board.strip().strip("/").split("/")[-1]

    def fetch(self) -> list[JobIn]:
        data = self.get_json(f"https://boards-api.greenhouse.io/v1/boards/{self.board}/jobs", content="true")
        company = data.get("meta", {}).get("name") if isinstance(data.get("meta"), dict) else None
        out = []
        for j in data.get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            out.append(JobIn(
                source=self.name,
                external_id=f"{self.board}:{j['id']}",
                url=j.get("absolute_url", ""),
                title=j.get("title", "").strip(),
                company=company or j.get("company_name") or self.board.title(),
                location=loc,
                remote=_is_remote(loc),
                description=html_to_text(j.get("content", "")),
                posted_at=j.get("first_published") or j.get("updated_at", ""),
                apply_type="external",
            ))
        return out


class LeverSource(JobSource):
    name = "lever"

    def __init__(self, company: str, client: httpx.Client | None = None):
        super().__init__(client)
        self.company = company.strip().strip("/").split("/")[-1]

    def fetch(self) -> list[JobIn]:
        data = self.get_json(f"https://api.lever.co/v0/postings/{self.company}", mode="json")
        if not isinstance(data, list):
            raise SourceError(f"lever: unexpected response for {self.company}")
        out = []
        for j in data:
            cats = j.get("categories") or {}
            parts = [j.get("descriptionPlain") or html_to_text(j.get("description", ""))]
            for lst in j.get("lists") or []:
                parts.append(f"{lst.get('text', '')}:\n{html_to_text(lst.get('content', ''))}")
            parts.append(j.get("additionalPlain") or html_to_text(j.get("additional", "")))
            loc = cats.get("location", "") or ", ".join(cats.get("allLocations") or [])
            out.append(JobIn(
                source=self.name,
                external_id=f"{self.company}:{j['id']}",
                url=j.get("hostedUrl", ""),
                title=j.get("text", "").strip(),
                company=self.company.replace("-", " ").title(),
                location=loc,
                remote=(j.get("workplaceType") == "remote") or _is_remote(loc),
                description="\n\n".join(p for p in parts if p),
                salary=_lever_salary(j.get("salaryRange")),
                posted_at=str(j.get("createdAt", "")),
                apply_type="external",
            ))
        return out


def _lever_salary(rng: dict | None) -> str:
    if not rng:
        return ""
    lo, hi, cur = rng.get("min"), rng.get("max"), rng.get("currency", "")
    return f"{cur} {lo:,}-{hi:,}".strip() if lo and hi else ""


class AshbySource(JobSource):
    name = "ashby"

    def __init__(self, board: str, client: httpx.Client | None = None):
        super().__init__(client)
        self.board = board.strip().strip("/").split("/")[-1]

    def fetch(self) -> list[JobIn]:
        data = self.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{self.board}", includeCompensation="true")
        out = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            comp = j.get("compensation") or {}
            out.append(JobIn(
                source=self.name,
                external_id=f"{self.board}:{j['id']}",
                url=j.get("jobUrl") or j.get("applyUrl", ""),
                title=j.get("title", "").strip(),
                company=self.board.replace("-", " ").title(),
                location=j.get("location", ""),
                remote=bool(j.get("isRemote")) or _is_remote(j.get("location")),
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
                salary=comp.get("compensationTierSummary", "") if isinstance(comp, dict) else "",
                posted_at=j.get("publishedAt", ""),
                apply_type="external",
            ))
        return out


class RemotiveSource(JobSource):
    name = "remotive"

    def __init__(self, query: str, client: httpx.Client | None = None):
        super().__init__(client)
        self.query = query

    def fetch(self) -> list[JobIn]:
        data = self.get_json("https://remotive.com/api/remote-jobs", search=self.query, limit="100")
        out = []
        for j in data.get("jobs", []):
            out.append(JobIn(
                source=self.name,
                external_id=str(j["id"]),
                url=j.get("url", ""),
                title=j.get("title", "").strip(),
                company=j.get("company_name", ""),
                location=j.get("candidate_required_location", "") or "Remote",
                remote=True,
                description=html_to_text(j.get("description", "")),
                salary=j.get("salary", "") or "",
                posted_at=j.get("publication_date", ""),
                apply_type="external",
            ))
        return out


class AdzunaSource(JobSource):
    name = "adzuna"

    def __init__(self, app_id: str, app_key: str, country: str, query: str, where: str = "",
                 max_pages: int = 2, client: httpx.Client | None = None):
        super().__init__(client)
        if not app_id or not app_key:
            raise SourceError("adzuna: app_id and app_key are required (free at developer.adzuna.com)")
        self.app_id, self.app_key = app_id, app_key
        self.country, self.query, self.where = (country or "in").lower(), query, where
        self.max_pages = max(1, min(int(max_pages or 1), 10))

    def fetch(self) -> list[JobIn]:
        out: list[JobIn] = []
        for page in range(1, self.max_pages + 1):
            params = {"app_id": self.app_id, "app_key": self.app_key, "what": self.query,
                      "results_per_page": "50", "content-type": "application/json"}
            if self.where:
                params["where"] = self.where
            data = self.get_json(f"https://api.adzuna.com/v1/api/jobs/{self.country}/search/{page}", **params)
            results = data.get("results", [])
            for j in results:
                lo, hi = j.get("salary_min"), j.get("salary_max")
                out.append(JobIn(
                    source=self.name,
                    external_id=str(j["id"]),
                    url=j.get("redirect_url", ""),
                    title=html_to_text(j.get("title", "")),
                    company=(j.get("company") or {}).get("display_name", ""),
                    location=(j.get("location") or {}).get("display_name", ""),
                    remote=_is_remote(j.get("title"), (j.get("location") or {}).get("display_name")),
                    description=html_to_text(j.get("description", "")),
                    salary=f"{int(lo):,}-{int(hi):,}" if lo and hi else "",
                    posted_at=j.get("created", ""),
                    apply_type="external",
                ))
            if len(results) < 50:
                break
        return out


def build_sources(cfg: dict, client: httpx.Client | None = None) -> tuple[list[JobSource], list[str]]:
    """Instantiate every configured source. Returns (sources, config_errors)."""
    sources: list[JobSource] = []
    errors: list[str] = []
    for board in cfg.get("greenhouse") or []:
        if board.strip():
            sources.append(GreenhouseSource(board, client))
    for company in cfg.get("lever") or []:
        if company.strip():
            sources.append(LeverSource(company, client))
    for board in cfg.get("ashby") or []:
        if board.strip():
            sources.append(AshbySource(board, client))
    rem = cfg.get("remotive") or {}
    if rem.get("enabled"):
        for q in rem.get("queries") or [""]:
            sources.append(RemotiveSource(q, client))
    adz = cfg.get("adzuna") or {}
    if adz.get("enabled"):
        for q in adz.get("queries") or []:
            try:
                sources.append(AdzunaSource(adz.get("app_id", ""), adz.get("app_key", ""), adz.get("country", "in"),
                                            q, adz.get("where", ""), adz.get("max_pages", 2), client))
            except SourceError as exc:
                errors.append(str(exc))
                break
    return sources, errors
