# JobPilot

A personal, local-first job search assistant. You keep one detailed profile. JobPilot:

1. **Builds a tailored, ATS-friendly resume for each job.** It writes a PDF and DOCX from your real experience, ordered and phrased for that job description.
2. **Finds and ranks jobs.** It pulls from public job APIs, and from Naukri and Indeed while you browse them with the extension. Every posting is scored against your profile.
3. **Applies in batches.** You select many jobs at once. A tailored resume is prepared for each one. The browser extension then opens each job, fills the form, attaches the right resume, and waits for you to review and submit.

Everything runs on your computer. Your data never leaves it, except for calls to the Claude API, which only happen if you add a key.

---

## Quick start

Requires **Python 3.10+** and **Google Chrome** (or any Chromium browser).

```bash
# macOS / Linux
./run.sh

# Windows
run.bat
```

Then open **http://127.0.0.1:8765**.

1. **Profile → Import from resume.** Upload your PDF/DOCX, or paste the text of your Naukri or LinkedIn profile. Review every section and click **Save profile**. Add *all* your achievements. Tailoring picks the best ones for each job, so more is better.
2. **Settings → AI (optional).** Paste an Anthropic API key to get Claude-quality parsing, tailoring, cover letters and screening answers. Without a key, a built-in engine does all of this deterministically.
3. **Settings → Job sources.** Add company boards (Greenhouse/Lever/Ashby), Remotive, and/or Adzuna. Adzuna has a free key and covers many Indian job boards. Click **Fetch new jobs**.
4. **Install the extension** (below) and browse Naukri and Indeed as usual. Jobs are captured and get a match badge.
5. **Jobs:** filter, select several, and click **Tailor resumes & queue**.
6. **Extension → Start applying.** It works through the queue one job at a time.

## Installing the browser extension

1. Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and select the `extension/` folder.
2. Click the JobPilot toolbar icon. Enter the server (`http://127.0.0.1:8765`) and the token from **Settings → Browser extension**, then click **Connect**.

What it does:

| Where | What happens |
|---|---|
| Naukri / Indeed search results | Each listing is captured and gets a **JobPilot score** badge |
| Naukri / Indeed job page | Full description captured. A panel shows the score, with buttons for **Tailor resume & queue** and **I applied** |
| During the queue | Opens each job, highlights (or clicks, if you enable it) **Apply**, fills form fields, attaches the tailored PDF, fills cover-letter boxes, and detects the "application submitted" message |
| Greenhouse, Lever, Ashby, Workday, Workable, SmartRecruiters, iCIMS, Indeed smart-apply | Generic form autofill |
| Any other page | Popup → **Autofill form** or **Save as job** |

Field colours: **green** means filled from your profile or saved answers. **Amber** means an AI draft that you should review. Unknown fields are left for you. When you press **Applied ✓ Next**, your answers are remembered, so next time they're automatic (see **Saved answers**).

## How the pieces fit

```
app/                     FastAPI backend + web UI (no build step)
  services/
    profile_parser.py    resume (PDF/DOCX/text) -> structured profile (Claude, or heuristic parser)
    skills.py            skill taxonomy (~200 skills, aliases, case-sensitive ambiguity handling)
    jd_analyzer.py       required vs nice-to-have skills, years of experience, remote
    matcher.py           0-100 match score: skills 45, text 20, title 15, experience 15, location 5
    tailor.py            tailored resume + anti-fabrication checks + cover letter
    render.py            ATS-friendly PDF (ReportLab) and DOCX
    answers.py           screening-question memory, profile facts, AI drafts
    sources/             Greenhouse, Lever, Ashby, Remotive, Adzuna clients
    store.py             persistence, dedupe across sources, background resume preparation
  routers/api.py         UI API
  routers/ext.py         extension API (token-protected)
extension/               Chrome MV3 extension (capture, badges, queue runner, autofill)
tests/                   pytest suite (services, API flow, mocked Claude integration)
```

### Truthfulness guarantees

The tailored resume may only **select, reorder and rephrase** what is in your profile:

- Every bullet the model writes must cite the profile bullet(s) it came from. Uncited bullets are dropped.
- A rewritten bullet may not contain any number that isn't in its source. If it does, the original wording is restored.
- Skills are limited to ones your profile actually evidences.
- The headline and summary are checked the same way.
- Every role stays on the resume. No gaps are silently introduced.
- Any repair is listed under **Safety checks** on the resume page.
- Skills a job wants that you don't have are shown as *genuine gaps*. They are never added.

The screening-question helper never guesses sensitive answers (gender, disability, criminal record and so on) or legal attestations (work authorisation, sponsorship). It uses only what you saved or set in your preferences.

### Match score

| Component | Weight | How |
|---|---|---|
| Skills | 45 | Weighted share of the JD's skills that you have. Required skills count 1, nice-to-have 0.5. Shrunk toward neutral when a JD lists very few skills |
| Text | 20 | TF-IDF similarity between your profile and the description |
| Title | 15 | Overlap with your target titles and recent titles (handles sr/jr/SDE/SWE…) |
| Experience | 15 | Your total years (overlapping roles counted once) vs. the stated range |
| Location | 5 | Remote preference and preferred cities |

Excluded companies and keywords (set in Preferences) force a score of 0.

## About the job platforms

Naukri and Indeed don't offer public APIs for job search or applying, and their terms forbid bots that scrape or submit applications on their own. JobPilot is built to respect that:

- It never stores your Naukri or Indeed password and never logs in for you. The extension works inside *your* browser session, at your pace, on pages you open.
- Listings are captured only from pages you are viewing. There's no background crawling.
- **You review and submit every application.** Automatic Apply-clicking is off by default. Turn it on only if you're comfortable with it.
- Keep volume sensible. 20–40 well-matched applications a day gets more callbacks than spraying hundreds.

LinkedIn is intentionally not automated. Use **Add job manually**, or the popup's **Save as job**, to bring LinkedIn postings in for scoring and tailoring.

Site markup changes from time to time. If badges stop appearing on Naukri or Indeed, the selectors in `extension/content/naukri.js` / `indeed.js` need updating. Each lookup has several fallbacks to make this rare.

## Security

- The server binds to `127.0.0.1` only.
- Extension endpoints require a random token (rotate it in Settings).
- Every state-changing request needs a custom header. Browsers can't send one cross-origin without a CORS preflight, and preflights are granted only to extensions. So a website you visit can't drive your local JobPilot.
- API keys are masked in the UI and can instead come from the `ANTHROPIC_API_KEY` environment variable (see `.env.example`).

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q          # 20 tests
.venv/bin/python -m app                # or: uvicorn app.main:app --reload --port 8765
```

Data lives in `./data` (SQLite database + generated resumes). Back it up, or delete it to start fresh.

## Troubleshooting

| Problem | Fix |
|---|---|
| Extension says "not connected" | Is JobPilot running? Re-copy the token from Settings (it changes if rotated) |
| "No text could be extracted" on import | The PDF is a scan. Paste the text instead |
| Resume upload not attached on a site | Some sites use custom upload widgets. Drag the PDF from the Applications page instead |
| Adzuna errors | Check the app ID and key. The free tier is rate limited, so lower the pages per query |
| AI features failing | Settings → **Test connection**. Everything falls back to the built-in engine automatically |
