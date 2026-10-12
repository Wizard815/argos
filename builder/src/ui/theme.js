/* Argos themes — the same presets and five-colour shape as Odysseus (bg, fg, panel, border, accent).
   Loaded in <head> so the saved theme is on before first paint. Everything else is derived. */
(function () {
  const THEMES = {
    dark:      { bg: "#282c34", fg: "#9cdef2", panel: "#111111", border: "#355a66", red: "#e06c75" },
    light:     { bg: "#f0ebe3", fg: "#5a5248", panel: "#faf6f0", border: "#d4cdc2", red: "#c47d5a" },
    midnight:  { bg: "#0d1117", fg: "#c9d1d9", panel: "#161b22", border: "#30363d", red: "#f85149" },
    paper:     { bg: "#faf8f5", fg: "#3b3836", panel: "#ffffff", border: "#d5d0c8", red: "#c5ac4a" },
    cyberpunk: { bg: "#0a0a0f", fg: "#0ff0fc", panel: "#12101a", border: "#9b30ff", red: "#e040fb" },
    retrowave: { bg: "#1a1a2e", fg: "#e94560", panel: "#16213e", border: "#533483", red: "#e94560" },
    forest:    { bg: "#1b2a1b", fg: "#a8d5a2", panel: "#142414", border: "#3d6b3d", red: "#7cb871" },
    ocean:     { bg: "#0b1a2c", fg: "#64d2ff", panel: "#091422", border: "#1e5074", red: "#4facfe" },
    ume:       { bg: "#2b1b2e", fg: "#f5c2e7", panel: "#1e1420", border: "#6c4675", red: "#f5a0c0" },
    copper:    { bg: "#1c1410", fg: "#e8c39e", panel: "#140f0a", border: "#7a5533", red: "#d4764e" },
    terminal:  { bg: "#000000", fg: "#00ff41", panel: "#0a0a0a", border: "#003b00", red: "#00ff41" },
    organs:    { bg: "#0a0406", fg: "#efe1c8", panel: "#15080a", border: "#3a1519", red: "#c83240" },
    lavender:  { bg: "#f3eef8", fg: "#3d3551", panel: "#faf7ff", border: "#cec3de", red: "#9b6dcc" },
    gpt:       { bg: "#212121", fg: "#ececec", panel: "#171717", border: "#424242", red: "#949494" },
    claude:    { bg: "#262624", fg: "#f5f4f0", panel: "#30302e", border: "#4a4a47", red: "#c6613f" },
    cute:      { bg: "#fff0f5", fg: "#d4608a", panel: "#fff8fa", border: "#f0c0d0", red: "#ff6b9d" },
  };
  const FONTS = {
    mono: "'Fira Code', ui-monospace, 'Cascadia Code', 'JetBrains Mono', Menlo, Consolas, monospace",
    sans: "system-ui, -apple-system, 'Segoe UI', sans-serif",
    serif: "Georgia, 'Times New Roman', serif",
  };
  const KEYS = ["bg", "fg", "panel", "border", "red"];
  const LS = { theme: "argos.theme", custom: "argos.themes", font: "argos.font" };
  const MAX_CUSTOM = 8;

  const read = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } };
  const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } };

  function luminance(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return 0;
    const c = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16) / 255)
      .map((v) => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)));
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }

  function all() { return Object.assign({}, THEMES, read(LS.custom, {})); }
  function current() { return read(LS.theme, "dark"); }

  function apply(name, colors) {
    const set = colors || all()[name] || THEMES.dark;
    const root = document.documentElement, st = root.style;
    const light = luminance(set.bg) > 0.4;
    root.classList.toggle("light", light);
    st.setProperty("--bg", set.bg);
    st.setProperty("--fg", set.fg);
    st.setProperty("--panel", set.panel);
    st.setProperty("--border", set.border);
    st.setProperty("--red", set.red);
    st.setProperty("--muted", "color-mix(in srgb, " + set.fg + " 62%, " + set.bg + ")");
    st.setProperty("--subheader", "color-mix(in srgb, " + set.fg + " 75%, " + set.bg + ")");
    st.setProperty("--strong", "color-mix(in srgb, " + set.fg + " 70%, " + (light ? "#000" : "#fff") + ")");
    st.setProperty("--hl-bg", "color-mix(in srgb, " + set.panel + " 60%, " + set.bg + ")");
    st.setProperty("--hl-comment", "color-mix(in srgb, " + set.fg + " 62%, " + set.bg + ")");
    return name;
  }

  function applyFont(name) {
    document.documentElement.style.setProperty("--font", FONTS[name] || FONTS.mono);
  }

  function choose(name) { write(LS.theme, name); return apply(name); }

  function save(name, colors) {
    const custom = read(LS.custom, {});
    if (!custom[name] && Object.keys(custom).length >= MAX_CUSTOM) return "limit";
    if (THEMES[name]) return "builtin";
    custom[name] = KEYS.reduce((o, k) => { o[k] = colors[k]; return o; }, {});
    write(LS.custom, custom);
    choose(name);
    return "ok";
  }

  function remove(name) {
    const custom = read(LS.custom, {});
    delete custom[name];
    write(LS.custom, custom);
    if (current() === name) choose("dark");
  }

  function setFont(name) { write(LS.font, name); applyFont(name); }

  window.ArgosTheme = { THEMES, FONTS, KEYS, MAX_CUSTOM, all, current, choose, save, remove, apply, setFont, font: () => read(LS.font, "mono"),
    isCustom: (n) => !THEMES[n] };
  apply(current());
  applyFont(read(LS.font, "mono"));
})();
