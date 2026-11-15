// JobPilot web UI — no build step, no dependencies.
// All dynamic text goes through esc(); job descriptions come from third parties.

// Must equal __version__ in app/__init__.py (checked by a test).
const APP_VERSION = "1.4.0";
import { barChart, columnChart, sparkline } from "./charts.js?v=1.4.0";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = $("#view");

async function api(path, { method = "GET", body, form } = {}) {
  const headers = { "X-JobPilot-Client": "web" };
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(path, { method, headers, body: payload });
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const data = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    let msg = data?.detail ?? data;
    if (Array.isArray(msg)) msg = msg.map((d) => `${d.loc?.slice(1).join(".")}: ${d.msg}`).join("; ");
    throw new Error(typeof msg === "string" ? msg : `Request failed (${res.status})`);
  }
  return data;
}

function toast(msg, error = false) {
  const t = document.createElement("div");
  t.className = "toast" + (error ? " error" : "");
  t.textContent = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), error ? 7000 : 3500);
}

async function busy(btn, fn) {
  const label = btn?.innerHTML;
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> ${label}`; }
  try { return await fn(); }
  catch (e) { toast(e.message, true); }
  finally { if (btn && btn.isConnected) { btn.disabled = false; btn.innerHTML = label; } }
}

const fmtDate = (s) => (s ? new Date(s).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "");
const scoreColor = (v) => (v >= 70 ? "var(--good)" : v >= 45 ? "var(--warn)" : "var(--bad)");
const scoreBadge = (v) => v == null ? `<div class="score" style="--v:0;--c:var(--line)"><span>–</span></div>`
  : `<div class="score" style="--v:${v};--c:${scoreColor(v)}" title="Match score"><span>${Math.round(v)}</span></div>`;
const STATUS_TONE = { preparing: "accent", queued: "accent", in_progress: "warn", applied: "good", interview: "good",
  offer: "good", skipped: "", failed: "bad", rejected: "bad" };
const statusPill = (s) => `<span class="pill ${STATUS_TONE[s] ?? ""}">${esc(s.replace("_", " "))}</span>`;
const APP_STATUSES = ["preparing", "queued", "in_progress", "applied", "skipped", "failed", "interview", "offer", "rejected"];

// --------------------------------------------------------------------------- two-way binding helpers

function getPath(obj, path) { return path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj); }
function setPath(obj, path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  const target = keys.reduce((o, k) => o[k], obj);
  target[last] = value;
}
function bindInputs(container, root) {
  container.addEventListener("input", (e) => {
    const el = e.target.closest("[data-bind]");
    if (!el) return;
    let v = el.type === "checkbox" ? el.checked : el.value;
    if (el.dataset.type === "number") v = el.value === "" ? null : Number(el.value);
    if (el.dataset.type === "tristate") v = el.value === "" ? null : el.value === "true";
    setPath(root(), el.dataset.bind, v);
  });
  container.addEventListener("change", (e) => {
    if (e.target.matches("select[data-bind]")) e.target.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
const inp = (label, path, obj, { type = "text", placeholder = "" } = {}) => {
  const v = getPath(obj, path) ?? "";
  return `<label class="field">${esc(label)}<input type="${type}" data-bind="${path}" ${type === "number" ? 'data-type="number"' : ""} value="${esc(v)}" placeholder="${esc(placeholder)}"></label>`;
};
const area = (label, path, obj, rows = 3) =>
  `<label class="field">${esc(label)}<textarea rows="${rows}" data-bind="${path}">${esc(getPath(obj, path) ?? "")}</textarea></label>`;

function tagInput(path, obj, placeholder = "Type and press Enter") {
  const list = getPath(obj, path) || [];
  return `<div class="tags" data-tags="${path}">${list.map((t, i) =>
    `<span class="tag">${esc(t)}<button type="button" data-tag-del="${i}" aria-label="Remove ${esc(t)}">×</button></span>`).join("")}
    <input type="text" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}"></div>`;
}
function bindTags(container, root, rerender) {
  container.addEventListener("keydown", (e) => {
    const box = e.target.closest("[data-tags]");
    if (!box || e.target.tagName !== "INPUT") return;
    const list = getPath(root(), box.dataset.tags);
    if ((e.key === "Enter" || e.key === ",") && e.target.value.trim()) {
      e.preventDefault();
      for (const part of e.target.value.split(",")) {
        const v = part.trim();
        if (v && !list.some((x) => x.toLowerCase() === v.toLowerCase())) list.push(v);
      }
      e.target.value = "";
      rerender(box.dataset.tags);
    } else if (e.key === "Backspace" && !e.target.value && list.length) {
      list.pop();
      rerender(box.dataset.tags);
    }
  });
  container.addEventListener("click", (e) => {
    const del = e.target.closest("[data-tag-del]");
    if (!del) return;
    const box = del.closest("[data-tags]");
    getPath(root(), box.dataset.tags).splice(Number(del.dataset.tagDel), 1);
    rerender(box.dataset.tags);
  });
}
function refreshTags(container, root, path) {
  const box = $(`[data-tags="${path}"]`, container);
  if (!box) return;
  box.outerHTML = tagInput(path, root());
  $(`[data-tags="${path}"] input`, container)?.focus();
}

// --------------------------------------------------------------------------- router

const routes = {};
let teardown = null;
async function router() {
  if (teardown) { teardown(); teardown = null; }
  closeDrawer();
  const [, name = "dashboard", arg] = location.hash.split("/");
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
  const fn = routes[name] || routes.dashboard;
  view.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  try { await fn(arg); } catch (e) { view.innerHTML = `<div class="card"><h2>Something went wrong</h2><p class="muted">${esc(e.message)}</p></div>`; }
  view.focus({ preventScroll: true });
}
window.addEventListener("hashchange", router);

async function refreshAiBadge() {
  try {
    const s = await api("/api/stats");
    $("#ai-badge").innerHTML = s.ai_enabled ? `<span class="pill good">AI on</span> Claude tailoring` :
      `<span class="pill">AI off</span> <a href="#/settings">add API key</a>`;
    return s;
  } catch { return null; }
}

// --------------------------------------------------------------------------- extension bridge

// The extension injects a small bridge into this page (content/bridge.js).
const ext = {
  ready: () => !!document.documentElement.dataset.jobpilotExtension,
  connected: () => document.documentElement.dataset.jobpilotConnected === "yes",
  call(type, payload = {}) {
    return new Promise((resolve, reject) => {
      const id = Math.random().toString(36).slice(2);
      const timer = setTimeout(() => { window.removeEventListener("message", onMsg); reject(new Error("The extension didn't answer. Reload it at chrome://extensions and try again.")); }, 20000);
      function onMsg(e) {
        if (e.source !== window || e.data?.source !== "jobpilot-ext" || e.data.id !== id) return;
        window.removeEventListener("message", onMsg);
        clearTimeout(timer);
        e.data.reply?.ok ? resolve(e.data.reply.data) : reject(new Error(e.data.reply?.error || "Extension error"));
      }
      window.addEventListener("message", onMsg);
      window.postMessage({ source: "jobpilot-web", id, type, payload }, location.origin);
    });
  },
};
let refreshTimer;
window.addEventListener("message", (e) => {
  if (e.source !== window || e.data?.source !== "jobpilot-ext" || e.data.event !== "changed") return;
  const st = e.data.detail?.status;
  if (st === "applied") toast("Application submitted ✓");
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => { if (["dashboard", "applications", "jobs"].includes(location.hash.split("/")[1] || "dashboard")) router(); }, 600);
});

// --------------------------------------------------------------------------- dashboard

