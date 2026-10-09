// Run: node web/tests/chat_markdown.test.js
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(__dirname, "..", "chat_markdown.js"), "utf8");
const sandbox = {};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const { formatChatMarkdown } = sandbox;
assert.strictEqual(typeof formatChatMarkdown, "function");

const FENCE = "```";
let n = 0;
function t(name, fn) { sandbox.__chatFences = []; fn(); n += 1; console.log("ok -", name); }

t("XSS escaped in code; raw in __chatFences", () => {
  const raw = "<script>alert(1)</script>";
  const html = formatChatMarkdown(`${FENCE}html\n${raw}\n${FENCE}`);
  assert(!html.includes("<script>"));
  assert(html.includes("&lt;script&gt;alert(1)&lt;/script&gt;"));
  assert(html.includes('class="language-html"'));
  assert(html.includes('<pre class="code-block-pre"><code'));
  assert.strictEqual(sandbox.__chatFences.length, 1);
  assert.strictEqual(sandbox.__chatFences[0].content, raw);
});

t("XSS in prose, info string, and path label", () => {
  const html = formatChatMarkdown(`<img src=x onerror=alert(1)>\n${FENCE}"><b>x</b>.js\nok\n${FENCE}`);
  assert(!/<img|<b>/.test(html));
  assert(html.includes("&lt;img"));
  assert(!html.includes('class="language-"><'));
});

t("multi-fence indices", () => {
  const html = formatChatMarkdown(`a\n${FENCE}js\none\n${FENCE}\nb\n${FENCE}py\ntwo\n${FENCE}`);
  assert(html.includes('data-fence-i="0"') && html.includes('data-fence-apply="0"'));
  assert(html.includes('data-fence-i="1"') && html.includes('data-fence-apply="1"'));
  assert(!html.includes('data-fence-i="2"'));
  assert.strictEqual(sandbox.__chatFences[0].content, "one");
  assert.strictEqual(sandbox.__chatFences[1].content, "two");
  assert.strictEqual(sandbox.__chatFences[1].lang, "py");
  assert.strictEqual(sandbox.__chatFences[1].proseBefore, "\nb\n");
});

t("unclosed fence stays escaped prose", () => {
  const html = formatChatMarkdown(`hi\n${FENCE}js\n<b>no close</b>`);
  assert(!html.includes("code-block"));
  assert(!html.includes("<b>"));
  assert(html.includes("&lt;b&gt;no close&lt;/b&gt;"));
  assert.strictEqual(sandbox.__chatFences.length, 0);
});

t("closed fence then unclosed opener", () => {
  const html = formatChatMarkdown(`${FENCE}js\nA\n${FENCE}\ntail ${FENCE}py\n<x>`);
  assert.strictEqual((html.match(/class="code-block"/g) || []).length, 1);
  assert(html.includes("&lt;x&gt;"));
  assert.strictEqual(sandbox.__chatFences.length, 1);
});

t("label: path > lang > code; no language class when empty", () => {
  let html = formatChatMarkdown(`${FENCE}src/a.py\nx\n${FENCE}`);
  assert(html.includes('<span class="code-block-label">src/a.py</span>'));
  assert(!html.includes("language-"));
  assert.strictEqual(sandbox.__chatFences[0].pathGuess, "src/a.py");
  sandbox.__chatFences = [];
  html = formatChatMarkdown(`${FENCE}\nx\n${FENCE}`);
  assert(html.includes('<span class="code-block-label">code</span>'));
  assert(!html.includes("language-"));
});

t("CRLF content trimmed raw", () => {
  formatChatMarkdown(`${FENCE}js\r\nx\r\n${FENCE}`);
  assert.strictEqual(sandbox.__chatFences[0].content, "x");
});

t("sequential calls without reset use global indices", () => {
  const h1 = formatChatMarkdown(`${FENCE}js\nA0\n${FENCE}\nmid\n${FENCE}py\nA1\n${FENCE}`);
  assert(h1.includes('data-fence-i="0"') && h1.includes('data-fence-apply="0"'));
  assert(h1.includes('data-fence-i="1"') && h1.includes('data-fence-apply="1"'));
  assert.strictEqual(sandbox.__chatFences.length, 2);
  const h2 = formatChatMarkdown(`${FENCE}js\nB2\n${FENCE}\nx\n${FENCE}sh\nB3\n${FENCE}`);
  assert(h2.includes('data-fence-i="2"') && h2.includes('data-fence-apply="2"'));
  assert(h2.includes('data-fence-i="3"') && h2.includes('data-fence-apply="3"'));
  assert(!h2.includes('data-fence-i="0"'));
  assert.strictEqual(sandbox.__chatFences.length, 4);
  assert.deepStrictEqual(sandbox.__chatFences.map((f) => f.content), ["A0", "A1", "B2", "B3"]);
});

t("reset between full renders restarts at 0", () => {
  formatChatMarkdown(`${FENCE}js\nold\n${FENCE}`);
  sandbox.__chatFences = [];
  const h = formatChatMarkdown(`${FENCE}js\nnew\n${FENCE}`);
  assert(h.includes('data-fence-i="0"'));
  assert.strictEqual(sandbox.__chatFences.length, 1);
  assert.strictEqual(sandbox.__chatFences[0].content, "new");
});

console.log(`${n} passed`);
