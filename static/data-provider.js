(function () {
  "use strict";

  const params = new URLSearchParams(window.location.search);
  const forceMock = params.get("mock") === "1";
  const disableMock = params.get("mock") === "0";

  const hostname = window.location.hostname;
  const port = window.location.port;

  const isLocalhost = ["localhost", "127.0.0.1", ""].includes(hostname);
  const isVsCodeLiveServer = isLocalhost && ["5500", "5501"].includes(port);
  const isDockerPort = isLocalhost && port === "8000";

  const USE_MOCKS = !disableMock && !isDockerPort && (forceMock || isVsCodeLiveServer);

  let mockScriptLoaded = false;
  let mockScriptPromise = null;

  function ensureMockLoaded() {
    if (!USE_MOCKS) return Promise.resolve();
    if (mockScriptLoaded) return Promise.resolve();
    if (mockScriptPromise) return mockScriptPromise;

    mockScriptPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/static/mock-data.js";
      script.onload = () => {
        mockScriptLoaded = true;
        resolve();
      };
      script.onerror = () => {
        console.error("Failed to load mock-data.js");
        reject(new Error("Failed to load mock data"));
      };
      document.head.appendChild(script);
    });

    return mockScriptPromise;
  }

  function injectMockBadge() {
    if (!USE_MOCKS || document.getElementById("mock-badge")) return;
    const badge = document.createElement("div");
    badge.id = "mock-badge";
    badge.textContent = "DEV MOCK DATA";
    Object.assign(badge.style, {
      position: "fixed",
      bottom: "16px",
      left: "16px",
      zIndex: "9999",
      background: "oklch(53% 0.17 29)",
      color: "white",
      padding: "6px 10px",
      borderRadius: "6px",
      fontFamily: "monospace",
      fontSize: "11px",
      fontWeight: "bold",
      boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
      pointerEvents: "none",
      textTransform: "uppercase"
    });
    document.body.appendChild(badge);
  }

  if (USE_MOCKS) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", injectMockBadge);
    } else {
      injectMockBadge();
    }
  }

  async function fetchReal(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: { Accept: "application/json", ...(options.headers || {}) },
    });
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`);
    }
    return response.json();
  }

  window.DataProvider = {
    isMockActive: USE_MOCKS,
    
    getDashboard: async () => {
      if (USE_MOCKS) {
        await ensureMockLoaded();
        return window.MockData.dashboard;
      }
      return fetchReal("/api/dashboard");
    },

    getSubscribers: async () => {
      if (USE_MOCKS) {
        await ensureMockLoaded();
        return window.MockData.subscribers;
      }
      return fetchReal("/api/subscribers");
    },

    getFeeds: async () => {
      if (USE_MOCKS) {
        await ensureMockLoaded();
        return window.MockData.feeds;
      }
      return fetchReal("/api/feeds");
    },

    getAnalytics: async () => {
      if (USE_MOCKS) {
        await ensureMockLoaded();
        return window.MockData.analytics;
      }
      return fetchReal("/api/analytics");
    },

    getLlmConfig: async () => {
      if (USE_MOCKS) {
        await ensureMockLoaded();
        return window.MockData.llmConfig;
      }
      return fetchReal("/api/llm-config");
    },

    runPipeline: async (period) => {
      if (USE_MOCKS) {
        await ensureMockLoaded();
        console.log(`[MOCK] runPipeline called for period: ${period}`);
        return { status: "ok", run_id: "mock_run_999" };
      }
      return fetchReal(`/run-pipeline/${encodeURIComponent(period)}`, { method: "POST" });
    },

    runPipelineLast24h: async () => {
      if (USE_MOCKS) {
        await ensureMockLoaded();
        console.log(`[MOCK] runPipelineLast24h called`);
        return { status: "ok", summaryCount: 42 };
      }
      return fetchReal("/api/run-pipeline/last-24h", { method: "POST" });
    },

    toggleFeed: async (id) => {
      if (USE_MOCKS) {
        await ensureMockLoaded();
        console.log(`[MOCK] toggleFeed called for id: ${id}`);
        const feed = window.MockData.feeds.find(f => f.id === id);
        if (feed) feed.active = !feed.active;
        return { status: "ok" };
      }
      return fetchReal(`/api/feeds/${encodeURIComponent(id)}/toggle`, { method: "POST" });
    },

    toggleSubscriber: async (id) => {
      if (USE_MOCKS) {
        await ensureMockLoaded();
        console.log(`[MOCK] toggleSubscriber called for id: ${id}`);
        const sub = window.MockData.subscribers.find(s => s.id === id);
        if (sub) sub.active = !sub.active;
        return { status: "ok" };
      }
      return fetchReal(`/api/subscribers/${encodeURIComponent(id)}/toggle`, { method: "POST" });
    }
  };

})();