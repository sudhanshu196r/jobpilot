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

1. **Profile → drop your resume** (PDF, DOCX or TXT), or paste your Naukri/LinkedIn profile text. Contact details, roles, achievements, skills, education, projects, certifications and languages are filled in and saved. Your target titles and location are suggested from the resume. A report shows what was found and what's missing, and **Undo import** reverts it. Uploading another resume later adds new details without overwriting your edits (or choose *Replace*). Review each section and add *all* your achievements. Tailoring picks the best ones for each job, so more is better.
2. **Settings → AI (optional).** Paste an Anthropic API key to get Claude-quality parsing, tailoring, cover letters and screening answers. Without a key, a built-in engine does all of this deterministically.
3. **Settings → Company career pages.** Follow companies from the built-in list (75 verified employers, sorted by how many roles they have in India), or paste any careers URL. Then click **Fetch new jobs**.
4. **Settings → job-alert emails.** Connect your mailbox so LinkedIn, Naukri, Indeed and other alerts flow into the same feed (see below).
5. **Install the extension** (below) and browse Naukri and Indeed as usual. Jobs are captured and get a match badge. LinkedIn capture is opt-in.
6. **Jobs:** filter, select several, and click **Tailor resumes & queue**.
7. **Extension → Start applying.** It works through the queue one job at a time.
8. *(Optional)* **Settings → New-match email alerts** to get a digest whenever new jobs match you, and **Settings → AI assistant (MCP)** to manage your search from Claude.

## Apply from JobPilot

Click **Apply** on any job (in a list, the job panel or the Applications board):

1. **Tailored resume.** It's built for that job from your real experience.
2. **Questions.** For Greenhouse jobs, JobPilot shows the employer's *actual* application questions, pre-answered from your profile and saved answers, so you review and edit them in JobPilot. Legal and sensitive questions are only answered from your preferences, never guessed. Consent boxes are never ticked for you.
3. **Apply now.** The extension opens the application form directly, fills your reviewed answers (including searchable dropdowns), attaches the resume, and, if you tick *Submit for me*, submits once every required field is filled. It then closes the tab and JobPilot shows the application as applied.

*Submit for me* is only offered for company application forms (Greenhouse, Lever, Ashby, SmartRecruiters). On Naukri, Indeed, LinkedIn and Workday you press Submit yourself. Captchas, if a site shows one, are yours to solve.

The final submit always happens in your browser. Employers' systems only accept applications through their own forms, or with the employer's private API key. **Apply to N queued** on the Applications board works through several applications in a row.

**Dashboard:** strong matches and applications with week-over-week change, the application pipeline with conversion rates, 30-day activity, match-score spread, the skills most often missing from your better matches (click one to see those jobs), where jobs come from, top companies hiring for you, and follow-ups due (applied 7+ days ago with no reply, with a copyable follow-up note).

**Applications board:** drag cards between *To apply*, *Applied*, *Interviewing*, *Offer* and *Closed*, or switch to the table view. **Interview prep** (in each job) lists likely technical and behavioural questions, how to answer each from your real experience, gaps to prepare for, and questions to ask.

## Every job in one place

JobPilot collects recent openings from four kinds of channel into one deduplicated feed, ranked against your profile:

| Channel | How | Coverage |
|---|---|---|
| **Company career pages** | Their public job-board APIs, fetched directly | Greenhouse, Lever, Ashby, SmartRecruiters and **Workday** (used by most large employers). A built-in list of 75 companies was checked live, including Paytm, Meesho, CRED, Zeta, Groww, InMobi, Freshworks, Bosch, ServiceNow, NVIDIA, Salesforce, Okta, Databricks and LinkedIn's own careers site. Paste any other careers URL and JobPilot finds the board behind it. |
| **LinkedIn, Naukri, Indeed, Glassdoor, foundit, Instahyre, iimjobs, hirist, Wellfound, Cutshort** | The job-alert emails these sites send you, read over IMAP (read-only) | Every job you're alerted to, continuously, with no scraping. Create alerts for your roles on each site and connect your mailbox in Settings with an App Password. |
| **Pages you browse** | The browser extension | Naukri and Indeed listings and job pages. LinkedIn is opt-in and read-only. Opening a job also fills in the full description of a job that came from an alert email. |
| **Aggregators** | Adzuna (free key, covers many Indian boards) and Remotive (remote jobs) | Keyword searches |

Large employers list thousands of roles, so Workday and SmartRecruiters are searched with your target titles (or keywords you set) and capped per company. Greenhouse, Lever and Ashby boards are pulled in full. **Fetch new jobs** runs in the background with live progress, and it also runs automatically every few hours.

Jobs from alert emails start with a short description, so their scores are rougher until you open them once with the extension, which captures the full description.

## Fresh jobs, alerts and your AI assistant

