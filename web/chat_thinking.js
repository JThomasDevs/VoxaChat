(function (g) {
  function ensureOpenThinking(history, placeholder) {
    for (let i = history.length - 1; i >= 0; i--) {
      const m = history[i];
      if (m === placeholder) continue;
      if (m && m.kind === "thinking" && m.streaming) return m;
      break;
    }
    const row = { kind: "thinking", content: "", streaming: true, collapsed: false };
    const idx = history.indexOf(placeholder);
    if (idx >= 0) history.splice(idx, 0, row);
    else history.push(row);
    return row;
  }

  function appendThinkingDelta(row, delta) {
    if (!row || !delta) return;
    row.content = (row.content || "") + delta;
    row.streaming = true;
    row.collapsed = false;
  }

  function collapseOpenThinking(history) {
    if (!history) return;
    for (let i = history.length - 1; i >= 0; i--) {
      const m = history[i];
      if (m && m.kind === "thinking" && m.streaming) {
        m.streaming = false;
        m.collapsed = true;
        return;
      }
    }
  }

  function toggleThinkingCollapsed(row) {
    if (!row || row.kind !== "thinking") return;
    row.collapsed = !row.collapsed;
  }

  g.ensureOpenThinking = ensureOpenThinking;
  g.appendThinkingDelta = appendThinkingDelta;
  g.collapseOpenThinking = collapseOpenThinking;
  g.toggleThinkingCollapsed = toggleThinkingCollapsed;
})(typeof window !== "undefined" ? window : globalThis);
