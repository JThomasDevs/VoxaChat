// Run: node web/tests/chat_runs.test.js
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(__dirname, "..", "chat_runs.js"), "utf8");
const sandbox = { AbortController };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const { createRunMap, SOFT_CONCURRENT_WARN } = sandbox;

let n = 0;
function t(name, fn) {
  fn();
  n += 1;
  console.log("ok -", name);
}

t("stop only aborts targeted chat", () => {
  const map = createRunMap();
  const a = { abort() { this.aborted = true; }, aborted: false };
  const b = { abort() { this.aborted = true; }, aborted: false };
  map.start("chat-a", a);
  map.start("chat-b", b);
  assert.strictEqual(map.stop("chat-a"), true);
  assert.strictEqual(a.aborted, true);
  assert.strictEqual(b.aborted, false);
  assert.strictEqual(map.get("chat-b").streaming, true);
});

t("rowState needs_attention when pending", () => {
  const map = createRunMap();
  map.start("c1", { abort() {} });
  map.setPending("c1", { kind: "plan", id: "p1" });
  assert.strictEqual(map.rowState("c1"), "needs_attention");
});

t("shouldWarnConcurrency at soft cap", () => {
  const map = createRunMap();
  for (let i = 0; i < SOFT_CONCURRENT_WARN; i++) {
    map.start("c" + i, { abort() {} });
  }
  assert.strictEqual(map.shouldWarnConcurrency(), true);
});

t("start replaces prior controller on same chat", () => {
  const map = createRunMap();
  const first = { abort() { this.aborted = true; }, aborted: false };
  map.start("c", first);
  map.start("c", { abort() {} });
  assert.strictEqual(first.aborted, true);
});

console.log("\n" + n + " tests passed");
