// Naukri: capture listings + job pages, show match badges, assist applying.
// Naukri's class names change often, so every lookup has several fallbacks.
(() => {
  const { text, first, debounce, captureJobs, badge, jobPanel, runTask } = window.JP;

  const CARD_SELECTORS = ["div.srp-jobtuple-wrapper", "article.jobTuple", "div.cust-job-tuple", "[data-job-id]"];

  function jobIdFromUrl(url) {
    const m = String(url).match(/-(\d{9,})(?:[?#/]|$)/) || String(url).match(/jobId=(\d+)/);
    return m ? m[1] : null;
  }

  function parseCard(card) {
    const link = card.querySelector("a.title, a[class*='title']");
    if (!link) return null;
    const id = card.getAttribute("data-job-id") || jobIdFromUrl(link.href);
    if (!id) return null;
    const tags = [...card.querySelectorAll(".tags-gt li, ul.tags li, [class*='tags'] li")].map(text).filter(Boolean);
    const snippet = text(first(card, [".job-desc", "[class*='job-desc']", ".ellipsis.job-description"]));
    return {
      source: "naukri",
      external_id: id,
      url: link.href.split("?")[0],
      title: text(link) || link.getAttribute("title") || "",
      company: text(first(card, ["a.comp-name", "a[class*='comp-name']", "a.subTitle", "[class*='companyInfo'] a"])),
      location: text(first(card, [".locWdth", "[class*='loc-wrap'] span", ".loc span", "li.location span", "[class*='location']"])),
      experience_text: text(first(card, [".expwdth", "[class*='exp-wrap'] span", ".exp span", "li.experience span"])),
      salary: text(first(card, ["[class*='sal-wrap'] span", ".sal span", "li.salary span"])),
      description: [snippet, tags.length ? `Skills: ${tags.join(", ")}` : ""].filter(Boolean).join("\n"),
      posted_at: text(first(card, [".job-post-day", "[class*='job-post-day']", ".type br + span"])),
    };
  }

  const scored = new WeakSet();
  async function scanListings() {
    const cards = [];
    for (const sel of CARD_SELECTORS) {
      document.querySelectorAll(sel).forEach((c) => { if (!scored.has(c) && !cards.includes(c)) cards.push(c); });
      if (cards.length) break;
    }
    const parsed = cards.map((c) => [c, parseCard(c)]).filter(([, j]) => j && j.title);
    if (!parsed.length) return;
    parsed.forEach(([c]) => scored.add(c));
    const res = await captureJobs(parsed.map(([, j]) => j)).catch(() => null);
    if (!res) return;
    const byId = Object.fromEntries(res.results.map((r) => [r.external_id, r]));
    for (const [card, job] of parsed) {
      const r = byId[job.external_id];
      if (!r) continue;
      card.querySelector(".jobpilot-badge")?.remove();
      const anchor = card.querySelector("a.title, a[class*='title']");
      anchor?.parentElement?.insertAdjacentElement("afterend", badge(r.score, r.application_status));
    }
  }

  function parseJobPage() {
    const id = jobIdFromUrl(location.href);
    const title = text(first(document, ["h1[class*='jd-header-title']", "h1[class*='title']", ".jd-header h1", "h1"]));
    if (!id || !title) return null;
    const skills = [...document.querySelectorAll("[class*='key-skill'] a, [class*='key-skill'] span.chip, [class*='chip'][class*='key']")].map(text).filter(Boolean);
    const desc = text(first(document, ["[class*='dang-inner-html']", "section[class*='job-desc']", "[class*='JDC__dang']", ".job-desc", "[class*='jd-desc']"]));
    const external = !!first(document, ["#company-site-button", "button[class*='company-site']"]);
    return {
      source: "naukri",
      external_id: id,
      url: location.href.split("?")[0],
      title,
      company: text(first(document, ["[class*='jd-header-comp-name'] a", "[class*='comp-name'] a", "a[class*='comp-name']"])),
      location: [...document.querySelectorAll("[class*='jhc__location'] a, [class*='location'] a")].map(text).filter(Boolean).slice(0, 4).join(", ")
        || text(first(document, ["[class*='jhc__location']", "[class*='location']"])),
      experience_text: text(first(document, ["[class*='jhc__exp']", "[class*='exp'] span"])),
      salary: text(first(document, ["[class*='jhc__salary']", "[class*='salary'] span"])),
      description: [desc, skills.length ? `Key skills: ${skills.join(", ")}` : ""].filter(Boolean).join("\n\n"),
      apply_type: external ? "external" : "easy",
    };
  }

  function findApplyButton() {
    const byId = document.querySelector("#apply-button, button[class*='apply-button']:not([class*='company'])");
    if (byId) return byId;
    return [...document.querySelectorAll("button")].find((b) => /^\s*apply\s*$/i.test(text(b))) || null;
  }

  async function init() {
    const task = await runTask({ findApplyButton });
    const job = parseJobPage();
    if (job) {
      const res = await captureJobs([job]).catch(() => null);
      if (!task) jobPanel(res, job);
    }
    scanListings();
    // Naukri is a SPA with infinite scroll: rescan when the DOM changes.
    const rescan = debounce(() => scanListings(), 1200);
    new MutationObserver(rescan).observe(document.body, { childList: true, subtree: true });
    let lastUrl = location.href;
    setInterval(async () => {
      if (location.href === lastUrl) return;
      lastUrl = location.href;
      const j = parseJobPage();
      if (j) jobPanel(await captureJobs([j]).catch(() => null), j);
    }, 1500);
  }
  init();
})();
