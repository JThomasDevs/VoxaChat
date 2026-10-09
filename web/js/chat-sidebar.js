(function (w) {
  const KEY = "voxachat-chat-sidebar-collapsed";

  function isCollapsed() {
    return localStorage.getItem(KEY) === "1";
  }

  function setCollapsed(collapsed) {
    const sb = document.getElementById("chat-sidebar");
    const btn = document.getElementById("chat-sidebar-toggle");
    if (sb) sb.classList.toggle("collapsed", !!collapsed);
    if (btn) btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    try {
      localStorage.setItem(KEY, collapsed ? "1" : "0");
    } catch (_) {}
  }

  function toggle() {
    setCollapsed(!isCollapsed());
    return isCollapsed();
  }

  function init() {
    setCollapsed(isCollapsed());
    const btn = document.getElementById("chat-sidebar-toggle");
    if (btn) btn.addEventListener("click", toggle);
  }

  w.VoxaChatSidebar = { init, isCollapsed, setCollapsed, toggle };
})(typeof window !== "undefined" ? window : globalThis);
