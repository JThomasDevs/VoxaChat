// Run: node web/tests/chat-sidebar.test.js
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const store = new Map();
const els = {};

function makeClassList() {
  const classes = new Set();
  return {
    add(c) {
      classes.add(c);
    },
    remove(c) {
      classes.delete(c);
    },
    toggle(c, on) {
      if (arguments.length === 1) {
        if (classes.has(c)) classes.delete(c);
        else classes.add(c);
      } else if (on) classes.add(c);
      else classes.delete(c);
    },
    contains(c) {
      return classes.has(c);
    },
  };
}

function el(id) {
  const node = {
    classList: makeClassList(),
    attrs: {},
    _listeners: {},
    addEventListener(type, fn) {
      this._listeners[type] = fn;
    },
    setAttribute(name, val) {
      this.attrs[name] = String(val);
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attrs, name)
        ? this.attrs[name]
        : null;
    },
  };
  els[id] = node;
  return node;
}

el("chat-sidebar");
el("chat-sidebar-toggle");

const src = fs.readFileSync(
  path.join(__dirname, "..", "js", "chat-sidebar.js"),
  "utf8"
);
const sandbox = {
  window: {},
  document: {
    getElementById(id) {
      return els[id] || null;
    },
  },
  localStorage: {
    getItem(k) {
      return store.has(k) ? store.get(k) : null;
    },
    setItem(k, v) {
      store.set(k, String(v));
    },
    removeItem(k) {
      store.delete(k);
    },
  },
  console,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const C = sandbox.window.VoxaChatSidebar;

let n = 0;
function t(name, fn) {
  fn();
  n += 1;
  console.log("ok -", name);
}

t("default expanded after init", () => {
  store.clear();
  els["chat-sidebar"].classList = makeClassList();
  els["chat-sidebar-toggle"].attrs = {};
  C.init();
  assert.strictEqual(C.isCollapsed(), false);
  assert.strictEqual(store.get("voxachat-chat-sidebar-collapsed"), "0");
  assert.strictEqual(
    els["chat-sidebar"].classList.contains("collapsed"),
    false
  );
  assert.strictEqual(els["chat-sidebar-toggle"].getAttribute("aria-expanded"), "true");
});

t("setCollapsed persists 1 and updates DOM", () => {
  store.clear();
  els["chat-sidebar"].classList = makeClassList();
  els["chat-sidebar-toggle"].attrs = {};
  C.setCollapsed(true);
  assert.strictEqual(C.isCollapsed(), true);
  assert.strictEqual(store.get("voxachat-chat-sidebar-collapsed"), "1");
  assert.ok(els["chat-sidebar"].classList.contains("collapsed"));
  assert.strictEqual(els["chat-sidebar-toggle"].getAttribute("aria-expanded"), "false");
  C.setCollapsed(false);
  assert.strictEqual(store.get("voxachat-chat-sidebar-collapsed"), "0");
  assert.strictEqual(
    els["chat-sidebar"].classList.contains("collapsed"),
    false
  );
  assert.strictEqual(els["chat-sidebar-toggle"].getAttribute("aria-expanded"), "true");
});

t("toggle cycles collapse and persistence", () => {
  store.clear();
  els["chat-sidebar"].classList = makeClassList();
  els["chat-sidebar-toggle"].attrs = {};
  C.setCollapsed(false);
  assert.strictEqual(C.toggle(), true);
  assert.strictEqual(store.get("voxachat-chat-sidebar-collapsed"), "1");
  assert.ok(els["chat-sidebar"].classList.contains("collapsed"));
  assert.strictEqual(C.toggle(), false);
  assert.strictEqual(store.get("voxachat-chat-sidebar-collapsed"), "0");
  assert.strictEqual(
    els["chat-sidebar"].classList.contains("collapsed"),
    false
  );
});

t("init restores persisted collapsed state", () => {
  store.clear();
  store.set("voxachat-chat-sidebar-collapsed", "1");
  els["chat-sidebar"].classList = makeClassList();
  els["chat-sidebar-toggle"].attrs = {};
  C.init();
  assert.strictEqual(C.isCollapsed(), true);
  assert.ok(els["chat-sidebar"].classList.contains("collapsed"));
  assert.strictEqual(els["chat-sidebar-toggle"].getAttribute("aria-expanded"), "false");
});

t("toggle button click handler", () => {
  store.clear();
  els["chat-sidebar"].classList = makeClassList();
  els["chat-sidebar-toggle"].attrs = {};
  els["chat-sidebar-toggle"]._listeners = {};
  C.init();
  assert.strictEqual(typeof els["chat-sidebar-toggle"]._listeners.click, "function");
  els["chat-sidebar-toggle"]._listeners.click();
  assert.strictEqual(C.isCollapsed(), true);
  els["chat-sidebar-toggle"]._listeners.click();
  assert.strictEqual(C.isCollapsed(), false);
});

console.log("\n" + n + " passed");
