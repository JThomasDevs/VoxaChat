// VoxaChat — vanilla JS, no build step

let modelsLoaded = false;

// Panel switching (app sidebar / icon rail)
function syncNavActive(tab) {
  document.querySelectorAll("[data-voxa-nav]").forEach((btn) => {
    const nav = btn.getAttribute("data-voxa-nav");
    const on = nav === tab;
    btn.classList.toggle("active", on);
    if (on) btn.setAttribute("active", "");
    else btn.removeAttribute("active");
  });
}

function switchPanel(tab) {
  if (!tab || tab === "theme") return;
  document.querySelectorAll(".panel").forEach((p) => p.removeAttribute("active"));
  const panel = document.getElementById("panel-" + tab);
  if (panel) panel.setAttribute("active", "");
  syncNavActive(tab);
  if (tab === "chat") {
    if (!modelsLoaded) loadChatModels();
    else maybeBootSessions();
  }
}

document.querySelectorAll("[data-voxa-nav]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const nav = btn.getAttribute("data-voxa-nav");
    if (nav === "theme") {
      if (window.VoxaTheme && typeof window.VoxaTheme.openPopup === "function") {
        window.VoxaTheme.openPopup();
      }
      return;
    }
    switchPanel(nav);
  });
});

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchPanel(btn.dataset.tab));
});

// Theme + sidebars
(function initChrome() {
  try {
    if (window.VoxaTheme) {
      try {
        if (typeof window.VoxaTheme.applySaved === "function") window.VoxaTheme.applySaved();
      } catch (e) {
        console.warn("VoxaTheme.applySaved failed", e);
      }
      try {
        if (typeof window.VoxaTheme.initThemeUI === "function") window.VoxaTheme.initThemeUI();
      } catch (e) {
        console.warn("VoxaTheme.initThemeUI failed", e);
      }
    }
    if (window.VoxaSidebar && typeof window.VoxaSidebar.init === "function") {
      window.VoxaSidebar.init();
    }
    if (window.VoxaChatSidebar && typeof window.VoxaChatSidebar.init === "function") {
      window.VoxaChatSidebar.init();
    }
  } catch (e) {
    console.warn("VoxaChat chrome init failed", e);
  }
  const activePanel = document.querySelector(".panel[active]");
  const tab = activePanel && activePanel.id ? activePanel.id.replace(/^panel-/, "") : "chat";
  syncNavActive(tab);
})();

function healthClass(v) {
  if (v.status === "starting" || v.status === "stopping") return "busy";
  if (v.enabled && v.ok) return "ok";
  if (v.ok) return "ok";
  return "bad";
}

async function checkHealth() {
  try {
    const r = await fetch("/api/health");
    const h = await r.json();
    if (h.repo_root) ensureDefaultWorkspace(h.repo_root);
    const el = document.getElementById("health");
    const services = ["ollama"];
    el.innerHTML = services.filter(k => h[k]).map(k => {
      const v = h[k];
      const tip = v.error || v.url || k;
      return `<span class="svc ${healthClass(v)}" data-svc="${escape(k)}" title="${escape(tip)}">${escape(k)}</span>`;
    }).join(" ");
    if (!modelsLoaded && h.ollama?.ok) loadChatModels();
  } catch (e) {
    document.getElementById("health").innerHTML = '<span class="bad">backend</span>';
  }
}

checkHealth();
setInterval(checkHealth, 5000);

function escape(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function errElShow(el, msg) {
  el.textContent = msg;
  el.hidden = false;
}

// ===================== Chat =====================

let chatHistory = []; // visible chat transcript; rebound on switch (aliases run.history while streaming)
const chatRuns = (typeof createRunMap === "function") ? createRunMap() : null;
if (typeof window !== "undefined" && chatRuns) window.chatRuns = chatRuns;
let chatStreaming = false; // visible-chat send/stop button mirror only
const deletedChatIds = new Set(); // runs that settle after delete must not re-save
let runEverythingByChat = true; // mirrors the visible chat; default on
let switchSeq = 0; // drops stale hydrate fetches
const WORKSPACE_KEY = "voxachat.chat.workspace_roots";
const ACTIVE_CHAT_KEY = "voxachat.chat.active_id";
let workspaceRoots = [];
let activeChatId = null;
let sessionBooted = false;
let sessionBooting = false;
let sessionHydrating = false;
let sessionIndex = [];

const MAX_CHAT_IMAGES = 4;
const MAX_CHAT_IMAGE_BYTES = 8 * 1024 * 1024;
// keep in sync with bin/vision_models.py
const VISION_NAME_MARKERS = [
  "llava",
  "bakllava",
  "moondream",
  "minicpm-v",
  "qwen2-vl",
  "qwen2.5-vl",
  "llama3.2-vision",
  "gemma3",
  "granite-vision",
  "vision",
];
let pendingChatImages = []; // { id, b64, mime }

function modelSupportsVision(model) {
  const n = String(model || "").toLowerCase();
  if (!n) return false;
  return VISION_NAME_MARKERS.some((m) => n.includes(m));
}

function fileToDownscaledB64(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = async () => {
      URL.revokeObjectURL(url);
      const maxEdge = 1280;
      let w = img.naturalWidth;
      let h = img.naturalHeight;
      if (!w || !h) {
        reject(new Error("Invalid image"));
        return;
      }
      const scale = Math.min(1, maxEdge / Math.max(w, h));
      w = Math.max(1, Math.round(w * scale));
      h = Math.max(1, Math.round(h * scale));
      let bitmap;
      try {
        if (typeof createImageBitmap === "function") {
          bitmap = await createImageBitmap(img, { resizeWidth: w, resizeHeight: h, resizeQuality: "high" });
        }
      } catch (e) {
        bitmap = null;
      }
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        reject(new Error("Canvas unavailable"));
        return;
      }
      if (bitmap) {
        ctx.drawImage(bitmap, 0, 0, w, h);
        bitmap.close();
      } else {
        ctx.drawImage(img, 0, 0, w, h);
      }
      const mime = file.type && file.type.startsWith("image/") ? file.type : "image/png";
      const dataUrl = canvas.toDataURL(mime, 0.92);
      const b64 = dataUrl.includes("base64,") ? dataUrl.split("base64,", 2)[1] : dataUrl;
      resolve(b64);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Could not load image"));
    };
    img.src = url;
  });
}

function renderImagePreviews() {
  const wrap = document.getElementById("chat-image-previews");
  if (!wrap) return;
  if (!pendingChatImages.length) {
    wrap.hidden = true;
    wrap.innerHTML = "";
    return;
  }
  wrap.hidden = false;
  wrap.innerHTML = pendingChatImages.map((x) => {
    const src = `data:${x.mime || "image/png"};base64,${x.b64}`;
    return `<div class="chat-image-thumb" data-id="${escape(x.id)}"><img src="${src}" alt=""><button type="button" data-remove="${escape(x.id)}" title="Remove" aria-label="Remove image">×</button></div>`;
  }).join("");
  wrap.querySelectorAll("button[data-remove]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-remove");
      pendingChatImages = pendingChatImages.filter((p) => p.id !== id);
      renderImagePreviews();
    });
  });
}

