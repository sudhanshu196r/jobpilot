"""Live checks against the real job-board APIs. Skipped unless JOBPILOT_LIVE=1.

Run occasionally to catch API changes:  JOBPILOT_LIVE=1 pytest tests/test_live_sources.py
"""

import os

import pytest

from app.services.sources import AshbySource, GreenhouseSource, LeverSource, SmartRecruitersSource, WorkdaySource
from app.services.sources.detect import detect

pytestmark = pytest.mark.skipif(os.environ.get("JOBPILOT_LIVE") != "1", reason="set JOBPILOT_LIVE=1 to hit real APIs")


@pytest.mark.parametrize("src", [
    lambda: GreenhouseSource("stripe"), lambda: LeverSource("paytm"), lambda: AshbySource("ramp"),
    lambda: SmartRecruitersSource("ServiceNow", ["engineer"], "in", 3),
    lambda: WorkdaySource("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite", ["engineer"], 3),
], ids=["greenhouse", "lever", "ashby", "smartrecruiters", "workday"])
def test_live_source(src):
    jobs = src().fetch()
    assert jobs, "no jobs returned"
    j = jobs[0]
    assert j.title and j.url.startswith("https://") and len(j.description) > 100


def test_live_detect():
    assert detect("https://jobs.lever.co/paytm")["jobs"] > 0
