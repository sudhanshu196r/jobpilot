from datetime import date

import httpx

from app.schemas import Bullet, Experience, ProfileData
from app.services import jd_analyzer, profile_parser, profile_utils, skills, tailor
from app.services.sources import AshbySource, GreenhouseSource, LeverSource, build_sources
from app.services.text import html_to_text

from .conftest import JD_BACKEND, SAMPLE_RESUME


def test_skill_extraction_handles_ambiguous_names():
    found = skills.extract("Go and C++ devs. Go to market fast. Rest of the team uses REST APIs and C#. Please express interest.")
    assert "Go" in found and "C++" in found and "C#" in found and "REST APIs" in found
    assert "Express" not in found
    assert skills.extract("go to the office") == []


def test_parse_dates_and_total_years():
    assert profile_utils.parse_month("Mar 2021") == (2021, 3)
    assert profile_utils.parse_month("03/2021") == (2021, 3)
    assert profile_utils.parse_month("2021-03") == (2021, 3)
    p = ProfileData(experience=[
        Experience(start="2018-01", end="2019-12"),
        Experience(start="2019-06", end="2020-12"),  # overlaps, counted once
    ])
    assert profile_utils.total_years(p, today=date(2024, 1, 1)) == 3.0


def test_heuristic_resume_parse():
    p = profile_parser.parse_heuristic(SAMPLE_RESUME)
    assert p.contact.email == "priya.sharma@example.com"
    assert p.contact.name == "Priya Sharma"
    assert "linkedin.com/in/priyasharma" in p.contact.linkedin
    assert len(p.experience) == 2
    first = p.experience[0]
    assert first.company == "Razorpay" and first.current and first.start == "2021-01"
    assert len(first.bullets) == 4
    assert p.experience[1].end == "2020-12"
    assert "Kafka" in p.skills and "Tally ERP 9" in p.skills
    assert p.education and "VIT" in p.education[0].institution
    assert p.education[0].grade.startswith("8.6")
    assert p.projects and p.projects[0].name == "OpenLedger"
    assert p.languages == ["English", "Hindi", "Kannada"]


def test_jd_analysis_weights_and_years():
    a = jd_analyzer.analyze("Senior Backend Engineer (Python)", JD_BACKEND)
    assert a.min_years == 4 and a.max_years == 7
    assert a.skills["Kafka"] == 1.0
    assert a.skills["Terraform"] == 0.5
    assert a.skills["Go"] == 0.5


def test_heuristic_tailoring_is_relevant_and_truthful():
    profile = profile_parser.parse_heuristic(SAMPLE_RESUME)
    result = tailor.tailor(profile, job_title="Senior Backend Engineer", description=JD_BACKEND)
    doc = result.doc
    all_source = {b.text for e in profile.experience for b in e.bullets}
    assert all(b in all_source for e in doc.experience for b in e.bullets)
    assert doc.experience[0].bullets[0].startswith(("Designed a Kafka", "Led migration"))
    assert doc.skills[0] in {"Python", "Kafka", "Django", "FastAPI"}
    assert "Terraform" not in doc.skills  # not in the profile
    assert "Terraform" in result.report.missing_entirely
    assert result.report.coverage > 0.7


def test_llm_validation_strips_fabrications():
    profile = profile_parser.parse_heuristic(SAMPLE_RESUME)
    analysis = jd_analyzer.analyze("Senior Backend Engineer", JD_BACKEND)
    exp0, exp1 = profile.experience
    out = tailor._LLMTailored(
        headline="Senior Backend Engineer | Python, Terraform",
        summary="Engineer with 6 years building payments systems.",
        skills=["Python", "Terraform", "kafka"],
        experience=[
            tailor._LLMSection(source_id=exp0.id, bullets=[
                tailor._LLMBullet(source_ids=[exp0.bullets[0].id], text="Architected Kafka payments pipeline handling 5M events/day"),
                tailor._LLMBullet(source_ids=["nope"], text="Invented a new database"),
                tailor._LLMBullet(source_ids=[exp0.bullets[1].id], text="Migrated 14 Django services to Kubernetes on AWS, reducing cost 30%"),
            ]),
        ],
        projects=[],
    )
    doc, notes = tailor._validate(out, profile, analysis, profile_utils.profile_skills(profile))
    bullets = doc.experience[0].bullets
    assert bullets[0] == exp0.bullets[0].text  # invented "5M" -> original restored
    assert "Invented a new database" not in bullets
    assert bullets[1].startswith("Migrated 14 Django")
    assert doc.skills == ["Python", "Kafka"]
    assert "Terraform" not in doc.headline
    assert len(doc.experience) == 2 and doc.experience[1].bullets  # omitted role filled in
    assert any("Terraform" in n for n in notes)


def test_profile_normalize_keeps_ids_and_dedupes():
    p = ProfileData(skills=["Python", "python", " SQL "], experience=[
        Experience(id="x1", bullets=[Bullet(id="b1", text="• Did a thing"), Bullet(id="b1", text="Another"),
                                     Bullet(text="   ")])])
    n = p.normalized()
    assert n.skills == ["Python", "SQL"]
    assert n.experience[0].id == "x1"
    assert [b.text for b in n.experience[0].bullets] == ["Did a thing", "Another"]
    assert n.experience[0].bullets[0].id == "b1" and n.experience[0].bullets[1].id != "b1"


def test_html_to_text():
    assert html_to_text("&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;&lt;ul&gt;&lt;li&gt;One&lt;/li&gt;&lt;/ul&gt;") == "Hello & welcome\n\n• One"


def _mock_client(routes: dict[str, object]) -> httpx.Client:
    def handler(request: httpx.Request):
        for prefix, payload in routes.items():
            if str(request.url).startswith(prefix):
                return httpx.Response(200, json=payload)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sources_parse_public_apis():
    client = _mock_client({
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs": {"jobs": [{
            "id": 1, "title": "Backend Engineer", "absolute_url": "https://x/1", "location": {"name": "Remote - India"},
            "content": "&lt;p&gt;Python &amp;amp; Kafka&lt;/p&gt;", "company_name": "Acme", "updated_at": "2024-01-01"}]},
        "https://api.lever.co/v0/postings/beta": [{
            "id": "abc", "text": "Data Engineer", "hostedUrl": "https://l/abc", "categories": {"location": "Pune"},
            "descriptionPlain": "Spark and Airflow", "lists": [{"text": "Requirements", "content": "<li>SQL</li>"}],
            "workplaceType": "onsite", "createdAt": 1700000000000}],
        "https://api.ashbyhq.com/posting-api/job-board/gamma": {"jobs": [{
            "id": "z", "title": "SRE", "location": "Bengaluru", "isRemote": False, "jobUrl": "https://a/z",
            "descriptionPlain": "Kubernetes", "publishedAt": "2024-02-02"}]},
    })
    gh = GreenhouseSource("acme", client).fetch()[0]
    assert gh.company == "Acme" and gh.remote is True and gh.description == "Python & Kafka"
    lv = LeverSource("beta", client).fetch()[0]
    assert lv.external_id == "beta:abc" and "Requirements:\n• SQL" in lv.description and lv.remote is False
    ab = AshbySource("gamma", client).fetch()[0]
    assert ab.title == "SRE" and ab.url == "https://a/z"


def test_build_sources_reports_missing_adzuna_keys():
    sources, errors = build_sources({"greenhouse": ["acme"], "adzuna": {"enabled": True, "queries": ["python"]}})
    assert len(sources) == 1 and errors and "app_id" in errors[0]
