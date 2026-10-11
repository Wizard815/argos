"use strict";
/* llamacpp-profilebuilder UI — plain DOM + fetch, no build step.
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
  zap: '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
  "file-text": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8M16 17H8M10 9H8"/>',
  "file-code": '<path d="M10 12.5 8 15l2 2.5"/><path d="m14 12.5 2 2.5-2 2.5"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  save: '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/><path d="M7 3v4a1 1 0 0 0 1 1h7"/>',
  pencil: '<path d="M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5z"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  "arrow-up": '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
  activity: '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"/>',
  message: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  cog: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  eye: '<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/>',
  star: '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
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
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
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

/** Sortable table. cols: [{key, label, num?, sort?(row), cell(row)}] */
function renderTable(table, cols, rows, state, emptyMsg) {
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
    onclick: c.key ? () => { state.asc = state.key === c.key ? !state.asc : false; state.key = c.key; renderTable(table, cols, rows, state, emptyMsg); } : null,
  }, c.label)));
  const body = rows.length
    ? rows.map((r) => h("tr", {}, cols.map((c) => { const v = c.cell(r); return v instanceof HTMLTableCellElement ? v : h("td", { class: c.num ? "num" : "" }, v); })))
    : [h("tr", {}, h("td", { colspan: cols.length }, h("div", { class: "empty" }, emptyMsg)))];
  fill(table, h("thead", {}, head), h("tbody", {}, body));
}

// ── State & router ───────────────────────────────────────────
const S = {
  view: "builder",
  profiles: [], models: [], catalog: [], launches: [], runs: [], best: [], env: [], integrations: null,
  settings: null, library: null, downloads: [],
  sel: null,            // selected launch id in the playground
  lastBench: {},        // launch id -> last recorded run
};
const VIEWS = { builder: "Builder", models: "Models", profiles: "Profiles", playground: "Playground", perf: "Perf sheet",
  logs: "Logs", assistant: "Assistant", env: "Env flags", integrations: "Integrations", settings: "Settings" };
const onShow = {
  models: () => { loadLibrary(); loadCatalog(); loadDownloads(); },
  profiles: () => renderProfiles(),
  perf: () => loadPerf(),
  logs: () => loadLogs(),
  assistant: () => loadAssistant(),
  env: () => loadEnv(),
  integrations: () => loadIntegrations(),
  settings: () => loadSettings().then(renderSettings),
  playground: () => { renderLaunches(); pollSelected(true); },
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
  renderIntegrationsNav();
  if (S.view === "integrations") renderIntegrations();
}
function importSources() {
  const i = S.integrations || {};
  return [
    ["Saved profiles", S.profiles.map((p) => ({ name: p.name, base_model: p.base_model, command: p.launch_command, source: p.source || "profile", profileId: p.profile_id }))],
    ["Odysseus cookbook", (i.odysseus && i.odysseus.commands) || []],
    ["llama-swap", (i.llama_swap && i.llama_swap.commands) || []],
  ];
}
function fillImportSelect() {
  const sel = $("#importSelect");
  const groups = importSources().filter(([, items]) => items.length);
  fill(sel, h("option", { value: "" }, groups.length ? "Import from…" : "Nothing to import yet"),
    groups.map(([label, items], gi) => h("optgroup", { label }, items.map((it, ii) => h("option", { value: `${gi}:${ii}` }, it.kind ? `${it.name} (${it.kind})` : it.name)))));
  sel.disabled = !groups.length;
  sel._groups = groups;
}

// ── Builder ──────────────────────────────────────────────────
const KV_TYPES = ["", "f16", "bf16", "q8_0", "q5_1", "q5_0", "q4_1", "q4_0", "iq4_nl"];
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
  { name: "cache-type-k", label: "KV cache K", flag: "-ctk", options: KV_TYPES },
  { name: "cache-type-v", label: "KV cache V", flag: "-ctv", options: KV_TYPES },
  { name: "load-mode", label: "Load mode", flag: "-lm", ph: "dio", list: "list-loadmode" },
  { name: "split-mode", label: "Split mode", flag: "-sm", options: ["", "none", "layer", "row"] },
  { name: "flash-attn", label: "Flash attention", flag: "-fa", seg: [["", "default"], ["on", "on"], ["off", "off"], ["auto", "auto"]] },
  { name: "mmap", label: "Memory map", flag: "--[no-]mmap", seg: [["", "default"], ["mmap", "on"], ["no-mmap", "off"]] },
  { name: "fit", label: "Auto-fit", flag: "--fit", seg: [["", "default"], ["on", "on"], ["off", "off"]] },
];
const FILES = [
  { name: "model", label: "Model", flag: "--model", ph: "/app/models/model-Q4_K_M.gguf", wide: true },
  { name: "mmproj", label: "Vision projector", flag: "--mmproj", ph: "mmproj-F16.gguf", list: "list-mmproj", wide: true },
  { name: "model-draft", label: "Draft model", flag: "-md", ph: "small-draft-Q8_0.gguf", list: "list-draft", wide: true },
  { name: "spec-type", label: "Speculative", flag: "--spec-type", ph: "draft-mtp", list: "list-spectype" },
  { name: "spec-draft-n-max", label: "Draft tokens", flag: "--spec-draft-n-max", type: "number", ph: "3" },
];
const KEY_NAMES = new Set([...SETTINGS, ...FILES].map((f) => f.name).concat(["no-mmap"]));

