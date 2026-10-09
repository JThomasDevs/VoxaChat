(function (global) {
  const SOFT_CONCURRENT_WARN = 3;

  function createRunMap() {
    const runs = new Map();

    function ensure(chatId) {
      const id = String(chatId || "");
      if (!id) throw new Error("chatId required");
      let r = runs.get(id);
      if (!r) {
        r = {
          chatId: id,
          controller: null,
          streaming: false,
          pending: null,
          error: null,
        };
        runs.set(id, r);
      }
      return r;
    }

    function get(chatId) {
      return runs.get(String(chatId || "")) || null;
    }

    function start(chatId, controller) {
      const r = ensure(chatId);
      if (r.controller && r.controller !== controller) {
        try { r.controller.abort(); } catch (e) { /* ignore */ }
      }
      r.controller = controller || null;
      r.streaming = true;
      r.pending = null;
      r.error = null;
      return r;
    }

    function stop(chatId) {
      const r = get(chatId);
      if (!r || !r.streaming) return false;
      if (r.controller) {
        try { r.controller.abort(); } catch (e) { /* ignore */ }
      }
      return true;
    }

    function setPending(chatId, pending) {
      const r = ensure(chatId);
      r.pending = pending || null;
    }

    function setError(chatId, message) {
      const r = ensure(chatId);
      r.error = message ? String(message) : null;
    }

    function clear(chatId) {
      const r = get(chatId);
      if (!r) return;
      r.controller = null;
      r.streaming = false;
      r.pending = null;
      // keep error until next start
    }

    function streamingCount() {
      let n = 0;
      runs.forEach((r) => { if (r.streaming) n += 1; });
      return n;
    }

    function rowState(chatId) {
      const r = get(chatId);
      if (!r) return "idle";
      if (r.pending) return "needs_attention";
      if (r.streaming) return "streaming";
      if (r.error) return "error";
      return "idle";
    }

    function shouldWarnConcurrency() {
      return streamingCount() >= SOFT_CONCURRENT_WARN;
    }

    return {
      get, ensure, start, stop, setPending, setError, clear,
      streamingCount, rowState, shouldWarnConcurrency,
    };
  }

  global.SOFT_CONCURRENT_WARN = SOFT_CONCURRENT_WARN;
  global.createRunMap = createRunMap;
})(typeof globalThis !== "undefined" ? globalThis : this);
