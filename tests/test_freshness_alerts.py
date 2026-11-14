"""Closed/stale job detection and new-match email alerts."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.db import session_scope
from app.models import Job
from app.schemas import JobIn
from app.services import alerts, liveness, store

from .conftest import JD_BACKEND, SAMPLE_RESUME

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("posted,days", [
    ("2026-11-10", 4), ("2026-11-13T08:00:00Z", 1), ("1762905600000", 2),  # Lever epoch ms (2025-11-12)
    ("Just now", 0), ("Today", 0), ("3 days ago", 3), ("30+ days ago", 30), ("Posted 2 weeks ago", 14),
    ("1 month ago", 30), ("", 5),
])
def test_age_days(posted, days):
    # Captured just now: the posted text alone decides.
    job = Job(source="x", external_id="1", title="t", posted_at=posted, fetched_at=NOW, updated_at=NOW)
    expected = 367 if posted == "1762905600000" else (0 if posted == "" else days)
    assert liveness.age_days(job, NOW) == expected


def test_age_counts_from_capture_and_first_seen():
    # "3 days ago" captured 2 days back means posted 5 days ago.
    job = Job(source="x", external_id="1", title="t", posted_at="3 days ago",
              fetched_at=NOW - timedelta(days=2), updated_at=NOW - timedelta(days=2))
    assert liveness.age_days(job, NOW) == 5
    # First seen 10 days ago but labelled "Today" by a re-capture: still 10 days old.
    job = Job(source="x", external_id="1", title="t", posted_at="Today",
              fetched_at=NOW - timedelta(days=10), updated_at=NOW)
    assert liveness.age_days(job, NOW) == 10
    assert liveness.freshness(job, NOW) == {"age_days": 10, "stale": False, "closed": False}


def _client(routes):
    def handler(request):
        for prefix, resp in routes.items():
            if str(request.url).startswith(prefix):
                return resp(request) if callable(resp) else resp
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_check_url_open_closed_unknown():
    c = _client({
        "https://ok.example/job": httpx.Response(200, text="<h1>Engineer</h1> Apply now"),
        "https://gone.example/job": httpx.Response(410),
        "https://text.example/job": httpx.Response(200, text="Sorry, this job is no longer accepting applications."),
        "https://blocked.example/job": httpx.Response(403, text="captcha"),
        "https://jobs.lever.co/acme/123": httpx.Response(302, headers={"Location": "https://jobs.lever.co/acme"}),
        "https://jobs.lever.co/acme": httpx.Response(200, text="All jobs"),
    })
    assert liveness.check_url("https://ok.example/job", c) == "open"
    assert liveness.check_url("https://gone.example/job", c) == "closed"
    assert liveness.check_url("https://text.example/job", c) == "closed"
    assert liveness.check_url("https://blocked.example/job", c) == "unknown"  # never close on a block
    assert liveness.check_url("https://jobs.lever.co/acme/123", c) == "closed"
    assert liveness.check_url("not a url", c) == "unknown"


class _Board:
    name = "greenhouse"

    def __init__(self, ids):
        self.board, self.ids = "acme", ids

    def fetch(self):
        return [JobIn(source="greenhouse", external_id=f"acme:{i}", title=f"Backend Engineer {i}", company="Acme",
                      description=JD_BACKEND, url=f"https://x/{i}") for i in self.ids]


def test_board_sync_closes_and_reopens(client, monkeypatch):
    client.post("/api/profile/import", data={"text": SAMPLE_RESUME, "use_llm": "false", "save": "true"})
    feed = {"ids": [1, 2, 3]}
    monkeypatch.setattr(store, "build_sources", lambda cfg: ([_Board(feed["ids"])], []))
    with session_scope() as s:
        assert store.run_ingest(s)["new"] == 3
        feed["ids"] = [1, 3]  # posting 2 taken down
        assert store.run_ingest(s)["closed"] == 1
    listed = client.get("/api/jobs").json()
    assert sorted(j["external_id"] for j in listed["items"]) == ["acme:1", "acme:3"]
    closed = client.get("/api/jobs?status=closed").json()["items"]
    assert [j["external_id"] for j in closed] == ["acme:2"] and closed[0]["freshness"]["closed"]
    # Queueing a closed job is refused; reappearing reopens it.
    r = client.post("/api/applications/bulk", json={"job_ids": [closed[0]["id"]], "use_llm": False})
    assert r.json() == {"queued": 0, "skipped_already_applied": 0, "skipped_closed": 1}
    feed["ids"] = [1, 2, 3]
    with session_scope() as s:
        store.run_ingest(s)
    assert client.get("/api/jobs?status=closed").json()["total"] == 0


def test_check_endpoint_marks_closed(client, monkeypatch):
    job = client.post("/api/jobs", json={"title": "Dev", "description": JD_BACKEND, "url": "https://gone.example/1"}).json()
    real = liveness.check_url
    monkeypatch.setattr(liveness, "check_url", lambda url, c: real(url, _client({"https://gone.example": httpx.Response(404)})))
    r = client.post(f"/api/jobs/{job['id']}/check").json()
    assert r["status"] == "closed" and r["job"]["closed_at"]
    assert client.get("/api/jobs").json()["total"] == 0
    assert client.get("/api/stats").json()["closed_jobs"] == 1


class _FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None, **kw):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        if password != "app-password":
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"bad")

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)


def test_alerts_digest_baseline_and_errors(client, monkeypatch):
    monkeypatch.setattr(alerts.smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.sent = []
    client.post("/api/profile/import", data={"text": SAMPLE_RESUME, "use_llm": "false", "save": "true"})
    client.post("/api/jobs", json={"title": "Old Backend Engineer", "company": "Before", "description": JD_BACKEND})

    cfg = {"alerts": {"enabled": True, "min_score": 60, "email_to": "me@example.com",
                      "smtp": {"host": "smtp.example.com", "port": 587, "username": "me", "password": "app-password"}}}
    client.put("/api/settings", json=cfg)  # enabling baselines existing jobs
    assert client.get("/api/settings").json()["alerts"]["smtp"]["password"] == "••••word"

    monkeypatch.setattr(store, "build_sources", lambda c: ([_Board([7, 8])], []))
    with session_scope() as s:
        result = store.run_ingest(s)
    assert result["alert"] == {"sent": True, "matches": 2}
    msg = _FakeSMTP.sent[-1]
    assert msg["To"] == "me@example.com" and "2 new matches" in msg["Subject"]
    body = msg.get_body(("plain",)).get_content()
    assert "Backend Engineer 7" in body and "Old Backend Engineer" not in body
    assert client.post("/api/alerts/send-now").json()["reason"] == "no new matches"  # not sent twice

    # Bad password -> clear message, and jobs stay pending for the next try.
    client.put("/api/settings", json={"alerts": {"smtp": {"password": "wrong"}}})
    r = client.post("/api/alerts/test")
    assert r.status_code == 400 and "App Password" in r.json()["detail"]
