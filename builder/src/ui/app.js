"use strict";
/* Argos UI — plain DOM + fetch, no build step.
   The server parses and rewrites commands; this file only renders and sends edits. */

// ── Icons (lucide-style strokes) ─────────────────────────────
const ICONS = {
  gauge: '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  sliders: '<path d="M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4"/>',
  bookmark: '<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/>',
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  chart: '<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
  package: '<path d="m7.5 4.27 9 5.15"/><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
  braces: '<path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5c0 1.1.9 2 2 2h1"/><path d="M16 21h1a2 2 0 0 0 2-2v-5c0-1.1.9-2 2-2a2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"/>',
  plug: '<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
  palette: '<circle cx="13.5" cy="6.5" r=".5"/><circle cx="17.5" cy="10.5" r=".5"/><circle cx="8.5" cy="7.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.93 0 1.65-.75 1.65-1.69 0-.44-.18-.84-.44-1.13-.29-.29-.44-.65-.44-1.13a1.64 1.64 0 0 1 1.67-1.67h2c3.05 0 5.55-2.5 5.55-5.55C21.97 6.01 17.46 2 12 2z"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
  copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  square: '<rect width="14" height="14" x="5" y="5" rx="2"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/>',
  layers: '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
  zap: '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
  "file-text": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8M16 17H8M10 9H8"/>',
  "file-code": '<path d="M10 12.5 8 15l2 2.5"/><path d="m14 12.5 2 2.5-2 2.5"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  save: '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/><path d="M7 3v4a1 1 0 0 0 1 1h7"/>',
  pencil: '<path d="M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5z"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  "arrow-up": '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
  "arrow-left": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
  activity: '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"/>',
  message: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  cog: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  eye: '<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/>',
  minus: '<path d="M5 12h14"/>',
  star: '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
  server: '<rect width="20" height="8" x="2" y="2" rx="2"/><rect width="20" height="8" x="2" y="14" rx="2"/><path d="M6 6h.01M6 18h.01"/>',
  key: '<path d="m21 2-9.6 9.6"/><circle cx="7.5" cy="15.5" r="5.5"/><path d="m15.5 7.5 3 3L22 7l-3-3"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
};

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "i");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = ICONS[name] || "";
  return svg;
}
function hydrateIcons(root = document) {
  root.querySelectorAll("i[data-icon]").forEach((el) => el.replaceWith(icon(el.dataset.icon)));
}

// ── DOM helpers ──────────────────────────────────────────────
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** h("button", {class: "btn", onclick}, "text", child) — strings become text nodes, never HTML. */
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (["value", "checked", "disabled", "selected", "hidden", "title", "placeholder", "type"].includes(k)) el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}
const fill = (el, ...kids) => { el.replaceChildren(); kids.flat(Infinity).forEach((k) => k != null && k !== false && el.append(k instanceof Node ? k : String(k))); return el; };
const iconBtn = (name, title, onclick, cls = "") => h("button", { class: `icon-btn ${cls}`, title, "aria-label": title, onclick }, icon(name));
const btn = (label, onclick, cls = "", iconName = null) => h("button", { class: `btn ${cls}`, onclick }, iconName && icon(iconName), label);

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

/** A text input that is also a dropdown: click or ▾ lists the choices, typing filters them, any typed value is kept.
 *  options() -> [string | {value, label?, sub?}]. Picking one sets the value and fires "change". Returns the wrapper. */
function attachCombo(input, options) {
  input.setAttribute("autocomplete", "off");
  input.removeAttribute("list");
  const wrap = h("div", { class: "combo" });
  if (input.parentNode) input.replaceWith(wrap);
  const toggle = h("button", { type: "button", class: "combo-btn", tabindex: "-1", "aria-label": "Show choices" }, "▾");
  const list = h("div", { class: "combo-list", role: "listbox", hidden: true });
  wrap.append(input, toggle, list);
  let items = [], active = -1, all = false;
  const render = () => {
    const q = all ? "" : input.value.trim().toLowerCase();
    items = options().map((o) => (typeof o === "string" ? { value: o } : o))
      .filter((o) => !q || `${o.value} ${o.label || ""} ${o.sub || ""}`.toLowerCase().includes(q)).slice(0, 300);
    fill(list, items.length ? items.map((o, i) => h("div", { class: `combo-item${i === active ? " on" : ""}`, role: "option",
      onmousedown: (e) => { e.preventDefault(); pick(o); } }, h("span", {}, o.label || o.value || "default"), o.sub ? h("span", { class: "muted mono-sm" }, o.sub) : null))
      : h("div", { class: "combo-empty" }, input.value ? "No match. Press Enter to keep what you typed." : "Nothing to choose from yet."));
    const on = list.querySelector(".on");
    if (on) on.scrollIntoView({ block: "nearest" });
  };
  const open = (everything) => { all = everything; active = -1; render(); list.hidden = false; };
  const close = () => { list.hidden = true; };
  const pick = (o) => { input.value = o.value; close(); input.dispatchEvent(new Event("change", { bubbles: true })); };
  input.addEventListener("focus", () => open(true));
  input.addEventListener("input", () => open(false));
  input.addEventListener("blur", () => setTimeout(close, 150));
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (list.hidden) open(true);
      active = Math.max(0, Math.min(items.length - 1, active + (e.key === "ArrowDown" ? 1 : -1)));
      render(); e.preventDefault();
    } else if (e.key === "Enter") {
      if (!list.hidden && active >= 0 && items[active]) { e.preventDefault(); pick(items[active]); } else close();
    } else if (e.key === "Escape") close();
  });
  toggle.addEventListener("mousedown", (e) => { e.preventDefault(); if (list.hidden) { input.focus(); open(true); } else close(); });
  return wrap;
}

/** Re-render a container without losing focus on the input the user moved to. */
function keepFocus(fn) {
  const a = document.activeElement;
  const key = a && a.dataset ? a.dataset.fk : null;
  const sel = key && "selectionStart" in a ? [a.selectionStart, a.selectionEnd] : null;
  fn();
  if (!key) return;
  const b = document.querySelector(`[data-fk="${CSS.escape(key)}"]`);
  if (b && b !== a) { b.focus(); if (sel && "setSelectionRange" in b) try { b.setSelectionRange(...sel); } catch (e) { /* number inputs */ } }
}

// ── API / feedback ───────────────────────────────────────────
// Every call gets a deadline. Without one, a request that never answers leaves a
// button looking dead - which is exactly how a stuck app reads from the outside.
// A call that is legitimately slow (a benchmark, a load measurement) passes 0.
const API_TIMEOUT_MS = 180000;

async function api(method, path, body, timeoutMs = API_TIMEOUT_MS) {
  const opts = { method, headers: {} };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const ctl = new AbortController();
  const timer = timeoutMs > 0 ? setTimeout(() => ctl.abort(), timeoutMs) : null;
  if (timer) opts.signal = ctl.signal;
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    if (e && e.name === "AbortError") {
      throw new Error(`${method} ${path} did not answer within ${Math.round(timeoutMs / 1000)}s. The app is busy or stuck — a restart usually clears it.`);
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const data = isJson ? await res.json() : await res.text();
  if (!res.ok || (isJson && data.ok === false)) throw new Error((isJson && data.error) || `${res.status} ${res.statusText}`);
  return data;
}

function toast(msg, kind = "") {
  const t = h("div", { class: `toast ${kind}`, role: kind === "err" ? "alert" : "status" }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "err" ? 6000 : 3200);
}
const fail = (e) => toast(e.message || String(e), "err");

function modal(title, body, actions = [], onClose = null) {
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    back.remove();
    document.removeEventListener("keydown", onKey);
    if (onClose) onClose();
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const back = h("div", { class: "modal-back", onclick: (e) => { if (e.target === back) close(); } },
    h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title },
      h("header", {}, h("h3", {}, title), iconBtn("x", "Close", () => close(), "ghost")),
      h("div", { class: "body" }, body),
      actions.length ? h("footer", {}, actions.map((a) => btn(a.label, () => a.run(close), a.cls || "", a.icon))) : null));
  document.addEventListener("keydown", onKey);
  document.body.append(back);
  return close;
}
function confirmBox(message, okLabel = "Delete") {
  return new Promise((resolve) => {
    let yes = false;
    modal("Confirm", h("p", { style: "margin:0" }, message), [
      { label: "Cancel", run: (close) => close() },
      { label: okLabel, cls: "danger", run: (close) => { yes = true; close(); } },
    ], () => resolve(yes));
  });
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) { // plain-http LAN access has no clipboard API
    const ta = h("textarea", { style: "position:fixed;opacity:0" }, text);
    document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  toast("Copied.", "ok");
}

// ── Formatting ───────────────────────────────────────────────
const DASH = "—";
function num(v, digits = 1) {
  if (v == null || v === "" || Number.isNaN(Number(v))) return DASH;
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: digits });
}
function ago(ts) {
  if (!ts) return DASH;
  const s = Date.now() / 1000 - ts;
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}
function uptime(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
function settingChips(s) {
  if (!s) return [];
  const out = [];
  if (s.ctx_size ?? s.n_ctx) out.push(`ctx ${s.ctx_size ?? s.n_ctx}`);
  if (s.n_gpu_layers ?? s.gpu_layers) out.push(`ngl ${s.n_gpu_layers ?? s.gpu_layers}`);
  const fa = s.flash_attn ?? s.flash_attention;
  if (fa) out.push(`fa ${fa}`);
  const k = s.cache_type_k, v = s.cache_type_v;
  if (k || v) out.push(`kv ${k || "f16"}/${v || "f16"}`);
  if (s.mmap === false || s.mmap === 0) out.push("no-mmap");
  if (s.mmproj || s.mmproj_file) out.push("vision");
  if (s.spec_type) out.push(s.spec_type);
  if (s.load_mode) out.push(`lm ${s.load_mode}`);
  if (s.model_draft || s.draft_model) out.push("draft");
  return out.map((c) => h("span", { class: "chip" }, c));
}

const SAFE_TOKEN = /^[\w@%+=:,./${}-]+$/;
const shq = (t) => (t !== "" && SAFE_TOKEN.test(t) ? t : `'${String(t).replace(/'/g, `'\\''`)}'`);

const PATH_FLAGS = new Set(["model", "mmproj", "model-draft"]);
const dq = (t) => (t && !/["$`\\]/.test(t) ? `"${t}"` : shq(t));

/** Syntax-highlighted command from a parse result (paths double-quoted in Odysseus format). */
function renderCommand(parsed, multiline) {
  const odysseus = parsed.format === "odysseus";
  const out = [];
  const sep = () => out.push(multiline ? h("span", { class: "tk-cont" }, " \\\n  ") : " ");
  Object.entries(parsed.env || {}).forEach(([k, v]) => out.push(h("span", { class: "tk-env" }, k), "=", h("span", { class: "tk-str" }, shq(v)), " "));
  out.push(h("span", { class: "tk-prog" }, shq(parsed.program)));
  for (const a of parsed.args || []) {
    sep();
    if (a.flag) out.push(h("span", { class: "tk-flag" }, a.flag));
    if (a.value != null) {
      if (a.flag) out.push(" ");
      const text = odysseus && PATH_FLAGS.has(a.name) ? dq(a.value) : shq(a.value);
      out.push(h("span", { class: /^-?\d+(\.\d+)?$/.test(a.value) ? "tk-num" : "tk-str" }, text));
    }
  }
  return out;
}

/** Sortable table. cols: [{key, label, num?, sort?(row), cell(row)}]; opts: {onRow(row), rowClass(row)} */
function renderTable(table, cols, rows, state, emptyMsg, opts = {}) {
  if (state.key) {
    const col = cols.find((c) => c.key === state.key);
    const get = col.sort || ((r) => r[col.key]);
    rows = rows.slice().sort((a, b) => {
      const x = get(a), y = get(b);
      const cmp = x == null ? 1 : y == null ? -1 : typeof x === "number" ? x - y : String(x).localeCompare(String(y));
      return state.asc ? cmp : -cmp;
    });
  }
  const head = h("tr", {}, cols.map((c) => h("th", {
    class: [c.num && "num", c.key && "sortable", state.key === c.key && "sorted", state.key === c.key && state.asc && "asc"].filter(Boolean).join(" "),
    onclick: c.key ? () => { state.asc = state.key === c.key ? !state.asc : false; state.key = c.key; renderTable(table, cols, rows, state, emptyMsg, opts); } : null,
  }, c.label)));
  const rowEl = (r) => h("tr", {
    class: [opts.onRow && "clickable", opts.rowClass && opts.rowClass(r)].filter(Boolean).join(" "),
    onclick: opts.onRow ? (e) => { if (!e.target.closest("button, a, input, select")) opts.onRow(r); } : null,
  }, cols.map((c) => { const v = c.cell(r); return v instanceof HTMLTableCellElement ? v : h("td", { class: c.num ? "num" : "" }, v); }));
  let body;
  if (!rows.length) body = [h("tr", {}, h("td", { colspan: cols.length }, h("div", { class: "empty" }, emptyMsg)))];
  else if (opts.group) {
    // a tree: a header row per group ({key, label}) that collapses its rows; rows keep the column sort inside a group
    const groups = new Map();
    rows.forEach((r) => { const g = opts.group(r); if (!groups.has(g.key)) groups.set(g.key, { ...g, rows: [] }); groups.get(g.key).rows.push(r); });
    const list = [...groups.values()];
    if (opts.groupOrder) list.sort(opts.groupOrder);
    body = list.flatMap((g) => {
      const closed = !!(opts.collapsed && opts.collapsed.has(g.key));
      const headRow = h("tr", { class: `group-row${closed ? " closed" : ""}`, title: closed ? "Show this group" : "Hide this group",
        onclick: (e) => { if (!e.target.closest("button, a, input, select") && opts.onToggle) opts.onToggle(g.key); } },
        h("td", { colspan: cols.length }, h("span", { class: "group-caret" }, closed ? "▸" : "▾"), h("span", { class: "group-name" }, g.label),
          h("span", { class: "group-count" }, String(g.rows.length)), opts.groupExtra ? opts.groupExtra(g) : null));
      return [headRow, ...(closed ? [] : g.rows.map(rowEl))];
    });
  } else body = rows.map(rowEl);
  fill(table, h("thead", {}, head), h("tbody", {}, body));
}

/** A small menu under a button: [{label, icon, run, danger}] (null entries are skipped). Closes on pick, outside click or Escape. */
function popMenu(anchor, items) {
  document.querySelectorAll(".pop-menu").forEach((m) => m.remove());
  const r = anchor.getBoundingClientRect();
  const menu = h("div", { class: "pop-menu", role: "menu" }, items.filter(Boolean).map((it) =>
    h("button", { class: `pop-item${it.danger ? " danger" : ""}`, role: "menuitem", onclick: () => { close(); it.run(); } }, it.icon ? icon(it.icon) : null, it.label)));
  const close = () => { menu.remove(); document.removeEventListener("pointerdown", outside, true); document.removeEventListener("keydown", esc); };
  const outside = (e) => { if (!menu.contains(e.target) && e.target !== anchor) close(); };
  const esc = (e) => { if (e.key === "Escape") close(); };
  document.body.append(menu);
  const w = menu.offsetWidth, hgt = menu.offsetHeight;
  menu.style.left = `${Math.max(8, Math.min(r.right - w, innerWidth - w - 8))}px`;
  menu.style.top = `${r.bottom + 4 + hgt > innerHeight ? Math.max(8, r.top - hgt - 4) : r.bottom + 4}px`;
  document.addEventListener("pointerdown", outside, true);
  document.addEventListener("keydown", esc);
  return close;
}

// ── State & router ───────────────────────────────────────────
const S = {
  view: "builder",
  profiles: [], models: [], catalog: [], launches: [], runs: [], best: [], env: [], integrations: null,
  settings: null, library: null, downloads: [],
  sel: null,            // selected launch id in the playground
  lastBench: {},        // launch id -> last recorded run
};
const VIEWS = { builder: "Builder", models: "Models", profiles: "Profiles", playground: "Playground", perf: "Benchmarks",
  schedule: "Schedule", inventory: "Inventory", serverlogs: "Logs", logs: "Usage", assistant: "Assistant", env: "Flags", servers: "Servers", integrations: "Odysseus", settings: "Settings" };
const onShow = {
  models: () => { loadLibrary(); loadCatalog(); loadDownloads(); },
  profiles: () => renderProfiles(),
  perf: () => loadPerf(),
  schedule: () => { loadSchedule(); loadLive(); },
  inventory: () => loadInventory(),
  logs: () => loadLogs(),
  serverlogs: () => { loadServerLogs(true); loadGpuProcs(); },
  assistant: () => loadAssistant(),
  env: () => loadEnv(),
  servers: () => loadServers(),
  integrations: () => loadIntegrations(),
  settings: () => loadSettings().then(renderSettings).then(loadIntegrations),
  playground: () => { if (!S.library) loadLibrary().then(fillProfileSelect); else fillProfileSelect(); renderLaunches(); pollSelected(true); },
};

function show(view) {
  if (view === "catalog") view = "models";
  if (!VIEWS[view]) view = "builder";
  S.view = view;
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  $$(".nav-item[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $("#topTitle").textContent = VIEWS[view];
  document.body.classList.remove("nav-open");
  if (location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
  onShow[view]?.();
}

// ── Shared data ──────────────────────────────────────────────
async function loadProfiles() {
  S.profiles = (await api("GET", "/api/profiles")).profiles;
  $("#navProfiles").textContent = S.profiles.length || "";
  fillImportSelect();
  fillProfileSelect();
  if (S.view === "profiles") renderProfiles();
}
async function loadModels() {
  S.models = (await api("GET", "/api/models")).models;
  fill($("#modelList"), S.models.map((m) => h("option", { value: m })));
}
async function loadSettings() {
  S.settings = (await api("GET", "/api/settings")).settings;
  return S.settings;
}
async function loadCatalog() {
  S.catalog = (await api("GET", "/api/catalog")).files;
  for (const kind of ["mmproj", "draft"]) {
    fill($(`#list-${kind}`), S.catalog.filter((f) => f.kind === kind && f.file_path)
      .map((f) => h("option", { value: f.file_path }, `${f.base_model} · ${f.name}`)));
  }
  if (S.view === "models") renderCatalog();
}
async function loadIntegrations() {
  try {
    S.integrations = await api("GET", "/api/integrations");
  } catch (e) { fail(e); return; }
  fillImportSelect();
  if (S.view === "integrations") { await loadOdyStatus(); renderIntegrations(); }
}
function importSources() {
  const i = S.integrations || {};
  return [
    ["Saved profiles", S.profiles.map((p) => ({ name: p.name, base_model: p.base_model, command: p.launch_command, source: p.source || "profile", profileId: p.profile_id }))],
    ["Odysseus cookbook", (i.odysseus && i.odysseus.commands) || []],
  ];
}
/** The model a profile or preset runs: its --model file name without .gguf (or the base model when there is none). */
function importModelOf(it) {
  const m = (it.command || "").match(/(?:^|\s)(?:-m|--model)(?:\s+|=)(?:"([^"]+)"|'([^']+)'|(\S+))/);
  const path = m ? m[1] || m[2] || m[3] : "";
  const file = path ? path.split(/[\\/]/).pop().replace(/\.gguf$/i, "").replace(/-0000\d-of-\d+$/, "") : "";
  return file || it.base_model || "Other";
}
// Profiles and Odysseus presets, grouped under the model they run (profile names rarely say which model).
function fillImportSelect() {
  const sel = $("#importSelect");
  const items = [];
  importSources().forEach(([from, list]) => list.forEach((it) => items.push({ ...it, from })));
  const byModel = new Map();
  items.forEach((it, i) => { const m = importModelOf(it); if (!byModel.has(m)) byModel.set(m, []); byModel.get(m).push(i); });
  const models = [...byModel.keys()].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true }));
  fill(sel, h("option", { value: "" }, items.length ? "Import from…" : "Nothing to import yet"),
    models.map((m) => h("optgroup", { label: `${m}  (${byModel.get(m).length})` },
      byModel.get(m).sort((a, b) => items[a].name.localeCompare(items[b].name)).map((i) =>
        h("option", { value: String(i) }, `${items[i].name}${items[i].from === "Odysseus cookbook" ? "  · Odysseus" : ""}${items[i].kind ? ` (${items[i].kind})` : ""}`)))));
  sel.disabled = !items.length;
  sel._items = items;
}

// ── Builder ──────────────────────────────────────────────────
const KV_TYPES = ["", "f16", "bf16", "q8_0", "q5_1", "q5_0", "q4_1", "q4_0", "iq4_nl"];
// values seen in the inventory (your custom cache types, split modes…) join the built-in choices
const SEEN = { cache_type: [], split_mode: [], load_mode: [], spec_type: [] };
const seenOpts = (kind, base) => [...new Set([...base, ...SEEN[kind]])].map((v) => ({ value: v, label: v || "default", sub: v && !base.includes(v) ? "seen in your runs" : "" }));
async function loadSeen() {
  try {
    const t = (await api("GET", "/api/inventory/traits")).traits;
    for (const k of Object.keys(SEEN)) SEEN[k] = t.filter((x) => x.kind === k && x.status !== "ignored" && x.status !== "alias").map((x) => x.value);
  } catch (e) { /* the inventory is optional */ }
}
const SETTINGS = [
  { name: "ctx-size", label: "Context", flag: "-c", type: "number", ph: "model default" },
  { name: "n-gpu-layers", label: "GPU layers", flag: "-ngl", ph: "99 · all · auto" },
  { name: "batch-size", label: "Batch", flag: "-b", type: "number", ph: "2048" },
  { name: "ubatch-size", label: "Micro-batch", flag: "-ub", type: "number", ph: "512" },
  { name: "threads", label: "Threads", flag: "-t", type: "number", ph: "auto" },
  { name: "parallel", label: "Parallel slots", flag: "-np", type: "number", ph: "auto" },
  { name: "n-cpu-moe", label: "MoE layers on CPU", flag: "-ncmoe", type: "number", ph: "0" },
  { name: "port", label: "Port", flag: "--port", ph: "8080 or ${PORT}" },
  { name: "host", label: "Host", flag: "--host", ph: "127.0.0.1" },
  { name: "cache-type", label: "KV cache", flag: "-ctk / -ctv", combo: () => seenOpts("cache_type", KV_TYPES) },
  { name: "load-mode", label: "Load mode", flag: "-lm", ph: "dio", combo: () => seenOpts("load_mode", ["", "dio", "mmap"]) },
  { name: "split-mode", label: "Split mode", flag: "-sm", combo: () => seenOpts("split_mode", ["", "none", "layer", "row", "tensor"]) },
  { name: "flash-attn", label: "Flash attention", flag: "-fa", seg: [["", "default"], ["on", "on"], ["off", "off"], ["auto", "auto"]] },
  { name: "mmap", label: "Memory map", flag: "--[no-]mmap", seg: [["", "default"], ["mmap", "on"], ["no-mmap", "off"]] },
  { name: "fit", label: "Auto-fit", flag: "--fit", seg: [["", "default"], ["on", "on"], ["off", "off"]] },
];
const FILES = [
  { name: "model", label: "Model", flag: "--model", ph: "/app/models/model-Q4_K_M.gguf", wide: true },
  { name: "mmproj", label: "Vision projector", flag: "--mmproj", ph: "mmproj-F16.gguf", list: "list-mmproj", wide: true },
  { name: "model-draft", label: "Draft model", flag: "-md", ph: "small-draft-Q8_0.gguf", list: "list-draft", wide: true },
  { name: "spec-type", label: "Speculative", flag: "--spec-type", ph: "draft-mtp",
    combo: () => seenOpts("spec_type", ["", "draft-mtp", "draft", "ngram-simple", "ngram-map-k", "ngram-mod", "ngram-cache"]) },
  { name: "spec-draft-n-max", label: "Draft tokens", flag: "--spec-draft-n-max", type: "number", ph: "3" },
];
const KEY_NAMES = new Set([...SETTINGS, ...FILES].map((f) => f.name).concat(["no-mmap", "cache-type-k", "cache-type-v"]));
/** K and V use one type. A command that already mixes them shows both, until you pick one value for both. */
function kvShown() {
  const k = argValue("cache-type-k"), v = argValue("cache-type-v");
  return k && v && k !== v ? `${k} / ${v}` : (k || v || "");
}

const B = { cmd: "", parsed: null, profileId: null, source: "", layout: "multi", format: "odysseus",
  queue: Promise.resolve(), controls: {}, saved: { cmd: "", name: "" } };

const isDirty = () => !!B.parsed && (B.cmd !== B.saved.cmd || $("#bName").value.trim() !== B.saved.name);
function markSaved() { B.saved = { cmd: B.cmd, name: $("#bName").value.trim(), base: $("#bBase").value }; renderDirty(); }
function renderDirty() { $("#bDirty").hidden = !isDirty(); $("#bDelete").disabled = !B.profileId; }
async function leaveOk() { return !isDirty() || confirmBox("This preset has unsaved changes. Discard them?", "Discard"); }

/** The library model the builder is on: from the command's file, else the Model box. */
function builderModel() {
  const lib = (S.library && S.library.models) || [];
  const id = B.parsed && B.parsed.model_id;
  return lib.find((m) => m.id === id) || lib.find((m) => m.name === $("#bBase").value.trim()) || null;
}
const presetsFor = (modelId) => S.profiles.filter((p) => p.model_id === modelId).sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
function openProfile(p) {
  return openCommand(p.launch_command, { name: p.name, base_model: p.base_model, source: p.source || "profile", profileId: p.profile_id });
}
function fillPresetSelect() {
  const sel = $("#bPreset"), m = builderModel(), id = m ? m.id : (B.parsed && B.parsed.model_id);
  const ps = id ? presetsFor(id) : [];
  const opts = [h("option", { value: "prefab" }, "Prefab (your defaults)"), ...ps.map((p) => h("option", { value: p.profile_id }, p.name))];
  if (B.profileId && !ps.some((p) => p.profile_id === B.profileId)) {
    const p = S.profiles.find((x) => x.profile_id === B.profileId);
    if (p) opts.push(h("option", { value: p.profile_id }, `${p.name} (other model)`));
  }
  if (!B.profileId && B.source !== "prefab") opts.unshift(h("option", { value: "" }, "Not saved yet"));
  fill(sel, opts);
  sel.value = B.profileId || (B.source === "prefab" ? "prefab" : "");
  sel.disabled = !m && !ps.length;
}
async function pickModel(name) {
  const m = ((S.library && S.library.models) || []).find((x) => x.name === name || x.id === name);
  if (!m) return;
  if (!(await leaveOk())) { $("#bBase").value = B.saved.base || $("#bBase").value; return; }
  const ps = presetsFor(m.id);
  if (ps.length) await openProfile(ps[0]); else await openPrefab(m);
}
async function editAlways() {
  const a = S.settings.always;
  const env = h("textarea", { rows: 4, spellcheck: "false", placeholder: "HSA_OVERRIDE_GFX_VERSION=9.0.6" }, a.env || "");
  const flags = h("input", { type: "text", value: a.flags || "", spellcheck: "false", placeholder: "-lm dio --no-warmup" });
  const onLaunch = h("input", { type: "checkbox", checked: a.on_launch }), onExport = h("input", { type: "checkbox", checked: a.on_export });
  modal("Always applied", h("div", { class: "stack" },
    h("p", { class: "hint", style: "margin:0" }, "Added to every command that does not set them itself. Changing them here changes every model at once."),
    h("label", { class: "field" }, h("span", {}, "Environment (KEY=VALUE per line)"), env),
    h("label", { class: "field" }, h("span", {}, "Flags"), flags),
    h("label", { class: "check" }, onLaunch, "Apply to launches here (playground, measure)"),
    h("label", { class: "check" }, onExport, "Apply to every model the engine starts")), [
    { label: "Cancel", run: (c) => c() },
    { label: "Save", cls: "primary", icon: "save", run: async (c) => {
      try {
        await api("PUT", "/api/settings/always", { env: env.value, flags: flags.value, on_launch: onLaunch.checked, on_export: onExport.checked });
        await loadSettings(); c(); toast("Saved. Every model gets these now.", "ok");
        if (B.parsed) await edit({});
      } catch (e) { fail(e); }
    } }]);
}

function argValue(name) {
  const a = (B.parsed?.args || []).find((x) => x.name === name);
  return a ? (a.value ?? "") : null;
}

function buildField(f) {
  let control;
  if (f.seg) {
    control = h("div", { class: "seg", role: "group", "aria-label": f.label },
      f.seg.map(([v, label]) => h("button", { type: "button", dataset: { v }, onclick: () => setSeg(f, v) }, label)));
  } else if (f.options) {
    control = h("select", { dataset: { fk: f.name }, onchange: (e) => edit({ [f.name]: e.target.value || null }) },
      f.options.map((o) => h("option", { value: o }, o || "default")));
  } else {
    control = h("input", { type: f.type || "text", placeholder: f.ph || (f.combo ? "default" : ""), dataset: { fk: f.name }, list: f.list || null, spellcheck: "false" });
    const commit = () => {
      const val = control.value.trim();
      if (f.name === "cache-type") { if (val !== kvShown()) edit({ "cache-type-k": val || null, "cache-type-v": val || null }); return; }
      if (val !== (argValue(f.name) ?? "")) edit({ [f.name]: val || null });
    };
    control.addEventListener("change", commit);
    control.addEventListener("keydown", (e) => { if (e.key === "Enter") commit(); });
  }
  B.controls[f.name] = control;
  const shown = f.combo ? attachCombo(control, f.combo) : control;
  return h("label", { class: `field${f.seg ? " span2" : ""}${f.wide ? " wide" : ""}` }, h("span", {}, f.label, h("code", {}, f.flag)), shown);
}

function setSeg(f, v) {
  if (f.name === "mmap") edit({ mmap: v === "mmap" ? true : null, "no-mmap": v === "no-mmap" ? true : null });
  else edit({ [f.name]: v || null });
}

function initBuilder() {
  attachCombo($("#bBase"), () => ((S.library && S.library.models) || []).map((m) => ({ value: m.name,
    sub: [m.quant, size(m.size), m.profiles ? `${m.profiles} preset${m.profiles > 1 ? "s" : ""}` : ""].filter(Boolean).join(" · ") })));
  $("#bBase").addEventListener("change", () => pickModel($("#bBase").value.trim()));
  $("#bPreset").onchange = async (e) => {
    const v = e.target.value;
    if (!(await leaveOk())) { fillPresetSelect(); return; }
    if (v === "prefab") { const m = builderModel(); if (m) openPrefab(m); }
    else { const p = S.profiles.find((x) => x.profile_id === v); if (p) openProfile(p); }
  };
  $("#bName").addEventListener("input", renderDirty);
  $$(".b-assist-toggle").forEach((b) => b.addEventListener("click", builderAssistToggle));
  $("#bAssistClose").addEventListener("click", builderAssistToggle);
  $("#bAssistSend").addEventListener("click", sendBuilderChat);
  $("#bAssistInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendBuilderChat(); }
  });
  $("#bDelete").onclick = async () => {
    const p = S.profiles.find((x) => x.profile_id === B.profileId);
    if (!p || !(await confirmBox(`Delete the preset “${p.name}”? The engine drops its variant on the next refresh.`))) return;
    try {
      await api("DELETE", `/api/profiles/${p.profile_id}`);
      toast(`Deleted “${p.name}”.`, "ok");
      await loadProfiles();
      B.profileId = null; markSaved(); fillPresetSelect();
    } catch (e) { fail(e); }
  };
  $("#bAlwaysEdit").onclick = editAlways;
  fill($("#bFields"), SETTINGS.map(buildField));
  fill($("#bFiles"), FILES.map(buildField));
  document.body.append(h("datalist", { id: "list-spectype" }, h("option", { value: "draft-mtp" })));
  $("#parseBtn").onclick = () => openCommand($("#composerInput").value, { source: "pasted" });
  $("#composerInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#parseBtn").click(); });
  $("#blankBtn").onclick = () => openCommand("llama-server -m /models/model.gguf --port 8080 -ngl 99", { source: "blank" });
  $("#importSelect").onchange = (e) => {
    const it = e.target.value === "" ? null : e.target._items?.[Number(e.target.value)];
    e.target.value = "";
    if (it) openCommand(it.command, { name: it.name, base_model: it.base_model, source: it.kind ? `${it.source} · ${it.kind}` : it.source, profileId: it.profileId });
  };
  $("#bSave").onclick = () => saveProfile(false);
  $("#bSaveNew").onclick = () => saveProfile(true);
  $("#bLaunch").onclick = async () => { await B.queue; launch(B.cmd, $("#bBase").value.trim(), $("#bName").value.trim()); };
  $("#bCopy").onclick = async () => { await B.queue; copyText(B.layout === "multi" ? B.parsed.multiline : B.parsed.command); };
  $("#bRaw").onclick = () => { $("#bRawText").value = B.parsed.multiline; $("#bRawBox").hidden = false; $("#bPreview").hidden = true; $("#bRawText").focus(); };
  $("#bRawCancel").onclick = () => { $("#bRawBox").hidden = true; $("#bPreview").hidden = false; };
  $("#bRawApply").onclick = async () => {
    const ok = await openCommand($("#bRawText").value, { name: $("#bName").value, base_model: $("#bBase").value, source: B.source, profileId: B.profileId });
    if (ok) $("#bRawCancel").click();
  };
  $$("#bLayout button").forEach((b) => (b.onclick = () => { B.layout = b.dataset.v; renderBuilder(); }));
  $$("#bFormat button").forEach((b) => (b.onclick = async () => {
    B.format = b.dataset.v;
    if (B.parsed) await openCommand(B.cmd, { name: $("#bName").value, base_model: $("#bBase").value, source: B.source, profileId: B.profileId });
    renderBuilder();
  }));
}

function resetBuilder() {
  Object.assign(B, { cmd: "", parsed: null, profileId: null, source: "" });
  $("#composerInput").value = "";
  $("#builderEmpty").hidden = false;
  $("#builderMain").hidden = true;
  show("builder");
  $("#composerInput").focus();
}

async function openCommand(command, meta = {}) {
  if (!command.trim()) { toast("Paste a command first.", "err"); return false; }
  try {
    const res = await api("POST", "/api/command/parse", { command, format: B.format });
    Object.assign(B, { cmd: res.command, parsed: res, profileId: meta.profileId || null, source: meta.source || "" });
    $("#bName").value = meta.name || "";
    const libm = ((S.library && S.library.models) || []).find((m) => m.id === res.model_id);
    $("#bBase").value = libm ? libm.name : (meta.base_model || res.base_model || "");
    markSaved();
    $("#builderEmpty").hidden = true;
    $("#builderMain").hidden = false;
    $("#bRawBox").hidden = true;
    $("#bPreview").hidden = false;
    renderBuilder();
    show("builder");
    return true;
  } catch (e) { fail(e); return false; }
}

/** Queue an edit so rapid changes apply in order on top of each other. */
function edit(edits, env, argEdits) {
  B.queue = B.queue.then(async () => {
    try {
      const res = await api("POST", "/api/command/build", { command: B.cmd, edits, env, arg_edits: argEdits, format: B.format,
        name: $("#bName").value || $("#bBase").value });
      B.cmd = res.command; B.parsed = res;
      if (!$("#bBase").value && res.base_model) $("#bBase").value = res.base_model;
    } catch (e) { fail(e); }
    renderBuilder();
  });
  return B.queue;
}

// ── the assistant, docked in the Builder ──────────────────────────────────────
// It answers about the command on the left, and can propose a flag change. Applying
// one goes through edit(), the same path the fields use, so the command is rebuilt
// and re-parsed exactly as if you had typed the flag yourself.
const BA = { on: false, messages: [], busy: false };

function builderContext() {
  try {
    const p = B.parsed || {}, flags = {}, other = [];
    for (const a of p.args || []) {
      const name = String(a.name || "");
      if (!name) continue;
      if (name.startsWith("-")) other.push(`${name}${a.value ? " " + a.value : ""}`);
      else flags[name] = a.value ?? "";
    }
    return { command: B.cmd || "", flags, other, env: p.env || {}, program: p.program || "",
      base_model: p.base_model || $("#bBase").value || "", model_id: p.model_id || "",
      always: p.always_added || [], models: [$("#bBase").value].filter(Boolean) };
  } catch (e) { return { command: B.cmd || "" }; }
}

// The panel is a window: drag it by its head, resize it by the corner grip, and it
// comes back where you left it. The sticky notes already work this way, so the handle
// looks and behaves like the one you know.
const BA_WIN_KEY = "argos.builder.assist.window";

function assistWindow() {
  try {
    const g = JSON.parse(localStorage.getItem(BA_WIN_KEY) || "null");
    if (g && Number.isFinite(g.x) && Number.isFinite(g.y)) return g;
  } catch (e) { /* private window: use the defaults */ }
  return { x: Math.max(12, window.innerWidth - 400), y: 84, w: 380, h: 360 };
}

function assistRemember(panel) {
  try {
    localStorage.setItem(BA_WIN_KEY, JSON.stringify({
      x: parseInt(panel.style.left, 10) || 0, y: parseInt(panel.style.top, 10) || 0,
      w: panel.offsetWidth, h: panel.offsetHeight }));
  } catch (e) { /* ignore */ }
}

