// Theme system — preset themes + custom color editing, stored in localStorage
// ES6 module



function hexToRgb(hex) {
  let h = String(hex || '').trim().replace(/^#/, '');
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return null;
  const n = parseInt(h, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

const LS_KEY = 'voxachat-theme';
const CUSTOM_THEMES_KEY = 'voxachat-custom-themes';

function storageGetJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback !== undefined ? fallback : null;
    return JSON.parse(raw);
  } catch (e) {
    return fallback !== undefined ? fallback : null;
  }
}
function storageSetJSON(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) {}
}
function storageRemove(key) {
  try { localStorage.removeItem(key); } catch (e) {}
}

(function (w) {

const THEMES = {
  dark:       { bg:'#282c34', fg:'#9cdef2', panel:'#111111', border:'#355a66', red:'#e06c75' },
  light:      { bg:'#f3f5f7', fg:'#26313a', panel:'#ffffff', border:'#cbd3da', red:'#b34d5c' },
  midnight:   { bg:'#0d1117', fg:'#c9d1d9', panel:'#161b22', border:'#30363d', red:'#f85149' },
  // Spicy / fun themes
  cyberpunk:  { bg:'#10121a', fg:'#9bdde0', panel:'#171a26', border:'#514b78', red:'#d45aa8' },
  retrowave:  { bg:'#1a1a2e', fg:'#e94560', panel:'#16213e', border:'#533483', red:'#e94560' },
  forest:     { bg:'#1b2a1b', fg:'#a8d5a2', panel:'#142414', border:'#3d6b3d', red:'#7cb871' },
  ocean:      { bg:'#0b1a2c', fg:'#64d2ff', panel:'#091422', border:'#1e5074', red:'#4facfe' },
  ume:        { bg:'#271d2b', fg:'#f1d8e9', panel:'#1d1721', border:'#66475f', red:'#d98bb7' },
  terminal:   { bg:'#0b1110', fg:'#b8e6c1', panel:'#080d0c', border:'#28553a', red:'#6bd38b' },
  organs:     { bg:'#0a0406', fg:'#efe1c8', panel:'#15080a', border:'#3a1519', red:'#c83240' },
  gpt:        { bg:'#212121', fg:'#ececec', panel:'#171717', border:'#424242', red:'#949494',
                advanced: { sendBtnBg: '#949494', sendBtnHover: '#7f7f7f',
                            userBubbleBg: '#2f2f2f', aiBubbleBg: '#171717',
                            inputBg: '#2f2f2f', brandColor: '#ffffff', brandMixTo: '#ffffff' } },
  claude:     { bg:'#1f1e1b', fg:'#f5f1e8', panel:'#2b2926', border:'#514b43', red:'#d97757' },
  cute:       { bg:'#fff4f7', fg:'#63394d', panel:'#fffafd', border:'#edc7d5', red:'#d65f88' },
  eclipse:    { bg:'#17191c', fg:'#e8e4dc', panel:'#0f1113', border:'#3c4248', red:'#46c2b3' },
  porcelain:  { bg:'#edf0f2', fg:'#252a30', panel:'#ffffff', border:'#c7cdd2', red:'#3478c9' },
  arcade:     { bg:'#15131d', fg:'#f4e85c', panel:'#0b1720', border:'#305f68', red:'#ff4f91' },
  blueprint:  { bg:'#10263b', fg:'#e8f1f5', panel:'#091a29', border:'#48758d', red:'#e6b84a' },
  monolith:   { bg:'#202020', fg:'#d8d8d2', panel:'#121212', border:'#50504b', red:'#b6e05c' },
  yoyo:       { bg:'#211f23', fg:'#dfdbd7', panel:'#141415', border:'#3e5146', red:'#e09d6c' },
};

const DEFAULT_THEME = 'dark';
const THEME_DISPLAY_NAMES = { ocean: 'starfield' };



const FONT_MAP = {
  mono: "'Fira Code', monospace",
  sans: "system-ui, -apple-system, 'Segoe UI', sans-serif",
  serif: "Georgia, 'Times New Roman', serif",
  opendyslexic: "'OpenDyslexic', sans-serif",
};
const DEFAULT_FONT = 'mono';

// Compatibility for persisted themes that selected the omitted custom face.
function normalizeFont(font) {
  return !font || font === 'gohu' || font === 'GohuFont' ? DEFAULT_FONT : font;
}
const DEFAULT_DENSITY = 'comfortable';
const MAX_CUSTOM_THEMES = 8;

// Default background patterns for built-in themes
const THEME_DEFAULT_PATTERN = {
  dark:       'none',
  light:      'dots',
  midnight:   'rain',
  cyberpunk:  'synapse',
  retrowave:  'embers',
  forest:     'petals',
  ocean:      'starfield-depth',
  terminal:   'ascii-fireflies',
  organs:     'rain',
  ume:        'petals',
  cute:       'sparkles',
  eclipse:    'starfield-depth',
  porcelain:  'dots',
  arcade:     'synapse',
  blueprint:  'dots',
  monolith:   'perlin-flow',
  yoyo:       'ascii-fireflies',
};

// Default effect colors for specific themes (overrides --fg)
const THEME_DEFAULT_EFFECT_COLOR = {
  midnight:   '#ffffff',
  cyberpunk:  '#62c4c7',
  organs:     '#451616',
  cute:       '#ff8cb8',
  ume:        '#f5a0c0',
  eclipse:    '#46c2b3',
  arcade:     '#ff4f91',
  blueprint:  '#e6b84a',
  monolith:   '#b6e05c',
  yoyo:       '#b8e6c1',
};

// Default effect intensity (0..1) per theme. Any theme not listed defaults to 1.
const THEME_DEFAULT_INTENSITY = {
  midnight:   0.5,
  cyberpunk:  0.55,
  terminal:   0.8,
  yoyo:       0.8,
  organs:     0.75,
};

const THEME_DEFAULT_SIZE = {
  organs:     0.75,
};

const THEME_DEFAULT_SPEED = {
  organs:     0.05,
};

// Default frosted-glass state per theme. Themes not listed default to false.
const THEME_DEFAULT_FROSTED = {
};

// ── Custom theme persistence ──
function _loadCustomThemes() {
  const themes = storageGetJSON(CUSTOM_THEMES_KEY, {});
  for (const theme of Object.values(themes)) {
    if (theme && theme.font) theme.font = normalizeFont(theme.font);
  }
  return themes;
}
function _saveCustomThemes(obj) {
  storageSetJSON(CUSTOM_THEMES_KEY, obj);
}
function saveCustomTheme(name, colors, opts) {
  const ct = _loadCustomThemes();
  // Enforce limit — allow overwriting existing, block new past max
  if (!ct[name] && Object.keys(ct).length >= MAX_CUSTOM_THEMES) {
    return 'limit';
  }
  const entry = { ...colors };
  if (opts) {
    if (opts.font) entry.font = normalizeFont(opts.font);
    if (opts.density) entry.density = opts.density;
    if (opts.bgPattern) entry.bgPattern = opts.bgPattern;
    if (opts.bgEffectColor) entry.bgEffectColor = opts.bgEffectColor;
    if (opts.bgEffectIntensity !== undefined) entry.bgEffectIntensity = opts.bgEffectIntensity;
    if (opts.bgEffectSize !== undefined) entry.bgEffectSize = opts.bgEffectSize;
    if (opts.bgEffectSpeed !== undefined) entry.bgEffectSpeed = opts.bgEffectSpeed;
    if (opts.frosted !== undefined) entry.frosted = !!opts.frosted;
  }
  ct[name] = entry;
  _saveCustomThemes(ct);
  initThemeUI._bound = false;
  initThemeUI();
  return 'ok';
}
function deleteCustomTheme(name) {
  const ct = _loadCustomThemes();
  delete ct[name];
  _saveCustomThemes(ct);
  initThemeUI._bound = false;
  initThemeUI();
}


// --- Syntax color derivation from theme base colors ---
function hexToHSL(hex) {
  const rgb = hexToRgb(hex) || { r: 0, g: 0, b: 0 };
  const r = rgb.r / 255;
  const g = rgb.g / 255;
  const b = rgb.b / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let h, s, l = (max + min) / 2;
  if (max === min) { h = s = 0; }
  else {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
    else if (max === g) h = ((b - r) / d + 2) / 6;
    else h = ((r - g) / d + 4) / 6;
  }
  return [h * 360, s * 100, l * 100];
}

function hslToHex(h, s, l) {
  h = ((h % 360) + 360) % 360;
  s = Math.max(0, Math.min(100, s)) / 100;
  l = Math.max(0, Math.min(100, l)) / 100;
  const a = s * Math.min(l, 1 - l);
  const f = n => { const k = (n + h / 30) % 12; return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)); };
  const toHex = v => Math.round(v * 255).toString(16).padStart(2, '0');
  return '#' + toHex(f(0)) + toHex(f(8)) + toHex(f(4));
}

function deriveSyntaxColors(colors) {
  const [fgH, fgS, fgL] = hexToHSL(colors.fg);
  const [bgH, bgS, bgL] = hexToHSL(colors.bg);
  const [redH, redS, redL] = hexToHSL(colors.red || '#e06c75');
  const isDark = bgL < 50;
  const codeBgL = isDark ? Math.max(bgL - 4, 0) : Math.min(bgL + 4, 100);
  return {
    bg: hslToHex(bgH, bgS, codeBgL),
    fg: colors.fg,
    keyword: hslToHex((redH + 280) % 360, Math.min(redS + 10, 80), isDark ? 70 : 45),
    string: hslToHex(40, Math.min(fgS + 20, 70), isDark ? 72 : 42),
    comment: hslToHex(fgH, Math.max(fgS - 20, 5), isDark ? (fgL * 0.5 + bgL * 0.5) : (fgL * 0.5 + bgL * 0.5)),
    function: hslToHex(210, Math.min(fgS + 20, 75), isDark ? 70 : 45),
    // Extra token colors for richer highlighting
    number: hslToHex(20, Math.min(fgS + 15, 65), isDark ? 68 : 48),
    builtin: hslToHex(180, Math.min(fgS + 15, 60), isDark ? 65 : 40),
    variable: hslToHex((fgH + 30) % 360, Math.min(fgS + 5, 60), isDark ? fgL : fgL),
    params: hslToHex(fgH, Math.max(fgS - 5, 10), isDark ? Math.min(fgL + 8, 85) : Math.max(fgL - 8, 25)),
  };
}

