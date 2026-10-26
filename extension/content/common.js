// Shared content-script runtime: messaging, the on-page panel, and the
// generic form-autofill engine used on every job site.
(() => {
  if (window.JP) return; // injected twice (manifest + popup)

  const send = (type, payload = {}) =>
    new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type, ...payload }, (res) => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        if (!res) return reject(new Error("No response from JobPilot extension"));
        res.ok ? resolve(res.data) : reject(new Error(res.error));
      });
    });
  const api = (path, method = "GET", body) => send("api", { path, method, body });

  const text = (el) => (el?.innerText ?? el?.textContent ?? "").replace(/\s+/g, " ").trim();
  const first = (root, selectors) => {
    for (const s of selectors) {
      const el = root.querySelector(s);
      if (el && text(el)) return el;
    }
    return null;
  };
  const visible = (el) => {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
  };
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  // ---------------------------------------------------------------- panel (shadow DOM keeps site CSS out)

  let panelRoot = null;
  function panel() {
    if (panelRoot) return panelRoot;
    const host = document.createElement("div");
    host.id = "jobpilot-panel-host";
    host.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483646;";
    const shadow = host.attachShadow({ mode: "open" });
    shadow.innerHTML = `<style>
      :host { all: initial; }
      .p { font: 13px/1.45 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; color: #16191f; background: #fff;
           border: 1px solid #d9dde5; border-radius: 12px; box-shadow: 0 8px 30px rgba(0,0,0,.18); width: 320px; overflow: hidden; }
      .h { display:flex; align-items:center; gap:8px; padding:10px 12px; background:#2952cc; color:#fff; font-weight:700; }
      .h .x { margin-left:auto; cursor:pointer; background:none; border:none; color:#fff; font-size:16px; }
      .b { padding: 10px 12px; display:flex; flex-direction:column; gap:8px; }
      .t { font-weight:600; }
      .m { color:#5b6272; font-size:12px; }
      .row { display:flex; gap:6px; flex-wrap:wrap; }
      button.a { font: inherit; font-weight:600; border-radius:8px; border:1px solid #d9dde5; background:#fff; padding:6px 10px; cursor:pointer; }
      button.a:hover { background:#f2f4f7; }
      button.pr { background:#2952cc; color:#fff; border-color:#2952cc; }
      button.ok { background:#137a4b; color:#fff; border-color:#137a4b; }
      .score { font-weight:800; font-size:20px; }
      .log { font-size:12px; color:#3d4452; max-height:120px; overflow:auto; background:#f6f7f9; border-radius:6px; padding:6px 8px; }
      .warn { color:#9a6200; } .err { color:#b42335; }
      .min .b { display:none; }
    </style><div class="p"><div class="h"><span>JobPilot</span><button class="x" title="Minimise">–</button></div><div class="b"></div></div>`;
    shadow.querySelector(".x").onclick = () => shadow.querySelector(".p").classList.toggle("min");
    document.documentElement.append(host);
    panelRoot = shadow;
    return shadow;
  }
  function setPanel(html, handlers = {}) {
    const body = panel().querySelector(".b");
    body.innerHTML = html;
    for (const [sel, fn] of Object.entries(handlers)) body.querySelectorAll(sel).forEach((el) => (el.onclick = fn));
    return body;
  }
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------------------------------------------------------------- field discovery

  const SKIP_TYPES = new Set(["hidden", "submit", "button", "reset", "image", "password", "search", "range", "color"]);

  function labelOf(el) {
    const bits = [];
    const aria = el.getAttribute("aria-label");
    if (aria) bits.push(aria);
    const lb = el.getAttribute("aria-labelledby");
    if (lb) lb.split(/\s+/).forEach((id) => { const n = document.getElementById(id); if (n) bits.push(text(n)); });
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) bits.push(text(l));
    }
    const wrap = el.closest("label");
    if (wrap) bits.push(text(wrap).replace(text(el), ""));
    if (!bits.join("").trim()) {
      // Walk up a few levels for question text placed beside the control.
      let node = el.parentElement;
      for (let depth = 0; node && depth < 4 && !bits.join("").trim(); depth++, node = node.parentElement) {
        const clone = node.cloneNode(true);
        clone.querySelectorAll("input,select,textarea,option,button,script,style").forEach((n) => n.remove());
        const t = text(clone);
        if (t && t.length < 300) bits.push(t);
      }
    }
    if (!bits.join("").trim() && el.placeholder) bits.push(el.placeholder);
    if (!bits.join("").trim()) bits.push((el.name || el.id || "").replace(/[_\-\[\]]+/g, " "));
    return bits.join(" ").replace(/\s+/g, " ").replace(/\*/g, "").trim().slice(0, 300);
  }

  function groupLabel(inputs) {
    const fs = inputs[0].closest("fieldset");
    const legend = fs?.querySelector("legend");
    if (legend) return text(legend);
    const labelled = inputs[0].closest("[role=radiogroup],[role=group]");
    if (labelled?.getAttribute("aria-label")) return labelled.getAttribute("aria-label");
    if (labelled?.getAttribute("aria-labelledby")) return text(document.getElementById(labelled.getAttribute("aria-labelledby")));
    // Nearest ancestor containing every option; strip the option labels.
    let node = inputs[0].parentElement;
    while (node && !inputs.every((i) => node.contains(i))) node = node.parentElement;
    for (let depth = 0; node && depth < 3; depth++, node = node.parentElement) {
      const clone = node.cloneNode(true);
      clone.querySelectorAll("input,label,option,button").forEach((n) => n.remove());
      const t = text(clone);
      if (t) return t.slice(0, 300);
    }
    return inputs[0].name || "";
  }

  function optionLabel(input) {
    if (input.id) {
      const l = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
      if (l) return text(l);
    }
    return text(input.closest("label")) || input.value;
  }

  // Returns [{kind, el|els, label, options}]
  function collectFields(root = document) {
    const fields = [];
    const radios = new Map();
    root.querySelectorAll("input, select, textarea, [contenteditable=true], [contenteditable='']").forEach((el) => {
      if (el.closest("#jobpilot-panel-host")) return;
      if (el.disabled || el.readOnly || !visible(el)) return;
      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (tag === "input" && SKIP_TYPES.has(type)) return;
      if (el.getAttribute("role") === "combobox" && el.closest("header,nav")) return;
      if (tag === "input" && type === "file") return fields.push({ kind: "file", el, label: labelOf(el) });
      if (tag === "input" && (type === "radio" || type === "checkbox")) {
        const key = el.name || labelOf(el);
        if (type === "checkbox" && !el.name) return fields.push({ kind: "checkbox", el, label: labelOf(el), options: ["Yes", "No"] });
        if (!radios.has(key)) radios.set(key, { type, els: [] });
        radios.get(key).els.push(el);
        return;
      }
      if (tag === "select") {
        const options = [...el.options].filter((o) => o.value !== "" && !/^(select|choose|--)/i.test(o.text.trim())).map((o) => o.text.trim());
        return fields.push({ kind: "select", el, label: labelOf(el), options });
      }
      fields.push({ kind: el.isContentEditable && tag !== "input" && tag !== "textarea" ? "editable" : tag === "textarea" ? "textarea" : "text", el, label: labelOf(el), inputType: type });
    });
    for (const { type, els } of radios.values()) {
      if (type === "checkbox" && els.length === 1) fields.push({ kind: "checkbox", el: els[0], label: labelOf(els[0]), options: ["Yes", "No"] });
      else fields.push({ kind: type === "radio" ? "radio" : "checkgroup", els, label: groupLabel(els), options: els.map(optionLabel) });
    }
    return fields;
  }

  const isEmpty = (f) => {
    if (f.kind === "radio" || f.kind === "checkgroup") return !f.els.some((e) => e.checked);
    if (f.kind === "checkbox") return !f.el.checked;
    if (f.kind === "editable") return !text(f.el);
    if (f.kind === "select") return f.el.selectedIndex <= 0 || !f.el.value;
    if (f.kind === "file") return !f.el.files?.length;
    return !f.el.value;
  };

  // ---------------------------------------------------------------- filling

  function setNativeValue(el, value) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  }
  const norm = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  function bestOption(options, answer) {
    const a = norm(answer);
    if (!a) return -1;
    let idx = options.findIndex((o) => norm(o) === a);
    if (idx < 0) idx = options.findIndex((o) => norm(o).startsWith(a) || a.startsWith(norm(o)));
    if (idx < 0) idx = options.findIndex((o) => norm(o).includes(a) || a.includes(norm(o)));
    if (idx < 0 && /^\d+(\.\d+)?$/.test(a)) {
      // Numeric answers against ranges like "3-5 years"
      const n = Number(a);
      idx = options.findIndex((o) => { const m = norm(o).match(/(\d+)\s+(?:to\s+)?(\d+)?/); return m && n >= Number(m[1]) && (m[2] ? n <= Number(m[2]) : true); });
    }
    return idx;
  }

  function fill(field, value) {
    if (value == null || value === "") return false;
    const el = field.el;
    switch (field.kind) {
      case "text": case "textarea": {
        let v = String(value);
        if (field.inputType === "number") v = (v.match(/\d+(\.\d+)?/) || [""])[0];
        if (!v) return false;
        el.focus(); setNativeValue(el, v); return true;
      }
      case "editable":
        el.focus();
        if (!document.execCommand("insertText", false, String(value))) el.textContent = String(value);
        el.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
      case "select": {
        const opts = [...el.options];
        const i = bestOption(opts.map((o) => o.text), value);
        if (i < 0) return false;
        el.value = opts[i].value;
        el.dispatchEvent(new Event("change", { bubbles: true }));
        el.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
      }
      case "radio": case "checkgroup": {
        const i = bestOption(field.options, value);
        if (i < 0) return false;
        field.els[i].click();
        return true;
      }
      case "checkbox":
        if (/^(yes|true|agree|i agree)$/i.test(String(value).trim()) && !el.checked) { el.click(); return true; }
        return false;
    }
    return false;
  }

  async function attachResume(field, resumeId) {
    const { name, base64 } = await send("resumeFile", { resumeId });
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    const file = new File([bytes], name, { type: "application/pdf" });
    const dt = new DataTransfer();
    dt.items.add(file);
    field.el.files = dt.files;
    field.el.dispatchEvent(new Event("input", { bubbles: true }));
    field.el.dispatchEvent(new Event("change", { bubbles: true }));
    return name;
  }

  function mark(field, source) {
    const color = source === "ai" ? "#e0a100" : "#16a34a";
    const targets = field.els || [field.el];
    targets.forEach((t) => {
      const box = field.kind === "radio" || field.kind === "checkgroup" ? t.closest("label") || t : t;
      box.style.outline = `2px solid ${color}`;
      box.style.outlineOffset = "1px";
      box.title = source === "ai" ? "JobPilot AI draft — please review" : "Filled by JobPilot";
    });
  }

  // ---------------------------------------------------------------- autofill orchestration

  const filled = new WeakMap(); // element -> {label, source, value}
  const attempted = new WeakSet();

  async function autofill({ task = null, useAi = true, log = () => {} } = {}) {
    const fields = collectFields().filter((f) => !attempted.has(f.el || f.els[0]) && isEmpty(f));
    if (!fields.length) return { filled: 0, review: 0, unanswered: 0 };
    fields.forEach((f) => attempted.add(f.el || f.els[0]));
    let count = 0, review = 0, unanswered = 0;

    // Resume upload
    const files = fields.filter((f) => f.kind === "file");
    if (task?.resume_id && files.length) {
      const target = files.find((f) => /resume|cv|curriculum/i.test(f.label)) || (files.length === 1 ? files[0] : null);
      if (target) {
        try { const name = await attachResume(target, task.resume_id); log(`Attached ${name}`); count++; mark(target, "saved"); }
        catch (e) { log(`Could not attach resume: ${e.message}`, "err"); }
      }
    }

    // Cover letter boxes
    const rest = fields.filter((f) => f.kind !== "file");
    const coverIdx = rest.findIndex((f) => (f.kind === "textarea" || f.kind === "editable") && /cover letter/i.test(f.label));
    if (coverIdx >= 0 && task?.resume_id) {
      try {
        const { cover_letter } = await api(`/api/ext/resumes/${task.resume_id}/cover-letter`);
        if (cover_letter && fill(rest[coverIdx], cover_letter)) { mark(rest[coverIdx], "saved"); count++; rest.splice(coverIdx, 1); }
      } catch { /* optional */ }
    }

    const questions = rest.filter((f) => f.label).slice(0, 80);
    if (!questions.length) return { filled: count, review, unanswered };
    const { answers } = await api("/api/ext/answers/suggest", "POST", {
      questions: questions.map((f) => ({ label: f.label, type: f.kind, options: f.options || [] })),
      job_id: task?.job?.id ?? null,
      use_llm: useAi,
    });
    answers.forEach((a, i) => {
      const f = questions[i];
      if (a.answer && fill(f, a.answer)) {
        filled.set(f.el || f.els[0], { label: f.label, source: a.source, field: f });
        mark(f, a.source);
        count++;
        if (a.needs_review) review++;
      } else {
        filled.set(f.el || f.els[0], { label: f.label, source: "none", field: f });
        unanswered++;
      }
    });
    log(`Filled ${count} field(s)${review ? `, ${review} AI draft(s) to review` : ""}${unanswered ? `, ${unanswered} left for you` : ""}.`);
    return { filled: count, review, unanswered };
  }

  function currentValue(f) {
    switch (f.kind) {
      case "radio": case "checkgroup": { const i = f.els.findIndex((e) => e.checked); return i >= 0 ? f.options[i] : ""; }
      case "checkbox": return f.el.checked ? "Yes" : "";
      case "select": return f.el.selectedIndex > 0 ? f.el.options[f.el.selectedIndex].text.trim() : "";
      case "editable": return text(f.el);
      default: return f.el.value;
    }
  }

  // Remember what the user finally answered, so next time it's automatic.
  async function learnAnswers() {
    const items = [];
    collectFields().forEach((f) => {
      const key = f.el || f.els[0];
      if (f.kind === "file" || !f.label) return;
      const v = currentValue(f);
      if (!v || v.length > 2000) return;
      if (/cover letter/i.test(f.label)) return;
      const prev = filled.get(key);
      if (prev && (prev.source === "saved" || prev.source === "profile")) return;
      items.push({ label: f.label, answer: v });
    });
    if (items.length) await api("/api/ext/answers/save", "POST", { answers: items }).catch(() => {});
    return items.length;
  }

  // ---------------------------------------------------------------- queue task UI

  const SUCCESS_RE = /(application (has been )?(submitted|sent|received)|successfully applied|you have applied|thank you for applying|thanks for applying|applied successfully)/i;

  async function runTask({ onPageReady, findApplyButton } = {}) {
    let task;
    try { task = await send("getTask"); } catch { return null; }
    if (!task) return null;
    const logLines = [];
    const log = (msg, cls = "") => { logLines.push(`<div class="${cls}">${esc(msg)}</div>`); const l = panel().querySelector(".log"); if (l) { l.innerHTML = logLines.join(""); l.scrollTop = 1e6; } };
    let done = false;
    const finish = async (status) => {
      if (done) return; done = true;
      if (status === "applied") { const n = await learnAnswers(); if (n) log(`Saved ${n} answer(s) for next time`); }
      setPanel(`<div class="m">Moving on…</div>`);
      await send("taskDone", { status }).catch((e) => alert(`JobPilot: ${e.message}`));
    };
    setPanel(`<div class="t">${esc(task.job.title)}</div><div class="m">${esc(task.job.company)} · ${task.remaining ?? 0} more in queue</div>
      <div class="log"></div>
      <div class="row"><button class="a pr" data-fill>Autofill</button><button class="a ok" data-applied>Applied ✓ Next</button>
      <button class="a" data-skip>Skip</button><button class="a" data-stop>Stop</button></div>
      <div class="m">Review every field, submit the application yourself, then press <b>Applied</b>.</div>`, {
      "[data-fill]": () => autofill({ task, useAi: task.useAi, log }).catch((e) => log(e.message, "err")),
      "[data-applied]": () => finish("applied"),
      "[data-skip]": () => finish("skipped"),
      "[data-stop]": async () => { done = true; await send("stopQueue"); setPanel(`<div class="m">Queue stopped. Resume it from the extension popup.</div>`); },
    });
    if (onPageReady) await onPageReady(task, log);
    const apply = findApplyButton?.();
    if (apply) {
      apply.style.outline = "3px solid #2952cc";
      apply.scrollIntoView({ block: "center", behavior: "smooth" });
      if (task.autoClickApply) { log("Clicking Apply…"); apply.click(); } else log("Apply button highlighted — click it when ready.");
    }
    if (task.autoFill) {
      const run = debounce(() => autofill({ task, useAi: task.useAi, log }).catch((e) => log(e.message, "err")), 900);
      run();
      new MutationObserver(run).observe(document.body, { childList: true, subtree: true });
    }
    // Watch for the site's own success message.
    const watch = new MutationObserver(debounce(() => {
      if (done) return watch.disconnect();
      if (SUCCESS_RE.test(text(document.body).slice(0, 20000))) {
        watch.disconnect();
        log("Looks like the application went through. Marking as applied in 4s…");
        setTimeout(() => finish("applied"), 4000);
      }
    }, 700));
    watch.observe(document.body, { childList: true, subtree: true, characterData: true });
    return task;
  }

  // ---------------------------------------------------------------- capture helpers

  async function captureJobs(jobs) {
    const cfg = await send("config").catch(() => null);
    if (!cfg?.token || !cfg.capture || !jobs.length) return null;
    return api("/api/ext/jobs", "POST", { jobs });
  }

  function badge(score, status) {
    const b = document.createElement("span");
    b.className = "jobpilot-badge";
    const color = score == null ? "#7a8191" : score >= 70 ? "#137a4b" : score >= 45 ? "#9a6200" : "#b42335";
    b.textContent = status && status !== "queued" ? `JobPilot ${Math.round(score ?? 0)} · ${status}` : `JobPilot ${score == null ? "–" : Math.round(score)}`;
    b.style.cssText = `display:inline-block;margin:4px 0;padding:1px 8px;border-radius:999px;font:600 11px/18px system-ui,sans-serif;color:#fff;background:${color};`;
    return b;
  }

  // Single job page: score + quick actions.
  function jobPanel(result, { title, company }) {
    if (!result) return;
    const r = result.results?.[0];
    if (!r) return;
    const s = r.score == null ? "–" : Math.round(r.score);
    setPanel(`<div class="row" style="align-items:center"><span class="score">${s}</span><div><div class="t">${esc(title)}</div><div class="m">${esc(company)}</div></div></div>
      <div class="m">${r.application_status ? `Status: <b>${esc(r.application_status)}</b>` : "Match score against your profile."}</div>
      <div class="row"><button class="a pr" data-q>Tailor resume &amp; queue</button><button class="a" data-mark>I applied</button></div>`, {
      "[data-q]": async (e) => {
        e.target.disabled = true; e.target.textContent = "Tailoring…";
        try { await api("/api/ext/queue/add", "POST", { job_id: r.job_id }); e.target.textContent = "Queued ✓"; }
        catch (err) { e.target.textContent = "Failed"; alert(`JobPilot: ${err.message}`); }
      },
      "[data-mark]": async (e) => { await api(`/api/ext/jobs/${r.job_id}/applied`, "POST").catch((err) => alert(err.message)); e.target.textContent = "Marked ✓"; },
    });
  }

  // Popup actions.
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === "jp:autofill") {
      autofill({ useAi: true, log: (m) => console.log("[JobPilot]", m) })
        .then((r) => sendResponse(r), (e) => sendResponse({ error: e.message }));
      return true;
    }
    if (msg?.type === "jp:capturePage") {
      const selected = String(window.getSelection() || "").trim();
      const main = document.querySelector("main, [role=main], article") || document.body;
      const description = (selected.length > 200 ? selected : text(main)).slice(0, 30000);
      const h1 = text(document.querySelector("h1")) || document.title;
      const id = Array.from(location.origin + location.pathname).reduce((h, c) => ((h << 5) - h + c.charCodeAt(0)) | 0, 0);
      captureJobs([{ source: "other", external_id: `${location.hostname}:${Math.abs(id)}`, url: location.href, title: h1.slice(0, 200),
        company: location.hostname.replace(/^www\./, ""), description }])
        .then((r) => sendResponse(r?.results?.[0] || { error: "Not connected" }), (e) => sendResponse({ error: e.message }));
      return true;
    }
    if (msg?.type === "jp:learn") { learnAnswers().then((n) => sendResponse({ saved: n })); return true; }
    return false;
  });

  window.JP = { send, api, text, first, visible, debounce, panel, setPanel, esc, collectFields, autofill, learnAnswers, runTask,
    captureJobs, badge, jobPanel, fill, bestOption, labelOf };
})();
