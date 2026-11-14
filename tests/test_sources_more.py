"""SmartRecruiters, Workday, careers-URL detection, the company directory and alert-email import."""

import email
import imaplib
from email.message import EmailMessage
from pathlib import Path

import httpx
import pytest

from app.db import session_scope
from app.services import store
from app.services.sources import SmartRecruitersSource, SourceError, WorkdaySource, build_sources
from app.services.sources.detect import _match, detect
from app.services.sources.directory import COMPANIES
from app.services.sources.email_alerts import EmailAlertSource, extract_jobs, match_job_link

from .conftest import SAMPLE_RESUME

EMAILS = Path(__file__).parent / "fixtures" / "emails"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# Payload shapes below were copied from the live APIs (Nov 2026), trimmed.
SR_LIST = {"offset": 0, "limit": 100, "totalFound": 1, "content": [{
    "id": "744000161370789", "name": "Staff Software Engineer", "releasedDate": "2026-11-12T19:24:59.688Z",
    "company": {"identifier": "ServiceNow", "name": "ServiceNow"},
    "location": {"city": "Hyderabad", "region": "", "country": "in", "remote": False, "fullLocation": "Hyderabad, , India"},
    "experienceLevel": {"id": "mid_senior_level", "label": "Mid-Senior Level"}}]}
SR_DETAIL = {"postingUrl": "https://jobs.smartrecruiters.com/ServiceNow/744000161370789-staff-software-engineer",
             "jobAd": {"sections": {
                 "companyDescription": {"title": "Company Description", "text": "<p>About us</p>"},
                 "jobDescription": {"title": "Job Description", "text": "<p>Build <b>Python</b> services on Kafka.</p>"},
                 "qualifications": {"title": "Qualifications", "text": "<ul><li>5+ years</li></ul>"}}}}


def test_smartrecruiters_search_and_details():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json=SR_DETAIL if req.url.path.endswith("744000161370789") else SR_LIST)

    jobs = SmartRecruitersSource("ServiceNow", ["software engineer"], "in", 10, _client(handler)).fetch()
    assert "q=software+engineer" in seen[0] and "country=in" in seen[0]
    j = jobs[0]
    assert (j.external_id, j.company, j.location) == ("ServiceNow:744000161370789", "ServiceNow", "Hyderabad, India")
    assert j.url.startswith("https://jobs.smartrecruiters.com/ServiceNow/") and j.experience_text == "Mid-Senior Level"
    assert "Python" in j.description and "About us" not in j.description and "• 5+ years" in j.description


WD_LIST = {"total": 2000, "jobPostings": [{
    "title": "Senior System Software Engineer", "externalPath": "/job/India-Bengaluru/Senior-System-Software-Engineer_JR2029799",
    "locationsText": "India, Bengaluru", "postedOn": "Posted Today", "bulletFields": ["JR2029799"]}]}
WD_DETAIL = {"jobPostingInfo": {
    "title": "Senior System Software Engineer", "jobDescription": "<p>Work on <b>C++</b> and Linux.</p>",
    "location": "India, Bengaluru", "startDate": "2026-11-14", "timeType": "Full time", "jobReqId": "JR2029799",
    "externalUrl": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/India-Bengaluru/Senior_JR2029799"},
    "hiringOrganization": {"name": "2100 NVIDIA USA", "url": ""}}


def test_workday_search_and_details():
    bodies = []

    def handler(req):
        if req.method == "POST":
            bodies.append(req.content)
            return httpx.Response(200, json=WD_LIST)
        assert req.url.path == "/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/India-Bengaluru/Senior-System-Software-Engineer_JR2029799"
        return httpx.Response(200, json=WD_DETAIL)

    src = WorkdaySource("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite", ["backend"], 1, _client(handler))
    j = src.fetch()[0]
    assert b'"searchText":"backend"' in bodies[0].replace(b" ", b"")
    assert (j.external_id, j.company, j.location, j.posted_at) == ("nvidia:JR2029799", "NVIDIA", "India, Bengaluru", "2026-11-14")
    assert j.description == "Work on C++ and Linux." and j.url == WD_DETAIL["jobPostingInfo"]["externalUrl"]
    with pytest.raises(SourceError):
        WorkdaySource("https://example.com/careers")


def test_build_sources_uses_directory_names_and_default_keywords():
    sources, errors = build_sources({"lever": ["cred"], "smartrecruiters": ["BoschGroup"],
                                     "workday": ["https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite", "bad-url"]},
                                    default_keywords=["Backend Engineer"])
    names = {(s.name, s.display_name) for s in sources}
    assert {("lever", "CRED"), ("smartrecruiters", "Bosch"), ("workday", "NVIDIA")} <= names
    assert next(s for s in sources if s.name == "workday").keywords == ["Backend Engineer"]
    assert errors and "workday" in errors[0]


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/stripe/jobs/123", ("greenhouse", "stripe")),
    ("https://job-boards.greenhouse.io/databricks", ("greenhouse", "databricks")),
    ("https://jobs.lever.co/paytm/abc", ("lever", "paytm")),
    ("https://jobs.ashbyhq.com/ramp", ("ashby", "ramp")),
    ("https://jobs.smartrecruiters.com/ServiceNow/744", ("smartrecruiters", "ServiceNow")),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/x",
     ("workday", "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")),
    ('<script src="https://boards.greenhouse.io/embed/job_board/js?for=okta"></script>', ("greenhouse", "okta")),
    ("https://example.com/about", None),
])
def test_match_board_urls(url, expected):
    assert _match(url) == expected