function assistFloat(panel) {
  if (panel.dataset.floating) return;
  document.body.append(panel); // above the page: the same window in either Builder state
  const g = assistWindow();
  // A column: .messages is written for one, and without this the message area takes
  // the whole width and the head and the input collapse into nothing.
  Object.assign(panel.style, { position: "fixed", left: `${g.x}px`, top: `${g.y}px`,
    width: `${g.w}px`, height: `${g.h}px`, zIndex: "70", display: "none",
    flexDirection: "column", boxShadow: "0 14px 48px rgba(0,0,0,0.45)" });
  panel.dataset.floating = "1";

  const head = $("#bAssistHead");
  head.style.cursor = "move";
  head.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button, input, textarea, select")) return; // let the controls be controls
    const r = panel.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
    head.setPointerCapture(e.pointerId);
    const move = (ev) => {
      panel.style.left = `${Math.max(0, Math.min(window.innerWidth - 120, ev.clientX - dx))}px`;
      panel.style.top = `${Math.max(0, Math.min(window.innerHeight - 40, ev.clientY - dy))}px`;
    };
    const up = () => {
      head.removeEventListener("pointermove", move);
      head.removeEventListener("pointerup", up);
      assistRemember(panel);
    };
    head.addEventListener("pointermove", move);
    head.addEventListener("pointerup", up);
  });

  const grip = $("#bAssistGrip");
  grip.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    const r = panel.getBoundingClientRect(), sx = e.clientX, sy = e.clientY;
    grip.setPointerCapture(e.pointerId);
    const move = (ev) => {
      panel.style.width = `${Math.max(280, r.width + (ev.clientX - sx))}px`;
      panel.style.height = `${Math.max(220, r.height + (ev.clientY - sy))}px`;
    };
    const up = () => {
      grip.removeEventListener("pointermove", move);
      grip.removeEventListener("pointerup", up);
      assistRemember(panel);
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up);
  });
}

function builderAssistToggle() {
  BA.on = !BA.on;
  const panel = $("#bAssist");
  assistFloat(panel);
  panel.hidden = false;
  panel.style.display = BA.on ? "flex" : "none";
  $$(".b-assist-toggle").forEach((b) => b.classList.toggle("primary", BA.on));
  if (BA.on) { renderBuilderChat(); setTimeout(() => $("#bAssistInput").focus(), 0); }
}

function builderActionEl(a) {
  // A model action (load, unload, keep) is the same button wherever it appears.
  if (a.verb === "load" || a.verb === "unload" || a.verb === "keep") return actionEl(a);
  const label = a.verb === "unset" ? `Remove ${a.flag}` : `Set ${a.flag}${a.value ? " " + a.value : ""}`;
  const btn = h("button", { class: "btn sm" }, "Apply");
  const row = h("div", { class: "assistant-action", style: "display:flex;gap:8px;align-items:center;margin-top:6px;padding:6px 8px;border:1px solid var(--border);border-radius:6px;" },
    h("span", { style: "flex:1;min-width:0;font-size:12px" }, label), btn);
  btn.onclick = async () => {
    btn.disabled = true; btn.textContent = "…";
    try {
      await edit({ [String(a.flag).replace(/^-+/, "")]: a.verb === "unset" ? null : (a.value || true) });
      btn.textContent = "Done"; row.style.opacity = "0.6";
      toast(`${label} — applied to the command`, "ok");
    } catch (e) { btn.disabled = false; btn.textContent = "Apply"; fail(e); }
  };
  return row;
}

function renderBuilderChat() {
  const box = $("#bAssistMessages");
  if (!box) return;
  fill(box);
  if (!BA.messages.length) {
    box.append(h("div", { class: "muted", style: "font-size:12px" },
      "Ask about the command on the left. It can read its flags, the environment and the model, and propose changes you apply with one click."));
  }
  for (const m of BA.messages) {
    box.append(msgEl(m));
    for (const a of m.actions || []) box.append(builderActionEl(a));
  }
  box.scrollTop = box.scrollHeight;
}

async function sendBuilderChat() {
  const input = $("#bAssistInput");
  const text = (input.value || "").trim();
  if (!text || BA.busy) return;
  BA.busy = true;
  input.value = "";
  const answer = { role: "assistant", content: "", actions: [] };
  BA.messages.push({ role: "user", content: text }, answer);
  renderBuilderChat();
  try {
    const res = await fetch("/api/assistant/chat", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, builder: builderContext() }) });
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const part of parts) {
        const line = part.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        let ev = {};
        try { ev = JSON.parse(line.slice(6)); } catch (e) { continue; }
        if (ev.actions?.length) answer.actions = ev.actions;
        if (ev.type === "delta") { answer.content += ev.text || ""; renderBuilderChat(); }
        else if (ev.type === "done" && ev.text) answer.content = ev.text;
        else if (ev.type === "error") answer.content = ev.error || "The assistant failed.";
      }
      renderBuilderChat();
    }
  } catch (e) {
    answer.content = answer.content || `The assistant is unreachable: ${e.message}`;
  }
  BA.busy = false;
  renderBuilderChat();
}

function renderBuilder() {
  if (!B.parsed) return;
  const p = B.parsed;
  renderDirty();
  fillPresetSelect();
  fill($("#bAlways"), (p.always_added || []).length ? p.always_added.map((x) => h("span", { class: "chip", title: "added at launch and export" }, x))
    : h("span", { class: "muted mono-sm" }, "Nothing. Set env vars or flags every model needs with Edit."));
  for (const f of [...SETTINGS, ...FILES]) {
    const c = B.controls[f.name];
    if (f.seg) {
      const cur = f.name === "mmap" ? (argValue("no-mmap") != null ? "no-mmap" : argValue("mmap") != null ? "mmap" : "")
        : (argValue(f.name) == null ? "" : (argValue(f.name) || "on"));
      $$("button", c).forEach((b) => b.classList.toggle("on", b.dataset.v === cur));
    } else if (c !== document.activeElement) {
      c.value = f.name === "cache-type" ? kvShown() : (argValue(f.name) ?? "");
    }
  }
  keepFocus(() => {
    renderEnv(p.env || {});
    renderOther(p.args || []);
  });
  fill($("#bPreview"), renderCommand(p, B.layout === "multi"));
  $$("#bLayout button").forEach((b) => b.classList.toggle("on", b.dataset.v === B.layout));
  $$("#bFormat button").forEach((b) => b.classList.toggle("on", b.dataset.v === B.format));
  const src = $("#bSource");
  src.hidden = !B.source && !B.profileId;
  src.textContent = B.profileId ? `profile${B.source && B.source !== "profile" ? " · " + B.source : ""}` : B.source;
  $("#bSave").title = B.profileId ? "Update this saved profile" : "Save as a new profile";
}

function renderEnv(env) {
  const entries = Object.entries(env);
  const rowFor = (k, v, i) => h("div", { class: "row", style: "flex-wrap:nowrap" },
    h("input", { type: "text", value: k, placeholder: "NAME", dataset: { fk: `env-k-${i}` }, style: "flex:1", onchange: commitEnv, spellcheck: "false" }),
    h("input", { type: "text", value: v, placeholder: "value", dataset: { fk: `env-v-${i}` }, style: "flex:1.4", onchange: commitEnv, spellcheck: "false" }),
    i < entries.length ? iconBtn("x", "Remove", (e) => { e.target.closest(".row").remove(); commitEnv(); }, "ghost") : h("span", { style: "width:28px" }));
  fill($("#bEnv"), entries.map(([k, v], i) => rowFor(k, v, i)), rowFor("", "", entries.length));
}
function commitEnv() {
  const env = {};
  $$("#bEnv .row").forEach((r) => {
    const [k, v] = $$("input", r).map((i) => i.value.trim());
    if (k) env[k] = v;
  });
  edit({}, env);
}

function renderOther(args) {
  const rows = args.map((a, i) => ({ a, i })).filter(({ a }) => !KEY_NAMES.has(a.name));
  const list = rows.map(({ a, i }) => h("div", { class: "row", style: "flex-wrap:nowrap" },
    h("code", { class: "tk-flag", style: "min-width:120px" }, a.flag || "(arg)"),
    a.value == null
      ? h("span", { class: "muted mono-sm", style: "flex:1" }, "switch")
      : h("input", { type: "text", value: a.value, dataset: { fk: `arg-${i}` }, style: "flex:1", spellcheck: "false",
          onchange: (e) => edit({}, undefined, { [i]: e.target.value }) }),
    iconBtn("x", `Remove ${a.flag}`, () => edit({}, undefined, { [i]: null }), "ghost")));
  const nf = h("input", { type: "text", placeholder: "--flag", dataset: { fk: "new-flag" }, style: "min-width:120px;flex:0.8", spellcheck: "false" });
  const nv = h("input", { type: "text", placeholder: "value (empty = switch)", dataset: { fk: "new-val" }, style: "flex:1", spellcheck: "false" });
  const add = () => {
    let flag = nf.value.trim();
    if (!flag) return;
    if (!flag.startsWith("-")) flag = `--${flag}`;
    edit({ [flag]: nv.value.trim() || true });
  };
  nv.addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
  nf.addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
  fill($("#bOther"), list.length ? list : h("div", { class: "muted mono-sm" }, "No other flags."), h("div", { class: "row", style: "flex-wrap:nowrap" }, nf, nv, iconBtn("plus", "Add flag", add)));
}

async function saveProfile(asNew) {
  if (!B.parsed) return;
  await B.queue;
  const name = $("#bName").value.trim() || $("#bBase").value.trim() || "profile";
  const updating = !asNew && !!B.profileId;
  let load = false;
  if (updating) {
    let d;
    try { d = await api("POST", "/api/profiles/diff", { profile_id: B.profileId, name, launch_command: B.cmd }); } catch (e) { return fail(e); }
    if (!d.changes.length) { toast("Nothing changed."); return; }
    const choice = await reviewChanges(d, name);
    if (!choice) return;
    load = choice.load;
  }
  try {
    const res = await api("POST", "/api/profiles", {
      profile_id: asNew ? null : B.profileId, name, base_model: $("#bBase").value.trim(),
      source: B.source === "profile" ? "" : B.source, launch_command: B.cmd,
    });
    B.profileId = res.profile.profile_id;
    $("#bName").value = res.profile.name;
    markSaved();
    toast(updating ? `Updated “${name}”.` : `Saved “${name}”.`, "ok");
    if (updating) {
      const r = await api("POST", `/api/profiles/${B.profileId}/apply`, { load });
      if (r.refresh === "ok") toast(load ? `The engine has the new settings; loading ${r.variant_id} now.` : "The engine has the new settings.", "ok");
      else if (r.refresh !== "skipped") toast(r.refresh, "err");
    }
    renderBuilder();
    loadProfiles().catch(fail);
    loadModels().catch(fail);
  } catch (e) { fail(e); }
}

/** Show what a save changes; resolves to {load} on confirm or null on cancel. */
function reviewChanges(d, name) {
  return new Promise((resolve) => {
    let result = null;
    const v = (x) => (x == null ? h("span", { class: "muted" }, "not set") : x === "" ? h("span", { class: "muted" }, "on") : x);
    const rows = d.changes.map((c) => h("tr", { class: `diff-${c.change}` },
      h("td", {}, h("code", { class: c.env ? "tk-env" : "tk-flag" }, c.name)), h("td", { class: "before" }, v(c.before)), h("td", {}, "→"), h("td", { class: "after" }, v(c.after))));
    const load = h("input", { type: "checkbox", checked: d.running === true });
    const where = !d.engine ? "The engine is not reachable, so this only saves the preset."
      : d.variant_id ? `In the engine this is ${d.variant_id}${d.running ? ", loaded right now" : ""}. Only this model restarts with the new settings; other models stay loaded.`
        : "The engine will list this preset after its next refresh.";
    modal(`Save changes to “${name}”?`, h("div", { class: "stack" },
      h("table", { class: "diff" }, h("thead", {}, h("tr", {}, h("th", {}, "Setting"), h("th", {}, "Before"), h("th", {}), h("th", {}, "After"))), h("tbody", {}, rows)),
      h("p", { class: "hint", style: "margin:0" }, where),
      d.engine && d.variant_id ? h("label", { class: "check" }, load, "Load it with the new settings now") : null), [
      { label: "Cancel", run: (c) => c() },
      { label: "Save", cls: "primary", icon: "save", run: (c) => { result = { load: load.checked }; c(); } }],
    () => resolve(result));
  });
}

// ── Profiles view ────────────────────────────────────────────
const profileSort = { key: null, asc: false };
function fillProfileModels() {
  const sel = $("#pModel"), cur = sel.value;
  const ids = [...new Set(S.profiles.map((p) => p.model_id || p.base_model).filter(Boolean))].sort();
  fill(sel, h("option", { value: "" }, `All models (${S.profiles.length})`),
    ids.map((id) => h("option", { value: id }, `${id} (${S.profiles.filter((p) => (p.model_id || p.base_model) === id).length})`)));
  sel.value = ids.includes(cur) ? cur : "";
}
function showProfilesFor(modelId) {
  show("profiles");
  fillProfileModels();
  $("#pModel").value = modelId;
  renderProfiles();
}
async function toggleFavorite(p) {
  try {
    await api("POST", "/api/profiles", { ...p, favorite: !p.favorite });
    await loadProfiles();
  } catch (e) { fail(e); }
}
function renderProfiles() {
  fillProfileModels();
  const q = $("#pSearch").value.trim().toLowerCase(), m = $("#pModel").value;
  const rows = S.profiles.filter((p) => (!m || (p.model_id || p.base_model) === m)
    && (!q || `${p.name} ${p.base_model} ${p.model_id} ${p.launch_command} ${p.source}`.toLowerCase().includes(q)));
  renderTable($("#pTable"), [
    { label: "", cell: (p) => h("td", { style: "width:1%" }, h("button", {
      class: "icon-btn ghost", title: p.favorite ? "Unstar" : "Star", "aria-label": "Favorite",
      style: p.favorite ? "color:var(--warn)" : "color:var(--muted)", onclick: () => toggleFavorite(p) }, icon("star"))) },
    { key: "name", label: "Name", cell: (p) => h("span", { class: "strong" }, p.name) },
    { key: "model_id", label: "Model", cell: (p) => h("td", { class: "trunc", title: p.summary?.model || "" },
      // the file name once (its id is the same name in lower case); the id only shows when it really differs
      p.base_model || p.model_id || DASH, p.base_model && p.model_id && p.base_model.toLowerCase() !== p.model_id.toLowerCase() ? h("div", { class: "muted mono-sm" }, p.model_id) : null) },
    { label: "Settings", cell: (p) => h("div", { class: "chips" }, settingChips(p.summary)) },
    { key: "source", label: "Source", cell: (p) => (p.source ? h("span", { class: "pill" }, p.source) : DASH) },
    { key: "updated_at", label: "Updated", cell: (p) => h("span", { class: "muted" }, ago(p.updated_at)) },
    { label: "", cell: (p) => h("td", { class: "actions" },
      iconBtn("sliders", "Open in builder", () => openCommand(p.launch_command, { name: p.name, base_model: p.base_model, source: p.source || "profile", profileId: p.profile_id })),
      iconBtn("play", "Launch", () => launch(p.launch_command, p.base_model, p.name)),
      iconBtn("trash", "Delete", async () => {
        if (!(await confirmBox(`Delete profile “${p.name}”?`))) return;
        try { await api("DELETE", `/api/profiles/${encodeURIComponent(p.profile_id)}`); toast("Deleted.", "ok"); loadProfiles(); } catch (e) { fail(e); }
      }, "danger")) },
  ], rows, profileSort, q || m ? "No profiles match." : h("span", {}, "No saved profiles yet. Load a prefab from ", h("b", {}, "Models"), " or start a ", h("b", {}, "New profile"), "."));
}

// ── Playground ───────────────────────────────────────────────
function fillProfileSelect() {
  const lib = (S.library && S.library.models) || [];
  fill($("#gProfile"), h("option", { value: "" }, "Load a profile or a model…"),
    S.profiles.length ? h("optgroup", { label: "Profiles" }, S.profiles.map((p) => h("option", { value: p.profile_id }, p.name))) : null,
    lib.length ? h("optgroup", { label: "Models (default launch)" }, lib.map((m) => h("option", { value: `model:${m.path}` }, m.name))) : null);
}

// Playground is for testing: it lists only what was started from it. Everything else the engine is running
// (requests, the scheduler, the assistant) is on the Logs page.
const mineKey = "argos.playground.engineLaunches";
const pgMine = new Set((() => { try { return JSON.parse(sessionStorage.getItem(mineKey) || "[]"); } catch { return []; } })());
const rememberMine = (id) => { pgMine.add(id); try { sessionStorage.setItem(mineKey, JSON.stringify([...pgMine])); } catch { /* private window */ } };
const playgroundLaunches = () => S.launches.filter((l) => !l.engine || pgMine.has(l.base_model));

async function launch(command, base_model, profile_name) {
  if (!command || !command.trim()) { toast("Nothing to launch.", "err"); return; }
  try {
    const res = await api("POST", "/api/launches", { command, base_model, profile_name });
    if (res.engine) {
      // The engine already serves this model, so nothing runs here: it loads on
      // whichever host the engine launches on, and Schedule → Live shows it.
      toast(`Asked the engine to load ${res.model}. It shows under Launches once it starts; benchmark it when it is ready.`, "ok");
      rememberMine(res.model);
      S.sel = `engine:${res.model}`;
      show("playground");
      await refreshLaunches();
      return;
    }
    S.sel = res.launch.launch_id;
    $("#gCmd").value = command;
    $("#gBase").value = base_model || "";
    toast("Launched.", "ok");
    show("playground");
    await refreshLaunches();
  } catch (e) { fail(e); }
}

async function refreshLaunches() {
  S.launches = (await api("GET", "/api/launches")).launches;
  const shown = playgroundLaunches();
  if ((!S.sel || !shown.some((l) => l.launch_id === S.sel)) && shown.length) S.sel = shown[0].launch_id;
  const running = shown.filter((l) => l.running);
  fill($("#navPlayground"), running.length ? `${running.length} running` : "idle",
    h("span", { class: `dot ${running.some((l) => l.ready) ? "on" : running.length ? "warn" : ""}` }));
  if (S.view === "playground") renderLaunches();
}

function launchState(l) {
  if (!l) return ["", "no launch selected"];
  if (l.engine) return l.ready ? ["on", "ready"] : ["warn", `${l.state || "starting"}…`];
  if (l.running) return l.ready ? ["on", "ready"] : ["warn", "starting…"];
  if (l.stopped) return ["", "stopped"];
  return [l.exit_code ? "bad" : "", `exited ${l.exit_code ?? ""}`.trim()];
}

function renderLaunches() {
  const row = (l) => {
    const [dot, label] = launchState(l);
    return h("div", { class: `launch${l.launch_id === S.sel ? " sel" : ""}`, onclick: () => { S.sel = l.launch_id; renderLaunches(); pollSelected(true); } },
      h("span", { class: `dot ${dot}` }),
      h("div", { class: "who" },
        h("div", { class: "t" }, l.profile_name || l.base_model || (l.command.split(" ").find((t) => !t.includes("=")) || l.launch_id).split("/").pop()),
        h("div", { class: "s" }, `${l.engine ? "engine" : `:${l.port}`} · ${label} · ${l.running ? uptime(l.started_at) : ago(l.started_at)}`)));
  };
  // two kinds of thing run here: servers started from this page, and models the engine loaded itself
  // (a request, the scheduler or the assistant asked for them). Mixing them read as "I never launched that".
  const list = playgroundLaunches().map(row);
  fill($("#gLaunches"), list.length ? list : h("div", { class: "empty" }, "Nothing started here yet. Pick a model above and Launch it to test it. Models already running in the engine are on the Logs page."));
  const cur = S.launches.find((l) => l.launch_id === S.sel);
  const [dot, label] = launchState(cur);
  fill($("#gState"), cur ? h("span", { class: `pill ${dot === "on" ? "ok" : dot === "bad" ? "bad" : ""}` }, h("span", { class: `dot ${dot}` }), label) : null);
  $("#gBench").disabled = !(cur && cur.running && cur.ready);
  $("#gRecord").disabled = !cur;
  $("#gStop").disabled = !(cur && cur.running);
  $("#gStop").lastChild.textContent = cur && cur.engine ? "Unload" : "Stop";
  $("#gLogTitle").textContent = cur ? `Output · ${cur.launch_id}` : "Output";
  renderStats(S.lastBench[S.sel]);
}

function renderStats(r) {
  if (!r) { fill($("#gStats"), h("div", { class: "muted mono-sm", style: "grid-column:1/-1" }, "Run a benchmark once the server is ready.")); return; }
  const stat = (k, v, unit, lead, sub) => h("div", { class: `stat${lead ? " lead" : ""}` },
    h("div", { class: "k" }, k), h("div", { class: "v" }, v, unit && h("small", {}, unit)), sub && h("div", { class: "muted mono-sm" }, sub));
  fill($("#gStats"),
    stat("Generation", num(r.tokens_per_second), "t/s", true, `${num(r.output_tokens, 0)} tokens`),
    stat("Prompt", num(r.prompt_per_second), "t/s", false, `${num(r.input_tokens, 0)} tokens`),
    stat("VRAM", num(r.mem_used_gb, 2), "GiB"),
    r.gpu_util != null && stat("GPU", num(r.gpu_util, 0), "%"),
    r.draft_acc != null && stat("Draft accept", num(r.draft_acc * 100, 0), "%"),
    r.layers_offloaded && stat("Offloaded", r.layers_offloaded, "layers"));
}

let logLen = -1;
async function pollSelected(force) {
  const id = S.sel;
  if (!id) { fill($("#gLog"), h("span", { class: "muted" }, "Launch a command to see its output here.")); return; }
  try {
    const res = await fetch(`/api/launches/${encodeURIComponent(id)}/log?tail=200000`);
    if (!res.ok) return;
    const text = await res.text();
    if (id !== S.sel || (!force && text.length === logLen)) return;
    logLen = text.length;
    const pre = $("#gLog");
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
    fill(pre, text.split("\n").map((line) => {
      const cls = /error|failed|out of memory|exception|invalid argument/i.test(line) ? "ln-err"
        : /eval time|tokens per second|t\/s/i.test(line) ? "ln-perf"
        : /listening|all slots are idle/i.test(line) ? "ln-ok" : "";
      return [cls ? h("span", { class: cls }, line) : line, "\n"];
    }));
    if ($("#gFollow").checked && (atBottom || force)) pre.scrollTop = pre.scrollHeight;
    pollInsight();
  } catch (e) { /* log may not exist yet */ }
}

// Live readings: load buffers, per-request speeds, and other profiles for reference.
const LIVE = { refs: [], shown: new Set() };

function reqChart(box, title, key, rows, refKey) {
  const vals = rows.map((r) => r[key]).filter((v) => v != null);
  const refs = LIVE.refs.filter((r) => LIVE.shown.has(r.key) && r[refKey] != null);
  if (!vals.length) { fill(box, h("h3", {}, title, h("small", {}, "t/s")), h("div", { class: "empty" }, "Send a request.")); return; }
  const W = Math.max(220, box.clientWidth || 300), H = 150, m = { l: 40, r: 10, t: 8, b: 20 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const top = niceTop(Math.max(...vals, ...refs.map((r) => r[refKey]))), step = niceStep(top);
  const n = rows.length, X = (i) => m.l + (n > 1 ? (i / (n - 1)) * iw : iw / 2), Y = (v) => m.t + ih - (v / top) * ih;
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${title} per request` });
  const grid = sv("g", { class: "grid" }), axis = sv("g", { class: "axis" });
  for (let i = 0; i <= Math.round(top / step); i++) {
    const y = Y(step * i);
    if (i) grid.append(sv("line", { x1: m.l, x2: W - m.r, y1: y, y2: y }));
    const t = sv("text", { x: m.l - 6, y: y + 3, "text-anchor": "end" }); t.textContent = `${+(step * i).toFixed(0)}`; axis.append(t);
  }
  const cap = sv("text", { x: W - m.r, y: H - 4, "text-anchor": "end" }); cap.textContent = "request"; axis.append(cap);
  svg.append(grid, sv("line", { class: "base", x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0) }), axis);
  refs.forEach((r, i) => {
    svg.append(sv("line", { x1: m.l, x2: W - m.r, y1: Y(r[refKey]), y2: Y(r[refKey]), stroke: seriesColor(i + 1), "stroke-dasharray": "4 3", "stroke-width": 1.5 }));
  });
  let d = "";
  rows.forEach((r, i) => { if (r[key] != null) d += `${d ? "L" : "M"}${X(i).toFixed(1)},${Y(r[key]).toFixed(1)}`; });
  svg.append(sv("path", { class: "series", d, stroke: seriesColor(0) }));
  rows.forEach((r, i) => {
    if (r[key] == null) return;
    const dot = sv("circle", { class: "dot", cx: X(i), cy: Y(r[key]), r: 3.5, fill: seriesColor(0) });
    const tt = sv("title", {}); tt.textContent = `#${r.n}: ${num(r[key])} t/s · ${r.prompt_tokens || 0} in / ${r.output_tokens || 0} out`;
    dot.append(tt); svg.append(dot);
  });
  const last = vals[vals.length - 1];
  fill(box, h("h3", {}, title, h("small", {}, `t/s · last ${num(last)}`)), svg);
}

async function pollInsight() {
  const id = S.sel;
  if (!id) return;
  try {
    const r = await api("GET", `/api/launches/${encodeURIComponent(id)}/insight`);
    if (id !== S.sel) return;
    const p = r.parsed, dev = Object.entries(p.gpu_by_device || {});
    fill($("#gBuf"),
      dev.length ? dev.map(([k, v]) => h("div", { class: "stat" }, h("div", { class: "k" }, k), h("div", { class: "v" }, num(v / 1024, 2), h("small", {}, "GiB")))) : null,
      p.layers_offloaded != null && h("div", { class: "stat" }, h("div", { class: "k" }, "Offloaded"), h("div", { class: "v" }, `${p.layers_offloaded}/${p.layers_total}`)),
      p.n_ctx && h("div", { class: "stat" }, h("div", { class: "k" }, "Context"), h("div", { class: "v" }, num(p.n_ctx, 0))),
      p.load_ms ? h("div", { class: "stat" }, h("div", { class: "k" }, "Load time"), h("div", { class: "v" }, num(p.load_ms / 1000, 1), h("small", {}, "s"))) : null,
      !dev.length && p.layers_offloaded == null && h("div", { class: "muted mono-sm", style: "grid-column:1/-1" }, "Readings appear as the server loads."));
    LIVE.rows = r.requests;
    reqChart($("#gDecode"), "Decode", "decode", r.requests, "decode");
    reqChart($("#gPrefill"), "Prefill", "prefill", r.requests, "prefill");
    fill($("#gReadings"), (r.readings.length ? r.readings : ["No tensor lines yet."]).join("\n"));
  } catch (e) { /* the launch may have no log yet */ }
}

async function compareSimilar() {
  const cur = S.launches.find((l) => l.launch_id === S.sel);
  const command = cur ? cur.command : $("#gCmd").value;
  if (!command) { toast("Launch or type a command first.", "err"); return; }
  try {
    const res = await api("POST", "/api/similar", { command, model: cur?.base_model || $("#gBase").value.trim() });
    LIVE.refs = res.results.filter((x) => x.decode != null || x.prefill != null).map((x) => ({ ...x, key: x.key }));
    LIVE.shown = new Set(LIVE.refs.slice(0, 3).map((x) => x.key));
    drawRefs();
    if (!res.results.length) toast("Nothing similar recorded yet.", "");
  } catch (e) { fail(e); }
}

function drawRefs() {
  const toggle = (r) => { LIVE.shown.has(r.key) ? LIVE.shown.delete(r.key) : LIVE.shown.add(r.key); drawRefs(); };
  fill($("#gRefs"), LIVE.refs.length ? h("div", { class: "stack", style: "margin-top:10px" },
    h("div", { class: "muted mono-sm" }, "Dashed lines are the median speed of similar profiles. Tick to show."),
    LIVE.refs.map((r, i) => h("label", { class: "row nowrap mono-sm" },
      h("input", { type: "checkbox", checked: LIVE.shown.has(r.key), onchange: () => toggle(r) }),
      h("span", { class: "swatch", style: `--c:${seriesColor(i + 1)}` }),
      h("span", {}, `${r.model || r.key} · ${r.label || ""}`),
      h("span", { class: "muted" }, ` ${num(r.decode)} decode · ${num(r.prefill, 0)} prefill${r.similarity != null ? ` · ${Math.round(r.similarity * 100)}% alike` : ""}`)))) : null);
  reqChart($("#gDecode"), "Decode", "decode", LIVE.rows || [], "decode");
  reqChart($("#gPrefill"), "Prefill", "prefill", LIVE.rows || [], "prefill");
}

function initPlayground() {
  $("#gSimilar").onclick = compareSimilar;
  $("#gProfile").onchange = (e) => {
    const v = e.target.value;
    e.target.value = "";
    if (v.startsWith("model:")) {
      const m = ((S.library && S.library.models) || []).find((x) => x.path === v.slice(6));
      if (!m) return;
      api("POST", "/api/library/prefab", { path: m.path }).then((res) => {
        $("#gCmd").value = res.command; $("#gBase").value = m.name; delete $("#gCmd").dataset.profile;
      }).catch(fail);
      return;
    }
    const p = S.profiles.find((x) => x.profile_id === v);
    if (p) { $("#gCmd").value = p.launch_command; $("#gBase").value = p.base_model || ""; $("#gCmd").dataset.profile = p.name; }
  };
  $("#gCmd").addEventListener("input", () => delete $("#gCmd").dataset.profile);
  $("#gLaunch").onclick = () => launch($("#gCmd").value, $("#gBase").value.trim(), $("#gCmd").dataset.profile || "");
  $("#gBench").onclick = async () => {
    const id = S.sel, b = $("#gBench");
    b.disabled = true; b.lastChild.textContent = "Benchmarking…";
    try {
      const res = await api("POST", `/api/launches/${encodeURIComponent(id)}/bench`, { prompt_words: +$("#gWords").value, n_predict: +$("#gNPredict").value }, 0);
      S.lastBench[id] = res.run;
      toast(`${num(res.run.tokens_per_second)} t/s generation · saved to Benchmarks`, "ok");
    } catch (e) { fail(e); }
    b.lastChild.textContent = "Run benchmark";
    renderLaunches(); pollSelected(true);
  };
  $("#gRecord").onclick = async () => {
    try {
      const res = await api("POST", `/api/launches/${encodeURIComponent(S.sel)}/record`, {});
      S.lastBench[S.sel] = res.run;
      toast(res.run.success ? "Recorded from log." : "Recorded — no timing lines found in the log yet.", res.run.success ? "ok" : "");
      renderLaunches();
    } catch (e) { fail(e); }
  };
  $("#gStop").onclick = async () => {
    const eng = (S.sel || "").startsWith("engine:");
    try { await api("POST", `/api/launches/${encodeURIComponent(S.sel)}/stop`); toast(eng ? "Unloaded." : "Stopped.", "ok"); } catch (e) { fail(e); }
    await refreshLaunches(); pollSelected(true);
  };
}

// ── Perf sheet ───────────────────────────────────────────────
const perfSort = { key: null, asc: false };
async function loadPerf() {
  try {
    const [runs, best] = await Promise.all([api("GET", "/api/runs"), api("GET", "/api/analytics/best")]);
    S.runs = runs.runs; S.best = best.rows;
  } catch (e) { fail(e); return; }
  $("#navPerf").textContent = S.runs.length || "";
  const models = [...new Set(S.runs.map((r) => r.base_model))].sort();
  const cur = $("#fModel").value;
  fill($("#fModel"), h("option", { value: "" }, "All models"), models.map((m) => h("option", { value: m }, m)));
  $("#fModel").value = models.includes(cur) ? cur : "";
  renderPerf();
}
function perfRows() {
  const m = $("#fModel").value, q = $("#fSearch").value.trim().toLowerCase();
  return S.runs.filter((r) => (!m || r.base_model === m) && (!q || `${r.base_model} ${r.profile_name} ${r.launch_command} ${r.note}`.toLowerCase().includes(q)));
}
function openRunInBuilder(r) {
  openCommand(r.launch_command, { base_model: r.base_model, name: r.profile_name || "", source: "benchmarks" });
}
async function showLog(launchId) {
  try {
    const res = await fetch(`/api/launches/${encodeURIComponent(launchId)}/log?tail=300000`);
    if (!res.ok) { toast("That run's log file is gone.", "err"); return; }
    const text = await res.text();
    modal(`Log · ${launchId}`, h("pre", { class: "log", style: "max-height:65dvh" }, text || "(empty)"));
  } catch (e) { fail(e); }
}
// The perf sheet: a ranked "best per model" table, then every run, grouped under its model (collapsible).
const PF = { grouped: true, collapsed: new Set(), bestSort: { key: "tokens_per_second", asc: false } };
try {
  PF.grouped = localStorage.getItem("argos.perf.grouped") !== "0";
  PF.collapsed = new Set(JSON.parse(localStorage.getItem("argos.perf.closed") || "[]"));
} catch (e) { /* defaults */ }
const pfSave = () => { try { localStorage.setItem("argos.perf.grouped", PF.grouped ? "1" : "0"); localStorage.setItem("argos.perf.closed", JSON.stringify([...PF.collapsed])); } catch (e) { /* ignore */ } };
const tpsBar = (v, max) => h("div", { class: "bar-cell", title: `${num(v, 1)} tokens per second. The bar compares this row with the fastest decode in the list below (${num(max, 1)} t/s = full bar).` }, num(v), h("span", { class: "bar" }, h("i", { style: `width:${Math.round(((v || 0) / max) * 100)}%` })));

function renderPerf() {
  const m = $("#fModel").value;
  const best = S.best.filter((b) => !m || b.base_model === m);
  const maxBest = Math.max(1, ...best.map((b) => b.tokens_per_second || 0));
  renderTable($("#fBest"), [
    { key: "base_model", label: "Model", cell: (b) => h("td", { class: "cell-model", title: b.base_model }, h("div", { class: "cell-main" }, b.base_model)) },
    { key: "prompt_per_second", label: "Prefill (prompt) t/s", num: true, cell: (b) => num(b.prompt_per_second, 0) },
    { key: "tokens_per_second", label: "Decode (gen) t/s", num: true, cell: (b) => tpsBar(b.tokens_per_second, maxBest) },
    { label: "With", cell: (b) => h("div", { class: "chips nowrap-chips" }, settingChips(b)) },
    { key: "runs", label: "Runs", num: true, cell: (b) => b.runs },
    { key: "created_at", label: "Best on", cell: (b) => h("span", { class: "muted nowrap", title: new Date(b.created_at * 1000).toLocaleString() }, ago(b.created_at)) },
    { label: "", cell: (b) => h("td", { class: "actions" }, iconBtn("sliders", "Open in builder", () => openRunInBuilder(b)), iconBtn("database", "Open in Inventory", () => openRunInInventory(b))) },
  ], best, PF.bestSort, "No successful runs yet. Benchmark something in the Playground.");

  const rows = perfRows();
  const maxTps = Math.max(1, ...rows.map((r) => r.tokens_per_second || 0));
  const groupBox = h("input", { type: "checkbox", checked: PF.grouped });
  groupBox.addEventListener("change", () => { PF.grouped = groupBox.checked; pfSave(); renderPerf(); });
  const names = [...new Set(rows.map((r) => r.base_model))];
  fill($("#fRunsHead"), h("h2", {}, `Runs (${rows.length})`), h("span", { class: "spacer" }),
    h("label", { class: "check" }, groupBox, "group by model"),
    PF.grouped ? [btn("Collapse all", () => { names.forEach((n) => PF.collapsed.add(n)); pfSave(); renderPerf(); }, "sm ghost"),
      btn("Expand all", () => { names.forEach((n) => PF.collapsed.delete(n)); pfSave(); renderPerf(); }, "sm ghost")] : null);
  renderTable($("#fTable"), [
    { key: "created_at", label: "When", cell: (r) => h("td", { class: "nowrap muted", title: new Date(r.created_at * 1000).toLocaleString() }, ago(r.created_at)) },
    { key: "base_model", label: PF.grouped ? "Profile" : "Model", cell: (r) => h("td", { class: "cell-model", title: r.launch_command },
      h("div", { class: "cell-main" }, PF.grouped ? r.profile_name || "(no profile)" : r.base_model),
      h("div", { class: "cell-sub" }, [PF.grouped ? null : r.profile_name, r.input_tokens ? `${num(r.input_tokens, 0)} in / ${num(r.output_tokens, 0)} out` : null].filter(Boolean).join(" · "))) },
    { key: "prompt_per_second", label: "Prefill t/s", num: true, cell: (r) => num(r.prompt_per_second, 0) },
    { key: "tokens_per_second", label: "Decode t/s", num: true, cell: (r) => tpsBar(r.tokens_per_second, maxTps) },
    { key: "n_ctx", label: "Ctx", num: true, cell: (r) => (r.n_ctx ? `${num(r.n_ctx / 1024, 0)}k` : DASH) },
    { label: "Settings", cell: (r) => h("td", { class: "settings-cell", title: r.layers_offloaded ? `layers on GPU: ${r.layers_offloaded}` : "" }, h("div", { class: "chips" }, settingChips({ ...r, n_ctx: null }))) },
    { key: "mem_used_gb", label: "VRAM · GPU", num: true, cell: (r) => h("td", { class: "num nowrap" }, r.mem_used_gb ? `${num(r.mem_used_gb, 1)} GiB` : DASH,
      r.gpu_util != null ? h("div", { class: "cell-sub" }, `${num(r.gpu_util, 0)}% busy`) : null) },
    { key: "log_source", label: "Src", cell: (r) => h("span", { class: `pill ${r.success ? "ok" : "bad"}`, title: r.note || "" }, r.success ? r.log_source || "run" : "no data") },
    { label: "", cell: (r) => h("td", { class: "actions nowrap" },
      iconBtn("sliders", "Open in builder", () => openRunInBuilder(r)),
      iconBtn("database", "Open in Inventory", () => openRunInInventory(r)),
      iconBtn("layers", "Compare with another run", () => addToCompare({ run_id: r.run_id, label: `${r.base_model} · ${r.profile_name || ago(r.created_at)}` })),
      iconBtn("file-text", "View log", () => showLog(r.launch_id || r.run_id)),
      iconBtn("trash", "Delete run", async () => {
        if (!(await confirmBox("Delete this benchmark run?"))) return;
        try { await api("DELETE", `/api/runs/${encodeURIComponent(r.run_id)}`); loadPerf(); } catch (e) { fail(e); }
      }, "danger")) },
  ], rows, perfSort, S.runs.length ? "No runs match." : "No runs recorded yet.", PF.grouped ? {
    group: (r) => ({ key: r.base_model, label: r.base_model }),
    groupOrder: (a, b) => Math.max(...b.rows.map((r) => r.tokens_per_second || 0)) - Math.max(...a.rows.map((r) => r.tokens_per_second || 0)),
    collapsed: PF.collapsed,
    onToggle: (k) => { PF.collapsed.has(k) ? PF.collapsed.delete(k) : PF.collapsed.add(k); pfSave(); renderPerf(); },
    groupExtra: (g) => {
      const ok = g.rows.filter((r) => r.success);
      const top = Math.max(0, ...ok.map((r) => r.tokens_per_second || 0));
      return h("span", { class: "group-meta" }, [top ? `best decode ${num(top)} t/s` : null, `last ${ago(Math.max(...g.rows.map((r) => r.created_at)))}`,
        g.rows.length - ok.length ? `${g.rows.length - ok.length} failed` : null].filter(Boolean).join(" · "));
    },
  } : {});
}

