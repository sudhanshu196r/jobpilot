import os
import tempfile

# Must happen before the app (and its frozen config) is imported.
_tmp = tempfile.mkdtemp(prefix="jobpilot-test-")
os.environ["JOBPILOT_DATA_DIR"] = _tmp
os.environ["JOBPILOT_DISABLE_SCHEDULER"] = "1"
os.environ["ANTHROPIC_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app.db import get_engine  # noqa: E402
from app.main import app  # noqa: E402

SAMPLE_RESUME = """PRIYA SHARMA
Senior Backend Engineer
Bengaluru, Karnataka | +91 98765 43210 | priya.sharma@example.com | linkedin.com/in/priyasharma | github.com/priyas

SUMMARY
Backend engineer who builds reliable, high-throughput services for fintech products.

SKILLS
Python, Django, FastAPI, PostgreSQL, Redis, Kafka, AWS, Docker, Kubernetes, REST APIs, Tally ERP 9

EXPERIENCE
Senior Software Engineer | Razorpay | Bengaluru
Jan 2021 - Present
• Designed a Kafka-based payments event pipeline processing 2M events/day with 99.99% uptime
• Led migration of 14 Django services to Kubernetes on AWS EKS, cutting infra cost by 30%
• Mentored 4 junior engineers and ran weekly design reviews
• Built internal admin dashboards in React

Software Engineer | Infosys | Pune
Jul 2017 - Dec 2020
• Developed REST APIs in Python and Flask for a banking client serving 1M users
• Optimised PostgreSQL queries, reducing p95 latency from 800ms to 120ms
• Wrote unit tests with pytest and set up Jenkins CI/CD pipelines

PROJECTS
OpenLedger | github.com/priyas/openledger
• Open-source double-entry accounting library in Python with 500+ GitHub stars

EDUCATION
B.Tech in Computer Science, VIT Vellore, 2013 - 2017, CGPA: 8.6/10

CERTIFICATIONS
AWS Certified Solutions Architect – Associate

LANGUAGES
English, Hindi, Kannada
"""

JD_BACKEND = """We are hiring a Senior Backend Engineer (Python) to build our payments platform.

Requirements:
- 4-7 years of experience in backend development
- Strong Python and Django or FastAPI
- Experience with Kafka, PostgreSQL and Redis
- AWS, Docker and Kubernetes in production
- Experience designing REST APIs and microservices

Nice to have:
- Go or Java
- Terraform
"""

JD_UNRELATED = """Looking for a Senior Graphic Designer with 6+ years of experience.
Requirements:
- Expert in Photoshop, Illustrator and Figma
- Strong portfolio in branding and print design
"""


@pytest.fixture()
def client():
    engine = get_engine()
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with TestClient(app, headers={"X-JobPilot-Client": "test"}) as c:
        yield c
