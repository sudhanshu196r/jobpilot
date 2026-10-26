from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from ...schemas import JobIn

log = logging.getLogger(__name__)

USER_AGENT = "JobPilot/1.0 (personal job search assistant)"


class SourceError(RuntimeError):
    pass


class JobSource(ABC):
    """A provider of job postings through an official/public API."""

    name: str

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    def get_json(self, url: str, **params):
        try:
            resp = self.client.get(url, params=params or None)
        except httpx.HTTPError as exc:
            raise SourceError(f"{self.name}: request failed: {exc}") from exc
        if resp.status_code == 404:
            raise SourceError(f"{self.name}: not found ({url})")
        if resp.status_code >= 400:
            raise SourceError(f"{self.name}: HTTP {resp.status_code} for {url}")
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceError(f"{self.name}: invalid JSON from {url}") from exc

    @abstractmethod
    def fetch(self) -> list[JobIn]:
        ...