/** Jump to the model's page in Inventory: every load, its GPUs, VRAM and speeds. */
async function openRunInInventory(r) {
  show("inventory");
  try { if (!IV.data) IV.data = await api("GET", "/api/inventory"); } catch (e) { return fail(e); }
  const want = [r.model_id, r.base_model].filter(Boolean).map((s) => s.toLowerCase());
  const models = IV.data.models || [];
  const m = models.find((x) => want.includes((x.model_id || "").toLowerCase())) || models.find((x) => want.includes((x.name || "").toLowerCase()));
  if (!m) return toast(`${r.base_model || r.model_id} is not in Inventory yet. Press Scan there to add it.`, "err");
  openModel(m.model_id);
}
function exportCsv() {
  const rows = perfRows();
  if (!rows.length) { toast("Nothing to export.", "err"); return; }
  const cols = ["created_at", "base_model", "profile_name", "tokens_per_second", "prompt_per_second", "input_tokens", "output_tokens",
    "n_ctx", "gpu_layers", "layers_offloaded", "n_batch", "n_ubatch", "n_threads", "flash_attention", "cache_type_k", "cache_type_v",
    "mmap", "load_mode", "spec_type", "mmproj_file", "draft_model", "model_id", "draft_acc", "mem_used_gb", "gpu_util", "temp_c", "log_source", "success", "launch_command"];
  const esc = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const csv = [cols.join(",")].concat(rows.map((r) => cols.map((c) => esc(c === "created_at" ? new Date(r[c] * 1000).toISOString() : r[c])).join(","))).join("\n");
  const a = h("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: "benchmarks.csv" });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ── Catalog ──────────────────────────────────────────────────
const CATALOG = [
  { kind: "mmproj", title: "Vision projectors", flag: "--mmproj", hint: "Projectors kept outside the model's folder. (One beside the model is found automatically.)" },
  { kind: "draft", title: "Draft models", flag: "-md", hint: "Small models for speculative decoding with a bigger one. (MTP needs no file: it is --spec-type draft-mtp.)" },
];
function renderCatalog() {
  const models = () => ((S.library && S.library.models) || []).map((m) => ({ value: m.id, label: m.name, sub: m.quant }));
  fill($("#cCards"), CATALOG.map((c) => {
    const baseIn = h("input", { type: "text", placeholder: "Which model it is for", spellcheck: "false" });
    const pathIn = h("input", { type: "text", placeholder: "/app/models/…/file.gguf", spellcheck: "false" });
    const repoIn = h("input", { type: "text", placeholder: "org/repo (optional)", spellcheck: "false" });
    const add = async () => {
      try {
        await api("POST", "/api/catalog", { kind: c.kind, base_model: baseIn.value.trim(), file_path: pathIn.value.trim(), hf_repo: repoIn.value.trim() });
        toast("Added.", "ok");
        await Promise.all([loadCatalog(), loadModels()]);
      } catch (e) { fail(e); }
    };
    const fromHf = () => {
      if (!baseIn.value.trim()) return toast("Pick the model it is for first.", "err");
      hfPicker(c.kind, baseIn.value.trim(), repoIn.value.trim());
    };
    const files = S.catalog.filter((f) => f.kind === c.kind);
    const table = h("table", { class: "dense" });
    renderTable(table, [
      { key: "base_model", label: "For model", cell: (f) => modelCell(f.base_model) },
      { key: "name", label: "File", cell: (f) => modelCell(f.name, f.file_path || f.hf_repo, f.file_path || f.hf_repo) },
      { label: "", cell: (f) => h("td", { class: "actions" }, f.notes && f.notes.startsWith("downloading") ? h("span", { class: "pill warn" }, "downloading") : null,
        iconBtn("trash", "Remove", async () => { try { await api("DELETE", `/api/catalog/${f.id}`); loadCatalog(); } catch (e) { fail(e); } }, "danger")) },
    ], files, { key: null, asc: false }, "None yet.");
    return h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h2", {}, c.title), h("code", { class: "tk-flag mono-sm" }, c.flag)),
      h("p", { class: "hint" }, c.hint),
      h("div", { class: "fields" },
        h("label", { class: "field wide" }, h("span", {}, "For model"), attachCombo(baseIn, models)),
        h("label", { class: "field wide" }, h("span", {}, "File on disk"), h("div", { class: "row nowrap" }, pathIn,
          btn("Browse…", () => filePicker((p) => { pathIn.value = p; }), "sm", "search"))),
        h("label", { class: "field wide" }, h("span", {}, "Hugging Face repo"), h("div", { class: "row nowrap" }, repoIn,
          btn("Find & download…", fromHf, "sm", "download")))),
      h("div", { class: "row end", style: "margin:10px 0 6px" }, btn("Add", add, "primary sm", "plus")),
      h("div", { class: "table-wrap" }, table));
  }));
}

/** Browse the models folder (or search it) and pick a file. */
function filePicker(onPick) {
  let dir = "", close = null;
  const list = h("div", { class: "file-list" }), crumb = h("div", { class: "muted mono-sm" });
  const q = h("input", { type: "search", placeholder: "Search every folder…" });
  const row = (e) => h("div", { class: "file-row", onclick: () => (e.dir ? go(e.path) : (onPick(e.abs), close())) },
    h("span", {}, e.dir ? "📁 " : "", e.name), e.dir ? null : h("span", { class: "muted mono-sm" }, `${size(e.size)}${e.dir ? "" : ` · ${e.path}`}`));
  const go = async (d) => {
    dir = d === "." ? "" : d;
    try {
      const r = await api("GET", `/api/files?dir=${encodeURIComponent(dir)}`);
      crumb.textContent = `${r.root}/${r.dir}`;
      fill(list, r.parent != null ? h("div", { class: "file-row", onclick: () => go(r.parent) }, "⬆ up") : null,
        r.entries.length ? r.entries.map(row) : h("div", { class: "empty" }, "No model files here."));
    } catch (e) { fill(list, h("div", { class: "empty" }, e.message)); }
  };
  q.addEventListener("input", debounce(async () => {
    if (q.value.trim().length < 2) return go(dir);
    try { const r = await api("GET", `/api/files/search?q=${encodeURIComponent(q.value.trim())}`); crumb.textContent = `${r.results.length} found`; fill(list, r.results.map(row)); }
    catch (e) { fill(list, h("div", { class: "empty" }, e.message)); }
  }, 250));
  close = modal("Pick a file", h("div", { class: "stack" }, q, crumb, list), [{ label: "Cancel", run: (c) => c() }]);
  go("");
}

/** Search Hugging Face, pick a file from a repo, and download it into the model's own folder. */
function hfPicker(kind, baseModel, repoHint) {
  const results = h("div", { class: "file-list" }), filesBox = h("div", { class: "file-list" });
  const q = h("input", { type: "search", value: repoHint || baseModel.replace(/[-_.](Q\d|IQ\d|BF16|F16|UD).*$/i, ""), placeholder: "Search repos or paste org/repo" });
  const openRepo = async (id) => {
    fill(filesBox, h("div", { class: "empty" }, "Loading…"));
    try {
      const r = await api("GET", `/api/hf/repo?id=${encodeURIComponent(id)}`);
      const files = r.files.filter((f) => (kind === "mmproj" ? /mmproj/i.test(f.name) : !/mmproj/i.test(f.name)));
      fill(filesBox, h("div", { class: "muted mono-sm" }, `${id}: ${files.length} ${kind === "mmproj" ? "projector" : "model"} file(s)`),
        files.length ? files.map((f) => h("div", { class: "file-row" }, h("span", {}, f.name), h("span", { class: "row nowrap" }, h("span", { class: "muted mono-sm" }, size(f.size)),
          btn("Download & add", async () => {
            try {
              await api("POST", "/api/catalog/download", { kind, base_model: baseModel, repo: id, file: f.name });
              toast(`Downloading ${f.name} into the model's folder.`, "ok");
              loadCatalog(); loadDownloads();
            } catch (e) { fail(e); }
          }, "sm primary")))) : h("div", { class: "empty" }, "No matching files in that repo."));
    } catch (e) { fill(filesBox, h("div", { class: "empty" }, e.message)); }
  };
  const search = async () => {
    const v = q.value.trim();
    if (/^[\w.-]+\/[\w.-]+$/.test(v)) return openRepo(v);
    if (v.length < 2) return;
    fill(results, h("div", { class: "empty" }, "Searching…"));
    try {
      const r = await api("GET", `/api/hf/search?q=${encodeURIComponent(v)}`);
      fill(results, r.results.length ? r.results.map((x) => h("div", { class: "file-row", onclick: () => openRepo(x.id) }, h("span", {}, x.id),
        h("span", { class: "muted mono-sm" }, `${num(x.downloads, 0)} downloads`))) : h("div", { class: "empty" }, "No repos found."));
    } catch (e) { fill(results, h("div", { class: "empty" }, e.message)); }
  };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
  modal(`Download ${kind === "mmproj" ? "a vision projector" : "a draft model"} for ${baseModel}`, h("div", { class: "stack" },
    h("div", { class: "row nowrap" }, q, btn("Search", search, "sm primary", "search")), results, filesBox), [{ label: "Close", run: (c) => c() }]);
  search();
}

// ── Env flags ────────────────────────────────────────────────
async function loadEnv() {
  try { [S.env, S.flags] = [(await api("GET", "/api/env-flags")).rows, (await api("GET", "/api/flags")).rows]; } catch (e) { fail(e); return; }
  renderFlagsTable();
}
// One table for both: llama-server flags (--jinja, -lm dio, --temp …) and environment variables (HIP_VISIBLE_DEVICES …).
// Every row can be edited or removed, and you can add your own. Built-in rows you remove are hidden, not lost:
// "Show removed" lists them with Restore.
const flagSort = { key: null, asc: true };
const FLAGS = { kind: "all", showRemoved: false };
function renderFlagsTable() {
  const q = $("#eSearch").value.trim().toLowerCase();
  const flags = (S.flags || []).map((f) => ({ kind: "flag", id: f.flag, name: f.spellings.join(" · "), category: f.category, description: f.description,
    note: f.notes, profiles: f.profiles || 0, values: f.values || [], raw: f, search: `${f.flag} ${f.spellings.join(" ")} ${f.category} ${f.description} ${f.notes}` }));
  const env = (S.env || []).map((e) => ({ kind: "env", id: e.flag, name: e.flag, category: e.category || "", description: e.description, note: e.recommended,
    profiles: 0, values: [], last: e, raw: e, search: `${e.flag} ${e.category} ${e.description} ${e.recommended}` }));
  const all = [...flags, ...env];
  const removed = all.filter((r) => r.raw.removed);
  const rows = all.filter((r) => (FLAGS.kind === "all" || r.kind === FLAGS.kind) && (!q || r.search.toLowerCase().includes(q))
    && (FLAGS.showRemoved ? r.raw.removed : !r.raw.removed));
  const shown = (k) => all.filter((r) => r.kind === k && !r.raw.removed).length;
  $("#flagCount").textContent = `${shown("flag")} flags · ${shown("env")} environment variables`;
  const toggle = $("#flagRemoved");
  toggle.hidden = !removed.length && !FLAGS.showRemoved;
  toggle.textContent = FLAGS.showRemoved ? "Back to the list" : `Show removed (${removed.length})`;
  const act = async (r, method, suffix = "") => {
    try {
      const res = await api(method, `/api/flags/${r.kind}/${encodeURIComponent(r.id)}${suffix}`);
      toast(suffix ? `Restored ${r.name}.` : res.result === "deleted" ? `Deleted ${r.name}.` : `Removed ${r.name}. Show removed brings it back.`, "ok");
      loadEnv();
    } catch (e) { fail(e); }
  };
  renderTable($("#flagTable"), [
    { key: "name", label: "Name", sort: (r) => r.name.replace(/^-+/, "").toLowerCase(), cell: (r) => h("td", {}, h("code", { class: r.kind === "env" ? "tk-env" : "tk-flag" }, r.name),
      r.raw.custom ? h("span", { class: "pill ok", style: "margin-left:6px" }, "yours") : r.raw.edited ? h("span", { class: "pill", style: "margin-left:6px", title: "You changed this row; Restore puts the original back" }, "edited") : null) },
    { key: "kind", label: "Type", sort: (r) => r.kind, cell: (r) => h("span", { class: `pill${r.kind === "env" ? " accent" : ""}` }, r.kind === "env" ? "env" : "flag") },
    { key: "category", label: "Group", cell: (r) => (r.category ? h("span", { class: "pill" }, r.category) : DASH) },
    { label: "What it does", cell: (r) => h("td", { style: "min-width:260px" }, r.description || h("span", { class: "muted" }, "no description"), r.note ? h("div", { class: "muted mono-sm" }, r.note) : null) },
    { key: "use", label: "Your use", sort: (r) => r.profiles || (r.last && r.last.last_run_at) || 0, cell: (r) => r.kind === "flag"
      ? (r.profiles || r.values.length ? h("td", {}, r.profiles ? h("div", { class: "mono-sm" }, `${r.profiles} profile${r.profiles === 1 ? "" : "s"}`) : null,
        r.values.length ? h("div", { class: "chips" }, r.values.map((v) => h("span", { class: "chip" }, v))) : null) : DASH)
      : (r.last.last_used_for ? h("td", {}, h("div", { class: "mono-sm" }, r.last.last_used_for),
        h("div", { class: "muted mono-sm" }, [r.last.last_tps ? `${num(r.last.last_tps)} t/s gen` : null, r.last.last_pps ? `${num(r.last.last_pps)} prompt` : null,
          r.last.last_run_at ? ago(r.last.last_run_at) : null].filter(Boolean).join(" · "))) : DASH) },
    { label: "", cell: (r) => h("td", { class: "actions" }, r.raw.removed
      ? btn("Restore", () => act(r, "POST", "/restore"), "sm", "refresh")
      : [iconBtn("pencil", "Edit", () => flagForm(r)),
        r.raw.edited ? iconBtn("refresh", "Restore the original text", () => act(r, "POST", "/restore")) : null,
        iconBtn("trash", r.raw.custom ? "Delete" : "Remove from the list", async () => {
          const msg = r.raw.custom ? `Delete ${r.name}? You added it, so it is gone for good.` : `Remove ${r.name} from the list? It is hidden, and Show removed brings it back.`;
          if (await confirmBox(msg, r.raw.custom ? "Delete" : "Remove")) act(r, "DELETE");
        }, "danger")]) },
  ], rows, flagSort, FLAGS.showRemoved ? "Nothing removed." : "No matches.");
}

/** Add a flag or variable (no row), or edit one. Profiles are not changed: this is the reference list. */
function flagForm(r = null) {
  const kind = h("select", { disabled: !!r }, [["flag", "llama-server flag"], ["env", "Environment variable"]].map(([v, l]) => h("option", { value: v }, l)));
  kind.value = r ? r.kind : FLAGS.kind === "env" ? "env" : "flag";
  const name = h("input", { type: "text", value: r ? r.id : "", disabled: !!r, spellcheck: "false" });
  const spell = h("input", { type: "text", value: r && r.kind === "flag" ? r.raw.spellings.join(" ") : "", placeholder: "-c --ctx-size", spellcheck: "false" });
  const cat = h("input", { type: "text", value: r ? r.category : "", placeholder: "e.g. Speed, gpu-select" });
  const desc = h("textarea", { rows: 3, placeholder: "What it does" }, r ? r.description : "");
  const note = h("textarea", { rows: 2, placeholder: "Your notes: values that work, what to watch out for" }, r ? r.note || "" : "");
  const field = (label, input, hint) => h("label", { class: "field wide" }, h("span", {}, label), input, hint ? h("span", { class: "muted mono-sm" }, hint) : null);
  const spellField = field("Spellings", spell, "Every way it can be written, short first.");
  const syncKind = () => {
    name.placeholder = kind.value === "env" ? "HIP_VISIBLE_DEVICES" : "--temp";
    spellField.hidden = kind.value !== "flag";
  };
  kind.addEventListener("change", syncKind);
  syncKind();
  modal(r ? `Edit ${r.name}` : "Add to the Flags page", h("div", { class: "fields" },
    field("Type", kind), field("Name", name, r ? "" : "For a flag, any spelling works (-c is stored as ctx-size)."), spellField,
    field("Group", cat), field("What it does", desc), field(kind.value === "env" ? "Recommended" : "Notes", note)), [
    { label: "Cancel", run: (c) => c() },
    { label: r ? "Save" : "Add", cls: "primary", run: async (close) => {
      const k = kind.value;
      const b = { category: cat.value, description: desc.value, [k === "env" ? "recommended" : "notes"]: note.value };
      if (k === "flag" && spell.value.trim()) b.spellings = spell.value;
      try {
        if (r) await api("PUT", `/api/flags/${k}/${encodeURIComponent(r.id)}`, b);
        else await api("POST", `/api/flags/${k}`, { ...b, name: name.value });
        close();
        toast(r ? "Saved." : "Added.", "ok");
        loadEnv();
      } catch (e) { fail(e); }
    } }]);
}

// ── Integrations ─────────────────────────────────────────────
function renderIntegrationsNav() {
  const st = ODY.status;
  fill($("#navIntegrations"), st && st.configured ? h("span", { class: `dot ${st.connected ? "on" : "bad"}`, title: st.connected ? "Odysseus connected" : st.error || "" }) : null);
}function commandList(cmds) {
  const table = h("table");
  renderTable(table, [
    { key: "name", label: "Name", cell: (c) => h("span", { class: "strong" }, c.name) },
    { key: "kind", label: "Kind", cell: (c) => h("span", { class: "pill" }, c.kind) },
    { label: "", cell: (c) => h("td", { class: "actions" },
      iconBtn("sliders", "Open in builder", () => openCommand(c.command, { name: c.name, base_model: c.base_model, source: `${c.source} · ${c.kind}` })),
      iconBtn("save", "Save as profile", async () => {
        try { await api("POST", "/api/profiles", { name: c.name, base_model: c.base_model, source: c.source, launch_command: c.command }); toast(`Saved “${c.name}”.`, "ok"); loadProfiles(); } catch (e) { fail(e); }
      })) },
  ], cmds, { key: null, asc: false }, "No launch commands found.");
  return h("div", { class: "table-wrap" }, table);
}
const statusLine = (ok, text) => h("div", { class: "row", style: "margin-bottom:8px; flex-wrap:nowrap; align-items:baseline" },
  h("span", { class: `dot ${ok ? "on" : "bad"}` }), h("span", { class: ok ? "" : "muted", style: "min-width:0; overflow-wrap:anywhere" }, text));
const setupHint = (lines) => h("div", { class: "stack" }, h("p", { class: "hint", style: "margin:0" }, "Not configured. Set these environment variables and restart:"),
  h("pre", { class: "code" }, lines.join("\n")));
// ── Odysseus hub ─────────────────────────────────────────────
const ODY = { status: null, area: "notes", data: {} };
const ODY_AREAS = { notes: "Notes", brain: "Brain", calendar: "Calendar", library: "Library", cookbook: "Cookbook", tasks: "Tasks", gallery: "Gallery", tools: "Tools" };
const ODY_SCOPES = { notes: "todos:read / todos:write", brain: "memory:read / memory:write", calendar: "calendar:read / calendar:write",
  library: "documents:read / documents:write", cookbook: "cookbook:read", tasks: "tasks:read / tasks:run", gallery: "gallery:read", tools: "skills:read" };

async function loadOdyStatus() {
  try { ODY.status = await api("GET", "/api/ody/status"); } catch (e) { ODY.status = { configured: false, areas: {} }; }
  renderIntegrationsNav();
  return ODY.status;
}
function renderIntegrations() {
  const st = ODY.status, o = (S.settings && S.settings.odysseus) || {};
  const url = h("input", { type: "text", value: o.url || "", placeholder: "http://odysseus:7000", spellcheck: "false" });
  const token = h("input", { type: "password", value: o.token || "", placeholder: "ody_… from Odysseus → Settings → API tokens" });
  const brain = h("input", { type: "checkbox", checked: o.brain_in_memory !== false });
  const presets = h("input", { type: "checkbox", checked: o.use_presets !== false });
  const save = async () => {
    try {
      await api("PUT", "/api/settings/odysseus", { url: url.value.trim(), token: token.value, brain_in_memory: brain.checked, use_presets: presets.checked });
      await loadSettings(); await loadOdyStatus(); renderIntegrations(); loadOdyArea(ODY.area);
      toast(ODY.status.connected ? "Connected to Odysseus." : (ODY.status.error || "Saved."), ODY.status.connected ? "ok" : "err");
    } catch (e) { fail(e); }
  };
  const chips = st && st.configured ? Object.entries(ODY_AREAS).map(([k, label]) => {
    const okA = st.areas && st.areas[k];
    const needsUpdate = !okA && ["gallery", "tasks", "tools"].includes(k) && st.connected && !st.updated;
    return h("span", { class: `pill ${okA ? "ok" : needsUpdate ? "warn" : ""}`, title: okA ? "allowed" : needsUpdate ? "this Odysseus needs the update that adds it" : `give the token ${ODY_SCOPES[k]}` },
      `${label}${okA ? "" : needsUpdate ? " · update" : " · no access"}`);
  }) : null;
  const cb = S.integrations && S.integrations.odysseus;
  // Pulling makes Argos profiles out of Odysseus' Cookbook presets. It is idempotent,
  // and it says what it could not use and why: a silent zero is what made this look
  // like it had done nothing.
  const pullOut = h("div", { style: "margin-top:10px" });
  const pullBtn = btn("Pull presets into profiles", async () => {
    pullBtn.disabled = true; pullBtn.textContent = "Pulling…";
    try {
      const r = await api("POST", "/api/ody/pull", undefined, 0);
      const rows = [];
      const add = (title, items, cls, sub) => {
        if (!items || !items.length) return;
        rows.push(h("div", { class: "grp" }, `${title} (${items.length})`));
        for (const x of items) rows.push(h("div", { class: `ody-row ${cls}` },
          h("div", { style: "min-width:0" }, h("div", { class: "t" }, x.label || ""),
            h("div", { class: "s" }, sub ? sub(x) : (x.reason || "")))));
      };
      add("New profiles", r.imported, "ok", (x) => x.model_id || "");
      add("Updated from Odysseus", r.updated, "warn", (x) => x.model_id || "");
      add("Already here, unchanged", r.skipped, "", (x) => x.reason);
      add("Could not be used", r.failed, "bad", (x) => x.reason);
      fill(pullOut, r.note ? h("p", { class: "hint", style: "margin:0 0 6px" }, r.note) : null, ...rows,
        ((r.imported || []).length || (r.updated || []).length)
          ? h("p", { class: "hint", style: "margin:6px 0 0" },
              "They are under Profiles now. If that page lists fewer, clear its model filter.")
          : null);
      // The Profiles page renders a cached list, so a pull has to refresh it: without
      // this the profiles exist and the page still says zero.
      await loadProfiles();
      if ((r.imported || []).length || (r.updated || []).length) {
        toast(`Pulled ${(r.imported || []).length} new and ${(r.updated || []).length} updated profile(s).`, "ok");
      } else if ((r.failed || []).length) {
        toast("Nothing could be pulled — the reasons are listed below.", "err");
      } else {
        toast("Already up to date.", "");
      }
    } catch (e) { fail(e); } finally { pullBtn.disabled = false; pullBtn.textContent = "Pull presets into profiles"; }
  }, "sm", "download");

  fill($("#oConn"),
    h("div", { class: "card-head" }, h("h2", {}, "Connection"), h("span", { class: "spacer" }),
      st && st.configured ? h("span", { class: `pill ${st.connected ? "ok" : "bad"}` }, st.connected ? "connected" : "not connected") : null),
    h("div", { class: "fields" },
      h("label", { class: "field" }, h("span", {}, "Odysseus URL"), url),
      h("label", { class: "field" }, h("span", {}, "API token"), token),
      h("label", { class: "check wide" }, brain, "Let the assistant's memory include Odysseus' Brain"),
      h("label", { class: "check wide" }, presets, "Use Odysseus' saved Cookbook presets as models")),
    h("div", { class: "row", style: "margin-top:10px" }, btn("Save & connect", save, "primary sm", "save"),
      pullBtn,
      st && st.error ? h("span", { class: "hint", style: "margin:0" }, st.error) : null),
    pullOut,
    chips ? h("div", { class: "chips", style: "margin-top:10px" }, chips) : null,
    h("p", { class: "hint" }, "Make the token in Odysseus → Settings → API tokens with the scopes for what you want here: ",
      Object.values(ODY_SCOPES).join(", "), ". Gallery, tasks and tools need the Odysseus update that adds them to its API."),
    cb && cb.configured ? h("p", { class: "hint" }, cb.ok ? `Cookbook presets from Odysseus: ${cb.commands.length}. They show up under Import in the builder and are served to the engine.` : `Cookbook presets: ${cb.error}`) : null,
    st && st.connected && st.capabilities === false ? h("p", { class: "hint" }, "This Odysseus does not list its capabilities, so Argos tried each area. Anything it lacks is simply left out.") : null);
  loadOdyArea(ODY.area);
}
function odyRow(main, sub, actions) {
  return h("div", { class: "ody-row" }, h("div", { class: "ody-main" }, h("div", { class: "cell-main" }, main), sub ? h("div", { class: "cell-sub" }, sub) : null),
    actions ? h("div", { class: "row nowrap" }, actions) : null);
}
function odyForm(fields, label, send) {
  const inputs = fields.map((f) => (f.type === "textarea" ? h("textarea", { rows: 3, placeholder: f.ph || "" }) : h("input", { type: f.type || "text", placeholder: f.ph || "" })));
  const go = async () => {
    const vals = Object.fromEntries(fields.map((f, i) => [f.k, inputs[i].value]));
    try { await send(vals); inputs.forEach((x) => { x.value = ""; }); toast("Sent to Odysseus.", "ok"); loadOdyArea(ODY.area); } catch (e) { fail(e); }
  };
  return h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, label)),
    h("div", { class: "fields" }, fields.map((f, i) => h("label", { class: `field${f.wide ? " wide" : ""}` }, h("span", {}, f.label), inputs[i]))),
    h("div", { class: "row end", style: "margin-top:8px" }, btn("Send", go, "primary sm", "arrow-up")));
}
async function loadOdyArea(area) {
  ODY.area = area;
  const el = $(`#oArea-${area}`);
  if (!el) return;
  const st = ODY.status;
  if (!st || !st.configured) return fill(el, h("div", { class: "card" }, h("div", { class: "empty" }, "Connect Odysseus above.")));
  if (!st.areas[area]) return fill(el, h("div", { class: "card" }, h("div", { class: "empty" },
    ["gallery", "tasks", "tools"].includes(area) && !st.updated ? "This Odysseus does not offer this to API tokens yet. It needs the update that adds gallery, tasks and tools."
      : `The API token does not allow this. Give it ${ODY_SCOPES[area]} in Odysseus.`)));
  fill(el, h("div", { class: "empty" }, "Loading…"));
  let d;
  try { d = await api("GET", `/api/ody/${area}`); } catch (e) { return fill(el, h("div", { class: "card" }, h("div", { class: "empty" }, e.message))); }
  const w = st.writable || {};
  const list = (items, row, empty) => h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, `${ODY_AREAS[area]} (${items.length})`)),
    items.length ? h("div", { class: "ody-list" }, items.map(row)) : h("div", { class: "empty" }, empty));
  const when = (v) => (v ? new Date(v).toLocaleString() : "");
  if (area === "notes") {
    fill(el, w.notes ? odyForm([{ k: "title", label: "Title" }, { k: "content", label: "Text", type: "textarea", wide: true }], "New note", (v) => api("POST", "/api/ody/notes", v)) : null,
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Notes")), h("pre", { class: "pre" }, d.text || "No notes.")));
  } else if (area === "brain") {
    fill(el, w.brain ? odyForm([{ k: "text", label: "Something to remember", wide: true }, { k: "category", label: "Kind", ph: "fact" }], "Teach the Brain", (v) => api("POST", "/api/ody/brain", v)) : null,
      list(d.items, (m) => odyRow(m.text || m.content || JSON.stringify(m), [m.category, when(m.created_at)].filter(Boolean).join(" · ")), "The Brain is empty."));
  } else if (area === "calendar") {
    fill(el, w.calendar ? odyForm([{ k: "summary", label: "Title" }, { k: "start", label: "Starts", type: "datetime-local" }, { k: "end", label: "Ends", type: "datetime-local" },
      { k: "description", label: "Details", type: "textarea", wide: true }], "New event", (v) => api("POST", "/api/ody/calendar", v)) : null,
      list(d.items, (e) => odyRow(e.summary || e.title || "event", [when(e.dtstart || e.start), e.location].filter(Boolean).join(" · ")), "No events from a week ago to a month ahead."));
  } else if (area === "library") {
    fill(el, w.library ? odyForm([{ k: "title", label: "Title" }, { k: "content", label: "Content (markdown)", type: "textarea", wide: true }], "New document", (v) => api("POST", "/api/ody/library", v)) : null,
      list(d.items, (x) => odyRow(x.title || "untitled", [x.language, when(x.updated_at || x.created_at)].filter(Boolean).join(" · ")), "The library is empty."));
  } else if (area === "cookbook") {
    fill(el, list(d.items, (p) => odyRow(p.name || "preset", p.cmd ? p.cmd.slice(0, 160) : p.model,
      p.cmd ? btn("Open in builder", () => openCommand(p.cmd, { name: p.name || "", source: "odysseus" }), "sm", "sliders") : null), "No cookbook presets."),
      (d.servers || []).length ? h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Servers")), h("div", { class: "chips" }, d.servers.map((x) => h("span", { class: "chip" }, x.host || x.name || JSON.stringify(x))))) : null);
  } else if (area === "tasks") {
    fill(el, list(d.items, (t) => odyRow(t.name || t.title || t.id, [t.status, t.schedule || t.cron, t.last_run_at ? `last ${when(t.last_run_at)}` : ""].filter(Boolean).join(" · "),
      w.tasks ? btn("Run now", async () => { try { await api("POST", `/api/ody/tasks/${encodeURIComponent(t.id)}/run`); toast(`Started “${t.name || t.id}”.`, "ok"); } catch (e) { fail(e); } }, "sm", "play") : null), "No scheduled tasks."));
  } else if (area === "gallery") {
    const base = (S.settings.odysseus.url || "").replace(/\/$/, "");
    fill(el, list(d.items, (g) => odyRow(g.filename || g.name || g.id, [g.tags, g.model, when(g.created_at)].filter(Boolean).join(" · "),
      base ? h("a", { class: "btn sm ghost", href: `${base}/gallery`, target: "_blank", rel: "noopener" }, "Open in Odysseus") : null), "No images."));
  } else {
    fill(el, list(d.items, (t) => odyRow(t.name || t.id, [t.category, t.description].filter(Boolean).join(" · ")), "No tools."));
  }
}

/** The builder's Odysseus button: send the saved preset as a note, a Brain fact, a document or a calendar reminder. */
async function sendToOdysseus() {
  if (!B.profileId) return toast("Save the preset first.", "err");
  if (!ODY.status) await loadOdyStatus();
  const st = ODY.status;
  if (!st.configured || !st.connected) return toast(st.error || "Connect Odysseus first (the Odysseus page).", "err");
  const when = h("input", { type: "datetime-local" });
  const go = (what, extra = {}) => async (close) => {
    try { await api("POST", "/api/ody/send", { profile_id: B.profileId, what, ...extra }, 0); toast(`Sent to Odysseus as a ${what}.`, "ok"); close(); } catch (e) { fail(e); }
  };
  const w = st.writable || {};
  modal("Send this preset to Odysseus", h("div", { class: "stack" },
    h("p", { class: "hint", style: "margin:0" }, "Sends the launch command and, when there is any, the speeds measured for each launch config of this model."),
    h("label", { class: "field" }, h("span", {}, "Reminder time (for a calendar event)"), when)), [
    w.notes ? { label: "Note", icon: "file-text", run: go("note") } : null,
    w.brain ? { label: "Remember in Brain", icon: "database", run: go("brain") } : null,
    w.library ? { label: "Library document", icon: "file-code", run: go("document") } : null,
    w.calendar ? { label: "Calendar reminder", icon: "plus", run: (c) => go("event", when.value ? { start: when.value } : {})(c) } : null,
    { label: "Cancel", run: (c) => c() }].filter(Boolean));
}

// ── Models: library + prefabs ────────────────────────────────
// binary units throughout: VRAM, the Schedule and the engine all count in GiB, so file sizes do too
const GB = 2 ** 30;
const size = (b) => (b >= GB ? `${(b / GB).toFixed(b >= 10 * GB ? 0 : 2)} GiB` : `${Math.max(1, Math.round(b / 2 ** 20))} MiB`);
const librarySort = { key: null, asc: true };

async function loadLibrary() {
  try { S.library = await api("GET", "/api/library"); } catch (e) { fail(e); return; }
  try { ML.groups = (await api("GET", "/api/library/groups")).groups; } catch (e) { /* groups are optional */ }
  $("#navModels").textContent = S.library.models.length || "";
  fillProfileSelect();
  if (S.view === "models") renderLibrary();
}
async function editModel(m) {
  if (!(await leaveOk())) return;
  const ps = presetsFor(m.id);
  if (ps.length) openProfile(ps[0]); else openPrefab(m);
}
async function openPrefab(m, launchIt = false) {
  try {
    const res = await api("POST", "/api/library/prefab", { path: m.path });
    if (launchIt) launch(res.command, m.name, "");
    else openCommand(res.command, { name: "", base_model: m.name, source: "prefab" });
  } catch (e) { fail(e); }
}
// The library is a tree: grouped by kind, family (all Qwen together), folder, or your own groups, and every group
// collapses. Where you left it (grouping, closed groups) is remembered in this browser.
const ML = { groups: [], by: "kind", collapsed: new Set() };
try {
  ML.by = localStorage.getItem("argos.models.by") || "kind";
  ML.collapsed = new Set(JSON.parse(localStorage.getItem("argos.models.closed") || "[]"));
} catch (e) { /* private window: defaults */ }
const mlSave = () => { try { localStorage.setItem("argos.models.by", ML.by); localStorage.setItem("argos.models.closed", JSON.stringify([...ML.collapsed])); } catch (e) { /* ignore */ } };
const KIND_GROUPS = [["chat", "Chat"], ["vision", "Vision"], ["mtp", "MTP / draft"], ["embedding", "Embedding"], ["reranker", "Reranker"], ["audio", "Audio"], ["image", "Image"]];
const cap1 = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

/** "Qwen" for qwen3.6-35b…, "Hermes" for NousResearch_Hermes-4…, the folder for audio/image models. */
function modelFamily(m) {
  if (m.listed_only) {
    const seg = (m.dir || "").split(/[\\/]/).filter((x) => x && x !== ".").pop();
    return cap1(seg || m.kind);
  }
  let n = m.name.replace(/^[A-Z][A-Za-z0-9]*_(?=[A-Za-z])/, "").toLowerCase().replace(/^(mtp|mmproj|draft)[-_]/, "");
  return cap1((n.match(/^[a-z]+/) || [m.kind])[0]);
}
function myGroupOf(m) { return ML.groups.find((g) => g.models.includes(m.id)); }
function libraryGroupOf(m) {
  if (ML.by === "kind") { const k = KIND_GROUPS.find(([v]) => v === m.kind); return { key: `k:${m.kind}`, label: k ? k[1] : cap1(m.kind) }; }
  if (ML.by === "family") { const f = modelFamily(m); return { key: `f:${f}`, label: f }; }
  if (ML.by === "folder") return { key: `d:${m.dir}`, label: m.dir === "." ? "(top of the models folder)" : m.dir };
  const g = myGroupOf(m);
  return g ? { key: `g:${g.name}`, label: g.name } : { key: "g:", label: "Ungrouped" };
}
function libraryGroupOrder(a, b) {
  if (ML.by === "kind") return KIND_GROUPS.findIndex(([v]) => `k:${v}` === a.key) - KIND_GROUPS.findIndex(([v]) => `k:${v}` === b.key);
  if (ML.by === "mine") {
    const i = (g) => (g.key === "g:" ? 1e6 : ML.groups.findIndex((x) => `g:${x.name}` === g.key));
    return i(a) - i(b);
  }
  return a.label.localeCompare(b.label, undefined, { numeric: true, sensitivity: "base" });
}
async function saveGroups(groups, msg) {
  try {
    ML.groups = (await api("PUT", "/api/library/groups", { groups })).groups;
    if (msg) toast(msg, "ok");
    renderLibrary();
  } catch (e) { fail(e); }
}
/** Put the models into groups by family (Qwen, Gemma, …). Models already in one of your groups stay where they are. */
function autoGroup() {
  const lib = S.library;
  const all = [...lib.models, ...(lib.media || [])];
  const groups = ML.groups.map((g) => ({ name: g.name, models: [...g.models] }));
  let moved = 0;
  for (const m of all) {
    if (myGroupOf(m)) continue;
    const f = modelFamily(m);
    let g = groups.find((x) => x.name.toLowerCase() === f.toLowerCase());
    if (!g) { g = { name: f, models: [] }; groups.push(g); }
    g.models.push(m.id);
    moved++;
  }
  ML.by = "mine"; mlSave();
  saveGroups(groups, moved ? `Grouped ${moved} model${moved === 1 ? "" : "s"} by name.` : "Every model is already in a group.");
}
function newGroupBox(m = null) {
  const name = h("input", { type: "text", placeholder: "e.g. Daily drivers", spellcheck: "false" });
  const existing = h("select", {}, [h("option", { value: "" }, "a new group…"), ...ML.groups.map((g) => h("option", { value: g.name }, g.name))]);
  const sync = () => { name.disabled = !!existing.value; };
  existing.addEventListener("change", sync);
  const body = m ? h("div", { class: "fields" },
    h("label", { class: "field wide" }, h("span", {}, "Group"), existing),
    h("label", { class: "field wide" }, h("span", {}, "New group name"), name))
    : h("div", { class: "fields" }, h("label", { class: "field wide" }, h("span", {}, "Name"), name));
  modal(m ? `Move ${m.name}` : "New group", body, [
    { label: "Cancel", run: (c) => c() },
    { label: m ? "Move" : "Create", cls: "primary", run: (close) => {
      const target = (m && existing.value) || name.value.trim();
      if (!target) return toast("Type a name for the group.", "err");
      const groups = ML.groups.map((g) => ({ name: g.name, models: g.models.filter((x) => !m || x !== m.id) }));
      let g = groups.find((x) => x.name.toLowerCase() === target.toLowerCase());
      if (!g) { g = { name: target, models: [] }; groups.push(g); }
      if (m) g.models.push(m.id);
      close();
      ML.by = "mine"; mlSave();
      saveGroups(groups, m ? `Moved ${m.name} to ${g.name}.` : `Made the group ${g.name}. Move models into it with ⋯.`);
    } }]);
  setTimeout(() => (m && ML.groups.length ? existing : name).focus(), 30);
}
async function deleteModel(m) {
  let info;
  try { info = await api("POST", "/api/library/delete/check", { path: m.path }); } catch (e) { return fail(e); }
  const withProj = h("input", { type: "checkbox" });
  const body = h("div", { class: "stack" },
    h("p", { style: "margin:0" }, `Delete ${m.name} (${size(info.size)}) from the models folder? This removes the file${info.files.length > 1 ? "s" : ""} from disk and cannot be undone.`),
    h("pre", { class: "pre", style: "max-height:140px" }, info.files.join("\n")),
    info.profiles.length ? h("p", { class: "hint", style: "margin:0;color:var(--warn)" },
      `${info.profiles.length} profile${info.profiles.length === 1 ? " uses" : "s use"} it and will stop working until the file is back: ${info.profiles.join(", ")}.`) : null,
    info.mmproj.length ? h("label", { class: "check" }, withProj, `Also delete its vision projector (${info.mmproj.map((p) => p.split(/[\\/]/).pop()).join(", ")})`) : null);
  modal("Delete model", body, [
    { label: "Cancel", run: (c) => c() },
    { label: "Delete", cls: "danger", icon: "trash", run: async (close) => {
      close();
      try {
        const r = await api("POST", "/api/library/delete", { path: m.path, with_mmproj: withProj.checked });
        toast(r.problems.length ? `Deleted ${r.removed.length} file(s); ${r.problems[0]}` : `Deleted ${m.name}.`, r.problems.length ? "err" : "ok");
      } catch (e) { fail(e); }
      loadLibrary();
    } }]);
}
function modelMenu(btnEl, m) {
  const g = myGroupOf(m);
  popMenu(btnEl, [
    { label: "Move to group…", icon: "layers", run: () => newGroupBox(m) },
    g ? { label: `Take out of ${g.name}`, icon: "x", run: () => saveGroups(ML.groups.map((x) => ({ name: x.name, models: x.models.filter((id) => id !== m.id) }))) } : null,
    { label: "Delete from disk…", icon: "trash", danger: true, run: () => deleteModel(m) },
  ]);
}

function renderLibrary() {
  const lib = S.library;
  if (!lib) return;
  $("#mSub").textContent = `Every model in ${lib.models_dir}. GGUFs get a ready-made prefab command; audio and image models are listed so you can see what you have.`;
  const q = $("#mSearch").value.trim().toLowerCase();
  const media = lib.media || [];
  const all = [...lib.models, ...media];
  const rows = all.filter((m) => !q || `${m.name} ${m.dir} ${m.kind} ${m.task || ""} ${m.quant} ${m.format || ""} ${modelFamily(m)} ${(myGroupOf(m) || {}).name || ""}`.toLowerCase().includes(q));
  const audio = media.filter((m) => m.kind === "audio").length, image = media.length - audio;
  $("#mCount").textContent = [`${lib.models.length} model${lib.models.length === 1 ? "" : "s"}`, audio ? `${audio} audio` : null, image ? `${image} image` : null].filter(Boolean).join(" · ");

  // grouping controls
  const by = h("select", { title: "Group the list", "aria-label": "Group by" }, [["kind", "Group: kind"], ["family", "Group: name (Qwen, Gemma…)"], ["folder", "Group: folder"],
    ["mine", "Group: my groups"], ["none", "No groups"]].map(([v, l]) => h("option", { value: v }, l)));
  by.value = ML.by;
  by.addEventListener("change", () => { ML.by = by.value; mlSave(); renderLibrary(); });
  const keys = () => [...new Set(rows.map((m) => libraryGroupOf(m).key))];
  fill($("#mGroupBar"), by,
    ML.by !== "none" ? [btn("Collapse all", () => { keys().forEach((k) => ML.collapsed.add(k)); mlSave(); renderLibrary(); }, "sm ghost"),
      btn("Expand all", () => { keys().forEach((k) => ML.collapsed.delete(k)); mlSave(); renderLibrary(); }, "sm ghost")] : null,
    h("span", { class: "spacer" }),
    btn("Auto group", autoGroup, "sm", "layers"), btn("New group", () => newGroupBox(), "sm ghost", "plus"));

  const empty = !lib.exists
    ? h("span", {}, `The models folder ${lib.models_dir} does not exist. Mount your GGUF folder there (e.g. `, h("b", {}, "-v /path/to/LLMBin:/app/models"), ") or change it in Settings.")
    : all.length ? "No models match." : h("span", {}, "No .gguf files yet. Get one from the ", h("b", {}, "Download"), " tab.");
  const groupOpts = ML.by === "none" ? {} : {
    group: libraryGroupOf, groupOrder: libraryGroupOrder, collapsed: q ? new Set() : ML.collapsed,
    onToggle: (k) => { ML.collapsed.has(k) ? ML.collapsed.delete(k) : ML.collapsed.add(k); mlSave(); renderLibrary(); },
    groupExtra: (g) => {
      const total = g.rows.reduce((a, m) => a + (m.size || 0), 0);
      const mine = ML.by === "mine" && g.key !== "g:" ? ML.groups.find((x) => `g:${x.name}` === g.key) : null;
      return [h("span", { class: "group-meta" }, size(total)), mine ? h("span", { class: "group-actions" },
        iconBtn("pencil", "Rename group", () => {
          const name = h("input", { type: "text", value: mine.name });
          modal("Rename group", h("label", { class: "field wide" }, h("span", {}, "Name"), name), [{ label: "Cancel", run: (c) => c() },
            { label: "Rename", cls: "primary", run: (c) => { c(); saveGroups(ML.groups.map((x) => (x === mine ? { ...x, name: name.value.trim() || x.name } : x))); } }]);
        }, "ghost"),
        iconBtn("x", "Remove the group (its models stay, ungrouped)", async () => {
          if (await confirmBox(`Remove the group ${mine.name}? Its ${mine.models.length} model(s) stay in the library, ungrouped.`, "Remove group")) saveGroups(ML.groups.filter((x) => x !== mine));
        }, "ghost")) : null];
    },
  };
  renderTable($("#mTable"), [
    { key: "name", label: "Model", cell: (m) => h("td", { class: "trunc", title: m.path }, h("span", { class: "strong" }, m.name),
      h("div", { class: "muted mono-sm" }, m.dir === "." ? m.id : `${m.dir} · ${m.id}`)) },
    { key: "kind", label: "Kind", cell: (m) => h("td", {}, h("span", { class: `pill${m.kind === "chat" ? "" : m.listed_only ? ` media-${m.kind}` : " accent"}` }, m.kind),
      m.task ? h("div", { class: "muted mono-sm" }, m.task) : null) },
    { key: "quant", label: "Quant", cell: (m) => (m.quant ? h("span", { class: "chip" }, m.quant) : DASH) },
    { key: "size", label: "Size", num: true, cell: (m) => size(m.size) },
    { label: "Files", cell: (m) => h("div", { class: "chips" },
      m.shards < 0 ? h("span", { class: "pill bad" }, `incomplete ${-m.shards} parts`) : m.shards > 1 ? h("span", { class: "chip" }, `${m.shards} parts`) : null,
      m.mmproj.length ? h("span", { class: "chip", title: m.mmproj.join("\n") }, "mmproj") : null,
      m.format && m.format !== "gguf" ? h("span", { class: "chip" }, m.format) : null) },
    { key: "profiles", label: "Profiles", num: true, cell: (m) => (m.profiles
      ? h("button", { class: "btn sm ghost", onclick: () => showProfilesFor(m.id) }, String(m.profiles)) : DASH) },
    { label: "", cell: (m) => {
      const more = h("button", { class: "icon-btn ghost", title: "More: move to a group, delete", "aria-label": "More actions" }, "⋯");
      more.addEventListener("click", () => modelMenu(more, m));
      return m.listed_only
        ? h("td", { class: "actions" }, h("span", { class: "muted mono-sm", title: "llama-server does not run audio or image models, so there is no profile to edit or launch." }, "listed only"), more)
        : h("td", { class: "actions" }, btn("Edit", () => editModel(m), "sm primary", "pencil"), btn("Prefab", () => openPrefab(m), "sm", "sliders"),
          iconBtn("play", "Launch the prefab", () => openPrefab(m, true)), more);
    } },
  ], rows, librarySort, empty, groupOpts);
}

// ── Models: Hugging Face downloader ──────────────────────────
const fitPill = { fits: ["ok", "fits"], tight: ["warn", "tight"], "too big": ["bad", "too big"], unknown: ["", "?"] };
let currentRepo = null;

async function hfSearch() {
  const q = $("#dQuery").value.trim();
  if (/^[\w.-]+\/[\w.-]+$/.test(q)) { loadRepo(q); return; }
  fill($("#dResults"), h("div", { class: "empty" }, "Searching…"));
  try {
    const res = await api("GET", `/api/hf/search?q=${encodeURIComponent(q)}`);
    fill($("#dResults"), res.results.length ? res.results.map((r) => h("div", {
      class: `repo${currentRepo === r.id ? " sel" : ""}`, onclick: () => loadRepo(r.id), role: "button", tabindex: "0",
      onkeydown: (e) => { if (e.key === "Enter") loadRepo(r.id); } },
      h("span", { class: "id" }, r.id), r.gated ? h("span", { class: "pill warn" }, "gated") : null,
      h("span", { class: "muted mono-sm" }, `↓ ${num(r.downloads, 0)}`))) : h("div", { class: "empty" }, "No GGUF repos found."));
  } catch (e) { fill($("#dResults"), h("div", { class: "empty" }, e.message)); }
}

async function loadRepo(id) {
  currentRepo = id;
  $$("#dResults .repo").forEach((r) => r.classList.toggle("sel", r.firstChild.textContent === id));
  const card = $("#dRepo");
  fill(card, h("div", { class: "empty" }, `Loading ${id}…`));
  let info;
  try { info = await api("GET", `/api/hf/repo?id=${encodeURIComponent(id)}`); } catch (e) { fill(card, h("div", { class: "empty" }, e.message)); return; }
  $("#dVram").textContent = info.vram_gb ? `fit vs ${info.vram_gb} GB VRAM` : "VRAM unknown — set it in Settings";
  const quantTable = h("table");
  renderTable(quantTable, [
    { label: "Quant", cell: (q) => h("span", { class: "strong", title: q.files.join("\n") }, q.quant) },
    { label: "Size", num: true, cell: (q) => size(q.size) },
    { label: "Parts", num: true, cell: (q) => q.shards },
    { label: "Fit", cell: (q) => { const [c, t] = fitPill[q.fit] || fitPill.unknown; return h("span", { class: `pill ${c}` }, t); } },
    { label: "", cell: (q) => h("td", { class: "actions" }, q.downloaded ? h("span", { class: "pill ok" }, "downloaded")
      : btn("Download", () => startDownload(info.id, q.files), "sm primary", "download")) },
  ], info.quants, { key: null, asc: true }, "No GGUF files in this repo.");
  fill(card,
    h("div", { class: "card-head" }, h("h2", {}, info.id), info.gated ? h("span", { class: "pill warn" }, "gated: needs HF token") : null),
    h("p", { class: "hint" }, `Saves to ${info.dest}. Fit is a rough guide: weights + 15% + 1 GB for cache and buffers.`),
    h("div", { class: "table-wrap" }, quantTable),
    info.mmproj.length ? [h("h2", { style: "font-size:12px;margin:14px 0 6px" }, "Vision projectors"),
      h("div", { class: "stack" }, info.mmproj.map((f) => h("div", { class: "row" },
        h("span", { style: "flex:1" }, f.name), h("span", { class: "muted mono-sm" }, size(f.size)),
        btn("Download", () => startDownload(info.id, [f.name]), "sm", "download"))))] : null);
}

async function startDownload(repo, files) {
  try {
    await api("POST", "/api/downloads", { repo, files });
    toast(`Downloading ${files.length > 1 ? `${files.length} files` : files[0]}…`, "ok");
    loadDownloads();
  } catch (e) { fail(e); }
}

async function loadDownloads() {
  try { S.downloads = (await api("GET", "/api/downloads")).downloads; } catch (e) { return; }
  const active = S.downloads.filter((d) => d.status === "running" || d.status === "queued");
  const meta = $("#navModels");
  if (active.length) meta.textContent = `↓ ${active.length}`;
  else if (S.library) meta.textContent = S.library.models.length || "";
  if (S.settings) $("#dDest").textContent = `into ${S.settings.paths.models_dir}`;
  renderDownloads();
  if (!active.length && S._hadActiveDownloads) {  // something just finished
    loadLibrary();
    if (currentRepo) loadRepo(currentRepo);
  }
  S._hadActiveDownloads = !!active.length;
}
function renderDownloads() {
  const jobs = S.downloads.slice(0, 20);
  fill($("#dJobs"), jobs.length ? jobs.map((d) => {
    const pct = d.bytes_total ? Math.min(100, (d.bytes_done / d.bytes_total) * 100) : 0;
    const running = d.status === "running" || d.status === "queued";
    const eta = running && d.speed > 0 ? Math.round((d.bytes_total - d.bytes_done) / d.speed) : null;
    const cls = d.status === "done" ? "ok" : d.status === "failed" ? "bad" : running ? "accent" : "warn";
    return h("div", { class: "stack", style: "padding:8px 0;border-bottom:1px solid var(--line)" },
      h("div", { class: "row" }, h("span", { class: "strong", style: "flex:1;min-width:0" }, d.repo,
        h("span", { class: "muted mono-sm" }, `  ${d.files.map((f) => f.name.split("/").pop()).join(", ")}`)),
        h("span", { class: `pill ${cls}` }, d.status),
        running ? btn("Cancel", async () => { await api("POST", `/api/downloads/${d.id}/cancel`).catch(fail); loadDownloads(); }, "sm")
          : d.status !== "done" ? btn("Resume", async () => { await api("POST", `/api/downloads/${d.id}/retry`).catch(fail); loadDownloads(); }, "sm") : null),
      h("div", { class: `progress${d.status === "done" ? " done" : ""}` }, h("i", { style: `width:${pct.toFixed(1)}%` })),
      h("div", { class: "muted mono-sm" }, `${size(d.bytes_done || 0)} of ${size(d.bytes_total || 0)}`,
        running && d.speed ? ` · ${size(d.speed)}/s` : "", eta != null ? ` · ${uptime(Date.now() / 1000 - eta)} left` : ""),
      // Anything that did not finish says why, in its own block: the one-line
      // summary above is too easy to miss, and "it stopped" without a reason is
      // what makes a failed download look like nothing happened.
      (d.status !== "done" || d.error) ? h("div", { class: "note " + (d.error ? "note-pink" : "note-yellow"),
        style: "margin-top:6px;padding:7px 9px;font-size:11.5px;line-height:1.5;" },
        h("div", {}, d.error ? `Error: ${d.error}` : `Did not finish (${d.status})`),
        h("div", { class: "muted" }, `onto ${d.dest || "?"} · last update ${ago(d.updated_at)}`),
        h("div", { class: "muted" }, `expected ${d.bytes_total ? size(d.bytes_total) : "unknown"}, got ${size(d.bytes_done || 0)}`)) : null);
  }) : h("div", { class: "empty" }, "No downloads yet."));
}

// ── Per-GPU VRAM in the top bar ──────────────────────────────
// One bar per card, so the headroom the pool sees is visible from any page
// without opening Logs. The query is small and pauses while the tab is hidden.
async function loadGpuBars() {
  const box = $("#gpuBars");
  if (!box || document.hidden) return;
  let d;
  try { d = await api("GET", "/api/gpu/series?range=1h"); } catch (e) { return; }
  const rows = (d.latest || []).filter((g) => g && (g.mem_total_mb || 0) > 0);
  const gb = (mb) => `${(mb / 1024).toFixed(1)}G`;
  box.replaceChildren(...rows.map((g) => {
    const used = g.mem_used_mb || 0, total = g.mem_total_mb || 1;
    const pct = Math.max(0, Math.min(100, (used / total) * 100));
    return h("span", { class: "gpu-bar", title: `${g.name || "GPU " + g.gpu} — ${gb(used)} / ${gb(total)} used (${pct.toFixed(0)}%)` },
      h("span", { class: "gpu-bar-label" }, `GPU${g.gpu}`),
      h("span", { class: "gpu-bar-track" }, h("i", { style: `width:${pct.toFixed(1)}%` })),
      h("span", { class: "gpu-bar-num" }, gb(used)));
  }));
}

// ── Logs: GPU charts ─────────────────────────────────────────
const SVGNS = "http://www.w3.org/2000/svg";
const sv = (tag, attrs = {}) => { const el = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); return el; };
const RANGES = [["30s", "30s"], ["5m", "5m"], ["10m", "10m"], ["30m", "30m"], ["1h", "1h"], ["6h", "6h"], ["24h", "24h"], ["7d", "7d"], ["30d", "30d"], ["90d", "90d"], ["1y", "1y"]];
const SHORT_RANGES = new Set(["30s", "5m", "10m", "30m"]);
const G = { range: "30m", data: null };
const seriesColor = (i) => `var(--series-${(i % 8) + 1})`;