const B = { cmd: "", parsed: null, profileId: null, source: "", layout: "multi", format: "odysseus",
  queue: Promise.resolve(), controls: {} };

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
    control = h("input", { type: f.type || "text", placeholder: f.ph || "", dataset: { fk: f.name }, list: f.list || null, spellcheck: "false" });
    const commit = () => { if (control.value.trim() !== (argValue(f.name) ?? "")) edit({ [f.name]: control.value.trim() || null }); };
    control.addEventListener("change", commit);
    control.addEventListener("keydown", (e) => { if (e.key === "Enter") commit(); });
  }
  B.controls[f.name] = control;
  return h("label", { class: `field${f.seg ? " span2" : ""}${f.wide ? " wide" : ""}` }, h("span", {}, f.label, h("code", {}, f.flag)), control);
}

function setSeg(f, v) {
  if (f.name === "mmap") edit({ mmap: v === "mmap" ? true : null, "no-mmap": v === "no-mmap" ? true : null });
  else edit({ [f.name]: v || null });
}

function initBuilder() {
  fill($("#bFields"), SETTINGS.map(buildField));
  fill($("#bFiles"), FILES.map(buildField));
  document.body.append(h("datalist", { id: "list-spectype" }, h("option", { value: "draft-mtp" })));
  $("#parseBtn").onclick = () => openCommand($("#composerInput").value, { source: "pasted" });
  $("#composerInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#parseBtn").click(); });
  $("#blankBtn").onclick = () => openCommand("llama-server -m /models/model.gguf --port 8080 -ngl 99", { source: "blank" });
  $("#importSelect").onchange = (e) => {
    const [gi, ii] = e.target.value.split(":").map(Number);
    e.target.value = "";
    const it = e.target._groups?.[gi]?.[1]?.[ii];
    if (it) openCommand(it.command, { name: it.name, base_model: it.base_model, source: it.kind ? `${it.source} · ${it.kind}` : it.source, profileId: it.profileId });
  };
  $("#bSave").onclick = () => saveProfile(false);
  $("#bSaveNew").onclick = () => saveProfile(true);
  $("#bLaunch").onclick = async () => { await B.queue; launch(B.cmd, $("#bBase").value.trim(), $("#bName").value.trim()); };
  $("#bCopy").onclick = async () => { await B.queue; copyText(B.layout === "multi" ? B.parsed.multiline : B.parsed.command); };
  $("#bYaml").onclick = showYaml;
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
    $("#bBase").value = meta.base_model || res.base_model || "";
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

function renderBuilder() {
  if (!B.parsed) return;
  const p = B.parsed;
  for (const f of [...SETTINGS, ...FILES]) {
    const c = B.controls[f.name];
    if (f.seg) {
      const cur = f.name === "mmap" ? (argValue("no-mmap") != null ? "no-mmap" : argValue("mmap") != null ? "mmap" : "")
        : (argValue(f.name) == null ? "" : (argValue(f.name) || "on"));
      $$("button", c).forEach((b) => b.classList.toggle("on", b.dataset.v === cur));
    } else if (c !== document.activeElement) {
      c.value = argValue(f.name) ?? "";
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
  try {
    const res = await api("POST", "/api/profiles", {
      profile_id: asNew ? null : B.profileId, name, base_model: $("#bBase").value.trim(),
      source: B.source === "profile" ? "" : B.source, launch_command: B.cmd,
    });
    B.profileId = res.profile.profile_id;
    $("#bName").value = res.profile.name;
    toast(updating ? `Updated “${name}”.` : `Saved “${name}”.`, "ok");
    renderBuilder();
    loadProfiles().catch(fail);
    loadModels().catch(fail);
  } catch (e) { fail(e); }
}

async function showYaml() {
  await B.queue;
  const yaml = B.parsed.llama_swap_yaml;
  modal("llama-swap config entry", h("div", { class: "stack" },
    h("p", { class: "hint", style: "margin:0" }, "Paste under models: in llama-swap's config.yaml. The port is swapped for ${PORT} so llama-swap can assign it."),
    h("pre", { class: "code" }, yaml)),
  [{ label: "Copy", icon: "copy", cls: "primary", run: () => copyText(yaml) }]);
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
      p.model_id || p.base_model || DASH, p.base_model && p.model_id && p.base_model !== p.model_id ? h("div", { class: "muted mono-sm" }, p.base_model) : null) },
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
  fill($("#gProfile"), h("option", { value: "" }, "Load a profile…"), S.profiles.map((p) => h("option", { value: p.profile_id }, p.name)));
}

