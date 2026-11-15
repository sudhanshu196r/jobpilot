// Small SVG charts for the dashboard: horizontal bars and vertical columns.
// Single series per chart (no legend needed; the card title names it), thin
// marks with rounded data ends, recessive grid, hover tooltip, and a table view.

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const truncate = (t, n) => (t.length > n ? t.slice(0, Math.max(1, n - 1)) + "…" : t);
const fmt = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));

let tip;
function tooltip() {
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "viz-tip";
    tip.hidden = true;
    document.body.append(tip);
  }
  return tip;
}
function showTip(evt, html) {
  const t = tooltip();
  t.innerHTML = html;
  t.hidden = false;
  const pad = 12;
  const { innerWidth: w } = window;
  const r = t.getBoundingClientRect();
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + r.width > w - 8) x = evt.clientX - r.width - pad;
  t.style.left = `${x}px`;
  t.style.top = `${y}px`;
}
const hideTip = () => { if (tip) tip.hidden = true; };

// A path for a bar whose data end is rounded (radius r) and whose base is square.
function barPath(x, y, w, h, r, horizontal) {
  r = Math.max(0, Math.min(r, horizontal ? h / 2 : w / 2, horizontal ? w : h));
  if (w <= 0 || h <= 0) return "";
  if (horizontal) { // rounded right end
    return `M${x},${y}h${w - r}a${r},${r} 0 0 1 ${r},${r}v${h - 2 * r}a${r},${r} 0 0 1 ${-r},${r}h${-(w - r)}z`;
  } // rounded top end
  return `M${x},${y + h}v${-(h - r)}a${r},${r} 0 0 1 ${r},${-r}h${w - 2 * r}a${r},${r} 0 0 1 ${r},${r}v${h - r}z`;
}

function tableView(rows, cols) {
  return `<details class="viz-table"><summary>Show data</summary><table class="tbl"><thead><tr>${cols.map((c) => `<th>${esc(c.label)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td>${esc(r[c.key])}</td>`).join("")}</tr>`).join("")}</tbody></table></details>`;
}

function wire(host, rows, tipHtml, onClick) {
  host.querySelectorAll("[data-i]").forEach((el) => {
    const r = rows[Number(el.dataset.i)];
    el.addEventListener("mousemove", (e) => showTip(e, tipHtml(r)));
    el.addEventListener("mouseleave", hideTip);
    if (onClick) { el.style.cursor = "pointer"; el.addEventListener("click", () => onClick(r)); }
  });
}

/**
 * Horizontal bars, one row per item, with the label on the left and the value
 * at the bar end. `color` may be a function of the row index (ordinal ramps).
 */
export function barChart(host, rows, { label, value, color = "var(--series-1)", tip, unit = "", onClick, empty = "No data yet." } = {}) {
  if (!rows.length || rows.every((r) => !r[value])) {
    host.innerHTML = `<div class="viz-empty">${esc(empty)}</div>`;
    return;
  }
  // Draw at the real pixel width so text stays 12px whatever the card size.
  const width = Math.max(240, Math.round(host.clientWidth) || 420);
  const rowH = 26, gap = 6, valueW = 44;
  const labelW = Math.min(150, Math.max(90, Math.round(width * 0.32)));
  const plotW = width - labelW - valueW;
  const max = Math.max(...rows.map((r) => r[value]), 1);
  const height = rows.length * (rowH + gap) - gap;
  const bars = rows.map((r, i) => {
    const y = i * (rowH + gap);
    const w = Math.max(r[value] ? 3 : 0, (r[value] / max) * plotW);
    const c = typeof color === "function" ? color(i) : color;
    return `<g data-i="${i}" class="viz-hit">
      <rect x="0" y="${y}" width="${width}" height="${rowH}" fill="transparent"/>
      <text x="${labelW - 10}" y="${y + rowH / 2}" class="viz-label" text-anchor="end" dominant-baseline="central">${esc(truncate(String(r[label]), Math.floor((labelW - 12) / 6.5)))}</text>
      <path d="${barPath(labelW, y + 4, w, rowH - 8, 4, true)}" fill="${c}"/>
      <text x="${labelW + w + 6}" y="${y + rowH / 2}" class="viz-value" dominant-baseline="central">${fmt(r[value])}${unit}</text></g>`;
  }).join("");
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" class="viz" role="img" aria-label="${esc(rows.map((r) => `${r[label]}: ${r[value]}`).join(", "))}">${bars}</svg>
    ${tableView(rows, [{ key: label, label: "Item" }, { key: value, label: "Count" }])}`;
  wire(host, rows, tip || ((r) => `<b>${esc(r[label])}</b><br>${esc(fmt(r[value]))}${unit}`), onClick);
}

/** Vertical columns over an ordered x axis (days, score ranges). */
export function columnChart(host, rows, { x, value, color = "var(--series-1)", tip, xLabel = (r) => r[x], every = 1, empty = "No data yet." } = {}) {
  if (!rows.length || rows.every((r) => !r[value])) {
    host.innerHTML = `<div class="viz-empty">${esc(empty)}</div>`;
    return;
  }
  const width = Math.max(240, Math.round(host.clientWidth) || 420), height = 150, left = 32, bottom = 22, top = 8;
  const plotW = width - left, plotH = height - bottom - top;
  const max = Math.max(...rows.map((r) => r[value]), 1);
  const nice = max <= 4 ? max : Math.ceil(max / 4) * 4;
  const step = plotW / rows.length;
  const barW = Math.max(2, Math.min(28, step - 2)); // 2px surface gap between columns
  const grid = [0, 0.5, 1].map((f) => {
    const y = top + plotH - f * plotH;
    return `<line x1="${left}" x2="${width}" y1="${y}" y2="${y}" class="viz-grid"/>
      <text x="${left - 6}" y="${y}" class="viz-axis" text-anchor="end" dominant-baseline="central">${fmt(Math.round(f * nice))}</text>`;
  }).join("");
  const cols = rows.map((r, i) => {
    const h = (r[value] / nice) * plotH;
    const cx = left + i * step + (step - barW) / 2;
    const lbl = i % every === 0 ? `<text x="${cx + barW / 2}" y="${height - 6}" class="viz-axis" text-anchor="middle">${esc(xLabel(r))}</text>` : "";
    return `<g data-i="${i}" class="viz-hit"><rect x="${left + i * step}" y="${top}" width="${step}" height="${plotH}" fill="transparent"/>
      <path d="${barPath(cx, top + plotH - h, barW, h, Math.min(4, barW / 2), false)}" fill="${color}"/>${lbl}</g>`;
  }).join("");
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" class="viz" role="img" aria-label="${esc(rows.map((r) => `${xLabel(r)}: ${r[value]}`).join(", "))}">${grid}${cols}</svg>
    ${tableView(rows, [{ key: x, label: "When" }, { key: value, label: "Count" }])}`;
  wire(host, rows, tip || ((r) => `<b>${esc(xLabel(r))}</b><br>${esc(r[value])}`));
}

/** A tiny trend line for a stat tile (no axes; the tile's number is the headline). */
export function sparkline(values, { color = "var(--series-1)", w = 96, h = 28 } = {}) {
  if (!values.length || values.every((v) => !v)) return "";
  const max = Math.max(...values, 1);
  const pts = values.map((v, i) => `${(i / Math.max(1, values.length - 1)) * (w - 4) + 2},${h - 2 - (v / max) * (h - 4)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}
