// Run: node web/tests/sidebar-layout.test.js
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const store = new Map();
const classes = new Set();
const classList = {
  add(c) {
    classes.add(c);
  },
  remove(c) {
    classes.delete(c);
  },
  toggle(c, on) {
    if (on === undefined) {
      if (classes.has(c)) classes.delete(c);
      else classes.add(c);
      return;
    }
    if (on) classes.add(c);
    else classes.delete(c);
  },
  contains(c) {
    return classes.has(c);
  },
};

const els = {};
function el(id, extra) {
  els[id] = Object.assign(
    {
      classList: {
        add(c) {
          this._c = this._c || new Set();
          this._c.add(c);
        },
        remove(c) {
          if (this._c) this._c.delete(c);
        },
        toggle(c, on) {
          if (!this._c) this._c = new Set();
          if (on === undefined) {
            if (this._c.has(c)) this._c.delete(c);
            else this._c.add(c);
            return;
          }
          if (on) this._c.add(c);
          else this._c.delete(c);
        },
        contains(c) {
          return this._c ? this._c.has(c) : false;
        },
      },
      className: "",
      style: {},
      textContent: "",
      hidden: false,
      addEventListener() {},
      setAttribute() {},
      getAttribute() {
        return null;
      },
    },
    extra || {}
  );
  return els[id];
}

el("hamburger-btn");
el("icon-rail");
el("app-sidebar");
el("sidebar-toggle-btn");
el("sidebar-backdrop");

const src = fs.readFileSync(
  path.join(__dirname, "..", "js", "sidebar-layout.js"),
  "utf8"
);
const sandbox = {
  window: { innerWidth: 1280, addEventListener() {} },
  document: {
    documentElement: { classList },
    getElementById(id) {
      return els[id] || null;
    },
    addEventListener() {},
  },
  localStorage: {
    getItem(k) {
      return store.has(k) ? store.get(k) : null;
    },
    setItem(k, v) {
      store.set(k, String(v));
    },
  },
  console,
};
sandbox.window = Object.assign(sandbox.window, sandbox);
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const S = sandbox.window.VoxaSidebar;

assert.ok(S, "VoxaSidebar must be defined");

let n = 0;
function t(name, fn) {
  fn();
  n += 1;
  console.log("ok -", name);
}

t("default full", () => {
  store.clear();
  classes.clear();
  S.init();
  assert.strictEqual(S.getMode(), "full");
});

t("desktop toggle full <-> mini (icons)", () => {
  store.clear();
  classes.clear();
  sandbox.window.innerWidth = 1280;
  S.init();
  assert.strictEqual(S.cycleMode(), "mini");
  assert.ok(classes.has("voxa-sidebar-mini"));
  assert.strictEqual(store.get("voxachat-sidebar-mode"), "mini");
  assert.strictEqual(S.cycleMode(), "full");
  assert.ok(!classes.has("voxa-sidebar-mini"));
  assert.strictEqual(store.get("voxachat-sidebar-mode"), "full");
});

t("desktop setMode(off) coerces to mini", () => {
  store.clear();
  classes.clear();
  sandbox.window.innerWidth = 1280;
  S.init();
  assert.strictEqual(S.setMode("off"), "mini");
  assert.ok(classes.has("voxa-sidebar-mini"));
});

console.log("\n" + n + " passed");
