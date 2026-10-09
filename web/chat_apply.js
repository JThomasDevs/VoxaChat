(function (global) {
  let getRoots = () => [];

  function setApplyWorkspaceGetter(fn) {
    getRoots = typeof fn === "function" ? fn : () => [];
  }

  function guessFencePath(infoOrGuess, proseBefore, workspaceRoots) {
    const roots = workspaceRoots || [];
    let guess = (infoOrGuess || "").trim();
    if (!guess && proseBefore) {
      const re = /`([^`]+)`|(\.?\.?\/[\w./-]+)|([\w.-]+\.[a-zA-Z0-9]{1,8})/g;
      let m;
      let last = "";
      while ((m = re.exec(proseBefore)) !== null) {
        const tok = (m[1] || m[2] || m[3] || "").trim();
        if (global.isPathLike && global.isPathLike(tok)) last = tok;
      }
      guess = last;
    }
    if (!guess) return "";
    if (guess.startsWith("/")) return guess;
    const root = (roots[0] || "").replace(/\/+$/, "");
    if (!root) return guess;
    const rel = guess.replace(/^\.\//, "");
    return root + "/" + rel;
  }

  function ensureConfirmEl() {
    let el = document.getElementById("chat-apply-confirm");
    if (el) return el;
    el = document.createElement("div");
    el.id = "chat-apply-confirm";
    el.className = "chat-apply-confirm";
    el.hidden = true;
    el.innerHTML =
      `<strong>Apply to file</strong>` +
      `<p class="apply-note">Existing files will be overwritten.</p>` +
      `<label>Path <input type="text" id="chat-apply-path" size="48"></label>` +
      `<div class="pending-actions">` +
      `<button type="button" id="chat-apply-ok">Apply</button>` +
      `<button type="button" class="secondary" id="chat-apply-cancel">Cancel</button>` +
      `</div>`;
    const panel = document.getElementById("panel-chat") || document.body;
    panel.appendChild(el);
    return el;
  }

  let pendingIndex = null;

  async function confirmApply() {
    const input = document.getElementById("chat-apply-path");
    const path = (input && input.value || "").trim();
    const fences = global.__chatFences || [];
    const fence = pendingIndex != null ? fences[pendingIndex] : null;
    const box = document.getElementById("chat-apply-confirm");
    if (!fence || !path) return;
    const status = document.getElementById("chat-status");
    try {
      const r = await fetch("/api/chat/apply-snippet", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path,
          content: fence.content,
          workspace_roots: getRoots(),
        }),
      });
      const t = await r.text();
      let j = {};
      try { j = JSON.parse(t); } catch (e) { /* ignore */ }
      if (!r.ok) {
        const detail = j.detail || t.slice(0, 200) || r.status;
        if (status) status.textContent = "Apply failed: " + detail;
        return;
      }
      if (status) status.textContent = "Applied → " + (j.path || path);
      if (box) box.hidden = true;
      pendingIndex = null;
    } catch (e) {
      if (status) status.textContent = "Apply failed: " + (e.message || e);
    }
  }

  function openApplyConfirm(fenceIndex) {
    const fences = global.__chatFences || [];
    const fence = fences[fenceIndex];
    if (!fence) return;
    pendingIndex = fenceIndex;
    const roots = getRoots();
    const path = guessFencePath(fence.pathGuess, fence.proseBefore, roots);
    const box = ensureConfirmEl();
    const input = document.getElementById("chat-apply-path");
    if (input) input.value = path;
    box.hidden = false;
    const ok = document.getElementById("chat-apply-ok");
    const cancel = document.getElementById("chat-apply-cancel");
    if (ok) ok.onclick = () => { confirmApply(); };
    if (cancel) cancel.onclick = () => { box.hidden = true; pendingIndex = null; };
  }

  function bindApplyControls(rootEl) {
    if (!rootEl) return;
    rootEl.querySelectorAll("[data-fence-apply]").forEach((btn) => {
      btn.onclick = (e) => {
        e.preventDefault();
        openApplyConfirm(Number(btn.getAttribute("data-fence-apply")));
      };
    });
  }

  global.setApplyWorkspaceGetter = setApplyWorkspaceGetter;
  global.guessFencePath = guessFencePath;
  global.openApplyConfirm = openApplyConfirm;
  global.bindApplyControls = bindApplyControls;
})(typeof window !== "undefined" ? window : globalThis);
