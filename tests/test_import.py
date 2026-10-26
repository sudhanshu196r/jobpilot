"""Resume upload -> profile: formats, merge, save/undo, report."""

import io
from pathlib import Path

import pytest

from app.schemas import Bullet, Experience, ProfileData
from app.services import profile_parser, render, tailor

from .conftest import SAMPLE_RESUME

FIXTURES = Path(__file__).parent / "fixtures"


def _rendered(kind: str, tmp_path: Path) -> tuple[str, bytes]:
    """A real PDF/DOCX resume, made with our own renderer (bullets lose their glyphs)."""
    doc = tailor.tailor_heuristic(profile_parser.parse_heuristic(SAMPLE_RESUME), None)
    doc.experience[0].bullets[0] += " and onboarded three new merchant verticals onto the shared event bus during the quarter"
    path = tmp_path / f"resume.{kind}"
    (render.render_pdf if kind == "pdf" else render.render_docx)(doc, path)
    return path.name, path.read_bytes()


@pytest.mark.parametrize("kind", ["pdf", "docx"])
def test_pdf_and_docx_round_trip(kind, tmp_path):
    name, content = _rendered(kind, tmp_path)
    p = profile_parser.parse_heuristic(profile_parser.extract_text(name, content))
    assert (p.contact.name, p.contact.email, p.contact.phone) == ("Priya Sharma", "priya.sharma@example.com", "+91 98765 43210")
    assert [(e.title, e.company, e.location) for e in p.experience] == [
        ("Senior Software Engineer", "Razorpay", "Bengaluru"), ("Software Engineer", "Infosys", "Pune")]
    first = p.experience[0]
    assert first.current and first.start == "2021-01" and len(first.bullets) == 4
    assert first.bullets[0].text.endswith("during the quarter")  # wrapped line re-joined
    assert len(p.experience[1].bullets) == 3 and p.experience[1].end == "2020-12"
    edu = p.education[0]
    assert (edu.degree, edu.institution, edu.grade) == ("B.Tech in Computer Science", "VIT Vellore", "8.6/10")
    assert "Kafka" in p.skills and p.languages == ["English", "Hindi", "Kannada"]


def test_layout_title_company_date_on_separate_lines():
    p = profile_parser.parse_heuristic((FIXTURES / "resume_style_b.txt").read_text())
    assert [(e.title, e.company, e.location, e.start, e.end) for e in p.experience] == [
        ("Data Analyst", "Flipkart", "Bengaluru", "2022-03", ""),
        ("Junior Analyst", "Tata Consultancy Services", "Pune", "2020-06", "2022-02")]
    assert p.skills == ["Python", "SQL", "R", "Power BI", "Tableau", "Excel"]
    assert p.education[0].grade == "78%"
    c = p.certifications[0]
    assert (c.name, c.issuer, c.year) == ("Google Data Analytics Professional Certificate", "Coursera", "2021")


def test_layout_everything_on_one_line():
    p = profile_parser.parse_heuristic((FIXTURES / "resume_style_c.txt").read_text())
    assert [(e.title, e.company) for e in p.experience] == [("Senior Product Manager", "Zomato"), ("Product Manager", "Paytm")]
    assert all(len(e.bullets) == 2 for e in p.experience)
    assert [(e.degree, e.institution, e.end) for e in p.education] == [("MBA", "IIM Ahmedabad", "2018"), ("B.Tech", "IIT Bombay", "2016")]
    assert "A/B Testing" in p.skills
    assert p.contact.phone == "+91-99887-76655" and p.contact.location == "Mumbai, India"


def test_merge_adds_without_overwriting():
    current = ProfileData(headline="Staff Engineer", skills=["Go"], experience=[
        Experience(title="Senior Software Engineer", company="Razorpay", start="2021-01", current=True,
                   bullets=[Bullet(text="My own edited bullet")])]).normalized()
    incoming = profile_parser.parse_heuristic(SAMPLE_RESUME)
    merged = profile_parser.merge_profiles(current, incoming)
    assert merged.headline == "Staff Engineer"  # kept
    assert merged.contact.email == "priya.sharma@example.com"  # filled
    assert merged.skills[0] == "Go" and "Kafka" in merged.skills
    assert [e.company for e in merged.experience] == ["Razorpay", "Infosys"]
    razorpay = merged.experience[0]
    assert razorpay.bullets[0].text == "My own edited bullet" and len(razorpay.bullets) == 5
    assert razorpay.bullets[0].id == current.experience[0].bullets[0].id  # ids survive
    again = profile_parser.merge_profiles(merged, incoming)  # idempotent
    assert again.model_dump(exclude={"preferences"}) == merged.model_dump(exclude={"preferences"})


def test_import_saves_reports_and_undoes(client, tmp_path):
    client.put("/api/profile", json={"preferences": {"notice_period_days": 30}})
    name, content = _rendered("pdf", tmp_path)
    r = client.post("/api/profile/import", files={"file": (name, io.BytesIO(content), "application/pdf")},
                    data={"use_llm": "false", "save": "true", "mode": "replace"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] and body["method"] == "heuristic"
    assert body["report"]["counts"]["experience"] == 2 and body["report"]["counts"]["achievements"] == 7
    assert body["report"]["missing"] == []
    saved = client.get("/api/profile").json()["profile"]
    assert saved["contact"]["name"] == "Priya Sharma"
    assert saved["preferences"]["notice_period_days"] == 30  # kept
    assert saved["preferences"]["target_titles"][0] == "Senior Backend Engineer"  # suggested
    assert saved["preferences"]["locations"] == ["Bengaluru"]
    # Undo = PUT the previous profile back.
    client.put("/api/profile", json=body["previous"])
    assert client.get("/api/profile").json()["profile"]["contact"]["name"] == ""


def test_import_merge_mode_and_report_missing(client):
    client.post("/api/profile/import", data={"text": SAMPLE_RESUME, "use_llm": "false", "save": "true"})
    style_b = (FIXTURES / "resume_style_b.txt").read_text()
    r = client.post("/api/profile/import", data={"text": style_b, "use_llm": "false", "save": "true", "mode": "merge"}).json()
    assert r["profile"]["contact"]["name"] == "Priya Sharma"  # not overwritten
    assert r["report"]["counts"]["experience"] == 4
    r = client.post("/api/profile/import", data={"text": "Jane Doe\njane@example.com\n\nSKILLS\nPython, SQL",
                                                 "use_llm": "false", "save": "false"}).json()
    assert {"Phone", "Experience", "Education"} <= set(r["report"]["missing"])
    assert r["saved"] is False


def test_import_rejects_old_doc_and_unknown_types(client):
    r = client.post("/api/profile/import", files={"file": ("cv.doc", io.BytesIO(b"\xd0\xcf\x11\xe0"), "application/msword")})
    assert r.status_code == 400 and ".docx" in r.json()["detail"]
    r = client.post("/api/profile/import", files={"file": ("cv.png", io.BytesIO(b"\x89PNG"), "image/png")})
    assert r.status_code == 400