async function addChatImageFiles(fileList) {
  const status = document.getElementById("chat-status");
  const files = Array.from(fileList || []).filter((f) => f && f.type && f.type.startsWith("image/"));
  if (!files.length) return;
  for (const file of files) {
    if (pendingChatImages.length >= MAX_CHAT_IMAGES) {
      if (status) status.textContent = `Maximum ${MAX_CHAT_IMAGES} images per message`;
      break;
    }
    try {
      const b64 = await fileToDownscaledB64(file);
      const approxBytes = Math.ceil((b64.length * 3) / 4);
      if (approxBytes > MAX_CHAT_IMAGE_BYTES) {
        if (status) status.textContent = "Image too large (max ~8 MB after encoding)";
        continue;
      }
      pendingChatImages.push({
        id: `img-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
        b64,
        mime: file.type || "image/png",
      });
    } catch (e) {
      if (status) status.textContent = e.message || "Could not process image";
    }
  }
  renderImagePreviews();
}

function ensureDefaultWorkspace(_repoRoot) {
  // Restore saved roots only. Never auto-grant the Voxa repo — the user must
  // Add a workspace path (Send is blocked when the list is empty).
  if (workspaceRoots.length) return;
  try {
    const saved = JSON.parse(localStorage.getItem(WORKSPACE_KEY) || "[]");
    if (Array.isArray(saved) && saved.length) {
      workspaceRoots = saved.filter(Boolean);
    }
  } catch (e) { /* ignore */ }
  renderWorkspaceRoots();
}

function persistWorkspace() {
  localStorage.setItem(WORKSPACE_KEY, JSON.stringify(workspaceRoots));
  if (sessionBooted && !sessionHydrating) saveActiveChat();
}

function removeWorkspaceRoot(index) {
  const i = Number(index);
  if (!Number.isInteger(i) || i < 0 || i >= workspaceRoots.length) return;
  workspaceRoots.splice(i, 1);
  persistWorkspace();
  renderWorkspaceRoots();
}

function renderWorkspaceRoots() {
  const el = document.getElementById("chat-workspace-roots");
  if (!el) return;
  if (!workspaceRoots.length) {
    el.textContent = "(none — click + to choose a workspace folder)";
    return;
  }
  el.innerHTML = workspaceRoots.map((r, i) => {
    const short = r.length > 40 ? "…" + r.slice(-39) : r;
    return `<span class="root-chip" title="${escape(r)}"><span class="root-chip-path">${escape(short)}</span><button type="button" data-i="${i}" title="Remove workspace" aria-label="Remove ${escape(short)}">×</button></span>`;
  }).join("");
  el.querySelectorAll("button[data-i]").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      removeWorkspaceRoot(btn.dataset.i);
    });
  });
}

function addWorkspaceRoot(path) {
  const p = (path || "").trim();
  if (!p) return;
  if (!workspaceRoots.includes(p)) {
    workspaceRoots.push(p);
    persistWorkspace();
    renderWorkspaceRoots();
  }
}

function syncWritesBatchCheckboxToModel(cb) {
  const batchId = cb.dataset.batchItem;
  const itemId = cb.dataset.itemId;
  if (!batchId || !itemId) return;
  const panel = chatHistory.find(
    (x) => x.kind === "writes_batch_pending" && x.id === batchId
  );
  if (!panel?.items) return;
  const it = panel.items.find((i) => i.item_id === itemId);
  if (it) it.checked = cb.checked;
}

let chatRenderRaf = 0;
/** False when the user has scrolled away from the bottom during a stream. */
let chatFollowOutput = true;
let chatScrollBound = false;

function chatHistoryNearBottom(el, px) {
  const slop = px == null ? 80 : px;
  return el.scrollHeight - el.scrollTop - el.clientHeight < slop;
}

function bindChatHistoryScroll() {
  if (chatScrollBound) return;
  const histEl = document.getElementById("chat-history");
  if (!histEl) return;
  chatScrollBound = true;
  histEl.addEventListener(
    "scroll",
    () => {
      chatFollowOutput = chatHistoryNearBottom(histEl);
    },
    { passive: true }
  );
}

function scheduleChatRender() {
  if (chatRenderRaf) return;
  chatRenderRaf = requestAnimationFrame(() => {
    chatRenderRaf = 0;
    renderChat();
  });
}

function renderChat(opts) {
  const forceStick = opts && opts.stickToBottom === true;
  const forceNoStick = opts && opts.stickToBottom === false;
  const histEl = document.getElementById("chat-history");
  bindChatHistoryScroll();
  histEl.querySelectorAll("input[data-batch-item]").forEach(syncWritesBatchCheckboxToModel);
  if (typeof window !== "undefined") window.__chatFences = [];
  let thinkingIdx = 0;
  histEl.innerHTML = chatHistory.map((m) => {
    if (m.kind === "thinking") {
      const i = thinkingIdx++;
      const open = !m.collapsed;
      const chev = open ? "▾" : "▸";
      const body = `<pre class="thinking-body">${escape(m.content || "")}</pre>`;
      return `<div class="msg thinking-block${m.collapsed ? " collapsed" : ""}" data-thinking-i="${i}"><button type="button" class="thinking-toggle" data-thinking-toggle="${i}">${chev} Thinking</button>${body}</div>`;
    }
    if (m.kind === "tool_chip") {
      const status = m.ok === false ? " ✗" : (m.ok === true ? " ✓" : " …");
      return `<div class="msg tool-chip">${escape(m.name)}${status} ${escape(m.summary || "")}</div>`;
    }
    if (m.kind === "plan_pending") {
      const todos = (m.todos || []).map((t) =>
        `<li data-status="${escape(t.status || "pending")}">${escape(t.label || "")}</li>`
      ).join("");
      return `<div class="msg plan-pending write-pending" data-plan="${escape(m.id)}">
        <strong>Plan pending approval</strong>
        <pre>${escape(m.summary || "")}</pre>
        <ul class="todo-list">${todos}</ul>
        <div class="pending-actions">
          <button type="button" data-plan-approve="${escape(m.id)}">Approve</button>
          <button type="button" class="secondary" data-plan-revise="${escape(m.id)}">Revise</button>
        </div>
      </div>`;
    }
    if (m.kind === "plan_resolved") {
      return `<div class="msg tool-chip">${escape(m.name || "Plan resolved")}</div>`;
    }
    if (m.kind === "todos") {
      const todos = (m.todos || []).map((t) =>
        `<li data-status="${escape(t.status || "pending")}"><span class="todo-status">${escape(t.status || "")}</span> ${escape(t.label || "")}</li>`
      ).join("");
      return `<div class="msg todos-panel"><strong>Todos</strong><ul class="todo-list">${todos}</ul></div>`;
    }
    if (m.kind === "write_pending") {
      return `<div class="msg write-pending" data-pending="${escape(m.id)}">
        <strong>Write pending:</strong> ${escape(m.tool)}
        <pre>${escape(m.preview || JSON.stringify(m.args || {}, null, 2))}</pre>
        <div class="pending-actions">
          <button type="button" data-approve="${escape(m.id)}">Approve</button>
          <button type="button" class="secondary" data-deny="${escape(m.id)}">Deny</button>
        </div>
      </div>`;
    }
    if (m.kind === "shell_pending") {
      return `<div class="msg write-pending" data-shell="${escape(m.id)}">
        <strong>Shell pending:</strong> ${escape(m.tool)}
        <pre>${escape(m.preview || JSON.stringify(m.args || {}, null, 2))}</pre>
        <div class="pending-actions">
          <button type="button" data-shell-approve="${escape(m.id)}">Approve</button>
          <button type="button" class="secondary" data-shell-deny="${escape(m.id)}">Deny</button>
        </div>
      </div>`;
    }
    if (m.kind === "writes_batch_pending") {
      const rows = (m.items || [])
        .map(
          (it) => `<label class="batch-row">
        <input type="checkbox" data-batch-item="${escape(m.id)}" data-item-id="${escape(it.item_id)}" ${it.checked === false ? "" : "checked"} />
        <strong>${escape(it.path || it.tool)}</strong>
        <span class="batch-tool">${escape(it.tool || "")}</span>
        <pre>${escape(it.preview || "")}</pre>
      </label>`
        )
        .join("");
      const actions = m.awaitingDecision
        ? `<div class="pending-actions">
        <button type="button" data-batch-approve="${escape(m.id)}">Approve selected</button>
        <button type="button" class="secondary" data-batch-deny="${escape(m.id)}">Deny all</button>
      </div>`
        : `<p class="batch-waiting">Queued — waiting for more edits or flush…</p>`;
      return `<div class="msg write-pending writes-batch" data-batch="${escape(m.id)}">
    <strong>Writes pending</strong>
    ${rows}
    ${actions}
  </div>`;
    }
    if (m.kind === "decision_pick") {
      const conf = typeof m.confidence === "number" ? m.confidence.toFixed(2) : "";
      const opt = m.option || "?";
      const label = opt === "reply" ? "reply (no tool)" : opt;
      const backend = m.backend ? " · " + escape(m.backend) : "";
      return `<div class="msg decision-pick tool-chip">Decision → ${escape(label)}${conf ? " · " + conf : ""}${backend}</div>`;
    }
    if (m.kind === "tool_permission_pending") {
      return `<div class="msg tool-permission-pending write-pending" data-tool-perm="${escape(m.id)}">
        <strong>Allow tool?</strong> ${escape(m.tool)}
        <p>Approve before the model fills arguments.</p>
        <div class="pending-actions">
          <button type="button" data-tool-perm-approve="${escape(m.id)}">Approve</button>
          <button type="button" class="secondary" data-tool-perm-deny="${escape(m.id)}">Deny</button>
        </div>
      </div>`;
    }
    if (m.kind === "grant_needed") {
      const path = m.path || "";
      const suggested = m.suggested_root || path.replace(/\/[^/]+\/?$/, "") || path;
      const parent = PathParent(suggested);
      return `<div class="msg grant-needed" data-grant="${escape(m.id)}">
        <strong>Workspace grant needed</strong>
        <pre>${escape(m.reason || "")}\n${escape(path)}</pre>
        <div class="pending-actions">
          <button type="button" data-grant-path="${escape(m.id)}" data-root="${escape(suggested)}">Add folder</button>
          <button type="button" class="secondary" data-grant-path="${escape(m.id)}" data-root="${escape(parent)}">Add parent</button>
          <button type="button" class="secondary" data-grant-deny="${escape(m.id)}">Deny</button>
        </div>
      </div>`;
    }
    if (m.role === "assistant") {
      const empty = !(m.content || "").trim();
      const cls = empty ? "msg assistant chat-placeholder-empty" : `msg ${m.role}`;
      if (!empty && typeof formatChatMarkdown === "function") {
        return `<div class="${cls}">${formatChatMarkdown(m.content || "")}</div>`;
      }
      return `<div class="${cls}">${escape(m.content || "")}</div>`;
    }
    return `<div class="msg ${m.role}">${escape(m.content || "")}</div>`;
  }).join("");
  if (forceStick || (!forceNoStick && chatFollowOutput)) {
    histEl.scrollTop = histEl.scrollHeight;
    chatFollowOutput = true;
  }

  histEl.querySelectorAll("[data-thinking-toggle]").forEach((btn) => {
    btn.onclick = () => {
      const idx = Number(btn.getAttribute("data-thinking-toggle"));
      const rows = chatHistory.filter((x) => x.kind === "thinking");
      const row = rows[idx];
      if (!row || typeof toggleThinkingCollapsed !== "function") return;
      toggleThinkingCollapsed(row);
      chatFollowOutput = false;
      renderChat({ stickToBottom: false });
      const card = document
        .getElementById("chat-history")
        .querySelector(`[data-thinking-i="${idx}"]`);
      if (card) card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    };
  });
  histEl.querySelectorAll("[data-approve]").forEach(btn => {
    btn.onclick = () => decideWrite(btn.dataset.approve, true);
  });
  histEl.querySelectorAll("[data-deny]").forEach(btn => {
    btn.onclick = () => decideWrite(btn.dataset.deny, false);
  });
  histEl.querySelectorAll("[data-shell-approve]").forEach(btn => {
    btn.onclick = () => decideShell(btn.dataset.shellApprove, true);
  });
  histEl.querySelectorAll("[data-shell-deny]").forEach(btn => {
    btn.onclick = () => decideShell(btn.dataset.shellDeny, false);
  });
  histEl.querySelectorAll("[data-tool-perm-approve]").forEach(btn => {
    btn.onclick = () => decideToolPermission(btn.dataset.toolPermApprove, true);
  });
  histEl.querySelectorAll("[data-tool-perm-deny]").forEach(btn => {
    btn.onclick = () => decideToolPermission(btn.dataset.toolPermDeny, false);
  });
  histEl.querySelectorAll("[data-grant-path]").forEach(btn => {
    btn.onclick = () => decideGrant(btn.dataset.grantPath, btn.dataset.root, false);
  });
  histEl.querySelectorAll("[data-grant-deny]").forEach(btn => {
    btn.onclick = () => decideGrant(btn.dataset.grantDeny, null, true);
  });
  histEl.querySelectorAll("[data-plan-approve]").forEach((btn) => {
    btn.onclick = () => decidePlan(btn.dataset.planApprove, "approve");
  });
  histEl.querySelectorAll("[data-plan-revise]").forEach((btn) => {
    btn.onclick = () => decidePlan(btn.dataset.planRevise, "revise");
  });
  histEl.querySelectorAll("[data-batch-approve]").forEach((btn) => {
    const id = btn.dataset.batchApprove;
    btn.onclick = () => {
      const approvedIds = [];
      histEl.querySelectorAll(`input[data-batch-item="${CSS.escape(id)}"]`).forEach((cb) => {
        if (cb.checked) approvedIds.push(cb.dataset.itemId);
      });
      decideWritesBatch(id, approvedIds);
    };
  });
  histEl.querySelectorAll("[data-batch-deny]").forEach((btn) => {
    btn.onclick = () => decideWritesBatch(btn.dataset.batchDeny, []);
  });
  histEl.querySelectorAll("input[data-batch-item]").forEach((cb) => {
    cb.onchange = () => syncWritesBatchCheckboxToModel(cb);
  });
  if (typeof bindApplyControls === "function") {
    bindApplyControls(histEl);
  }
}

function cancelPendingApprovalsForTurn(fromUserIdx, hist) {
  const target = hist || chatHistory;
  for (let i = fromUserIdx + 1; i < target.length; i++) {
    const m = target[i];
    if (m.kind === "write_pending") {
      m.kind = "tool_chip";
      m.name = m.tool + " cancelled";
      m.ok = false;
      m.summary = "";
    } else if (m.kind === "shell_pending") {
      m.kind = "tool_chip";
      m.name = m.tool + " cancelled";
      m.ok = false;
      m.summary = "";
    } else if (m.kind === "grant_needed") {
      m.kind = "tool_chip";
      m.name = "grant cancelled";
      m.ok = false;
      m.summary = m.path || "";
    } else if (m.kind === "plan_pending") {
      m.kind = "tool_chip";
      m.name = "Plan cancelled";
      m.ok = false;
      m.summary = "";
    } else if (m.kind === "writes_batch_pending") {
      m.kind = "tool_chip";
      m.name = "Writes batch cancelled";
      m.ok = false;
      m.summary = "";
    }
  }
  if (target === chatHistory) renderChat();
}

async function decidePlan(id, action) {
  const note =
    action === "revise"
      ? (window.prompt("Revise note (optional):") || "").trim()
      : "";
  const r = await fetch("/api/chat/plan-decision", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, action, note }),
  });
  if (!r.ok) return;
  clearVisiblePending();
  const m = chatHistory.find((x) => x.kind === "plan_pending" && x.id === id);
  if (m) {
    m.kind = "plan_resolved";
    m.action = action;
    m.name = action === "approve" ? "Plan approved" : "Plan revise requested";
  }
  renderChat();
}

async function decideWritesBatch(id, approvedIds) {
  const r = await fetch("/api/chat/writes-batch-decision", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, approved_ids: approvedIds }),
  });
  clearVisiblePending();
  if (!r.ok) {
    chatHistory.forEach((m) => {
      if (m.kind === "writes_batch_pending" && m.id === id) {
        m.kind = "tool_chip";
        m.name = "Writes batch cancelled";
        m.ok = false;
        m.summary = "";
      }
    });
    renderChat();
  }
}

async function decideWrite(id, approve) {
  const r = await fetch("/api/chat/write-decision", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, approve }),
  });
  clearVisiblePending();
  chatHistory.forEach(m => {
    if (m.kind === "write_pending" && m.id === id) {
      m.kind = "tool_chip";
      if (!r.ok) {
        m.name = m.tool + " cancelled";
        m.ok = false;
        m.summary = "";
      } else {
        m.name = m.tool + (approve ? " approved" : " denied");
        m.ok = approve;
        m.summary = "";
      }
    }
  });
  renderChat();
}

async function decideShell(id, approve) {
  const r = await fetch("/api/chat/approve-shell", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, approve }),
  });
  clearVisiblePending();
  chatHistory.forEach(m => {
    if (m.kind === "shell_pending" && m.id === id) {
      m.kind = "tool_chip";
      if (!r.ok) {
        m.name = m.tool + " cancelled";
        m.ok = false;
        m.summary = "";
      } else {
        m.name = m.tool + (approve ? " approved" : " denied");
        m.ok = approve;
        m.summary = "";
      }
    }
  });
  renderChat();
}

async function decideToolPermission(id, approve) {
  await fetch("/api/chat/tool-permission", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, approve }),
  });
  chatHistory.forEach((m) => {
    if (m.kind === "tool_permission_pending" && m.id === id) {
      m.kind = "tool_chip";
      m.name = (m.tool || "tool") + (approve ? " allowed" : " denied");
      m.ok = approve;
      m.summary = "";
    }
  });
  renderChat();
  clearVisiblePending();
  if (isVisibleStreaming()) {
    setChatActivity("thinking");
  }
}

async function decideGrant(id, root, deny) {
  const r = await fetch("/api/chat/workspace-grant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id,
      deny: !!deny,
      root: deny ? null : root,
      workspace_roots: workspaceRoots,
    }),
  });
  if (!r.ok) {
    chatHistory.forEach(m => {
      if (m.kind === "grant_needed" && m.id === id) {
        m.kind = "tool_chip";
        m.name = "grant cancelled";
        m.ok = false;
        m.summary = root || m.path || "";
      }
    });
    renderChat();
    return;
  }
  clearVisiblePending();
  if (!deny && root) addWorkspaceRoot(root);
  chatHistory.forEach(m => {
    if (m.kind === "grant_needed" && m.id === id) {
      m.kind = "tool_chip";
      m.name = deny ? "grant denied" : "grant added";
      m.ok = !deny;
      m.summary = root || "";
    }
  });
  renderChat();
}

function visibleRun() {
  return chatRuns && activeChatId ? chatRuns.get(activeChatId) : null;
}

function isVisibleStreaming() {
  const r = visibleRun();
  return !!(r && r.streaming);
}

function setVisibleChatButtons() {
  const streaming = isVisibleStreaming();
  chatStreaming = streaming;
  const send = document.getElementById("chat-send");
  const stop = document.getElementById("chat-stop");
  if (send) send.hidden = streaming;
  if (stop) stop.hidden = !streaming;
  // Background turns: never lock the sidebar while streaming.
  setSidebarDisabled(false);
}

function refreshSessionListStates() {
  if (!chatRuns) return;
  document.querySelectorAll("#chat-session-list [data-chat-id]").forEach((row) => {
    const id = row.getAttribute("data-chat-id");
    row.classList.remove("streaming", "needs-attention", "error");
    const st = chatRuns.rowState(id);
    if (st === "streaming") row.classList.add("streaming");
    if (st === "needs_attention") row.classList.add("needs-attention");
    if (st === "error") row.classList.add("error");
  });
}

/** A decision card in the visible chat was resolved: clear its pending badge. */
function clearVisiblePending() {
  const r = visibleRun();
  if (r && r.pending) {
    chatRuns.setPending(activeChatId, null);
    refreshSessionListStates();
  }
}

const PLAN_RESUME_KEY = "voxachat.chat.plan_resume";

function defaultResumeState() {
  return { mode: "normal", plan_approved: false, plan_summary: "", todos: [] };
}

let resumeState = defaultResumeState();

function persistPlanResume() {
  try {
    localStorage.setItem(PLAN_RESUME_KEY, JSON.stringify(resumeState));
  } catch (e) { /* ignore quota */ }
  if (sessionBooted && !sessionHydrating) saveActiveChat();
}

function clearPlanResume() {
  resumeState = defaultResumeState();
  try { localStorage.removeItem(PLAN_RESUME_KEY); } catch (e) { /* ignore */ }
  const soft = document.getElementById("chat-plan-resume-hint");
  if (soft) soft.hidden = true;
}

function renderSoftResumeChrome() {
  const soft = document.getElementById("chat-plan-resume-hint");
  const showHint = resumeState.mode !== "normal" || (resumeState.todos && resumeState.todos.length);
  if (soft) soft.hidden = !showHint;
  if (resumeState.todos && resumeState.todos.length) {
    const hasTodos = chatHistory.some((m) => m.kind === "todos");
    if (!hasTodos) {
      chatHistory.push({ kind: "todos", todos: resumeState.todos });
      renderChat();
    }
  }
}

function loadPlanResume() {
  try {
    const raw = localStorage.getItem(PLAN_RESUME_KEY);
    if (!raw) return;
    const j = JSON.parse(raw);
    if (!j || typeof j !== "object") return;
    resumeState = {
      mode: j.mode || "normal",
      plan_approved: !!j.plan_approved,
      plan_summary: j.plan_summary || "",
      todos: Array.isArray(j.todos) ? j.todos : [],
    };
    if (resumeState.mode !== "normal" || (resumeState.todos && resumeState.todos.length)) {
      renderSoftResumeChrome();
    }
  } catch (e) {
    clearPlanResume();
  }
}

async function apiChats(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch("/api/chats" + (path || ""), opts);
  if (r.status === 204) return null;
  if (!r.ok) throw new Error(await r.text() || r.status);
  return r.json();
}

function serializeChatMessage(m) {
  const o = { ...m };
  if (o.kind === "thinking") {
    o.streaming = false;
    if (o.collapsed === undefined) o.collapsed = true;
  }
  return o;
}

function currentModelValue() {
  const modelEl = document.getElementById("chat-model");
  return modelEl ? modelEl.value : "";
}

/** Build a PUT body from explicit state (never reads the visible chat globals). */
function buildPayloadFromState(chatId, state) {
  return {
    id: chatId,
    title: "",
    model: state.model || "",
    workspace_roots: (state.roots || []).slice(),
    resume: { ...(state.resume || defaultResumeState()) },
    messages: (state.history || []).map(serializeChatMessage),
    run_everything: state.run_everything !== false,
  };
}

function buildSessionPayload() {
  return buildPayloadFromState(activeChatId, {
    history: chatHistory,
    roots: workspaceRoots,
    resume: resumeState,
    model: currentModelValue(),
    run_everything: isRunEverything(),
  });
}

/** Save a specific chat from a state snapshot (background runs settle here). */
async function saveChatFromState(chatId, state) {
  if (!chatId) return;
  try {
    await apiChats("PUT", "/" + chatId, buildPayloadFromState(chatId, state));
  } catch (e) {
    const st = document.getElementById("chat-status");
    if (st) st.textContent = "Could not save chat";
  }
}

/** Freeze the visible streaming chat's live state onto its run before leaving it. */
function snapshotActiveIntoRun() {
  const run = visibleRun();
  if (!run || !run.streaming) return;
  run.history = chatHistory;
  run.roots = workspaceRoots;
  run.resume = resumeState;
  run.model = currentModelValue();
  run.runEverything = isRunEverything();
}

function hydrateSession(session) {
  sessionHydrating = true;
  try {
    snapshotActiveIntoRun();
    activeChatId = session.id;
    try { localStorage.setItem(ACTIVE_CHAT_KEY, activeChatId); } catch (e) { /* ignore */ }
    const run = chatRuns ? chatRuns.get(session.id) : null;
    const live = !!(run && run.streaming && run.history);
    const modelEl = document.getElementById("chat-model");
    let wantModel = "";
    if (live) {
      // Rebind to the in-flight transcript (the SSE consumer keeps mutating it).
      chatHistory = run.history;
      workspaceRoots = Array.isArray(run.roots) ? run.roots : [];
      resumeState = run.resume || defaultResumeState();
      wantModel = run.model || "";
      setRunEverythingUi(run.runEverything !== false);
    } else {
      chatHistory = [];
      (session.messages || []).forEach((m) => chatHistory.push(m));
      workspaceRoots = Array.isArray(session.workspace_roots) ? session.workspace_roots.slice() : [];
      resumeState = {
        mode: (session.resume && session.resume.mode) || "normal",
        plan_approved: !!(session.resume && session.resume.plan_approved),
        plan_summary: (session.resume && session.resume.plan_summary) || "",
        todos: (session.resume && session.resume.todos) || [],
      };
      wantModel = session.model || "";
      setRunEverythingUi(session.run_everything !== false);
    }
    persistWorkspace();
    persistPlanResume();
    if (modelEl && wantModel) {
      if ([...modelEl.options].some((o) => o.value === wantModel)) {
        modelEl.value = wantModel;
      }
    }
    renderWorkspaceRoots();
    renderSoftResumeChrome();
    pendingChatImages = [];
    renderImagePreviews();
    chatFollowOutput = true;
    renderChat({ stickToBottom: true });
    renderSessionList();
    setVisibleChatButtons();
    const st = document.getElementById("chat-status");
    if (live) {
      if (run.pending) setChatActivity("waiting", "Waiting for approval…");
      else setChatActivity("thinking");
    } else if (st) {
      st.textContent = "";
    }
  } finally {
    sessionHydrating = false;
  }
}

async function saveActiveChat() {
  if (!activeChatId) return;
  // While this chat is streaming, do not PUT a partial turn — mirror chrome into the run.
  if (isVisibleStreaming()) {
    const r = visibleRun();
    if (r) {
      r.roots = workspaceRoots.slice();
      r.resume = {
        mode: resumeState.mode,
        plan_approved: resumeState.plan_approved,
        plan_summary: resumeState.plan_summary,
        todos: resumeState.todos,
      };
      const modelEl = document.getElementById("chat-model");
      r.model = modelEl ? modelEl.value : (r.model || "");
      r.runEverything = isRunEverything();
    }
    return;
  }
  try {
    await apiChats("PUT", "/" + activeChatId, buildSessionPayload());
  } catch (e) {
    const st = document.getElementById("chat-status");
    if (st) st.textContent = "Could not save chat";
  }
}

/** Persist a chat: visible chat from live globals; others only via saveChatFromState on settle. */
async function saveChat(chatId) {
  if (!chatId) return;
  if (chatId === activeChatId) await saveActiveChat();
}

/** Save the visible chat before leaving it — streaming chats are saved on settle instead. */
async function saveActiveUnlessStreaming() {
  if (isVisibleStreaming()) return;
  await saveActiveChat();
}

async function refreshSessionIndex() {
  try {
    const data = await apiChats("GET", "");
    sessionIndex = (data && data.chats) || [];
    renderSessionList();
  } catch (e) {
    console.warn("Could not refresh chat list", e);
  }
}

function renderSessionList() {
  const el = document.getElementById("chat-session-list");
  if (!el) return;
  el.innerHTML = sessionIndex.map((c) => {
    const active = c.id === activeChatId ? " active" : "";
    return `<div class="chat-session-item${active}" data-chat-id="${escape(c.id)}">
      <span class="chat-session-title" title="${escape(c.title || "New chat")}">${escape(c.title || "New chat")}</span>
      <button type="button" class="secondary small" data-chat-delete="${escape(c.id)}" title="Delete">×</button>
    </div>`;
  }).join("");
  el.querySelectorAll("[data-chat-id]").forEach((row) => {
    row.onclick = (ev) => {
      if (ev.target.closest("[data-chat-delete]")) return;
      switchChat(row.getAttribute("data-chat-id"));
    };
  });
  el.querySelectorAll("[data-chat-delete]").forEach((btn) => {
    btn.onclick = (ev) => {
      ev.stopPropagation();
      deleteChat(btn.getAttribute("data-chat-delete"));
    };
  });
  refreshSessionListStates();
}

function setSidebarDisabled(on) {
  const sb = document.getElementById("chat-sidebar");
  if (sb) sb.classList.toggle("disabled", !!on);
}

async function switchChat(id) {
  if (!id || id === activeChatId) return;
  const seq = ++switchSeq;
  try {
    snapshotActiveIntoRun();
    await saveActiveUnlessStreaming();
    const target = chatRuns ? chatRuns.get(id) : null;
    // A just-settled chat may still be flushing to disk; wait so GET is fresh.
    if (target && target.savePromise) {
      try { await target.savePromise; } catch (e) { /* ignore */ }
    }
    let session;
    if (target && target.streaming && target.history) {
      session = { id }; // live run owns the transcript; no fetch needed
    } else {
      session = await apiChats("GET", "/" + id);
    }
    if (seq !== switchSeq) return; // a newer switch/new won; drop stale render
    hydrateSession(session);
    await refreshSessionIndex();
  } catch (e) {
    console.warn("switchChat failed", e);
    const st = document.getElementById("chat-status");
    if (st) st.textContent = "Could not switch chat";
  }
}

async function newChat() {
  const seq = ++switchSeq;
  try {
    snapshotActiveIntoRun();
    await saveActiveUnlessStreaming();
    const modelEl = document.getElementById("chat-model");
    const session = await apiChats("POST", "", {
      model: modelEl ? modelEl.value : "",
      workspace_roots: workspaceRoots.slice(),
      messages: [],
      resume: defaultResumeState(),
      run_everything: true,
    });
    if (seq !== switchSeq) {
      await refreshSessionIndex();
      return;
    }
    hydrateSession(session);
    await refreshSessionIndex();
  } catch (e) {
    console.warn("newChat failed", e);
    const st = document.getElementById("chat-status");
    if (st) st.textContent = "Could not create chat";
  }
}

async function deleteChat(id) {
  if (!confirm("Delete this chat permanently?")) return;
  try {
    // Streaming chat: stop the run first so it cannot re-save after delete.
    deletedChatIds.add(id);
    if (chatRuns) chatRuns.stop(id);
    await apiChats("DELETE", "/" + id);
    if (id === activeChatId) {
      activeChatId = null;
      await refreshSessionIndex();
      if (sessionIndex.length) {
        const nextId = sessionIndex[0].id;
        const target = chatRuns ? chatRuns.get(nextId) : null;
        const session = (target && target.streaming && target.history)
          ? { id: nextId }
          : await apiChats("GET", "/" + nextId);
        hydrateSession(session);
      } else {
        await newChat();
        return;
      }
    }
    await refreshSessionIndex();
  } catch (e) {
    deletedChatIds.delete(id);
    console.warn("deleteChat failed", e);
    const st = document.getElementById("chat-status");
    if (st) st.textContent = "Could not delete chat";
  }
}

async function bootSessions() {
  if (sessionBooted || sessionBooting) return;
  sessionBooting = true;
  try {
  await refreshSessionIndex();
  let id = null;
  try { id = localStorage.getItem(ACTIVE_CHAT_KEY); } catch (e) { /* ignore */ }
  if (!sessionIndex.length) {
    ensureDefaultWorkspace(null);
    const modelEl = document.getElementById("chat-model");
    const session = await apiChats("POST", "", {
      model: modelEl ? modelEl.value : "",
      workspace_roots: workspaceRoots.slice(),
      messages: chatHistory.slice().map(serializeChatMessage),
      resume: { ...resumeState },
      run_everything: true,
    });
    hydrateSession(session);
  } else {
    if (!id || !sessionIndex.some((c) => c.id === id)) id = sessionIndex[0].id;
    const session = await apiChats("GET", "/" + id);
    hydrateSession(session);
  }
  await refreshSessionIndex();
  sessionBooted = true;
  } finally {
    sessionBooting = false;
  }
}

function maybeBootSessions() {
  if (sessionBooted) return;
  bootSessions().catch((e) => {
    console.warn("Chat session boot failed", e);
    const st = document.getElementById("chat-status");
    if (st) st.textContent = "Could not load chats";
  });
}

async function sendChat() {
  if (!chatRuns || !activeChatId) return;
  if (isVisibleStreaming()) return;
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  const model = document.getElementById("chat-model").value;
  const visionOk = modelSupportsVision(model);
  // Only non-empty payloads count as images (empty/whitespace must not trip the gate).
  const images = pendingChatImages
    .map((x) => x && x.b64)
    .filter((b) => typeof b === "string" && b.trim());
  if (!text && images.length === 0) return;
  // Block only when this turn actually attaches images to a non-vision model.
  if (images.length && !visionOk) {
    document.getElementById("chat-status").textContent =
      "Selected model does not support images. Pick a vision model.";
    return;
  }
  if (!workspaceRoots.length) {
    document.getElementById("chat-status").textContent = "Add a workspace folder first (click +)";
    return;
  }
  input.value = "";
  input.style.height = "auto";

  // Everything below is bound to the originating chat, not whatever is visible later.
  const chatId = activeChatId;
  const hist = chatHistory;
  hist.push({
    role: "user",
    content: text || (images.length ? "(image)" : ""),
    images: images.length ? images : undefined,
  });
  pendingChatImages = [];
  renderImagePreviews();
  const placeholder = { role: "assistant", content: "" };
  hist.push(placeholder);
  chatFollowOutput = true;
  renderChat({ stickToBottom: true });

  const controller = new AbortController();
  const run = chatRuns.start(chatId, controller);
  run.history = hist;
  run.roots = workspaceRoots;
  run.resume = resumeState;
  run.model = model;
  run.runEverything = isRunEverything();
  const runEverything = run.runEverything;
  const turnUserIdx = hist.length - 2;

  setVisibleChatButtons();
  refreshSessionListStates();
  const concurrencyWarn = chatRuns.shouldWarnConcurrency();
  setChatActivity("thinking", concurrencyWarn ? "3+ chats streaming…" : undefined);

  const isVisible = () => chatId === activeChatId;
  const isLive = () => {
    const r = chatRuns.get(chatId);
    return !!(r && r.controller === controller);
  };
  const act = (phase, detail) => { if (isVisible()) setChatActivity(phase, detail); };
  const paint = () => { if (isVisible()) renderChat(); };
  const paintLazy = () => { if (isVisible()) scheduleChatRender(); };
  const markPending = (kind, id) => {
    chatRuns.setPending(chatId, { kind, id });
    refreshSessionListStates();
  };
  const clearPending = () => {
    const r = chatRuns.get(chatId);
    if (r && r.pending) {
      chatRuns.setPending(chatId, null);
      refreshSessionListStates();
    }
  };
  // Resume state lives on the run; mirror to globals/localStorage only when visible.
  const resumeChanged = () => {
    if (!isVisible()) return;
    resumeState = run.resume;
    persistPlanResume();
    renderSoftResumeChrome();
  };
  const clearResume = () => {
    if (isVisible()) {
      clearPlanResume();
      run.resume = resumeState;
    } else {
      run.resume = defaultResumeState();
    }
  };
  // Clear needs-attention only when a gate resolves or the turn ends — not on tokens.
  const CLEAR_PENDING_TYPES = new Set([
    "plan_decision", "writes_batch_resolved", "tool_start",
    "segment_done", "done", "cancelled", "error",
  ]);

  try {
    const messagesToSend = hist
      .filter(m => m.role === "user" || m.role === "assistant")
      .filter(m => m !== placeholder)
      .filter(m => (m.content || "") !== "" || (m.role === "user" && m.images && m.images.length))
      .map(m => {
        const o = { role: m.role, content: m.content };
        // Non-vision models: never attach images (incl. prior-turn history) so
        // text-only sends are not rejected by the server vision gate.
        if (visionOk && m.role === "user" && m.images && m.images.length) o.images = m.images;
        return o;
      });

    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: messagesToSend,
        model,
        workspace_roots: run.roots,
        // Jev always decides. Run everything is per-chat (default on): auto-approves
        // write/shell/sensitive gates; plan Approve and workspace grant stay manual.
        run_everything: runEverything,
        writes_armed: runEverything,
        tools_mode: "always",
        mode: run.resume.mode,
        plan_approved: run.resume.plan_approved,
        plan_summary: run.resume.plan_summary,
        todos: run.resume.todos,
      }),
      signal: controller.signal,
    });
    if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);

    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let acc = "";
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6).trim();
        if (!payload) continue;
        let j;
        try { j = JSON.parse(payload); } catch (e) { continue; }
        // Superseded/aborted run: ignore late events.
        if (!isLive()) continue;
        if (CLEAR_PENDING_TYPES.has(j.type)) clearPending();

        if (j.type === "thinking") {
          const row = ensureOpenThinking(hist, placeholder);
          appendThinkingDelta(row, j.content || "");
          paintLazy();
          act("thinking");
        } else if (j.type === "token") {
          collapseOpenThinking(hist);
          acc += j.content || "";
          placeholder.content = acc;
          paintLazy();
          act("thinking");
        } else if (j.type === "tool_start") {
          collapseOpenThinking(hist);
          hist.splice(hist.length - 1, 0, {
            kind: "tool_chip",
            name: j.name,
            path: toolPathFromArgs(j.args),
            summary: summarizeArgs(j.args),
            ok: null,
          });
          paint();
          const phase = verbForTool(j.name);
          act(phase, summarizeArgs(j.args));
        } else if (j.type === "tool_result") {
          for (let i = hist.length - 1; i >= 0; i--) {
            const m = hist[i];
            if (m.kind === "tool_chip" && m.name === j.name && m.ok === null) {
              m.ok = !!j.ok;
              m.summary = formatToolResultSummary(m, j.content || "");
              break;
            }
          }
          paint();
          act("thinking");
        } else if (j.type === "plan_pending") {
          hist.splice(hist.length - 1, 0, {
            kind: "plan_pending",
            id: j.id,
            summary: j.summary || "",
            todos: j.todos || [],
          });
          run.resume = {
            mode: "awaiting_approval",
            plan_approved: false,
            plan_summary: j.summary || "",
            todos: j.todos || [],
          };
          markPending("plan", j.id);
          resumeChanged();
          paint();
          act("waiting", "Waiting for plan approval…");
        } else if (j.type === "plan_decision") {
          const m = hist.find((x) => x.kind === "plan_pending" && x.id === j.id);
          if (m) {
            m.kind = "plan_resolved";
            m.action = j.action;
            if (j.action === "approve") m.name = "Plan approved";
            else if (j.action === "revise") m.name = "Plan revise requested";
            else m.name = "Plan cancelled";
          }
          if (j.action === "approve") {
            run.resume.mode = "implementing";
            run.resume.plan_approved = true;
            resumeChanged();
          } else if (j.action === "revise") {
            run.resume.mode = "planning";
            run.resume.plan_approved = false;
            resumeChanged();
          } else {
            clearResume();
          }
          paint();
        } else if (j.type === "todos") {
          const idx = [...hist].map((x) => x.kind).lastIndexOf("todos");
          const entry = { kind: "todos", todos: j.todos || [] };
          if (idx >= 0) hist[idx] = entry;
          else hist.splice(hist.length - 1, 0, entry);
          run.resume.todos = j.todos || [];
          resumeChanged();
          paint();
        } else if (j.type === "write_queued") {
          let panel = hist.find(
            (m) => m.kind === "writes_batch_pending" && m.id === j.batch_id
          );
          if (!panel) {
            panel = {
              kind: "writes_batch_pending",
              id: j.batch_id,
              items: [],
              awaitingDecision: false,
            };
            hist.push(panel);
          }
          const idx = panel.items.findIndex((it) => it.item_id === j.item_id);
          const row = {
            item_id: j.item_id,
            tool: j.tool,
            path: j.path,
            preview: j.preview || "",
            args: j.args || {},
            checked: true,
          };
          if (idx >= 0) panel.items[idx] = row;
          else panel.items.push(row);
          paint();
        } else if (j.type === "writes_batch_pending") {
          let panel = hist.find(
            (m) => m.kind === "writes_batch_pending" && m.id === j.id
          );
          if (!panel) {
            panel = { kind: "writes_batch_pending", id: j.id, items: [], awaitingDecision: true };
            hist.push(panel);
          }
          panel.awaitingDecision = true;
          panel.items = (j.items || []).map((it) => ({
            item_id: it.item_id,
            tool: it.tool,
            path: it.path,
            preview: it.preview || "",
            args: it.args || {},
            checked: true,
          }));
          markPending("writes_batch", j.id);
          paint();
          act("waiting", "Waiting for write approval…");
        } else if (j.type === "writes_batch_resolved") {
          const panel = hist.find(
            (m) => m.kind === "writes_batch_pending" && m.id === j.id
          );
          if (panel) {
            panel.kind = "tool_chip";
            const nApp = (j.applied || []).length;
            const nDen = (j.denied || []).length;
            panel.name = `Writes batch: ${nApp} applied, ${nDen} denied`;
            panel.ok = nApp > 0 || nDen >= 0;
            panel.summary = "";
          }
          paint();
          act("idle");
        } else if (j.type === "write_pending") {
          hist.splice(hist.length - 1, 0, {
            kind: "write_pending",
            id: j.id,
            tool: j.tool,
            args: j.args,
            preview: j.preview,
          });
          markPending("write", j.id);
          paint();
          act("waiting", "Waiting for write approval…");
        } else if (j.type === "shell_pending") {
          hist.splice(hist.length - 1, 0, {
            kind: "shell_pending",
            id: j.id,
            tool: j.tool,
            args: j.args,
            preview: j.preview,
          });
          markPending("shell", j.id);
          paint();
          act("waiting", "Waiting for shell approval…");
        } else if (j.type === "workspace_grant_needed") {
          hist.splice(hist.length - 1, 0, {
            kind: "grant_needed",
            id: j.id,
            path: j.path,
            suggested_root: j.suggested_root,
            reason: j.reason,
          });
          markPending("grant", j.id);
          paint();
          act("waiting", "Waiting for workspace grant…");
        } else if (j.type === "decision_pick") {
          hist.splice(hist.length - 1, 0, {
            kind: "decision_pick",
            option: j.option,
            confidence: j.confidence,
            backend: j.backend,
          });
          paint();
        } else if (j.type === "tool_permission_pending") {
          hist.splice(hist.length - 1, 0, {
            kind: "tool_permission_pending",
            id: j.id,
            tool: j.tool,
          });
          markPending("tool_permission", j.id);
          paint();
          act("waiting", "Waiting for tool approval…");
        } else if (j.type === "segment_done") {
          // Mid-turn assistant note (e.g. fake ask_clarify narration): seal it
          // into history and keep the stream open with a fresh placeholder.
          collapseOpenThinking(hist);
          const sealedText = (acc || (typeof j.message === "string" ? j.message : "") || placeholder.content || "").trim();
          const sealed = { role: "assistant", content: sealedText };
          const idx = hist.indexOf(placeholder);
          if (idx >= 0) hist.splice(idx, 0, sealed);
          else hist.push(sealed);
          placeholder.content = "";
          acc = "";
          paint();
          act("thinking");
        } else if (j.type === "done") {
          collapseOpenThinking(hist);
          // Prefer streamed tokens; fall back to done.message when the model
          // only produced thinking (or the client missed tokens).
          if (!acc && typeof j.message === "string" && j.message) {
            placeholder.content = j.message;
          } else if (!acc && !(placeholder.content || "").trim()) {
            placeholder.content =
              "(Empty reply — model produced no visible text. Ask to continue.)";
          }
          paint();
          act("idle");
        } else if (j.type === "cancelled") {
          collapseOpenThinking(hist);
          cancelPendingApprovalsForTurn(turnUserIdx, hist);
          clearResume();
          act("stopped");
          paint();
          // Keep placeholder.content / acc as-is (partial tokens).
        } else if (j.type === "error") {
          collapseOpenThinking(hist);
          placeholder.content = (placeholder.content || "") + "\n(error: " + (j.message || "unknown") + ")";
          chatRuns.setError(chatId, j.message || "unknown");
          paint();
          act("error");
        } else if (j.message && j.message.content) {
          acc += j.message.content;
          placeholder.content = acc;
          paint();
        }
      }
    }
  } catch (e) {
    collapseOpenThinking(hist);
    if (e.name === "AbortError") {
      cancelPendingApprovalsForTurn(turnUserIdx, hist);
      clearResume();
      act("stopped");
      paint();
    } else {
      placeholder.content = "(error: " + e.message + ")";
      chatRuns.setError(chatId, e.message || "error");
      paint();
      act("error");
    }
  } finally {
    // Only the owning controller may settle this chat (superseded runs stay silent).
    if (isLive()) {
      const visible = isVisible();
      const state = {
        history: hist,
        roots: visible ? workspaceRoots : run.roots,
        resume: visible ? resumeState : run.resume,
        model: visible ? currentModelValue() : run.model,
        run_everything: visible ? isRunEverything() : run.runEverything,
      };
      chatRuns.clear(chatId);
      setVisibleChatButtons();
      refreshSessionListStates();
      if (sessionBooted && !deletedChatIds.has(chatId)) {
        const p = saveChatFromState(chatId, state);
        run.savePromise = p;
        try { await p; } finally { if (run.savePromise === p) run.savePromise = null; }
      }
      refreshSessionIndex();
    }
  }
}

function PathParent(p) {
  if (!p) return "";
  const cleaned = p.replace(/\/+$/, "");
  const idx = cleaned.lastIndexOf("/");
  return idx > 0 ? cleaned.slice(0, idx) : cleaned;
}

function toolPathFromArgs(args) {
  if (!args || typeof args !== "object") return "";
  const p = args.path || args.root || args.src || args.dst || "";
  return p ? String(p) : "";
}

function summarizeArgs(args) {
  if (!args || typeof args !== "object") return "";
  const p = toolPathFromArgs(args);
  return p || JSON.stringify(args).slice(0, 80);
}

function formatToolResultSummary(chip, content) {
  const path = (chip && chip.path) || "";
  const text = String(content || "").trim();
  if (chip && chip.ok) {
    // Prefer the path label; keep a short content peek for list_dir previews.
    if (path) return path;
    return text.slice(0, 160);
  }
  // Errors: keep the full path from args (not truncated errno text).
  const err = text.replace(/^error:\s*/i, "");
  if (path) return path + " — " + err.slice(0, 200);
  return err.slice(0, 280);
}

function stopChat() {
  // Stop = visible chat only; other runs keep streaming.
  if (!activeChatId || !chatRuns) return;
  const run = chatRuns.get(activeChatId);
  chatRuns.stop(activeChatId);
  clearPlanResume();
  if (run) run.resume = resumeState; // keep run aliased to the reset state
}

const chatInput = document.getElementById("chat-input");
const chatInputArea = document.querySelector(".chat-input-area");
const chatImageInput = document.getElementById("chat-image-input");

document.getElementById("chat-attach").addEventListener("click", () => {
  if (chatImageInput) chatImageInput.click();
});

if (chatImageInput) {
  chatImageInput.addEventListener("change", () => {
    addChatImageFiles(chatImageInput.files);
    chatImageInput.value = "";
  });
}

function handleChatImagePaste(e) {
  const items = e.clipboardData;
  if (!items) return;
  const files = [];
  if (items.files && items.files.length) {
    for (const f of items.files) {
      if (f.type && f.type.startsWith("image/")) files.push(f);
    }
  }
  for (const item of items.items || []) {
    if (item.kind === "file" && item.type.startsWith("image/")) {
      const f = item.getAsFile();
      if (f) files.push(f);
    }
  }
  if (!files.length) return;
  e.preventDefault();
  addChatImageFiles(files);
}

if (chatInputArea) chatInputArea.addEventListener("paste", handleChatImagePaste);

chatInput.addEventListener("input", () => {
  chatInput.style.height = "auto";
  chatInput.style.height = Math.min(chatInput.scrollHeight, 200) + "px";
});

chatInput.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    sendChat();
  }
});

document.getElementById("chat-send").addEventListener("click", sendChat);
document.getElementById("chat-stop").addEventListener("click", stopChat);

document.getElementById("chat-clear").addEventListener("click", () => {
  if (isVisibleStreaming()) return;
  chatHistory.length = 0;
  pendingChatImages = [];
  renderImagePreviews();
  clearPlanResume();
  renderChat();
  document.getElementById("chat-status").textContent = "";
  if (sessionBooted) saveActiveChat().then(() => refreshSessionIndex());
});

function isRunEverything() {
  return !!runEverythingByChat;
}

function setRunEverythingUi(on) {
  runEverythingByChat = !!on;
  const el = document.getElementById("chat-run-everything");
  if (el) el.checked = runEverythingByChat;
}

// Per-chat setting, default on; the checkbox is bound to the visible chat.
(function initRunEverything() {
  const el = document.getElementById("chat-run-everything");
  if (!el) return;
  el.checked = true;
  el.addEventListener("change", () => {
    runEverythingByChat = !!el.checked;
    const run = visibleRun();
    if (run) run.runEverything = runEverythingByChat;
    if (sessionBooted && activeChatId && !sessionHydrating) {
      saveChat(activeChatId);
    }
  });
})();

document.getElementById("chat-workspace-add").addEventListener("click", async () => {
  const btn = document.getElementById("chat-workspace-add");
  const status = document.getElementById("chat-status");
  if (btn) btn.disabled = true;
  try {
    const r = await fetch("/api/chat/pick-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        initial_dir: workspaceRoots.length ? workspaceRoots[workspaceRoots.length - 1] : null,
      }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (status) status.textContent = j.detail || "Could not open folder picker";
      return;
    }
    if (j.cancelled || !j.path) return;
    addWorkspaceRoot(j.path);
  } catch (e) {
    if (status) status.textContent = e.message || "Could not open folder picker";
  } finally {
    if (btn) btn.disabled = false;
  }
});

if (typeof setApplyWorkspaceGetter === "function") {
  setApplyWorkspaceGetter(() => workspaceRoots.slice());
}

renderWorkspaceRoots();
loadPlanResume();
renderChat();

// Populate chat models from Ollama, with localStorage persistence for default selection
const CHAT_MODEL_KEY = "voxachat-chat-model";

async function loadChatModels() {
  const sel = document.getElementById("chat-model");
  if (!sel) return;
  try {
    const r = await fetch("/api/models");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    const models = (data.models || []).map(m => m.name || m.model).filter(Boolean);
    if (models.length === 0) {
      sel.innerHTML = '<option value="">(no models — ollama pull mistral-nemo)</option>';
      sel.disabled = true;
      modelsLoaded = true;
      maybeBootSessions();
      return;
    }
    sel.innerHTML = models.map(m =>
      `<option value="${escape(m)}">${escape(m)}</option>`
    ).join("");
    sel.disabled = false;
    modelsLoaded = true;
    const saved = localStorage.getItem(CHAT_MODEL_KEY);
    if (saved && models.includes(saved)) {
      sel.value = saved;
    }
    if (!sel.dataset.bound) {
      sel.dataset.bound = "1";
      sel.addEventListener("change", () => {
        localStorage.setItem(CHAT_MODEL_KEY, sel.value);
        if (sessionBooted && !sessionHydrating) saveActiveChat();
      });
    }
    maybeBootSessions();
  } catch (e) {
    if (!modelsLoaded) {
      sel.innerHTML = '<option value="">(loading models...)</option>';
      sel.disabled = true;
    }
  }
}
loadChatModels();

const chatNewBtn = document.getElementById("chat-new");
if (chatNewBtn) chatNewBtn.addEventListener("click", () => newChat());