// Advanced picker key → CSS variable mapping
const ADV_KEYS = [
  { key: 'userBubbleBg',       css: '--user-bubble-bg',    label: 'User Chat Bubble', group: 'Chat Bubbles' },
  { key: 'aiBubbleBg',         css: '--ai-bubble-bg',      label: 'AI Chat Bubble',   group: 'Chat Bubbles' },
  { key: 'bubbleBorder',       css: '--bubble-border',     label: 'Border Chat Bubble', group: 'Chat Bubbles' },
  { key: 'sidebarBg',          css: '--sidebar-bg',        label: 'Sidebar Bg',       group: 'Sidebar' },
  { key: 'brandColor',         css: '--brand-color',       label: 'Odysseus Logo',    group: 'Sidebar' },
  { key: 'brandMixTo',         css: '--brand-mix-to',      label: 'Logo Gradient End', group: 'Sidebar' },
  { key: 'hamburgerColor',     css: '--hamburger-color',   label: 'Hamburger Menu',   group: 'Sidebar' },
  { key: 'inputBg',            css: '--input-bg',          label: 'Input Bg',         group: 'Chat Input / Prompt Area' },
  { key: 'inputBorder',        css: '--input-border',      label: 'Input Border',     group: 'Chat Input / Prompt Area' },
  { key: 'sendBtnBg',          css: '--send-btn-bg',       label: 'Send Btn',         group: 'Chat Input / Prompt Area' },
  { key: 'sendBtnHover',       css: '--send-btn-hover',    label: 'Send Hover',       group: 'Chat Input / Prompt Area' },
  { key: 'codeBg',             css: '--code-bg',           label: 'Code Bg',          group: 'Code Blocks' },
  { key: 'codeFg',             css: '--code-fg',           label: 'Code Text',        group: 'Code Blocks' },
  { key: 'toggleActive',       css: '--toggle-active',     label: 'Toggle On',        group: 'Controls' },
];

function computeAdvancedDefaults(colors) {
  const syn = deriveSyntaxColors(colors);
  const red = colors.red || '#e06c75';
  return {
    userBubbleBg: colors.bg,
    aiBubbleBg: colors.panel,
    bubbleBorder: colors.border,
    sidebarBg: colors.panel,
    brandColor: red,
    brandMixTo: colors.fg,
    hamburgerColor: colors.fg,
    inputBg: colors.panel,
    inputBorder: colors.border,
    sendBtnBg: red,
    sendBtnHover: red,
    codeBg: syn.bg,
    codeFg: syn.fg,
    toggleActive: red,
  };
}

function generateHarmonyColors(accentHex, harmonyType, mode) {
  const [h, s] = hexToHSL(accentHex);
  const isDark = mode === 'dark';

  let bgH, bgS, bgL, fgS, fgL, panelL, borderH, borderS, borderL;

  if (harmonyType === 'complementary') {
    bgH = h; bgS = Math.max(s * 0.15, 3);
    bgL = isDark ? 13 : 95; fgL = isDark ? 85 : 15; fgS = Math.max(s * 0.2, 5);
    panelL = isDark ? 8 : 98;
    borderH = h; borderS = Math.max(s * 0.25, 8); borderL = isDark ? 28 : 75;
  } else if (harmonyType === 'analogous') {
    bgH = (h - 30 + 360) % 360; bgS = Math.max(s * 0.12, 3);
    bgL = isDark ? 14 : 95; fgL = isDark ? 84 : 18; fgS = Math.max(s * 0.15, 5);
    panelL = isDark ? 9 : 97;
    borderH = (h + 30) % 360; borderS = Math.max(s * 0.3, 10); borderL = isDark ? 30 : 72;
  } else if (harmonyType === 'triadic') {
    bgH = (h + 240) % 360; bgS = Math.max(s * 0.1, 2);
    bgL = isDark ? 13 : 96; fgL = isDark ? 86 : 14; fgS = Math.max(s * 0.18, 5);
    panelL = isDark ? 8 : 99;
    borderH = (h + 120) % 360; borderS = Math.max(s * 0.2, 8); borderL = isDark ? 28 : 74;
  } else { // monochromatic
    bgH = h; bgS = Math.max(s * 0.08, 2);
    bgL = isDark ? 12 : 96; fgL = isDark ? 87 : 13; fgS = Math.max(s * 0.15, 5);
    panelL = isDark ? 7 : 99;
    borderH = h; borderS = Math.max(s * 0.2, 6); borderL = isDark ? 26 : 76;
  }

  return {
    bg: hslToHex(bgH, bgS, bgL),
    fg: hslToHex(h, fgS, fgL),
    panel: hslToHex(bgH, bgS * 0.6, panelL),
    border: hslToHex(borderH, borderS, borderL),
    red: accentHex,
  };
}

function applyColors(colors) {
  const s = document.documentElement.style;
  s.setProperty('--bg', colors.bg);
  s.setProperty('--fg', colors.fg);
  s.setProperty('--panel', colors.panel);
  s.setProperty('--border', colors.border);
  if (colors.red) s.setProperty('--red', colors.red);

  // Keep the mobile browser toolbar / status bar matched to the theme bg
  // (same as the early head-script does on first paint).
  const _mtc = document.querySelector('meta[name="theme-color"]');
  if (_mtc && colors.bg) _mtc.setAttribute('content', colors.bg);

  // Derive and apply syntax highlighting colors
  const syn = deriveSyntaxColors(colors);
  s.setProperty('--hl-bg', syn.bg);
  s.setProperty('--hl-fg', syn.fg);
  s.setProperty('--hl-keyword', syn.keyword);
  s.setProperty('--hl-string', syn.string);
  s.setProperty('--hl-comment', syn.comment);
  s.setProperty('--hl-function', syn.function);
  s.setProperty('--hl-number', syn.number);
  s.setProperty('--hl-builtin', syn.builtin);
  s.setProperty('--hl-variable', syn.variable);
  s.setProperty('--hl-params', syn.params);

  // Apply advanced overrides (or defaults)
  const adv = colors.advanced || {};
  const defaults = computeAdvancedDefaults(colors);
  for (const { key, css } of ADV_KEYS) {
    s.setProperty(css, adv[key] || defaults[key]);
  }

  // Keep semantic accent aliases in lockstep with theme red
  const accent = colors.red || '#e06c75';
  s.setProperty('--color-accent', accent);
  s.setProperty('--accent', accent);
  s.setProperty('--accent-primary', accent);

  // Update favicon to match theme accent color
  _updateFavicon(accent);
}