/** Round tick step for roughly four intervals up to ``max``. */
function niceStep(max) {
  const raw = (max > 0 ? max : 1) / 4, p = 10 ** Math.floor(Math.log10(raw)), n = raw / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}
function timeLabel(t, span) {
  const d = new Date(t * 1000);
  if (span <= 86400) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (span <= 8 * 86400) return d.toLocaleDateString([], { weekday: "short" }) + " " + d.toLocaleTimeString([], { hour: "2-digit" });
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

/** One metric, one line per GPU. Crosshair + one tooltip listing every GPU at that time. */
function lineChart(box, { title, unit, xs, series, yMax, digits = 0, bucket }) {
  const W = Math.max(260, box.clientWidth || 400), H = 190, m = { l: 40, r: 12, t: 8, b: 22 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const all = series.flatMap((s) => s.values).filter((v) => v != null);
  const peak = Math.max(yMax || 0, ...all, 0) || 1, step = niceStep(peak), top = Math.ceil(peak / step) * step;
  const x0 = xs[0], x1 = xs[xs.length - 1], span = Math.max(1, x1 - x0);
  const X = (t) => m.l + ((t - x0) / span) * iw, Y = (v) => m.t + ih - (v / top) * ih;
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${title} chart`, tabindex: "0" });
  const grid = sv("g", { class: "grid" }), axis = sv("g", { class: "axis" });
  for (let i = 0; i <= Math.round(top / step); i++) {
    const v = step * i, y = Y(v);
    if (i) grid.append(sv("line", { x1: m.l, x2: W - m.r, y1: y, y2: y }));
    const t = sv("text", { x: m.l - 6, y: y + 3, "text-anchor": "end" });
    t.textContent = v >= 1000 ? `${+(v / 1000).toFixed(1)}K` : `${+v.toFixed(step < 1 ? 1 : 0)}`;
    axis.append(t);
  }
  const ticks = Math.min(5, xs.length);
  for (let i = 0; i < ticks; i++) {
    const t = x0 + (span * i) / Math.max(1, ticks - 1);
    const lbl = sv("text", { x: X(t), y: H - 6, "text-anchor": i === 0 ? "start" : i === ticks - 1 ? "end" : "middle" });
    lbl.textContent = timeLabel(t, span);
    axis.append(lbl);
  }
  svg.append(grid, sv("line", { class: "base", x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0) }), axis);
  const gap = (bucket || 60) * 2.5;
  series.forEach((s, si) => {
    let d = "", prev = null;
    s.values.forEach((v, i) => {
      if (v == null) { prev = null; return; }
      d += `${prev == null || xs[i] - prev > gap ? "M" : "L"}${X(xs[i]).toFixed(1)},${Y(v).toFixed(1)}`;
      prev = xs[i];
    });
    svg.append(sv("path", { class: "series", d, stroke: seriesColor(si) }));
    const last = s.values.map((v, i) => [v, i]).filter(([v]) => v != null).pop();
    if (last) svg.append(sv("circle", { class: "dot", cx: X(xs[last[1]]), cy: Y(last[0]), r: 4, fill: seriesColor(si) }));
  });
  const cross = sv("line", { class: "cross", y1: m.t, y2: m.t + ih, visibility: "hidden" });
  const dots = series.map((_, si) => sv("circle", { class: "dot", r: 4, fill: seriesColor(si), visibility: "hidden" }));
  const hit = sv("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" });
  svg.append(cross, ...dots, hit);
  const tip = h("div", { class: "tip" });
  const fmt = (v) => (v == null ? DASH : `${num(v, digits)}${unit ? ` ${unit}` : ""}`);
  const showAt = (i) => {
    const x = X(xs[i]);
    cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
    series.forEach((s, si) => {
      const v = s.values[i];
      dots[si].setAttribute("visibility", v == null ? "hidden" : "visible");
      if (v != null) { dots[si].setAttribute("cx", x); dots[si].setAttribute("cy", Y(v)); }
    });
    fill(tip, h("div", { class: "t" }, new Date(xs[i] * 1000).toLocaleString()),
      series.map((s, si) => h("div", { class: "r" }, h("span", { class: "key", style: `background:${seriesColor(si)}` }), h("b", {}, fmt(s.values[i])), s.name)));
    tip.style.display = "block";
    const left = (x / W) * box.clientWidth;
    tip.style.left = `${Math.min(Math.max(0, left + 12), box.clientWidth - tip.offsetWidth)}px`;
    tip.style.top = "26px";
  };
  const hide = () => { cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); tip.style.display = "none"; };
  const nearest = (px) => { const t = x0 + ((px - m.l) / iw) * span; let best = 0; xs.forEach((x, i) => { if (Math.abs(x - t) < Math.abs(xs[best] - t)) best = i; }); return best; };
  let focusIdx = xs.length - 1;
  hit.addEventListener("pointermove", (e) => { const r = svg.getBoundingClientRect(); focusIdx = nearest(((e.clientX - r.left) / r.width) * W); showAt(focusIdx); });
  hit.addEventListener("pointerleave", hide);
  svg.addEventListener("focus", () => showAt(focusIdx));
  svg.addEventListener("blur", hide);
  svg.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") focusIdx = Math.max(0, focusIdx - 1);
    else if (e.key === "ArrowRight") focusIdx = Math.min(xs.length - 1, focusIdx + 1);
    else return;
    e.preventDefault(); showAt(focusIdx);
  });
  fill(box, h("h3", {}, title, unit ? h("small", {}, unit) : null), svg, tip);
}

function renderGpu() {
  const d = G.data;
  fill($("#gRange"), RANGES.map(([v, l]) => h("button", { class: G.range === v ? "on" : "", onclick: () => { G.range = v; loadGpu(); } }, l)));
  if (!d) return;
  const every = S.settings?.telemetry?.interval_s;
  $("#gSource").textContent = (d.rows.length
    ? `${d.source === "hourly" ? "hourly rollups" : `${d.bucket}s buckets`} · source: ${d.collector.source || "stored"}`
    : "") + (SHORT_RANGES.has(G.range) && every > 5 ? ` · readings come every ${every}s; set Telemetry to 2–5 s in Settings for short ranges` : "")
    + (SHORT_RANGES.has(G.range) ? " · live" : "");
  const gpus = [...new Map(d.rows.map((r) => [r.gpu, r.name])).entries()].sort((a, b) => a[0] - b[0]);
  if (!d.rows.length) {
    fill($("#gLegend"));
    fill($("#gCharts"), h("div", { class: "empty", style: "grid-column:1/-1" },
      d.collector.enabled ? "No GPU readings in this range yet. They come from the engine; otherwise from /sys/class/drm (AMD) or nvidia-smi."
        : h("span", {}, "Telemetry is off. Turn it on in ", h("b", {}, "Settings"), ".")));
    fill($("#gTable"));
    return;
  }
  const xs = [...new Set(d.rows.map((r) => r.t))].sort((a, b) => a - b);
  const idx = new Map(xs.map((t, i) => [t, i]));
  const pick = (key, scale = 1) => gpus.map(([g, name]) => {
    const values = new Array(xs.length).fill(null);
    d.rows.filter((r) => r.gpu === g && r[key] != null).forEach((r) => { values[idx.get(r.t)] = r[key] * scale; });
    return { name: `GPU${g} ${name}`, values };
  });
  fill($("#gLegend"), gpus.map(([g, name], i) => h("span", {}, h("span", { class: "key", style: `background:${seriesColor(i)}` }), `GPU${g} ${name}`)));
  const vramTop = Math.max(...d.rows.map((r) => (r.mem_total_mb || 0) / 1024));
  const charts = [
    { title: "Utilization", unit: "%", key: "util", yMax: 100 },
    { title: "VRAM used", unit: "GiB", key: "mem_used_mb", scale: 1 / 1024, yMax: vramTop, digits: 1 },
    { title: "Temperature", unit: "°C", key: "temp_c" },
    { title: "Power", unit: "W", key: "power_w" },
  ];
  const boxes = charts.map(() => h("div", { class: "chart" }));
  fill($("#gCharts"), boxes);
  charts.forEach((c, i) => lineChart(boxes[i], { ...c, xs, series: pick(c.key, c.scale), bucket: d.bucket }));
  const stat = (g, key, f) => { const v = d.rows.filter((r) => r.gpu === g && r[key] != null).map((r) => r[key]); return v.length ? f(v) : null; };
  const avg = (v) => v.reduce((a, b) => a + b, 0) / v.length, max = (v) => Math.max(...v);
  const latest = new Map((d.latest || []).map((r) => [r.gpu, r]));
  renderTable($("#gTable"), [
    { label: "GPU", cell: (r) => h("span", {}, h("span", { class: "key", style: `background:${seriesColor(r.i)};margin-right:8px;vertical-align:middle` }), h("span", { class: "strong" }, r.label)) },
    { label: "Now", num: true, cell: (r) => (r.now ? `${num(r.now.util, 0)}% · ${num((r.now.mem_used_mb || 0) / 1024, 1)} GiB · ${num(r.now.temp_c, 0)}°C` : DASH) },
    { label: "Util avg / max", num: true, cell: (r) => `${num(r.ua, 0)}% / ${num(r.um, 0)}%` },
    { label: "VRAM peak", num: true, cell: (r) => (r.vm != null ? `${num(r.vm / 1024, 1)} / ${num(r.vt / 1024, 1)} GiB` : DASH) },
    { label: "Temp max", num: true, cell: (r) => (r.tm != null ? `${num(r.tm, 0)}°C` : DASH) },
    { label: "Power avg", num: true, cell: (r) => (r.pa != null ? `${num(r.pa, 0)} W` : DASH) },
  ], gpus.map(([g, name], i) => ({ i, label: `GPU${g} ${name}`, now: latest.get(g),
    ua: stat(g, "util", avg), um: stat(g, "util_max", max) ?? stat(g, "util", max), vm: stat(g, "mem_used_mb", max),
    vt: stat(g, "mem_total_mb", max), tm: stat(g, "temp_max", max) ?? stat(g, "temp_c", max), pa: stat(g, "power_w", avg) })),
  { key: null, asc: false }, "");
}

async function loadGpu() {
  $("#gCharts").style.opacity = G.data ? "0.5" : "1";
  try { G.data = await api("GET", `/api/gpu/series?range=${G.range}`); } catch (e) { fail(e); }
  $("#gCharts").style.opacity = "1";
  renderGpu();
}

// ── Logs: requests + events ──────────────────────────────────
async function loadRequests() {
  const rng = $("#rRange").value, model = $("#rModel").value;
  let res;
  try { res = await api("GET", `/api/requests?range=${rng}&limit=500${model ? `&model=${encodeURIComponent(model)}` : ""}`); } catch (e) { fail(e); return; }
  const url = S.settings?.integrations.engine_url;
  $("#rHint").textContent = url ? "synced from the engine" : "Request history appears once the engine is running.";
  if (!model) {
    const cur = $("#rModel").value;
    fill($("#rModel"), h("option", { value: "" }, "All models"), res.stats.map((s) => h("option", { value: s.model }, s.model)));
    $("#rModel").value = cur;
  }
  const maxTg = Math.max(1, ...res.stats.map((s) => s.tg_avg || 0));
  renderTable($("#rStats"), [
    { key: "model", label: "Model", cell: (s) => h("span", { class: "strong" }, s.model || DASH) },
    { key: "requests", label: "Requests", num: true, cell: (s) => num(s.requests, 0) },
    { key: "tg_avg", label: "Gen t/s avg", num: true, cell: (s) => h("div", { class: "bar-cell" }, num(s.tg_avg, 1), h("span", { class: "bar" }, h("i", { style: `width:${Math.round(((s.tg_avg || 0) / maxTg) * 100)}%` }))) },
    { key: "tg_max", label: "max", num: true, cell: (s) => num(s.tg_max, 1) },
    { key: "pp_avg", label: "Prompt t/s", num: true, cell: (s) => num(s.pp_avg, 0) },
    { key: "output_tokens", label: "Tokens in / out", num: true, cell: (s) => `${num(s.input_tokens, 0)} / ${num(s.output_tokens, 0)}` },
    { key: "errors", label: "Errors", num: true, cell: (s) => (s.errors ? h("span", { class: "pill bad" }, String(s.errors)) : "0") },
    { key: "last_ts", label: "Last", cell: (s) => h("span", { class: "muted" }, ago(s.last_ts)) },
  ], res.stats, { key: null, asc: false }, url ? "No requests in this range." : "No request history yet.");
  renderTable($("#rTable"), [
    { key: "ts", label: "When", cell: (r) => h("span", { class: "muted", title: new Date(r.ts * 1000).toLocaleString() }, ago(r.ts)) },
    { key: "model", label: "Model", cell: (r) => r.model || DASH },
    { key: "tokens_per_second", label: "Gen t/s", num: true, cell: (r) => num(r.tokens_per_second, 1) },
    { key: "prompt_per_second", label: "Prompt t/s", num: true, cell: (r) => num(r.prompt_per_second, 0) },
    { label: "In / out", num: true, cell: (r) => `${num(r.input_tokens, 0)} / ${num(r.output_tokens, 0)}` },
    { key: "duration_ms", label: "Time", num: true, cell: (r) => (r.duration_ms ? `${num(r.duration_ms / 1000, 1)} s` : DASH) },
    { key: "status", label: "Status", cell: (r) => (r.status || r.error ? h("span", { class: `pill ${r.status >= 400 || r.error ? "bad" : "ok"}`, title: r.error || "" }, String(r.status || "error"))
      : h("span", { class: "muted", title: "the engine did not report a status for this request" }, DASH)) },
  ], res.requests, { key: null, asc: false }, "No requests.");
}

let lastEvents = [];
async function askAboutEvents() {
  const q = $("#eAsk").value.trim();
  if (!q) return toast("Type a question first.", "err");
  const box = $("#eAnswer"), b = $("#eAskBtn");
  b.disabled = true;
  fill(box, h("p", { class: "hint" }, "Asking the assistant… (the first question can take a moment while it loads)"));
  const lines = lastEvents.slice(0, 60).map((e) => `${new Date(e.ts * 1000).toLocaleString()} ${e.level} ${e.kind}: ${e.message}`);
  try {
    const r = await api("POST", "/api/assistant/ask", { question: q, title: "Events on screen", lines });
    fill(box, h("div", { class: "answer" }, h("div", { class: "muted mono-sm" }, `About the ${lines.length} events listed below`), h("div", { style: "white-space:pre-wrap" }, r.answer)));
  } catch (e) { fill(box, h("p", { class: "hint" }, e.message, " — set the assistant up under Assistant.")); } finally { b.disabled = false; }
}
const eventLine = (e) => `${new Date(e.ts * 1000).toLocaleString()} ${e.level} ${e.kind}: ${e.message}`;
function askAboutEvent(ev) {
  const i = lastEvents.indexOf(ev);
  const around = lastEvents.slice(Math.max(0, i - 6), i + 7).filter((x) => x !== ev);
  const q = h("input", { type: "text", value: ev.level === "info" ? "What does this mean?" : "Why did this happen, and what should I change?" });
  const answer = h("div");
  const ask = async () => {
    fill(answer, h("p", { class: "hint" }, "Asking… (the first question can take a moment while the model loads)"));
    try {
      const r = await api("POST", "/api/assistant/ask", { question: q.value, title: "The log entry asked about, then the entries around it",
        lines: [`>>> ${eventLine(ev)}`, ...around.map(eventLine)] });
      fill(answer, h("div", { class: "answer", style: "white-space:pre-wrap" }, r.answer));
    } catch (e) { fill(answer, h("p", { class: "hint" }, e.message)); }
  };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); });
  modal("Ask about this log entry", h("div", { class: "stack" },
    h("pre", { class: "pre", style: "white-space:pre-wrap" }, eventLine(ev)),
    h("div", { class: "row nowrap" }, q, btn("Ask", ask, "primary sm", "message")), answer,
    h("p", { class: "hint", style: "margin:0" }, `The assistant also gets the ${around.length} entries around it and everything in its memory about them.`)),
  [{ label: "Close", run: (c) => c() }]);
  ask();
}
async function loadEvents() {
  const qs = new URLSearchParams({ limit: "500" });
  for (const [k, id] of [["kind", "#eKind"], ["level", "#eLevel"], ["q", "#eQuery"]]) if ($(id).value.trim()) qs.set(k, $(id).value.trim());
  let res;
  try { res = await api("GET", `/api/events?${qs}`); } catch (e) { fail(e); return; }
  lastEvents = res.events;
  const lvl = { error: "bad", warn: "warn", info: "" };
  renderTable($("#evTable"), [
    { key: "ts", label: "When", cell: (e) => h("span", { class: "muted", title: new Date(e.ts * 1000).toLocaleString() }, new Date(e.ts * 1000).toLocaleString()) },
    { key: "level", label: "Level", cell: (e) => h("span", { class: `pill ${lvl[e.level] || ""}` }, e.level) },
    { key: "kind", label: "Kind", cell: (e) => h("span", { class: "chip" }, e.kind) },
    { label: "Message", cell: (e) => h("td", { style: "white-space:normal;min-width:280px" }, e.message) },
    { label: "", cell: (e) => h("td", { class: "actions" }, iconBtn("message", "Ask the assistant about this entry", () => askAboutEvent(e), "ghost")) },
  ], res.events, { key: null, asc: false }, "Nothing logged yet.");
}

async function refreshLogsNav() {
  try {
    const res = await api("GET", "/api/events?level=error&hours=24&limit=100");
    fill($("#navLogs"), res.events.length ? [String(res.events.length), h("span", { class: "dot bad", title: "errors in the last 24h" })] : null);
  } catch (e) { /* ignore */ }
}

function loadLogs() {
  const pane = $(".tabs[data-tabs=logs] button.on")?.dataset.pane || "gpu";
  if (pane === "gpu") loadGpu();
  else if (pane === "requests") loadRequests();
  else loadEvents();
}

// ── Logs: what is really on the GPUs ─────────────────────────
async function loadGpuProcs() {
  const table = $("#gpuProcs");
  let r;
  try { r = await api("GET", "/api/gpu/processes"); } catch (e) { fill(table, h("tbody", {}, h("tr", {}, h("td", {}, e.message)))); return; }
  const total = (r.processes || []).reduce((a, p) => a + (p.vram_gib || 0), 0);
  $("#gpuProcNote").textContent = r.tool ? `read with ${r.tool}${total ? ` · ${num(total, 1)} GiB across ${r.processes.length} process(es)` : ""}` : "";
  const kill = async (p, hard) => {
    const what = `${p.name} (PID ${p.pid})${p.vram_gib ? `, ${num(p.vram_gib, 1)} GiB` : ""}`;
    const owned = (p.owner || "").startsWith("model ");
    if (!(await confirmBox(owned ? `${what} is ${p.owner}, loaded by the engine. Unload it from Schedule → Live instead. Kill it anyway?` : `Stop ${what}? ${p.owner || "Argos did not start it."}`, hard ? "Kill" : "Stop"))) return;
    try { const res = await api("POST", `/api/gpu/processes/${p.pid}/kill`, { force: owned, hard }); toast(`${p.name}: ${res.result}.`, "ok"); } catch (e) { fail(e); }
    setTimeout(loadGpuProcs, 1200);
  };
  renderTable(table, [
    { label: "PID", num: true, cell: (p) => p.pid },
    { label: "Process", cell: (p) => p.name },
    { label: "GPU", cell: (p) => p.gpu },
    { label: "VRAM", num: true, cell: (p) => (p.vram_gib != null ? gib(p.vram_gib) : DASH) },
    { label: "Started by", cell: (p) => (p.self ? "this app" : p.owner ? h("span", { class: "pill ok" }, p.owner) : h("span", { class: "pill warn", title: "Argos does not know this process: another container, a leftover, or something you started by hand." }, "unknown")) },
    { label: "Command", cell: (p) => h("td", { class: "mono-sm", title: p.cmd, style: "max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, p.cmd || DASH) },
    { label: "", cell: (p) => (p.self ? "" : h("td", { class: "actions nowrap" }, btn("Stop", () => kill(p, false), "sm ghost"), " ", btn("Kill", () => kill(p, true), "sm danger"))) },
  ], r.processes || [], { key: null }, r.note || "Nothing is using the GPUs.");
}

// ── Logs: every llama-server's own output ───────────────────
const SL = { sel: null, len: -1, auto: true };
function slSources() {
  const engine = S.launches.filter((l) => l.engine), local = S.launches.filter((l) => !l.engine);
  return { engine, local, all: [...engine, ...local, { launch_id: "proxy", base_model: "Engine: routing and loads", special: true },
    { launch_id: "upstream", base_model: "Engine: all model output", special: true }] };
}
function renderServerLogSources() {
  const { engine, local, all } = slSources();
  // until you pick one, show the first running model (the launches may arrive after the first render)
  if (!SL.sel || SL.auto || !all.some((x) => x.launch_id === SL.sel)) {
    const pick = (engine[0] || local.find((l) => l.running) || all[0]).launch_id;
    if (pick !== SL.sel) SL.len = -1;
    SL.sel = pick; SL.auto = true;
  }
  const item = (l, sub) => {
    const [dot] = l.special ? ["", ""] : launchState(l);
    return h("div", { class: `src${l.launch_id === SL.sel ? " sel" : ""}`, onclick: () => { SL.sel = l.launch_id; SL.auto = false; SL.len = -1; renderServerLogSources(); loadServerLogs(true); } },
      h("span", { class: `dot ${dot}` }), h("div", { style: "min-width:0" }, h("div", { class: "t", title: l.base_model }, l.profile_name || l.base_model || l.launch_id), h("div", { class: "s" }, sub)));
  };
  fill($("#slSources"),
    h("div", { class: "grp" }, "Running in the engine"),
    engine.length ? engine.map((l) => item(l, `${launchState(l)[1]} · ${uptime(l.started_at)}`)) : h("div", { class: "hint", style: "margin:0 8px" }, "No model loaded."),
    local.length ? [h("div", { class: "grp" }, "Launched here"), local.map((l) => item(l, `:${l.port} · ${launchState(l)[1]}`))] : null,
    h("div", { class: "grp" }, "Engine"), all.filter((x) => x.special).map((l) => item(l, l.launch_id)));
  fill($("#navServerLogs"), engine.length ? String(engine.length) : null);
}
const SL_ERR = /error|failed|out of memory|exception|invalid argument|abort|fatal|cannot|unable/i;
const SL_PERF = /eval time|tokens per second|t\/s|prompt eval/i;
async function loadServerLogs(force) {
  renderServerLogSources();
  const id = SL.sel;
  if (!id) return;
  const special = id === "proxy" || id === "upstream";
  const url = special ? `/api/engine/log/${id}?tail=400000` : `/api/launches/${encodeURIComponent(id)}/log?tail=400000`;
  try {
    const res = await fetch(url);
    const text = res.ok ? await res.text() : `(no log: HTTP ${res.status})`;
    if (id !== SL.sel || (!force && text.length === SL.len)) return;
    SL.len = text.length;
    const src = slSources().all.find((x) => x.launch_id === id);
    $("#slTitle").textContent = src ? (src.profile_name || src.base_model || id) : id;
    const q = $("#slFilter").value.trim().toLowerCase(), errOnly = $("#slErrors").checked;
    const lines = text.split("\n").filter((l) => (!q || l.toLowerCase().includes(q)) && (!errOnly || SL_ERR.test(l)));
    const pre = $("#slLog");
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
    fill(pre, lines.length ? lines.map((line) => {
      const cls = SL_ERR.test(line) ? "ln-err" : SL_PERF.test(line) ? "ln-perf" : /listening|all slots are idle|model loaded/i.test(line) ? "ln-ok" : "";
      return [cls ? h("span", { class: cls }, line) : line, "\n"];
    }) : h("span", { class: "muted" }, text ? "No line matches." : "Nothing logged yet."));
    if ($("#slFollow").checked && (atBottom || force)) pre.scrollTop = pre.scrollHeight;
    // per-request speeds, as llama-server printed them
    const dec = [...text.matchAll(/^\s*eval time.*?([\d.]+) tokens per second/gim)].map((m) => +m[1]);
    const pre_ = [...text.matchAll(/prompt eval time.*?([\d.]+) tokens per second/gi)].map((m) => +m[1]);
    const errs = text.split("\n").filter((l) => SL_ERR.test(l)).length;
    const stat = (k, v, unit) => h("div", { class: "stat" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v, unit && h("small", {}, unit)));
    fill($("#slStats"), special ? null : [
      stat("Requests", dec.length || DASH),
      stat("Decode last", dec.length ? num(dec[dec.length - 1]) : DASH, dec.length ? "t/s" : ""),
      stat("Decode low–high", dec.length ? `${num(Math.min(...dec))}–${num(Math.max(...dec))}` : DASH, dec.length ? "t/s" : ""),
      stat("Prefill last", pre_.length ? num(pre_[pre_.length - 1], 0) : DASH, pre_.length ? "t/s" : ""),
      stat("Error lines", errs ? h("span", { style: "color:var(--danger)" }, String(errs)) : "0"),
    ]);
  } catch (e) { /* the engine may be restarting */ }
}
function initServerLogs() {
  $("#slFilter").addEventListener("input", debounce(() => loadServerLogs(true), 150));
  $("#slErrors").addEventListener("change", () => loadServerLogs(true));
  $("#slCopy").onclick = () => { navigator.clipboard?.writeText($("#slLog").textContent || "").then(() => toast("Copied.", "ok"), () => toast("Could not copy.", "err")); };
  $("#gpuProcRefresh").onclick = () => loadGpuProcs();
}

// ── Assistant ────────────────────────────────────────────────
const A = { state: null, busy: false };
const SUGGESTIONS = ["How did my GPUs do today?", "Which model is fastest here?", "Any errors in the last day?", "How can I fit a bigger context?"];

/** Tiny markdown: paragraphs, lists, `code`, ```blocks```, **bold** — built as DOM, never HTML. */
function markdown(text) {
  const out = [];
  const inline = (line) => {
    const parts = [];
    line.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).forEach((p) => {
      if (/^`[^`]+`$/.test(p)) parts.push(h("code", {}, p.slice(1, -1)));
      else if (/^\*\*[^*]+\*\*$/.test(p)) parts.push(h("strong", {}, p.slice(2, -2)));
      else if (p) parts.push(p);
    });
    return parts;
  };
  const blocks = text.split(/```/);
  blocks.forEach((block, bi) => {
    if (bi % 2) { out.push(h("pre", {}, h("code", {}, block.replace(/^\w*\n/, "")))); return; }
    let list = null;
    block.split("\n").forEach((line) => {
      const m = line.match(/^\s*(?:[-*]|(\d+)\.)\s+(.*)$/);
      if (m) {
        const tag = m[1] ? "ol" : "ul";
        if (!list || list.tagName.toLowerCase() !== tag) { list = h(tag); out.push(list); }
        list.append(h("li", {}, inline(m[2])));
        return;
      }
      list = null;
      if (line.trim()) out.push(h("p", {}, inline(line)));
    });
  });
  return out;
}

function msgEl(m) {
  if (m.role === "user") return h("div", { class: `msg user${m.compacted ? " compacted" : ""}` }, m.content);
  return h("div", { class: `msg assistant${m.compacted ? " compacted" : ""}` }, h("div", { class: "who" }, "assistant"), markdown(m.content));
}

async function loadAssistant() {
  try { A.state = await api("GET", "/api/assistant"); } catch (e) { fail(e); return; }
  renderAssistant();
}

function assistantNav() {
  const chat = A.state?.models?.chat;
  const state = chat && chat.state;
  fill($("#navAssistant"), state === "ready" || state === "starting" ? h("span", { class: `dot ${state === "ready" ? "on" : "warn"}`, title: `assistant model ${state}` }) : null);
}

/** The chat model and the memory's embedding model: what each is, whether it is loaded, and Load / Unload. */
function renderAssistantModels(models) {
  const el = $("#aModels");
  if (!models) return fill(el);
  const STATES = { ready: ["on", "loaded"], starting: ["warn", "loading"], stopping: ["warn", "unloading"], stopped: ["", "not loaded"],
    unknown: ["", "state unknown"], none: ["", "off"] };
  const part = (which, label, m) => {
    const [dot, text] = STATES[m.state] || STATES.unknown;
    const loaded = m.state === "ready" || m.state === "starting";
    const act = async (action) => {
      try {
        fill(el.querySelector(`[data-m="${which}"] .assist-model-act`), h("span", { class: "muted mono-sm" }, action === "load" ? "loading…" : "unloading…"));
        const r = await api("POST", `/api/assistant/models/${which}/${action}`);
        A.state.models = r.models;
        toast(`${action === "load" ? "Loaded" : "Unloaded"} ${r.model.model}.`, "ok");
      } catch (e) { fail(e); }
      renderAssistantModels(A.state.models);
      assistantNav();
    };
    const name = m.model || (which === "embed" ? "none (search by words)" : "no model set");
    const where = m.where && m.where !== "engine" && m.where !== "here" ? ` @ ${m.where}` : "";
    return h("div", { class: "assist-model", dataset: { m: which }, title: m.last_used ? `last used ${ago(m.last_used)}` : "" },
      h("span", { class: "muted mono-sm" }, label),
      h("span", { class: "strong ellipsis", style: "max-width:220px" }, name + where),
      m.model ? h("span", { class: "status-line" }, h("span", { class: `dot ${dot}` }), text) : null,
      h("span", { class: "assist-model-act" }, m.controllable
        ? (loaded ? btn("Unload", () => act("unload"), "sm ghost", "square") : btn("Load", () => act("load"), "sm ghost", "play"))
        : null));
  };
  fill(el, part("chat", "Chat", models.chat), part("embed", "Memory", models.embed),
    h("span", { class: "muted mono-sm", title: "Change it under Settings" }, models.idle_unload_min ? `unloads after ${models.idle_unload_min} min unused` : "never unloads by itself"));
}

function renderAssistant() {
  const st = A.state;
  if (!st) return;
  const sv = st.server;
  renderAssistantModels(st.models);
  $("#aCompact").disabled = st.messages.filter((m) => !m.compacted).length <= 2;
  $("#aTokens").textContent = `${num(st.tokens, 0)} / ${num(st.budget, 0)} tokens`;
  $("#aMeter").style.width = `${Math.min(100, (st.tokens / st.budget) * 100).toFixed(0)}%`;
  assistantNav();
  const box = $("#aMessages");
  const msgs = st.messages;
  const needsSetup = (sv.mode === "managed" && !sv.profile) || (sv.mode === "engine" && !sv.model);
  const kids = [];
  if (!msgs.length) {
    kids.push(h("div", { class: "hero", style: "padding-top:4vh" },
      h("div", { class: "logo" }, icon("message"), "assistant"),
      h("div", { class: "tag" }, "Ask about your GPUs, speeds, models and profiles. It reads this app's logs."),
      needsSetup ? h("div", { class: "stack", style: "align-items:center;margin-top:16px" },
        h("p", { class: "hint", style: "margin:0;max-width:520px" }, "The assistant runs a small model beside your others: Qwen3-0.6B (Q8_0, under 1 GB). It is reused if it's already in your models folder, otherwise downloaded from Hugging Face."),
        h("div", { class: "row" }, btn("Set up Qwen3-0.6B", setupAssistant, "primary", "download"), btn("Pick a model…", () => openAssistantSettings(true), ""))) :
        h("div", { class: "suggest" }, SUGGESTIONS.map((q) => h("button", { onclick: () => { $("#aInput").value = q; sendChat(); } }, q)))));
  }
  let shownDivider = false;
  msgs.forEach((m, i) => {
    if (!m.compacted && i > 0 && msgs[i - 1].compacted && !shownDivider) {
      shownDivider = true;
      kids.push(h("details", { class: "divider-box" }, h("summary", { class: "divider" }, "earlier messages compacted into a summary — click to read it"),
        h("div", { class: "msg assistant", style: "margin-top:8px" }, markdown(st.summary || "(empty)"))));
    }
    kids.push(msgEl(m));
  });
  fill(box, kids);
  box.lastElementChild?.scrollIntoView({ block: "end" });
}

async function setupAssistant() {
  try {
    const res = await api("POST", "/api/assistant/setup", {}, 0);
    toast(res.download ? "Downloading Qwen3-0.6B — the assistant starts once it lands." : `Assistant set to ${res.profile.name}.`, "ok");
    loadDownloads();
    loadProfiles();
    loadAssistant();
  } catch (e) { fail(e); }
}

// ACTION lines are how the assistant asks for a model action; they are a
// contract with the backend, not something to show in the answer.
function visibleAnswer(t) {
  return (t || "").replace(/^ACTION\s+.*$/gm, "").replace(/\n{3,}/g, "\n\n").trim();
}

// One proposed action, with the confirmation it requires. The assistant never
// performs these itself, so a wrong model id costs a click, not a launch.
function actionEl(a) {
  const what = a.verb === "keep"
    ? `${a.on ? "Keep loaded" : "Allow unloading"}: ${a.model}`
    : `${a.verb === "load" ? "Load" : "Unload"}: ${a.model}`;
  const btn = h("button", { class: "btn sm" }, "Apply");
  const row = h("div", { class: "assistant-action", style: "display:flex;gap:8px;align-items:center;"
    + "margin-top:6px;padding:6px 8px;border:1px solid var(--border);border-radius:6px;" },
    h("span", { style: "flex:1;min-width:0;font-size:12px;" }, what), btn);
  btn.onclick = async () => {
    btn.disabled = true;
    btn.textContent = "…";
    try {
      await api("POST", `/api/schedule/live/${encodeURIComponent(a.model)}/${a.verb}`,
                a.verb === "keep" ? { on: a.on } : {});
      btn.textContent = "Done";
      row.style.opacity = "0.6";
      toast(`${what} — done`, "");
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Retry";
      toast(e.message, "");
    }
  };
  return row;
}

async function sendChat() {
  const input = $("#aInput");
  const text = input.value.trim();
  if (!text || A.busy) return;
  A.busy = true;
  $("#aSend").disabled = true;
  input.value = "";
  const box = $("#aMessages");
  if (!A.state?.messages.length) fill(box);
  box.append(msgEl({ role: "user", content: text }));
  const status = h("div", { class: "status-line" }, h("span", { class: "dot warn" }), "thinking…");
  const body = h("div");
  const bubble = h("div", { class: "msg assistant" }, h("div", { class: "who" }, "assistant"), status, body);
  box.append(bubble);
  bubble.scrollIntoView({ block: "end" });
  let answer = "";
  try {
    const res = await fetch("/api/assistant/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text }) });
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let cut;
      while ((cut = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, cut).trim();
        buf = buf.slice(cut + 2);
        if (!line.startsWith("data:")) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.type === "status") fill(status, h("span", { class: "dot warn" }), ev.text);
        else if (ev.type === "compacted") toast("Older messages were compacted into a summary.", "");
        else if (ev.type === "delta") { status.remove(); answer += ev.text; body.textContent = visibleAnswer(answer); bubble.scrollIntoView({ block: "end" }); }
        else if (ev.type === "action") { status.remove(); body.append(actionEl(ev.action)); bubble.scrollIntoView({ block: "end" }); }
        else if (ev.type === "error") { status.remove(); body.append(h("p", { style: "color:var(--danger)" }, ev.error)); }
      }
    }
  } catch (e) { status.remove(); body.append(h("p", { style: "color:var(--danger)" }, e.message)); }
  A.busy = false;
  $("#aSend").disabled = false;
  await loadAssistant();
  input.focus();
}

// ── Assistant settings: its own page ─────────────────────────
// The chat model and the embedding model are picked from what Argos has: your profiles, the models in the library,
// and anything else the engine lists. Another OpenAI-compatible server is under Advanced, for the rare case.
async function openAssistantSettings(open = true) {
  A.settingsOpen = open;
  $("#aSettings").hidden = !open;
  $(".chat-wrap").hidden = open;
  if (!open) return loadAssistant();
  try { await loadSettings(); A.choices = await api("GET", "/api/assistant/choices"); } catch (e) { A.choices = null; }
  renderAssistantSettings();
}

/** A <select> of model choices grouped like the API returns them, with a first "none" option when asked. */
function modelSelect(items, value, { none = null, connected = true } = {}) {
  const sel = h("select", {});
  if (none) sel.append(h("option", { value: "" }, none));
  const groups = {};
  items.forEach((c) => { (groups[c.group] = groups[c.group] || []).push(c); });
  Object.entries(groups).forEach(([g, cs]) => sel.append(h("optgroup", { label: g }, cs.map((c) =>
    h("option", { value: c.id }, `${c.label}${c.label !== c.id ? ` · ${c.id}` : ""}${connected && !c.listed ? " (engine has not listed it yet)" : ""}`)))));
  if (value && !items.some((c) => c.id === value)) sel.append(h("optgroup", { label: "Currently set" }, h("option", { value }, value)));
  sel.value = value || "";
  return sel;
}

function renderAssistantSettings() {
  const cfg = S.settings.assistant, mem = S.settings.memory;
  const ch = A.choices || { chat: [], embed: [], engine_connected: false };
  const field = (label, input, hint, wide) => h("label", { class: `field${wide ? " wide" : ""}` }, h("span", {}, label), input, hint ? h("span", { class: "muted mono-sm" }, hint) : null);

  // the chat model: one of Argos's models, or (Advanced) another server
  let current = cfg.mode === "engine" ? cfg.model : "";
  if (cfg.mode === "managed" && cfg.profile_id) {  // older setups started a profile beside the engine: show its engine model
    const p = S.profiles.find((x) => x.profile_id === cfg.profile_id);
    const c = p && ch.chat.find((x) => x.group === "Profiles" && x.label === p.name);
    current = c ? c.id : "";
  }
  const chat = modelSelect(ch.chat, current, { none: "choose a model…", connected: ch.engine_connected });
  const useOther = h("input", { type: "checkbox", checked: cfg.mode === "endpoint" });
  const endpoint = h("input", { type: "text", value: cfg.endpoint || "", placeholder: "http://other-box:8080/v1", spellcheck: "false" });
  const endpointModel = h("input", { type: "text", value: cfg.mode === "endpoint" ? cfg.model || "" : "", placeholder: "model id on that server", spellcheck: "false" });
  const syncOther = () => { chat.disabled = useOther.checked; };
  useOther.addEventListener("change", syncOther);
  syncOther();

  // escalation: what a hard question is handed to
  const escalate = modelSelect(ch.chat, cfg.escalate_model || "", { none: "none: the small model answers everything", connected: ch.engine_connected });
  const escalateQuant = h("select", {}, ["Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"].map((q) => h("option", { value: q }, q)));
  escalateQuant.value = cfg.escalate_quant || "Q4_K_M";
  const escalateFetch = h("button", { class: "btn sm", type: "button" }, "Fetch it");
  escalateFetch.onclick = async () => {
    escalateFetch.disabled = true; escalateFetch.textContent = "…";
    try {
      const r = await api("POST", "/api/assistant/setup", { which: "escalate" }, 0);
      if (r && r.model) escalate.value = r.model;
      toast("Fetching the bigger model. It shows up as an engine model once the models folder is scanned.", "ok");
    } catch (e) { fail(e); } finally { escalateFetch.disabled = false; escalateFetch.textContent = "Fetch it"; }
  };

  // the conversation
  const ctx = h("input", { type: "number", value: cfg.context_tokens });
  const keep = h("input", { type: "number", value: cfg.keep_recent });
  const temp = h("input", { type: "number", step: "0.1", value: cfg.temperature });
  const idle = h("select", {}, [[0, "Never"], [5, "5 minutes"], [10, "10 minutes"], [15, "15 minutes"], [30, "30 minutes"], [60, "1 hour"], [240, "4 hours"]]
    .map(([v, l]) => h("option", { value: v }, l)));
  idle.value = String(cfg.idle_unload_min ?? 15);
  if (idle.value === "") idle.append(h("option", { value: cfg.idle_unload_min, selected: true }, `${cfg.idle_unload_min} minutes`));

  // memory
  const memOn = h("input", { type: "checkbox", checked: !!mem.enabled });
  const embed = modelSelect(ch.embed, mem.model, { none: "none: search by words only", connected: ch.engine_connected });
  const results = h("input", { type: "number", value: mem.results });
  const memEndpoint = h("input", { type: "text", value: mem.endpoint || "", placeholder: "blank = the engine (normal)", spellcheck: "false" });

  const save = async () => {
    try {
      const body = { context_tokens: ctx.value, keep_recent: keep.value, temperature: temp.value, idle_unload_min: idle.value,
        escalate_model: escalate.value, escalate_quant: escalateQuant.value };
      if (useOther.checked) {
        if (!endpoint.value.trim()) throw new Error("Type the other server's URL, or untick “Use another server”.");
        Object.assign(body, { mode: "endpoint", endpoint: endpoint.value.trim(), model: endpointModel.value.trim() });
      } else {
        Object.assign(body, { mode: "engine", model: chat.value });
      }
      await api("PUT", "/api/settings/assistant", body);
      await api("PUT", "/api/settings/memory", { enabled: memOn.checked, model: embed.value, endpoint: memEndpoint.value.trim(), results: results.value });
      await loadSettings();
      toast("Saved the assistant settings.", "ok");
      openAssistantSettings(false);
    } catch (e) { fail(e); }
  };

  const engineNote = ch.engine_connected ? null
    : h("p", { class: "hint" }, "The engine is not answering right now, so these lists come from your library and profiles. They load once the engine is up.");
  fill($("#aSettings"),
    h("div", { class: "view-head" }, h("div", {}, h("h1", {}, "Assistant settings"), h("p", { class: "sub" }, "A small model that knows your GPUs, speeds and history. Pick it from the models Argos has.")),
      h("span", { class: "spacer" }), btn("Back to chat", () => openAssistantSettings(false), "", "arrow-left"), btn("Save", save, "primary", "save")),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Chat model")),
      h("p", { class: "hint" }, "Any of your models or profiles. The engine loads it on the first message (or with Load on the chat page) and it shares the GPUs like any other model. A small one is plenty: Qwen3-0.6B fits in under 1 GB."),
      engineNote,
      h("div", { class: "fields" },
        field("Model", chat, ch.chat.length ? `${ch.chat.length} to choose from` : "No models yet: add one under Models, or set up Qwen3-0.6B.", true),
        h("div", { class: "row wide" }, btn("Set up Qwen3-0.6B", async () => { await setupAssistant(); openAssistantSettings(true); }, "sm", "download"),
          h("span", { class: "muted mono-sm" }, "Uses the file if it is already in your models folder, otherwise downloads it, saves a profile for it and picks it."))),
      h("details", { class: "advanced", open: cfg.mode === "endpoint" }, h("summary", {}, "Advanced: use a model on another server"),
        h("div", { class: "fields", style: "margin-top:8px" },
          h("label", { class: "check wide" }, useOther, "Use another OpenAI-compatible server instead of Argos"),
          field("URL", endpoint, "Its /v1 address.", true), field("Model id", endpointModel)))),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Hard questions")),
      h("p", { class: "hint" }, "A bigger model to hand a question to when the small one says it is out of its depth: the flag reference, comparing many models, a long log. It is only asked when a turn hands over, so it can be cheap to keep — the download default is the smaller quant, because it may run on CPU, where that is the faster choice."),
      h("div", { class: "fields" },
        field("Escalation model", escalate, "empty: the small model answers everything, including what it cannot do", true),
        field("Download it at", h("div", { class: "row", style: "gap:8px" }, escalateQuant, escalateFetch), "used only when fetching it"))),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Conversation")),
      h("div", { class: "fields" },
        field("Context window (tokens)", ctx, "Must match the -c of the model above. At about 70% full, or when you press Compact, older turns are summarised."),
        field("Messages kept as written", keep, "The newest ones are never summarised."),
        field("Temperature", temp, "Lower is steadier for questions about numbers."),
        field("Unload when unused for", idle, "Frees the GPU memory. Only models the assistant itself used are unloaded; Load brings one back.", true))),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Memory"), h("span", { class: "spacer" }),
        h("span", { class: `pill ${mem.enabled && mem.model ? "ok" : ""}` }, mem.enabled && mem.model ? "searching by meaning" : "searching by words")),
      h("p", { class: "hint" }, "The assistant remembers your events, notes, profiles, benchmarks and loads. An embedding model turns each record into numbers that describe its meaning, so a question about “cache settings that were slow” also finds records that never use those words. Without one it matches words only."),
      h("p", { class: "hint" }, "Pick one of your embedding models (nomic-embed, bge, e5, gte…). There is nothing to connect: the engine serves it like any other model and loads it when the memory needs it."),
      h("div", { class: "fields" },
        h("label", { class: "check wide" }, memOn, "Use the memory"),
        field("Embedding model", embed, ch.embed.length ? `${ch.embed.length} embedding model${ch.embed.length === 1 ? "" : "s"} in your library` : "No embedding models in your library yet. Download one under Models (search “nomic-embed”).", true),
        field("Records per question", results)),
      h("details", { class: "advanced" }, h("summary", {}, "Advanced: embeddings from another server"),
        h("div", { class: "fields", style: "margin-top:8px" },
          field("Embeddings URL", memEndpoint, "Leave blank. Only set this if the embedding model runs somewhere other than Argos.", true)))));
}

function initAssistant() {
  $("#aSettingsBtn").onclick = () => openAssistantSettings(true);
  $("#aSend").onclick = sendChat;
  $("#aInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } });
  $("#aReset").onclick = async () => {
    if (!(await confirmBox("Clear the whole conversation and its summary?", "Reset chat"))) return;
    try { await api("POST", "/api/assistant/reset"); toast("Chat cleared.", "ok"); loadAssistant(); } catch (e) { fail(e); }
  };
  $("#aCompact").onclick = async () => {
    const b = $("#aCompact");
    b.disabled = true;
    try {
      const r = await api("POST", "/api/assistant/compact");
      toast(r.compacted ? `Compacted ${r.compacted} messages into the summary.` : "Nothing to compact yet.", "ok");
    } catch (e) { fail(e); }
    loadAssistant();
  };
  $("#aDigest").onclick = async () => {
    try {
      const res = await api("GET", "/api/assistant/digest");
      modal("What the assistant sees each turn", h("div", { class: "stack" },
        h("p", { class: "hint", style: "margin:0" }, "Built fresh from the database for every message, after the system prompt."),
        h("pre", { class: "code wrap" }, res.digest)));
    } catch (e) { fail(e); }
  };
}

// ── Settings ─────────────────────────────────────────────────
const SETTINGS_FORM = [
  { group: "paths", title: "Paths", fields: [
    { k: "models_dir", label: "Models folder", hint: "Library and download target. In Docker, mount your GGUF folder here (Argos scans /app/models)." }] },
  { group: "odysseus", title: "Odysseus", hint: "Also on the Odysseus page, where you can see what the token may do.", fields: [
    { k: "url", label: "Odysseus URL", ph: "http://odysseus:7000" },
    { k: "token", label: "API token", type: "password", hint: "ody_… from Odysseus → Settings → API tokens." },
    { k: "brain_in_memory", label: "Let the assistant's memory include Odysseus' Brain", type: "check" },
    { k: "use_presets", label: "Use Odysseus' saved Cookbook presets as models", type: "check" }] },
  { group: "always", title: "Always set", hint: "Added to every model the engine starts and every launch here, when the command does not set them already. A preset's own value wins.", fields: [
    { k: "env", label: "Environment (KEY=VALUE per line)", type: "textarea", ph: "HSA_OVERRIDE_GFX_VERSION=9.0.6" },
    { k: "flags", label: "Flags", ph: "-lm dio --no-warmup", wide: true },
    { k: "on_launch", label: "Apply to launches here (playground, measure)", type: "check" },
    { k: "on_export", label: "Apply to every model the engine starts", type: "check" }] },
  { group: "builder", title: "Builder", fields: [
    { k: "command_format", label: "Command format", type: "select", wide: true, options: [["odysseus", "Odysseus (Cookbook shape)"], ["plain", "As written"]] }] },
  { group: "prefab", title: "Prefab defaults", hint: "Every model's prefab starts from these; embedding, vision and MTP models get their extras on top.", fields: [
    { k: "program", label: "Launch program", wide: true, ph: "/app/bin/remote-llama rocm" },
    { k: "host", label: "Host" }, { k: "port", label: "Port" }, { k: "ngl", label: "GPU layers (-ngl)" }, { k: "ctx", label: "Context (-c)" },
    { k: "flash_attn", label: "Flash attention", type: "select", options: [["", "default"], ["on", "on"], ["off", "off"], ["auto", "auto"]] },
    { k: "cache_type", label: "KV cache type", type: "select", options: KV_TYPES.map((t) => [t, t || "default"]) },
    { k: "load_mode", label: "Load mode (-lm)", list: "list-loadmode", ph: "dio" },
    { k: "split_mode", label: "Split mode", type: "select", options: [["", "default"], ["none", "none"], ["layer", "layer"], ["row", "row"]] },
    { k: "jinja", label: "--jinja for chat models", type: "check" },
    { k: "env", label: "Environment (KEY=VALUE per line)", type: "textarea", ph: "HIP_VISIBLE_DEVICES=0" },
    { k: "extra", label: "Extra flags", ph: "--no-warmup" }] },
  { group: "downloads", title: "Downloads", fields: [
    { k: "hf_endpoint", label: "Hugging Face endpoint", ph: "https://huggingface.co" },
    { k: "hf_token", label: "HF token", type: "password", hint: "Only for gated or private repos." },
    { k: "vram_gb", label: "VRAM for fit (GB)", type: "number", hint: "0 = detect from telemetry / sysfs / nvidia-smi." }] },
  { group: "telemetry", title: "Telemetry", hint: "GPU readings for Logs, Inventory and the Schedule. Everything stays on this machine: the engine samples the GPUs it runs on, and Argos reads them from it.", fields: [
    { k: "enabled", label: "Record GPU history", type: "check" },
    { k: "source", label: "Source", type: "select", wide: true, options: [["auto", "Auto: the engine, else this machine directly"], ["engine", "The engine (rocm-smi / nvidia-smi / sysfs)"], ["local", "This machine directly (sysfs / nvidia-smi)"], ["off", "Off"]] },
    { k: "interval_s", label: "Every (seconds)", type: "number" },
    { k: "raw_days", label: "Keep raw readings (days)", type: "number", hint: "Hourly rollups are kept forever." }] },
];

function settingsField(f, val) {
  let input;
  if (f.type === "select" || f.type === "profile") {
    const opts = f.type === "profile" ? [["", "none"], ...S.profiles.map((p) => [p.profile_id, p.name])] : f.options;
    input = h("select", {}, opts.map(([v, l]) => h("option", { value: v }, l)));
    input.value = val ?? "";
  } else if (f.type === "check") {
    input = h("input", { type: "checkbox", checked: !!val });
    return { input, el: h("label", { class: "check wide" }, input, f.label) };
  } else if (f.type === "textarea") {
    input = h("textarea", { rows: 3, placeholder: f.ph || "", spellcheck: "false" }, val || "");
  } else {
    input = h("input", { type: f.type || "text", value: val ?? "", placeholder: f.ph || "", list: f.list || null, step: f.step || null, spellcheck: "false" });
  }
  const wide = f.wide || ["textarea", "check"].includes(f.type) || (f.hint && f.hint.length > 40) || /dir|file|config|cookbook|endpoint|url/.test(f.k);
  return { input, el: h("label", { class: `field${wide ? " wide" : ""}` }, h("span", {}, f.label), input, f.hint ? h("span", { class: "muted mono-sm" }, f.hint) : null) };
}

function renderSettings() {
  if (!S.settings) return;
  fill($("#sGrid"), SETTINGS_FORM.map((g) => {
    const vals = S.settings[g.group] || {};
    const fields = g.fields.map((f) => ({ f, ...settingsField(f, vals[f.k]) }));
    const save = async () => {
      const out = {};
      fields.forEach(({ f, input }) => { out[f.k] = f.type === "check" ? input.checked : input.value; });
      try {
        await api("PUT", `/api/settings/${g.group}`, out);
        await loadSettings();
        toast(`Saved ${g.title.toLowerCase()}.`, "ok");
        if (g.group === "builder") B.format = S.settings.builder.command_format;
        if (g.group === "paths") loadLibrary();
      } catch (e) { fail(e); }
    };
    return h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, g.title), h("span", { class: "spacer" }), btn("Save", save, "sm primary", "save")),
      g.hint ? h("p", { class: "hint" }, g.hint) : null, h("div", { class: "fields" }, fields.map((x) => x.el)));
  }));
}



// ── Servers ──────────────────────────────────────────────────
// Laid out like Odysseus' Cookbook → Settings → Servers: one card per machine, split into Connection, Environment,
// Model directories (tags with a download-target check and a remove ✖) and Status, plus an inline key panel.
// Local is this container; the rest are reached over SSH.
const SV = { servers: [], draft: null, keyOpen: new Set(), keys: {} };
const SV_COLORS = [["", "Auto"], ["#ff5555", "Red"], ["#ffb86c", "Orange"], ["#f1fa8c", "Yellow"], ["#50fa7b", "Green"],
  ["#8be9fd", "Cyan"], ["#6c8cff", "Blue"], ["#bd93f9", "Purple"], ["#ff79c6", "Pink"]];
const svAuto = (i) => SV_COLORS[1 + (i % (SV_COLORS.length - 1))][0];
const blankServer = () => ({ id: null, name: "", kind: "ssh", host: "", port: 22, gpu: "auto", env_kind: "none", env_value: "", llama_server: "",
  model_dirs: [], download_dir: "", color: "", status: "unknown", engines: {}, has_key: false, is_default: false });

async function loadServers() {
  try {
    SV.servers = (await api("GET", "/api/servers")).servers;
    $("#navServers").textContent = SV.servers.length > 1 ? SV.servers.length : "";
    renderServers();
  } catch (e) { fail(e); }
}

const svSmall = (text, title) => h("span", { class: "sv-msg", title: title || text }, text);

function svStatusText(s) {
  if (s.id == null) return "";
  if (s.status === "connected") return `Connected · ${num(s.latency_ms, 0)} ms`;
  if (s.status === "no-key") return "Key needed";
  if (s.status === "failed") return `Failed · ${s.last_error || "no answer"}`;
  return "";
}

function svModels(s) {
  const m = (s.engines || {}).models;
  if (!m || s.status !== "connected") return null;
  const ok = m.same && !(m.dirs_missing || []).length;
  const bits = [`${m.found} model file${m.found === 1 ? "" : "s"} in ${m.folders || 0} folder${m.folders === 1 ? "" : "s"}`];
  bits.push(m.argos ? (m.same ? `sees all ${m.argos} that Argos sees` : `missing ${m.argos - m.common} of the ${m.argos} Argos sees`) : "Argos sees none yet");
  const detail = [
    (m.dirs_missing || []).length ? `Folder not found on the server: ${m.dirs_missing.join(", ")}` : "",
    (m.missing_here || []).length ? `Not on the server: ${m.missing_here.join(", ")}` : "",
    (m.differ_in_size || []).length ? `Same name, different size: ${m.differ_in_size.join(", ")}` : "",
    (m.only_here || []).length ? `Only on the server: ${m.only_here.join(", ")}` : ""].filter(Boolean).join("\n");
  return h("div", { class: `sv-models ${ok ? "ok" : "warn"}`, title: detail || "Same model files as Argos" },
    h("span", { class: `dot ${ok ? "on" : "warn"}` }), bits.join(" · "), detail ? h("span", { class: "muted" }, " — hover for the list") : null);
}

function svEngines(s) {
  const e = s.engines || {};
  if (s.id == null || s.status !== "connected") return null;
  const chip = (label, value) => h("span", { class: `pill ${value ? "ok" : ""}`, title: value || "not found on this server" }, `${label} ${value ? "✓" : "—"}`);
  return h("div", { class: "chips sv-engines" }, chip("llama.cpp", e.llama_server), chip("vLLM", e.vllm), chip("SGLang", e.sglang), chip("Docker", e.docker),
    h("span", { class: `pill ${e.gpu && e.gpu !== "none" ? "ok" : ""}` }, `GPU: ${e.gpu || "none"}${(e.gpus || []).length ? ` (${[...new Set(e.gpus)].join(", ")})` : ""}`));
}

function serverCard(s, index) {
  const local = s.kind === "local";
  const isNew = s.id == null;
  const color = s.color || svAuto(index);
  const dirs = (s.model_dirs || []).map((d) => d.path);
  if (!dirs.length) dirs.push("/app/models");
  let target = s.download_dir || "";
  const name = h("input", { class: "sv-name", value: s.name || (local ? "Local" : ""), placeholder: "Name", disabled: local, spellcheck: "false" });
  const colorSel = h("select", { class: "sv-color", title: "Server colour" }, SV_COLORS.map(([v, l]) => h("option", { value: v }, v ? l : `Auto · ${SV_COLORS.find((c) => c[0] === svAuto(index))[1]}`)));
  colorSel.value = s.color || "";
  const host = h("input", { class: "sv-host", value: s.host || "", placeholder: "e.g. user@ip", readonly: local, spellcheck: "false" });
  const port = h("input", { class: "sv-port", value: local ? "" : s.port || 22, placeholder: "Port", title: "SSH port (default 22)", readonly: local });
  const envKind = h("select", { class: "sv-env" }, [["none", "None"], ["venv", "venv"], ["conda", "conda"], ["custom", "custom"]].map(([v, l]) => h("option", { value: v }, l)));
  envKind.value = s.env_kind || "none";
  const envValue = h("input", { class: "sv-path", value: s.env_value || "", placeholder: "~/venv or conda-env", spellcheck: "false" });
  const bin = h("input", { class: "sv-bin", value: s.llama_server || "", placeholder: (s.engines || {}).llama_server ? `llama-server: ${s.engines.llama_server}` : "llama-server path (blank = find it)", spellcheck: "false" });
  const read = () => ({
    name: name.value.trim(), host: host.value.trim(), port: Number(port.value) || 22, gpu: s.gpu || "auto", env_kind: envKind.value, env_value: envValue.value.trim(),
    llama_server: bin.value.trim(), model_dirs: dirs.map((path) => ({ path })),
    download_dir: target && dirs.includes(target) ? target : "", color: colorSel.value });
  // Model directories, as Odysseus draws them: a tag per folder; the circled check marks where downloads go
  // (click a tag to move it there), ✖ removes the folder.
  const tags = h("div", { class: "sv-dirs" });
  const newDir = h("input", { class: "sv-dir-input", placeholder: "/path/to/models", spellcheck: "false", "aria-label": "Add a model directory" });
  const addDir = () => { const v = newDir.value.trim().replace(/\/+$/, "") || ""; if (v && !dirs.includes(v)) dirs.push(v); newDir.value = ""; drawDirs(); };
  newDir.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addDir(); } });
  const drawDirs = () => {
    fill(tags, dirs.map((d, j) => {
      const on = (target || dirs[0]) === d;
      return h("span", { class: `sv-dir${on ? " target" : ""}`, title: on ? "Downloads go here" : "Click to send downloads here", dataset: { dir: d },
        onclick: () => { target = d; drawDirs(); } },
        h("span", { class: `sv-dl${on ? " active" : ""}` }, dlCheck(on)),
        h("span", { class: "sv-dir-path" }, d),
        dirs.length === 1 ? null : h("span", { class: "sv-rm", title: "Remove", onclick: (e) => { e.stopPropagation(); dirs.splice(j, 1); if (target === d) target = ""; drawDirs(); } }, "✖"));
    }));
  };
  const save = async () => {
    try {
      const body = read();
      if (isNew) { await api("POST", "/api/servers", { ...body, kind: "ssh" }); SV.draft = null; }
      else await api("PUT", `/api/servers/${s.id}`, local ? { llama_server: body.llama_server, model_dirs: body.model_dirs, download_dir: body.download_dir, color: body.color, name: undefined } : body);
      toast(`Saved ${body.name || "server"}.`, "ok");
      await loadServers();
    } catch (x) { fail(x); }
  };
  const check = async () => {
    try {
      const r = (await api("POST", `/api/servers/${s.id}/check`)).server;
      toast(r.status === "connected" ? `${r.name}: connected.` : `${r.name}: ${r.last_error || r.status}`, r.status === "connected" ? "ok" : "err");
      await loadServers();
    } catch (x) { fail(x); }
  };
  const toggleKey = () => { SV.keyOpen.has(s.id) ? SV.keyOpen.delete(s.id) : SV.keyOpen.add(s.id); renderServers(); };
  const del = async () => {
    if (isNew) { SV.draft = null; return renderServers(); }
    if (!(await confirmBox(`Remove the server “${s.name}”? Models on it are not touched.`, "Remove"))) return;
    try { await api("DELETE", `/api/servers/${s.id}`); await loadServers(); } catch (x) { fail(x); }
  };
  const makeDefault = async () => { if (isNew || s.is_default) return; try { await api("POST", `/api/servers/${s.id}/default`); await loadServers(); } catch (x) { fail(x); } };
  const ico = (n) => icon(n);
  const small = (label, fn, iconName, cls = "") => h("button", { class: `sv-btn ${cls}`, onclick: fn }, ico(iconName), label);

  const keyPanel = !local && !isNew && SV.keyOpen.has(s.id) ? (() => {
    const cmd = h("textarea", { class: "sv-key-cmd", rows: 3, readonly: true, spellcheck: "false" }, SV.keys[s.id] || (s.has_key ? "Loading…" : "Click Generate key."));
    const copy = h("button", { class: "sv-btn", disabled: !(SV.keys[s.id]), onclick: () => copyText(cmd.value) }, "Copy command");
    const genBtn = h("button", { class: "sv-btn", onclick: async () => {
      const replacing = !!(s.has_key && SV.keys[s.id]);
      if (replacing && !(await confirmBox("Replace this server's key? The old one stops working.", "Replace"))) return;
      try {
        const r = await api("POST", `/api/servers/${s.id}/key`, { force: replacing });
        SV.keys[s.id] = r.command; cmd.value = r.command; copy.disabled = false; genBtn.textContent = "New key";
        if (!s.has_key) loadServers();
      } catch (x) { fail(x); }
    } }, s.has_key ? "New key" : "Generate key");
    if (s.has_key && !SV.keys[s.id]) api("GET", `/api/servers/${s.id}/key`).then((r) => { SV.keys[s.id] = r.command; cmd.value = r.command; copy.disabled = false; }).catch(fail);
    return h("div", { class: "sv-key" },
      h("div", { class: "sv-key-row" }, genBtn, copy, h("span", { class: "muted" }, "Run this command in your terminal once, then click Check.")), cmd);
  })() : null;

  const section = (title, hint, ...kids) => h("div", { class: "sv-section" },
    h("div", { class: "sv-section-title" }, title, hint ? h("span", { class: "muted" }, ` — ${hint}`) : null), ...kids);
  const card = h("div", { class: "sv", style: `--sv-color:${color}` },
    h("div", { class: "sv-head" },
      h("span", { class: `dot ${s.status === "connected" ? "on" : s.status === "failed" ? "bad" : s.status === "no-key" ? "warn" : ""}` }),
      h("span", { class: "sv-title" }, s.name || (isNew ? "New server" : "Local")),
      svSmall(svStatusText(s)),
      h("span", { class: "spacer" }),
      isNew ? null : small("Check", check, "check"),
      !local && !isNew ? small("Key", toggleKey, "key", SV.keyOpen.has(s.id) ? "on" : "") : null,
      isNew ? small("Cancel", del, "x") : h("span", { class: `sv-default${s.is_default ? " on" : ""}`, title: s.is_default ? "Default server" : "Make this the default server", onclick: makeDefault },
        s.is_default ? h("span", { class: "sv-tick" }, "✓") : null, "default")),
    section("Connection", local ? "this container" : "SSH: user@host and port", h("div", { class: "sv-row" }, name, colorSel, host, port)),
    section("Environment", "what runs before llama-server, and where it is", h("div", { class: "sv-row" }, envKind, envValue, bin)),
    section("Model directories", "the circled check is where downloads go; click a folder to move it",
      tags, h("div", { class: "sv-row sv-add-row" }, newDir, h("button", { class: "sv-btn", onclick: addDir }, icon("plus"), "Add directory"))),
    isNew ? null : section("Status", null, svEngines(s) || h("span", { class: "muted mono-sm" }, s.status === "connected" ? "" : "Check to see what this server has."), svModels(s)),
    keyPanel,
    h("div", { class: "sv-foot" }, !local && !isNew ? small("Delete", del, "trash", "danger") : null, small("Save", save, "save")));
  drawDirs();
  return card;
}

/** Odysseus' download-target mark: a circle, with a check inside on the folder downloads go to. */
function dlCheck(on) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [k, v] of Object.entries({ width: 13, height: 13, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 2.6,
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" })) svg.setAttribute(k, v);
  svg.innerHTML = '<circle cx="12" cy="12" r="9"></circle>' + (on ? '<polyline points="8 12 11 15 16 9"></polyline>' : "");
  return svg;
}

function renderServers() {
  fill($("#svList"), [SV.draft ? serverCard(SV.draft, SV.servers.length) : null, ...SV.servers.map((s, i) => serverCard(s, i))]);
}
document.addEventListener("DOMContentLoaded", () => {
  $("#svAdd").addEventListener("click", () => { SV.draft = blankServer(); renderServers(); });
  $("#svImport").addEventListener("click", async () => {
    try {
      const r = await api("POST", "/api/servers/import/odysseus");
      toast(r.created.length ? `Imported ${r.created.join(", ")}. Open Key on each, then Check.` : "Nothing new to import from Odysseus.", "ok");
      SV.servers = r.servers; renderServers();
    } catch (x) { fail(x); }
  });
});

// ── Inventory ────────────────────────────────────────────────
// Long-term history. New quants, cache types, flags and models are recorded as "new" and shown under New & unknown.
const IV = { pane: "models", data: null, sessions: [], traits: [], showAll: false, suggestions: null, assistNote: "", busy: false,
  filter: "", days: 30, model: "", sortM: { key: null, asc: false }, sortS: { key: null, asc: false },
  selected: null, page: null, pageDays: 7, byFamily: false, closed: new Set() };

function dur(sec) {
  if (sec == null) return DASH;
  const s = Math.max(0, Math.round(sec));
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  if (s < 86400 * 2) return `${num(s / 3600, 1)} h`;
  return `${num(s / 86400, 1)} d`;
}
const chips = (xs) => (xs && xs.length ? h("div", { class: "chips" }, xs.map((x) => h("span", { class: "chip" }, x))) : DASH);

async function loadInventory() {
  try {
    IV.data = await api("GET", "/api/inventory");
    fill($("#navInventory"), IV.data.pending ? h("span", { class: "pill warn", title: "new settings waiting for review" }, String(IV.data.pending)) : null);
    fill($("#ivPending"), IV.data.pending ? h("span", { class: "pill warn", style: "margin-left:6px" }, String(IV.data.pending)) : null);
    const last = IV.data.last_refresh && IV.data.last_refresh.at;
    $("#ivRefreshed").textContent = last ? `scanned ${ago(last)}` : "not scanned yet";
    renderInvPane();
  } catch (e) { fail(e); }
}
function invPane() { return $(".tabs[data-tabs=inventory] button.on")?.dataset.pane || "models"; }
function renderInvPane() {
  const p = invPane();
  if (p === "models") renderInvModels();
  else if (p === "host") renderInvHost();
  else if (p === "sessions") loadInvSessions();
  else if (p === "compare") loadCompare();
  else loadInvReview();
}
async function refreshInventory() {
  const b = $("#ivRefresh");
  b.disabled = true;
  try {
    const r = (await api("POST", "/api/inventory/refresh")).result;
    const bits = [];
    if (r.models_new.length) bits.push(`${r.models_new.length} new model${r.models_new.length > 1 ? "s" : ""}`);
    if (r.models_gone.length) bits.push(`${r.models_gone.length} gone`);
    if (r.traits_new.length) bits.push(`${r.traits_new.length} new setting${r.traits_new.length > 1 ? "s" : ""} to review`);
    if (r.host_changed) bits.push("host facts changed");
    toast(bits.length ? `Scanned: ${bits.join(", ")}.` : "Scanned: nothing new.", "ok");
    if (r.problems.length) toast(r.problems[0], "err");
    await loadInventory();
  } catch (e) { fail(e); } finally { b.disabled = false; }
}

// ----- models ------------------------------------------------------------------------------
function statTiles(items) {
  // a word (a model name) gets the smaller one-line style; numbers stay large
  return h("div", { class: `stats${items.filter(Boolean).length > 6 ? " tight" : ""}`, style: "margin-bottom:14px" }, items.filter(Boolean).map(([k, v, small, title]) => {
    const text = typeof v === "string" && !/^[\d.,\s]+[a-zA-Z]{0,3}$/.test(v) && v !== DASH;
    return h("div", { class: "stat", title: title || (text ? v : null) }, h("div", { class: "k" }, k),
      h("div", { class: `v${text ? " text" : ""}` }, v ?? DASH, small ? h("small", {}, small) : null));
  }));
}
function modelCell(main, sub, title) {
  return h("td", { class: "cell-model", title: title || main }, h("div", { class: "cell-main" }, main), sub ? h("div", { class: "cell-sub" }, sub) : null);
}
/** "62 – 205" lowest to highest speed, over 90 days of requests and benchmarks. */
function speedRange(r, d) {
  if (!r) return DASH;
  return h("td", { class: "num nowrap", title: `lowest ${num(r.min, d)} · highest ${num(r.max, d)} t/s over ${r.n} requests and benchmarks (90 days)` },
    r.min === r.max ? num(r.max, d) : [h("span", { class: "muted" }, `${num(r.min, d)} – `), h("b", { class: "strong" }, num(r.max, d))]);
}
function renderInvModels() {
  const el = $("#ivModels");
  if (IV.selected) return renderInvModelPage();
  if (!IV.data) return fill(el, h("div", { class: "empty" }, "Loading…"));
  const all = IV.data.models;
  const q = IV.filter.toLowerCase();
  const rows = all.filter((m) => !q || `${m.name} ${m.model_id} ${m.quants.join(" ")} ${m.arch} ${m.cache.join(" ")}`.toLowerCase().includes(q));
  const table = h("table", { class: "dense" });
  renderTable(table, [
    { key: "name", label: "Model", sort: (m) => m.name.toLowerCase(), cell: (m) => modelCell(m.name,
      [m.open ? "● loaded" : null, m.origin, m.arch, !m.present ? "gone" : null].filter(Boolean).join(" · "), m.model_id) },
    { label: "Settings seen", cell: (m) => chips([...m.quants, ...m.cache.map((c) => `kv ${c}`), ...m.split_modes.map((x) => `sm ${x}`), ...m.spec_types].slice(0, 6)) },
    { label: "GPUs", cell: (m) => (m.gpus.length ? m.gpus.join(",") : DASH) },
    { key: "prefill", label: "Prefill t/s", num: true, sort: (m) => (m.prefill ? m.prefill.max : 0), cell: (m) => speedRange(m.prefill, 0) },
    { key: "loaded", label: "Loaded", num: true, sort: (m) => m.loaded_seconds, cell: (m) => (m.loaded_seconds ? dur(m.loaded_seconds) : DASH) },
    { key: "last", label: "Last", sort: (m) => m.last_loaded || 0, cell: (m) => h("td", { class: "nowrap" }, ago(m.last_loaded)) },
    { key: "vram", label: "VRAM", num: true, sort: (m) => m.vram_gib_avg || 0, cell: (m) => (m.vram_gib_avg ? `${num(m.vram_gib_avg)} GiB` : DASH) },
    { key: "tps", label: "Decode t/s", num: true, sort: (m) => (m.decode ? m.decode.max : 0), cell: (m) => speedRange(m.decode, 1) },
  ], rows, IV.sortM, "Nothing here yet. Press Scan, or load a model.", { onRow: (m) => openModel(m.model_id), ...(IV.byFamily ? {
    group: (m) => { const f = modelFamily({ name: m.name || m.model_id, kind: "other" }); return { key: f, label: f }; },
    groupOrder: (a, b) => a.label.localeCompare(b.label), collapsed: IV.closed,
    onToggle: (k) => { IV.closed.has(k) ? IV.closed.delete(k) : IV.closed.add(k); renderInvModels(); },
    groupExtra: (g) => h("span", { class: "group-meta" }, `${g.rows.reduce((a, m) => a + (m.sessions || 0), 0)} loads · ${dur(g.rows.reduce((a, m) => a + (m.loaded_seconds || 0), 0))} loaded`),
  } : {}) });
  const famBox = h("input", { type: "checkbox", checked: !!IV.byFamily });
  famBox.addEventListener("change", () => { IV.byFamily = famBox.checked; renderInvModels(); });
  const search = h("input", { type: "search", placeholder: "Filter by name, quant, cache…", value: IV.filter, style: "width:260px" });
  search.addEventListener("input", debounce(() => { IV.filter = search.value; renderInvModels(); }, 150));
  const used = all.filter((m) => m.sessions);
  fill(el,
    statTiles([
      ["Models", all.filter((m) => m.present).length, `of ${all.length} seen`],
      ["Loads", all.reduce((a, m) => a + m.sessions, 0), IV.data.open_sessions ? `${IV.data.open_sessions} now` : null],
      ["Time loaded", dur(all.reduce((a, m) => a + m.loaded_seconds, 0))],
      ["Requests", num(all.reduce((a, m) => a + (m.requests || 0), 0), 0), "90 days"],
      ["Most used", used.length ? used[0].name : DASH, null, "longest time loaded"],
      ["To review", IV.data.pending, null, "new settings under New & unknown"],
    ]),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, `Models (${rows.length})`), h("span", { class: "spacer" }),
      h("span", { class: "hint" }, "click a model for its speeds by launch config"), h("label", { class: "check" }, famBox, "group by name"), search),
      h("div", { class: "table-wrap" }, table)));
}

// ----- one model -------------------------------------------------------------------------
const cfgColor = (c) => (!c || c.id === "untracked" || c.slot >= 8 ? "var(--muted)" : seriesColor(c.slot));
const niceTop = (v) => { const st = niceStep(Math.max(v, 1)); return Math.ceil(Math.max(v, 1) / st) * st; };

async function openModel(id) {
  IV.selected = id; IV.page = null;
  if (invPane() !== "models") $(".tabs[data-tabs=inventory] button[data-pane=models]").click();
  renderInvModels();
  try { IV.page = await api("GET", `/api/inventory/model/${encodeURIComponent(id)}?days=${IV.pageDays}`); } catch (e) { fail(e); IV.selected = null; }
  renderInvModels();
}

function renderInvModelPage() {
  const el = $("#ivModels"), P = IV.page;
  const back = btn("All models", () => { IV.selected = null; IV.page = null; renderInvModels(); }, "sm ghost", "arrow-left");
  const range = h("div", { class: "seg", role: "group", "aria-label": "Range" }, [[1 / 24, "1h"], [0.25, "6h"], [0.5, "12h"], [1, "1d"], [7, "7d"], [30, "30d"], [90, "90d"], [365, "1y"]].map(([v, l]) =>
    h("button", { class: IV.pageDays === v ? "on" : "", onclick: () => { IV.pageDays = v; openModel(IV.selected); } }, l)));
  const compareAll = P && P.configs.filter((c) => c.id !== "untracked").length > 1
    ? btn("Compare its configs", () => { CMP.items = P.configs.filter((c) => c.id !== "untracked").slice(0, 6).map((c) => ({ model_id: IV.selected, config: c.id })); CMP.result = null; openCompare(); }, "sm", "layers") : null;
  const head = h("div", { class: "row", style: "margin-bottom:12px" }, back, h("h2", { style: "margin:0; font-size:14px", class: "strong" }, P?.model?.name || IV.selected), h("span", { class: "spacer" }), compareAll, range);
  if (!P) return fill(el, head, h("div", { class: "empty" }, "Loading…"));
  const S = P.summary, cfgs = P.configs, byId = Object.fromEntries(cfgs.map((c) => [c.id, c]));
  const m = P.model || {};
  const tiles = statTiles([
    ["Decode", S.decode_median != null ? num(S.decode_median) : DASH, "t/s median"],
    ["Prefill", S.prefill_median != null ? num(S.prefill_median, 0) : DASH, "t/s median"],
    ["Requests", num(S.requests, 0), S.benchmarks ? `+ ${S.benchmarks} bench` : null],
    ["Launch configs", S.configs],
    ["Loaded", dur(S.loaded_s), null, `${S.sessions} loads`],
    m.vram_gib_avg ? ["VRAM", num(m.vram_gib_avg), "GiB"] : null,
  ]);
  if (!cfgs.length) return fill(el, head, tiles, h("div", { class: "card" }, h("div", { class: "empty" }, "No requests, loads or benchmarks for this model in this range.")));
  const legend = cfgs.length > 1 ? h("div", { class: "legend" }, cfgs.map((c) => h("span", {}, h("span", { class: "key", style: `background:${cfgColor(c)}` }), c.label))) : null;
  const pointsFor = (k) => P.points.filter((p) => p[k] != null).map((p) => ({ ...p, y: p[k] }));
  const decBox = h("div", { class: "chart" }), preBox = h("div", { class: "chart" });
  const table = h("table", { class: "dense" });
  const range3 = (st, d = 1) => (st.median == null ? DASH : h("span", { title: `p10 ${num(st.p10, d)} · p90 ${num(st.p90, d)} · ${st.n} samples` }, h("b", { class: "strong" }, num(st.median, d)), h("span", { class: "muted" }, ` ${num(st.p10, d)}–${num(st.p90, d)}`)));
  renderTable(table, [
    { label: "Launch config", cell: (c) => h("td", { class: "cell-model", title: c.command || c.label }, h("div", { class: "cell-main" }, h("span", { class: "swatch", style: `--c:${cfgColor(c)}` }), c.label),
      h("div", { class: "cell-sub" }, [c.gpus.length ? `GPU ${c.gpus.join(",")}` : null, c.vram_gib ? `${num(c.vram_gib)} GiB` : null, c.sessions ? `${c.sessions} loads · ${dur(c.loaded_s)}` : null].filter(Boolean).join(" · "))) },
    { label: "-np", num: true, cell: (c) => c.parallel_setting ?? (c.id === "untracked" ? DASH : "1") },
    { label: "Requests", num: true, cell: (c) => `${c.requests}${c.benchmarks ? ` +${c.benchmarks}b` : ""}` },
    { label: "Prefill t/s", num: true, cell: (c) => range3(c.prefill, 0) },
    { label: "Decode t/s", num: true, cell: (c) => range3(c.decode) },
    { label: "Last used", cell: (c) => ago(c.last_used) },
    { label: "", cell: (c) => (c.command ? h("td", { class: "actions nowrap" }, iconBtn("layers", "Add to Compare", () => addToCompare({ model_id: IV.selected, config: c.id })),
      iconBtn("copy", "Copy the launch command", () => copyText(c.command), "ghost")) : "") },
  ], cfgs, { key: null }, "");
  fill(el, head, tiles,
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Speed by launch config"), h("span", { class: "spacer" }),
      h("span", { class: "hint" }, "dot = median, line = 10th to 90th percentile")), configStrips(cfgs)),
    h("div", { class: "card" }, legend, h("div", { class: "charts" }, decBox, preBox)),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Launch configs"), h("span", { class: "spacer" }),
      h("span", { class: "hint" }, `prefill counts prompts of ${P.thresholds.min_prompt_tokens}+ tokens, decode ${P.thresholds.min_output_tokens}+ tokens`)),
      h("div", { class: "table-wrap" }, table)));
  const simBox = h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Similar models")), h("div", { class: "muted mono-sm" }, "Looking…"));
  el.append(simBox);
  api("GET", `/api/inventory/similar/${encodeURIComponent(IV.selected)}`).then((r) => {
    fill(simBox, h("div", { class: "card-head" }, h("h2", {}, "Similar models"), h("span", { class: "spacer" }),
      h("span", { class: "hint" }, r.results.some((x) => x.similarity != null) ? "by meaning" : "by words; add an embedding model in Settings for meaning")),
      r.results.length ? r.results.map((x) => h("div", { class: "row nowrap mono-sm", style: "padding:3px 0" },
        h("a", { href: "#", onclick: (e) => { e.preventDefault(); openModel(x.model_id); } }, x.model_id),
        h("span", { class: "muted" }, x.similarity != null ? `${Math.round(x.similarity * 100)}% alike` : ""))) : h("div", { class: "muted mono-sm" }, "Nothing similar yet."));
  }).catch(() => simBox.remove());
  scatterChart(decBox, { title: "Decode speed per request", unit: "t/s", points: pointsFor("decode"), byId });
  scatterChart(preBox, { title: "Prefill speed per request", unit: "t/s", points: pointsFor("prefill"), byId, digits: 0 });

}

/** One row per config: a dot-and-whisker strip for prefill and one for decode, each with its own axis. */
function configStrips(cfgs) {
  const W = 300, metrics = [["prefill", "Prefill t/s", 0], ["decode", "Decode t/s", 1]];
  const tops = Object.fromEntries(metrics.map(([k]) => [k, niceTop(Math.max(...cfgs.map((c) => c[k].p90 ?? c[k].median ?? 0)))]));
  const X = (k, v) => 8 + (v / tops[k]) * (W - 16);
  const axis = (k, title) => {
    const svg = sv("svg", { viewBox: `0 0 ${W} 30`, class: "strip-axis", "aria-hidden": "true" });
    const t = sv("text", { x: 8, y: 11, class: "strip-title" }); t.textContent = title; svg.append(t);
    [0, 0.5, 1].forEach((f) => { const v = tops[k] * f, x = X(k, v);
      const lbl = sv("text", { x, y: 27, "text-anchor": f === 0 ? "start" : f === 1 ? "end" : "middle" }); lbl.textContent = num(v, 0); svg.append(lbl); });
    return svg;
  };
  const strip = (c, k, d) => {
    const st = c[k], svg = sv("svg", { viewBox: `0 0 ${W} 22`, role: "img", "aria-label": `${c.label} ${k}` });
    [0, 0.5, 1].forEach((f) => svg.append(sv("line", { x1: X(k, tops[k] * f), x2: X(k, tops[k] * f), y1: 0, y2: 22, class: "strip-grid" })));
    if (st.median == null) { const t = sv("text", { x: 8, y: 15, class: "strip-none" }); t.textContent = "no data"; svg.append(t); return svg; }
    const tip = sv("title"); tip.textContent = `${c.label}\n${k}: median ${num(st.median, d)} t/s (p10 ${num(st.p10, d)}, p90 ${num(st.p90, d)}, max ${num(st.max, d)}) · ${st.n} samples`;
    svg.append(sv("line", { x1: X(k, st.p10), x2: X(k, st.p90), y1: 11, y2: 11, stroke: cfgColor(c), "stroke-width": 2, "stroke-linecap": "round" }),
      sv("circle", { cx: X(k, st.median), cy: 11, r: 5, fill: cfgColor(c), class: "dot" }), tip);
    return svg;
  };
  return h("div", { class: "strips" },
    h("div", { class: "strips-row head" }, h("div", {}), metrics.map(([k, t]) => h("div", {}, axis(k, t)))),
    cfgs.map((c) => h("div", { class: "strips-row" },
      h("div", { class: "strip-label", title: c.command || c.label }, h("span", { class: "swatch", style: `--c:${cfgColor(c)}` }), c.label,
        h("span", { class: "muted" }, ` ${c.requests + c.benchmarks}`)),
      metrics.map(([k, , d]) => h("div", {}, strip(c, k, d))))));
}

/** Each request as a dot over time, colored by its launch config; benchmarks are squares. */
function scatterChart(box, { title, unit, points, byId, digits = 1 }) {
  if (!points.length) return fill(box, h("h3", {}, title), h("div", { class: "empty" }, "No samples."));
  const W = Math.max(260, box.clientWidth || 400), H = 200, m = { l: 44, r: 12, t: 8, b: 22 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const top = niceTop(Math.max(...points.map((p) => p.y))), step = niceStep(top);
  let x0 = Math.min(...points.map((p) => p.ts)), x1 = Math.max(...points.map((p) => p.ts));
  if (x1 - x0 < 600) { x0 -= 300; x1 += 300; }
  const span = x1 - x0, X = (t) => m.l + ((t - x0) / span) * iw, Y = (v) => m.t + ih - (v / top) * ih;
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${title} chart`, tabindex: "0" });
  const grid = sv("g", { class: "grid" }), axis = sv("g", { class: "axis" });
  for (let i = 0; i <= Math.round(top / step); i++) {
    const v = step * i, y = Y(v);
    if (i) grid.append(sv("line", { x1: m.l, x2: W - m.r, y1: y, y2: y }));
    const t = sv("text", { x: m.l - 6, y: y + 3, "text-anchor": "end" }); t.textContent = v >= 1000 ? `${+(v / 1000).toFixed(1)}K` : `${+v.toFixed(0)}`; axis.append(t);
  }
  for (let i = 0; i < 4; i++) {
    const t = x0 + (span * i) / 3, lbl = sv("text", { x: X(t), y: H - 6, "text-anchor": i === 0 ? "start" : i === 3 ? "end" : "middle" });
    lbl.textContent = timeLabel(t, span); axis.append(lbl);
  }
  svg.append(grid, sv("line", { class: "base", x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0) }), axis);
  const marks = points.map((p) => {
    const c = byId[p.config], color = cfgColor(c), cx = X(p.ts), cy = Y(p.y);
    const mark = p.source === "bench" ? sv("rect", { x: cx - 4, y: cy - 4, width: 8, height: 8, rx: 1.5, fill: color, class: "dot" })
      : sv("circle", { cx, cy, r: 3.5, fill: color, class: "dot" });
    svg.append(mark);
    return { p, cx, cy, c };
  });
  const ring = sv("circle", { r: 7, fill: "none", stroke: "var(--strong)", "stroke-width": 1.5, visibility: "hidden" });
  const hit = sv("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" });
  svg.append(ring, hit);
  const tip = h("div", { class: "tip" });
  const show = (k) => {
    const { p, cx, cy, c } = marks[k];
    ring.setAttribute("cx", cx); ring.setAttribute("cy", cy); ring.setAttribute("visibility", "visible");
    fill(tip, h("div", { class: "t" }, new Date(p.ts * 1000).toLocaleString()),
      h("div", { class: "r" }, h("span", { class: "key", style: `background:${cfgColor(c)}` }), h("b", {}, `${num(p.y, digits)} ${unit}`), c ? c.label : p.config),
      h("div", { class: "r muted" }, p.source === "bench" ? "benchmark" : `${p.concurrent} at once · ${num(p.input_tokens, 0)} in / ${num(p.output_tokens, 0)} out`));
    tip.style.display = "block";
    tip.style.left = `${Math.min(Math.max(0, (cx / W) * box.clientWidth + 12), box.clientWidth - tip.offsetWidth)}px`;
    tip.style.top = "26px";
  };
  const hide = () => { ring.setAttribute("visibility", "hidden"); tip.style.display = "none"; };
  let cur = marks.length - 1;
  hit.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect(), px = ((e.clientX - r.left) / r.width) * W, py = ((e.clientY - r.top) / r.height) * H;
    let best = -1, bd = 400;
    marks.forEach((mk, i) => { const d = (mk.cx - px) ** 2 + (mk.cy - py) ** 2; if (d < bd) { bd = d; best = i; } });
    if (best < 0) hide(); else { cur = best; show(best); }
  });
  hit.addEventListener("pointerleave", hide);
  svg.addEventListener("focus", () => show(cur));
  svg.addEventListener("blur", hide);
  svg.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") cur = Math.max(0, cur - 1); else if (e.key === "ArrowRight") cur = Math.min(marks.length - 1, cur + 1); else return;
    e.preventDefault(); show(cur);
  });
  fill(box, h("h3", {}, title, h("small", {}, unit)), svg, tip);
}

