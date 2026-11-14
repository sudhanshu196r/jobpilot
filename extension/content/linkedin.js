// LinkedIn: opt-in capture of the job listings and job pages you view.
// Off unless enabled in the popup. Never clicks or fills anything on LinkedIn:
// you apply there yourself; JobPilot only records and scores what you look at.
(() => {
  const { text, first, debounce, captureJobs, badge, jobPanel, runTask, send, isClosedText } = window.JP;

  const onJobs = () => location.pathname.startsWith("/jobs");

  function jobIdFromUrl(href = location.href) {
    const u = new URL(href, location.origin);
    return u.searchParams.get("currentJobId") || (u.pathname.match(/\/jobs\/view\/(?:[\w-]*?-)?(\d{6,})/) || [])[1] || null;
  }

  // LinkedIn repeats titles for screen readers; prefer the single visible copy.
  const titleOf = (el) => el && (el.getAttribute("aria-label") || text(el.querySelector("strong, span[aria-hidden='true']")) || text(el));

  function parseCard(card) {
    const id = card.getAttribute("data-occludable-job-id") || card.getAttribute("data-job-id")
      || jobIdFromUrl(card.querySelector("a[href*='/jobs/view/']")?.href || "");
    const link = card.querySelector("a.job-card-list__title--link, a.job-card-container__link, a[href*='/jobs/view/']");
    const title = titleOf(link);
    if (!id || !title) return null;
    return {
      source: "linkedin",
      external_id: String(id),
      url: `https://www.linkedin.com/jobs/view/${id}/`,
      title: title.replace(/\s+with verification$/i, "").slice(0, 200),
      company: text(first(card, [".artdeco-entity-lockup__subtitle", ".job-card-container__primary-description", ".job-card-container__company-name"])),
      location: text(first(card, [".artdeco-entity-lockup__caption", ".job-card-container__metadata-wrapper li", ".job-card-container__metadata-item"])),
      apply_type: /easy apply/i.test(text(card)) ? "easy" : "unknown",
    };
  }

  const seen = new WeakSet();
  async function scanListings() {
    const cards = [...document.querySelectorAll("li[data-occludable-job-id], div.job-card-container[data-job-id]")]
      .filter((c) => !seen.has(c) && text(c));
    const parsed = cards.map((c) => [c, parseCard(c)]).filter(([, j]) => j);
    if (!parsed.length) return;
    parsed.forEach(([c]) => seen.add(c));
    const res = await captureJobs(parsed.map(([, j]) => j)).catch(() => null);
    if (!res) return;
    const byId = Object.fromEntries(res.results.map((r) => [r.external_id, r]));
    for (const [card, job] of parsed) {
      const r = byId[job.external_id];
      if (!r) continue;
      card.querySelector(".jobpilot-badge")?.remove();
      const where = card.querySelector(".artdeco-entity-lockup__title, .job-card-list__title--link, a[href*='/jobs/view/']");
      where?.insertAdjacentElement("afterend", badge(r.score, r.application_status, r.closed));
    }
  }

  function parseDetail() {
    const id = jobIdFromUrl();
    const desc = first(document, ["#job-details", ".jobs-description__content", ".jobs-box__html-content", ".jobs-description-content__text"]);
    const titleEl = first(document, [".job-details-jobs-unified-top-card__job-title h1", ".job-details-jobs-unified-top-card__job-title",
      ".jobs-unified-top-card__job-title", ".top-card-layout__title", "h1"]);
    if (!id || !desc || !titleEl) return null;
    const top = titleEl.closest(".job-details-jobs-unified-top-card__container--two-pane, .jobs-unified-top-card, .job-view-layout, main") || document.body;
    const meta = text(first(top, [".job-details-jobs-unified-top-card__primary-description-container", ".job-details-jobs-unified-top-card__tertiary-description-container",
      ".jobs-unified-top-card__primary-description"]));
    return {
      source: "linkedin",
      external_id: String(id),
      url: `https://www.linkedin.com/jobs/view/${id}/`,
      title: text(titleEl).slice(0, 200),
      company: text(first(top, [".job-details-jobs-unified-top-card__company-name a", ".job-details-jobs-unified-top-card__company-name",
        ".jobs-unified-top-card__company-name", ".topcard__org-name-link"])),
      location: (meta.split("·")[0] || "").trim().slice(0, 120),
      posted_at: (meta.match(/(\d+\s+\w+\s+ago|just now|today)/i) || [""])[0],
      description: (desc.innerText || "").trim(),
      apply_type: /easy apply/i.test(text(first(top, [".jobs-apply-button", ".jobs-s-apply"]))) ? "easy" : "external",
      closed: isClosedText(text(top)),
    };
  }

  let lastDetail = null;
  async function captureDetail(showPanel) {
    const job = parseDetail();
    if (!job || job.external_id === lastDetail) return;
    lastDetail = job.external_id;
    const res = await captureJobs([job]).catch(() => null);
    if (showPanel) jobPanel(res, job);
  }

  async function init() {
    const cfg = await send("config").catch(() => null);
    if (!cfg?.captureLinkedIn) return; // opt-in only
    // Queue tasks still get the panel (mark applied / skip), but nothing is automated here.
    const task = await runTask({ manualOnly: true });
    const tick = debounce(() => { if (onJobs()) { scanListings(); captureDetail(!task); } }, 1500);
    tick();
    new MutationObserver(tick).observe(document.body, { childList: true, subtree: true });
  }
  init();
})();
