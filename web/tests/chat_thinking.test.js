// Run: node web/tests/chat_thinking.test.js
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(__dirname, "..", "chat_thinking.js"), "utf8");
const sandbox = {};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);

const {
  ensureOpenThinking,
  appendThinkingDelta,
  collapseOpenThinking,
  toggleThinkingCollapsed,
} = sandbox;

let n = 0;
function t(name, fn) {
  fn();
  n += 1;
  console.log("ok -", name);
}

t("ensureOpenThinking inserts before placeholder", () => {
  const placeholder = { role: "assistant", content: "" };
  const history = [{ role: "user", content: "hi" }, placeholder];
  const row = ensureOpenThinking(history, placeholder);
  assert.strictEqual(row.kind, "thinking");
  assert.strictEqual(row.streaming, true);
  assert.strictEqual(row.collapsed, false);
  assert.strictEqual(history[1], row);
  assert.strictEqual(history[2], placeholder);
});

t("appendThinkingDelta accumulates and keeps open", () => {
  const row = { kind: "thinking", content: "", streaming: true, collapsed: false };
  appendThinkingDelta(row, "a");
  appendThinkingDelta(row, "b");
  assert.strictEqual(row.content, "ab");
  assert.strictEqual(row.streaming, true);
  assert.strictEqual(row.collapsed, false);
});

t("collapseOpenThinking closes streaming row", () => {
  const row = { kind: "thinking", content: "x", streaming: true, collapsed: false };
  const history = [row, { role: "assistant", content: "" }];
  collapseOpenThinking(history);
  assert.strictEqual(row.streaming, false);
  assert.strictEqual(row.collapsed, true);
});

t("new ensure after collapse starts a new row", () => {
  const placeholder = { role: "assistant", content: "" };
  const closed = { kind: "thinking", content: "old", streaming: false, collapsed: true };
  const history = [closed, placeholder];
  const row = ensureOpenThinking(history, placeholder);
  assert.notStrictEqual(row, closed);
  assert.strictEqual(history.length, 3);
  assert.strictEqual(history[1], row);
  appendThinkingDelta(row, "new");
  assert.strictEqual(row.content, "new");
});

t("toggleThinkingCollapsed flips collapsed", () => {
  const row = { kind: "thinking", content: "x", streaming: false, collapsed: true };
  toggleThinkingCollapsed(row);
  assert.strictEqual(row.collapsed, false);
  toggleThinkingCollapsed(row);
  assert.strictEqual(row.collapsed, true);
});

t("ensure reuses streaming row", () => {
  const placeholder = { role: "assistant", content: "" };
  const open = { kind: "thinking", content: "a", streaming: true, collapsed: false };
  const history = [open, placeholder];
  const row = ensureOpenThinking(history, placeholder);
  assert.strictEqual(row, open);
  assert.strictEqual(history.length, 2);
});

console.log(`\n${n} passed`);