/** Median decode speed by how many requests were being served at once, one line per launch config. */
function concurrencyChart(box, cfgs) {
  const series = cfgs.map((c) => ({ c, pts: Object.entries(c.decode_by_concurrency).map(([k, v]) => ({ x: Number(k), y: v.median, n: v.n })).filter((p) => p.y != null) }))
    .filter((s) => s.pts.length);
  const maxX = Math.max(1, ...series.flatMap((s) => s.pts.map((p) => p.x)));
  const title = h("h3", {}, "Decode speed by requests at once", h("small", {}, "t/s median"));
  const note = h("p", { class: "hint", style: "margin:6px 0 0" },
    "How much each extra simultaneous request costs. --parallel sets how many can run at once; this shows how many actually did. Only requests the engine served count, not benchmarks.");
  if (maxX < 2) return fill(box, title, h("div", { class: "empty" }, "Every request so far ran alone, so there is nothing to compare yet."), note);
  const W = Math.max(260, box.clientWidth || 400), H = 190, m = { l: 44, r: 16, t: 10, b: 24 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const top = niceTop(Math.max(...series.flatMap((s) => s.pts.map((p) => p.y)))), step = niceStep(top);
  const X = (x) => m.l + ((x - 1) / Math.max(1, maxX - 1)) * iw, Y = (v) => m.t + ih - (v / top) * ih;
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Decode speed by concurrent requests" });
  const grid = sv("g", { class: "grid" }), axis = sv("g", { class: "axis" });
  for (let i = 0; i <= Math.round(top / step); i++) {
    const v = step * i, y = Y(v);
    if (i) grid.append(sv("line", { x1: m.l, x2: W - m.r, y1: y, y2: y }));
    const t = sv("text", { x: m.l - 6, y: y + 3, "text-anchor": "end" }); t.textContent = `${+v.toFixed(0)}`; axis.append(t);
  }
  for (let x = 1; x <= maxX; x++) { const t = sv("text", { x: X(x), y: H - 6, "text-anchor": "middle" }); t.textContent = `${x} at once`; axis.append(t); }
  svg.append(grid, sv("line", { class: "base", x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0) }), axis);
  series.forEach(({ c, pts }) => {
    const color = cfgColor(c);
    if (pts.length > 1) svg.append(sv("path", { class: "series", stroke: color, d: pts.map((p, i) => `${i ? "L" : "M"}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join("") }));
    pts.forEach((p) => {
      const dot = sv("circle", { cx: X(p.x), cy: Y(p.y), r: 5, fill: color, class: "dot" });
      const t = sv("title"); t.textContent = `${c.label}\n${p.x} at once: ${num(p.y)} t/s median (${p.n} requests)`; dot.append(t);
      svg.append(dot);
    });
  });
  fill(box, title, svg, note);
}

// ----- compare -----------------------------------------------------------------------------
// Two to six launch configs side by side: only what differs (flags, env vars, the model file) and what it did.
const CMP = { items: [], options: null, result: null, showSame: false, answer: "" };
const cmpKey = (it) => (it.run_id ? `run:${it.run_id}` : `${it.model_id}|${it.config}`);

/** Add a config (or a benchmark run) to Compare; with two or more picked, open the Compare tab. */
function addToCompare(item, open = false) {
  if (CMP.items.some((x) => cmpKey(x) === cmpKey(item))) return toast("Already in Compare.", "");
  CMP.items = [...CMP.items, item].slice(-6);
  CMP.result = null;
  if (open || CMP.items.length >= 2) openCompare();
  else toast("Added to Compare. Pick one more to compare with.", "ok");
}
function openCompare() {
  if (S.view !== "inventory") show("inventory");
  $(".tabs[data-tabs=inventory] button[data-pane=compare]").click();
}
async function loadCompare() {
  const el = $("#ivCompare");
  if (!CMP.options) {
    fill(el, h("div", { class: "empty" }, "Loading the launch configs…"));
    try { CMP.options = (await api("GET", "/api/compare/options")).options; } catch (e) { return fill(el, h("div", { class: "empty" }, e.message)); }
  }
  if (CMP.items.length >= 2 && !CMP.result) {
    try {
      CMP.result = await api("POST", "/api/compare", { items: CMP.items });
      CMP.answer = "";
    } catch (e) { CMP.result = { error: e.message }; }
  }
  renderCompare();
}
function renderCompare() {
  const el = $("#ivCompare"), opts = CMP.options || [];
  const byModel = {};
  opts.forEach((o) => { (byModel[o.model_id] = byModel[o.model_id] || []).push(o); });
  const picker = (it, i) => {
    const sel = h("select", { "aria-label": `Config ${String.fromCharCode(65 + i)}` }, h("option", { value: "" }, "choose a launch config…"),
      Object.entries(byModel).map(([mid, list]) => h("optgroup", { label: mid }, list.map((o) =>
        h("option", { value: `${o.model_id}|${o.config}` }, `${o.model_id} › ${o.label}${o.decode ? ` · ${num(o.decode)} t/s` : ""}`)))));
    if (it && it.run_id) sel.append(h("option", { value: cmpKey(it) }, `benchmark run ${it.label || it.run_id}`));
    sel.value = it ? cmpKey(it) : "";
    sel.addEventListener("change", () => {
      const [model_id, config] = sel.value.split("|");
      const items = [...CMP.items];
      if (sel.value) items[i] = { model_id, config }; else items.splice(i, 1);
      CMP.items = items.filter(Boolean); CMP.result = null; loadCompare();
    });
    return h("div", { class: "cmp-pick" }, h("span", { class: "cmp-letter" }, String.fromCharCode(65 + i)), sel,
      it ? iconBtn("x", "Remove", () => { CMP.items = CMP.items.filter((_, j) => j !== i); CMP.result = null; loadCompare(); }, "ghost") : null);
  };
  const quick = h("select", { "aria-label": "Compare every config of a model" }, h("option", { value: "" }, "every config of one model…"),
    Object.entries(byModel).filter(([, l]) => l.length > 1).map(([mid, l]) => h("option", { value: mid }, `${mid} (${l.length})`)));
  quick.addEventListener("change", () => {
    if (!quick.value) return;
    CMP.items = byModel[quick.value].slice(0, 6).map((o) => ({ model_id: o.model_id, config: o.config })); CMP.result = null; loadCompare();
  });
  const pickers = h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Compare launch configs"), h("span", { class: "spacer" }), quick),
    h("p", { class: "hint" }, "A launch config is one way a model was started: its flags and env vars (port and host do not count). Pick two or more, of one model or of different ones. Benchmark runs and model pages have Compare buttons too."),
    h("div", { class: "cmp-picks" }, [...CMP.items, null].slice(0, 6).map((it, i) => (it || i < 2 || CMP.items.length === i ? picker(it, i) : null)).filter(Boolean)));
  if (!opts.length) return fill(el, pickers, h("div", { class: "card" }, h("div", { class: "empty" }, "Nothing to compare yet: models need loads or benchmarks first.")));
  const R = CMP.result;
  if (!R || CMP.items.length < 2) return fill(el, pickers);
  if (R.error) return fill(el, pickers, h("div", { class: "card" }, h("div", { class: "empty" }, R.error)));

  const letters = R.items.map((_, i) => String.fromCharCode(65 + i));
  const head = h("tr", {}, h("th", {}, "Setting"), h("th", {}, ""), R.items.map((it, i) => h("th", { title: it.command }, `${letters[i]} · ${it.label}`)));
  const shown = R.settings.filter((r) => r.differs || CMP.showSame);
  const setRows = shown.map((r) => h("tr", { class: r.differs ? "cmp-diff" : "cmp-same" },
    h("td", {}, h("code", { class: r.kind === "env" ? "tk-env" : "tk-flag" }, r.kind === "flag" ? `--${r.key}` : r.key)),
    h("td", {}, h("span", { class: `pill${r.kind === "env" ? " accent" : ""}` }, r.kind)),
    r.values.map((v, i) => h("td", { class: r.differs && v !== r.values[0] ? "cmp-changed" : v === "not set" ? "muted" : "" }, v))));
  const same = h("input", { type: "checkbox", checked: CMP.showSame });
  same.addEventListener("change", () => { CMP.showSame = same.checked; renderCompare(); });
  const pctCell = (x, better) => {
    if (x == null || Math.abs(x) < 1) return null;
    const good = better === 0 ? null : (x > 0) === (better > 0);
    return h("span", { class: `cmp-pct ${good == null ? "" : good ? "good" : "bad"}` }, `${x > 0 ? "+" : ""}${num(x, 0)}%`);
  };
  const resRows = R.results.map((r) => h("tr", {}, h("td", { class: "strong" }, r.label),
    r.values.map((v, i) => h("td", { class: "num" }, v == null ? DASH : num(v, r.key === "prefill" ? 0 : 1), i ? [" ", pctCell(r.change_pct[i], r.better)] : null))));
  const resHead = h("tr", {}, h("th", {}, "Result"), R.items.map((it, i) => h("th", { class: "num" }, `${letters[i]}${i ? ` vs A` : ""}`)));
  const ask = btn("Ask the assistant why", async () => {
    ask.disabled = true; CMP.answer = "Thinking…"; renderCompare();
    try {
      const lines = R.summary.concat(R.settings.filter((r) => r.differs).map((r) => `${r.key} (${r.kind}): ${r.values.map((v, i) => `${letters[i]}=${v}`).join(", ")}`));
      CMP.answer = (await api("POST", "/api/assistant/ask", { question: "These launch configs differ only in the settings listed. What explains the change in speed and memory, and which should I keep?",
        lines, title: "Compared launch configs" })).answer;
    } catch (e) { CMP.answer = e.message; }
    renderCompare();
  }, "sm", "message");
  fill(el, pickers,
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, R.differences ? `What is different (${R.differences})` : "Nothing is different"),
      h("span", { class: "spacer" }), h("label", { class: "check" }, same, "show unchanged settings too")),
      R.summary.map((s) => h("p", { class: "hint", style: "margin:0 0 6px" }, s)),
      h("div", { class: "table-wrap" }, h("table", { class: "dense cmp-table" }, h("thead", {}, head), h("tbody", {}, setRows.length ? setRows : h("tr", {}, h("td", { colspan: 2 + R.items.length }, h("div", { class: "empty" }, "Same settings."))))))),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Results"), h("span", { class: "spacer" }), ask),
      h("p", { class: "hint" }, "Medians over every request and benchmark of each config. Green is better, red is worse, compared with A."),
      h("div", { class: "table-wrap" }, h("table", { class: "dense cmp-table" }, h("thead", {}, resHead), h("tbody", {}, resRows))),
      CMP.answer ? h("div", { class: "answer" }, markdown(CMP.answer)) : null),
    h("details", { class: "card" }, h("summary", { class: "strong" }, "Full launch commands"),
      R.items.map((it, i) => h("div", { style: "margin-top:8px" }, h("div", { class: "muted mono-sm" }, `${letters[i]} · ${it.model_id}`), h("pre", { class: "pre" }, it.command || "(not recorded)")))));
}

// ----- loads (sessions) --------------------------------------------------------------------
async function loadInvSessions() {
  try {
    const qs = new URLSearchParams({ days: IV.days, ...(IV.model ? { model: IV.model } : {}) });
    IV.sessions = (await api("GET", `/api/inventory/sessions?${qs}`)).sessions;
    renderInvSessions();
  } catch (e) { fail(e); }
}
function renderInvSessions() {
  const el = $("#ivSessions"), qs = new URLSearchParams({ days: IV.days, ...(IV.model ? { model: IV.model } : {}) });
  const range = h("select", { style: "width:auto" }, [[1, "Last day"], [7, "Last 7 days"], [30, "Last 30 days"], [90, "Last 90 days"], [365, "Last year"]].map(([v, l]) => h("option", { value: v }, l)));
  range.value = String(IV.days);
  range.addEventListener("change", () => { IV.days = Number(range.value); loadInvSessions(); });
  const models = h("select", { style: "width:auto; max-width:260px" }, [h("option", { value: "" }, "All models"), ...(IV.data ? IV.data.models.filter((m) => m.sessions).map((m) => h("option", { value: m.model_id }, m.name)) : [])]);
  models.value = IV.model;
  models.addEventListener("change", () => { IV.model = models.value; loadInvSessions(); });
  const table = h("table", { class: "dense" });
  const settings = (s) => chips([s.quant, s.ctx ? `ctx ${num(s.ctx, 0)}` : null, s.cache_k || s.cache_v ? `kv ${s.cache_k || "f16"}/${s.cache_v || "f16"}` : null,
    s.parallel ? `np ${s.parallel}` : null, s.split_mode ? `sm ${s.split_mode}` : null, s.spec_type || null].filter(Boolean));
  const gpus = (s) => (s.gpu_stats.length ? h("div", { class: "stack", style: "gap:0" }, s.gpu_stats.map((g) => h("span", { class: "mono-sm" },
    `GPU${g.gpu} ${num(g.util_avg, 0)}% · ${num((g.mem_max_mb || 0) / 1024, 1)} GiB`))) : DASH);
  renderTable(table, [
    { key: "t", label: "Started", sort: (s) => s.started_at, cell: (s) => h("td", { title: new Date(s.started_at * 1000).toLocaleString() }, ago(s.started_at), s.observed_running ? " ≈" : "") },
    { key: "d", label: "For", num: true, sort: (s) => s.duration_s, cell: (s) => (s.open ? h("span", { class: "pill ok" }, dur(s.duration_s)) : dur(s.duration_s)) },
    { key: "m", label: "Model", sort: (s) => s.model_id, cell: (s) => modelCell(s.model_id, [s.gpus.length ? `GPU ${s.gpus.join(",")}` : null,
      s.measured_gib || s.size_gib ? `${num(s.measured_gib || s.size_gib)} GiB` : null].filter(Boolean).join(" · ")) },
    { label: "Settings", cell: settings },
    { label: "GPU busy · VRAM peak", cell: gpus },
    { key: "r", label: "Requests", num: true, sort: (s) => s.requests.requests, cell: (s) => (s.requests.requests ? `${s.requests.requests} · ${num(s.requests.tg_avg)} t/s` : DASH) },
  ], IV.sessions, IV.sortS, "No loads recorded in this range. Loads are recorded while the engine is running.",
  { onRow: (s) => openModel(s.model_id) });
  fill(el, h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, `Loads (${IV.sessions.length})`), h("span", { class: "spacer" }), models, range,
    h("a", { class: "btn sm", href: `/api/inventory/sessions.csv?${qs}`, download: "model-sessions.csv" }, icon("download"), "CSV")),
    h("p", { class: "hint" }, "One row each time a model was loaded. GPU busy and VRAM peak are per card, from the GPU history while it was loaded. ≈ = it was already running when first seen."),
    h("div", { class: "table-wrap" }, table)));
}

// ----- new & unknown -----------------------------------------------------------------------
async function loadInvReview() {
  try {
    IV.traits = (await api("GET", "/api/inventory/traits")).traits;
    renderInvReview();
  } catch (e) { fail(e); }
}
async function setTrait(t, body, msg) {
  try {
    await api("PUT", `/api/inventory/traits/${t.id}`, body);
    toast(msg, "ok");
    IV.suggestions = IV.suggestions && IV.suggestions.filter((s) => s.trait_id !== t.id);
    await loadInventory();
  } catch (e) { fail(e); }
}
async function askAssistant() {
  IV.busy = true; renderInvReview();
  try {
    const r = await api("POST", "/api/inventory/assist");
    IV.suggestions = r.suggestions; IV.assistNote = r.assistant;
  } catch (e) { fail(e); } finally { IV.busy = false; renderInvReview(); }
}
async function applySuggestion(s) {
  const t = IV.traits.find((x) => x.id === s.trait_id);
  if (!t) return;
  const body = s.action === "alias" ? { status: "alias", canonical: s.canonical } : { status: s.action === "ignore" ? "ignored" : "known" };
  await setTrait(t, body, `${t.kind} ${t.value}: ${body.status}${s.canonical ? ` → ${s.canonical}` : ""}`);
}
function renderInvReview() {
  const el = $("#ivReview"), all = IV.traits;
  const pending = all.filter((t) => t.status === "new");
  const shown = IV.showAll ? all : pending;
  const table = h("table");
  renderTable(table, [
    { label: "Kind", cell: (t) => h("span", { class: "chip" }, t.kind.replace("_", " ")) },
    { label: "Value", cell: (t) => h("span", {}, t.value, t.status === "alias" ? ` → ${t.canonical}` : "") },
    { label: "Status", cell: (t) => h("span", { class: `pill ${t.status === "new" ? "warn" : t.status === "known" ? "ok" : ""}` }, t.status) },
    { label: "Uses", num: true, cell: (t) => t.count },
    { label: "First seen", cell: (t) => ago(t.first_seen) },
    { label: "Where", cell: (t) => (t.example ? h("span", { class: "muted mono-sm" }, t.example) : DASH) },
    { label: "", cell: (t) => {
      const others = all.filter((o) => o.kind === t.kind && o.id !== t.id && o.status !== "alias" && o.status !== "ignored");
      const pick = h("select", { style: "width:auto; max-width:160px", title: "fold this into another value" }, [h("option", { value: "" }, "same as…"), ...others.map((o) => h("option", { value: o.value }, o.value))]);
      pick.addEventListener("change", () => pick.value && setTrait(t, { status: "alias", canonical: pick.value }, `${t.value} → ${pick.value}`));
      return h("td", {}, h("div", { class: "row end nowrap" }, others.length ? pick : null,
        t.status !== "known" ? btn("Known", () => setTrait(t, { status: "known" }, `${t.value}: known`), "sm") : null,
        t.status !== "ignored" ? btn("Ignore", () => setTrait(t, { status: "ignored" }, `${t.value}: ignored`), "sm ghost") : null));
    } },
  ], shown, { key: null }, IV.showAll ? "Nothing recorded yet." : "Nothing waiting for review.");
  const sug = IV.suggestions && IV.suggestions.length ? h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Suggestions"), h("span", { class: "spacer" }),
      h("span", { class: "hint" }, IV.assistNote && IV.assistNote !== "ok" ? `assistant: ${IV.assistNote}` : "checked before they are shown; nothing changes until you apply"),
      btn("Apply all", async () => { for (const s of IV.suggestions.slice()) await applySuggestion(s); }, "sm primary")),
    h("div", { class: "table-wrap" }, simpleTable(["Value", "Suggestion", "Why", "From", ""], IV.suggestions.map((s) => [
      `${s.kind.replace("_", " ")}: ${s.value}`, s.action === "alias" ? `same as ${s.canonical}` : s.action === "ignore" ? "ignore" : "mark known", s.reason || DASH,
      h("span", { class: `pill ${s.source === "assistant" ? "accent" : ""}` }, s.source), btn("Apply", () => applySuggestion(s), "sm")])))) : null;
  const only = h("input", { type: "checkbox", checked: IV.showAll });
  only.addEventListener("change", () => { IV.showAll = only.checked; renderInvReview(); });
  fill(el, sug, h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, IV.showAll ? `Everything seen (${all.length})` : `Waiting for review (${pending.length})`), h("span", { class: "spacer" }),
      h("label", { class: "check" }, only, "show all"),
      btn(IV.busy ? "Asking…" : "Ask the assistant", askAssistant, "sm", "message")),
    h("p", { class: "hint" }, "The first time a quant, cache type, split mode, speculative type, load mode, flag or env var appears it is logged here. Familiar ones are marked known on their own; the rest wait. A new value never breaks anything: it is stored as text, along with the whole command."),
    h("div", { class: "table-wrap" }, table)));
  if (IV.busy) $$("button", el).forEach((b) => { if (b.textContent === "Asking…") b.disabled = true; });
}

// ----- host --------------------------------------------------------------------------------
function renderInvHost() {
  const el = $("#ivHost");
  if (!IV.data) return fill(el, h("div", { class: "empty" }, "Loading…"));
  const H = IV.data.host, app = H.app && H.app.data, ls = H["engine"] && H["engine"].data;
  const kv = (rows) => kvTable(rows.filter((r) => r[1] != null && r[1] !== ""));
  const accel = ls && ls.hardware && ls.hardware.accelerators ? ls.hardware.accelerators : [];
  const hw = ls && ls.hardware;
  fill(el,
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "This app"), h("span", { class: "spacer" }),
        h("span", { class: "hint" }, `${IV.data.host_changes.app || 0} change(s) recorded`)),
      app ? kv([["Host", app.hostname], ["In a container", app.container ? "yes" : "no"], ["Platform", app.platform], ["Python", app.python], ["CPUs", app.cpus], ["Memory", app.mem_gib ? `${app.mem_gib} GiB` : ""]])
        : h("div", { class: "empty" }, "Press Scan to record this host.")),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Engine"), h("span", { class: "spacer" }),
        h("span", { class: "hint" }, `${IV.data.host_changes["engine"] || 0} change(s) recorded`)),
      ls ? h("div", { class: "stack" },
        kv([["Version", ls.version && ls.version.version], ["Commit", ls.version && ls.version.commit], ["Built", ls.version && ls.version.build_date],
          ["OS", hw && hw.operating_system && `${hw.operating_system.name || hw.operating_system.family} ${hw.operating_system.version || ""}`],
          ["Environment", hw && hw.environment && `${hw.environment.kind}${hw.environment.name ? ` (${hw.environment.name})` : ""}`],
          ["CPU", hw && hw.cpu && `${hw.cpu.model || ""} · ${hw.cpu.logical_thread_count || "?"} threads`],
          ["Memory", hw && hw.memory && hw.memory.capacity_bytes ? `${num(hw.memory.capacity_bytes / 2 ** 30, 0)} GiB` : ""]]),
        accel.length ? simpleTable(["GPU", "Model", "Architecture", "Memory", "Driver", "Power limit"], accel.map((a) => [a.index, a.model || a.kind, a.architecture || DASH,
          a.memory && a.memory.capacity_bytes ? `${num(a.memory.capacity_bytes / 2 ** 30, 1)} GiB` : DASH, a.driver ? `${a.driver.name || ""} ${a.driver.version || ""}` : DASH, a.power_limit_watts ? `${a.power_limit_watts} W` : DASH])) : null,
        h("details", {}, h("summary", { class: "muted" }, "raw snapshot"), h("pre", { class: "pre" }, JSON.stringify(ls, null, 2))))
        : h("div", { class: "empty" }, "No engine facts yet. Press Scan once the engine is running.")));
}

// ── Schedule ─────────────────────────────────────────────────
// The plan is made here and applied to the engine's pool router; Live shows what the router is doing.
const SC = {
  pane: "live", data: null, live: null,
  plan: null, sizes: {}, persistent: new Set(), dirty: false,   // the Plan form, kept until Save
  filter: "", onlyProfiles: false, sort: { key: null, asc: true },
  steps: [{ op: "request", model: "" }], sim: null, simAt: 0,
};

function segColor(id) {
  let x = 0;
  for (const ch of id) x = (x * 31 + ch.charCodeAt(0)) >>> 0;
  return `hsl(${x % 360} 55% 58%)`;
}
// "20 s" below two minutes, else "10 min": rounding 20 s to "0 min" read as no protection at all
const span = (s) => (s < 120 ? `${num(s, 0)} s` : `${num(s / 60, 0)} min`);
const gib = (v) => (v == null ? DASH : `${num(v, 1)} GiB`);
const statePill = (s) => h("span", { class: `pill ${s === "ready" ? "ok" : s === "starting" || s === "stopping" ? "warn" : ""}` }, s);
const resultPill = (r) => h("span", { class: `pill ${["loaded", "served", "finished", "unloaded"].includes(r) ? "ok" : r === "queued" ? "warn" : r === "error" ? "bad" : ""}` }, r);

/** One bar per GPU: a segment per model, sized by the VRAM it holds there. cards: [{id, cap_gib, used_gib, models}] */
function cardBars(cards, usage, real = {}) {
  return h("div", { class: "gpu-cards" }, cards.map((c) => {
    const parts = c.models.map((m) => ({ id: m, gib: usage(m, c) })).filter((p) => p.gib > 0);
    return h("div", { class: "gpu-card" },
      h("div", { class: "head" }, h("b", {}, c.id === "pool" ? "Pool" : `GPU ${c.id}`),
        h("span", { title: "planned / capacity" }, `${num(c.used_gib)} / ${num(c.cap_gib)} GiB`,
          real && real[c.id] != null ? h("small", { class: "muted", title: "VRAM the GPU reports in use now" }, ` · in use ${num(real[c.id])}`) : null)),
      h("div", { class: "vbar", title: `${num(c.free_gib ?? Math.max(c.cap_gib - c.used_gib, 0))} GiB free` },
        parts.map((p) => h("i", { style: `width:${Math.min(100, (p.gib / c.cap_gib) * 100)}%; background:${segColor(p.id)}`, title: `${p.id}: ${num(p.gib)} GiB` }))),
      parts.length ? h("div", { class: "vbar-legend" }, parts.map((p) => h("span", { style: `--c:${segColor(p.id)}` }, `${p.id} ${num(p.gib)}`)))
        : h("div", { class: "hint", style: "margin:8px 0 0" }, "empty"));
  }));
}

// ----- data ------------------------------------------------------------------------------
async function loadSchedule() {
  try {
    SC.data = await api("GET", "/api/schedule");
    if (!SC.dirty) syncPlanForm();
    renderSchedule();
  } catch (e) { fail(e); }
}
function syncPlanForm() {
  const p = SC.data.plan;
  SC.plan = { enabled: p.enabled, cards: p.cards.join(", "), reserve_gib: p.reserve_gib, protect_seconds: p.protect_seconds,
    placement: p.placement, idle_unload_min: p.idle_unload_min ?? 30 };
  SC.sizes = { ...p.sizes };
  SC.persistent = new Set(p.persistent);
}
const planBody = () => ({ ...SC.plan, persistent: [...SC.persistent], sizes: SC.sizes });

async function loadLive() {
  try { SC.live = await api("GET", "/api/schedule/live"); } catch (e) { SC.live = { connected: false, error: e.message, pool: null }; }
  if (S.view === "schedule" && SC.pane === "live") renderLive();
  refreshScheduleNav();
}
function refreshScheduleNav() {
  const P = SC.live && SC.live.connected && SC.live.pool;
  fill($("#navSchedule"), P && P.enabled ? h("span", { class: "dot on", title: "the pool router is running" }) : null);
}
async function savePlan(msg = "Saved the plan.") {
  try {
    SC.data = await api("PUT", "/api/schedule", planBody());
    SC.dirty = false;
    syncPlanForm();
    renderSchedule();
    toast(msg, "ok");
  } catch (e) { fail(e); }
}

function renderSchedule() {
  if (!SC.data) return;
  renderPlan();
  renderSim();
}

// ----- live ------------------------------------------------------------------------------
function renderLive() {
  const el = $("#scLive"), L = SC.live;
  if (!L) return fill(el, h("div", { class: "empty" }, "Loading…"));
  if (!L.connected) {
    return fill(el, h("div", { class: "card" }, h("div", { class: "empty" }, h("b", {}, "Not connected."), h("br"), L.error || "",
      h("br"), "This view reads the engine's /api/pool once it is running.")));
  }
  const P = L.pool;
  if (!P.enabled) {
    return fill(el, h("div", { class: "card" }, h("div", { class: "empty" }, h("b", {}, `The engine is using the “${P.router || "group"}” router.`), h("br"),
      "Turn scheduling on in the Plan tab. The engine switches to the pool router when it reloads.")));
  }
  const byId = Object.fromEntries(P.models.map((m) => [m.id, m]));
  const running = P.models.filter((m) => m.state !== "stopped");
  // a model with no card of its own spreads over every card (llama.cpp's default split), as the engine counts it
  const usage = (id, c) => { const m = byId[id]; return m && m.size_gib ? m.size_gib / Math.max(m.cards.length || P.cards.length, 1) : c.cap_gib; };
  const cards = P.cards.length ? P.cards : [{ id: "pool", cap_gib: P.budget_gib, used_gib: running.reduce((a, m) => a + m.size_gib, 0), models: running.map((m) => m.id) }];
  const rows = P.models.slice().sort((a, b) => (a.state === "stopped") - (b.state === "stopped") || a.id.localeCompare(b.id));
  const table = h("table");
  renderTable(table, [
    { label: "Model", cell: (m) => h("td", { class: "cell-model", title: m.id }, h("div", { class: "cell-main" }, h("span", { class: "swatch", style: `--c:${segColor(m.id)}` }), m.id)) },
    { label: "State", cell: (m) => statePill(m.state) },
    { label: "GPU", cell: (m) => m.cards.length ? m.cards.join(", ") : DASH },
    { label: "Size", num: true, cell: (m) => m.size_known ? gib(m.size_gib) : h("span", { class: "pill warn", title: "no size: this model runs alone" }, "unknown") },
    { label: "In flight", num: true, cell: (m) => m.in_flight || DASH },
    { label: "Last used", cell: (m) => (m.last_used ? ago(Date.parse(m.last_used) / 1000) : DASH) },
    { label: "Load time", num: true, cell: (m) => (m.load_seconds ? `${num(m.load_seconds, 0)}s` : DASH) },
    { label: "", cell: (m) => h("div", { class: "chips" }, m.persistent && h("span", { class: "chip" }, "kept loaded"), m.protected && h("span", { class: "chip", title: `Used in the last ${span(P.protect_seconds)}, so it is evicted last when another model needs room. Not a setting: it ends on its own once the model sits idle.` }, "recently used")) },
    { label: "", cell: (m) => { const b = h("button", { class: "icon-btn ghost", title: "Load, unload, keep loaded, logs, edit", "aria-label": `Actions for ${m.id}` }, "⋯");
      b.addEventListener("click", () => liveMenu(b, m)); return h("td", { class: "actions" }, b); } },
  ], rows, { key: null }, "No models.");
  const swaps = P.swaps.length ? h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Loading")),
    h("div", { class: "table-wrap" }, simpleTable(["Model", "Evicting", "Waiting requests", "Elapsed"],
      P.swaps.map((s) => [s.model, s.evict.length ? s.evict.join(", ") : DASH, s.waiters, `${num(s.elapsed_seconds, 0)}s`])))) : null;
  const queue = P.queue.length ? h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Queue"),
    h("span", { class: "spacer" }), h("span", { class: "hint" }, "waiting for room, or for a busy model to finish")),
    h("div", { class: "table-wrap" }, simpleTable(["#", "Model", "Waited"], P.queue.map((q) => [q.position, q.model, `${num(q.waited_seconds, 0)}s`])))) : null;
  // what the scheduler decided, newest first: each load says which models it pushed out and why
  const feed = (L.activity || []).filter((a) => !/^applied the scheduling plan/.test(a.message)).slice(0, 12);
  const activity = h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "What the scheduler did"),
    h("span", { class: "spacer" }), h("span", { class: "hint" }, "newest first · loads, evictions, measured sizes")),
    feed.length ? h("div", { class: "table-wrap" }, simpleTable(["When", "What happened"],
      feed.map((a) => [ago(a.ts), h("span", { class: a.level === "info" ? "" : `pill ${a.level === "error" ? "bad" : "warn"}` }, a.message)])))
      : h("div", { class: "empty" }, "Nothing yet. Send a request to a model that is not loaded and the decision shows up here."));
  fill(el,
    h("div", { class: "status-line" }, h("span", { class: "pill ok" }, "pool router"),
      h("span", { class: `pill ${P.placement ? "ok" : "warn"}`, title: P.placement ? "each model is placed on a card" : "no per-card sizes: planning by total VRAM" }, P.placement ? "placement on" : "placement off"),
      h("span", { class: "muted" }, `${num(P.budget_gib)} GiB usable · models used in the last ${span(P.protect_seconds)} are evicted last`),
      h("span", { class: "spacer" }), h("span", { class: "hint", style: "margin:0" }, `updated ${ago(L.at)}`)),
    cardBars(cards, usage, L.real_gib),
    h("p", { class: "hint", style: "margin:-4px 0 12px" }, "Bars are the planned sizes the scheduler fits models by; “in use” is what the GPU reports. After a model loads once, Argos measures its real size and the plan uses that."),
    swaps, queue, activity,
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Models")), h("div", { class: "table-wrap" }, table)));
}
function kvTable(rows) {
  return h("table", {}, h("tbody", {}, rows.map(([k, v]) => h("tr", {}, h("td", { class: "muted", style: "width:34%" }, k), h("td", {}, v)))));
}
function simpleTable(head, rows) {
  return h("table", {}, h("thead", {}, h("tr", {}, head.map((x) => h("th", {}, x)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, c))))));
}

// ----- plan ------------------------------------------------------------------------------
function renderPlan() {
  const D = SC.data, P = SC.plan, el = $("#scPlan");
  const touch = () => { SC.dirty = true; };
  const field = (label, key, attrs = {}, hint = "") => {
    const input = h("input", { type: "text", value: P[key], spellcheck: "false", ...attrs });
    input.addEventListener("input", () => { P[key] = input.value; touch(); });
    return h("label", { class: "field" }, h("span", {}, label), input, hint && h("span", { class: "muted mono-sm" }, hint));
  };
  const enable = h("input", { type: "checkbox", checked: P.enabled });
  enable.addEventListener("change", () => { P.enabled = enable.checked; touch(); });
  const placement = h("select", {}, [["auto", "auto — choose a card for each model"], ["off", "off — plan by total VRAM only"]].map(([v, l]) => h("option", { value: v }, l)));
  placement.value = P.placement;
  placement.addEventListener("change", () => { P.placement = placement.value; touch(); });
  const cardsInput = field("Cards (GiB each)", "cards", { placeholder: "32, 32" }, "numbered from 0 like HIP_VISIBLE_DEVICES; blank = the engine detects them");
  const detected = D.detected_cards;
  const useDetected = detected.length ? btn(`Use detected (${detected.map((g) => num(g.total_gib, 1)).join(", ")})`, () => {
    P.cards = detected.map((g) => num(g.total_gib, 1).replace(",", "")).join(", ");
    touch(); renderPlan();
  }, "sm") : null;

  const rows = D.rows.filter((r) => (!SC.onlyProfiles || r.kind === "profile") &&
    (!SC.filter || `${r.id} ${r.name}`.toLowerCase().includes(SC.filter.toLowerCase())));
  const table = h("table");
  const sizeInput = (r) => {
    const input = h("input", { type: "number", class: "size-input", min: "0", step: "0.5", value: SC.sizes[r.id] ?? "", placeholder: r.effective_gib ? String(r.effective_gib) : "?", title: "blank = use the measurement, or the estimate" });
    input.addEventListener("input", () => { const v = parseFloat(input.value); if (v > 0) SC.sizes[r.id] = v; else delete SC.sizes[r.id]; touch(); });
    return h("td", { class: "num" }, input);
  };
  renderTable(table, [
    { key: "name", label: "Model", sort: (r) => r.id, cell: (r) => h("td", { class: "cell-model", title: r.id }, h("span", { class: "swatch", style: `--c:${segColor(r.id)}` }), r.id,
      r.known === false ? h("span", { class: "pill warn", style: "margin-left:6px", title: "This model is in your files or profiles but the engine has not picked it up yet (it reads its model list from a config file and reloads about every 2 seconds), so the plan skips it until it appears." }, "engine has not loaded its config yet") : null) },
    { key: "kind", label: "Type", sort: (r) => r.kind, cell: (r) => h("span", { class: "chip" }, r.kind === "profile" ? "profile" : r.model_kind) },
    { key: "ctx", label: "Context", num: true, cell: (r) => (r.ctx ? num(r.ctx, 0) : DASH) },
    { key: "est", label: "Estimate", num: true, sort: (r) => r.estimate && r.estimate.gib, cell: (r) => r.estimate
      ? h("span", { title: `weights ${r.estimate.weights_gib} + KV cache ${r.estimate.kv_gib} + buffers ${r.estimate.overhead_gib}` + (r.estimate.notes.length ? ` — ${r.estimate.notes.join("; ")}` : "") }, gib(r.estimate.gib)) : DASH },
    { key: "meas", label: "Measured", num: true, sort: (r) => r.measured && r.measured.gib, cell: (r) => r.measured
      ? h("span", { title: `measured ${ago(r.measured.at)}${r.measured.load_seconds ? `, loaded in ${num(r.measured.load_seconds, 0)}s` : ""}` }, gib(r.measured.gib)) : DASH },
    { label: "Size (GiB)", num: true, cell: sizeInput },
    { key: "eff", label: "Used", num: true, sort: (r) => r.effective_gib, cell: (r) => r.effective_gib
      ? h("span", {}, gib(r.effective_gib), " ", h("span", { class: `pill ${r.source === "measured" ? "ok" : ""}`, title: { plan: "set by hand", measured: "from a measurement", estimate: "estimated from the file" }[r.source] }, r.source))
      : h("span", { class: "pill warn", title: "no size: this model runs alone" }, "unknown") },
    { label: "Keep loaded", cell: (r) => { const c = h("input", { type: "checkbox", checked: SC.persistent.has(r.id), title: "never evicted to make room for another model" });
      c.addEventListener("change", () => { c.checked ? SC.persistent.add(r.id) : SC.persistent.delete(r.id); touch(); }); return h("td", {}, c); } },
    { label: "", cell: (r) => h("td", {}, h("div", { class: "row end" },
      r.measured && r.measured.gib !== SC.sizes[r.id] ? btn("Use measured", async () => { try { SC.data = await api("POST", `/api/schedule/measured/${encodeURIComponent(r.id)}/use`); syncPlanForm(); SC.dirty = false; renderSchedule(); } catch (e) { fail(e); } }, "sm ghost") : null,
      btn("Measure", () => startMeasure(r), "sm", "gauge"))) },
  ], rows, SC.sort, "No models yet. Scan the Models folder or save a profile first.");

  const search = h("input", { type: "search", placeholder: "Filter models…", value: SC.filter, style: "width:220px" });
  search.addEventListener("input", debounce(() => { SC.filter = search.value; renderPlan(); }, 150));
  const only = h("input", { type: "checkbox", checked: SC.onlyProfiles });
  only.addEventListener("change", () => { SC.onlyProfiles = only.checked; renderPlan(); });

  fill(el,
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h2", {}, "Rules the scheduler follows"), h("span", { class: "spacer" }),
        SC.dirty ? h("span", { class: "pill warn" }, "unsaved changes") : appliedPill(D), btn("Save plan", () => savePlan(), "sm primary", "save")),
      h("p", { class: "hint" }, "What this page does: it sets the rules for how models share your GPUs. When a request needs a model that is not loaded, the scheduler loads it beside the others if the GPU has room. If not, it unloads the model that is cheapest to reload and has been idle longest, never one that is busy. The Live tab shows what it did; this tab is where you change the rules. Saving sends them to the engine."),
      h("div", { class: "fields" },
        h("label", { class: "check wide" }, enable, "Use the pool router (when off, the engine keeps its own router)"),
        cardsInput,
        field("Reserve (GiB)", "reserve_gib", { type: "number", step: "0.5", min: "0" }, "left free for KV growth and buffers"),
        field("Protect (seconds)", "protect_seconds", { type: "number", step: "30", min: "0" }, "a model used this recently is evicted last"),
        field("Unload after (min)", "idle_unload_min", { type: "number", step: "5", min: "0" }, "a model unused this long is unloaded; 0 = never"),
        h("label", { class: "field wide" }, h("span", {}, "Placement"), placement)),
      useDetected && h("div", { class: "row", style: "margin-top:8px" }, useDetected)),
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h2", {}, "How much VRAM each model needs"), h("span", { class: "spacer" }), search,
        h("label", { class: "check" }, only, "profiles only")),
      h("p", { class: "hint" }, "The scheduler decides what fits using these sizes. Estimate is worked out from the file, context length and cache type. Measured is what the GPU really used the last time the model loaded (it is recorded automatically, or with Measure) and replaces the estimate. Type a size in the box only to override both."),
      D.skipped.length ? h("p", { class: "hint" }, "Waiting for the engine to list ", D.skipped.join(", "), ". They join the plan once it does (a new profile appears within a minute).") : null,
      h("div", { class: "table-wrap" }, table)),
    h("details", { class: "card" }, h("summary", { class: "strong" }, "What the engine reads"),
      h("div", { class: "row", style: "margin:8px 0" }, h("span", { class: "hint", style: "flex:1;margin:0" }, D.known_from
        ? "Model ids are checked against the engine's live list." : "The engine is not answering, so model ids are not checked."), btn("Copy", () => copyText(D.fragment), "sm", "copy")),
      h("pre", { class: "pre" }, D.fragment)));
}
/** Whether the saved plan reached the engine. */
function appliedPill(D) {
  const a = D.applied || {};
  if (!a.connected) return h("span", { class: "pill warn", title: "Argos is running without its engine (outside Docker), so the plan only feeds the simulator." }, "no engine");
  if (a.error) return h("span", { class: "pill bad", title: a.error }, "not applied");
  return a.at ? h("span", { class: "pill ok", title: new Date(a.at * 1000).toLocaleString() }, `applied ${ago(a.at)}`) : h("span", { class: "pill" }, "saved");
}

// ----- measuring -------------------------------------------------------------------------
async function startMeasure(row) {
  let job;
  try { job = (await api("POST", "/api/schedule/measure", { id: row.id }, 0)).job; } catch (e) { return fail(e); }
  const msg = h("p", { style: "margin:0 0 10px" }, "Starting…");
  const bar = h("div", { class: "progress" }, h("i", { style: "width:5%" }));
  const note = h("p", { class: "hint", style: "margin:10px 0 0" },
    "Launches this command here, waits for it to load, and reads the GPU memory and llama.cpp's own buffer sizes. Other models loading at the same time add noise.");
  let stopped = false;
  const close = modal(`Measuring ${row.id}`, h("div", {}, msg, bar, note), [
    { label: "Cancel measurement", cls: "danger", run: async () => { try { await api("POST", `/api/schedule/measure/${job.job_id}/cancel`); } catch (e) { fail(e); } } },
    { label: "Close", run: (c) => c() },
  ], () => { stopped = true; });
  const pct = { queued: 5, baseline: 10, loading: 45, settling: 85, done: 100, error: 100, cancelled: 100 };
  while (!stopped) {
    await new Promise((r) => setTimeout(r, 1000));
    let j;
    try { j = (await api("GET", `/api/schedule/measure/${job.job_id}`)).job; } catch (e) { msg.textContent = e.message; break; }
    msg.textContent = j.message;
    bar.firstChild.style.width = `${pct[j.state] || 50}%`;
    if (["done", "error", "cancelled"].includes(j.state)) {
      if (j.state === "done") {
        const r = j.result;
        msg.textContent = `${num(r.gib)} GiB (from the ${r.source === "gpu" ? "GPU memory" : "launch log"}), loaded in ${num(r.load_seconds, 0)}s` +
          (r.per_card.length ? ` · per GPU: ${r.per_card.map((x) => num(x)).join(" / ")} GiB` : "");
        loadSchedule();
      }
      if (j.state !== "done") bar.firstChild.style.background = "var(--danger)";
      break;
    }
  }
  if (stopped) close();
}

// ----- simulate --------------------------------------------------------------------------
function renderSim() {
  const D = SC.data, el = $("#scSim");
  const ids = D.rows.map((r) => r.id);
  const steps = h("div", { class: "steps" }, SC.steps.map((s, i) => {
    const op = h("select", {}, [["request", "a request arrives for"], ["finish", "a request finishes on"], ["unload", "unload"], ["wait", "wait (minutes)"]].map(([v, l]) => h("option", { value: v }, l)));
    op.value = s.op;
    op.addEventListener("change", () => { s.op = op.value; renderSim(); });
    const model = h("input", { type: "text", class: "model", list: "scModels", value: s.model || "", placeholder: "model id", spellcheck: "false" });
    model.addEventListener("input", () => { s.model = model.value; });
    const mins = h("input", { type: "number", min: "0", step: "1", value: s.minutes ?? 10 });
    mins.addEventListener("input", () => { s.minutes = parseFloat(mins.value) || 0; });
    return h("div", { class: "step" }, h("span", { class: "n" }, `${i + 1}.`), op, s.op === "wait" ? mins : model,
      iconBtn("x", "Remove step", () => { SC.steps.splice(i, 1); if (!SC.steps.length) SC.steps.push({ op: "request", model: "" }); renderSim(); }, "ghost"));
  }));
  const addExample = () => {
    const run = D.rows.filter((r) => r.effective_gib > 0).sort((a, b) => b.effective_gib - a.effective_gib).slice(0, 3).map((r) => r.id);
    if (!run.length) return toast("Give some models a size first.", "err");
    SC.steps = [...run.map((m) => ({ op: "request", model: m })), ...run.map((m) => ({ op: "finish", model: m }))];
    SC.sim = null; renderSim();
  };
  fill(el,
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h2", {}, "Try the plan"), h("span", { class: "spacer" }),
        btn("Example", addExample, "sm ghost"), btn("Add step", () => { SC.steps.push({ op: "request", model: "" }); renderSim(); }, "sm", "plus"),
        btn("Run", runSim, "sm primary", "play")),
      h("p", { class: "hint" }, "Runs the steps through the same rules the pool router uses, with the Plan tab as it is now (saved or not). Loading takes the measured load time, or one second per GiB."),
      h("datalist", { id: "scModels" }, ids.map((m) => h("option", { value: m }))),
      steps),
    SC.sim ? renderSimResult() : null);
}
async function runSim() {
  const P = SC.plan;
  const clean = SC.steps.filter((s) => s.op === "wait" || (s.model || "").trim());
  if (!clean.length) return toast("Add a model to a step first.", "err");
  try {
    SC.sim = await api("POST", "/api/schedule/simulate", { steps: clean.map((s) => ({ op: s.op, model: (s.model || "").trim(), minutes: s.minutes ?? 10 })),
      plan: { cards: P.cards, reserve_gib: P.reserve_gib, protect_seconds: P.protect_seconds, idle_unload_min: P.idle_unload_min, placement: P.placement, persistent: [...SC.persistent] }, sizes: SC.sizes });
    SC.simAt = SC.sim.results.length - 1;
    renderSim();
  } catch (e) { fail(e); }
}
function renderSimResult() {
  const R = SC.sim.results, at = R[Math.min(SC.simAt, R.length - 1)], st = at.state;
  const table = h("table");
  renderTable(table, [
    { label: "#", num: true, cell: (r) => r.step },
    { label: "Step", cell: (r) => (r.op === "wait" ? r.note : `${r.op} ${r.model}`) },
    { label: "Result", cell: (r) => resultPill(r.result) },
    { label: "GPU", cell: (r) => (r.cards && r.cards.length ? r.cards.join(", ") : DASH) },
    { label: "What happened", cell: (r) => [r.op === "wait" ? "" : r.note, ...(r.drained || []).map((d) => ` · then ${d}`)].join("") },
  ], R, { key: null }, "", { onRow: (r) => { SC.simAt = R.indexOf(r); renderSim(); }, rowClass: (r) => (r === at ? "sel" : "") });
  const sized = Object.fromEntries(st.running.map((r) => [r.id, r]));
  const sizes = (id) => (SC.data.rows.find((r) => r.id === id) || {}).effective_gib || SC.sizes[id] || 0;
  const cards = SC.sim.placement ? st.cards : [{ id: "pool", cap_gib: st.budget_gib, used_gib: st.used_gib, models: st.running.map((r) => r.id) }];
  const usage = (id, c) => (SC.sim.placement ? (sizes(id) || c.cap_gib) / Math.max((sized[id] && sized[id].cards.length) || 1, 1) : sizes(id));
  return h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, `After step ${at.step}`), h("span", { class: "spacer" }),
      h("span", { class: "hint" }, `clock ${num(st.clock_s / 60, 1)} min · click a row to look at another step`)),
    cardBars(cards, usage),
    st.queue.length ? h("p", { class: "hint" }, `Waiting: ${st.queue.join(", ")}`) : null,
    h("div", { class: "table-wrap" }, table));
}

/** The ⋯ menu on a Live row. */
/** After "Load now": say when the model is ready, or that it never started (the request itself only starts the load). */
async function watchLoad(id) {
  const t0 = Date.now();
  while (Date.now() - t0 < 300000) {
    await new Promise((r) => setTimeout(r, 1500));
    try {
      const L = await api("GET", "/api/schedule/live");
      const m = L.pool && L.pool.models.find((x) => x.id === id);
      if (!m) return;
      if (m.state === "ready") { toast(`${id} is ready (${num((Date.now() - t0) / 1000, 0)}s).`, "ok"); loadLive(); return; }
      if (m.state === "stopped" && Date.now() - t0 > 8000) { toast(`${id} did not start. Check its log: ⋯ → Check logs.`, "err"); loadLive(); return; }
    } catch { return; }
  }
}
function liveMenu(anchor, m) {
  const loaded = m.state === "ready" || m.state === "starting";
  const act = async (action, body, msg) => {
    try {
      const r = await api("POST", `/api/schedule/live/${encodeURIComponent(m.id)}/${action}`, body);
      toast(r.already ? r.message : msg, r.already ? "" : "ok");
      if (action === "load" && !r.already) watchLoad(m.id);
    } catch (e) { fail(e); }
    setTimeout(loadLive, action === "load" ? 1500 : 300);
    if (action === "keep") loadSchedule();
  };
  popMenu(anchor, [
    loaded ? { label: "Unload model", icon: "square", run: () => act("unload", undefined, `Unloaded ${m.id}.`) }
      : { label: "Load now", icon: "play", run: () => act("load", undefined, `Loading ${m.id}…`) },
    m.persistent ? { label: "Keep loaded: off (may be unloaded for others)", icon: "x", run: () => act("keep", { on: false }, `${m.id} can be unloaded again.`) }
      : { label: "Keep loaded: on (never unload it for others)", icon: "check", run: () => act("keep", { on: true }, `${m.id} stays loaded.`) },
    { label: "Check logs", icon: "file-text", run: () => liveLog(m.id) },
    { label: "Edit launch command", icon: "pencil", run: () => liveEdit(m.id) },
  ]);
}
async function liveLog(id) {
  const pre = h("pre", { class: "log", style: "max-height:65dvh" }, "Reading…");
  const read = async () => {
    try { const r = await api("GET", `/api/schedule/live/${encodeURIComponent(id)}/log`); pre.textContent = r.log || "(nothing logged yet)"; pre.scrollTop = pre.scrollHeight; }
    catch (e) { pre.textContent = e.message; }
  };
  modal(`Log · ${id}`, pre, [{ label: "Refresh", icon: "refresh", run: () => read() }, { label: "Close", run: (c) => c() }]);
  read();
}
async function liveEdit(id) {
  try {
    const r = await api("GET", `/api/schedule/live/${encodeURIComponent(id)}/command`);
    if (!(await leaveOk())) return;
    openCommand(r.command, { name: r.name || "", base_model: r.base_model || "", source: r.source === "profile" ? "profile" : r.source, profileId: r.profile_id });
    if (r.source !== "profile") toast(r.source === "prefab" ? "This is the library prefab. Save it as a profile to change how the engine starts it." : "Opened the running command. Save it as a profile to keep the change.", "");
  } catch (e) { fail(e); }
}

// ── Sticky notes & movable panels ────────────────────────────
// Every card can be dragged by its ⠿ grip; the order is saved per page on the server.
// Notes are one set for the whole app. An open note floats over every page and stays where you put it while you
// switch pages; minimizing sends it to the Notes menu in the top bar; deleting removes it for good.
const NOTE_COLORS = ["yellow", "blue", "green", "pink", "gray"];
const LAYOUT = { data: {}, drag: null, loaded: false };
const NOTES = { list: [], menu: null, z: 1 };
/** Raise a note above the others. (Moving the element in the DOM would cancel the click that is in progress.) */
const raiseNote = (card) => { card.style.zIndex = String(++NOTES.z); };
/** Keep a note on screen: a spot saved on a wider window is pulled back inside this one (the saved spot is kept). */
function fitNote(card) {
  const layer = noteLayer(), w = layer.clientWidth, hgt = layer.clientHeight;
  if (!w || !card.dataset.x) return;
  const x = Number(card.dataset.x), y = Number(card.dataset.y);
  card.style.left = `${Math.max(0, Math.min(x, w - Math.min(card.offsetWidth, w) - 4))}px`;
  card.style.top = `${Math.max(0, Math.min(y, Math.max(0, hgt - 60)))}px`;
}
window.addEventListener("resize", debounce(() => $$("#noteLayer .note").forEach(fitNote), 100));

async function loadNotes() {
  try { NOTES.list = (await api("GET", "/api/notes")).notes; } catch (e) { return; /* notes are optional */ }
  const layer = noteLayer();
  layer.replaceChildren();
  NOTES.list.filter((n) => n.is_open).forEach((n, i) => layer.append(noteCard(n, i)));
  noteChanged();
}
function noteLayer() {
  let layer = $("#noteLayer");
  if (!layer) { layer = h("div", { id: "noteLayer", class: "note-layer" }); $(".main").append(layer); }
  return layer;
}
/** Keep the local list, the menu badge and an open menu in step after any change. */
function noteChanged(n, removed = false) {
  if (n) {
    const i = NOTES.list.findIndex((x) => x.id === n.id);
    if (removed) { if (i >= 0) NOTES.list.splice(i, 1); }
    else if (i >= 0) NOTES.list[i] = n; else NOTES.list.push(n);
  }
  const hidden = NOTES.list.filter((x) => !x.is_open).length;
  $("#notesCount").textContent = hidden || "";
  $("#notesBtn").title = hidden ? `Notes: ${hidden} minimized` : "Notes";
  if (NOTES.menu) renderNotesMenu();
}
const noteName = (n) => n.title || (n.body || "").split("\n").find((l) => l.trim()) || "Untitled note";
async function putNote(id, fields) {
  const n = (await api("PUT", `/api/notes/${id}`, fields)).note;
  noteChanged(n);
  return n;
}
function noteCard(n, i = 0) {
  let current = n;
  const title = h("input", { type: "text", class: "note-title", value: n.title, placeholder: "Note" });
  const text = h("textarea", { class: "note-body", placeholder: "Write anything: what you tried, what to try next…" }, n.body);
  const meta = h("span", { class: "muted mono-sm" });
  const card = h("div", { class: `note note-${n.color}`, dataset: { note: n.id } });
  const layer = noteLayer();
  const box = () => layer.getBoundingClientRect();
  // default spot: stacked down the right edge
  const x0 = n.x != null ? n.x : Math.max(8, box().width - 300 - 24 - i * 18), y0 = n.y != null ? n.y : 12 + i * 28;
  Object.assign(card.style, { left: `${x0}px`, top: `${y0}px`, width: `${n.w || 280}px`, height: `${n.h || 200}px` });
  Object.assign(card.dataset, { x: x0, y: y0 });
  requestAnimationFrame(() => fitNote(card));
  const showMeta = () => { meta.textContent = `saved ${ago(current.updated_at)}`; };
  const put = async (fields) => { try { current = await putNote(n.id, fields); showMeta(); } catch (e) { fail(e); } };
  const save = debounce(() => put({ title: title.value, body: text.value }), 700);
  title.addEventListener("input", save);
  text.addEventListener("input", save);
  const colors = h("div", { class: "note-colors" }, NOTE_COLORS.map((c) => h("button", { class: `swatch-btn note-${c}`, title: c, "aria-label": `Color ${c}`,
    onclick: async () => { await put({ color: c }); card.className = `note note-${c}`; } })));
  const minimize = iconBtn("minus", "Minimize to the Notes menu", async () => {
    await put({ title: title.value, body: text.value, is_open: false });
    card.remove();
  }, "ghost");
  const del = iconBtn("trash", "Delete for good", async () => {
    if (!(await confirmBox("Delete this note for good? It cannot be brought back. Minimize it instead to keep it in the Notes menu."))) return;
    try { await api("DELETE", `/api/notes/${n.id}`); card.remove(); noteChanged(current, true); } catch (e) { fail(e); }
  }, "ghost danger");
  const head = h("div", { class: "note-head", title: "Drag to move" }, h("span", { class: "grip" }, "⠿"), title, colors, minimize, del);
  // the last note touched sits on top
  card.addEventListener("pointerdown", () => raiseNote(card), true);
  // drag by the head with pointer events (works with mouse, pen and touch)
  head.addEventListener("pointerdown", (e) => {
    if (e.target.closest("input, button")) return;
    e.preventDefault();
    const b = box(), startX = e.clientX, startY = e.clientY, left = card.offsetLeft, top = card.offsetTop;
    card.classList.add("moving");
    head.setPointerCapture(e.pointerId);
    const move = (ev) => {
      card.style.left = `${Math.min(Math.max(0, left + ev.clientX - startX), b.width - 80)}px`;
      card.style.top = `${Math.min(Math.max(0, top + ev.clientY - startY), b.height - 40)}px`;
    };
    const up = () => {
      head.removeEventListener("pointermove", move); head.removeEventListener("pointerup", up);
      card.classList.remove("moving");
      Object.assign(card.dataset, { x: card.offsetLeft, y: card.offsetTop });
      put({ x: card.offsetLeft, y: card.offsetTop });
    };
    head.addEventListener("pointermove", move); head.addEventListener("pointerup", up);
  });
  // remember a resize
  let lastSize = `${card.style.width}x${card.style.height}`;
  if (window.ResizeObserver) new ResizeObserver(debounce(() => {
    const size = `${card.offsetWidth}x${card.offsetHeight}`;
    if (size !== lastSize && card.isConnected) { lastSize = size; put({ w: card.offsetWidth, h: card.offsetHeight }); }
  }, 500)).observe(card);
  // Resize by an explicit corner grip rather than relying on the browser's
  // own (near-invisible) handle. Whatever it produces is persisted by the
  // ResizeObserver below.
  const gripEl = h("span", { class: "note-resize", title: "Drag to resize" });
  gripEl.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    raiseNote(card);
    const from = { x: e.clientX, y: e.clientY, w: card.offsetWidth, h: card.offsetHeight };
    const move = (ev) => {
      card.style.width = `${Math.max(200, from.w + (ev.clientX - from.x))}px`;
      card.style.height = `${Math.max(120, from.h + (ev.clientY - from.y))}px`;
    };
    const up = () => {
      gripEl.removeEventListener("pointermove", move);
      gripEl.removeEventListener("pointerup", up);
      put({ w: card.offsetWidth, h: card.offsetHeight });
    };
    gripEl.addEventListener("pointermove", move);
    gripEl.addEventListener("pointerup", up);
  });
  fill(card, head, text, h("div", { class: "note-foot" }, meta), gripEl);
  showMeta();
  return card;
}
/** Bring a minimized note out. Several can be open at once. */
async function openNote(id, focus = false) {
  const layer = noteLayer();
  const existing = layer.querySelector(`[data-note="${id}"]`);
  if (existing) { raiseNote(existing); return existing; }
  try {
    const n = await putNote(id, { is_open: true });
    const card = noteCard(n, layer.children.length);
    layer.append(card);
    if (focus) card.querySelector(".note-body").focus();
    return card;
  } catch (e) { fail(e); return null; }
}
async function addNote() {
  try {
    const n = (await api("POST", "/api/notes", { color: "yellow" })).note;
    noteChanged(n);
    const layer = noteLayer();
    const card = noteCard(n, layer.children.length);
    layer.append(card);
    card.querySelector(".note-body").focus();
  } catch (e) { fail(e); }
}
// The Notes menu: every note, open or minimized. Open brings one out, Minimize puts one away.
function toggleNotesMenu(force) {
  const want = force != null ? force : !NOTES.menu;
  if (!want) {
    if (NOTES.menu) NOTES.menu.remove();
    NOTES.menu = null;
    document.removeEventListener("pointerdown", notesMenuOutside, true);
    $("#notesBtn").classList.remove("on");
    return;
  }
  if (!NOTES.menu) {
    NOTES.menu = h("div", { class: "notes-menu", role: "dialog", "aria-label": "Notes" });
    $(".main").append(NOTES.menu);
    document.addEventListener("pointerdown", notesMenuOutside, true);
  }
  $("#notesBtn").classList.add("on");
  renderNotesMenu();
}
function notesMenuOutside(e) {
  if (!NOTES.menu || NOTES.menu.contains(e.target) || e.target.closest("#notesBtn")) return;
  toggleNotesMenu(false);
}
function renderNotesMenu() {
  const menu = NOTES.menu;
  if (!menu) return;
  const hidden = NOTES.list.filter((n) => !n.is_open), open = NOTES.list.filter((n) => n.is_open);
  const row = (n) => h("div", { class: `notes-menu-row note-${n.color}` },
    h("span", { class: "notes-dot" }),
    h("div", { class: "notes-menu-main", onclick: () => openNote(n.id, true) },
      h("div", { class: "strong ellipsis" }, noteName(n)),
      h("div", { class: "muted mono-sm ellipsis" }, `${ago(n.updated_at)}${n.body ? ` · ${n.body.replace(/\s+/g, " ").slice(0, 70)}` : ""}`)),
    n.is_open
      ? iconBtn("minus", "Minimize", async () => {
        try { await putNote(n.id, { is_open: false }); const c = noteLayer().querySelector(`[data-note="${n.id}"]`); if (c) c.remove(); } catch (e) { fail(e); }
      }, "ghost")
      : iconBtn("eye", "Open", () => openNote(n.id, true), "ghost"),
    iconBtn("trash", "Delete for good", async () => {
      if (!(await confirmBox(`Delete “${noteName(n)}” for good? It cannot be brought back.`))) return;
      try { await api("DELETE", `/api/notes/${n.id}`); const c = noteLayer().querySelector(`[data-note="${n.id}"]`); if (c) c.remove(); noteChanged(n, true); } catch (e) { fail(e); }
    }, "ghost danger"));
  fill(menu,
    h("div", { class: "notes-menu-head" }, h("b", { class: "strong" }, "Notes"), h("span", { class: "spacer" }),
      hidden.length > 1 ? btn("Open all", async () => { for (const n of hidden) await openNote(n.id); }, "sm ghost") : null,
      btn("New note", () => { addNote(); }, "sm", "plus")),
    NOTES.list.length ? null : h("div", { class: "empty" }, "No notes yet. New note makes one; it stays on screen as you move between pages."),
    hidden.length ? [h("div", { class: "notes-menu-label" }, `Minimized (${hidden.length})`), hidden.map(row)] : null,
    open.length ? [h("div", { class: "notes-menu-label" }, `On screen (${open.length})`), open.map(row)] : null);
}
function panelId(card) {
  if (card.dataset.panel) return card.dataset.panel;
  const t = card.querySelector(":scope > .card-head h2, :scope > h2, :scope > .card-head h3");
  const id = t ? t.textContent.replace(/\(.*?\)/g, "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") : "";
  if (id) card.dataset.panel = id;
  return id;
}
function containerKey(parent) {
  const view = parent.closest(".view");
  return `${view ? view.id.replace("view-", "") : "page"}/${parent.id || parent.dataset.pane || [...parent.classList].join(".") || "main"}`;
}
function decoratePanels(root) {
  root.querySelectorAll(".card").forEach((card) => {
    if (card.closest(".modal") || !panelId(card)) return;
    const head = card.querySelector(":scope > .card-head") || card;
    if (head.querySelector(":scope > .grip")) return;
    const grip = h("span", { class: "grip", title: "Drag to move this panel", draggable: "true" }, "⠿");
    grip.addEventListener("dragstart", (e) => { LAYOUT.drag = card; card.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", panelId(card)); });
    grip.addEventListener("dragend", () => {
      card.classList.remove("dragging");
      const parent = card.parentElement;
      LAYOUT.drag = null;
      if (!parent) return;
      const key = containerKey(parent), order = [...parent.children].filter((c) => c.classList.contains("card")).map(panelId).filter(Boolean);
      LAYOUT.data[key] = order;
      api("PUT", "/api/layout", { [key]: order }).catch(() => {});
    });
    head.prepend(grip);
  });
}
function applyLayout(root) {
  const parents = new Set([...root.querySelectorAll(".card")].map((c) => c.parentElement).filter(Boolean));
  for (const parent of parents) {
    const saved = LAYOUT.data[containerKey(parent)];
    if (!saved) continue;
    const cards = [...parent.children].filter((c) => c.classList.contains("card") && panelId(c));
    const rank = (c) => { const i = saved.indexOf(panelId(c)); return i < 0 ? saved.length + cards.indexOf(c) : i; };
    const want = cards.slice().sort((a, b) => rank(a) - rank(b));
    if (want.every((c, i) => c === cards[i])) continue;  // already in order: no DOM change, so no loop
    const anchor = cards[cards.length - 1].nextSibling;
    want.forEach((c) => parent.insertBefore(c, anchor));
  }
}
function initPanels() {
  api("GET", "/api/layout").then((r) => { LAYOUT.data = r.layout || {}; LAYOUT.loaded = true; const v = $(".view.active"); if (v) applyLayout(v); }).catch(() => {});
  document.addEventListener("dragover", (e) => {
    const drag = LAYOUT.drag;
    if (!drag) return;
    const over = e.target.closest(".card");
    if (!over || over === drag || over.parentElement !== drag.parentElement) return;
    e.preventDefault();
    const r = over.getBoundingClientRect();
    const sideways = getComputedStyle(over.parentElement).display === "grid" && Math.abs(e.clientX - (r.left + r.width / 2)) > r.width / 4;
    const after = sideways ? e.clientX > r.left + r.width / 2 : e.clientY > r.top + r.height / 2;
    over.parentElement.insertBefore(drag, after ? over.nextSibling : over);
  });
  document.addEventListener("drop", (e) => { if (LAYOUT.drag) e.preventDefault(); });
  let queued = false;
  new MutationObserver(() => {
    if (queued || LAYOUT.drag) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      const v = $(".view.active");
      if (!v) return;
      decoratePanels(v);
      if (LAYOUT.loaded) applyLayout(v);
    });
  }).observe($(".content"), { childList: true, subtree: true });
}

async function showMemory() {
  let st;
  try { st = await api("GET", "/api/assistant/memory"); } catch (e) { return fail(e); }
  const q = h("input", { type: "search", placeholder: "Search the memory, e.g. why did tiel crash" });
  const results = h("div", { class: "stack" });
  const info = h("div");
  const showStatus = (x) => fill(info, h("div", { class: "chips" },
    h("span", { class: "chip" }, `${x.records} records`), h("span", { class: `chip` }, x.vectors_on ? `${x.embedded} with embeddings (${x.model})` : "words only: no embedding model set"),
    Object.entries(x.by_source || {}).map(([k, v]) => h("span", { class: "chip" }, `${k} ${v}`))),
    x.error ? h("p", { class: "hint" }, x.error) : null);
  const search = async () => {
    if (!q.value.trim()) return;
    try {
      const r = await api("POST", "/api/assistant/memory/search", { q: q.value, k: 12 });
      fill(results, r.results.length ? r.results.map((x) => h("div", { class: "answer" },
        h("div", { class: "muted mono-sm" }, `${x.source} · ${x.matched_by.join(" + ")}${x.similarity != null ? ` · similarity ${x.similarity}` : ""}`), x.text))
        : h("div", { class: "empty" }, "Nothing found."));
    } catch (e) { fail(e); }
  };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
  showStatus(st);
  modal("The assistant's memory", h("div", { class: "stack" }, info, h("div", { class: "row nowrap" }, q, btn("Search", search, "primary sm", "search")), results,
    h("p", { class: "hint", style: "margin:0" }, "Every question the assistant gets also searches this, and it sees the closest records. Set the embedding model in Settings → Assistant memory.")), [
    { label: "Update now", icon: "refresh", run: async () => { try { const r = await api("POST", "/api/assistant/memory/sync", { budget: 512 }); showStatus(r); toast(`Added ${r.result.added}, embedded ${r.result.embedded}.`, "ok"); } catch (e) { fail(e); } } },
    { label: "Re-embed everything", run: async () => { try { showStatus(await api("POST", "/api/assistant/memory/rebuild")); toast("Embeddings cleared; they rebuild in the background.", "ok"); } catch (e) { fail(e); } } },
    { label: "Close", run: (c) => c() }]);
}

// ── Boot ─────────────────────────────────────────────────────
function themeLabel() { $("#themeLabel").textContent = ArgosTheme.current(); }

function showThemes() {
  const T = ArgosTheme;
  const grid = h("div", { class: "theme-grid" });
  const draft = { ...T.all()[T.current()] };
  const name = h("input", { type: "text", placeholder: "Name your theme", maxlength: 24 });
  const pickers = h("div", { class: "theme-pick" });
  const labels = { bg: "Background", fg: "Text", panel: "Panels", border: "Borders", red: "Accent" };
  const draw = () => {
    fill(grid, Object.entries(T.all()).map(([id, c]) => h("button", {
      class: `theme-card${id === T.current() ? " on" : ""}`, title: id,
      onclick: () => { T.choose(id); Object.assign(draft, c); name.value = T.isCustom(id) ? id : ""; themeLabel(); draw(); drawPick(); },
    }, h("span", { class: "theme-sw", style: `background:${c.bg}; border-color:${c.border}` },
        h("i", { style: `background:${c.panel}` }), h("b", { style: `background:${c.fg}` }), h("em", { style: `background:${c.red}` })),
      h("span", { class: "theme-name" }, id),
      T.isCustom(id) && h("span", { class: "theme-del", title: "Delete", onclick: (e) => { e.stopPropagation(); T.remove(id); themeLabel(); draw(); } }, "×"))));
  };
  const drawPick = () => fill(pickers, T.KEYS.map((k) => h("label", { class: "theme-field" },
    h("input", { type: "color", value: draft[k], oninput: (e) => { draft[k] = e.target.value; T.apply("custom", draft); } }),
    h("span", {}, labels[k]))));
  const font = h("select", { onchange: (e) => T.setFont(e.target.value) },
    Object.keys(T.FONTS).map((f) => h("option", { value: f, selected: f === T.font() }, f)));
  draw(); drawPick();
  modal("Theme", h("div", { class: "stack" }, grid,
    h("h4", { class: "theme-h" }, "Make your own"), pickers,
    h("div", { class: "row nowrap" }, name, btn("Save", () => {
      const n = name.value.trim().toLowerCase().replace(/[^a-z0-9 _-]/g, "");
      if (!n) { toast("Give it a name.", "err"); return; }
      const r = T.save(n, draft);
      if (r === "limit") toast(`At most ${T.MAX_CUSTOM} of your own.`, "err");
      else if (r === "builtin") toast("That name is a built-in theme.", "err");
      else { toast("Saved.", "ok"); themeLabel(); draw(); }
    }, "primary sm", "check")),
    h("label", { class: "field" }, h("span", {}, "Font"), font),
    h("p", { class: "hint", style: "margin:0" }, "Same themes as Odysseus. Yours are kept in this browser.")),
    [{ label: "Close", run: (c) => { T.apply(T.current()); c(); } }], () => ArgosTheme.apply(ArgosTheme.current()));
}

async function migrateBrowserProfiles() {
  // The first version kept profiles in localStorage; move them to the server once.
  let old;
  try { old = JSON.parse(localStorage.getItem("profiles") || "null"); } catch (e) { return; }
  if (!Array.isArray(old) || !old.length) return;
  let moved = 0;
  for (const p of old) {
    if (!p || !p.command) continue;
    try { await api("POST", "/api/profiles", { name: p.name || p.base_model || "profile", base_model: p.base_model || "", source: p.copied_from || "browser", launch_command: p.command }); moved++; } catch (e) { /* skip bad rows */ }
  }
  try { localStorage.removeItem("profiles"); } catch (e) { /* ignore */ }
  if (moved) { toast(`Moved ${moved} browser-saved profile(s) to the server.`, "ok"); await loadProfiles(); }
}

let pollTimer = null;
let tickCount = 0;
async function tick() {
  clearTimeout(pollTimer);
  let busy = false;
  tickCount++;
  try {
    await refreshLaunches();
    busy = S.launches.some((l) => l.running && !l.engine);
    if (S.view === "serverlogs") loadServerLogs(false);
    if (S.view === "playground") await pollSelected(false);
    const downloading = S.downloads.some((d) => d.status === "running" || d.status === "queued");
    if (downloading || (S.view === "models" && tickCount % 4 === 0)) { await loadDownloads(); busy = busy || downloading; }
    const logsPane = $(".tabs[data-tabs=logs] button.on")?.dataset.pane;
    if (S.view === "logs" && ((logsPane === "gpu" && SHORT_RANGES.has(G.range)) || (logsPane === "requests" && SHORT_RANGES.has($("#rRange").value)) || tickCount % 10 === 0)) loadLogs();
    if (S.view === "schedule" && SC.pane === "live") loadLive();
    else if (tickCount % 20 === 3) loadLive();
    if (S.view === "inventory" && tickCount % 10 === 0) loadInventory();
    else if (tickCount % 40 === 5) loadInventory();
    if (tickCount % 20 === 1) refreshLogsNav();
    if (S.view === "assistant" && !A.busy && tickCount % 3 === 0) loadAssistant();
  } catch (e) { /* server restarting; try again */ }
  const liveLogs = S.view === "logs" && (SHORT_RANGES.has(G.range) || SHORT_RANGES.has($("#rRange").value));
  pollTimer = setTimeout(tick, busy || ["playground", "models", "assistant", "serverlogs"].includes(S.view) || (S.view === "schedule" && SC.pane === "live") ? 1500 : liveLogs ? 3000 : 8000);
}

function initTabs() {
  $$(".tabs[data-tabs]").forEach((bar) => bar.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-pane]");
    if (!b) return;
    $$("button", bar).forEach((x) => x.classList.toggle("on", x === b));
    $$(".pane", bar.parentElement).forEach((p) => p.classList.toggle("on", p.dataset.pane === b.dataset.pane));
    if (bar.dataset.tabs === "logs") loadLogs();
    else if (bar.dataset.tabs === "inventory") renderInvPane();
    else if (bar.dataset.tabs === "odysseus") loadOdyArea(b.dataset.pane);
    else if (bar.dataset.tabs === "schedule") { SC.pane = b.dataset.pane; if (SC.pane === "live") loadLive(); else loadSchedule(); }
  }));
}

function boot() {
  hydrateIcons();
  document.addEventListener("click", (e) => {
    const nav = e.target.closest("[data-view]");
    if (nav) { show(nav.dataset.view); return; }
    const act = e.target.closest("[data-action]");
    if (!act) return;
    const a = act.dataset.action;
    if (a === "new") resetBuilder();
    else if (a === "theme") showThemes();
    else if (a === "open-nav") document.body.classList.add("nav-open");
    else if (a === "close-nav") document.body.classList.remove("nav-open");
  });
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));
  themeLabel();
  $("#pSearch").addEventListener("input", debounce(renderProfiles, 120));
  $("#pModel").addEventListener("change", renderProfiles);
  $("#fSearch").addEventListener("input", debounce(renderPerf, 120));
  $("#fModel").addEventListener("change", renderPerf);
  $("#fCsv").onclick = exportCsv;
  $("#eSearch").addEventListener("input", debounce(renderFlagsTable, 120));
  $("#flagKinds").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-k]");
    if (!b) return;
    FLAGS.kind = b.dataset.k;
    $$("#flagKinds button").forEach((x) => x.classList.toggle("on", x === b));
    renderFlagsTable();
  });
  $("#flagAdd").onclick = () => flagForm();
  $("#flagRemoved").onclick = () => { FLAGS.showRemoved = !FLAGS.showRemoved; renderFlagsTable(); };
  $("#iRefresh").onclick = loadIntegrations;
  fill($("#ivRefresh"), icon("refresh"), "Scan");
  $("#ivRefresh").onclick = refreshInventory;
  $("#mSearch").addEventListener("input", debounce(renderLibrary, 120));
  $("#mRescan").onclick = async () => {
    const b = $("#mRescan");
    b.disabled = true;
    try {
      const r = await api("POST", "/api/library/rescan");
      const e = r.engine;
      toast(e && e.ok ? `${r.models} model files here · the engine lists ${e.models}${e.changed ? " (updated)" : ""}`
        : e ? `${r.models} model files here · engine: ${e.error || "no answer"}` : `${r.models} model files here`, e && !e.ok ? "err" : "ok");
    } catch (err) { fail(err); }
    b.disabled = false;
    loadLibrary(); loadDownloads();
  };
  $("#dSearch").onclick = hfSearch;
  $("#dQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") hfSearch(); });
  $("#rRange").addEventListener("change", loadRequests);
  $("#rModel").addEventListener("change", loadRequests);
  $("#eKind").addEventListener("change", loadEvents);
  $("#eLevel").addEventListener("change", loadEvents);
  $("#eQuery").addEventListener("input", debounce(loadEvents, 250));
  $("#eAskBtn").onclick = askAboutEvents;
  $("#eAsk").addEventListener("keydown", (e) => { if (e.key === "Enter") askAboutEvents(); });
  if (window.ResizeObserver) new ResizeObserver(debounce(() => { if (S.view === "logs" && G.data) renderGpu(); }, 150)).observe($("#gCharts"));
  initTabs();
  initPanels();
  $("#aMemory").onclick = showMemory;
  $("#bOdysseus").onclick = sendToOdysseus;
  $("#addNote").onclick = addNote;
  $("#notesBtn").onclick = () => toggleNotesMenu();
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && NOTES.menu) toggleNotesMenu(false); });
  loadNotes();
  loadSeen();
  loadGpuBars();
  setInterval(loadGpuBars, 8000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadGpuBars(); });
  initBuilder();
  initPlayground();
  initServerLogs();
  initAssistant();
  fillImportSelect();
  loadSettings().then((st) => { B.format = st.builder.command_format; }).catch(() => {})
    .finally(() => show(location.hash.slice(1) || "builder"));
  Promise.all([loadProfiles(), loadModels(), loadCatalog(), loadIntegrations(), loadLibrary(), loadDownloads()])
    .then(migrateBrowserProfiles).catch(fail);
  api("GET", "/api/assistant").then((st) => { A.state = st; assistantNav(); }).catch(() => {});
  api("GET", "/api/runs?limit=100000").then((r) => { $("#navPerf").textContent = r.runs.length || ""; }).catch(() => {});
  tick();
}

boot();