def test_detect_finds_board_embedded_in_careers_page():
    def handler(req):
        if req.url.host == "careers.acme.example":
            return httpx.Response(200, text='<html><a href="https://jobs.lever.co/acme-corp">Open roles</a></html>')
        assert str(req.url).startswith("https://api.lever.co/v0/postings/acme-corp")
        return httpx.Response(200, json=[{"id": "1"}, {"id": "2"}])

    assert detect("careers.acme.example/jobs", _client(handler)) == {"type": "lever", "id": "acme-corp", "name": "Acme Corp", "jobs": 2}
    with pytest.raises(SourceError, match="Couldn't find"):
        detect("https://plain.example", _client(lambda r: httpx.Response(200, text="<html>No jobs here</html>")))


def test_directory_api_follow_unfollow(client):
    assert len(COMPANIES) > 50 and all({"type", "id", "name", "india"} <= set(c) for c in COMPANIES)
    paytm = next(c for c in client.get("/api/sources/directory").json() if c["name"] == "Paytm")
    assert paytm["followed"] is False
    lists = client.post("/api/sources/follow", json={"type": "lever", "id": "paytm"}).json()
    client.post("/api/sources/follow", json={"type": "lever", "id": "PAYTM"})  # no duplicate
    assert lists["lever"] == ["paytm"] and client.get("/api/settings").json()["sources"]["lever"] == ["paytm"]
    assert next(c for c in client.get("/api/sources/directory").json() if c["name"] == "Paytm")["followed"]
    assert client.post("/api/sources/unfollow", json={"type": "lever", "id": "paytm"}).json()["lever"] == []


# --------------------------------------------------------------------------- alert emails


def test_match_job_link_unwraps_tracking():
    assert match_job_link("https://www.linkedin.com/comm/jobs/view/4012345678/?trk=x") == (
        "linkedin", "4012345678", "https://www.linkedin.com/jobs/view/4012345678")
    wrapped = "https://click.naukri.com/t?u=https%3A%2F%2Fwww.naukri.com%2Fjob-listings-sde-acme-pune-2-to-4-years-141125000001"
    assert match_job_link(wrapped)[:2] == ("naukri", "141125000001")
    assert match_job_link("https://in.indeed.com/rc/clk?jk=a1b2c3d4e5f60718&from=ja")[:2] == ("indeed", "a1b2c3d4e5f60718")
    assert match_job_link("https://www.linkedin.com/comm/jobs/search?keywords=x") is None


def test_extract_linkedin_and_naukri_alerts():
    li = extract_jobs((EMAILS / "linkedin.html").read_text())
    assert [(j.title, j.company, j.location) for j in li] == [
        ("Senior Backend Engineer", "Acme Technologies", "Bengaluru, Karnataka, India (Hybrid)"),
        ("Python Developer", "Globex", "Remote")]
    assert li[1].remote is True and li[0].url == "https://www.linkedin.com/jobs/view/4012345678"
    nk = extract_jobs((EMAILS / "naukri.html").read_text())
    assert [(j.external_id, j.title, j.company, j.location, j.experience_text) for j in nk] == [
        ("141125012345", "Data Engineer", "Flipkart", "Bengaluru", "3-6 Yrs"),
        ("141125099999", "Backend Developer - Java", "Zeta", "Hyderabad", "")]


def _mail(sender: str, html: str | None = None, text: str | None = None) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, "me@example.com", "Jobs for you"
    m["Date"] = "Fri, 14 Nov 2026 07:30:00 +0530"
    if html:
        m.set_content("see html")
        m.add_alternative(html, subtype="html")
    else:
        m.set_content(text)
    return m.as_bytes()


class FakeIMAP:
    """Just enough of imaplib.IMAP4_SSL; records that nothing is ever modified."""

    mailbox = {
        "linkedin.com": [_mail("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", (EMAILS / "linkedin.html").read_text())],
        "naukri.com": [_mail("Naukri <jobalert@naukri.com>", (EMAILS / "naukri.html").read_text())],
        "indeed.com": [_mail("Indeed <alert@indeed.com>", text=(EMAILS / "indeed.txt").read_text())],
    }
    commands: list = []

    def __init__(self, host, port):
        self.host = host

    def login(self, user, password):
        if password != "app-pass":
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def select(self, folder, readonly=False):
        FakeIMAP.commands.append(("select", folder, readonly))
        return "OK", [b"3"]

    def search(self, charset, *criteria):
        FakeIMAP.commands.append(("search",) + criteria)
        domain = criteria[-1].strip('"')
        msgs = self.mailbox.get(domain, [])
        self._last = {str(i + 1).encode(): m for i, m in enumerate(msgs)}
        return "OK", [b" ".join(self._last)]

    def fetch(self, num, what):
        FakeIMAP.commands.append(("fetch", what))
        return "OK", [(b"1 (BODY[] {n}", self._last[num]), b")"]

    def logout(self):
        pass


