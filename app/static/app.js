// JobPilot web UI — no build step, no dependencies.
// All dynamic text goes through esc(); job descriptions come from third parties.

// Must equal __version__ in app/__init__.py (checked by a test).
const APP_VERSION = "1.1.0";

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

// --------------------------------------------------------------------------- dashboard

routes.dashboard = async () => {
  const [s, top, settings] = await Promise.all([api("/api/stats"), api("/api/jobs?limit=6&sort=score"), api("/api/settings")]);
  const a = s.applications || {};
  const src = settings.sources;
  const hasSources = src.greenhouse.length || src.lever.length || src.ashby.length || src.remotive.enabled || src.adzuna.enabled;
  const li = s.last_ingest;
  view.innerHTML = `
    <div class="page-head"><div><h1>Dashboard</h1><p class="muted">Your job search at a glance.</p></div>
      <div class="row"><button id="fetch" class="primary">Fetch new jobs</button></div></div>
    <div class="grid cols-4">
      <div class="card stat"><div class="num">${s.jobs}</div><div class="lbl">Jobs tracked</div></div>
      <div class="card stat"><div class="num">${s.strong_matches}</div><div class="lbl">Strong matches (70+)</div></div>
      <div class="card stat"><div class="num">${(a.queued || 0) + (a.preparing || 0) + (a.in_progress || 0)}</div><div class="lbl">In apply queue</div></div>
      <div class="card stat"><div class="num">${(a.applied || 0) + (a.interview || 0) + (a.offer || 0) + (a.rejected || 0)}</div><div class="lbl">Applied</div></div>
    </div>
    <div class="grid cols-2" style="margin-top:16px">
      <div class="card"><h2>Setup</h2><ul class="checklist">
        <li><span class="dot ${s.profile_ready ? "on" : ""}"></span><div class="spacer">Profile with your experience &amp; skills</div><a href="#/profile">${s.profile_ready ? "Edit" : "Upload resume"}</a></li>
        <li><span class="dot ${s.ai_enabled ? "on" : ""}"></span><div class="spacer">Claude API key for AI tailoring <span class="muted small">(optional)</span></div><a href="#/settings">Settings</a></li>
        <li><span class="dot ${hasSources ? "on" : ""}"></span><div class="spacer">Job sources (company boards, Adzuna, Remotive)</div><a href="#/settings">Configure</a></li>
        <li><span class="dot"></span><div class="spacer">Browser extension for Naukri &amp; Indeed</div><a href="#/settings">Install guide</a></li>
      </ul></div>
      <div class="card"><h2>Last fetch</h2>${li ? `
        <p>${fmtDate(li.at)} — <b>${li.new}</b> new of ${li.fetched} fetched from ${li.sources} source(s).</p>
        ${li.errors?.length ? `<p class="small" style="color:var(--bad)">${li.errors.map(esc).join("<br>")}</p>` : ""}`
        : `<p class="muted">No fetch yet. Add sources in Settings, or capture jobs from Naukri/Indeed with the extension.</p>`}
        <p class="small muted">Interviews: ${a.interview || 0} · Offers: ${a.offer || 0} · Resumes generated: ${s.resumes}</p></div>
    </div>
    <div class="card" style="margin-top:16px"><div class="row"><h2 class="spacer">Top matches</h2><a href="#/jobs">All jobs →</a></div>
      <div class="job-list">${top.items.length ? top.items.map((j) => jobRow(j)).join("") : `<div class="empty">No jobs yet.</div>`}</div></div>`;
  $("#fetch").onclick = (e) => busy(e.currentTarget, async () => {
    const r = await api("/api/ingest/run", { method: "POST" });
    if (r.status === "busy") return toast("A fetch is already running.");
    toast(`Fetched ${r.fetched} jobs, ${r.new} new.${r.errors.length ? ` ${r.errors.length} source error(s).` : ""}`, r.errors.length > 0 && !r.fetched);
    router();
  });
  $(".job-list").addEventListener("click", (e) => { const el = e.target.closest("[data-job]"); if (el) openJob(Number(el.dataset.job)); });
};

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
    <div class="side"><span class="pill">${esc(j.source)}</span>
      ${j.application ? statusPill(j.application.status) : j.status === "shortlisted" ? `<span class="pill accent">shortlisted</span>` : ""}
      ${d.excluded ? `<span class="pill bad">excluded</span>` : ""}</div>
  </div>`;
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
    <div class="row" style="margin:16px 0">
      <button class="primary" data-act="queue">Tailor resume &amp; add to queue</button>
      <button data-act="resume">Tailor resume only</button>
      <button data-act="assess">AI fit check</button>
      <span class="spacer"></span>
      <button class="sm" data-act="shortlist">${j.status === "shortlisted" ? "Unshortlist" : "Shortlist"}</button>
      <button class="sm" data-act="hide">${j.status === "hidden" ? "Unhide" : "Hide"}</button>
    </div>
    ${d.reasons?.length ? `<p class="small">${d.reasons.map(esc).join(" · ")}</p>` : ""}
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

// --------------------------------------------------------------------------- jobs

const jobsState = { q: "", source: "", status: "active", min_score: 0, sort: "score", remote: "", offset: 0, selected: new Set() };
routes.jobs = async () => {
  view.innerHTML = `
    <div class="page-head"><div><h1>Jobs</h1><p class="muted">Ranked against your profile. Select several and queue them in one go.</p></div>
      <div class="row"><button id="add-job">Add job manually</button><button id="fetch" class="primary">Fetch new jobs</button></div></div>
    <div class="toolbar">
      <input type="search" id="f-q" placeholder="Search title, company, skill…" value="${esc(jobsState.q)}">
      <select id="f-source" aria-label="Source"><option value="">All sources</option></select>
      <select id="f-status" aria-label="Status">${[["active", "Active"], ["new", "New"], ["shortlisted", "Shortlisted"], ["hidden", "Hidden"], ["all", "All"]]
        .map(([v, l]) => `<option value="${v}" ${jobsState.status === v ? "selected" : ""}>${l}</option>`).join("")}</select>
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
      <button data-bulk="clear">Clear</button><button class="primary" data-bulk="queue">Tailor resumes &amp; queue ${n}</button>`;
  };
  let t;
  $("#f-q").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { jobsState.q = e.target.value; jobsState.offset = 0; load(); }, 250); };
  $("#f-source").onchange = (e) => { jobsState.source = e.target.value; jobsState.offset = 0; load(); };
  $("#f-status").onchange = (e) => { jobsState.status = e.target.value; jobsState.offset = 0; load(); };
  $("#f-remote").onchange = (e) => { jobsState.remote = e.target.value; jobsState.offset = 0; load(); };
  $("#f-min").onchange = (e) => { jobsState.min_score = Number(e.target.value) || 0; jobsState.offset = 0; load(); };
  $("#f-sort").onchange = (e) => { jobsState.sort = e.target.value; load(); };
  $("#pager").onclick = (e) => { const b = e.target.closest("[data-page]"); if (b) { jobsState.offset = Number(b.dataset.page) * 50; load(); } };
  $("#list").onclick = (e) => {
    const cb = e.target.closest("[data-select]");
    if (cb) {
      const id = Number(cb.dataset.select);
      cb.checked ? jobsState.selected.add(id) : jobsState.selected.delete(id);
      cb.closest(".job").classList.toggle("selected", cb.checked);
      return renderBulk();
    }
    const row = e.target.closest("[data-job]");
    if (row) openJob(Number(row.dataset.job), load);
  };
  $("#bulk").onclick = async (e) => {
    const b = e.target.closest("[data-bulk]");
    if (!b) return;
    const ids = [...jobsState.selected];
    if (b.dataset.bulk === "clear") { jobsState.selected.clear(); return load(); }
    if (b.dataset.bulk === "queue") return busy(b, async () => {
      const r = await api("/api/applications/bulk", { method: "POST", body: { job_ids: ids, use_llm: true, cover_letter: $("#b-cover").checked } });
      toast(`Queued ${r.queued}. Tailoring resumes in the background…${r.skipped_already_applied ? ` (${r.skipped_already_applied} already applied)` : ""}`);
      jobsState.selected.clear();
      location.hash = "#/applications";
    });
    await busy(b, async () => {
      await api("/api/jobs/bulk-status", { method: "POST", body: { job_ids: ids, status: b.dataset.bulk } });
      jobsState.selected.clear(); load();
    });
  };
  $("#fetch").onclick = (e) => busy(e.currentTarget, async () => {
    const r = await api("/api/ingest/run", { method: "POST" });
    if (r.status === "busy") return toast("A fetch is already running.");
    toast(`Fetched ${r.fetched}, ${r.new} new.${r.errors.length ? " Errors: " + r.errors.join("; ") : ""}`, r.errors.length > 0 && !r.fetched);
    load();
  });
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

routes.applications = async () => {
  view.innerHTML = `<div class="page-head"><div><h1>Applications</h1>
    <p class="muted">Queued jobs are opened one by one by the browser extension, which fills the form and attaches the tailored resume. You review and submit.</p></div>
    <div class="row"><select id="f-st" aria-label="Filter"><option value="">All</option><option value="preparing,queued,in_progress">Queue</option>
    <option value="applied,interview,offer,rejected">Applied</option><option value="failed,skipped">Failed / skipped</option></select></div></div>
    <div class="card" style="padding:0;overflow-x:auto"><table class="tbl"><thead><tr><th>Job</th><th>Status</th><th>Resume</th><th>Updated</th><th>Notes</th><th></th></tr></thead><tbody id="rows"></tbody></table></div>`;
  let timer;
  const load = async () => {
    const st = $("#f-st")?.value ?? "";
    const apps = await api(`/api/applications${st ? `?status=${st}` : ""}`);
    $("#rows").innerHTML = apps.length ? apps.map((a) => `<tr data-id="${a.id}">
      <td><a href="#" data-open="${a.job_id}"><b>${esc(a.job?.title ?? "(deleted)")}</b></a><div class="small muted">${esc(a.job?.company ?? "")} · ${esc(a.job?.source ?? "")}</div>
        ${a.error ? `<div class="small" style="color:var(--bad)">${esc(a.error)}</div>` : ""}</td>
      <td>${a.status === "preparing" ? `<span class="pill accent"><span class="spinner"></span> preparing</span>` :
        `<select data-status aria-label="Status">${APP_STATUSES.map((s) => `<option ${s === a.status ? "selected" : ""}>${s}</option>`).join("")}</select>`}</td>
      <td>${a.resume_id ? `<a href="#/resumes/${a.resume_id}">View</a> · <a href="/api/resumes/${a.resume_id}/file/pdf">PDF</a>` : "—"}</td>
      <td class="small muted">${fmtDate(a.applied_at || a.updated_at)}</td>
      <td><textarea data-notes rows="1" style="min-height:32px;min-width:160px">${esc(a.notes)}</textarea></td>
      <td>${a.job?.url ? `<a class="btn sm" href="${esc(a.job.url)}" target="_blank" rel="noopener noreferrer">Open ↗</a>` : ""}
        <button class="sm ghost danger" data-remove aria-label="Remove">✕</button></td></tr>`).join("")
      : `<tr><td colspan="6" class="empty">Nothing here yet. Select jobs on the Jobs page and click “Tailor resumes &amp; queue”.</td></tr>`;
    clearTimeout(timer);
    if (apps.some((a) => a.status === "preparing")) timer = setTimeout(load, 2500);
  };
  teardown = () => clearTimeout(timer);
  $("#f-st").onchange = load;
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
    const open = e.target.closest("[data-open]");
    if (open) { e.preventDefault(); return openJob(Number(open.dataset.open), load); }
    if (e.target.closest("[data-remove]")) {
      const tr = e.target.closest("tr");
      if (!confirm("Remove this application from the tracker?")) return;
      await api(`/api/applications/${tr.dataset.id}`, { method: "DELETE" }).catch((err) => toast(err.message, true));
      load();
    }
  };
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
  const s = await api("/api/settings");
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

    <div class="card"><h2>Job sources</h2>
      <p class="small muted">Official public APIs. Naukri and Indeed have none — their jobs come from the browser extension while you browse.</p>
      <div class="grid cols-3">
        <label class="field">Greenhouse boards<input id="gh" value="${list(src.greenhouse)}" placeholder="stripe, razorpay"><span class="muted small">from boards.greenhouse.io/<b>name</b></span></label>
        <label class="field">Lever companies<input id="lv" value="${list(src.lever)}" placeholder="cred, meesho"><span class="muted small">from jobs.lever.co/<b>name</b></span></label>
        <label class="field">Ashby boards<input id="ab" value="${list(src.ashby)}" placeholder="notion, linear"><span class="muted small">from jobs.ashbyhq.com/<b>name</b></span></label>
      </div>
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
        remotive: { enabled: $("#rm-on").checked, queries: split($("#rm-q").value) },
        adzuna: { enabled: $("#az-on").checked, app_id: $("#az-id").value.trim(), app_key: $("#az-key").value.trim(), country: $("#az-c").value,
          where: $("#az-w").value.trim(), queries: split($("#az-q").value), max_pages: Number($("#az-p").value) || 1 } },
      ingest: { interval_hours: Number($("#int").value) || 0, max_age_days: Number($("#age").value) || 0 },
    };
    if (!$("#llm-key").disabled) body.llm.api_key = $("#llm-key").value.trim();
    await api("/api/settings", { method: "PUT", body });
    toast("Settings saved"); refreshAiBadge(); router();
  });
  $("#test").onclick = (e) => busy(e.currentTarget, async () => { const r = await api("/api/settings/test-llm", { method: "POST" }); toast(`Connected to ${r.model}`); });
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