const greeting = () => { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"; };
const delta = (now, before, unit = "") => {
  if (!now && !before) return "";
  const d = now - before;
  const cls = d > 0 ? "up" : d < 0 ? "down" : "";
  return `<span class="delta ${cls}">${d > 0 ? "▲" : d < 0 ? "▼" : "•"} ${Math.abs(d)}${unit} vs last week</span>`;
};

routes.dashboard = async () => {
  const [s, a, top, settings, profile] = await Promise.all([api("/api/stats"), api("/api/analytics"),
    api("/api/jobs?limit=6&sort=score"), api("/api/settings"), api("/api/profile")]);
  const k = a.kpis;
  const src = settings.sources;
  const hasSources = ["greenhouse", "lever", "ashby", "smartrecruiters", "workday"].some((t) => (src[t] || []).length)
    || src.remotive.enabled || src.adzuna.enabled || settings.email_import.enabled;
  const setup = [
    [s.profile_ready, "Upload your resume to build your profile", "#/profile", "Upload resume"],
    [hasSources, "Choose where jobs come from (career pages, alert emails)", "#/settings", "Choose sources"],
    [ext.ready(), "Install the browser extension to apply from JobPilot", "#/settings", "Install guide"],
    [s.ai_enabled, "Add a Claude API key for AI tailoring (optional)", "#/settings", "Add key"],
  ];
  const todo = setup.filter(([done]) => !done);
  const li = s.last_ingest;
  const name = (profile.profile.contact.name || "").split(" ")[0];
  const spark = (key) => sparkline(a.activity.map((d) => d[key]));
  view.innerHTML = `
    <div class="page-head"><div><h1>${greeting()}${name ? `, ${esc(name)}` : ""}</h1>
      <p class="muted">${li ? `Last fetch ${fmtDate(li.at)}: ${li.new} new of ${li.fetched} jobs from ${li.sources} sources.` : "Fetch jobs to fill your feed."}</p></div>
      <div class="row"><button id="fetch" class="primary">Fetch new jobs</button></div></div>
    ${todo.length ? `<div class="card setup"><div class="row"><h2 class="spacer">Get set up</h2><span class="muted small">${setup.length - todo.length} of ${setup.length} done</span></div>
      <div class="progress"><i style="width:${Math.round(((setup.length - todo.length) / setup.length) * 100)}%"></i></div>
      <ul class="checklist">${setup.map(([done, label, href, cta]) => `<li><span class="dot ${done ? "on" : ""}"></span><div class="spacer ${done ? "muted" : ""}">${label}</div>${done ? `<span class="small muted">Done</span>` : `<a href="${href}">${cta}</a>`}</li>`).join("")}</ul></div>` : ""}
    <div class="grid cols-4 kpis">
      <a class="card kpi" href="#/jobs" data-strong><div class="lbl">Strong matches</div><div class="num">${k.strong_matches}</div>
        <div class="row">${delta(k.new_matches_week, k.new_matches_prev_week)}<span class="spacer"></span>${spark("found")}</div></a>
      <a class="card kpi" href="#/applications"><div class="lbl">Applied</div><div class="num">${k.applied_total}</div>
        <div class="row">${delta(k.applied_week, k.applied_prev_week)}<span class="spacer"></span>${spark("applied")}</div></a>
      <a class="card kpi" href="#/applications"><div class="lbl">Interviews</div><div class="num">${k.interviews}</div>
        <div class="row"><span class="delta">${k.offers} offer${k.offers === 1 ? "" : "s"}</span></div></a>
      <div class="card kpi"><div class="lbl">Response rate</div><div class="num">${k.response_rate == null ? "–" : Math.round(k.response_rate * 100) + "%"}</div>
        <div class="row"><span class="delta">${k.in_queue} waiting in queue</span></div></div>
    </div>
    <div class="grid cols-2">
      <div class="card"><h3>Application pipeline</h3><div id="c-funnel"></div>
        <div class="rates">${a.funnel.slice(1).map((st, i) => {
          const prev = a.funnel[i].count;
          return `<div><div class="num">${prev ? Math.round((st.count / prev) * 100) + "%" : "–"}</div><div class="small muted">${esc(a.funnel[i].stage)} → ${esc(st.stage)}</div></div>`;
        }).join("")}</div></div>
      <div class="card"><h3>Activity — last 30 days</h3>
        <p class="small muted viz-sub">Applications sent</p><div id="c-applied"></div>
        <p class="small muted viz-sub">New jobs found</p><div id="c-found"></div></div>
    </div>
    <div class="grid cols-3">
      <div class="card"><h3>Match scores</h3><p class="small muted">Open jobs by how well they fit you</p><div id="c-scores"></div></div>
      <div class="card"><h3>Skills to learn next</h3><p class="small muted">Most-requested skills you don't list, in your better matches</p><div id="c-gaps"></div></div>
      <div class="card"><h3>Where your jobs come from</h3><p class="small muted">Open jobs by source</p><div id="c-sources"></div></div>
    </div>
    <div class="grid cols-2">
      <div class="card"><div class="row"><h3 class="spacer">Follow-ups due</h3><span class="muted small">Applied ${7}+ days ago, no reply</span></div>
        <div id="followups">${a.follow_ups.length ? a.follow_ups.slice(0, 5).map((f) => `<div class="fu" data-app="${f.application_id}">
          <div class="spacer"><b>${esc(f.title)}</b><div class="small muted">${esc(f.company)} · ${f.days} days</div></div>
          <button class="sm" data-copy-fu="${f.application_id}">Copy note</button><button class="sm" data-fu="${f.application_id}">Mark done</button></div>`).join("")
          : `<div class="viz-empty">Nothing to chase. Applications waiting a week without a reply show up here.</div>`}
          ${a.follow_ups.length > 5 ? `<p class="small muted" style="margin-top:8px">+ ${a.follow_ups.length - 5} more on the <a href="#/applications">Applications</a> board (marked “follow up”).</p>` : ""}</div></div>
      <div class="card"><h3>Companies hiring for you</h3><p class="small muted">Where most of your good matches are</p><div id="c-companies"></div></div>
    </div>
    <div class="card"><div class="row"><h2 class="spacer">Top matches</h2><a href="#/jobs">All jobs →</a></div>
      <div class="job-list">${top.items.length ? top.items.map((j) => jobRow(j)).join("") : `<div class="empty">No jobs yet.</div>`}</div></div>`;

  const ord = (i) => `var(--ord-${i + 1})`;
  barChart($("#c-funnel"), a.funnel, { label: "stage", value: "count", color: ord, empty: "Queue or apply to a job to start your pipeline." });
  const day = (r) => new Date(r.date).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  columnChart($("#c-applied"), a.activity, { x: "date", value: "applied", xLabel: day, every: 7, empty: "No applications in the last 30 days." });
  columnChart($("#c-found"), a.activity, { x: "date", value: "found", xLabel: day, every: 7, empty: "No jobs found in the last 30 days." });
  columnChart($("#c-scores"), a.scores, { x: "range", value: "count", xLabel: (r) => r.range.split("–")[0], every: 2,
    tip: (r) => `<b>Score ${esc(r.range)}</b><br>${r.count} open job${r.count === 1 ? "" : "s"}` });
  barChart($("#c-gaps"), a.skill_gaps, { label: "skill", value: "jobs", empty: "No common gaps — nice.",
    tip: (r) => `<b>${esc(r.skill)}</b><br>Wanted by ${r.jobs} of your better matches.<br><span class="muted">Click to see them</span>`,
    onClick: (r) => { jobsState.q = r.skill; jobsState.min_score = 50; location.hash = "#/jobs"; } });
  barChart($("#c-sources"), a.sources, { label: "source", value: "count", empty: "Fetch jobs to see sources." });
  barChart($("#c-companies"), a.top_companies, { label: "company", value: "jobs", empty: "No good matches yet.",
    onClick: (r) => { jobsState.q = r.company; jobsState.min_score = 50; location.hash = "#/jobs"; } });

  $("#fetch").onclick = (e) => fetchJobs(e.currentTarget, () => router());
  $("[data-strong]").addEventListener("click", () => { jobsState.min_score = 70; });
  $("#followups").onclick = async (e) => {
    const done = e.target.closest("[data-fu]"), copy = e.target.closest("[data-copy-fu]");
    if (done) { await api(`/api/applications/${done.dataset.fu}/follow-up`, { method: "POST" }); toast("Marked as followed up"); router(); }
    if (copy) {
      const f = a.follow_ups.find((x) => String(x.application_id) === copy.dataset.copyFu);
      const note = `Hi, I applied for the ${f.title} role at ${f.company} ${f.days} days ago and wanted to follow up. I'm still very interested and would be glad to share more about how my experience fits. Thank you for your time!`;
      navigator.clipboard.writeText(note).then(() => toast("Follow-up note copied"));
    }
  };
  bindJobList($(".job-list"), () => router());
};

const ageLabel = (days) => (days == null ? "" : days === 0 ? "today" : days < 30 ? `${days}d ago` : `${Math.floor(days / 30)}mo ago`);
function freshPills(j) {
  const f = j.freshness || {};
  if (f.closed) return `<span class="pill bad" title="No longer accepting applications">closed</span>`;
  const isNew = f.age_days != null && f.age_days <= 1;  // posted in the last day (or first seen, if no date)
  return `${isNew ? `<span class="pill accent">new</span>` : ""}${f.stale ? `<span class="pill warn" title="Posted over a month ago; may be filled">stale</span>` : ""}
    <span class="small muted" title="Posting age">${ageLabel(f.age_days)}</span>`;
}

// Fetch runs on the server in the background; show progress on the button.
async function fetchJobs(btn, onDone) {
  const label = btn.innerHTML;
  btn.disabled = true;
  try {
    const r = await api("/api/ingest/run?background=true", { method: "POST" });
    if (r.status === "busy") toast("A fetch is already running — showing its progress.");
    let st;
    do {
      await new Promise((res) => setTimeout(res, 1200));
      st = await api("/api/ingest/status");
      if (btn.isConnected) btn.innerHTML = `<span class="spinner"></span> ${st.total ? `${Math.min(st.done + 1, st.total)}/${st.total}` : ""} ${esc(st.current || "Fetching…")}`.slice(0, 160);
    } while (st.running);
    const last = st.last || {};
    const errs = last.errors || [];
    toast(`Fetched ${last.fetched ?? 0} jobs from ${last.sources ?? 0} sources — ${last.new ?? 0} new${last.closed ? `, ${last.closed} closed` : ""}.${errs.length ? ` ${errs.length} source error(s): ${errs.slice(0, 2).join("; ")}` : ""}`,
      errs.length > 0 && !last.fetched);
    onDone?.();
  } catch (e) { toast(e.message, true); }
  finally { if (btn.isConnected) { btn.disabled = false; btn.innerHTML = label; } }
}

function jobRow(j, selectable = false, selected = false) {
  const d = j.score_detail || {};
  const matched = (d.matched_skills || []).slice(0, 6);
  return `<div class="job ${selected ? "selected" : ""}" data-job="${j.id}" style="${selectable ? "" : "grid-template-columns:auto 1fr auto"}">
    ${selectable ? `<input type="checkbox" data-select="${j.id}" ${selected ? "checked" : ""} aria-label="Select ${esc(j.title)}">` : ""}
    ${scoreBadge(j.score)}
    <div style="min-width:0">
      <div class="title">${esc(j.title)}</div>
      <div class="meta">${esc(j.company)}${j.location ? " · " + esc(j.location) : ""}${j.remote ? " · Remote" : ""}${j.experience_text ? " · " + esc(j.experience_text) : ""}</div>
      <div class="chips" style="margin-top:6px">${matched.map((s) => `<span class="pill good">${esc(s)}</span>`).join("")}
        ${(d.missing_skills || []).slice(0, 3).map((s) => `<span class="pill">${esc(s)}</span>`).join("")}</div>
    </div>
    <div class="side"><div class="row" style="gap:4px;justify-content:flex-end">${freshPills(j)}<span class="pill">${esc(j.source)}</span></div>
      <div class="row" style="gap:6px;justify-content:flex-end">
        ${j.application ? statusPill(j.application.status) : j.status === "shortlisted" ? `<span class="pill accent">shortlisted</span>` : ""}
        ${d.excluded ? `<span class="pill bad">excluded</span>` : ""}
        ${canApply(j) ? `<button class="sm primary" data-apply="${j.id}">Apply</button>` : ""}</div></div>
  </div>`;
}

const DONE_STATES = ["applied", "interview", "offer", "rejected"];
const canApply = (j) => !j.freshness?.closed && !DONE_STATES.includes(j.application?.status);

// Clicks inside a job list: Apply buttons and opening the drawer.
function bindJobList(list, onChange) {
  list.addEventListener("click", (e) => {
    const apply = e.target.closest("[data-apply]");
    if (apply) { e.stopPropagation(); return openApply(Number(apply.dataset.apply), onChange); }
    if (e.target.closest("[data-select]")) return;
    const row = e.target.closest("[data-job]");
    if (row) openJob(Number(row.dataset.job), onChange);
  });
}

// --------------------------------------------------------------------------- job drawer

function closeDrawer() { const d = $("#drawer"); d.hidden = true; d.innerHTML = ""; }
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeDrawer(); closeModal(); } });

async function openJob(id, onChange) {
  const j = await api(`/api/jobs/${id}`);
  const d = j.score_detail || {};
  const comps = d.components || {};
  const W = { skills: 45, text: 20, title: 15, experience: 15, location: 5 };
  const drawer = $("#drawer");
  drawer.hidden = false;
  drawer.innerHTML = `
    <button class="ghost close" data-close aria-label="Close">✕</button>
    <div class="row" style="gap:14px;align-items:flex-start;padding-right:40px">${scoreBadge(j.score)}
      <div><h1>${esc(j.title)}</h1><div class="muted">${esc(j.company)} · ${esc(j.location || "Location n/a")}${j.salary ? " · " + esc(j.salary) : ""}</div>
      <div class="row small" style="margin-top:6px"><span class="pill">${esc(j.source)}</span>${j.application ? statusPill(j.application.status) : ""}
        ${j.url ? `<a href="${esc(j.url)}" target="_blank" rel="noopener noreferrer">Open posting ↗</a>` : ""}</div></div></div>
    ${j.freshness?.closed ? `<div class="import-result" style="border-color:var(--bad);background:var(--bad-soft);margin-top:12px">This posting looks <b>closed</b> (taken down or no longer accepting applications). It's hidden from your matches and won't be queued.</div>` : ""}
    <div class="row" style="margin:16px 0">
      <button class="primary" data-act="apply" ${canApply(j) ? "" : "disabled"} title="${j.freshness?.closed ? "This posting is closed" : "Review and apply from JobPilot"}">Apply…</button>
      <button data-act="queue" ${j.freshness?.closed ? "disabled" : ""}>Add to queue</button>
      <button data-act="resume">Tailor resume</button>
      <button data-act="prep">Interview prep</button>
      <button data-act="assess">AI fit check</button>
      <span class="spacer"></span>
      <button class="sm" data-act="shortlist">${j.status === "shortlisted" ? "Unshortlist" : "Shortlist"}</button>
      <button class="sm" data-act="hide">${j.status === "hidden" ? "Unhide" : "Hide"}</button>
      <button class="sm" data-act="check" title="Re-fetch the posting to see if it still accepts applications">Still open?</button>
    </div>
    <p class="small">${[`Posted ${ageLabel(j.freshness?.age_days)}`, ...(j.freshness?.stale ? ["may already be filled"] : []), ...(d.reasons || [])].map(esc).join(" · ")}
      ${j.last_checked_at ? `<span class="muted"> · checked ${fmtDate(j.last_checked_at)}</span>` : ""}</p>
    <div class="grid cols-2">
      <div class="card"><h3>Score breakdown</h3><div class="bars">${Object.entries(W).map(([k, w]) =>
        `<span>${k} <span class="muted">(${w})</span></span><div class="bar"><i style="width:${Math.round((comps[k] || 0) * 100)}%"></i></div><span>${Math.round((comps[k] || 0) * w)}</span>`).join("")}</div></div>
      <div class="card"><h3>Skills</h3>
        <p class="small muted">You have</p><div class="chips">${(d.matched_skills || []).map((s) => `<span class="pill good">${esc(s)}</span>`).join("") || "—"}</div>
        <p class="small muted" style="margin-top:8px">Missing</p><div class="chips">${(d.missing_skills || []).map((s) => `<span class="pill bad">${esc(s)}</span>`).join("") || "—"}</div></div>
    </div>
    <div id="assessment">${d.llm ? assessmentHtml(d.llm) : ""}</div>
    ${j.resumes.length ? `<div class="card" style="margin-top:16px"><h3>Tailored resumes</h3>${j.resumes.map((r) =>
      `<div class="row"><a href="#/resumes/${r.id}">${esc(r.title)}</a><span class="pill">${esc(r.method)}</span><span class="muted small">${fmtDate(r.created_at)}</span></div>`).join("")}</div>` : ""}
    <h3 style="margin-top:18px">Description</h3><div class="desc">${esc(j.description || "No description captured yet. Open the posting with the extension installed to capture it.")}</div>`;
  drawer.onclick = async (e) => {
    if (e.target.closest("[data-close]")) return closeDrawer();
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const act = btn.dataset.act;
    if (act === "apply") return openApply(id, onChange);
    if (act === "prep") return openPrep(id);
    if (act === "queue") await busy(btn, async () => {
      await api("/api/applications/bulk", { method: "POST", body: { job_ids: [id], use_llm: true } });
      toast("Added to the apply queue. The resume is being tailored."); onChange?.(); openJob(id, onChange);
    });
    if (act === "resume") await busy(btn, async () => {
      const r = await api("/api/resumes", { method: "POST", body: { job_id: id, use_llm: true } });
      location.hash = `#/resumes/${r.id}`;
    });
    if (act === "assess") await busy(btn, async () => {
      const r = await api(`/api/jobs/${id}/assess`, { method: "POST" });
      $("#assessment").innerHTML = assessmentHtml(r);
    });
    if (act === "check") await busy(btn, async () => {
      const r = await api(`/api/jobs/${id}/check`, { method: "POST" });
      toast(r.status === "closed" ? "Closed — hidden from your matches" : r.status === "open" ? "Still open" : "Couldn't tell (the site blocked the check). Open the posting to see.");
      onChange?.(); openJob(id, onChange);
    });
    if (act === "shortlist" || act === "hide") await busy(btn, async () => {
      const target = act === "shortlist" ? (j.status === "shortlisted" ? "new" : "shortlisted") : (j.status === "hidden" ? "new" : "hidden");
      await api(`/api/jobs/${id}`, { method: "PATCH", body: { status: target } });
      onChange?.(); openJob(id, onChange);
    });
  };
}