async function launch(command, base_model, profile_name) {
  if (!command || !command.trim()) { toast("Nothing to launch.", "err"); return; }
  try {
    const res = await api("POST", "/api/launches", { command, base_model, profile_name });
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
  if (!S.sel && S.launches.length) S.sel = S.launches[0].launch_id;
  const running = S.launches.filter((l) => l.running);
  fill($("#navPlayground"), running.length ? `${running.length} running` : "idle",
    h("span", { class: `dot ${running.some((l) => l.ready) ? "on" : running.length ? "warn" : ""}` }));
  if (S.view === "playground") renderLaunches();
}

function launchState(l) {
  if (!l) return ["", "no launch selected"];
  if (l.running) return l.ready ? ["on", "ready"] : ["warn", "starting…"];
  if (l.stopped) return ["", "stopped"];
  return [l.exit_code ? "bad" : "", `exited ${l.exit_code ?? ""}`.trim()];
}

function renderLaunches() {
  const list = S.launches.map((l) => {
    const [dot, label] = launchState(l);
    return h("div", { class: `launch${l.launch_id === S.sel ? " sel" : ""}`, onclick: () => { S.sel = l.launch_id; renderLaunches(); pollSelected(true); } },
      h("span", { class: `dot ${dot}` }),
      h("div", { class: "who" },
        h("div", { class: "t" }, l.profile_name || l.base_model || (l.command.split(" ").find((t) => !t.includes("=")) || l.launch_id).split("/").pop()),
        h("div", { class: "s" }, `:${l.port} · ${label} · ${l.running ? uptime(l.started_at) : ago(l.started_at)}`)));
  });
  fill($("#gLaunches"), list.length ? list : h("div", { class: "empty" }, "Nothing launched yet."));
  const cur = S.launches.find((l) => l.launch_id === S.sel);
  const [dot, label] = launchState(cur);
  fill($("#gState"), cur ? h("span", { class: `pill ${dot === "on" ? "ok" : dot === "bad" ? "bad" : ""}` }, h("span", { class: `dot ${dot}` }), label) : null);
  $("#gBench").disabled = !(cur && cur.running && cur.ready);
  $("#gRecord").disabled = !cur;
  $("#gStop").disabled = !(cur && cur.running);
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
    stat("VRAM", num(r.mem_used_gb, 2), "GB"),
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
  } catch (e) { /* log may not exist yet */ }
}