def test_email_alert_source_reads_mailbox_read_only():
    FakeIMAP.commands = []
    src = EmailAlertSource("imap.example.com", "me", "app-pass", days=3, imap_factory=FakeIMAP)
    jobs = src.fetch()
    assert sorted({j.source for j in jobs}) == ["indeed", "linkedin", "naukri"]
    assert src.stats["linkedin"] == 2 and src.stats["naukri"] == 2 and src.stats["indeed"] == 2
    indeed = {j.title: j for j in jobs if j.source == "indeed"}
    assert indeed["Python Developer"].company == "Persistent Systems" and indeed["Senior Python Engineer"].location == "Remote"
    assert all(j.posted_at.startswith("2026-11-14") for j in jobs)
    assert ("select", '"INBOX"', True) in FakeIMAP.commands  # read-only
    assert all(c[1] == "(BODY.PEEK[])" for c in FakeIMAP.commands if c[0] == "fetch")  # never marks as read
    with pytest.raises(SourceError, match="App Password"):
        EmailAlertSource("imap.example.com", "me", "wrong", imap_factory=FakeIMAP).fetch()


def test_ingest_merges_email_jobs_and_extension_captures(client, monkeypatch):
    client.post("/api/profile/import", data={"text": SAMPLE_RESUME, "use_llm": "false", "save": "true"})
    monkeypatch.setattr("app.services.sources.email_alerts.imaplib.IMAP4_SSL", FakeIMAP)
    client.put("/api/settings", json={"email_import": {"enabled": True, "username": "me", "password": "app-pass"}})
    assert client.get("/api/settings").json()["email_import"]["password"] == "••••pass"
    preview = client.post("/api/sources/email/test").json()
    assert preview["found"] == 6 and preview["per_platform"]["linkedin"] == 2
    with session_scope() as s:
        result = store.run_ingest(s)
    assert result["new"] == 6 and result["per_source"]["email:naukri"] == 2
    jobs = client.get("/api/jobs?source=linkedin").json()["items"]
    assert {j["title"] for j in jobs} == {"Senior Backend Engineer", "Python Developer"}
    # Opening the same LinkedIn job with the extension later fills in the full description.
    token = client.get("/api/settings").json()["extension_token"]
    full = "We need Python, Kafka, PostgreSQL, Redis and AWS experience. 4-7 years of experience. " * 3
    r = client.post("/api/ext/jobs", headers={"X-JobPilot-Token": token}, json={"jobs": [{
        "source": "linkedin", "external_id": "4012345678", "title": "Senior Backend Engineer", "description": full}]})
    assert r.json()["created"] == 0
    job = client.get(f"/api/jobs/{r.json()['results'][0]['job_id']}").json()
    assert job["description"].startswith("We need Python") and job["company"] == "Acme Technologies"


def test_background_fetch_reports_progress(client, monkeypatch):
    import time

    class Slow:
        name, board, display_name = "greenhouse", "slowco", "SlowCo"

        def fetch(self):
            time.sleep(0.3)
            return []

    monkeypatch.setattr(store, "build_sources", lambda cfg, **kw: ([Slow(), Slow()], []))
    assert client.post("/api/ingest/run?background=true").json() == {"status": "started"}
    assert client.post("/api/ingest/run?background=true").json() == {"status": "busy"}
    st = client.get("/api/ingest/status").json()
    assert st["running"] and st["total"] == 2
    for _ in range(50):
        st = client.get("/api/ingest/status").json()
        if not st["running"]:
            break
        time.sleep(0.1)
    assert not st["running"] and st["last"]["sources"] == 2 and st["last"]["errors"] == []


def test_concurrent_captures_of_the_same_job_do_not_collide(client):
    """Listing + detail pane posted at the same moment used to hit UNIQUE(source, external_id)."""
    from concurrent.futures import ThreadPoolExecutor

    token = client.get("/api/settings").json()["extension_token"]
    h = {"X-JobPilot-Token": token}
    jobs = [{"jobs": [{"source": "linkedin", "external_id": f"9{n:05d}", "title": "Backend Engineer",
                       "description": "x" * k}]} for n in range(10) for k in (10, 500)]
    with ThreadPoolExecutor(8) as pool:
        statuses = list(pool.map(lambda body: client.post("/api/ext/jobs", headers=h, json=body).status_code, jobs))
    assert statuses == [200] * len(jobs)
    found = client.get("/api/jobs?source=linkedin&limit=50").json()
    assert found["total"] == 10
    assert all(len(client.get(f"/api/jobs/{j['id']}").json()["description"]) == 500 for j in found["items"])
