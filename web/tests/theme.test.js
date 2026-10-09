// Run: node web/tests/theme.test.js
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(__dirname, "..", "js", "theme.js"), "utf8");
const store = new Map();
const sandbox = {
  window: {},
  document: {
    documentElement: { style: { setProperty() {}, removeProperty() {} }, classList: { add() {}, remove() {}, toggle() {} } },
    body: { classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } }, querySelectorAll() { return []; } },
    getElementById() { return null; },
    querySelector() { return null; },
    createElement() { return { style: {}, dataset: {}, textContent: "", appendChild() {} }; },
    head: { appendChild() {} },
  },
  localStorage: {
    getItem(k) { return store.has(k) ? store.get(k) : null; },
    setItem(k, v) { store.set(k, String(v)); },
    removeItem(k) { store.delete(k); },
  },
  matchMedia() { return { matches: false, addEventListener() {}, removeEventListener() {} }; },
  requestAnimationFrame() { return 0; },
  cancelAnimationFrame() {},
  URLSearchParams,
  console,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const T = sandbox.window.VoxaTheme;
assert.ok(T);

let n = 0;
function t(name, fn) { fn(); n += 1; console.log("ok -", name); }

t("dark preset has core colors", () => {
  assert.strictEqual(T.THEMES.dark.bg, "#282c34");
  assert.strictEqual(T.THEMES.dark.fg, "#9cdef2");
});

t("save/load round trip", () => {
  T.save("dark", T.THEMES.dark, { font: "mono", density: "comfortable", bgPattern: "none" });
  const got = T.getSaved();
  assert.strictEqual(got.name, "dark");
  assert.strictEqual(got.colors.bg, "#282c34");
});

t("corrupt JSON falls back", () => {
  store.set("voxachat-theme", "{not-json");
  const got = T.getSaved();
  assert.strictEqual(got.name, "dark");
});

t("custom theme max 8", () => {
  store.clear();
  for (let i = 0; i < 8; i++) {
    assert.strictEqual(T.saveCustomTheme("c" + i, T.THEMES.dark, {}), "ok");
  }
  assert.strictEqual(T.saveCustomTheme("c8", T.THEMES.dark, {}), "limit");
});

console.log("\n" + n + " passed");
