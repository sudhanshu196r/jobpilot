// Indeed: capture search results + job details, badges, apply assistance.
// "Easily apply" continues on smartapply.indeed.com, handled by ats.js.
(() => {
  const { text, first, debounce, captureJobs, badge, jobPanel, runTask } = window.JP;

  function parseCard(card) {
    const link = card.querySelector("a[data-jk], a.jcs-JobTitle, h2.jobTitle a");
    const jk = link?.getAttribute("data-jk") || card.getAttribute("data-jk") || new URL(link?.href || location.href).searchParams.get("jk");
    if (!jk) return null;
    const easy = /easily apply/i.test(text(card)) || !!card.querySelector("[data-testid='indeedApply'], .iaLabel");
    return {
      source: "indeed",
      external_id: jk,
      url: `${location.origin}/viewjob?jk=${jk}`,
      title: link?.querySelector("span[title]")?.getAttribute("title") || text(link) || "",
      company: text(first(card, ["[data-testid='company-name']", ".companyName", "span.css-1x7z1ps"])),
      location: text(first(card, ["[data-testid='text-location']", ".companyLocation"])),
      salary: text(first(card, ["[data-testid='attribute_snippet_testid']", ".salary-snippet-container", ".estimated-salary"])),
      description: text(first(card, ["[data-testid='jobsnippet_footer']", ".job-snippet", "[class*='snippet']"])),
      posted_at: text(first(card, ["[data-testid='myJobsStateDate']", "span.date"])),
      apply_type: easy ? "easy" : "unknown",
    };
  }

  const seen = new WeakSet();
  async function scanListings() {
    const cards = [...document.querySelectorAll("div.job_seen_beacon, li div.cardOutline, td.resultContent")]
      .map((c) => c.closest("li") || c).filter((c) => !seen.has(c));
    const parsed = cards.map((c) => [c, parseCard(c)]).filter(([, j]) => j && j.title);
    if (!parsed.length) return;
    parsed.forEach(([c]) => seen.add(c));
    const res = await captureJobs(parsed.map(([, j]) => j)).catch(() => null);
    if (!res) return;
    const byId = Object.fromEntries(res.results.map((r) => [r.external_id, r]));
    for (const [card, job] of parsed) {
      const r = byId[job.external_id];
      if (!r) continue;
      card.querySelector(".jobpilot-badge")?.remove();
      const title = card.querySelector("h2.jobTitle, h2");
      title?.insertAdjacentElement("afterend", badge(r.score, r.application_status));
    }
  }

  function currentJk() {
    const p = new URL(location.href).searchParams;
    return p.get("jk") || p.get("vjk");
  }

  function parseDetail() {
    const jk = currentJk();
    const descEl = document.querySelector("#jobDescriptionText");
    if (!jk || !descEl) return null;
    const title = text(first(document, ["h1[data-testid='jobsearch-JobInfoHeader-title']", ".jobsearch-JobInfoHeader-title", "h2[data-testid='jobsearch-JobInfoHeader-title']", "h1"]))
      .replace(/\s*-\s*job post$/i, "");
    return {
      source: "indeed",
      external_id: jk,
      url: `${location.origin}/viewjob?jk=${jk}`,
      title,
      company: text(first(document, ["[data-testid='inlineHeader-companyName']", "[data-company-name]", ".jobsearch-CompanyInfoContainer a"])),
      location: text(first(document, ["[data-testid='inlineHeader-companyLocation']", "[data-testid='job-location']", ".jobsearch-JobInfoHeader-subtitle div:last-child"])),
      salary: text(first(document, ["#salaryInfoAndJobType span", "[data-testid='jobsearch-OtherJobDetailsContainer'] span"])),
      description: (descEl.innerText || "").trim(),
      apply_type: document.querySelector("#indeedApplyButton, [id*='indeedApplyButton']") ? "easy" : "external",
    };
  }

  function findApplyButton() {
    return document.querySelector("#indeedApplyButton, button[id*='indeedApplyButton'], [data-testid='indeedApplyButton']")
      || [...document.querySelectorAll("button, a")].find((b) => /^(apply now|apply on company site)$/i.test(text(b))) || null;
  }

  let lastJk = null;
  async function captureDetail(showPanel) {
    const job = parseDetail();
    if (!job || job.external_id === lastJk) return;
    lastJk = job.external_id;
    const res = await captureJobs([job]).catch(() => null);
    if (showPanel) jobPanel(res, job);
  }

  async function init() {
    const task = await runTask({ findApplyButton });
    await captureDetail(!task);
    scanListings();
    const onChange = debounce(() => { scanListings(); captureDetail(!task); }, 1000);
    new MutationObserver(onChange).observe(document.body, { childList: true, subtree: true });
  }
  init();
})();
