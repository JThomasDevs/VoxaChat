(function (global) {
  const READING = new Set(["read_file", "list_dir", "search_files"]);
  const EDITING = new Set([
    "write_file", "edit_file", "mkdir", "delete_file", "move_path",
  ]);
  const RUNNING = new Set([
    "shell_run", "web_search", "web_fetch", "get_weather",
  ]);

  function verbForTool(name) {
    const n = String(name || "");
    if (READING.has(n)) return "reading";
    if (EDITING.has(n)) return "editing";
    if (RUNNING.has(n)) return "running";
    return "thinking";
  }

  function activityLabel(phase, detail) {
    const d = (detail == null ? "" : String(detail)).trim();
    switch (phase) {
      case "idle":
        return "";
      case "stopped":
        return "Stopped";
      case "error":
        return "Error";
      case "waiting":
        return d || "Waiting…";
      case "reading":
        return d ? "Reading " + d : "Reading…";
      case "editing":
        return d ? "Editing " + d : "Editing…";
      case "running":
        return d ? "Running " + d : "Running…";
      case "thinking":
      default:
        return "Thinking…";
    }
  }

  const CONCURRENCY_WARN_MESSAGE =
    "3+ chats streaming — responses may slow down";

  function setChatActivity(phase, detail) {
    const el = document.getElementById("chat-status");
    if (!el) return;
    el.textContent = activityLabel(phase, detail);
    const map = global.chatRuns;
    if (
      phase === "thinking" &&
      map &&
      typeof map.shouldWarnConcurrency === "function" &&
      map.shouldWarnConcurrency()
    ) {
      el.textContent = CONCURRENCY_WARN_MESSAGE;
    }
  }

  // Thinking-block helpers (also in chat_thinking.js). Defined here so a
  // stale index.html missing that script tag still streams Thinking cards.
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

  global.verbForTool = verbForTool;
  global.activityLabel = activityLabel;
  global.setChatActivity = setChatActivity;
  global.ensureOpenThinking = ensureOpenThinking;
  global.appendThinkingDelta = appendThinkingDelta;
  global.collapseOpenThinking = collapseOpenThinking;
  global.toggleThinkingCollapsed = toggleThinkingCollapsed;
})(typeof window !== "undefined" ? window : globalThis);
