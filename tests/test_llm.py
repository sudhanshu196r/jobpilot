"""The Claude integration, exercised against a mocked HTTP transport."""

import json

import anthropic
import httpx2 as httpx
import pytest

from app.services import profile_parser, tailor
from app.services.llm import LLM, LLMError

from .conftest import JD_BACKEND, SAMPLE_RESUME


def _llm(handler) -> LLM:
    client = anthropic.Anthropic(api_key="sk-test", max_retries=0,
                                 http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return LLM(api_key="sk-test", model="claude-opus-5-5", client=client)


def _message(payload: dict, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def test_request_shape_and_parsing():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta", "")
        return httpx.Response(200, json=_message({"body": "Dear Hiring Team, ..."}))

    out = _llm(handler).structured(system="s", prompt="p", schema=tailor._Letter)
    assert out.body.startswith("Dear Hiring Team")
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["beta"]
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in body


def test_refusal_and_errors_raise_llm_error():
    llm = _llm(lambda r: httpx.Response(200, json=_message({}, stop_reason="refusal")))
    with pytest.raises(LLMError, match="declined"):
        llm.structured(system="s", prompt="p", schema=tailor._Letter)
    llm = _llm(lambda r: httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "bad"}}))
    with pytest.raises(LLMError, match="invalid"):
        llm.structured(system="s", prompt="p", schema=tailor._Letter)


def test_tailor_falls_back_to_heuristic_when_api_fails():
    profile = profile_parser.parse_heuristic(SAMPLE_RESUME)
    llm = _llm(lambda r: httpx.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "boom"}}))
    result = tailor.tailor(profile, job_title="Backend Engineer", description=JD_BACKEND, llm=llm)
    assert result.method == "heuristic"
    assert any("AI tailoring unavailable" in n for n in result.notes)


def test_tailor_with_llm_end_to_end():
    profile = profile_parser.parse_heuristic(SAMPLE_RESUME)
    e0, e1 = profile.experience

    def handler(request):
        prompt = json.loads(request.content)["messages"][0]["content"]
        assert e0.bullets[0].id in prompt  # bullet ids are offered for citation
        return httpx.Response(200, json=_message({
            "headline": "Senior Backend Engineer | Python, Kafka",
            "summary": "Backend engineer with 9 years building payments systems on Python and Kafka.",
            "skills": ["Kafka", "Python", "PostgreSQL"],
            "experience": [
                {"source_id": e0.id, "bullets": [{"source_ids": [e0.bullets[0].id],
                                                 "text": "Built a Kafka payments event pipeline handling 2M events/day at 99.99% uptime"}]},
                {"source_id": e1.id, "bullets": [{"source_ids": [e1.bullets[1].id],
                                                 "text": "Cut PostgreSQL p95 latency from 800ms to 120ms through query optimisation"}]},
            ],
            "projects": [],
        }))

    result = tailor.tailor(profile, job_title="Backend Engineer", description=JD_BACKEND, llm=_llm(handler))
    assert result.method == "llm", result.notes
    assert result.doc.skills[:2] == ["Kafka", "Python"]
    assert result.doc.experience[0].bullets[0].startswith("Built a Kafka")
    assert result.doc.headline.startswith("Senior Backend Engineer")