const assessmentHtml = (a) => `<div class="card" style="margin-top:16px"><div class="row"><h3 class="spacer">AI fit check</h3>
  <span class="pill ${a.verdict === "strong" || a.verdict === "good" ? "good" : a.verdict === "stretch" ? "warn" : "bad"}">${esc(a.verdict)} · ${esc(a.fit_score)}</span></div>
  <div class="grid cols-2"><div><p class="small muted">Strengths</p><ul class="steps">${a.strengths.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>
  <div><p class="small muted">Gaps</p><ul class="steps">${a.gaps.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div></div>
  <p class="small"><b>Advice:</b> ${esc(a.advice)}</p></div>`;

// --------------------------------------------------------------------------- apply from JobPilot

const SOURCE_BADGE = { profile: ["good", "from profile"], saved: ["good", "saved answer"], reviewed: ["good", "you reviewed"],
  ai: ["warn", "AI draft"], none: ["bad", "needs you"] };

function fieldInput(f, i) {
  const id = `af-${i}`;
  const val = f.value || "";
  if (f.type === "select") return `<select id="${id}" data-field="${i}"><option value="">Select…</option>${f.options.map((o) =>
    `<option ${o.label === val ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`;
  if (f.type === "multiselect") return `<select id="${id}" data-field="${i}"><option value="">Select…</option>${f.options.map((o) =>
    `<option ${o.label === val ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`;
  if (f.type === "checkbox") return `<label class="check"><input type="checkbox" id="${id}" data-field="${i}" ${val ? "checked" : ""}> ${esc(f.options[0]?.label || "Yes")}</label>`;
  if (f.type === "textarea") return `<textarea id="${id}" data-field="${i}" rows="3">${esc(val)}</textarea>`;
  return `<input id="${id}" data-field="${i}" value="${esc(val)}">`;
}

async function openApply(jobId, onChange) {
  const m = $("#modal");
  m.hidden = false;
  m.innerHTML = `<div class="card wide" role="dialog" aria-label="Apply"><div class="empty"><span class="spinner"></span> Preparing your application…</div></div>`;
  m.onclick = (e) => { if (e.target === m) closeModal(); };
  let info, job;
  try {
    [info, job] = await Promise.all([api(`/api/jobs/${jobId}/apply-info`), api(`/api/jobs/${jobId}`)]);
  } catch (e) { closeModal(); return toast(e.message, true); }

  const render = () => {
    const app = info.application;
    const ready = app && app.resume_id && !["preparing", "failed"].includes(app.status);
    const form = info.form || [];
    const missing = form.filter((f) => f.required && !f.value).length;
    const host = (() => { try { return new URL(info.apply_url).hostname.replace(/^www\./, ""); } catch { return ""; } })();
    const kr = info.resume?.keyword_report;
    m.innerHTML = `<div class="card wide" role="dialog" aria-label="Apply to ${esc(job.title)}">
      <div class="row" style="align-items:flex-start">${scoreBadge(job.score)}
        <div class="spacer"><h2 style="margin:0">${esc(job.title)}</h2><div class="muted small">${esc(job.company)} · ${esc(job.location || "")}${host ? ` · applies on ${esc(host)}` : ""}</div></div>
        <button class="ghost" data-cancel aria-label="Close">✕</button></div>

      <div class="step"><div class="step-n ${ready ? "done" : ""}">1</div><div class="spacer">
        <b>Tailored resume</b>
        ${ready ? `<div class="small">Ready${kr ? ` — covers ${Math.round((kr.coverage || 0) * 100)}% of the skills they ask for` : ""}.
            <a href="#/resumes/${app.resume_id}" data-cancel>Review / edit</a> · <a href="/api/resumes/${app.resume_id}/file/pdf" target="_blank">PDF</a></div>`
          : `<div class="small muted">A resume tailored to this job is attached to the application. Nothing is invented — only your real experience, reordered for this job.</div>
             <div class="row" style="margin-top:8px"><button class="primary sm" id="ap-prepare">Tailor my resume</button>
             <label class="check small"><input type="checkbox" id="ap-cover"> with cover letter</label></div>`}
      </div></div>

      <div class="step"><div class="step-n ${form.length && !missing ? "done" : ""}">2</div><div class="spacer">
        <b>Application questions</b>
        ${form.length ? `<div class="small muted">These are the actual questions on ${esc(host || "the form")}, answered from your profile and saved answers.
            Change anything — what you change is remembered for next time.${missing ? ` <b class="bad-text">${missing} required answer${missing === 1 ? "" : "s"} still needed.</b>` : ""}</div>
          <div class="qa">${form.map((f, i) => `<div class="qa-row ${f.required && !f.value ? "need" : ""}">
            <label for="af-${i}">${esc(f.label)}${f.required ? ' <span class="req">*</span>' : ""}
              <span class="pill ${SOURCE_BADGE[f.value ? f.source : "none"]?.[0] || ""}">${SOURCE_BADGE[f.value ? f.source : "none"]?.[1] || esc(f.source)}</span></label>
            ${fieldInput(f, i)}</div>`).join("")}</div>`
          : `<div class="small muted">This site's questions are filled in on the page by the extension, from your profile and saved answers. Anything it can't answer truthfully is left for you.</div>`}
      </div></div>

      <div class="step"><div class="step-n">3</div><div class="spacer">
        <b>Submit</b>
        ${ext.ready() ? `
          ${info.can_auto_submit ? `<label class="check" style="margin-top:6px"><input type="checkbox" id="ap-auto" ${app?.auto_submit || (form.length && !missing) ? "checked" : ""}>
            Submit for me once every required field is filled <span class="muted small">(otherwise you press Submit on the page)</span></label>`
            : `<div class="small muted">${esc(job.source)} applications are submitted by you on the page — JobPilot fills what it can and tracks it.</div>`}
          <div class="small muted" style="margin-top:4px">A tab opens directly on the application form, fills it, attaches your resume${info.can_auto_submit ? ", submits if you allowed it," : ""} and closes when it's done. Captchas, if any, are yours to solve.</div>`
        : `<div class="small"><b>Install the browser extension</b> to apply from here (Settings → Browser extension). Without it you can open the form and apply yourself:</div>`}
      </div></div>

      <div class="row end modal-foot">
        ${info.apply_url ? `<a class="btn" href="${esc(info.apply_url)}" target="_blank" rel="noopener noreferrer">Open form myself ↗</a>` : ""}
        ${!ext.ready() && app ? `<button id="ap-mark">I applied</button>` : ""}
        <button class="primary" id="ap-go" ${ext.ready() ? "" : "disabled"}>${ready ? "Apply now" : "Tailor resume & apply"}</button></div>
    </div>`;

    const collect = () => {
      const answers = {}, labels = {};
      form.forEach((f, i) => {
        const el = $(`[data-field="${i}"]`, m);
        const v = f.type === "checkbox" ? (el.checked ? "Yes" : "") : el.value.trim();
        f.value = v;
        if (v) { answers[f.name] = v; labels[f.name] = f.label; }
      });
      return { answers, labels, auto_submit: !!$("#ap-auto", m)?.checked };
    };
    const prepare = async () => {
      await api(`/api/jobs/${jobId}/prepare-apply`, { method: "POST", body: { use_llm: true, cover_letter: !!$("#ap-cover", m)?.checked } });
      info = await api(`/api/jobs/${jobId}/apply-info`);
    };
    $$("[data-cancel]", m).forEach((b) => (b.onclick = closeModal));
    $("#ap-prepare", m)?.addEventListener("click", (e) => busy(e.currentTarget, async () => { const keep = collect(); await prepare(); restore(keep); render(); onChange?.(); }));
    $("#ap-mark", m)?.addEventListener("click", async () => {
      await api(`/api/applications/${info.application.id}`, { method: "PATCH", body: { status: "applied" } });
      closeModal(); toast("Marked as applied"); onChange?.();
    });
    $("#ap-go", m).onclick = (e) => busy(e.currentTarget, async () => {
      const chosen = collect();
      if (!(info.application?.resume_id) || ["preparing", "failed"].includes(info.application.status)) {
        e.currentTarget.innerHTML = `<span class="spinner"></span> Tailoring resume…`;
        await prepare();
      }
      await api(`/api/applications/${info.application.id}/answers`, { method: "PUT", body: chosen });
      const r = await ext.call("applyNow", { applicationIds: [info.application.id] });
      closeModal();
      toast(`Opened the application for ${r.title}${chosen.auto_submit ? " — it will submit once everything is filled" : ""}.`);
      onChange?.();
    });
  };
  // Keep edits when re-rendering after tailoring.
  const restore = (keep) => (info.form || []).forEach((f) => { if (keep.answers[f.name] != null) { f.value = keep.answers[f.name]; f.source = "reviewed"; } });
  render();
}

async function openPrep(jobId) {
  const m = $("#modal");
  m.hidden = false;
  m.onclick = (e) => { if (e.target === m) closeModal(); };
  const job = await api(`/api/jobs/${jobId}`);
  const show = (p) => {
    const items = (list) => list.map((q) => `<div class="prep"><b>${esc(q.question)}</b><div class="small muted">${esc(q.why_they_ask)}</div>
      <div class="small"><span class="pill good">Your angle</span> ${esc(q.your_angle)}</div></div>`).join("");
    m.innerHTML = `<div class="card wide" role="dialog" aria-label="Interview prep"><div class="row"><h2 class="spacer" style="margin:0">Interview prep</h2>
      <span class="pill ${p.method === "llm" ? "accent" : ""}">${p.method === "llm" ? "AI" : "built-in"}</span><button class="ghost" data-cancel aria-label="Close">✕</button></div>
      <p class="muted">${esc(job.title)} · ${esc(job.company)}</p><p>${esc(p.summary)}</p>
      <h3>Technical</h3>${items(p.technical) || '<p class="muted small">No specific skills found in the description.</p>'}
      <h3>Behavioural</h3>${items(p.behavioral)}
      ${p.gaps_to_prepare.length ? `<h3>Gaps to prepare for</h3><ul class="steps">${p.gaps_to_prepare.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>` : ""}
      <h3>Questions to ask them</h3><ul class="steps">${p.questions_to_ask.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>
      <div class="row end"><button id="prep-again">Regenerate</button><button class="primary" data-cancel>Done</button></div></div>`;
    $$("[data-cancel]", m).forEach((b) => (b.onclick = closeModal));
    $("#prep-again", m).onclick = (e) => busy(e.currentTarget, async () => show(await api(`/api/jobs/${jobId}/interview-prep`, { method: "POST" })));
  };
  const cached = job.score_detail?.interview_prep;
  if (cached) return show(cached);
  m.innerHTML = `<div class="card wide"><div class="empty"><span class="spinner"></span> Preparing likely questions from the job and your experience…</div></div>`;
  try { show(await api(`/api/jobs/${jobId}/interview-prep`, { method: "POST" })); } catch (e) { closeModal(); toast(e.message, true); }
}

// --------------------------------------------------------------------------- jobs

const jobsState = { q: "", source: "", status: "active", min_score: 0, sort: "score", remote: "", fresh: "", offset: 0, selected: new Set() };
routes.jobs = async () => {
  view.innerHTML = `
    <div class="page-head"><div><h1>Jobs</h1><p class="muted">Ranked against your profile. Select several and queue them in one go.</p></div>
      <div class="row"><button id="add-job">Add job manually</button><button id="fetch" class="primary">Fetch new jobs</button></div></div>
    <div class="toolbar">
      <input type="search" id="f-q" placeholder="Search title, company, skill…" value="${esc(jobsState.q)}">
      <select id="f-source" aria-label="Source"><option value="">All sources</option></select>
      <select id="f-status" aria-label="Status">${[["active", "Active"], ["new", "New"], ["shortlisted", "Shortlisted"], ["hidden", "Hidden"], ["closed", "Closed"], ["all", "All"]]
        .map(([v, l]) => `<option value="${v}" ${jobsState.status === v ? "selected" : ""}>${l}</option>`).join("")}</select>
      <select id="f-fresh" aria-label="Found within">${[["", "Any time"], ["1", "Found today"], ["3", "Last 3 days"], ["7", "Last 7 days"], ["30", "Last 30 days"]]
        .map(([v, l]) => `<option value="${v}" ${jobsState.fresh === v ? "selected" : ""}>${l}</option>`).join("")}</select>
      <select id="f-remote" aria-label="Remote"><option value="">Any location</option><option value="true" ${jobsState.remote === "true" ? "selected" : ""}>Remote only</option></select>
      <label class="check">Min score <input type="number" id="f-min" min="0" max="100" step="5" value="${jobsState.min_score}" style="width:70px"></label>
      <select id="f-sort" aria-label="Sort">${[["score", "Best match"], ["recent", "Newest"], ["company", "Company"]]
        .map(([v, l]) => `<option value="${v}" ${jobsState.sort === v ? "selected" : ""}>${l}</option>`).join("")}</select>
      <span class="spacer"></span><span id="count" class="muted small"></span>
    </div>
    <div class="job-list" id="list"></div>
    <div class="row" id="pager" style="margin-top:12px"></div>
    <div class="bulkbar" id="bulk" hidden></div>`;
  const load = async () => {
    const p = new URLSearchParams({ q: jobsState.q, source: jobsState.source, status: jobsState.status === "all" ? "" : jobsState.status,
      min_score: jobsState.min_score || 0, sort: jobsState.sort, limit: 50, offset: jobsState.offset });
    if (jobsState.remote) p.set("remote", jobsState.remote);
    if (jobsState.fresh) p.set("new_within_days", jobsState.fresh);
    if (jobsState.status === "all") p.set("include_closed", "true");
    const r = await api(`/api/jobs?${p}`);
    const srcSel = $("#f-source");
    if (srcSel.options.length <= 1) r.sources.forEach((s) => srcSel.add(new Option(s, s, false, s === jobsState.source)));
    $("#count").textContent = `${r.total} job${r.total === 1 ? "" : "s"}`;
    $("#list").innerHTML = r.items.length ? r.items.map((j) => jobRow(j, true, jobsState.selected.has(j.id))).join("")
      : `<div class="empty card">No jobs match. Fetch jobs, loosen filters, or browse Naukri/Indeed with the extension installed.</div>`;
    const pages = Math.ceil(r.total / 50);
    const page = jobsState.offset / 50;
    $("#pager").innerHTML = pages > 1 ? `<button class="sm" data-page="${page - 1}" ${page === 0 ? "disabled" : ""}>← Prev</button>
      <span class="small muted">Page ${page + 1} of ${pages}</span><button class="sm" data-page="${page + 1}" ${page + 1 >= pages ? "disabled" : ""}>Next →</button>
      <span class="spacer"></span><button class="sm" id="sel-page">Select page</button>` : r.items.length ? `<span class="spacer"></span><button class="sm" id="sel-page">Select all</button>` : "";
    $("#sel-page")?.addEventListener("click", () => { r.items.forEach((j) => jobsState.selected.add(j.id)); load(); });
    renderBulk();
  };
  const renderBulk = () => {
    const n = jobsState.selected.size;
    const bar = $("#bulk");
    bar.hidden = n === 0;
    bar.innerHTML = `<b>${n} selected</b><span class="spacer"></span>
      <label class="check" style="color:inherit"><input type="checkbox" id="b-cover"> cover letters</label>
      <button data-bulk="shortlisted">Shortlist</button><button data-bulk="hidden">Hide</button>
      <button data-bulk="check" title="Re-fetch each posting to see if it is still open">Still open?</button><button data-bulk="clear">Clear</button><button class="primary" data-bulk="queue">Tailor resumes &amp; queue ${n}</button>`;
  };
  let t;
  $("#f-q").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { jobsState.q = e.target.value; jobsState.offset = 0; load(); }, 250); };
  $("#f-source").onchange = (e) => { jobsState.source = e.target.value; jobsState.offset = 0; load(); };
  $("#f-status").onchange = (e) => { jobsState.status = e.target.value; jobsState.offset = 0; load(); };
  $("#f-remote").onchange = (e) => { jobsState.remote = e.target.value; jobsState.offset = 0; load(); };
  $("#f-fresh").onchange = (e) => { jobsState.fresh = e.target.value; jobsState.offset = 0; load(); };
  $("#f-min").onchange = (e) => { jobsState.min_score = Number(e.target.value) || 0; jobsState.offset = 0; load(); };
  $("#f-sort").onchange = (e) => { jobsState.sort = e.target.value; load(); };
  $("#pager").onclick = (e) => { const b = e.target.closest("[data-page]"); if (b) { jobsState.offset = Number(b.dataset.page) * 50; load(); } };
  $("#list").onclick = (e) => {
    const cb = e.target.closest("[data-select]");
    if (!cb) return;
    const id = Number(cb.dataset.select);
    cb.checked ? jobsState.selected.add(id) : jobsState.selected.delete(id);
    cb.closest(".job").classList.toggle("selected", cb.checked);
    renderBulk();
  };
  bindJobList($("#list"), load);
  $("#bulk").onclick = async (e) => {
    const b = e.target.closest("[data-bulk]");
    if (!b) return;
    const ids = [...jobsState.selected];
    if (b.dataset.bulk === "clear") { jobsState.selected.clear(); return load(); }
    if (b.dataset.bulk === "check") return busy(b, async () => {
      const r = await api("/api/jobs/check", { method: "POST", body: { job_ids: ids.slice(0, 100) } });
      toast(`${r.open} open, ${r.closed} closed${r.unknown ? `, ${r.unknown} couldn't be checked` : ""}`);
      load();
    });
    if (b.dataset.bulk === "queue") return busy(b, async () => {
      const r = await api("/api/applications/bulk", { method: "POST", body: { job_ids: ids, use_llm: true, cover_letter: $("#b-cover").checked } });
      toast(`Queued ${r.queued}. Tailoring resumes in the background…${r.skipped_already_applied ? ` (${r.skipped_already_applied} already applied)` : ""}${r.skipped_closed ? ` ${r.skipped_closed} closed posting(s) skipped.` : ""}`);
      jobsState.selected.clear();
      location.hash = "#/applications";
    });
    await busy(b, async () => {
      await api("/api/jobs/bulk-status", { method: "POST", body: { job_ids: ids, status: b.dataset.bulk } });
      jobsState.selected.clear(); load();
    });
  };
  $("#fetch").onclick = (e) => fetchJobs(e.currentTarget, load);
  $("#add-job").onclick = () => manualJobModal(load);
  await load();
};

function closeModal() { const m = $("#modal"); m.hidden = true; m.innerHTML = ""; }
function manualJobModal(onDone) {
  const m = $("#modal");
  m.hidden = false;
  m.innerHTML = `<div class="card" role="dialog" aria-label="Add job"><h2>Add a job</h2>
    <p class="small muted">Paste any posting (LinkedIn, a company site, a referral) to score it and tailor a resume.</p>
    <form id="jf" class="grid">
      <div class="form-grid"><label class="field">Title<input name="title" required></label><label class="field">Company<input name="company"></label>
      <label class="field">Location<input name="location"></label><label class="field">Posting URL<input name="url" type="url"></label></div>
      <label class="field">Job description<textarea name="description" rows="10" required minlength="20"></textarea></label>
      <div class="row end"><button type="button" data-cancel>Cancel</button><button class="primary" type="submit">Add &amp; score</button></div></form></div>`;
  m.onclick = (e) => { if (e.target === m || e.target.closest("[data-cancel]")) closeModal(); };
  $("#jf").onsubmit = (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    busy(e.submitter, async () => {
      const j = await api("/api/jobs", { method: "POST", body });
      closeModal(); toast(`Added — match score ${Math.round(j.score)}`); await onDone?.(); openJob(j.id, onDone);
    });
  };
}

// --------------------------------------------------------------------------- profile

let profileState = null;
routes.profile = async () => {
  importResult = null;
  const r = await api("/api/profile");
  profileState = r.profile;
  renderProfile(r);
};

function renderProfile(meta = {}) {
  const p = profileState;
  const isEmpty = !p.experience.length && !p.skills.length && !p.contact.name;
  const exp = p.experience.map((e, i) => `
    <div class="section-edit"><div class="row head"><b class="spacer">${esc(e.title || "New role")}${e.company ? " · " + esc(e.company) : ""}</b>
      <button class="sm" data-move="experience.${i}.-1" aria-label="Move up">↑</button><button class="sm" data-move="experience.${i}.1" aria-label="Move down">↓</button>
      <button class="sm danger" data-del="experience.${i}">Remove</button></div>
      <div class="form-grid">${inp("Title", `experience.${i}.title`, p)}${inp("Company", `experience.${i}.company`, p)}
        ${inp("Location", `experience.${i}.location`, p)}${inp("Start (YYYY-MM)", `experience.${i}.start`, p, { placeholder: "2021-03" })}
        ${inp("End (YYYY-MM)", `experience.${i}.end`, p, { placeholder: "blank if current" })}
        <label class="check" style="align-self:end"><input type="checkbox" data-bind="experience.${i}.current" ${e.current ? "checked" : ""}> Current role</label></div>
      <p class="small muted" style="margin:10px 0 6px">Achievements — one per line item. Write every accomplishment; tailoring picks the best per job.</p>
      ${e.bullets.map((b, k) => `<div class="bullet-row"><textarea rows="1" data-bind="experience.${i}.bullets.${k}.text">${esc(b.text)}</textarea>
        <button class="sm ghost" data-del="experience.${i}.bullets.${k}" aria-label="Remove bullet">✕</button></div>`).join("")}
      <button class="sm" data-add="experience.${i}.bullets">+ Achievement</button></div>`).join("");
  const proj = p.projects.map((pr, i) => `
    <div class="section-edit"><div class="row head"><b class="spacer">${esc(pr.name || "New project")}</b><button class="sm danger" data-del="projects.${i}">Remove</button></div>
      <div class="form-grid">${inp("Name", `projects.${i}.name`, p)}${inp("URL", `projects.${i}.url`, p)}</div>
      ${area("Description", `projects.${i}.description`, p, 2)}
      ${pr.bullets.map((b, k) => `<div class="bullet-row"><textarea rows="1" data-bind="projects.${i}.bullets.${k}.text">${esc(b.text)}</textarea>
        <button class="sm ghost" data-del="projects.${i}.bullets.${k}" aria-label="Remove bullet">✕</button></div>`).join("")}
      <button class="sm" data-add="projects.${i}.bullets">+ Bullet</button></div>`).join("");
  const edu = p.education.map((e, i) => `
    <div class="section-edit"><div class="row head"><b class="spacer">${esc(e.degree || "New entry")}</b><button class="sm danger" data-del="education.${i}">Remove</button></div>
      <div class="form-grid">${inp("Degree", `education.${i}.degree`, p)}${inp("Institution", `education.${i}.institution`, p)}
      ${inp("Start", `education.${i}.start`, p)}${inp("End", `education.${i}.end`, p)}${inp("Grade", `education.${i}.grade`, p)}${inp("Location", `education.${i}.location`, p)}</div></div>`).join("");
  const certs = p.certifications.map((c, i) => `<div class="form-grid section-edit">${inp("Certification", `certifications.${i}.name`, p)}
      ${inp("Issuer", `certifications.${i}.issuer`, p)}${inp("Year", `certifications.${i}.year`, p)}
      <button class="sm danger" style="align-self:end" data-del="certifications.${i}">Remove</button></div>`).join("");
  const pr = p.preferences;
  const tri = (label, path) => { const v = getPath(p, path); return `<label class="field">${label}<select data-bind="${path}" data-type="tristate">
      <option value="" ${v == null ? "selected" : ""}>Not set</option><option value="true" ${v === true ? "selected" : ""}>Yes</option><option value="false" ${v === false ? "selected" : ""}>No</option></select></label>`; };

  view.innerHTML = `
    <div class="page-head"><div><h1>Profile</h1><p class="muted">Your master profile. Every tailored resume is built only from what's here.
      ${meta.derived && !isEmpty ? ` · <b>${meta.derived.total_years}</b> yrs experience · ${meta.derived.skills.length} skills detected` : ""}</p></div>
      <div class="row"><button id="import">Upload resume</button><button class="primary" id="save">Save profile</button></div></div>
    ${importCardHtml(isEmpty)}
    <div id="pf">
    <div class="card"><h2>Contact</h2><div class="form-grid">
      ${inp("Full name", "contact.name", p)}${inp("Email", "contact.email", p, { type: "email" })}${inp("Phone", "contact.phone", p)}
      ${inp("Location", "contact.location", p, { placeholder: "Bengaluru, India" })}${inp("LinkedIn", "contact.linkedin", p)}${inp("GitHub", "contact.github", p)}${inp("Website", "contact.website", p)}</div></div>
    <div class="card"><h2>Headline &amp; summary</h2>${inp("Headline", "headline", p, { placeholder: "Senior Backend Engineer" })}
      <div style="margin-top:10px">${area("Summary", "summary", p, 4)}</div></div>
    <div class="card"><h2>Skills</h2>${tagInput("skills", p, "Add a skill and press Enter")}
      <p class="small muted" style="margin-top:6px">List everything you can genuinely work with. Order doesn't matter; tailoring re-orders per job.</p></div>
    <div class="card"><div class="row"><h2 class="spacer">Experience</h2><button class="sm" data-add="experience">+ Role</button></div>${exp || `<p class="muted">No roles yet.</p>`}</div>
    <div class="card"><div class="row"><h2 class="spacer">Projects</h2><button class="sm" data-add="projects">+ Project</button></div>${proj || `<p class="muted">Optional.</p>`}</div>
    <div class="card"><div class="row"><h2 class="spacer">Education</h2><button class="sm" data-add="education">+ Education</button></div>${edu}</div>
    <div class="card"><div class="row"><h2 class="spacer">Certifications</h2><button class="sm" data-add="certifications">+ Certification</button></div>${certs}</div>
    <div class="card"><h2>Languages</h2>${tagInput("languages", p, "English, Hindi…")}</div>
    <div class="card"><h2>Job preferences</h2><p class="small muted">Used for ranking and for answering standard application questions.</p>
      <div class="grid cols-2">
        <div><label class="field">Target job titles</label>${tagInput("preferences.target_titles", p, "Backend Engineer…")}</div>
        <div><label class="field">Preferred locations</label>${tagInput("preferences.locations", p, "Bengaluru, Pune…")}</div>
        <div><label class="field">Never show companies</label>${tagInput("preferences.excluded_companies", p, "Company name")}</div>
        <div><label class="field">Never show jobs mentioning</label>${tagInput("preferences.excluded_keywords", p, "e.g. night shift")}</div></div>
      <div class="form-grid" style="margin-top:12px">
        <label class="check"><input type="checkbox" data-bind="preferences.remote_ok" ${pr.remote_ok ? "checked" : ""}> Open to remote</label>
        ${inp("Notice period (days)", "preferences.notice_period_days", p, { type: "number" })}
        ${inp("Current CTC", "preferences.current_ctc", p, { placeholder: "e.g. 28 LPA" })}${inp("Expected CTC", "preferences.expected_ctc", p, { placeholder: "e.g. 40 LPA" })}
        ${inp("Work authorization", "preferences.work_authorization", p, { placeholder: "e.g. Indian citizen" })}
        ${tri("Requires visa sponsorship", "preferences.requires_sponsorship")}${tri("Willing to relocate", "preferences.willing_to_relocate")}</div></div>
    </div>`;

  const pf = $("#pf");
  bindInputs(pf, () => profileState);
  bindTags(pf, () => profileState, (path) => refreshTags(pf, () => profileState, path));
  pf.onclick = (e) => {
    const add = e.target.closest("[data-add]"); const del = e.target.closest("[data-del]"); const mv = e.target.closest("[data-move]");
    if (add) {
      const blank = { experience: { title: "", company: "", location: "", start: "", end: "", current: false, bullets: [{ text: "" }], skills: [] },
        projects: { name: "", url: "", description: "", bullets: [], skills: [] }, education: { degree: "", institution: "", location: "", start: "", end: "", grade: "" },
        certifications: { name: "", issuer: "", year: "" } };
      const path = add.dataset.add;
      const key = path.split(".").pop();
      getPath(profileState, path).push(key === "bullets" ? { text: "" } : structuredClone(blank[key]));
      renderProfile(meta);
    } else if (del) {
      const parts = del.dataset.del.split("."); const idx = Number(parts.pop());
      getPath(profileState, parts.join(".")).splice(idx, 1); renderProfile(meta);
    } else if (mv) {
      const [sec, i, dir] = mv.dataset.move.split("."); const list = profileState[sec]; const a = Number(i), b = a + Number(dir);
      if (b >= 0 && b < list.length) { [list[a], list[b]] = [list[b], list[a]]; renderProfile(meta); }
    }
  };
  $("#save").onclick = (e) => busy(e.currentTarget, async () => {
    const r = await api("/api/profile", { method: "PUT", body: profileState });
    profileState = r.profile; toast("Profile saved — jobs re-scored."); renderProfile(r);
  });
  $("#import").onclick = () => $("#imp-file").click();
  bindImportCard(isEmpty);
}

// Resume upload -> profile. The import card sits above the editor; a result
// banner reports what was filled, what is missing, and offers undo.
let importResult = null;

function importCardHtml(isEmpty) {
  const r = importResult;
  const c = r?.report.counts;
  const banner = r ? `<div class="import-result">
      <div class="row"><b class="spacer">✓ Filled from ${esc(r.fileName)} ${r.method === "llm" ? `<span class="pill accent">AI</span>` : `<span class="pill">built-in parser</span>`}</b>
        ${r.previous ? `<button class="sm" id="imp-undo">Undo import</button>` : ""}<button class="sm ghost" id="imp-close" aria-label="Dismiss">✕</button></div>
      <div class="chips" style="margin:6px 0">${[["experience", "role"], ["achievements", "achievement"], ["skills", "skill"], ["education", "education entry", "education entries"],
        ["projects", "project"], ["certifications", "certification"], ["languages", "language"]].filter(([k]) => c[k])
        .map(([k, one, many]) => `<span class="pill good">${c[k]} ${c[k] === 1 ? one : many || one + "s"}</span>`).join("")}<span class="pill">${r.report.total_years} yrs experience</span></div>
      ${r.report.missing.length ? `<p class="small">Not found in the resume — please fill in below: ${r.report.missing.map((m) => `<span class="pill warn">${esc(m)}</span>`).join(" ")}</p>`
        : `<p class="small muted">Everything important was found.</p>`}
      ${r.warnings.map((w) => `<p class="small muted">${esc(w)}</p>`).join("")}
      <p class="small muted">${r.mode === "merge" ? "New details were added to your existing profile; nothing was overwritten." : "Your profile was rebuilt from this resume."} It's saved — review each section and edit anything that's off.</p>
    </div>` : "";
  return `<div class="card import-card ${isEmpty ? "hero" : ""}" id="import-card">
    ${banner}
    <div class="dropzone" id="dropzone" tabindex="0" role="button" aria-label="Upload resume">
      <input type="file" id="imp-file" accept=".pdf,.docx,.txt,.md" hidden>
      <div class="dz-idle">
        <div class="dz-title">${isEmpty ? "Upload your resume to fill in your profile" : "Update your profile from a resume"}</div>
        <div class="small muted">Drop a PDF, DOCX or TXT here, or <u>choose a file</u>. Contact details, experience, achievements, skills, education, projects and certifications are filled in automatically.</div>
      </div>
      <div class="dz-busy" hidden><span class="spinner"></span> <span id="dz-msg">Reading…</span></div>
    </div>
    <div class="row" style="margin-top:10px">
      ${isEmpty ? "" : `<label class="check"><input type="radio" name="imp-mode" value="merge" checked> Add to my profile</label>
        <label class="check"><input type="radio" name="imp-mode" value="replace"> Replace my profile</label><span class="spacer"></span>`}
      <label class="check"><input type="checkbox" id="imp-ai" checked> Use AI parsing when an API key is set</label>
    </div>
    <details style="margin-top:8px"><summary class="small">No file? Paste your resume or Naukri/LinkedIn profile text</summary>
      <textarea id="imp-text" rows="7" style="margin-top:8px" placeholder="Paste the full text here"></textarea>
      <div class="row end" style="margin-top:6px"><button class="primary sm" id="imp-text-go">Fill profile from text</button></div></details>
  </div>`;
}

function bindImportCard(isEmpty) {
  const dz = $("#dropzone");
  const fileInput = $("#imp-file");
  const run = async (file, text) => {
    const fd = new FormData();
    if (file) fd.set("file", file); else fd.set("text", text);
    fd.set("use_llm", $("#imp-ai").checked ? "true" : "false");
    fd.set("mode", isEmpty ? "replace" : ($("input[name=imp-mode]:checked")?.value || "merge"));
    fd.set("save", "true");
    if (!isEmpty && fd.get("mode") === "replace" && !confirm("Replace your whole profile with this resume? Job preferences are kept, and you can undo.")) return;
    $(".dz-idle", dz).hidden = true; $(".dz-busy", dz).hidden = false;
    $("#dz-msg").textContent = `Reading ${file ? file.name : "pasted text"}…${$("#imp-ai").checked ? " (AI parsing can take a moment)" : ""}`;
    try {
      const r = await api("/api/profile/import", { method: "POST", form: fd });
      if (!r.report || !r.saved) {  // response from an older server process
        await checkServerVersion();
        throw new Error(RESTART_HELP);
      }
      profileState = r.profile;
      importResult = { ...r, fileName: file ? file.name : "pasted text" };
      renderProfile({ derived: r.derived, exists: true });
      toast("Profile filled and saved");
      $("#import-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
      refreshAiBadge();
    } catch (e) {
      toast(e.message, true);
      $(".dz-idle", dz).hidden = false; $(".dz-busy", dz).hidden = true;
    }
  };
  dz.onclick = () => fileInput.click();
  dz.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } };
  fileInput.onchange = () => fileInput.files[0] && run(fileInput.files[0]);
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) run(f); });
  $("#imp-text-go").onclick = () => { const t = $("#imp-text").value.trim(); t ? run(null, t) : toast("Paste some text first", true); };
  $("#imp-close")?.addEventListener("click", () => { importResult = null; renderProfile({ derived: null, exists: true }); });
  $("#imp-undo")?.addEventListener("click", (e) => busy(e.currentTarget, async () => {
    const r = await api("/api/profile", { method: "PUT", body: importResult.previous });
    profileState = r.profile; importResult = null; toast("Import undone"); renderProfile({ ...r, exists: true });
  }));
  // Point at what the resume didn't contain.
  if (importResult) {
    $$("#pf [data-bind]").forEach((el) => {
      if (/^(contact\.(name|email|phone|location|linkedin)|headline|summary)$/.test(el.dataset.bind) && !el.value) el.classList.add("needs");
    });
  }
}

// --------------------------------------------------------------------------- applications

const BOARD = [
  { key: "todo", title: "To apply", statuses: ["preparing", "queued", "in_progress", "failed"], drop: "queued" },
  { key: "applied", title: "Applied", statuses: ["applied"], drop: "applied" },
  { key: "interview", title: "Interviewing", statuses: ["interview"], drop: "interview" },
  { key: "offer", title: "Offer", statuses: ["offer"], drop: "offer" },
  { key: "closed", title: "Closed", statuses: ["rejected", "skipped"], drop: "rejected" },
];
const daysSince = (d) => (d ? Math.floor((Date.now() - new Date(d).getTime()) / 864e5) : null);
let appsView = (() => { try { return localStorage.getItem("jp-apps-view") || "board"; } catch { return "board"; } })();

routes.applications = async () => {
  view.innerHTML = `<div class="page-head"><div><h1>Applications</h1>
    <p class="muted">Drag cards between columns as things progress. <b>Apply</b> opens the form, fills it and attaches your tailored resume.</p></div>
    <div class="row"><button class="primary" id="apply-all" hidden></button>
      <div class="seg" role="tablist"><button data-view="board" class="${appsView === "board" ? "on" : ""}">Board</button><button data-view="table" class="${appsView === "table" ? "on" : ""}">Table</button></div></div></div>
    <div id="apps"></div>`;
  let timer;
  const load = async () => {
    const apps = await api("/api/applications");
    const ready = apps.filter((a) => a.status === "queued" && a.resume_id);
    const btn = $("#apply-all");
    btn.hidden = !ready.length || !ext.ready();
    btn.textContent = `Apply to ${ready.length} queued`;
    btn.onclick = (e) => busy(e.currentTarget, async () => {
      const r = await ext.call("applyNow", { applicationIds: ready.map((a) => a.id) });
      toast(`Applying — started with ${r.title}. JobPilot updates as each one finishes.`);
    });
    if (appsView === "board") renderBoard(apps); else renderTable(apps);
    clearTimeout(timer);
    if (apps.some((a) => a.status === "preparing")) timer = setTimeout(load, 2500);
  };
  teardown = () => clearTimeout(timer);
  $$("[data-view]").forEach((b) => (b.onclick = () => {
    appsView = b.dataset.view;
    try { localStorage.setItem("jp-apps-view", appsView); } catch { /* private mode */ }
    $$("[data-view]").forEach((x) => x.classList.toggle("on", x === b));
    load();
  }));

  const setStatus = async (id, status) => {
    try { await api(`/api/applications/${id}`, { method: "PATCH", body: { status } }); }
    catch (err) { toast(err.message, true); }
    load();
  };

  function card(a) {
    const since = daysSince(a.applied_at);
    const due = a.status === "applied" && since >= 7 && daysSince(a.followed_up_at ?? a.applied_at) >= 7;
    return `<div class="kcard" draggable="true" data-id="${a.id}">
      <div class="row" style="gap:8px;align-items:flex-start;flex-wrap:nowrap">${a.job?.score != null ? scoreBadge(a.job.score) : ""}
        <div class="spacer" style="min-width:0"><a href="#" data-open="${a.job_id}" class="ktitle" draggable="false">${esc(a.job?.title ?? "(deleted job)")}</a>
          <div class="small muted">${esc(a.job?.company ?? "")}</div></div></div>
      <div class="row small" style="gap:6px;margin-top:6px">
        ${a.status === "preparing" ? `<span class="pill accent"><span class="spinner"></span> tailoring</span>` : ""}
        ${a.status === "failed" ? `<span class="pill bad" title="${esc(a.error)}">failed</span>` : ""}
        ${a.status === "in_progress" ? `<span class="pill warn">applying…</span>` : ""}
        ${a.status === "skipped" ? `<span class="pill">skipped</span>` : ""}
        ${since != null ? `<span class="muted">applied ${since === 0 ? "today" : since + "d ago"}</span>` : ""}
        ${due ? `<span class="pill warn">follow up</span>` : ""}
        <span class="spacer"></span>
        ${["queued", "failed"].includes(a.status) ? `<button class="sm primary" data-apply="${a.job_id}">Apply</button>` : ""}
        ${a.status === "interview" ? `<button class="sm" data-prep="${a.job_id}">Prep</button>` : ""}
        ${a.resume_id ? `<a class="btn sm" href="/api/resumes/${a.resume_id}/file/pdf" target="_blank" title="Resume sent" draggable="false">CV</a>` : ""}
      </div></div>`;
  }

  function renderBoard(apps) {
    $("#apps").innerHTML = `<div class="board">${BOARD.map((col) => {
      const items = apps.filter((a) => col.statuses.includes(a.status));
      return `<section class="kcol" data-col="${col.key}" data-drop="${col.drop}"><header><b>${col.title}</b><span class="pill">${items.length}</span></header>
        <div class="kitems">${items.map(card).join("") || `<div class="kempty">${col.key === "todo" ? "Click Apply on a job, or queue several from Jobs." : "Drag a card here"}</div>`}</div></section>`;
    }).join("")}</div>`;
    const board = $(".board");
    board.addEventListener("dragstart", (e) => {
      const c = e.target.closest?.(".kcard");
      if (!c) return;
      e.dataTransfer.setData("application/x-jobpilot-app", c.dataset.id);
      e.dataTransfer.effectAllowed = "move";
      c.classList.add("dragging");
    });
    board.addEventListener("dragend", (e) => e.target.closest?.(".kcard")?.classList.remove("dragging"));
    board.addEventListener("dragover", (e) => { const col = e.target.closest(".kcol"); if (col) { e.preventDefault(); $$(".kcol").forEach((c) => c.classList.toggle("over", c === col)); } });
    board.addEventListener("dragleave", (e) => { if (!e.relatedTarget || !board.contains(e.relatedTarget)) $$(".kcol").forEach((c) => c.classList.remove("over")); });
    board.addEventListener("drop", (e) => {
      const col = e.target.closest(".kcol");
      $$(".kcol").forEach((c) => c.classList.remove("over"));
      const id = e.dataTransfer.getData("application/x-jobpilot-app");
      const a = apps.find((x) => String(x.id) === id);
      if (!col || !a || BOARD.find((b) => b.key === col.dataset.col).statuses.includes(a.status)) return;
      e.preventDefault();
      setStatus(id, col.dataset.drop);
    });
    board.addEventListener("click", (e) => {
      const open = e.target.closest("[data-open]"), apply = e.target.closest("[data-apply]"), prep = e.target.closest("[data-prep]");
      if (open) { e.preventDefault(); openJob(Number(open.dataset.open), load); }
      if (apply) openApply(Number(apply.dataset.apply), load);
      if (prep) openPrep(Number(prep.dataset.prep));
    });
  }

  function renderTable(apps) {
    $("#apps").innerHTML = `<div class="card" style="padding:0;overflow-x:auto"><table class="tbl"><thead><tr><th>Job</th><th>Status</th><th>Resume</th><th>Updated</th><th>Notes</th><th></th></tr></thead><tbody id="rows">
      ${apps.length ? apps.map((a) => `<tr data-id="${a.id}">
      <td><a href="#" data-open="${a.job_id}"><b>${esc(a.job?.title ?? "(deleted)")}</b></a><div class="small muted">${esc(a.job?.company ?? "")} · ${esc(a.job?.source ?? "")}</div>
        ${a.error ? `<div class="small" style="color:var(--bad)">${esc(a.error)}</div>` : ""}</td>
      <td>${a.status === "preparing" ? `<span class="pill accent"><span class="spinner"></span> preparing</span>` :
        `<select data-status aria-label="Status">${APP_STATUSES.map((st) => `<option ${st === a.status ? "selected" : ""}>${st}</option>`).join("")}</select>`}</td>
      <td>${a.resume_id ? `<a href="#/resumes/${a.resume_id}">View</a> · <a href="/api/resumes/${a.resume_id}/file/pdf">PDF</a>` : "—"}</td>
      <td class="small muted">${fmtDate(a.applied_at || a.updated_at)}</td>
      <td><textarea data-notes rows="1" style="min-height:32px;min-width:160px">${esc(a.notes)}</textarea></td>
      <td><div class="row" style="flex-wrap:nowrap">${["queued", "failed"].includes(a.status) ? `<button class="sm primary" data-apply="${a.job_id}">Apply</button>` : ""}
        ${a.job?.url ? `<a class="btn sm" href="${esc(a.job.url)}" target="_blank" rel="noopener noreferrer">Open ↗</a>` : ""}
        <button class="sm ghost danger" data-remove aria-label="Remove">✕</button></div></td></tr>`).join("")
      : `<tr><td colspan="6" class="empty">Nothing here yet. Click Apply on a job, or select several on the Jobs page and queue them.</td></tr>`}</tbody></table></div>`;
    const rows = $("#rows");
    rows.onchange = async (e) => {
      const tr = e.target.closest("tr[data-id]");
      if (!tr) return;
      try {
        if (e.target.matches("[data-status]")) { await api(`/api/applications/${tr.dataset.id}`, { method: "PATCH", body: { status: e.target.value } }); toast("Status updated"); }
        if (e.target.matches("[data-notes]")) { await api(`/api/applications/${tr.dataset.id}`, { method: "PATCH", body: { notes: e.target.value } }); toast("Notes saved"); }
      } catch (err) { toast(err.message, true); load(); }
    };
    rows.onclick = async (e) => {
      const open = e.target.closest("[data-open]"), apply = e.target.closest("[data-apply]");
      if (open) { e.preventDefault(); return openJob(Number(open.dataset.open), load); }
      if (apply) return openApply(Number(apply.dataset.apply), load);
      if (e.target.closest("[data-remove]")) {
        const tr = e.target.closest("tr");
        if (!confirm("Remove this application from the tracker?")) return;
        await api(`/api/applications/${tr.dataset.id}`, { method: "DELETE" }).catch((err) => toast(err.message, true));
        load();
      }
    };
  }
  await load();
};

// --------------------------------------------------------------------------- resumes

routes.resumes = async (id) => {
  if (id) return resumeEditor(Number(id));
  const list = await api("/api/resumes");
  view.innerHTML = `<div class="page-head"><div><h1>Resumes</h1><p class="muted">Every tailored version, with its keyword coverage against the job.</p></div>
    <button class="primary" id="general">Generate general resume</button></div>
    <div class="card" style="padding:0;overflow-x:auto"><table class="tbl"><thead><tr><th>Resume</th><th>Method</th><th>JD coverage</th><th>Created</th><th></th></tr></thead><tbody>
    ${list.length ? list.map((r) => `<tr><td><a href="#/resumes/${r.id}"><b>${esc(r.title)}</b></a></td><td><span class="pill ${r.method === "llm" ? "accent" : ""}">${r.method === "llm" ? "AI" : esc(r.method)}</span></td>
      <td>${r.job_id ? `${Math.round((r.keyword_report.coverage || 0) * 100)}%` : "—"}</td><td class="small muted">${fmtDate(r.created_at)}</td>
      <td class="row"><a class="btn sm" href="/api/resumes/${r.id}/file/pdf">PDF</a><a class="btn sm" href="/api/resumes/${r.id}/file/docx">DOCX</a></td></tr>`).join("")
      : `<tr><td colspan="5" class="empty">No resumes yet. Open a job and click “Tailor resume”.</td></tr>`}</tbody></table></div>`;
  $("#general").onclick = (e) => busy(e.currentTarget, async () => {
    const r = await api("/api/resumes", { method: "POST", body: { job_id: null } });
    location.hash = `#/resumes/${r.id}`;
  });
};

async function resumeEditor(id) {
  const r = await api(`/api/resumes/${id}`);
  const doc = r.doc;
  const kr = r.keyword_report || {};
  let version = Date.now();
  const render = () => {
    view.innerHTML = `<div class="page-head"><div><a href="#/resumes" class="small">← Resumes</a><h1>${esc(r.title)}</h1>
      <p class="muted small">${r.method === "llm" ? "Tailored by Claude, verified against your profile" : r.method === "heuristic" ? "Tailored by the built-in engine" : "General resume"} · edit anything, then save to re-render.</p></div>
      <div class="row"><a class="btn" href="/api/resumes/${id}/file/docx">DOCX</a><a class="btn" href="/api/resumes/${id}/file/pdf">Download PDF</a>
      <button class="danger" id="del">Delete</button><button class="primary" id="save">Save &amp; re-render</button></div></div>
      ${r.notes.length ? `<div class="card small" style="border-color:var(--warn)"><b>Safety checks:</b> ${r.notes.map(esc).join(" · ")}</div>` : ""}
      <div class="split" style="margin-top:16px"><div id="ed">
        ${r.job_id ? `<div class="card"><div class="row"><h3 class="spacer">Job description coverage</h3><span class="pill ${kr.coverage >= .7 ? "good" : kr.coverage >= .45 ? "warn" : "bad"}">${Math.round((kr.coverage || 0) * 100)}%</span></div>
          <div class="chips">${(kr.matched || []).map((s) => `<span class="pill good">${esc(s)}</span>`).join("")}</div>
          ${kr.missing_in_resume_but_in_profile?.length ? `<p class="small" style="margin-top:8px">In your profile but not on this resume — consider adding: ${kr.missing_in_resume_but_in_profile.map((s) => `<span class="pill warn">${esc(s)}</span>`).join(" ")}</p>` : ""}
          ${kr.missing_entirely?.length ? `<p class="small muted" style="margin-top:8px">Genuine gaps (not in your profile, so not claimed): ${kr.missing_entirely.map(esc).join(", ")}</p>` : ""}</div>` : ""}
        <div class="card">${inp("Headline", "headline", doc)}<div style="margin-top:10px">${area("Summary", "summary", doc, 4)}</div>
          <label class="field" style="margin-top:10px">Skills (order matters)</label>${tagInput("skills", doc)}</div>
        <div class="card"><h3>Experience</h3>${doc.experience.map((e, i) => `<div class="section-edit"><b>${esc(e.title)}</b> <span class="muted">· ${esc(e.company)}</span>
          ${e.bullets.map((b, k) => `<div class="bullet-row" style="margin-top:6px"><textarea rows="2" data-bind="experience.${i}.bullets.${k}">${esc(b)}</textarea>
          <button class="sm ghost" data-del="experience.${i}.bullets.${k}" aria-label="Remove">✕</button></div>`).join("")}</div>`).join("")}</div>
        ${doc.projects.length ? `<div class="card"><h3>Projects</h3>${doc.projects.map((p, i) => `<div class="section-edit"><div class="row"><b class="spacer">${esc(p.name)}</b>
          <button class="sm danger" data-del="projects.${i}">Remove</button></div>${p.bullets.map((b, k) => `<div class="bullet-row" style="margin-top:6px"><textarea rows="2" data-bind="projects.${i}.bullets.${k}">${esc(b)}</textarea>
          <button class="sm ghost" data-del="projects.${i}.bullets.${k}" aria-label="Remove">✕</button></div>`).join("")}</div>`).join("")}</div>` : ""}
        <div class="card"><div class="row"><h3 class="spacer">Cover letter</h3><button class="sm" id="cl">${r.cover_letter ? "Regenerate" : "Generate"}</button></div>
          <textarea id="cltext" rows="10">${esc(r.cover_letter)}</textarea></div>
      </div><div class="preview"><iframe title="Resume preview" src="/api/resumes/${id}/file/pdf?inline=true&v=${version}"></iframe></div></div>`;
    const ed = $("#ed");
    bindInputs(ed, () => doc);
    bindTags(ed, () => doc, (path) => refreshTags(ed, () => doc, path));
    ed.addEventListener("click", (e) => {
      const del = e.target.closest("[data-del]");
      if (!del) return;
      const parts = del.dataset.del.split("."); const idx = Number(parts.pop());
      getPath(doc, parts.join(".")).splice(idx, 1); render();
    });
    $("#save").onclick = (e) => busy(e.currentTarget, async () => {
      const out = await api(`/api/resumes/${id}`, { method: "PUT", body: { doc, cover_letter: $("#cltext").value } });
      Object.assign(r, out); Object.assign(kr, out.keyword_report); r.cover_letter = out.cover_letter;
      version = Date.now(); toast("Saved and re-rendered"); render();
    });
    $("#cl").onclick = (e) => busy(e.currentTarget, async () => {
      const out = await api(`/api/resumes/${id}/cover-letter`, { method: "POST" });
      $("#cltext").value = out.cover_letter; r.cover_letter = out.cover_letter;
    });
    $("#del").onclick = async () => {
      if (!confirm("Delete this resume?")) return;
      await api(`/api/resumes/${id}`, { method: "DELETE" }); location.hash = "#/resumes";
    };
  };
  render();
}

// --------------------------------------------------------------------------- answers

routes.answers = async () => {
  const list = await api("/api/answers");
  view.innerHTML = `<div class="page-head"><div><h1>Saved answers</h1>
    <p class="muted">Screening answers you approved. The extension reuses them on every application form. Sensitive questions are only ever answered from here.</p></div></div>
    <div class="card"><form id="af" class="row"><input name="question" placeholder="Question, e.g. Are you willing to work from office 5 days?" required style="flex:2">
      <input name="answer" placeholder="Answer" required style="flex:1"><button class="primary">Add</button></form></div>
    <div class="card" style="padding:0;overflow-x:auto"><table class="tbl"><thead><tr><th>Question</th><th>Answer</th><th>Used</th><th></th></tr></thead><tbody id="rows">
    ${list.length ? list.map((a) => `<tr data-id="${a.id}"><td><input data-f="question" value="${esc(a.question)}"></td><td><textarea data-f="answer" rows="1" style="min-height:34px">${esc(a.answer)}</textarea></td>
      <td class="small muted">${a.uses}×</td><td><button class="sm ghost danger" data-del aria-label="Delete">✕</button></td></tr>`).join("")
      : `<tr><td colspan="4" class="empty">Answers you give while applying are saved here automatically.</td></tr>`}</tbody></table></div>`;
  $("#af").onsubmit = (e) => { e.preventDefault(); busy(e.submitter, async () => { await api("/api/answers", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); router(); }); };
  $("#rows").onchange = async (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (!tr) return;
    await api(`/api/answers/${tr.dataset.id}`, { method: "PUT", body: { question: $('[data-f="question"]', tr).value, answer: $('[data-f="answer"]', tr).value } })
      .then(() => toast("Saved")).catch((err) => toast(err.message, true));
  };
  $("#rows").onclick = async (e) => {
    if (!e.target.closest("[data-del]")) return;
    await api(`/api/answers/${e.target.closest("tr").dataset.id}`, { method: "DELETE" }); router();
  };
};

// --------------------------------------------------------------------------- settings

routes.settings = async () => {
  const [s, mcp, directory] = await Promise.all([api("/api/settings"), api("/api/mcp-config"), api("/api/sources/directory")]);
  const al = s.alerts;
  const em = s.email_import;
  const PLATFORMS = [["linkedin", "LinkedIn"], ["naukri", "Naukri"], ["indeed", "Indeed"], ["glassdoor", "Glassdoor"], ["foundit", "foundit"],
    ["instahyre", "Instahyre"], ["iimjobs", "iimjobs"], ["hirist", "hirist"], ["wellfound", "Wellfound"], ["cutshort", "Cutshort"]];
  const followedCount = ["greenhouse", "lever", "ashby", "smartrecruiters", "workday"].reduce((n, k) => n + (s.sources[k] || []).length, 0);
  const src = s.sources;
  const list = (arr) => esc((arr || []).join(", "));
  view.innerHTML = `<div class="page-head"><div><h1>Settings</h1><p class="muted">Stored locally in your JobPilot database.</p></div>
    <button class="primary" id="save">Save settings</button></div>
    <div class="card"><h2>AI (Claude)</h2>
      <p class="small muted">Optional. With a key, resume parsing, tailoring, cover letters and screening answers use Claude; every AI output is checked against your profile so nothing is invented. Without one, the built-in engine is used.</p>
      <div class="form-grid">
        <label class="field">Anthropic API key ${s.llm.api_key_source === "env" ? `<span class="pill good">from ANTHROPIC_API_KEY</span>` : ""}
          <input type="password" id="llm-key" value="${esc(s.llm.api_key)}" placeholder="sk-ant-…" autocomplete="off" ${s.llm.api_key_source === "env" ? "disabled" : ""}></label>
        <label class="field">Model<select id="llm-model">${[["claude-opus-5-5", "Claude Opus 5.5 (best quality)"], ["claude-sonnet-5-5", "Claude Sonnet 5.5 (faster, cheaper)"], ["claude-haiku-5-5", "Claude Haiku 5.5 (cheapest)"]]
          .map(([v, l]) => `<option value="${v}" ${s.llm.model === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label class="field">Effort<select id="llm-effort">${["low", "medium", "high"].map((v) => `<option ${s.llm.effort === v ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        <label class="check" style="align-self:end"><input type="checkbox" id="llm-on" ${s.llm.enabled ? "checked" : ""}> AI features enabled</label>
      </div><div class="row" style="margin-top:10px"><button class="sm" id="test">Test connection</button></div></div>

    <div class="card"><h2>Where jobs come from</h2>
      <p class="small muted">JobPilot gathers recent openings from every channel into one ranked feed: company <b>career pages</b> (fetched directly),
        <b>LinkedIn, Naukri, Indeed and more</b> through the job-alert emails they already send you, the pages you browse with the extension,
        and the aggregators below. Click <b>Fetch new jobs</b> on the Jobs page, or let it run every few hours.</p></div>

    <div class="card"><div class="row"><h2 class="spacer">Company career pages</h2><span class="pill accent" id="follow-count">${followedCount} followed</span></div>
      <p class="small muted">Follow companies to pull every new opening straight from their careers site. The list below was checked live; the number is how many roles they had open in India.</p>
      <div class="row"><input type="search" id="dir-q" placeholder="Filter companies…" style="max-width:260px">
        <label class="check"><input type="checkbox" id="dir-india" checked> Hiring in India</label><span class="spacer"></span>
        <button class="sm" id="dir-all">Follow all shown</button></div>
      <div class="chips" id="dir" style="margin-top:10px;max-height:260px;overflow:auto"></div>
      <div class="section-edit" style="margin-top:12px"><b>Any other company</b>
        <p class="small muted" style="margin:4px 0 8px">Paste its careers page or any job link. Works with Greenhouse, Lever, Ashby, SmartRecruiters and Workday (used by most large employers).</p>
        <div class="row"><input id="det-url" placeholder="https://careers.example.com  or  https://example.wd1.myworkdayjobs.com/External" style="flex:1">
          <button class="primary sm" id="det-go">Find job board</button></div>
        <div id="det-out" class="small" style="margin-top:8px"></div></div>
      <div class="form-grid" style="margin-top:12px">
        <label class="field">Search large employers for<input id="sr-kw" value="${list(src.search.keywords)}" placeholder="defaults to your target titles"></label>
        <label class="field">Country (SmartRecruiters)<input id="sr-c" value="${esc(src.search.country)}" placeholder="in"></label>
        <label class="field">Max jobs per large employer<input id="sr-max" type="number" min="5" max="200" value="${esc(src.search.max_per_company)}"></label></div>
      <details style="margin-top:10px"><summary class="small">Advanced: edit followed boards directly</summary>
        <div class="grid cols-3" style="margin-top:8px">
          <label class="field">Greenhouse boards<input id="gh" value="${list(src.greenhouse)}"></label>
          <label class="field">Lever companies<input id="lv" value="${list(src.lever)}"></label>
          <label class="field">Ashby boards<input id="ab" value="${list(src.ashby)}"></label>
          <label class="field">SmartRecruiters (case-sensitive)<input id="srl" value="${list(src.smartrecruiters)}"></label>
          <label class="field" style="grid-column:span 2">Workday careers URLs<input id="wd" value="${list(src.workday)}"></label></div></details></div>

    <div class="card"><h2>LinkedIn, Naukri, Indeed &amp; more — via job-alert emails</h2>
      <p class="small muted">These sites don't offer an API and forbid scraping, but they email you new jobs. JobPilot reads those alert emails
        (read-only — nothing is marked, moved or deleted) and adds every job to your feed.</p>
      <ol class="steps small"><li>Create job alerts on <a href="https://www.linkedin.com/jobs/" target="_blank" rel="noopener">LinkedIn</a>, <a href="https://www.naukri.com/" target="_blank" rel="noopener">Naukri</a>,
        <a href="https://in.indeed.com/" target="_blank" rel="noopener">Indeed</a> etc. for the roles you want (daily emails).</li>
        <li>For Gmail, create an <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener">App Password ↗</a> (the same one as for alerts works) and enter it below.</li>
        <li>Save, then <b>Test mailbox</b> to see what it finds.</li></ol>
      <div class="form-grid">
        <label class="check" style="align-self:end"><input type="checkbox" id="em-on" ${em.enabled ? "checked" : ""}> Import from alert emails</label>
        <label class="field">Email / username<input id="em-user" value="${esc(em.username)}" placeholder="you@gmail.com" autocomplete="off"></label>
        <label class="field">App Password<input id="em-pass" type="password" value="${esc(em.password)}" autocomplete="off"></label>
        <label class="field">IMAP server<input id="em-host" value="${esc(em.host)}"></label>
        <label class="field">Port<input id="em-port" type="number" value="${esc(em.port)}"></label>
        <label class="field">Folder<input id="em-folder" value="${esc(em.folder)}"></label>
        <label class="field">Look back (days)<input id="em-days" type="number" min="1" max="60" value="${esc(em.days)}"></label></div>
      <div class="chips" style="margin-top:10px">${PLATFORMS.map(([k, l]) => `<label class="check" style="margin-right:10px"><input type="checkbox" data-plat="${k}" ${em.platforms.includes(k) ? "checked" : ""}> ${l}</label>`).join("")}</div>
      <div class="row" style="margin-top:10px"><button class="sm" id="em-test">Test mailbox</button><span id="em-out" class="small muted"></span></div></div>

    <div class="card"><h2>Other job feeds</h2>
      <div class="section-edit" style="margin-top:14px"><label class="check"><input type="checkbox" id="rm-on" ${src.remotive.enabled ? "checked" : ""}> <b>Remotive</b> (remote jobs worldwide)</label>
        <label class="field" style="margin-top:8px">Search queries<input id="rm-q" value="${list(src.remotive.queries)}" placeholder="python, backend"></label></div>
      <div class="section-edit"><label class="check"><input type="checkbox" id="az-on" ${src.adzuna.enabled ? "checked" : ""}> <b>Adzuna</b> aggregator — includes listings from many Indian job boards. <a href="https://developer.adzuna.com/" target="_blank" rel="noopener">Free API key ↗</a></label>
        <div class="form-grid" style="margin-top:8px"><label class="field">App ID<input id="az-id" value="${esc(src.adzuna.app_id)}"></label>
          <label class="field">App key<input id="az-key" type="password" value="${esc(src.adzuna.app_key)}" autocomplete="off"></label>
          <label class="field">Country<select id="az-c">${["in", "gb", "us", "ca", "au", "sg", "de", "nl", "fr"].map((c) => `<option ${src.adzuna.country === c ? "selected" : ""}>${c}</option>`).join("")}</select></label>
          <label class="field">Where<input id="az-w" value="${esc(src.adzuna.where)}" placeholder="Bengaluru"></label>
          <label class="field">Pages per query<input id="az-p" type="number" min="1" max="10" value="${esc(src.adzuna.max_pages)}"></label></div>
        <label class="field" style="margin-top:8px">Search queries<input id="az-q" value="${list(src.adzuna.queries)}" placeholder="python developer, backend engineer"></label></div>
      <div class="form-grid" style="margin-top:12px"><label class="field">Auto-fetch every (hours, 0 = off)<input id="int" type="number" min="0" step="1" value="${esc(s.ingest.interval_hours)}"></label>
        <label class="field">Forget unsaved jobs after (days)<input id="age" type="number" min="0" value="${esc(s.ingest.max_age_days)}"></label></div></div>

    <div class="card"><h2>New-match email alerts</h2>
      <p class="small muted">After each fetch, JobPilot emails you newly found, still-open jobs that score at least your threshold — one digest, never the same job twice.
        Gmail: turn on 2-Step Verification, create an <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener">App Password ↗</a> and use it below (not your normal password).</p>
      <div class="form-grid">
        <label class="check" style="align-self:end"><input type="checkbox" id="al-on" ${al.enabled ? "checked" : ""}> Alerts enabled</label>
        <label class="field">Send to<input id="al-to" type="email" value="${esc(al.email_to)}" placeholder="you@gmail.com"></label>
        <label class="field">Minimum score<input id="al-min" type="number" min="0" max="100" step="5" value="${esc(al.min_score)}"></label>
        <label class="field">Max jobs per email<input id="al-max" type="number" min="1" max="50" value="${esc(al.max_items)}"></label>
        <label class="field">SMTP server<input id="al-host" value="${esc(al.smtp.host)}" placeholder="smtp.gmail.com"></label>
        <label class="field">Port<input id="al-port" type="number" value="${esc(al.smtp.port)}"></label>
        <label class="field">Username<input id="al-user" value="${esc(al.smtp.username)}" placeholder="you@gmail.com" autocomplete="off"></label>
        <label class="field">Password / App Password<input id="al-pass" type="password" value="${esc(al.smtp.password)}" autocomplete="off"></label>
      </div>
      <div class="row" style="margin-top:10px"><button class="sm" id="al-test">Send test email</button>
        <span class="small muted">Save settings first. Turning alerts on won't email jobs you already have.</span></div></div>

    <div class="card"><h2>AI assistant (MCP)</h2>
      <p class="small muted">Connect Claude (or any MCP client) to JobPilot, then just ask: “show my best new remote matches”, “tailor a resume for job 42”, “queue the top 5”, “mark Acme as interview”.
        It reads the same local database; JobPilot doesn't need to be open. You still submit applications yourself.</p>
      <p class="small"><b>Claude Code</b> — run in a terminal:</p>
      <div class="row"><div class="token mono spacer" id="mcp-cc">${esc(mcp.claude_code)}</div><button class="sm" data-copy="mcp-cc">Copy</button></div>
      <p class="small" style="margin-top:10px"><b>Claude Desktop</b> — Settings → Developer → Edit Config, and add to <code>claude_desktop_config.json</code>, then restart Claude:</p>
      <div class="row" style="align-items:flex-start"><pre class="token mono spacer" id="mcp-cd" style="margin:0;white-space:pre-wrap">${esc(JSON.stringify(mcp.claude_desktop, null, 2))}</pre><button class="sm" data-copy="mcp-cd">Copy</button></div></div>

    <div class="card"><h2>Browser extension (Naukri, Indeed, company ATS forms)</h2>
      <ol class="steps"><li>Open <code>chrome://extensions</code>, turn on <b>Developer mode</b>, click <b>Load unpacked</b> and choose the <code>extension</code> folder of JobPilot.</li>
        <li>Click the JobPilot icon → paste this server address and token → <b>Connect</b>.</li>
        <li>Browse Naukri/Indeed as usual. Each listing gets a match badge, and jobs are captured here automatically.</li>
        <li>Queue jobs here, then press <b>Start applying</b> in the extension. It opens each job, fills the form, attaches the tailored resume and waits for you to review and submit.</li></ol>
      <div class="grid cols-2"><label class="field">Server<div class="token mono">${esc(location.origin)}</div></label>
      <label class="field">Extension token<div class="row"><div class="token mono spacer" id="tok">${esc(s.extension_token)}</div>
        <button class="sm" id="copy">Copy</button><button class="sm" id="rotate">Rotate</button></div></label></div></div>`;

  const split = (v) => v.split(",").map((x) => x.trim()).filter(Boolean);
  $("#save").onclick = (e) => busy(e.currentTarget, async () => {
    const body = {
      llm: { model: $("#llm-model").value, effort: $("#llm-effort").value, enabled: $("#llm-on").checked },
      sources: { greenhouse: split($("#gh").value), lever: split($("#lv").value), ashby: split($("#ab").value),
        smartrecruiters: split($("#srl").value), workday: split($("#wd").value),
        search: { keywords: split($("#sr-kw").value), country: $("#sr-c").value.trim().toLowerCase(), max_per_company: Number($("#sr-max").value) || 40 },
        remotive: { enabled: $("#rm-on").checked, queries: split($("#rm-q").value) },
        adzuna: { enabled: $("#az-on").checked, app_id: $("#az-id").value.trim(), app_key: $("#az-key").value.trim(), country: $("#az-c").value,
          where: $("#az-w").value.trim(), queries: split($("#az-q").value), max_pages: Number($("#az-p").value) || 1 } },
      ingest: { interval_hours: Number($("#int").value) || 0, max_age_days: Number($("#age").value) || 0 },
      email_import: { enabled: $("#em-on").checked, username: $("#em-user").value.trim(), password: $("#em-pass").value,
        host: $("#em-host").value.trim(), port: Number($("#em-port").value) || 993, folder: $("#em-folder").value.trim() || "INBOX",
        days: Number($("#em-days").value) || 3, platforms: $$("[data-plat]").filter((c) => c.checked).map((c) => c.dataset.plat) },
      alerts: { enabled: $("#al-on").checked, email_to: $("#al-to").value.trim(), min_score: Number($("#al-min").value) || 0,
        max_items: Number($("#al-max").value) || 15,
        smtp: { host: $("#al-host").value.trim(), port: Number($("#al-port").value) || 587, username: $("#al-user").value.trim(),
          password: $("#al-pass").value } },
    };
    if (!$("#llm-key").disabled) body.llm.api_key = $("#llm-key").value.trim();
    await api("/api/settings", { method: "PUT", body });
    toast("Settings saved"); refreshAiBadge(); router();
  });
  $("#test").onclick = (e) => busy(e.currentTarget, async () => { const r = await api("/api/settings/test-llm", { method: "POST" }); toast(`Connected to ${r.model}`); });
  // Company directory: follow/unfollow immediately.
  const TYPE_LABEL = { greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby", smartrecruiters: "SmartRecruiters", workday: "Workday" };
  const syncAdvanced = (lists) => { for (const [k, id] of [["greenhouse", "gh"], ["lever", "lv"], ["ashby", "ab"], ["smartrecruiters", "srl"], ["workday", "wd"]]) $("#" + id).value = lists[k].join(", ");
    $("#follow-count").textContent = `${Object.values(lists).reduce((n, l) => n + l.length, 0)} followed`; };
  const shown = () => { const q = $("#dir-q").value.toLowerCase(); return directory.filter((c) => (!$("#dir-india").checked || c.india > 0) && (!q || c.name.toLowerCase().includes(q))); };
  const renderDir = () => {
    $("#dir").innerHTML = shown().map((c) => `<button class="sm ${c.followed ? "primary" : ""}" data-follow="${directory.indexOf(c)}" title="${esc(TYPE_LABEL[c.type])}">
      ${c.followed ? "✓ " : "+ "}${esc(c.name)}${c.india ? ` <span class="muted small">${c.india}</span>` : ""}</button>`).join("") || `<span class="muted small">No match.</span>`;
  };
  const toggle = async (c, on) => {
    const lists = await api(`/api/sources/${on ? "follow" : "unfollow"}`, { method: "POST", body: { type: c.type, id: c.id } });
    c.followed = on; syncAdvanced(lists);
  };
  renderDir();
  $("#dir-q").oninput = renderDir; $("#dir-india").onchange = renderDir;
  $("#dir").onclick = async (e) => { const b = e.target.closest("[data-follow]"); if (!b) return; const c = directory[b.dataset.follow];
    try { await toggle(c, !c.followed); renderDir(); } catch (err) { toast(err.message, true); } };
  $("#dir-all").onclick = (e) => busy(e.currentTarget, async () => { for (const c of shown().filter((x) => !x.followed)) await toggle(c, true);
    renderDir(); toast("Following — click Fetch new jobs on the Jobs page"); });
  $("#det-go").onclick = (e) => busy(e.currentTarget, async () => {
    const d = await api("/api/sources/detect", { method: "POST", body: { url: $("#det-url").value } });
    $("#det-out").innerHTML = `Found <b>${esc(d.name)}</b> on ${esc(TYPE_LABEL[d.type])} with ${d.jobs} open role${d.jobs === 1 ? "" : "s"}. <button class="sm primary" id="det-follow">Follow</button>`;
    $("#det-follow").onclick = async () => { const lists = await api("/api/sources/follow", { method: "POST", body: { type: d.type, id: d.id } });
      syncAdvanced(lists); $("#det-out").textContent = `Following ${d.name}.`; };
  });
  $("#em-test").onclick = (e) => busy(e.currentTarget, async () => {
    const r = await api("/api/sources/email/test", { method: "POST" });
    const per = Object.entries(r.per_platform).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join(", ");
    $("#em-out").innerHTML = r.found ? `Found <b>${r.found}</b> jobs (${esc(per)}), e.g. ${r.sample.slice(0, 3).map((j) => esc(`${j.title} — ${j.company || j.source}`)).join("; ")}`
      : "Connected, but no job-alert emails found in that period. Create alerts on LinkedIn/Naukri/Indeed, or look back more days.";
  });
  $("#al-test").onclick = (e) => busy(e.currentTarget, async () => { await api("/api/alerts/test", { method: "POST" }); toast("Test email sent — check your inbox"); });
  view.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = () =>
    navigator.clipboard.writeText($("#" + b.dataset.copy).textContent).then(() => toast("Copied"))));
  $("#copy").onclick = () => navigator.clipboard.writeText($("#tok").textContent).then(() => toast("Token copied"));
  $("#rotate").onclick = async () => {
    if (!confirm("Rotate the token? The extension will need the new one.")) return;
    const r = await api("/api/settings/rotate-token", { method: "POST" }); $("#tok").textContent = r.extension_token; toast("Token rotated");
  };
};

// After `git pull` the browser gets the new UI immediately, but a running
// server keeps the old Python code until restarted. Detect and say so.
const RESTART_HELP = "JobPilot was updated, but the running server is still the old version. Close the JobPilot window (or press Ctrl+C), start it again with run.bat / ./run.sh, then refresh this page.";
async function checkServerVersion() {
  try {
    const h = await (await fetch("/healthz", { cache: "no-store" })).json();
    if (h.version === APP_VERSION) return true;
  } catch { /* offline: other calls will report it */ }
  if (!$("#version-banner")) {
    const b = document.createElement("div");
    b.id = "version-banner";
    b.className = "version-banner";
    b.textContent = RESTART_HELP;
    document.body.prepend(b);
  }
  return false;
}

checkServerVersion();
refreshAiBadge();
router();