function initPlayground() {
  $("#gProfile").onchange = (e) => {
    const p = S.profiles.find((x) => x.profile_id === e.target.value);
    if (p) { $("#gCmd").value = p.launch_command; $("#gBase").value = p.base_model || ""; $("#gCmd").dataset.profile = p.name; }
    e.target.value = "";
  };
  $("#gCmd").addEventListener("input", () => delete $("#gCmd").dataset.profile);
  $("#gLaunch").onclick = () => launch($("#gCmd").value, $("#gBase").value.trim(), $("#gCmd").dataset.profile || "");
  $("#gBench").onclick = async () => {
    const id = S.sel, b = $("#gBench");
    b.disabled = true; b.lastChild.textContent = "Benchmarking…";
    try {
      const res = await api("POST", `/api/launches/${encodeURIComponent(id)}/bench`, { prompt_words: +$("#gWords").value, n_predict: +$("#gNPredict").value });
      S.lastBench[id] = res.run;
      toast(`${num(res.run.tokens_per_second)} t/s generation · saved to perf sheet`, "ok");
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
    try { await api("POST", `/api/launches/${encodeURIComponent(S.sel)}/stop`); toast("Stopped.", "ok"); } catch (e) { fail(e); }
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
  openCommand(r.launch_command, { base_model: r.base_model, name: r.profile_name || "", source: "perf sheet" });
}
async function showLog(launchId) {
  try {
    const res = await fetch(`/api/launches/${encodeURIComponent(launchId)}/log?tail=300000`);
    if (!res.ok) { toast("That run's log file is gone.", "err"); return; }
    const text = await res.text();
    modal(`Log · ${launchId}`, h("pre", { class: "log", style: "max-height:65dvh" }, text || "(empty)"));
  } catch (e) { fail(e); }
}
function renderPerf() {
  const m = $("#fModel").value;
  const best = S.best.filter((b) => !m || b.base_model === m);
  fill($("#fBest"), best.length ? best.map((b) => h("div", { class: "best" },
    h("div", { class: "model", title: b.base_model }, b.base_model),
    h("div", { class: "tps" }, num(b.tokens_per_second), h("small", {}, ` t/s · pp ${num(b.prompt_per_second, 0)} t/s`)),
    h("div", { class: "chips" }, settingChips(b)),
    h("div", { class: "row" }, h("span", { class: "muted mono-sm", style: "flex:1" }, `${b.runs} run${b.runs === 1 ? "" : "s"} · ${ago(b.created_at)}`),
      btn("Open", () => openRunInBuilder(b), "sm", "sliders")))) : h("div", { class: "empty" }, "No successful runs yet. Benchmark something in the Playground."));
  const rows = perfRows();
  const maxTps = Math.max(1, ...rows.map((r) => r.tokens_per_second || 0));
  renderTable($("#fTable"), [
    { key: "created_at", label: "When", cell: (r) => h("span", { class: "muted", title: new Date(r.created_at * 1000).toLocaleString() }, ago(r.created_at)) },
    { key: "base_model", label: "Model", cell: (r) => h("td", { class: "trunc", title: r.launch_command }, h("span", { class: "strong" }, r.base_model), r.profile_name ? h("div", { class: "muted mono-sm" }, r.profile_name) : null) },
    { key: "tokens_per_second", label: "Gen t/s", num: true, cell: (r) => h("div", { class: "bar-cell" }, num(r.tokens_per_second), h("span", { class: "bar" }, h("i", { style: `width:${Math.round(((r.tokens_per_second || 0) / maxTps) * 100)}%` }))) },
    { key: "prompt_per_second", label: "Prompt t/s", num: true, cell: (r) => num(r.prompt_per_second) },
    { key: "input_tokens", label: "In / out", num: true, cell: (r) => `${num(r.input_tokens, 0)} / ${num(r.output_tokens, 0)}` },
    { key: "n_ctx", label: "Ctx", num: true, cell: (r) => num(r.n_ctx, 0) },
    { key: "gpu_layers", label: "NGL", cell: (r) => r.layers_offloaded ? `${r.gpu_layers ?? DASH} (${r.layers_offloaded})` : (r.gpu_layers ?? DASH) },
    { label: "Settings", cell: (r) => h("div", { class: "chips" }, settingChips({ ...r, n_ctx: null, gpu_layers: null })) },
    { key: "mem_used_gb", label: "VRAM", num: true, cell: (r) => (r.mem_used_gb ? `${num(r.mem_used_gb, 2)} GB` : DASH) },
    { key: "gpu_util", label: "GPU", num: true, cell: (r) => (r.gpu_util != null ? `${num(r.gpu_util, 0)}%` : DASH) },
    { key: "log_source", label: "Src", cell: (r) => h("span", { class: `pill ${r.success ? "ok" : "bad"}`, title: r.note || "" }, r.success ? r.log_source || "run" : "no data") },
    { label: "", cell: (r) => h("td", { class: "actions" },
      iconBtn("sliders", "Open in builder", () => openRunInBuilder(r)),
      iconBtn("file-text", "View log", () => showLog(r.launch_id || r.run_id)),
      iconBtn("trash", "Delete run", async () => {
        if (!(await confirmBox("Delete this run from the perf sheet?"))) return;
        try { await api("DELETE", `/api/runs/${encodeURIComponent(r.run_id)}`); loadPerf(); } catch (e) { fail(e); }
      }, "danger")) },
  ], rows, perfSort, S.runs.length ? "No runs match." : "No runs recorded yet.");
}
function exportCsv() {
  const rows = perfRows();
  if (!rows.length) { toast("Nothing to export.", "err"); return; }
  const cols = ["created_at", "base_model", "profile_name", "tokens_per_second", "prompt_per_second", "input_tokens", "output_tokens",
    "n_ctx", "gpu_layers", "layers_offloaded", "n_batch", "n_ubatch", "n_threads", "flash_attention", "cache_type_k", "cache_type_v",
    "mmap", "load_mode", "spec_type", "mmproj_file", "draft_model", "model_id", "draft_acc", "mem_used_gb", "gpu_util", "temp_c", "log_source", "success", "launch_command"];
  const esc = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const csv = [cols.join(",")].concat(rows.map((r) => cols.map((c) => esc(c === "created_at" ? new Date(r[c] * 1000).toISOString() : r[c])).join(","))).join("\n");
  const a = h("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: "perf-sheet.csv" });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ── Catalog ──────────────────────────────────────────────────
const CATALOG = [
  { kind: "mmproj", title: "Vision projectors", flag: "--mmproj", hint: "Projectors kept outside the model's folder. (One beside the model is found automatically.)" },
  { kind: "draft", title: "Draft models", flag: "-md", hint: "Small models for speculative decoding with a bigger one. (MTP needs no file: it is --spec-type draft-mtp.)" },
];
function renderCatalog() {
  fill($("#cCards"), CATALOG.map((c) => {
    const base = h("input", { type: "text", list: "modelList", placeholder: "Base model" });
    const path = h("input", { type: "text", placeholder: "/models/file.gguf", spellcheck: "false" });
    const repo = h("input", { type: "text", placeholder: "org/repo (optional)", spellcheck: "false" });
    const add = async () => {
      try {
        await api("POST", "/api/catalog", { kind: c.kind, base_model: base.value.trim(), file_path: path.value.trim(), hf_repo: repo.value.trim() });
        toast("Added.", "ok");
        await Promise.all([loadCatalog(), loadModels()]);
      } catch (e) { fail(e); }
    };
    const files = S.catalog.filter((f) => f.kind === c.kind);
    const table = h("table");
    renderTable(table, [
      { key: "base_model", label: "Base model", cell: (f) => h("span", { class: "strong" }, f.base_model) },
      { key: "name", label: "File", cell: (f) => h("td", { class: "trunc", title: f.file_path || f.hf_repo }, f.name, h("div", { class: "muted mono-sm" }, f.file_path || f.hf_repo)) },
      { label: "", cell: (f) => h("td", { class: "actions" }, iconBtn("trash", "Remove", async () => {
        try { await api("DELETE", `/api/catalog/${f.id}`); loadCatalog(); } catch (e) { fail(e); }
      }, "danger")) },
    ], files, { key: null, asc: false }, "None registered.");
    return h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h2", {}, c.title), h("code", { class: "tk-flag mono-sm" }, c.flag)),
      h("p", { class: "hint" }, c.hint),
      h("div", { class: "fields" }, h("label", { class: "field wide" }, h("span", {}, "Base model"), base),
        h("label", { class: "field wide" }, h("span", {}, "File path"), path),
        h("label", { class: "field wide" }, h("span", {}, "Hugging Face repo"), repo)),
      h("div", { class: "row end", style: "margin:10px 0 6px" }, btn("Add", add, "primary sm", "plus")),
      h("div", { class: "table-wrap" }, table));
  }));
}

// ── Env flags ────────────────────────────────────────────────
async function loadEnv() {
  try { S.env = (await api("GET", "/api/env-flags")).rows; } catch (e) { fail(e); return; }
  renderEnvTable();
}
const envSort = { key: null, asc: false };
function renderEnvTable() {
  const q = $("#eSearch").value.trim().toLowerCase();
  const rows = S.env.filter((e) => !q || `${e.flag} ${e.category} ${e.description} ${e.recommended}`.toLowerCase().includes(q));
  renderTable($("#eTable"), [
    { key: "flag", label: "Variable", cell: (e) => h("code", { class: "tk-env" }, e.flag) },
    { key: "category", label: "Category", cell: (e) => h("span", { class: "pill" }, e.category || DASH) },
    { label: "What it does", cell: (e) => h("td", { style: "min-width:240px" }, e.description, e.recommended ? h("div", { class: "muted mono-sm" }, e.recommended) : null) },
    { key: "last_used_for", label: "Last used for", cell: (e) => e.last_used_for || DASH },
    { key: "last_tps", label: "Gen t/s", num: true, cell: (e) => num(e.last_tps) },
    { key: "last_pps", label: "Prompt t/s", num: true, cell: (e) => num(e.last_pps) },
    { key: "last_run_at", label: "When", cell: (e) => h("span", { class: "muted" }, ago(e.last_run_at)) },
  ], rows, envSort, "No matches.");
}

// ── Integrations ─────────────────────────────────────────────
function renderIntegrationsNav() {
  const i = S.integrations;
  if (!i) return;
  const swapOk = !i.llama_swap.configured || ((i.llama_swap.config_ok ?? true) && (!i.llama_swap.server || i.llama_swap.server.ok));
  const odyOk = !i.odysseus.configured || i.odysseus.ok;
  const any = i.llama_swap.configured || i.odysseus.configured;
  fill($("#navIntegrations"), any ? h("span", { class: `dot ${swapOk && odyOk ? "on" : "bad"}` }) : null);
}
function commandList(cmds) {
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
function renderExport(x) {
  const origin = location.origin;
  const viaUrl = ["odysseus:", "  enabled: true", `  baseURL: "${origin}"`,
    `  token: "${x.token_required ? "<your export token>" : "any"}"  # llama-swap needs a value`,
    "  refreshSeconds: 60", '  profilePrefix: "pb"', '  outputFile: "/app/config.d/odysseus.generated.yaml"', "  ensureDio: true"].join("\n");
  const viaFile = ["odysseus:", "  enabled: true", `  stateFile: "${x.file || "/app/profilebuilder-data/cookbook_state.json"}"`,
    "  refreshSeconds: 60", '  profilePrefix: "pb"', '  outputFile: "/app/config.d/odysseus.generated.yaml"', "  ensureDio: true"].join("\n");
  fill($("#iExport"),
    h("div", { class: "card-head" }, h("h2", {}, "Export for llama-swap"), h("span", { class: "spacer" }),
      h("span", { class: "pill accent" }, `${x.presets} presets`), x.tasks ? h("span", { class: "pill" }, `${x.tasks} tasks`) : null),
    h("p", { class: "hint" }, "This app serves your profiles at ", h("code", {}, "/api/cookbook/state"),
      " in Odysseus' cookbook_state.json shape, so llama-swap-rescan's Odysseus integration reads it exactly like Odysseus — every profile becomes a variant ",
      h("code", {}, "<model-id>--<profile name>"), ". No five-per-model limit.",
      x.include_odysseus ? " Odysseus' own presets are passed through, so pointing llama-swap here keeps them." : ""),
    statusLine(!x.error, x.file ? (x.error ? `${x.file}: ${x.error}` : `writing ${x.file}${x.written_at ? ` · last ${ago(x.written_at)}` : ""}`)
      : "No export file set (Settings → Export) — llama-swap can still read the URL."),
    (x.warnings || []).length ? h("div", { class: "stack", style: "margin-bottom:10px" }, x.warnings.map((w) => h("div", { class: "muted mono-sm" }, `⚠ ${w}`))) : null,
    h("div", { class: "cols2" },
      h("div", { class: "stack" }, h("span", { class: "muted mono-sm" }, "llama-swap config.yaml — over HTTP"), h("pre", { class: "code" }, viaUrl),
        h("div", { class: "row end" }, btn("Copy", () => copyText(viaUrl), "sm", "copy"))),
      h("div", { class: "stack" }, h("span", { class: "muted mono-sm" }, "or from the export file (shared volume)"), h("pre", { class: "code" }, viaFile),
        h("div", { class: "row end" }, btn("Copy", () => copyText(viaFile), "sm", "copy")))));
}

function renderIntegrations() {
  const i = S.integrations;
  if (!i) return;
  renderExport(i.export);
  const o = i.odysseus, s = i.llama_swap;
  fill($("#iOdysseus"), h("div", { class: "card-head" }, h("h2", {}, "Odysseus cookbook")),
    !o.configured ? setupHint(["ODYSSEUS_COOKBOOK=/path/to/cookbook_state.json"])
      : [statusLine(o.ok, o.ok ? `${o.commands.length} commands from ${o.location}` : `${o.location}: ${o.error}`), o.ok && commandList(o.commands)]);
  fill($("#iSwap"), h("div", { class: "card-head" }, h("h2", {}, "llama-swap")),
    !s.configured ? setupHint(["LLAMA_SWAP_CONFIG=/path/to/config.yaml", "LLAMA_SWAP_URL=http://127.0.0.1:8080"])
      : [
        s.url && statusLine(s.server.ok, s.server.ok ? `${s.url} · ${s.server.running.length} model(s) loaded` : `${s.url}: ${s.server.error}`),
        s.url && s.server.ok && s.server.running.length ? h("div", { class: "chips", style: "margin-bottom:10px" }, s.server.running.map((r) => h("span", { class: "chip" }, `${r.model || r.name || "?"} · ${r.state || "running"}`))) : null,
        s.config && statusLine(s.config_ok, s.config_ok ? `${s.commands.length} models in ${s.config}` : `${s.config}: ${s.config_error}`),
        s.config_ok && commandList(s.commands),
      ]);
}

// ── Models: library + prefabs ────────────────────────────────
const GB = 1e9;
const size = (b) => (b >= GB ? `${(b / GB).toFixed(b >= 10 * GB ? 0 : 1)} GB` : `${Math.max(1, Math.round(b / 1e6))} MB`);
const librarySort = { key: null, asc: true };

async function loadLibrary() {
  try { S.library = await api("GET", "/api/library"); } catch (e) { fail(e); return; }
  $("#navModels").textContent = S.library.models.length || "";
  if (S.view === "models") renderLibrary();
}
async function openPrefab(m, launchIt = false) {
  try {
    const res = await api("POST", "/api/library/prefab", { path: m.path });
    if (launchIt) launch(res.command, m.name, "");
    else openCommand(res.command, { name: "", base_model: m.name, source: "prefab" });
  } catch (e) { fail(e); }
}
function renderLibrary() {
  const lib = S.library;
  if (!lib) return;
  $("#mSub").textContent = `Every GGUF in ${lib.models_dir}, each with a ready-made prefab command.`;
  const q = $("#mSearch").value.trim().toLowerCase();
  const rows = lib.models.filter((m) => !q || `${m.name} ${m.dir} ${m.kind} ${m.quant}`.toLowerCase().includes(q));
  $("#mCount").textContent = `${lib.models.length} model${lib.models.length === 1 ? "" : "s"}`;
  const empty = !lib.exists
    ? h("span", {}, `The models folder ${lib.models_dir} does not exist. Mount your GGUF folder there (e.g. `, h("b", {}, "-v /path/to/LLMBin:/app/models"), ") or change it in Settings.")
    : lib.models.length ? "No models match." : h("span", {}, "No .gguf files yet. Get one from the ", h("b", {}, "Download"), " tab.");
  renderTable($("#mTable"), [
    { key: "name", label: "Model", cell: (m) => h("td", { class: "trunc", title: m.path }, h("span", { class: "strong" }, m.name),
      h("div", { class: "muted mono-sm" }, m.dir === "." ? m.id : `${m.dir} · ${m.id}`)) },
    { key: "kind", label: "Kind", cell: (m) => h("span", { class: `pill${m.kind === "chat" ? "" : " accent"}` }, m.kind) },
    { key: "quant", label: "Quant", cell: (m) => (m.quant ? h("span", { class: "chip" }, m.quant) : DASH) },
    { key: "size", label: "Size", num: true, cell: (m) => size(m.size) },
    { label: "Files", cell: (m) => h("div", { class: "chips" },
      m.shards < 0 ? h("span", { class: "pill bad" }, `incomplete ${-m.shards} parts`) : m.shards > 1 ? h("span", { class: "chip" }, `${m.shards} parts`) : null,
      m.mmproj.length ? h("span", { class: "chip", title: m.mmproj.join("\n") }, "mmproj") : null) },
    { key: "profiles", label: "Profiles", num: true, cell: (m) => (m.profiles
      ? h("button", { class: "btn sm ghost", onclick: () => showProfilesFor(m.id) }, String(m.profiles)) : DASH) },
    { label: "", cell: (m) => h("td", { class: "actions" },
      btn("Prefab", () => openPrefab(m), "sm", "sliders"),
      iconBtn("play", "Launch the prefab", () => openPrefab(m, true))) },
  ], rows, librarySort, empty);
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
        running && d.speed ? ` · ${size(d.speed)}/s` : "", eta != null ? ` · ${uptime(Date.now() / 1000 - eta)} left` : "",
        d.error ? ` · ${d.error}` : ""));
  }) : h("div", { class: "empty" }, "No downloads yet."));
}

