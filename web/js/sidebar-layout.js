// App sidebar layout — desktop: full ↔ mini (icon rail); mobile: full ↔ off
(function (w) {
  "use strict";

  const KEY = "voxachat-sidebar-mode";
  const MOBILE_MAX = 768;

  let mode = "full";

  function isMobile() {
    return w.innerWidth < MOBILE_MAX;
  }

  function normalizeMode(m) {
    if (m === "full" || m === "mini" || m === "off") return m;
    return "full";
  }

  function setHtmlModeClasses(m) {
    const root = document.documentElement;
    root.classList.toggle("voxa-sidebar-mini", m === "mini");
    root.classList.toggle("voxa-sidebar-off", m === "off");
  }

  function applyDom(m) {
    const sidebar = document.getElementById("app-sidebar");
    const rail = document.getElementById("icon-rail");
    const backdrop = document.getElementById("sidebar-backdrop");
    const toggle = document.getElementById("sidebar-toggle-btn");

    if (sidebar) {
      sidebar.classList.toggle("hidden", m !== "full");
    }
    if (rail) {
      // Icon rail visible in mini (desktop). Hidden in full + off.
      rail.classList.toggle("rail-hidden", m !== "mini");
    }
    if (backdrop) {
      backdrop.classList.toggle("visible", isMobile() && m === "full");
    }
    if (toggle) {
      toggle.setAttribute("aria-expanded", m === "full" ? "true" : "false");
      toggle.title = m === "full" ? "Collapse to icons" : "Expand sidebar";
    }
  }

  function readStoredMode() {
    try {
      const stored = localStorage.getItem(KEY);
      if (stored) return normalizeMode(stored);
    } catch (_) {}
    return "full";
  }

  function persistMode(m) {
    try {
      localStorage.setItem(KEY, m);
    } catch (_) {}
  }

  function getMode() {
    return mode;
  }

  function setMode(next) {
    mode = normalizeMode(next);
    // Desktop never lands on off via setMode from toggle; allow explicit off for mobile.
    if (!isMobile() && mode === "off") mode = "mini";
    setHtmlModeClasses(mode);
    applyDom(mode);
    persistMode(mode);
    return mode;
  }

  /** Desktop: full ↔ mini. Mobile: full ↔ off. */
  function cycleMode() {
    if (isMobile()) {
      return setMode(mode === "full" ? "off" : "full");
    }
    return setMode(mode === "full" ? "mini" : "full");
  }

  function onCycleClick(e) {
    if (e && e.stopPropagation) e.stopPropagation();
    cycleMode();
  }

  function onBackdropClick() {
    if (isMobile()) setMode("off");
  }

  function bindOnce(el, handler) {
    if (!el || el._voxaSidebarBound) return;
    el._voxaSidebarBound = true;
    el.addEventListener("click", handler);
  }

  function init() {
    mode = readStoredMode();
    if (!isMobile() && mode === "off") mode = "mini";
    setHtmlModeClasses(mode);
    applyDom(mode);

    bindOnce(document.getElementById("hamburger-btn"), onCycleClick);
    bindOnce(document.getElementById("sidebar-toggle-btn"), onCycleClick);
    bindOnce(document.getElementById("sidebar-backdrop"), onBackdropClick);

    w.addEventListener("resize", function () {
      if (!isMobile() && mode === "off") setMode("mini");
      else applyDom(mode);
    });
  }

  w.VoxaSidebar = {
    init: init,
    getMode: getMode,
    setMode: setMode,
    cycleMode: cycleMode,
  };
})(typeof window !== "undefined" ? window : globalThis);
