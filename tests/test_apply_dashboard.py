"""Apply from JobPilot, dashboard analytics, follow-ups and interview prep."""

from datetime import timedelta

from app.db import session_scope
from app.models import Application, Job, utcnow
from app.services import apply as apply_svc
from app.services.profile_parser import parse_heuristic

from .conftest import JD_BACKEND, SAMPLE_RESUME

# Trimmed from the live Greenhouse job-board API (?questions=true), Nov 2026.
GH_JOB = {"questions": [
    {"label": "First Name", "required": True, "fields": [{"name": "first_name", "type": "input_text"}]},
    {"label": "Email", "required": True, "fields": [{"name": "email", "type": "input_text"}]},
    {"label": "Resume/CV", "required": True, "fields": [{"name": "resume", "type": "input_file"}, {"name": "resume_text", "type": "textarea"}]},
    {"label": "Are you legally authorized to work in the country you reside?", "required": True,
     "fields": [{"name": "question_1", "type": "multi_value_single_select", "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]}]},
    {"label": "Will you now or in the future require Visa Sponsorship?", "required": True,
     "fields": [{"name": "question_2", "type": "multi_value_single_select", "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]}]},
    {"label": "Why do you want to work here?", "required": False, "fields": [{"name": "question_3", "type": "textarea"}]},
    {"label": "I agree to the privacy notice", "required": True,
     "fields": [{"name": "question_4[]", "type": "multi_value_multi_select", "values": [{"label": "I agree", "value": 1}]}]},
], "location_questions": [{"label": "Location", "required": False, "fields": [{"name": "location", "type": "input_text"}]}]}


def _job(**kw):
    return Job(**{"source": "greenhouse", "external_id": "okta:123", "title": "Backend Engineer", "url": "https://x/1", **kw})


def test_apply_urls_go_straight_to_the_form():
    assert apply_svc.apply_url(_job()) == "https://job-boards.greenhouse.io/okta/jobs/123"
    assert apply_svc.apply_url(_job(source="lever", url="https://jobs.lever.co/paytm/abc")) == "https://jobs.lever.co/paytm/abc/apply"
    assert apply_svc.apply_url(_job(source="ashby", url="https://jobs.ashbyhq.com/ramp/x")) == "https://jobs.ashbyhq.com/ramp/x/application"
    assert apply_svc.apply_url(_job(source="smartrecruiters", url="https://jobs.smartrecruiters.com/X/1")).endswith("?oga=true")
    assert apply_svc.apply_url(_job(source="naukri", url="https://www.naukri.com/job-listings-1")) == "https://www.naukri.com/job-listings-1"
    assert apply_svc.can_auto_submit(_job()) and not apply_svc.can_auto_submit(_job(source="naukri"))


def test_greenhouse_questions_normalized_and_prefilled(client):
    fields = apply_svc._normalize_questions(GH_JOB)
    assert [f["name"] for f in fields] == ["first_name", "email", "question_1", "question_2", "question_3", "question_4[]", "location"]
    assert {f["name"]: f["type"] for f in fields}["question_4[]"] == "checkbox"
    profile = parse_heuristic(SAMPLE_RESUME)
    profile.preferences.work_authorization = "Indian citizen"
    profile.preferences.requires_sponsorship = False
    with session_scope() as s:
        out = {f["name"]: f for f in apply_svc.prefill(s, profile, fields, _job(description=JD_BACKEND), None,
                                                        saved={"question_3": "I love infra."})}
    assert out["first_name"]["value"] == "Priya" and out["email"]["source"] == "profile"
    assert out["question_1"]["value"] == "Yes"  # free-text work authorization -> Yes
    assert out["question_2"]["value"] == "No"
    assert out["question_3"]["value"] == "I love infra." and out["question_3"]["source"] == "reviewed"
    assert out["question_4[]"]["value"] == "" and out["question_4[]"]["needs_review"]  # consent never pre-ticked


def test_prepare_answers_and_extension_queue(client):
    client.post("/api/profile/import", data={"text": SAMPLE_RESUME, "use_llm": "false", "save": "true"})
    job = client.post("/api/jobs", json={"title": "Backend Engineer", "company": "Acme", "description": JD_BACKEND,
                                         "url": "https://example.com/j"}).json()
    info = client.get(f"/api/jobs/{job['id']}/apply-info").json()
    assert info["form"] is None and info["apply_url"] == "https://example.com/j" and info["application"] is None
    app = client.post(f"/api/jobs/{job['id']}/prepare-apply", json={"use_llm": False}).json()
    assert app["status"] == "queued" and app["resume_id"]
    saved = client.put(f"/api/applications/{app['id']}/answers", json={
        "answers": {"question_9": "Yes", "empty": ""}, "labels": {"question_9": "Willing to work from the office?"},
        "auto_submit": True}).json()
    assert saved["answers"] == {"question_9": "Yes"}
    assert saved["auto_submit"] is False  # a "manual" job: never auto-submitted
    assert any(a["question"] == "Willing to work from the office?" for a in client.get("/api/answers").json())
    token = client.get("/api/settings").json()["extension_token"]
    q = client.get(f"/api/ext/queue?ids={app['id']}", headers={"X-JobPilot-Token": token}).json()["items"]
    assert len(q) == 1 and q[0]["answers"] == {"question_9": "Yes"} and q[0]["job"]["apply_url"] == "https://example.com/j"
    assert client.get("/api/ext/queue?ids=99999", headers={"X-JobPilot-Token": token}).json()["items"] == []


def test_analytics_followups_and_interview_prep(client):
    client.post("/api/profile/import", data={"text": SAMPLE_RESUME, "use_llm": "false", "save": "true"})
    ids = [client.post("/api/jobs", json={"title": f"Backend Engineer {i}", "company": "Acme", "description": JD_BACKEND,
                                          "url": f"https://example.com/{i}"}).json()["id"] for i in range(4)]
    with session_scope() as s:
        for jid, (status, days) in zip(ids, [("applied", 10), ("applied", 2), ("interview", 12), ("rejected", 20)]):
            s.add(Application(job_id=jid, status=status, applied_at=utcnow() - timedelta(days=days)))
        s.commit()
    a = client.get("/api/analytics").json()
    assert [f["count"] for f in a["funnel"]] == [4, 4, 1, 0]
    assert a["kpis"]["response_rate"] == 0.5 and a["kpis"]["applied_week"] == 1
    assert len(a["activity"]) == 30 and sum(d["applied"] for d in a["activity"]) == 4
    assert sum(b["count"] for b in a["scores"]) == 4 and a["sources"] == [{"source": "manual", "count": 4}]
    assert [f["days"] for f in a["follow_ups"]] == [10]
    client.post(f"/api/applications/{a['follow_ups'][0]['application_id']}/follow-up")
    assert client.get("/api/analytics").json()["follow_ups"] == []
    prep = client.post(f"/api/jobs/{ids[0]}/interview-prep").json()
    assert prep["method"] == "heuristic" and prep["technical"] and prep["questions_to_ask"]
    assert any("Kafka" in q["question"] for q in prep["technical"])
    assert "Terraform" in " ".join(prep["gaps_to_prepare"])
    assert client.get(f"/api/jobs/{ids[0]}").json()["score_detail"]["interview_prep"]["summary"]


def test_versions_match_charts_import():
    import re

    from app import __version__
    from app.main import STATIC_DIR

    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert re.search(r'charts\.js\?v=([\d.]+)"', js)[1] == __version__