**Only live, fresh postings.** Every job shows how old it is (*new*, *4d ago*; *stale* after 30 days) and you can filter by *Found today / last 3 / 7 / 30 days*. Ghost jobs are caught three ways:
- a Greenhouse, Lever or Ashby posting that disappears from its company's board feed is marked **closed** on the next fetch (and reopened if it comes back);
- the extension spots "no longer accepting applications" on Naukri and Indeed pages;
- **Still open?** (in a job or for a selection) re-checks the posting. A site that blocks the check is reported as *unknown*, never closed.

Closed jobs are hidden from matches, can't be queued, and are listed under *Closed*.

**New-match email alerts.** After each fetch (manual or the automatic one every few hours), JobPilot emails one digest of newly found, still-open jobs that score at least your threshold, and never the same job twice. It works with any mailbox over SMTP. For Gmail, use an [App Password](https://myaccount.google.com/apppasswords) with `smtp.gmail.com`, port 587. Turning alerts on doesn't email the jobs you already have.

**Use JobPilot from Claude (MCP).** JobPilot includes an [MCP](https://modelcontextprotocol.io) server, so Claude can search your matches, explain fits, tailor resumes, queue applications and update your tracker in conversation. Try "show my best new remote matches from this week", "tailor a resume for job 42", "queue the top 5" or "mark the Acme application as interview". **Settings → AI assistant (MCP)** shows the exact command for your computer:

```bash
# Claude Code
claude mcp add jobpilot -- "/path/to/jobpilot/.venv/bin/python" "/path/to/jobpilot/jobpilot_mcp.py"
```

```json
// Claude Desktop: Settings → Developer → Edit Config (claude_desktop_config.json), then restart Claude
{ "mcpServers": { "jobpilot": { "command": "/path/to/jobpilot/.venv/bin/python",
                               "args": ["/path/to/jobpilot/jobpilot_mcp.py"] } } }
```

Tools: `search_jobs`, `get_job`, `get_profile`, `tailor_resume`, `queue_applications`, `list_applications`, `update_application`, `list_resumes`, `fetch_new_jobs`, `check_job_open`. The server reads the same local database, so the web app doesn't need to be running. Submitting applications stays with you and the extension.

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
    liveness.py          posting age, board sync and "still open?" checks (ghost-job detection)
    alerts.py            new-match email digests over SMTP
    sources/             Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Remotive, Adzuna clients;
                         detect.py (careers URL -> board), directory.py (verified companies),
                         email_alerts.py (LinkedIn/Naukri/Indeed/... job-alert emails over IMAP)
    store.py             persistence, dedupe across sources, background resume preparation
  routers/api.py         UI API
  routers/ext.py         extension API (token-protected)
  mcp_server.py          MCP server for Claude and other assistants (run via jobpilot_mcp.py)
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
- Listings are captured only from pages you are viewing. There's no background crawling. For continuous coverage of these sites, JobPilot reads the job-alert emails they send you instead.
- **You review and submit every application.** Automatic Apply-clicking is off by default. Turn it on only if you're comfortable with it.
- Keep volume sensible. 20–40 well-matched applications a day gets more callbacks than spraying hundreds.

LinkedIn is the strictest about automation. JobPilot gets LinkedIn jobs from your LinkedIn job-alert emails. The extension's LinkedIn capture is **off by default**. When you turn it on, it only reads the job pages you open. It never clicks or fills anything on LinkedIn: you apply there yourself, and JobPilot tracks the application.

Site markup changes from time to time. If badges stop appearing on Naukri, Indeed or LinkedIn, the selectors in `extension/content/naukri.js` / `indeed.js` / `linkedin.js` need updating. Alert emails are parsed by their job links rather than their layout, so template changes rarely break them. Each lookup has several fallbacks to make this rare.

## Security

- The server binds to `127.0.0.1` only.
- Extension endpoints require a random token (rotate it in Settings).
- Every state-changing request needs a custom header. Browsers can't send one cross-origin without a CORS preflight, and preflights are granted only to extensions. So a website you visit can't drive your local JobPilot.
- API keys are masked in the UI and can instead come from the `ANTHROPIC_API_KEY` environment variable (see `.env.example`).

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q          # 69 tests
JOBPILOT_LIVE=1 .venv/bin/python -m pytest tests/test_live_sources.py   # optional: hits the real job-board APIs
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
| **Test mailbox** finds 0 jobs | Create job alerts on LinkedIn, Naukri and Indeed first, increase *Look back (days)*, and check the folder (Gmail tabs are all in `INBOX`) |
| A company you followed shows a fetch error | Its careers site moved. Remove it under *Advanced*, then paste its current careers URL into *Any other company* |
| Alert test email fails with a login error | Gmail and Outlook need an App Password, not your normal password |
| Claude doesn't show the JobPilot tools | Use the exact paths from Settings → AI assistant, then fully restart Claude Desktop |
| AI features failing | Settings → **Test connection**. Everything falls back to the built-in engine automatically |