// ── Logs: GPU charts ─────────────────────────────────────────
const SVGNS = "http://www.w3.org/2000/svg";
const sv = (tag, attrs = {}) => { const el = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); return el; };
const RANGES = [["1h", "1h"], ["6h", "6h"], ["24h", "24h"], ["7d", "7d"], ["30d", "30d"], ["90d", "90d"], ["1y", "1y"]];
const G = { range: "24h", data: null };
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
  $("#gSource").textContent = d.rows.length
    ? `${d.source === "hourly" ? "hourly rollups" : `${d.bucket}s buckets`} · source: ${d.collector.source || "stored"}`
    : "";
  const gpus = [...new Map(d.rows.map((r) => [r.gpu, r.name])).entries()].sort((a, b) => a[0] - b[0]);
  if (!d.rows.length) {
    fill($("#gLegend"));
    fill($("#gCharts"), h("div", { class: "empty", style: "grid-column:1/-1" },
      d.collector.enabled ? "No GPU readings in this range yet. With LLAMA_SWAP_URL set they come from llama-swap; otherwise from /sys/class/drm (AMD) or nvidia-smi."
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
    { title: "VRAM used", unit: "GB", key: "mem_used_mb", scale: 1 / 1024, yMax: vramTop, digits: 1 },
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
    { label: "Now", num: true, cell: (r) => (r.now ? `${num(r.now.util, 0)}% · ${num((r.now.mem_used_mb || 0) / 1024, 1)} GB · ${num(r.now.temp_c, 0)}°C` : DASH) },
    { label: "Util avg / max", num: true, cell: (r) => `${num(r.ua, 0)}% / ${num(r.um, 0)}%` },
    { label: "VRAM peak", num: true, cell: (r) => (r.vm != null ? `${num(r.vm / 1024, 1)} / ${num(r.vt / 1024, 1)} GB` : DASH) },
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
  const url = S.settings?.integrations.llama_swap_url;
  $("#rHint").textContent = url ? `synced from ${url}` : "Set the llama-swap URL in Settings to collect request history.";
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
    { key: "status", label: "Status", cell: (r) => h("span", { class: `pill ${r.status >= 400 || r.error ? "bad" : "ok"}`, title: r.error || "" }, String(r.status || "?")) },
  ], res.requests, { key: null, asc: false }, "No requests.");
}

