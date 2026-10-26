from pathlib import Path

from app.models import Application
from app.services import store

from .conftest import JD_BACKEND, JD_UNRELATED, SAMPLE_RESUME


def _setup_profile(client):
    r = client.post("/api/profile/import", data={"text": SAMPLE_RESUME, "use_llm": "false"})
    assert r.status_code == 200, r.text
    draft = r.json()["profile"]
    draft["preferences"].update({"target_titles": ["Backend Engineer"], "locations": ["Bengaluru"],
                                 "notice_period_days": 30, "expected_ctc": "45 LPA"})
    r = client.put("/api/profile", json=draft)
    assert r.status_code == 200, r.text
    return r.json()


def _add_job(client, title, description, company="Acme", location="Bengaluru", url="https://example.com/job"):
    r = client.post("/api/jobs", json={"title": title, "company": company, "location": location,
                                       "url": url, "description": description})
    assert r.status_code == 200, r.text
    return r.json()


def test_csrf_guard_blocks_writes_without_header(client):
    r = client.put("/api/profile", json={}, headers={"X-JobPilot-Client": ""})
    assert r.status_code == 403


def test_full_flow(client):
    prof = _setup_profile(client)
    assert prof["derived"]["total_years"] > 7

    good = _add_job(client, "Senior Backend Engineer", JD_BACKEND, url="https://example.com/a")
    bad = _add_job(client, "Senior Graphic Designer", JD_UNRELATED, company="Pixel", url="https://example.com/b")
    jobs = client.get("/api/jobs").json()["items"]
    assert [j["id"] for j in jobs][0] == good["id"]
    scores = {j["id"]: j["score"] for j in jobs}
    assert scores[good["id"]] >= 70 > 40 > scores[bad["id"]]
    detail = client.get(f"/api/jobs/{good['id']}").json()
    assert "Kafka" in detail["score_detail"]["matched_skills"]

    r = client.post("/api/resumes", json={"job_id": good["id"], "use_llm": False, "cover_letter": True})
    assert r.status_code == 200, r.text
    resume = r.json()
    assert resume["method"] == "heuristic" and resume["has_pdf"]
    assert resume["cover_letter"].startswith("Dear Hiring Team")
    pdf = client.get(f"/api/resumes/{resume['id']}/file/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    docx = client.get(f"/api/resumes/{resume['id']}/file/docx")
    assert docx.status_code == 200 and docx.content[:2] == b"PK"

    # Edit the resume: re-renders and recomputes keyword coverage.
    doc = resume["doc"]
    doc["skills"] = ["Python"]
    r = client.put(f"/api/resumes/{resume['id']}", json={"doc": doc})
    assert r.status_code == 200 and r.json()["keyword_report"]["coverage"] < resume["keyword_report"]["coverage"]

    # Bulk apply (background task runs inside TestClient).
    r = client.post("/api/applications/bulk", json={"job_ids": [good["id"], bad["id"]], "use_llm": False})
    assert r.status_code == 200 and r.json()["queued"] == 2
    apps = client.get("/api/applications").json()
    assert {a["status"] for a in apps} == {"queued"} and all(a["resume_id"] for a in apps)

    # Extension: needs a token.
    assert client.get("/api/ext/queue").status_code == 401
    token = client.get("/api/settings").json()["extension_token"]
    h = {"X-JobPilot-Token": token}
    q = client.get("/api/ext/queue", headers=h).json()
    assert [i["job"]["id"] for i in q["items"]] == [good["id"], bad["id"]]
    rpdf = client.get(f"/api/ext/resumes/{q['items'][0]['resume_id']}/pdf", headers=h)
    assert rpdf.content[:5] == b"%PDF-"
    r = client.post(f"/api/ext/applications/{q['items'][0]['application_id']}/status", headers=h,
                    json={"status": "applied"})
    assert r.json()["status"] == "applied"
    stats = client.get("/api/stats").json()
    assert stats["applications"]["applied"] == 1

    # Applied jobs are not re-queued.
    r = client.post("/api/applications/bulk", json={"job_ids": [good["id"]], "use_llm": False})
    assert r.json() == {"queued": 0, "skipped_already_applied": 1}


def test_extension_capture_and_dedupe(client):
    _setup_profile(client)
    token = client.get("/api/settings").json()["extension_token"]
    h = {"X-JobPilot-Token": token}
    payload = {"jobs": [{"source": "naukri", "external_id": "123", "title": "Backend Developer - Python",
                         "company": "Foo Pvt Ltd", "location": "Bengaluru", "description": "Python Django Kafka",
                         "experience_text": "3-6 Yrs", "url": "https://www.naukri.com/job-listings-123"}]}
    r = client.post("/api/ext/jobs", headers=h, json=payload)
    assert r.status_code == 200 and r.json()["created"] == 1
    res = r.json()["results"][0]
    assert res["score"] is not None and res["job_id"]
    # Same job again with a fuller description updates in place.
    payload["jobs"][0]["description"] = JD_BACKEND
    r = client.post("/api/ext/jobs", headers=h, json=payload)
    assert r.json()["created"] == 0
    # The same role on Indeed merges into the existing one.
    payload["jobs"][0].update({"source": "indeed", "external_id": "jk9", "company": "Foo"})
    r = client.post("/api/ext/jobs", headers=h, json=payload)
    assert r.json()["created"] == 0 and r.json()["results"][0]["job_id"] == res["job_id"]


def test_answers_memory_and_profile_facts(client):
    _setup_profile(client)
    token = client.get("/api/settings").json()["extension_token"]
    h = {"X-JobPilot-Token": token}
    qs = [{"label": "First Name*"}, {"label": "What is your notice period?"},
          {"label": "Total years of experience"}, {"label": "Gender"},
          {"label": "Are you willing to work night shifts?", "options": ["Yes", "No"]}]
    ans = client.post("/api/ext/answers/suggest", headers=h, json={"questions": qs}).json()["answers"]
    assert ans[0]["answer"] == "Priya" and ans[0]["source"] == "profile"
    assert ans[1]["answer"] == "30 days"
    assert float(ans[2]["answer"]) > 7
    assert ans[3]["answer"] == "" and ans[3]["source"] == "none"
    assert ans[4]["answer"] == ""
    client.post("/api/ext/answers/save", headers=h,
                json={"answers": [{"label": "Are you willing to work night shifts?", "answer": "no"},
                                  {"label": "First Name", "answer": "Priya"}]})
    saved = client.get("/api/answers").json()
    assert len(saved) == 1  # profile fact not duplicated
    ans = client.post("/api/ext/answers/suggest", headers=h,
                      json={"questions": [{"label": "Willing to work night shifts?", "options": ["Yes", "No"]}]}
                      ).json()["answers"]
    assert ans[0]["answer"] == "No" and ans[0]["source"] == "saved"


def test_settings_masks_secrets(client):
    client.put("/api/settings", json={"llm": {"api_key": "sk-ant-secret-1234"}})
    s = client.get("/api/settings").json()
    assert s["llm"]["api_key"] == "••••1234"
    client.put("/api/settings", json={"llm": s["llm"]})  # masked value round-trips unchanged
    from app.db import session_scope
    from app.services import settings_store

    with session_scope() as session:
        assert settings_store.get(session, "llm")["api_key"] == "sk-ant-secret-1234"
        client.put("/api/settings", json={"llm": {"api_key": "", "enabled": True}})


def test_delete_job_cascades_application(client):
    _setup_profile(client)
    job = _add_job(client, "Python Developer", JD_BACKEND)
    client.post("/api/applications/bulk", json={"job_ids": [job["id"]], "use_llm": False})
    resume_id = client.get("/api/applications").json()[0]["resume_id"]
    assert client.delete(f"/api/jobs/{job['id']}").json() == {"ok": True}
    assert client.get("/api/applications").json() == []
    r = client.get(f"/api/resumes/{resume_id}").json()
    assert r["job_id"] is None
    from app.db import session_scope

    with session_scope() as s:
        assert s.get(Application, 1) is None
    assert Path(store.resume_files_dir()).exists()