function _updateFavicon(fg) {
  try {
    const color = fg || '#e06c75';
    const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><circle cx='16' cy='16' r='10' fill='${color}'/></svg>`;
    const href = 'data:image/svg+xml,' + encodeURIComponent(svg);
    let link = document.querySelector("link[rel='icon']");
    if (!link) {
      link = document.createElement('link');
      link.rel = 'icon';
      link.type = 'image/svg+xml';
      if (document.head) document.head.appendChild(link);
    }
    link.href = href;
  } catch (_) {
    /* favicon is optional — never block theme apply */
  }
}

// Cache of discovered custom fonts: { "Family Name": [ {file, url, format} ] }
let _customFonts = {};
// Track which custom font families already have @font-face injected
const _injectedFonts = new Set();

function _injectFontFace(familyName, variants) {
  if (_injectedFonts.has(familyName)) return;
  const style = document.createElement('style');
  style.dataset.customFont = familyName;
  const fmtMap = { woff2: 'woff2', woff: 'woff', ttf: 'truetype', otf: 'opentype' };
  for (const v of variants) {
    style.textContent += `@font-face { font-family: '${familyName}'; src: url('${v.url}') format('${fmtMap[v.format] || v.format}'); font-display: swap; }\n`;
  }
  document.head.appendChild(style);
  _injectedFonts.add(familyName);
}

function applyFontDensity(font, density) {
  const f = normalizeFont(font);
  const d = density || DEFAULT_DENSITY;
  let family = FONT_MAP[f];
  if (!family && _customFonts[f]) {
    // It's a custom font from the local folder
    _injectFontFace(f, _customFonts[f]);
    family = "'" + f + "', sans-serif";
  }
  if (!family) family = FONT_MAP[DEFAULT_FONT];
  document.documentElement.style.setProperty('--font-family', family);
  document.documentElement.classList.remove('density-compact', 'density-spacious');
  if (d !== 'comfortable') document.documentElement.classList.add('density-' + d);
}

const _BG_CLASSES = ['bg-pattern-dots',
  'bg-pattern-synapse', 'bg-pattern-rain', 'bg-pattern-constellations',
  'bg-pattern-perlin-flow',
  'bg-pattern-petals', 'bg-pattern-sparkles', 'bg-pattern-embers',
  'bg-pattern-starfield-depth', 'bg-pattern-ascii-fireflies'];
const _CANVAS_PATTERNS = { synapse: _initSynapse, rain: _initRain, constellations: _initConstellations,
  'perlin-flow': _initPerlinFlow,
  petals: _initPetals, sparkles: _initSparkles, embers: _initEmbers,
  'starfield-depth': _initStarfieldDepth, 'ascii-fireflies': _initAsciiFireflies };

function applyBgEffectColor(color) {
  document.documentElement.style.setProperty('--bg-effect-color', color || '');
}

function applyBgEffectIntensity(v) {
  // v is 0..1. Default 1 (full intensity) when missing.
  const n = (v === undefined || v === null || isNaN(v)) ? 1 : Math.max(0, Math.min(1, Number(v)));
  document.documentElement.style.setProperty('--bg-effect-intensity', String(n));
}

function applyBgEffectSize(v) {
  // v is a multiplier 0.3..2.5. Default 1 when missing.
  const n = (v === undefined || v === null || isNaN(v)) ? 1 : Math.max(0.2, Math.min(3, Number(v)));
  document.documentElement.style.setProperty('--bg-effect-size', String(n));
}

function applyBgEffectSpeed(v) {
  // v is a multiplier 0.05..2.5. Canvas effects read this every frame.
  const n = (v === undefined || v === null || isNaN(v)) ? 1 : Math.max(0.05, Math.min(2.5, Number(v)));
  document.documentElement.style.setProperty('--bg-effect-speed', String(n));
}

/** Toggle the global "frosted glass" look — applies a translucent + blurred
 *  treatment to every panel, sidebar, modal, dropdown, and popover via CSS
 *  rules scoped to `body.theme-frosted`. */
function applyFrostedGlass(on) {
  document.body.classList.toggle('theme-frosted', !!on);
}

// Read current size multiplier for JS effects (canvas-based).
function _getEffectSize() {
  const v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--bg-effect-size'));
  return isNaN(v) ? 1 : v;
}

function _getEffectSpeed() {
  const v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--bg-effect-speed'));
  return isNaN(v) ? 1 : v;
}

// Patterns where the intensity/size sliders have no visible effect.
const _STATIC_PATTERNS = new Set(['none', 'dots']);

function applyBgPattern(pattern) {
  const p = pattern || 'none';
  document.body.classList.remove(..._BG_CLASSES);
  // Clean up any canvas backgrounds
  document.querySelectorAll('#synapse-canvas, #rain-canvas, #constellations-canvas, #perlin-flow-canvas, #petals-canvas, #sparkles-canvas, #embers-canvas, #starfield-depth-canvas, #ascii-fireflies-background').forEach(c => c.remove());
  if (p !== 'none') document.body.classList.add('bg-pattern-' + p);
  if (_CANVAS_PATTERNS[p]) {
    const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!reduced) _CANVAS_PATTERNS[p]();
  }
  // Hide sliders that do nothing on static patterns.
  const hide = _STATIC_PATTERNS.has(p);
  const ig = document.getElementById('theme-bg-intensity-group');
  const sg = document.getElementById('theme-bg-size-group');
  const spg = document.getElementById('theme-bg-speed-group');
  if (ig) ig.style.display = hide ? 'none' : '';
  if (sg) sg.style.display = hide ? 'none' : '';
  if (spg) spg.style.display = hide ? 'none' : '';
}

function getSaved() {
  const obj = storageGetJSON(LS_KEY, null);
  if (!obj || !obj.colors) {
    return { name: DEFAULT_THEME, colors: { ...THEMES[DEFAULT_THEME] } };
  }
  // Migration: 'chatgpt' preset was renamed to 'gpt'
  if (obj && obj.name === 'chatgpt') obj.name = 'gpt';
  // Migration: 'sakura' preset was renamed to 'ume'
  if (obj && obj.name === 'sakura') obj.name = 'ume';
  // Migrate retired built-ins to their replacement themes, including colors.
  const replacement = { paper: 'monolith', copper: 'arcade', lavender: 'porcelain' };
  if (obj && replacement[obj.name] && THEMES[replacement[obj.name]]) {
    obj.name = replacement[obj.name];
    obj.colors = { ...THEMES[obj.name] };
  }
  if (obj && obj.font) obj.font = normalizeFont(obj.font);
  return obj;
}

function save(name, colors, opts) {
  const obj = { name, colors };
  if (opts) {
    if (normalizeFont(opts.font) !== DEFAULT_FONT) obj.font = normalizeFont(opts.font);
    if (opts.density && opts.density !== DEFAULT_DENSITY) obj.density = opts.density;
    if (opts.bgPattern && opts.bgPattern !== 'none') obj.bgPattern = opts.bgPattern;
    if (opts.bgEffectColor) obj.bgEffectColor = opts.bgEffectColor;
    if (opts.bgEffectIntensity !== undefined && opts.bgEffectIntensity !== 1) obj.bgEffectIntensity = opts.bgEffectIntensity;
    if (opts.bgEffectSize !== undefined && opts.bgEffectSize !== 1) obj.bgEffectSize = opts.bgEffectSize;
    if (opts.bgEffectSpeed !== undefined && opts.bgEffectSpeed !== 1) obj.bgEffectSpeed = opts.bgEffectSpeed;
    if (opts.frosted) obj.frosted = true;
  }
  storageSetJSON(LS_KEY, obj);
}






function syncPickers(colors) {
  document.getElementById('clr-bg').value = colors.bg;
  document.getElementById('clr-fg').value = colors.fg;
  document.getElementById('clr-panel').value = colors.panel;
  document.getElementById('clr-border').value = colors.border;
  document.getElementById('clr-red').value = colors.red;
  syncAdvancedPickers(colors);
}


function syncAdvancedPickers(colors) {
  const adv = colors.advanced || {};
  const defaults = computeAdvancedDefaults(colors);
  for (const { key } of ADV_KEYS) {
    const el = document.getElementById('adv-' + key);
    if (el) el.value = adv[key] || defaults[key];
  }
}

function initThemeUI() {
  if (initThemeUI._bound) return;
  initThemeUI._bound = true;
  if (typeof document.querySelectorAll !== 'function') return;

  function el(primary, legacy) {
    return document.getElementById(primary) || (legacy ? document.getElementById(legacy) : null);
  }

  const COLOR_KEYS = ["bg", "fg", "panel", "border", "red"];
  function colorInput(key) {
    return el("theme-color-" + key, "clr-" + key);
  }

  function readColorsFromPickers() {
    const colors = {};
    for (const k of COLOR_KEYS) {
      const inp = colorInput(k);
      if (inp && inp.value) colors[k] = inp.value;
    }
    const saved = getSaved();
    return Object.keys(colors).length ? colors : (saved && saved.colors) || { ...THEMES[DEFAULT_THEME] };
  }

  function readOptsFromUI() {
    const opts = {};
    const fontEl = el("theme-font", "theme-font-select");
    const densityEl = el("theme-density", "theme-density-select");
    const patternEl = el("theme-bg-pattern", "theme-bg-pattern-select");
    const effectColorEl = el("theme-bg-effect-color");
    const intensityEl = el("theme-bg-intensity");
    const sizeEl = el("theme-bg-size");
    const speedEl = el("theme-bg-speed");
    const frostedEl = el("theme-frosted", "theme-frosted-toggle");
    if (fontEl) opts.font = fontEl.value;
    if (densityEl) opts.density = densityEl.value;
    if (patternEl) opts.bgPattern = patternEl.value;
    if (effectColorEl && effectColorEl.value) opts.bgEffectColor = effectColorEl.value;
    if (intensityEl && intensityEl.value !== "") opts.bgEffectIntensity = parseFloat(intensityEl.value) / 100;
    if (sizeEl && sizeEl.value !== "") opts.bgEffectSize = parseFloat(sizeEl.value) / 100;
    if (speedEl && speedEl.value !== "") opts.bgEffectSpeed = parseFloat(speedEl.value) / 100;
    if (frostedEl) opts.frosted = frostedEl.type === "checkbox" ? !!frostedEl.checked : frostedEl.value === "1" || frostedEl.value === "true";
    return opts;
  }

  function syncPickersToColors(colors) {
    if (!colors) return;
    for (const k of COLOR_KEYS) {
      const inp = colorInput(k);
      if (inp && colors[k]) inp.value = colors[k];
    }
  }

  function applyThemeName(name, colors, opts) {
    const ct = _loadCustomThemes();
    const base = colors || THEMES[name] || ct[name];
    if (!base) return;
    applyColors(base);
    const mergedOpts = opts || {};
    const f = normalizeFont(mergedOpts.font || (ct[name] && ct[name].font));
    const d = mergedOpts.density || (ct[name] && ct[name].density) || DEFAULT_DENSITY;
    const p = mergedOpts.bgPattern || (ct[name] && ct[name].bgPattern) || THEME_DEFAULT_PATTERN[name] || "none";
    const ec = mergedOpts.bgEffectColor || (ct[name] && ct[name].bgEffectColor) || THEME_DEFAULT_EFFECT_COLOR[name] || "";
    const ei = mergedOpts.bgEffectIntensity !== undefined ? mergedOpts.bgEffectIntensity
      : (ct[name] && ct[name].bgEffectIntensity !== undefined) ? ct[name].bgEffectIntensity
      : (THEME_DEFAULT_INTENSITY[name] !== undefined ? THEME_DEFAULT_INTENSITY[name] : 1);
    const sz = mergedOpts.bgEffectSize !== undefined ? mergedOpts.bgEffectSize
      : (ct[name] && ct[name].bgEffectSize !== undefined) ? ct[name].bgEffectSize
      : (THEME_DEFAULT_SIZE[name] !== undefined ? THEME_DEFAULT_SIZE[name] : 1);
    const sp = mergedOpts.bgEffectSpeed !== undefined ? mergedOpts.bgEffectSpeed
      : (ct[name] && ct[name].bgEffectSpeed !== undefined) ? ct[name].bgEffectSpeed
      : (THEME_DEFAULT_SPEED[name] !== undefined ? THEME_DEFAULT_SPEED[name] : 1);
    const fr = mergedOpts.frosted !== undefined ? !!mergedOpts.frosted
      : (ct[name] && ct[name].frosted !== undefined) ? !!ct[name].frosted
      : (THEME_DEFAULT_FROSTED[name] === true);
    applyFontDensity(f, d);
    applyBgEffectColor(ec);
    applyBgEffectIntensity(ei);
    applyBgEffectSize(sz);
    applyBgEffectSpeed(sp);
    applyFrostedGlass(fr);
    applyBgPattern(p);
    save(name, base, { font: f, density: d, bgPattern: p, bgEffectColor: ec, bgEffectIntensity: ei, bgEffectSize: sz, bgEffectSpeed: sp, frosted: fr });
    syncPickersToColors(base);
    const fontEl = el("theme-font", "theme-font-select");
    const densityEl = el("theme-density", "theme-density-select");
    const patternEl = el("theme-bg-pattern", "theme-bg-pattern-select");
    const effectColorEl = el("theme-bg-effect-color");
    const intensityEl = el("theme-bg-intensity");
    const sizeEl = el("theme-bg-size");
    const speedEl = el("theme-bg-speed");
    const frostedEl = el("theme-frosted", "theme-frosted-toggle");
    if (fontEl) fontEl.value = f;
    if (densityEl) densityEl.value = d;
    if (patternEl) patternEl.value = p;
    if (effectColorEl) effectColorEl.value = ec || base.fg || "#9cdef2";
    if (intensityEl) intensityEl.value = String(Math.round(ei * 100));
    if (sizeEl) sizeEl.value = String(Math.round(sz * 100));
    if (speedEl) speedEl.value = String(Math.round(sp * 100));
    if (frostedEl && frostedEl.type === "checkbox") frostedEl.checked = fr;
    document.querySelectorAll("[data-theme-preset], .theme-swatch[data-theme]").forEach((node) => {
      const n = node.getAttribute("data-theme-preset") || node.getAttribute("data-theme");
      node.classList.toggle("active", n === name);
    });
  }

  const closeBtn = el("close-theme-popup");
  if (closeBtn && !closeBtn.dataset.voxaBound) {
    closeBtn.dataset.voxaBound = "1";
    closeBtn.addEventListener("click", () => closePopup());
  }

  // Themes / Customize tabs (Odysseus layout)
  const themeTabs = document.getElementById("theme-tabs");
  if (themeTabs && !themeTabs.dataset.voxaBound) {
    themeTabs.dataset.voxaBound = "1";
    themeTabs.querySelectorAll(".admin-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        const target = tab.getAttribute("data-tab");
        themeTabs.querySelectorAll(".admin-tab").forEach((t) => t.classList.toggle("active", t === tab));
        document.querySelectorAll("#theme-popup .theme-tab-panel").forEach((panel) => {
          panel.style.display = panel.id === target ? "" : "none";
        });
      });
    });
  }

  const importBtn = el("theme-import-btn");
  const importArea = el("theme-import-area");
  const importActions = el("theme-import-actions");
  const importCancel = el("theme-import-cancel");
  if (importBtn && !importBtn.dataset.voxaBound) {
    importBtn.dataset.voxaBound = "1";
    importBtn.addEventListener("click", () => {
      if (importArea) importArea.classList.remove("hidden");
      if (importActions) importActions.classList.remove("hidden");
    });
  }
  if (importCancel && !importCancel.dataset.voxaBound) {
    importCancel.dataset.voxaBound = "1";
    importCancel.addEventListener("click", () => {
      if (importArea) { importArea.classList.add("hidden"); importArea.value = ""; }
      if (importActions) importActions.classList.add("hidden");
    });
  }

  const resetBtn = el("theme-reset-btn");
  if (resetBtn && !resetBtn.dataset.voxaBound) {
    resetBtn.dataset.voxaBound = "1";
    resetBtn.addEventListener("click", () => {
      applyThemeName(DEFAULT_THEME, { ...THEMES[DEFAULT_THEME] }, {
        font: DEFAULT_FONT,
        density: DEFAULT_DENSITY,
        bgPattern: THEME_DEFAULT_PATTERN[DEFAULT_THEME] || "none",
        frosted: false,
      });
    });
  }

  document.querySelectorAll("[data-theme-preset]").forEach((btn) => {
    if (btn.dataset.voxaBound) return;
    btn.dataset.voxaBound = "1";
    btn.addEventListener("click", () => {
      const name = btn.getAttribute("data-theme-preset");
      if (!name) return;
      applyThemeName(name, THEMES[name] ? { ...THEMES[name] } : null, {});
    });
  });

  const grid = document.getElementById("themeGrid");
  if (grid) {
    const saved = getSaved();
    const activeName = saved ? saved.name : DEFAULT_THEME;
    const customThemes = _loadCustomThemes();
    grid.innerHTML = Object.entries(THEMES).map(([name, c]) => `
      <div class="theme-swatch${name === activeName ? " active" : ""}" data-theme="${name}">
        <div class="theme-swatch-colors">
          <span style="background:${c.bg}"></span>
          <span style="background:${c.panel}"></span>
          <span style="background:${c.fg}"></span>
          <span style="background:${c.red}"></span>
        </div>
        ${name === "dark" ? "original" : (name === "gpt" ? "GPT" : (THEME_DISPLAY_NAMES[name] || name))}
      </div>`).join("");
    grid.querySelectorAll(".theme-swatch").forEach((sw) => {
      sw.addEventListener("click", (e) => {
        if (e.target.closest(".theme-delete-btn")) return;
        const name = sw.dataset.theme;
        // Do not pass Customize-tab pattern/font selects — they override
        // each preset's THEME_DEFAULT_PATTERN (e.g. midnight→rain).
        applyThemeName(name, THEMES[name] ? { ...THEMES[name] } : null, {});
      });
    });
    const userGrid = document.getElementById("themeUserGrid");
    const userCard = document.getElementById("themeUserCard");
    const customEntries = Object.entries(customThemes).filter(([name]) => !THEMES[name]);
    if (customEntries.length > 0 && userGrid && userCard) {
      userCard.style.display = "";
      userGrid.innerHTML = customEntries.map(([name, c]) => `
        <div class="theme-swatch${name === activeName ? " active" : ""}" data-theme="${name}" data-custom="1">
          <div class="theme-swatch-colors">
            <span style="background:${c.bg}"></span>
            <span style="background:${c.panel}"></span>
            <span style="background:${c.fg}"></span>
            <span style="background:${c.red}"></span>
          </div>
          <span class="theme-swatch-name">${name}</span>
          <button type="button" class="theme-delete-btn" data-delete="${name}" title="Delete theme">×</button>
        </div>`).join("");
      userGrid.querySelectorAll(".theme-delete-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const name = btn.dataset.delete;
          if (typeof confirm === "function" && !confirm('Delete theme "' + name + '"?')) return;
          deleteCustomTheme(name);
        });
      });
      userGrid.querySelectorAll(".theme-swatch").forEach((sw) => {
        sw.addEventListener("click", () => {
          const name = sw.dataset.theme;
          applyThemeName(name, customThemes[name], {});
        });
      });
    } else if (userCard) userCard.style.display = "none";
  }

  function bindChange(id, legacy, handler) {
    const node = el(id, legacy);
    if (!node || node.dataset.voxaBound) return;
    node.dataset.voxaBound = "1";
    node.addEventListener("change", handler);
    node.addEventListener("input", handler);
  }

  bindChange("theme-font", "theme-font-select", () => {
    const s = getSaved();
    applyFontDensity(el("theme-font", "theme-font-select").value, el("theme-density", "theme-density-select")?.value);
    if (s) save(s.name, s.colors, readOptsFromUI());
  });
  bindChange("theme-density", "theme-density-select", () => {
    const s = getSaved();
    applyFontDensity(el("theme-font", "theme-font-select")?.value, el("theme-density", "theme-density-select").value);
    if (s) save(s.name, s.colors, readOptsFromUI());
  });
  bindChange("theme-bg-pattern", "theme-bg-pattern-select", () => {
    applyBgPattern(el("theme-bg-pattern", "theme-bg-pattern-select").value);
    const s = getSaved();
    if (s) save(s.name, s.colors, readOptsFromUI());
  });
  bindChange("theme-bg-effect-color", null, () => {
    applyBgEffectColor(el("theme-bg-effect-color").value);
    const s = getSaved();
    if (s) save(s.name, s.colors, readOptsFromUI());
  });
  bindChange("theme-bg-intensity", null, () => {
    applyBgEffectIntensity(parseFloat(el("theme-bg-intensity").value) / 100);
    const s = getSaved();
    if (s) save(s.name, s.colors, readOptsFromUI());
  });
  bindChange("theme-bg-size", null, () => {
    applyBgEffectSize(parseFloat(el("theme-bg-size").value) / 100);
    const s = getSaved();
    if (s) save(s.name, s.colors, readOptsFromUI());
  });
  bindChange("theme-bg-speed", null, () => {
    applyBgEffectSpeed(parseFloat(el("theme-bg-speed").value) / 100);
    const s = getSaved();
    if (s) save(s.name, s.colors, readOptsFromUI());
  });
  bindChange("theme-frosted", "theme-frosted-toggle", () => {
    const fr = el("theme-frosted", "theme-frosted-toggle");
    applyFrostedGlass(fr.type === "checkbox" ? fr.checked : fr.value === "1");
    const s = getSaved();
    if (s) save(s.name, s.colors, readOptsFromUI());
  });

  COLOR_KEYS.forEach((key) => {
    const inp = colorInput(key);
    if (!inp || inp.dataset.voxaBound) return;
    inp.dataset.voxaBound = "1";
    inp.addEventListener("input", () => {
      const colors = readColorsFromPickers();
      applyColors(colors);
      const s = getSaved();
      save(s && s.name ? s.name : "custom", colors, readOptsFromUI());
    });
  });

  const saveBtn = el("theme-save-custom", "theme-save-go");
  const saveName = el("theme-save-custom-name", "theme-save-name");
  const saveError = el("theme-save-error");
  if (saveBtn) {
    saveBtn.addEventListener("click", () => {
      if (saveError) saveError.style.display = "none";
      const raw = saveName ? saveName.value.trim() : "";
      if (!raw) {
        if (saveError) { saveError.textContent = "Enter a name."; saveError.style.display = "block"; }
        return;
      }
      const slug = raw.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "");
      if (!slug) return;
      if (THEMES[slug]) {
        if (saveError) { saveError.textContent = "Cannot overwrite a built-in theme."; saveError.style.display = "block"; }
        return;
      }
      const colors = readColorsFromPickers();
      const opts = readOptsFromUI();
      const result = saveCustomTheme(slug, colors, opts);
      if (result === "limit") {
        if (saveError) { saveError.textContent = "Max " + MAX_CUSTOM_THEMES + " custom themes."; saveError.style.display = "block"; }
        return;
      }
      save(slug, colors, opts);
      if (saveName) saveName.value = "";
      initThemeUI._bound = false;
      initThemeUI();
    });
  }

  const deleteBtn = el("theme-delete-custom");
  if (deleteBtn) {
    deleteBtn.addEventListener("click", () => {
      const s = getSaved();
      if (!s || !s.name || THEMES[s.name]) return;
      if (typeof confirm === "function" && !confirm('Delete theme "' + s.name + '"?')) return;
      deleteCustomTheme(s.name);
      applyThemeName(DEFAULT_THEME, { ...THEMES[DEFAULT_THEME] }, {});
    });
  }

  const exportBtn = el("theme-export-btn");
  if (exportBtn) {
    exportBtn.addEventListener("click", () => {
      const cur = getSaved();
      const blob = new Blob([JSON.stringify(cur, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "voxa_" + (cur.name || "theme") + ".json";
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  const importGo = el("theme-import-go");
  if (importGo && importArea) {
    importGo.addEventListener("click", () => {
      let parsed;
      try { parsed = JSON.parse(importArea.value.trim()); }
      catch {
        if (saveError) { saveError.textContent = "Invalid JSON."; saveError.style.display = "block"; }
        return;
      }
      const colors = parsed.colors || parsed;
      const name = (parsed.name || "imported").toLowerCase().replace(/\s+/g, "-");
      const opts = {};
      ["font", "density", "bgPattern", "bgEffectColor", "bgEffectIntensity", "bgEffectSize", "bgEffectSpeed", "frosted"].forEach((k) => {
        if (parsed[k] !== undefined) opts[k === "bgPattern" ? "bgPattern" : k] = parsed[k];
      });
      const result = saveCustomTheme(name, colors, opts);
      if (result === "limit" && saveError) {
        saveError.textContent = "Max " + MAX_CUSTOM_THEMES + " custom themes.";
        saveError.style.display = "block";
        return;
      }
      applyThemeName(name, colors, opts);
    });
  }

  const saved = getSaved();
  if (saved && saved.name) {
    applyThemeName(saved.name, saved.colors, saved);
  }
}


// ── Zone highlighter ───────────────────────────────────────────────────
// Maps each color input id to a selector for the part of the UI it affects.
// When the user hovers the color row, we overlay a translucent box on the
// matching elements so it's obvious what's being edited.
const _THEME_ZONE_MAP = {
  'clr-bg':            'body',
  'clr-fg':            '.msg .body, .chat-input-bar',
  'clr-panel':         '.sidebar',
  'clr-border':        '.chat-input-bar, .sidebar, .msg .body',
  'clr-red':           '.send-btn, .icon-rail-btn.active',
  'theme-bg-effect-color': 'body',
  'adv-userBubbleBg':  '.msg.msg-user .body',
  'adv-aiBubbleBg':    '.msg.msg-ai .body',
  'adv-bubbleBorder':  '.msg .body',
  'adv-sidebarBg':     '.sidebar',
  'adv-sectionAccent': '.sidebar h4',
  'adv-brandColor':    '#sidebar-brand-btn',
  'adv-inputBg':       '#message',
  'adv-inputBorder':   '.chat-input-bar',
  'adv-sendBtnBg':     '.send-btn',
  'adv-sendBtnHover':  '.send-btn',
  'adv-codeBg':        'pre, code',
  'adv-codeFg':        'pre code, p code',
  'adv-toggleBg':      '.mode-toggle, .admin-switch',
  'adv-toggleActive':  '.mode-toggle-btn.active, .admin-switch input:checked + .admin-slider',
  'adv-accentPrimary': '.send-btn, .icon-rail-btn.active',
  'adv-accentError':   '.toast.error',
};

function _showThemeZoneHighlight(selector) {
  _clearThemeZoneHighlight();
  if (!selector) return;
  let els;
  try { els = document.querySelectorAll(selector); }
  catch { return; }
  els.forEach(el => {
    // Skip elements inside the theme modal — highlighting itself is noise.
    if (el.closest && el.closest('#theme-modal')) return;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    const overlay = document.createElement('div');
    overlay.className = 'theme-zone-highlight';
    overlay.style.top    = (r.top - 2) + 'px';
    overlay.style.left   = (r.left - 2) + 'px';
    overlay.style.width  = (r.width + 4) + 'px';
    overlay.style.height = (r.height + 4) + 'px';
    document.body.appendChild(overlay);
  });
}

function _clearThemeZoneHighlight() {
  document.querySelectorAll('.theme-zone-highlight').forEach(el => el.remove());
}

let _flashTimer = null;
function _flashAutosaved(label = 'Auto-saved') {
  let pill = document.getElementById('theme-autosaved-pill');
  if (!pill) {
    pill = document.createElement('div');
    pill.id = 'theme-autosaved-pill';
    pill.className = 'theme-autosaved-pill';
    pill.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg><span></span>';
    // Anchor inside the customize tab so it floats with the form.
    const customizeTab = document.getElementById('theme-tab-customize');
    (customizeTab || document.body).appendChild(pill);
  }
  const labelEl = pill.querySelector('span');
  if (labelEl) labelEl.textContent = label;
  pill.classList.add('visible');
  clearTimeout(_flashTimer);
  _flashTimer = setTimeout(() => pill.classList.remove('visible'), 1100);
}

// Wire hover-to-highlight on every color row inside the theme modal. Call
// once after the modal markup is in the DOM. Idempotent.
function initThemeZoneHighlight() {
  const root = document.getElementById('theme-tab-customize');
  if (!root || root.dataset.zoneBound === '1') return;
  root.dataset.zoneBound = '1';
  root.querySelectorAll('.color-row').forEach(row => {
    const input = row.querySelector('input[type="color"]');
    if (!input) return;
    const sel = _THEME_ZONE_MAP[input.id];
    if (!sel) return;
    row.addEventListener('mouseenter', () => _showThemeZoneHighlight(sel));
    row.addEventListener('mouseleave', _clearThemeZoneHighlight);
    // Also trigger when the picker actually opens (input focus)
    input.addEventListener('focus', () => _showThemeZoneHighlight(sel));
    input.addEventListener('blur', _clearThemeZoneHighlight);
  });
  // Clear highlight when the modal closes.
  const modal = document.getElementById('theme-modal');
  if (modal) {
    new MutationObserver(() => {
      if (modal.classList.contains('hidden')) _clearThemeZoneHighlight();
    }).observe(modal, { attributes: true, attributeFilter: ['class'] });
  }
}

// Generic draggable helper for fixed-position elements
// Thin wrapper around the shared makeWindowDraggable helper. Existing
// callers pass (el, handle) — `el` is what gets moved, `handle` is the
// drag handle. No fullscreen support (none of these consumers wanted it).
function makeDraggable(el, handle) {
  if (!el || !handle) return;
  const dockTarget = (el.closest && el.closest('.modal')) || el;
  const dragOptions = {
    content: el,
    header: handle,
    // Don't start a window-drag when the user grabs an interactive control
    // in the header — e.g. the theme opacity slider now lives next to the
    // title, and dragging its thumb must move the slider, not the window.
    skipSelector: 'button, input, select, .theme-opacity-wrap',
  };
  if (dockTarget && dockTarget.id === 'theme-modal') {
    dragOptions.onEnterFullscreen = () => {
      snapModalToZone(dockTarget, {
        name: 'fullscreen',
        rect: {
          left: 0,
          top: 0,
          width: window.innerWidth || document.documentElement.clientWidth || 0,
          height: window.innerHeight || document.documentElement.clientHeight || 0,
        },
      });
    };
  }
  makeWindowDraggable(dockTarget, dragOptions);
}

// Toggle the popup
function togglePopup() {
  const modal = document.getElementById('theme-modal');
  if (!modal) return;
  const visible = !modal.classList.contains('hidden');
  if (visible) {
    modal.classList.add('hidden');
  } else {
    modal.classList.remove('hidden');
  }
}


function applySaved() {
  const saved = getSaved();
  const name = saved.name || DEFAULT_THEME;
  const colors = saved.colors || THEMES[DEFAULT_THEME];
  applyColors(colors);
  const ct = _loadCustomThemes();
  const custom = ct[name];
  const f = normalizeFont(saved.font || (custom && custom.font));
  const d = saved.density || (custom && custom.density) || DEFAULT_DENSITY;
  const p = saved.bgPattern || (custom && custom.bgPattern) || THEME_DEFAULT_PATTERN[name] || 'none';
  const ec = saved.bgEffectColor || (custom && custom.bgEffectColor) || THEME_DEFAULT_EFFECT_COLOR[name] || '';
  const ei = saved.bgEffectIntensity !== undefined ? saved.bgEffectIntensity
    : (custom && custom.bgEffectIntensity !== undefined) ? custom.bgEffectIntensity
    : (THEME_DEFAULT_INTENSITY[name] !== undefined ? THEME_DEFAULT_INTENSITY[name] : 1);
  const sz = saved.bgEffectSize !== undefined ? saved.bgEffectSize
    : (custom && custom.bgEffectSize !== undefined) ? custom.bgEffectSize
    : (THEME_DEFAULT_SIZE[name] !== undefined ? THEME_DEFAULT_SIZE[name] : 1);
  const sp = saved.bgEffectSpeed !== undefined ? saved.bgEffectSpeed
    : (custom && custom.bgEffectSpeed !== undefined) ? custom.bgEffectSpeed
    : (THEME_DEFAULT_SPEED[name] !== undefined ? THEME_DEFAULT_SPEED[name] : 1);
  const fr = saved.frosted !== undefined ? !!saved.frosted
    : (custom && custom.frosted !== undefined) ? !!custom.frosted
    : (THEME_DEFAULT_FROSTED[name] === true);
  applyFontDensity(f, d);
  applyBgEffectColor(ec);
  applyBgEffectIntensity(ei);
  applyBgEffectSize(sz);
  applyBgEffectSpeed(sp);
  applyFrostedGlass(fr);
  applyBgPattern(p);
}

function openPopup() {
  const modal = document.getElementById('theme-modal');
  if (!modal) return;
  // Ensure presets are painted even if an earlier init aborted.
  if (!document.getElementById('themeGrid')?.innerHTML) {
    initThemeUI._bound = false;
  }
  initThemeUI();
  modal.classList.remove('hidden');
}

function closePopup() {
  const modal = document.getElementById('theme-modal');
  if (!modal) return;
  modal.classList.add('hidden');
  const content = modal.querySelector('.modal-content, #theme-popup');
  if (content) content.classList.remove('modal-closing');
}

// Expose for app.js wiring + AI ui_control
function getCustomThemes() { return _loadCustomThemes(); }

// ── Synapse background effect ──
// Uses the CSS grid pattern as base, overlays fast-moving small light pulses on grid lines
function _initSynapse() {
  if (document.getElementById('synapse-canvas')) return;
  const canvas = document.createElement('canvas');
  canvas.id = 'synapse-canvas';
  canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:0;';
  // Decorative background effect — hide from assistive tech so screen readers
  // don't announce an empty canvas and axe's "region" rule doesn't flag it.
  canvas.setAttribute('aria-hidden', 'true');
  document.body.prepend(canvas);
  const ctx = canvas.getContext('2d');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const GRID = 24; // matches CSS grid size
  const MAX_PULSES = 20;
  const SPEED_MIN = 2;
  const SPEED_MAX = 22;
  const TRAIL_LEN = 12; // pixels of trailing glow

  let W, H, cols, rows, pulses = [];

  function resize() {
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cols = Math.ceil(W / GRID); rows = Math.ceil(H / GRID);
  }
  resize();
  const _onResize = () => resize();
  window.addEventListener('resize', _onResize);

  function getColor() {
    const s = getComputedStyle(document.documentElement);
    return s.getPropertyValue('--bg-effect-color').trim() || s.getPropertyValue('--fg').trim() || '#9cdef2';
  }

  function spawnPulse() {
    const speed = SPEED_MIN + Math.random() * (SPEED_MAX - SPEED_MIN);
    if (Math.random() > 0.5) {
      // Horizontal — pick a grid row
      const row = Math.floor(Math.random() * (rows + 1));
      pulses.push({ x: -TRAIL_LEN, y: row * GRID, dx: speed, dy: 0 });
    } else {
      // Vertical — pick a grid column
      const col = Math.floor(Math.random() * (cols + 1));
      pulses.push({ x: col * GRID, y: -TRAIL_LEN, dx: 0, dy: speed });
    }
  }

  function draw() {
    if (!document.body.classList.contains('bg-pattern-synapse') || document.body.classList.contains('memory-disabled')) {
      window.removeEventListener('resize', _onResize);
      canvas.remove();
      return;
    }
    requestAnimationFrame(draw);
    ctx.clearRect(0, 0, W, H);
    const c = getColor();

    // Spawn
    if (pulses.length < MAX_PULSES && Math.random() < 0.12) spawnPulse();

    // Draw pulses as small bright dots with a short trail
    for (let i = pulses.length - 1; i >= 0; i--) {
      const p = pulses[i];
      const speedMult = _getEffectSpeed();
      p.x += p.dx * speedMult; p.y += p.dy * speedMult;

      // Off screen — remove
      if (p.x > W + TRAIL_LEN || p.y > H + TRAIL_LEN) { pulses.splice(i, 1); continue; }

      // Trail (line gradient fading behind the dot)
      const tx = p.x - (p.dx > 0 ? TRAIL_LEN : 0);
      const ty = p.y - (p.dy > 0 ? TRAIL_LEN : 0);
      const grad = ctx.createLinearGradient(tx, ty, p.x, p.y);
      grad.addColorStop(0, 'transparent');
      grad.addColorStop(1, c);
      ctx.strokeStyle = grad;
      ctx.globalAlpha = 0.35;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(tx, ty);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();

      // Bright dot at head
      ctx.globalAlpha = 0.55;
      ctx.fillStyle = c;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 1.2, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.globalAlpha = 1;
  }
  draw();
}

// ── Rain — thin vertical streaks falling ──
function _initRain() {
  if (document.getElementById('rain-canvas')) return;
  const canvas = document.createElement('canvas');
  canvas.id = 'rain-canvas';
  canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:0;';
  // Decorative background effect — hide from assistive tech so screen readers
  // don't announce an empty canvas and axe's "region" rule doesn't flag it.
  canvas.setAttribute('aria-hidden', 'true');
  document.body.prepend(canvas);
  const ctx = canvas.getContext('2d');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let W, H;
  const drops = [];
  const MAX_DROPS = 130;

  function resize() {
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  resize();
  const _onResize = () => resize();
  window.addEventListener('resize', _onResize);

  function getColor() {
    const s = getComputedStyle(document.documentElement);
    return s.getPropertyValue('--bg-effect-color').trim() || s.getPropertyValue('--fg').trim() || '#9cdef2';
  }

  function spawn() {
    const len = 20 + Math.random() * 40;
    const speed = 4 + Math.random() * 8;
    drops.push({ x: Math.random() * W, y: -len, len, speed, alpha: 0.32 + Math.random() * 0.28 });
  }

  function draw() {
    if (!document.body.classList.contains('bg-pattern-rain')) {
      window.removeEventListener('resize', _onResize);
      canvas.remove();
      return;
    }
    requestAnimationFrame(draw);
    ctx.clearRect(0, 0, W, H);
    const c = getColor();
    // Intensity also controls rain speed + spawn rate (feels slower/lighter when dim)
    const intenCss = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--bg-effect-intensity'));
    const inten = isNaN(intenCss) ? 1 : intenCss;
    const speedMult = (0.35 + inten * 0.65) * _getEffectSpeed();
    const sizeMult = _getEffectSize();

    if (drops.length < MAX_DROPS * inten && Math.random() < 0.6 * inten) spawn();

    for (let i = drops.length - 1; i >= 0; i--) {
      const d = drops[i];
      d.y += d.speed * speedMult;
      if (d.y > H + d.len * sizeMult) { drops.splice(i, 1); continue; }

      const effLen = d.len * sizeMult;
      const grad = ctx.createLinearGradient(d.x, d.y - effLen, d.x, d.y);
      grad.addColorStop(0, 'transparent');
      grad.addColorStop(1, c);
      ctx.strokeStyle = grad;
      ctx.globalAlpha = d.alpha;
      ctx.lineWidth = 1.3 * Math.min(2, Math.max(0.6, sizeMult));
      ctx.beginPath();
      ctx.moveTo(d.x, d.y - effLen);
      ctx.lineTo(d.x, d.y);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }
  draw();
}

// ── Constellations — static dots that slowly form/dissolve connecting lines ──
function _initConstellations() {
  if (document.getElementById('constellations-canvas')) return;
  const canvas = document.createElement('canvas');
  canvas.id = 'constellations-canvas';
  canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:0;';
  // Decorative background effect — hide from assistive tech so screen readers
  // don't announce an empty canvas and axe's "region" rule doesn't flag it.
  canvas.setAttribute('aria-hidden', 'true');
  document.body.prepend(canvas);
  const ctx = canvas.getContext('2d');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let W, H;
  const STAR_COUNT = 50;
  const CONNECT_DIST = 120;
  let stars = [];

  function resize() {
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (stars.length === 0) initStars();
  }

  function initStars() {
    stars = [];
    for (let i = 0; i < STAR_COUNT; i++) {
      stars.push({
        x: Math.random() * W, y: Math.random() * H,
        vx: (Math.random() - 0.5) * 0.15,
        vy: (Math.random() - 0.5) * 0.15,
        r: 0.8 + Math.random() * 0.8,
        phase: Math.random() * Math.PI * 2,
      });
    }
  }

  resize();
  const _onResize = () => { resize(); initStars(); };
  window.addEventListener('resize', _onResize);

  function getColor() {
    const s = getComputedStyle(document.documentElement);
    return s.getPropertyValue('--bg-effect-color').trim() || s.getPropertyValue('--fg').trim() || '#9cdef2';
  }

  let t = 0;
  function draw() {
    if (!document.body.classList.contains('bg-pattern-constellations')) {
      window.removeEventListener('resize', _onResize);
      canvas.remove();
      return;
    }
    requestAnimationFrame(draw);
    const speedMult = _getEffectSpeed();
    t += 0.01 * speedMult;
    ctx.clearRect(0, 0, W, H);
    const c = getColor();

    // Move stars gently
    for (const s of stars) {
      s.x += s.vx * speedMult; s.y += s.vy * speedMult;
      if (s.x < 0) s.x = W; if (s.x > W) s.x = 0;
      if (s.y < 0) s.y = H; if (s.y > H) s.y = 0;
    }

    // Draw connections
    ctx.strokeStyle = c;
    ctx.lineWidth = 0.5;
    for (let i = 0; i < stars.length; i++) {
      for (let j = i + 1; j < stars.length; j++) {
        const dx = stars[i].x - stars[j].x;
        const dy = stars[i].y - stars[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < CONNECT_DIST) {
          ctx.globalAlpha = (1 - dist / CONNECT_DIST) * 0.15;
          ctx.beginPath();
          ctx.moveTo(stars[i].x, stars[i].y);
          ctx.lineTo(stars[j].x, stars[j].y);
          ctx.stroke();
        }
      }
    }

    // Draw stars with subtle twinkle
    ctx.fillStyle = c;
    for (const s of stars) {
      const twinkle = 0.5 + 0.5 * Math.sin(t * 2 + s.phase);
      ctx.globalAlpha = 0.15 + twinkle * 0.25;
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
  draw();
}

// ── Noise helper for Perlin effects ──
function _bgNoise2d(x, y) { const n = Math.sin(x * 12.9898 + y * 78.233) * 43758.5453; return n - Math.floor(n); }
function _bgSmoothNoise(x, y) {
  const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
  const a = _bgNoise2d(ix, iy), b = _bgNoise2d(ix + 1, iy), cc = _bgNoise2d(ix, iy + 1), d = _bgNoise2d(ix + 1, iy + 1);
  const ux = fx * fx * (3 - 2 * fx), uy = fy * fy * (3 - 2 * fy);
  return a + (b - a) * ux + (cc - a) * uy + (a - b - cc + d) * ux * uy;
}

// ── Perlin Flow — colored particle streams ──
function _initPerlinFlow() {
  if (document.getElementById('perlin-flow-canvas')) return;
  const canvas = document.createElement('canvas');
  canvas.id = 'perlin-flow-canvas';
  canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:0;';
  // Decorative background effect — hide from assistive tech so screen readers
  // don't announce an empty canvas and axe's "region" rule doesn't flag it.
  canvas.setAttribute('aria-hidden', 'true');
  document.body.prepend(canvas);
  const ctx = canvas.getContext('2d');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let W, H, t = 0;
  const particles = [];
  function resize() {
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (particles.length === 0) for (let i = 0; i < 350; i++) particles.push({ x: Math.random() * W, y: Math.random() * H, life: Math.random() });
  }
  resize();
  const _onResize = () => resize();
  window.addEventListener('resize', _onResize);
  function getColor() { const s = getComputedStyle(document.documentElement); return s.getPropertyValue('--bg-effect-color').trim() || s.getPropertyValue('--fg').trim() || '#9cdef2'; }
  // Leave a small wake behind each particle, but clear it before the field
  // can build into a smoky layer.
  const trailLifetime = 400;
  let trails = [], previousTime = performance.now();
  function draw(now = performance.now()) {
    if (!canvas.isConnected || !document.body.classList.contains('bg-pattern-perlin-flow')) { window.removeEventListener('resize', _onResize); canvas.remove(); return; }
    requestAnimationFrame(draw);
    const step = Math.min(3, Math.max(0, now - previousTime) / (1000 / 60)) * _getEffectSpeed();
    previousTime = now;
    // Redraw only living marks: alpha blending old pixels leaves permanent
    // low-level residue on an 8-bit canvas, even after repeated fade passes.
    ctx.clearRect(0, 0, W, H);
    trails = trails.filter(mark => now - mark.time < trailLifetime);
    const c = getColor();
    particles.forEach(p => {
      const n = _bgSmoothNoise(p.x * 0.004 + t * 0.0008, p.y * 0.004 + 100);
      const angle = n * Math.PI * 6;
      const speed = 1 + _bgSmoothNoise(p.x * 0.003, p.y * 0.003 + 50) * 1.5;
      p.x += Math.cos(angle) * speed * step; p.y += Math.sin(angle) * speed * step; p.life -= 0.001 * step;
      if (p.life <= 0 || p.x < 0 || p.x > W || p.y < 0 || p.y > H) { p.x = Math.random() * W; p.y = Math.random() * H; p.life = 1; }
      trails.push({ x: p.x, y: p.y, alpha: p.life * 0.15 * Math.min(step, 1), time: now });
    });
    ctx.fillStyle = c;
    for (const mark of trails) {
      const remaining = 1 - (now - mark.time) / trailLifetime;
      ctx.globalAlpha = mark.alpha * remaining * remaining;
      ctx.beginPath(); ctx.arc(mark.x, mark.y, 1, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;
    t += step;
  }
  draw();
}

// ── Starfield Depth — falling particles with a short visible wake ──
function _initStarfieldDepth() {
  if (document.getElementById('starfield-depth-canvas')) return;
  const canvas = document.createElement('canvas');
  canvas.id = 'starfield-depth-canvas';
  canvas.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:0;';
  canvas.setAttribute('aria-hidden', 'true');
  document.body.prepend(canvas);
  const ctx = canvas.getContext('2d');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let W = 0, H = 0, stars = [];
  const resize = () => {
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (!stars.length) stars = Array.from({ length: 180 }, () => ({
      x: Math.random() * W, y: Math.random() * H, z: 0.15 + Math.random() * 0.9,
    }));
  };
  resize();
  window.addEventListener('resize', resize);
  const getBgFade = () => {
    const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
    const { r, g, b } = hexToRgb(bg) || { r: 0, g: 0, b: 0 };
    return `rgba(${r},${g},${b},0.72)`;
  };
  const draw = () => {
    if (!canvas.isConnected || !document.body.classList.contains('bg-pattern-starfield-depth')) {
      window.removeEventListener('resize', resize); canvas.remove(); return;
    }
    requestAnimationFrame(draw);
    ctx.fillStyle = getBgFade();
    ctx.fillRect(0, 0, W, H);
    const color = getComputedStyle(document.documentElement).getPropertyValue('--bg-effect-color').trim()
      || getComputedStyle(document.documentElement).getPropertyValue('--fg').trim();
    const size = _getEffectSize();
    for (const star of stars) {
      star.y += (0.12 + star.z * 0.8) * size * _getEffectSpeed();
      if (star.y > H + 4) { star.y = -4; star.x = Math.random() * W; }
      ctx.fillStyle = color;
      ctx.globalAlpha = (0.1 + star.z * 0.4) * (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--bg-effect-intensity')) || 1);
      ctx.beginPath(); ctx.arc(star.x, star.y, (0.35 + star.z * 1.3) * size, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;
  };
  draw();
}

// ── ASCII Fireflies — an animated scene sampled to a monochrome glyph grid ──
function _initAsciiFireflies() {
  if (document.getElementById('ascii-fireflies-background')) return;
  const output = document.createElement('pre');
  output.id = 'ascii-fireflies-background';
  output.setAttribute('aria-hidden', 'true');
  document.body.prepend(output);
  const source = document.createElement('canvas');
  const ctx = source.getContext('2d', { willReadFrequently: true });
  const ramp = ' .,:;irsXA253hMHGS#9B&@';
  let columns = 0, rows = 0, flies = [], tick = 0, lastRender = 0;
  const resize = () => {
    const metrics = document.createElement('span');
    metrics.style.cssText = 'position:fixed;visibility:hidden;font:12px ui-monospace';
    metrics.textContent = 'M'; document.body.append(metrics);
    const charWidth = metrics.getBoundingClientRect().width || 7;
    metrics.remove();
    columns = Math.max(40, Math.floor((window.innerWidth - 32) / charWidth));
    rows = Math.max(18, Math.floor((window.innerHeight - 28) / 12));
    source.width = columns * 2; source.height = rows * 2;
    flies = Array.from({ length: Math.min(135, Math.floor(columns * rows * 0.08)) }, (_, index) => {
      const depth = index < Math.min(90, Math.floor(columns * rows * 0.055)) ? Math.random() * 0.32 : 0.32 + Math.random() * 0.68;
      return { x: Math.random() * source.width, y: Math.random() * source.height, phase: Math.random() * 7, depth, drift: 0.02 + depth * 0.42, size: 0.4 + depth * 1.7 };
    });
  };
  resize();
  window.addEventListener('resize', resize);
  const draw = now => {
    if (!output.isConnected || !document.body.classList.contains('bg-pattern-ascii-fireflies')) {
      window.removeEventListener('resize', resize); output.remove(); return;
    }
    requestAnimationFrame(draw);
    if (now - lastRender < 66) return;
    lastRender = now; tick += _getEffectSpeed();
    const W = source.width, H = source.height;
    ctx.fillStyle = 'rgb(2,8,4)'; ctx.fillRect(0, 0, W, H);
    const vortices = [
      { x: W * (0.3 + Math.sin(tick * 0.002) * 0.08), y: H * (0.42 + Math.cos(tick * 0.0017) * 0.08), dir: 1 },
      { x: W * (0.72 + Math.cos(tick * 0.0015) * 0.06), y: H * (0.62 + Math.sin(tick * 0.0022) * 0.07), dir: -1 },
    ];
    for (const fly of flies) {
      const speed = 0.004 + fly.depth * 0.028;
      fly.x += Math.sin(tick * speed + fly.phase) * fly.drift;
      fly.y += Math.cos(tick * speed * 0.72 + fly.phase) * fly.drift * 0.55;
      for (const vortex of vortices) {
        const dx = fly.x - vortex.x, dy = fly.y - vortex.y, distance = Math.hypot(dx, dy) || 1;
        const turn = Math.max(0, 1 - distance / (W * 0.25)) * (0.006 + fly.depth * 0.052) * vortex.dir;
        fly.x += -dy / distance * turn; fly.y += dx / distance * turn;
      }
      if (fly.x < 0 || fly.x >= W || fly.y < 0 || fly.y >= H) { fly.x = Math.random() * W; fly.y = Math.random() * H; }
      const pulse = (Math.sin(tick * (0.018 + fly.depth * 0.045) + fly.phase) + 1) / 2;
      const brightness = 45 + Math.round((75 + fly.depth * 135) * pulse);
      ctx.fillStyle = `rgb(${brightness},${brightness},${brightness})`;
      ctx.beginPath(); ctx.arc(fly.x, fly.y, fly.size * (0.7 + pulse * 0.8), 0, Math.PI * 2); ctx.fill();
    }
    const pixels = ctx.getImageData(0, 0, W, H).data;
    const lines = [];
    for (let y = 0; y < rows; y++) {
      let line = '';
      for (let x = 0; x < columns; x++) {
        let luminosity = 0;
        for (let sampleY = 0; sampleY < 2; sampleY++) for (let sampleX = 0; sampleX < 2; sampleX++) luminosity += pixels[((y * 2 + sampleY) * W + x * 2 + sampleX) * 4];
        luminosity /= 4;
        line += ramp[Math.min(ramp.length - 1, Math.floor(luminosity / 256 * ramp.length))];
      }
      lines.push(line);
    }
    output.textContent = lines.join('\n');
  };
  requestAnimationFrame(draw);
}

// ── Petals — gentle falling flower petals ──
function _initPetals() {
  if (document.getElementById('petals-canvas')) return;
  const canvas = document.createElement('canvas');
  canvas.id = 'petals-canvas';
  canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:0;';
  // Decorative background effect — hide from assistive tech so screen readers
  // don't announce an empty canvas and axe's "region" rule doesn't flag it.
  canvas.setAttribute('aria-hidden', 'true');
  document.body.prepend(canvas);
  const ctx = canvas.getContext('2d');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let W, H;
  const petals = [];
  function makePetal() {
    return {
      x: Math.random() * W, y: -10 - Math.random() * 40,
      size: 3 + Math.random() * 5, rot: Math.random() * Math.PI * 2,
      vr: (Math.random() - 0.5) * 0.03, vy: 0.3 + Math.random() * 0.6,
      drift: Math.random() * Math.PI * 2, driftSpeed: 0.008 + Math.random() * 0.012,
      wobble: 0.3 + Math.random() * 0.8
    };
  }
  function resize() {
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (petals.length === 0) for (let i = 0; i < 30; i++) { const p = makePetal(); p.y = Math.random() * H; petals.push(p); }
  }
  resize();
  const _onResize = () => resize();
  window.addEventListener('resize', _onResize);
  function getColor() { const s = getComputedStyle(document.documentElement); return s.getPropertyValue('--bg-effect-color').trim() || s.getPropertyValue('--fg').trim() || '#9cdef2'; }
  function draw() {
    if (!document.body.classList.contains('bg-pattern-petals')) { window.removeEventListener('resize', _onResize); canvas.remove(); return; }
    requestAnimationFrame(draw);
    ctx.clearRect(0, 0, W, H);
    const c = getColor();
    const sz = _getEffectSize();
    petals.forEach(p => {
      const speedMult = _getEffectSpeed();
      p.y += p.vy * speedMult; p.rot += p.vr * speedMult; p.drift += p.driftSpeed * speedMult;
      p.x += Math.sin(p.drift) * p.wobble * speedMult;
      if (p.y > H + 15) Object.assign(p, makePetal());
      ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot);
      ctx.globalAlpha = 0.2;
      // petal shape — two overlapping ellipses
      ctx.fillStyle = c;
      ctx.beginPath(); ctx.ellipse(-p.size * 0.2 * sz, 0, p.size * 0.6 * sz, p.size * 0.3 * sz, 0.3, 0, Math.PI * 2); ctx.fill();
      ctx.globalAlpha = 0.15;
      ctx.beginPath(); ctx.ellipse(p.size * 0.2 * sz, 0, p.size * 0.6 * sz, p.size * 0.3 * sz, -0.3, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
    });
    ctx.globalAlpha = 1;
  }
  draw();
}

// ── Sparkles — twinkling star-shaped sparkles ──
function _initSparkles() {
  if (document.getElementById('sparkles-canvas')) return;
  const canvas = document.createElement('canvas');
  canvas.id = 'sparkles-canvas';
  canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:0;';
  // Decorative background effect — hide from assistive tech so screen readers
  // don't announce an empty canvas and axe's "region" rule doesn't flag it.
  canvas.setAttribute('aria-hidden', 'true');
  document.body.prepend(canvas);
  const ctx = canvas.getContext('2d');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let W, H;
  const sparkles = [];
  function makeSpark() {
    return { x: Math.random() * W, y: Math.random() * H, size: 2 + Math.random() * 5, phase: Math.random() * Math.PI * 2, speed: 0.015 + Math.random() * 0.03, life: 0.5 + Math.random() * 0.5 };
  }
  function resize() {
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (sparkles.length === 0) for (let i = 0; i < 35; i++) sparkles.push(makeSpark());
  }
  resize();
  const _onResize = () => resize();
  window.addEventListener('resize', _onResize);
  function getColor() { const s = getComputedStyle(document.documentElement); return s.getPropertyValue('--bg-effect-color').trim() || s.getPropertyValue('--fg').trim() || '#9cdef2'; }
  function drawStar(x, y, r, c, alpha) {
    ctx.save(); ctx.translate(x, y); ctx.fillStyle = c; ctx.globalAlpha = alpha;
    // 4-point star
    ctx.beginPath();
    ctx.moveTo(0, -r); ctx.quadraticCurveTo(r * 0.15, -r * 0.15, r, 0);
    ctx.quadraticCurveTo(r * 0.15, r * 0.15, 0, r);
    ctx.quadraticCurveTo(-r * 0.15, r * 0.15, -r, 0);
    ctx.quadraticCurveTo(-r * 0.15, -r * 0.15, 0, -r);
    ctx.fill();
    ctx.restore();
  }
  function draw() {
    if (!document.body.classList.contains('bg-pattern-sparkles')) { window.removeEventListener('resize', _onResize); canvas.remove(); return; }
    requestAnimationFrame(draw);
    ctx.clearRect(0, 0, W, H);
    const c = getColor();
    const sizeMult = _getEffectSize();
    sparkles.forEach(s => {
      s.phase += s.speed * _getEffectSpeed();
      const twinkle = Math.sin(s.phase);
      const alpha = Math.max(0, twinkle) * 0.25 * s.life;
      const scale = 0.5 + Math.max(0, twinkle) * 0.5;
      if (alpha > 0.01) drawStar(s.x, s.y, s.size * scale * sizeMult, c, alpha);
      // respawn when cycle completes
      if (s.phase > Math.PI * 6) Object.assign(s, makeSpark());
    });
    ctx.globalAlpha = 1;
  }
  draw();
}

// ── Embers — warm particles rising with glow and occasional spark bursts ──
function _initEmbers() {
  if (document.getElementById('embers-canvas')) return;
  const canvas = document.createElement('canvas');
  canvas.id = 'embers-canvas';
  canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:0;';
  // Decorative background effect — hide from assistive tech so screen readers
  // don't announce an empty canvas and axe's "region" rule doesn't flag it.
  canvas.setAttribute('aria-hidden', 'true');
  document.body.prepend(canvas);
  const ctx = canvas.getContext('2d');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let W, H;
  const embers = [];
  function makeEmber() {
    return {
      x: Math.random() * W,
      y: H + Math.random() * 40,
      vx: (Math.random() - 0.5) * 0.3,
      vy: -0.3 - Math.random() * 0.8,
      r: 0.3 + Math.random() * 0.6,
      life: 0,
      maxLife: 220 + Math.random() * 220,
      wobble: Math.random() * Math.PI * 2,
      spark: false,
    };
  }
  function resize() {
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (embers.length === 0) {
      for (let i = 0; i < 60; i++) { const e = makeEmber(); e.y = Math.random() * H; e.life = Math.random() * e.maxLife; embers.push(e); }
    }
  }
  resize();
  const _onResize = () => resize();
  window.addEventListener('resize', _onResize);
  function getColor() {
    const s = getComputedStyle(document.documentElement);
    return s.getPropertyValue('--bg-effect-color').trim() || s.getPropertyValue('--fg').trim() || '#c9a95a';
  }
  function rgba(hex, a) {
    const { r, g, b } = hexToRgb(hex) || { r: 0, g: 0, b: 0 };
    return `rgba(${r},${g},${b},${a})`;
  }
  function draw() {
    if (!document.body.classList.contains('bg-pattern-embers')) {
      window.removeEventListener('resize', _onResize);
      canvas.remove();
      return;
    }
    requestAnimationFrame(draw);
    // Fade previous frame (destination-out keeps canvas transparent where no embers)
    ctx.globalCompositeOperation = 'destination-out';
    ctx.fillStyle = 'rgba(0,0,0,0.18)';
    ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = 'lighter';
    const color = getColor();
    for (let i = embers.length - 1; i >= 0; i--) {
      const e = embers[i];
      const speedMult = _getEffectSpeed();
      e.wobble += 0.03 * speedMult;
      e.x += (e.vx + Math.sin(e.wobble) * 0.5) * speedMult;
      e.y += e.vy * speedMult;
      e.life += speedMult;
      if (e.life > e.maxLife || e.y < -20) {
        embers.splice(i, 1);
        if (embers.length < 70) embers.push(makeEmber());
        continue;
      }
      if (!e.spark && Math.random() < 0.003) e.spark = true;
      const lifeRatio = e.life / e.maxLife;
      const fade = Math.min(1, Math.min(lifeRatio * 4, (1 - lifeRatio) * 3));
      const sz = _getEffectSize();
      const r = e.r * (e.spark ? 2.4 : 1) * sz;
      const a = (e.spark ? 0.9 : 0.55) * fade;
      const g = ctx.createRadialGradient(e.x, e.y, 0, e.x, e.y, r * 4);
      g.addColorStop(0, rgba(color, a));
      g.addColorStop(0.4, rgba(color, a * 0.3));
      g.addColorStop(1, rgba(color, 0));
      ctx.fillStyle = g;
      ctx.fillRect(e.x - r * 4, e.y - r * 4, r * 8, r * 8);
      ctx.fillStyle = rgba('#ffffff', a * 0.6);
      ctx.beginPath();
      ctx.arc(e.x, e.y, r * 0.5, 0, Math.PI * 2);
      ctx.fill();
      e.spark = false;
    }
    if (Math.random() < 0.015) {
      const bx = Math.random() * W;
      for (let i = 0; i < 5; i++) {
        const e = makeEmber();
        e.x = bx + (Math.random() - 0.5) * 40;
        e.y = H - 10;
        e.vy *= 1.5;
        embers.push(e);
      }
    }
    ctx.globalCompositeOperation = 'source-over';
  }
  draw();
}

w.VoxaTheme = {
  THEMES,
  applyColors,
  applyFontDensity,
  applyBgPattern,
  applyBgEffectColor,
  applyBgEffectIntensity,
  applyBgEffectSize,
  applyBgEffectSpeed,
  applyFrostedGlass,
  getSaved,
  save,
  saveCustomTheme,
  deleteCustomTheme,
  initThemeUI,
  openPopup,
  closePopup,
  applySaved,
};

})(typeof window !== 'undefined' ? window : globalThis);
