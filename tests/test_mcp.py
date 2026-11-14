"""End-to-end: launch jobpilot_mcp.py over stdio, as Claude Desktop would, and call its tools."""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from app.config import config

from .conftest import JD_BACKEND, JD_UNRELATED, SAMPLE_RESUME

ROOT = Path(__file__).resolve().parent.parent


def _data(result):
    assert not result.is_error, result.content
    if result.structured_content is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


async def _session_calls():
    params = StdioServerParameters(
        command=sys.executable, args=[str(ROOT / "jobpilot_mcp.py")], cwd="/",  # any cwd must work
        env={**os.environ, "JOBPILOT_DATA_DIR": str(config.data_dir), "JOBPILOT_DISABLE_SCHEDULER": "1"},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = {t.name for t in (await session.list_tools()).tools}
        jobs = _data(await session.call_tool("search_jobs", {"min_score": 0}))
        backend = next(j for j in jobs if j["title"] == "Senior Backend Engineer")
        detail = _data(await session.call_tool("get_job", {"job_id": backend["job_id"]}))
        profile = _data(await session.call_tool("get_profile", {}))
        resume = _data(await session.call_tool("tailor_resume", {"job_id": backend["job_id"], "use_ai": False}))
        queued = _data(await session.call_tool("queue_applications", {"job_ids": [backend["job_id"]], "use_ai": False}))
        apps = _data(await session.call_tool("list_applications", {"status": "queued"}))
        upd = _data(await session.call_tool("update_application",
                                            {"application_id": apps[0]["application_id"], "status": "interview",
                                             "note": "Recruiter call Monday"}))
        bad = await session.call_tool("get_job", {"job_id": 999999})
        return tools, jobs, backend, detail, profile, resume, queued, apps, upd, bad


def test_mcp_server_end_to_end(client):
    draft = client.post("/api/profile/import", data={"text": SAMPLE_RESUME, "use_llm": "false", "save": "true"})
    assert draft.status_code == 200
    for title, desc in (("Senior Backend Engineer", JD_BACKEND), ("Senior Graphic Designer", JD_UNRELATED)):
        client.post("/api/jobs", json={"title": title, "company": "Acme", "description": desc,
                                       "url": f"https://example.com/{title.replace(' ', '-')}"})

    tools, jobs, backend, detail, profile, resume, queued, apps, upd, bad = asyncio.run(_session_calls())

    assert {"search_jobs", "get_job", "get_profile", "tailor_resume", "queue_applications", "list_applications",
            "update_application", "fetch_new_jobs", "check_job_open", "list_resumes"} <= tools
    assert jobs[0]["title"] == "Senior Backend Engineer" and jobs[0]["score"] > jobs[-1]["score"]
    assert "Kafka" in backend["matched_skills"] and backend["closed"] is False
    assert "payments platform" in detail["description"] and detail["application"] is None
    assert profile["name"] == "Priya Sharma" and profile["total_years"] > 5
    assert Path(resume["pdf_path"]).exists() and resume["coverage"] > 0.5
    assert queued["queued"][0]["status"] == "queued"
    assert apps[0]["title"] == "Senior Backend Engineer"
    assert upd["status"] == "interview"
    assert bad.is_error