async function loadEvents() {
  const qs = new URLSearchParams({ limit: "500" });
  for (const [k, id] of [["kind", "#eKind"], ["level", "#eLevel"], ["q", "#eQuery"]]) if ($(id).value.trim()) qs.set(k, $(id).value.trim());
  let res;
  try { res = await api("GET", `/api/events?${qs}`); } catch (e) { fail(e); return; }
  const lvl = { error: "bad", warn: "warn", info: "" };
  renderTable($("#evTable"), [
    { key: "ts", label: "When", cell: (e) => h("span", { class: "muted", title: new Date(e.ts * 1000).toLocaleString() }, new Date(e.ts * 1000).toLocaleString()) },
    { key: "level", label: "Level", cell: (e) => h("span", { class: `pill ${lvl[e.level] || ""}` }, e.level) },
    { key: "kind", label: "Kind", cell: (e) => h("span", { class: "chip" }, e.kind) },
    { label: "Message", cell: (e) => h("td", { style: "white-space:normal;min-width:280px" }, e.message) },
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
  const sv = A.state?.server;
  fill($("#navAssistant"), sv && sv.mode === "managed" && sv.running ? h("span", { class: `dot ${sv.ready ? "on" : "warn"}` }) : null);
}

function renderAssistant() {
  const st = A.state;
  if (!st) return;
  const sv = st.server;
  $("#aModel").textContent = sv.mode === "endpoint" ? `${sv.model || "model"} @ ${sv.endpoint || "?"}` : (sv.profile ? sv.profile.name : "no model set");
  fill($("#aStatus"), sv.mode === "managed" && sv.profile
    ? [h("span", { class: `dot ${sv.ready ? "on" : sv.running ? "warn" : ""}` }), sv.ready ? "ready" : sv.running ? "loading" : "starts on first message"]
    : null);
  $("#aStop").disabled = !(sv.mode === "managed" && sv.running);
  $("#aTokens").textContent = `${num(st.tokens, 0)} / ${num(st.budget, 0)} tokens`;
  $("#aMeter").style.width = `${Math.min(100, (st.tokens / st.budget) * 100).toFixed(0)}%`;
  assistantNav();
  const box = $("#aMessages");
  const msgs = st.messages;
  const needsSetup = sv.mode === "managed" && !sv.profile;
  const kids = [];
  if (!msgs.length) {
    kids.push(h("div", { class: "hero", style: "padding-top:4vh" },
      h("div", { class: "logo" }, icon("message"), "assistant"),
      h("div", { class: "tag" }, "Ask about your GPUs, speeds, models and profiles. It reads this app's logs."),
      needsSetup ? h("div", { class: "stack", style: "align-items:center;margin-top:16px" },
        h("p", { class: "hint", style: "margin:0;max-width:520px" }, "The assistant runs a small model beside your others: Qwen3-0.6B (Q8_0, under 1 GB). It is reused if it's already in your models folder, otherwise downloaded from Hugging Face."),
        h("div", { class: "row" }, btn("Set up Qwen3-0.6B", setupAssistant, "primary", "download"), btn("Use another profile…", () => show("settings"), ""))) :
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
    const res = await api("POST", "/api/assistant/setup", {});
    toast(res.download ? "Downloading Qwen3-0.6B — the assistant starts once it lands." : `Assistant set to ${res.profile.name}.`, "ok");
    loadDownloads();
    loadProfiles();
    loadAssistant();
  } catch (e) { fail(e); }
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
        else if (ev.type === "delta") { status.remove(); answer += ev.text; body.textContent = answer; bubble.scrollIntoView({ block: "end" }); }
        else if (ev.type === "error") { status.remove(); body.append(h("p", { style: "color:var(--danger)" }, ev.error)); }
      }
    }
  } catch (e) { status.remove(); body.append(h("p", { style: "color:var(--danger)" }, e.message)); }
  A.busy = false;
  $("#aSend").disabled = false;
  await loadAssistant();
  input.focus();
}

function initAssistant() {
  $("#aSend").onclick = sendChat;
  $("#aInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } });
  $("#aReset").onclick = async () => {
    if (!(await confirmBox("Clear the whole conversation and its summary?", "Reset chat"))) return;
    try { await api("POST", "/api/assistant/reset"); toast("Chat cleared.", "ok"); loadAssistant(); } catch (e) { fail(e); }
  };
  $("#aStop").onclick = async () => { await api("POST", "/api/assistant/stop").catch(fail); loadAssistant(); };
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
    { k: "models_dir", label: "Models folder", hint: "Library and download target. In Docker, mount your GGUF folder here (llama-swap uses /app/models)." }] },
  { group: "integrations", title: "Integrations", fields: [
    { k: "odysseus_cookbook", label: "Odysseus cookbook_state.json", ph: "/app/odysseus-data/cookbook_state.json" },
    { k: "llama_swap_config", label: "llama-swap config", ph: "/app/llama-swap/config.yaml" },
    { k: "llama_swap_url", label: "llama-swap URL", ph: "http://llama-swap:8080", hint: "GPU readings, request history and status come from here." }] },
  { group: "builder", title: "Builder", fields: [
    { k: "command_format", label: "Command format", type: "select", wide: true, options: [["odysseus", "Odysseus (Cookbook shape)"], ["plain", "As written"]] }] },
  { group: "prefab", title: "Prefab defaults", hint: "Every model's prefab starts from these; embedding, vision and MTP models get their extras on top.", fields: [
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
  { group: "telemetry", title: "Telemetry", fields: [
    { k: "enabled", label: "Record GPU history", type: "check" },
    { k: "source", label: "Source", type: "select", wide: true, options: [["auto", "auto (llama-swap, then local)"], ["llama-swap", "llama-swap only"], ["local", "local sysfs / nvidia-smi"], ["off", "off"]] },
    { k: "interval_s", label: "Every (seconds)", type: "number" },
    { k: "raw_days", label: "Keep raw readings (days)", type: "number", hint: "Hourly rollups are kept forever." }] },
  { group: "export", title: "Export for llama-swap", fields: [
    { k: "file", label: "Also write cookbook_state.json to", ph: "/data/export/cookbook_state.json" },
    { k: "include_odysseus", label: "Pass Odysseus' own presets through", type: "check" },
    { k: "include_tasks", label: "Include running launches as tasks", type: "check" },
    { k: "token", label: "Require X-Odysseus-Internal-Token", type: "password" }] },
  { group: "assistant", title: "Assistant", fields: [
    { k: "mode", label: "Runs on", type: "select", wide: true, options: [["managed", "a profile launched here"], ["endpoint", "an OpenAI-compatible URL"]] },
    { k: "profile_id", label: "Profile", type: "profile", wide: true },
    { k: "endpoint", label: "Endpoint", ph: "http://llama-swap:8080/v1" },
    { k: "model", label: "Model ID at the endpoint", ph: "qwen3-0.6b-q8_0" },
    { k: "context_tokens", label: "Context window (tokens)", type: "number", hint: "Must match the profile's -c; compaction starts at ~70%." },
    { k: "keep_recent", label: "Messages kept verbatim", type: "number" },
    { k: "temperature", label: "Temperature", type: "number", step: "0.1" }] },
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

// ── Boot ─────────────────────────────────────────────────────
function setTheme(light) {
  document.documentElement.classList.toggle("light", light);
  $("#themeLabel").textContent = light ? "light" : "dark";
  try { localStorage.setItem("pb.theme", light ? "light" : "dark"); } catch (e) { /* private mode */ }
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
    busy = S.launches.some((l) => l.running);
    if (S.view === "playground") await pollSelected(false);
    const downloading = S.downloads.some((d) => d.status === "running" || d.status === "queued");
    if (downloading || (S.view === "models" && tickCount % 4 === 0)) { await loadDownloads(); busy = busy || downloading; }
    if (S.view === "logs" && tickCount % 10 === 0) loadLogs();
    if (tickCount % 20 === 1) refreshLogsNav();
    if (S.view === "assistant" && !A.busy && tickCount % 3 === 0) loadAssistant();
  } catch (e) { /* server restarting; try again */ }
  pollTimer = setTimeout(tick, busy || ["playground", "models", "assistant"].includes(S.view) ? 1500 : 8000);
}

function initTabs() {
  $$(".tabs[data-tabs]").forEach((bar) => bar.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-pane]");
    if (!b) return;
    $$("button", bar).forEach((x) => x.classList.toggle("on", x === b));
    $$(".pane", bar.parentElement).forEach((p) => p.classList.toggle("on", p.dataset.pane === b.dataset.pane));
    if (bar.dataset.tabs === "logs") loadLogs();
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
    else if (a === "theme") setTheme(!document.documentElement.classList.contains("light"));
    else if (a === "open-nav") document.body.classList.add("nav-open");
    else if (a === "close-nav") document.body.classList.remove("nav-open");
  });
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));
  $("#themeLabel").textContent = document.documentElement.classList.contains("light") ? "light" : "dark";
  $("#pSearch").addEventListener("input", debounce(renderProfiles, 120));
  $("#pModel").addEventListener("change", renderProfiles);
  $("#fSearch").addEventListener("input", debounce(renderPerf, 120));
  $("#fModel").addEventListener("change", renderPerf);
  $("#fCsv").onclick = exportCsv;
  $("#eSearch").addEventListener("input", debounce(renderEnvTable, 120));
  $("#iRefresh").onclick = loadIntegrations;
  $("#mSearch").addEventListener("input", debounce(renderLibrary, 120));
  $("#mRescan").onclick = () => { loadLibrary(); loadDownloads(); };
  $("#dSearch").onclick = hfSearch;
  $("#dQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") hfSearch(); });
  $("#rRange").addEventListener("change", loadRequests);
  $("#rModel").addEventListener("change", loadRequests);
  $("#eKind").addEventListener("change", loadEvents);
  $("#eLevel").addEventListener("change", loadEvents);
  $("#eQuery").addEventListener("input", debounce(loadEvents, 250));
  if (window.ResizeObserver) new ResizeObserver(debounce(() => { if (S.view === "logs" && G.data) renderGpu(); }, 150)).observe($("#gCharts"));
  initTabs();
  initBuilder();
  initPlayground();
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
