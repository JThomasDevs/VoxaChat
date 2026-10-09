(function (global) {
  function escapeMd(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Keep only safe characters for a CSS class suffix (defense in depth).
  function safeLangClass(lang) {
    return String(lang || "").replace(/[^a-z0-9_+#.-]/gi, "");
  }

  const LANG_ONLY = new Set([
    "js", "javascript", "ts", "tsx", "typescript", "py", "python", "bash", "sh",
    "shell", "json", "html", "css", "md", "markdown", "text", "plaintext", "diff",
    "yaml", "yml", "toml", "go", "rust", "java", "c", "cpp", "rb", "ruby",
  ]);

  function infoToken(info) {
    const t = String(info || "").trim().split(/\s+/)[0] || "";
    return t;
  }

  function isPathLike(token) {
    if (!token) return false;
    const t = token.trim();
    if (LANG_ONLY.has(t.toLowerCase())) return false;
    if (t.startsWith("./") || t.startsWith("../") || t.startsWith("/")) return true;
    if (t.includes("/")) return true;
    return /\.[a-zA-Z0-9]{1,8}$/.test(t);
  }

  function splitFences(text) {
    const src = String(text ?? "");
    const re = /```([^\n`]*)\n([\s\S]*?)```/g;
    const parts = [];
    let last = 0;
    let m;
    while ((m = re.exec(src)) !== null) {
      if (m.index > last) {
        parts.push({ type: "prose", text: src.slice(last, m.index) });
      }
      const info = (m[1] || "").trim();
      const tok = infoToken(info);
      const pathGuess = isPathLike(tok) ? tok : "";
      const lang = !pathGuess && tok ? tok.toLowerCase() : "";
      parts.push({
        type: "fence",
        info,
        lang,
        pathGuess,
        content: m[2].replace(/\r?\n$/, ""),
      });
      last = m.index + m[0].length;
    }
    if (last < src.length) parts.push({ type: "prose", text: src.slice(last) });
    if (!parts.length) parts.push({ type: "prose", text: src });
    return parts;
  }

  function formatChatMarkdown(text) {
    const parts = splitFences(text);
    // Append to the shared array so indices are global across messages.
    // Callers (e.g. renderChat) must reset global.__chatFences = [] before a full history render.
    if (!Array.isArray(global.__chatFences)) global.__chatFences = [];
    const fences = global.__chatFences;
    let html = "";
    let lastProse = "";
    for (const p of parts) {
      if (p.type === "prose") {
        lastProse = p.text;
        html += `<div class="md-prose">${escapeMd(p.text)}</div>`;
        continue;
      }
      const i = fences.length;
      // content stays RAW for Apply; only the rendered HTML is escaped.
      fences.push({
        content: p.content,
        lang: p.lang,
        pathGuess: p.pathGuess,
        proseBefore: lastProse,
      });
      lastProse = "";
      const label = escapeMd(p.pathGuess || p.lang || "code");
      const safeLang = safeLangClass(p.lang);
      const langClass = safeLang ? ` class="language-${escapeMd(safeLang)}"` : "";
      html += `<div class="code-block" data-fence-i="${i}">` +
        `<div class="code-block-header"><span class="code-block-label">${label}</span>` +
        `<button type="button" class="secondary small" data-fence-apply="${i}">Apply</button></div>` +
        `<pre class="code-block-pre"><code${langClass}>${escapeMd(p.content)}</code></pre>` +
        `</div>`;
    }
    return html;
  }

  global.escapeMd = escapeMd;
  global.splitFences = splitFences;
  global.isPathLike = isPathLike;
  global.formatChatMarkdown = formatChatMarkdown;
})(typeof window !== "undefined" ? window : globalThis);
