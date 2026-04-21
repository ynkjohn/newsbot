(function () {
  const CAT_LABELS = {
    tech: "Tecnologia",
    "economia-cripto": "Criptoativos",
    "economia-brasil": "Economia Brasil",
    "economia-mundao": "Economia Global",
    "politica-brasil": "Política Brasil",
    "politica-mundao": "Geopolítica",
  };

  const GROUP_MAPPING = {
    Brasil: ["politica-brasil", "economia-brasil"],
    Economia: ["economia-cripto", "economia-brasil", "economia-mundao"],
    Mundo: ["politica-mundao", "economia-mundao"],
    Tech: ["tech"],
  };

  const PERIOD_LABELS = {
    morning: "Manhã",
    midday: "Meio-dia",
    afternoon: "Tarde",
    evening: "Noite",
  };

  const RUN_STATUS_META = {
    running: { label: "Rodando", tone: "warn" },
    completed: { label: "Concluído", tone: "ok" },
    failed: { label: "Falhou", tone: "danger" },
  };

  const DEFAULT_SCHEDULE_HOURS = [7, 12, 17, 21];
  const AUTO_REFRESH_MS = 60_000;
  const STORAGE_KEY = "newsbot-dashboard-v6";

  const reducedMotionQuery =
    typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-reduced-motion: reduce)")
      : { matches: false };

  const state = {
    currentView: "ops",
    currentGroup: "Todas",
    filters: {
      search: "",
      sort: "newest",
      periods: new Set(),
    },
    dashboard: null,
    whatsapp: { connected: false, status: "carregando" },
    loading: false,
    toastTimer: null,
    reducedMotion: !!reducedMotionQuery.matches,
    drawer: {
      isOpen: false,
      isAnimating: false,
      summaryId: null,
      opener: null,
    },
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    if (value === undefined || value === null) return "";
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function normalizeText(value) {
    return String(value || "")
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();
  }

  function setText(id, value) {
    const node = byId(id);
    if (node) node.textContent = value ?? "--";
  }

  function setTagState(id, text, tone) {
    const node = byId(id);
    if (!node) return;

    node.textContent = text;
    node.classList.remove("is-ok", "is-warn", "is-danger");
    if (tone === "ok") node.classList.add("is-ok");
    if (tone === "warn") node.classList.add("is-warn");
    if (tone === "danger") node.classList.add("is-danger");
  }

  function formatCategory(category) {
    return CAT_LABELS[category] || category || "Categoria";
  }

  function formatPeriod(period) {
    return PERIOD_LABELS[period] || period || "Janela";
  }

  function formatTime(value) {
    if (!value) return "--";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "--";
    return date.toLocaleTimeString("pt-BR", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatDateTime(value) {
    if (!value) return "--";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "--";
    return date.toLocaleString("pt-BR", {
      hour: "2-digit",
      minute: "2-digit",
      day: "2-digit",
      month: "2-digit",
    });
  }

  function formatSourceLabel(url) {
    try {
      const parsed = new URL(url);
      return parsed.hostname.replace(/^www\./, "");
    } catch (error) {
      return url;
    }
  }

  function pluralize(value, singular, plural) {
    return `${value} ${value === 1 ? singular : plural}`;
  }

  function showToast(message, isError) {
    const toast = byId("toast");
    if (!toast) return;

    toast.textContent = message;
    toast.classList.toggle("error", !!isError);
    toast.classList.add("show");

    if (state.toastTimer) window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => {
      toast.classList.remove("show");
      toast.classList.remove("error");
    }, 3200);
  }

  function getScheduleHours() {
    const hours = state.dashboard?.pipelineHours;
    if (!Array.isArray(hours)) return DEFAULT_SCHEDULE_HOURS;

    const valid = hours
      .map((hour) => Number(hour))
      .filter((hour) => Number.isFinite(hour) && hour >= 0 && hour <= 23)
      .sort((a, b) => a - b);

    return valid.length ? valid : DEFAULT_SCHEDULE_HOURS;
  }

  function computeNextWindow() {
    const hours = getScheduleHours();
    const now = new Date();
    const nextDate = new Date(now);
    let nextHour = hours.find(
      (hour) => hour > now.getHours() || (hour === now.getHours() && now.getMinutes() === 0 && now.getSeconds() === 0),
    );

    if (nextHour === undefined) {
      nextHour = hours[0];
      nextDate.setDate(now.getDate() + 1);
    }

    nextDate.setHours(nextHour, 0, 0, 0);

    if (nextDate <= now) {
      const fallbackHour = hours[(hours.indexOf(nextHour) + 1) % hours.length];
      if (fallbackHour <= nextHour) nextDate.setDate(nextDate.getDate() + 1);
      nextDate.setHours(fallbackHour, 0, 0, 0);
      nextHour = fallbackHour;
    }

    return {
      hour: nextHour,
      date: nextDate,
      diffMs: nextDate.getTime() - now.getTime(),
    };
  }

  function updateCountdown() {
    const next = computeNextWindow();
    if (!next || next.diffMs < 0) {
      setText("nextSendTime", "--:--");
      setText("nextCountdown", "Calculando...");
      setText("nextSendLabel", "--");
      return;
    }

    const hours = Math.floor(next.diffMs / 3600000);
    const mins = Math.floor((next.diffMs % 3600000) / 60000);
    const secs = Math.floor((next.diffMs % 60000) / 1000);
    const timeLabel = `${String(next.hour).padStart(2, "0")}:00`;
    const now = new Date();
    const isTomorrow =
      next.date.getDate() !== now.getDate() ||
      next.date.getMonth() !== now.getMonth() ||
      next.date.getFullYear() !== now.getFullYear();

    setText("nextSendTime", timeLabel);
    setText("nextCountdown", `em ${hours}h ${mins}m ${secs}s`);
    setText("nextSendLabel", `${isTomorrow ? "Amanhã" : "Hoje"}, ${timeLabel}`);
  }

  function getAllSummaries() {
    return Array.isArray(state.dashboard?.summaries) ? state.dashboard.summaries : [];
  }

  function getSummaryPreview(summary) {
    if (Array.isArray(summary?.bullets) && summary.bullets.length > 0) {
      return summary.bullets[0];
    }

    if (summary?.insight) return summary.insight;

    const body = extractSummaryBody(summary);
    if (!body) return "Resumo completo indisponível.";
    const firstParagraph = body.split(/\n+/).find(Boolean) || "";
    return firstParagraph;
  }

  function extractSummaryBody(summary) {
    const rawText = String(summary?.summaryText || "").trim();
    if (!rawText) return "";

    const lines = rawText
      .split(/\r?\n/)
      .map((line) => line.trimEnd())
      .filter((line, index, arr) => line || (index > 0 && arr[index - 1]));

    while (lines.length && !lines[0].trim()) lines.shift();

    const header = String(summary?.header || "").trim();
    if (lines.length && normalizeText(lines[0]) === normalizeText(header)) {
      lines.shift();
    }

    return lines.join("\n").trim();
  }

  function buildSummaryParagraphs(summary) {
    const body = extractSummaryBody(summary);
    if (!body) {
      return '<p>O corpo completo deste resumo não foi salvo.</p>';
    }

    return body
      .split(/\n{2,}/)
      .map((paragraph) => paragraph.trim())
      .filter(Boolean)
      .map((paragraph) => `<p>${escapeHtml(paragraph).replace(/\n/g, "<br />")}</p>`)
      .join("");
  }

  function getFilteredItems() {
    let items = [...getAllSummaries()];

    if (state.currentGroup !== "Todas") {
      const allowedCategories = GROUP_MAPPING[state.currentGroup] || [];
      items = items.filter((item) => allowedCategories.includes(item.category));
    }

    if (state.filters.periods.size > 0) {
      items = items.filter((item) => state.filters.periods.has(item.period));
    }

    const search = state.filters.search.trim().toLowerCase();
    if (search) {
      items = items.filter((item) => {
        const haystack = [
          item.header,
          item.insight,
          item.summaryText,
          ...(Array.isArray(item.bullets) ? item.bullets : []),
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(search);
      });
    }

    items.sort((left, right) => {
      const dateLeft = new Date(left.created_at || left.date || 0);
      const dateRight = new Date(right.created_at || right.date || 0);
      return state.filters.sort === "oldest" ? dateLeft - dateRight : dateRight - dateLeft;
    });

    return items;
  }

  function updateCounts() {
    const summaries = getAllSummaries();
    setText("count-all", summaries.length);

    Object.keys(GROUP_MAPPING).forEach((group) => {
      const count = summaries.filter((item) => GROUP_MAPPING[group].includes(item.category)).length;
      const countNode = byId(`count-${group.toLowerCase()}`);
      if (countNode) countNode.textContent = count;
    });
  }

  function buildReadingContextLabel() {
    const parts = [state.currentGroup];
    if (state.filters.periods.size > 0) {
      parts.push(Array.from(state.filters.periods).map(formatPeriod).join(" / "));
    }
    return parts.join(" · ");
  }

  function updateFilterControls() {
    document.querySelectorAll(".cat-nav-btn").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.group === state.currentGroup);
    });

    document.querySelectorAll(".sort-chip").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.sort === state.filters.sort);
    });

    document.querySelectorAll(".period-chip").forEach((button) => {
      button.classList.toggle("is-active", state.filters.periods.has(button.dataset.filterPeriod));
    });

    const contextTag = byId("readingContextTag");
    if (contextTag) contextTag.textContent = buildReadingContextLabel();
  }

  function syncSelectedSummaryCard() {
    const cards = document.querySelectorAll(".summary-card");
    let activeCardFound = false;

    cards.forEach((card) => {
      const isSelected = state.drawer.isOpen && Number(card.dataset.summaryId) === state.drawer.summaryId;
      card.classList.toggle("is-selected", isSelected);
      card.setAttribute("aria-expanded", isSelected ? "true" : "false");
      if (isSelected) {
        activeCardFound = true;
        state.drawer.opener = card;
      }
    });

    if (state.drawer.isOpen && !activeCardFound) {
      closeSummaryDrawer(false);
    }
  }

  function renderSummaries() {
    const container = byId("summaries");
    if (!container) return;

    const items = getFilteredItems();
    const resultCountTag = byId("readingResultCount");
    if (resultCountTag) {
      resultCountTag.textContent = pluralize(items.length, "resumo", "resumos");
    }

    if (!items.length) {
      container.innerHTML =
        '<div class="empty-state">Nenhum resumo encontrado para os filtros atuais.</div>';
      syncSelectedSummaryCard();
      return;
    }

    container.innerHTML = items
      .map((summary) => {
        const metaLeft = `${formatCategory(summary.category)} · ${formatPeriod(summary.period)}`;
        const sourceCountText = summary.sourceCount
          ? pluralize(summary.sourceCount, "fonte", "fontes")
          : "Sem fontes mapeadas";

        return `
          <button
            type="button"
            class="summary-card"
            data-summary-id="${summary.id}"
            aria-expanded="false"
          >
            <div class="summary-card-meta">
              <span>${escapeHtml(metaLeft)}</span>
              <span>${escapeHtml(formatTime(summary.created_at || summary.date))}</span>
            </div>
            <strong class="summary-card-title">${escapeHtml(summary.header || "Resumo")}</strong>
            <span class="summary-card-preview">${escapeHtml(getSummaryPreview(summary))}</span>
            <span class="summary-card-foot">${escapeHtml(sourceCountText)}</span>
          </button>
        `;
      })
      .join("");

    syncSelectedSummaryCard();
  }

  function computeScore() {
    if (!state.dashboard) return "0%";

    let score = 100;
    if (!state.whatsapp?.connected) score -= 35;
    if (state.dashboard.subscribers === 0) score -= 20;
    if (state.dashboard.todaySummaries === 0) score -= 10;
    if (state.dashboard.pendingSummaries > 0) score -= Math.min(25, state.dashboard.pendingSummaries * 5);

    return `${Math.max(0, score)}%`;
  }

  function buildNarrative() {
    if (!state.dashboard) return "Aguardando dados da operação.";

    const parts = [];
    if (state.whatsapp?.connected) {
      parts.push("bridge online");
    } else {
      parts.push(`bridge com status ${state.whatsapp?.status || "indisponível"}`);
    }

    parts.push(`${state.dashboard.subscribers} assinante(s) ativo(s)`);
    parts.push(`${state.dashboard.todaySummaries} lote(s) gerado(s) hoje`);

    if (state.dashboard.pendingSummaries > 0) {
      parts.push(`${state.dashboard.pendingSummaries} pendência(s) aguardando envio`);
    } else {
      parts.push("sem pendências de envio");
    }

    return `Visão rápida: ${parts.join(", ")}.`;
  }

  function getRunStatus(run) {
    return RUN_STATUS_META[run?.status] || { label: run?.status || "Desconhecido", tone: "warn" };
  }

  function formatRunWindow(run) {
    const start = formatTime(run?.startedAt);
    const finish = formatTime(run?.finishedAt);
    if (start === "--") return "Horário indisponível";
    if (!run?.finishedAt) return `Iniciado às ${start}`;
    return `${start} → ${finish}`;
  }

  function renderRecentRuns() {
    const container = byId("recentRuns");
    if (!container) return;

    const runs = Array.isArray(state.dashboard?.recentRuns) ? state.dashboard.recentRuns : [];
    setText("recentRunCount", pluralize(runs.length, "pipeline recente", "pipelines recentes"));

    if (!runs.length) {
      container.innerHTML = '<div class="empty-state">Nenhum pipeline recente registrado.</div>';
      return;
    }

    container.innerHTML = runs
      .map((run) => {
        const runStatus = getRunStatus(run);
        return `
          <article class="run-item">
            <div>
              <div class="run-title-row">
                <strong>${escapeHtml(formatPeriod(run.period))}</strong>
                <span class="tag-premium is-${runStatus.tone}">${escapeHtml(runStatus.label)}</span>
              </div>
              <span class="run-time">${escapeHtml(formatRunWindow(run))}</span>
            </div>
            <div class="run-metrics">
              ${escapeHtml(`${run.articlesCollected || 0} art · ${run.summariesGenerated || 0} res · ${run.messagesSent || 0} env`)}
            </div>
          </article>
        `;
      })
      .join("");
  }

  function updateOperationView() {
    if (!state.dashboard) return;

    const scoreValue = computeScore();
    const scoreNumber = Number.parseInt(scoreValue, 10) || 0;
    const hasPending = state.dashboard.pendingSummaries > 0;
    const bridgeOnline = !!state.whatsapp?.connected;

    setText("subscriberCount", state.dashboard.subscribers);
    setText("summaryCount", state.dashboard.todaySummaries);
    setText("pendingSummaries", state.dashboard.pendingSummaries);
    setText("healthScore", scoreValue);
    setText("bridgeStatusText", bridgeOnline ? "Online" : state.whatsapp?.status || "Indisponível");
    setText("lastUpdatedAt", `Atualizado: ${formatDateTime(new Date().toISOString())}`);
    setText("opsNarrative", buildNarrative());

    const scheduleDisplay =
      state.dashboard.nextSend ||
      getScheduleHours().map((hour) => `${String(hour).padStart(2, "0")}:00`).join(" / ");
    setTagState("scheduleTag", `Agenda: ${scheduleDisplay}`, "warn");

    if (bridgeOnline && !hasPending && scoreNumber >= 80) {
      setTagState("opsStatusTag", "Status: operacional", "ok");
    } else if (scoreNumber >= 55) {
      setTagState("opsStatusTag", "Status: atenção", "warn");
    } else {
      setTagState("opsStatusTag", "Status: degradado", "danger");
    }

    if (bridgeOnline) {
      setTagState("bridgePill", "Bridge: online", "ok");
    } else {
      setTagState("bridgePill", `Bridge: ${state.whatsapp?.status || "offline"}`, "danger");
    }

    renderRecentRuns();
    updateCountdown();
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    let payload = null;

    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }

    if (!response.ok) {
      const message = payload?.error || payload?.message || `Falha ao chamar ${url}`;
      throw new Error(message);
    }

    return payload;
  }

  async function loadData() {
    if (state.loading) return;

    state.loading = true;
    const refreshButton = byId("btnRefresh");
    const originalLabel = refreshButton ? refreshButton.innerHTML : "";
    if (refreshButton) {
      refreshButton.disabled = true;
      refreshButton.innerHTML = '<span class="spin">◌</span> Atualizando';
    }

    try {
      const [dashboardPayload, whatsappPayload] = await Promise.all([
        fetchJson("/api/dashboard"),
        fetch("/api/whatsapp-status")
          .then(async (response) => {
            if (!response.ok) {
              return { connected: false, status: "indisponível" };
            }
            return response.json();
          })
          .catch(() => ({ connected: false, status: "indisponível" })),
      ]);

      state.dashboard = dashboardPayload;
      state.whatsapp = whatsappPayload || { connected: false, status: "indisponível" };

      updateOperationView();
      updateCounts();
      updateFilterControls();
      renderSummaries();

      if (state.drawer.isOpen) {
        const summary = getAllSummaries().find((item) => item.id === state.drawer.summaryId);
        if (summary) {
          renderDrawer(summary);
        } else {
          closeSummaryDrawer(false);
        }
      }
    } catch (error) {
      console.error(error);
      showToast(error.message || "Erro ao carregar dados da dashboard", true);
    } finally {
      state.loading = false;
      if (refreshButton) {
        refreshButton.disabled = false;
        refreshButton.innerHTML = originalLabel || "Recarregar";
      }
    }
  }

  function savePreferences() {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          view: state.currentView,
          group: state.currentGroup,
          sort: state.filters.sort,
          periods: Array.from(state.filters.periods),
        }),
      );
    } catch (error) {
      console.debug("Falha ao salvar preferências", error);
    }
  }

  function loadPreferences() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;

      const saved = JSON.parse(raw);
      if (saved?.view) state.currentView = saved.view;
      if (saved?.group) state.currentGroup = saved.group;
      if (saved?.sort) state.filters.sort = saved.sort;
      if (Array.isArray(saved?.periods)) {
        state.filters.periods = new Set(saved.periods);
      }
    } catch (error) {
      console.debug("Falha ao ler preferências", error);
    }
  }

  function applyPreferences() {
    switchView(state.currentView, false);
    setGroupFilter(state.currentGroup, false);
    setSort(state.filters.sort, false);
    updateFilterControls();
  }

  async function runButtonAction(buttonId, busyLabel, action) {
    const button = byId(buttonId);
    const originalHtml = button ? button.innerHTML : "";

    if (button) {
      button.disabled = true;
      button.innerHTML = `<strong>${busyLabel}</strong><span>Aguarde a resposta da API.</span>`;
    }

    try {
      await action();
    } finally {
      if (button) {
        button.disabled = false;
        button.innerHTML = originalHtml;
      }
    }
  }

  function switchView(view, persist = true) {
    state.currentView = view;

    document.querySelectorAll(".view-content").forEach((node) => {
      node.classList.toggle("active", node.id === `view-${view}`);
    });

    document.querySelectorAll(".nav-btn").forEach((node) => {
      node.classList.toggle("is-active", node.dataset.view === view);
    });

    if (view !== "reading" && state.drawer.isOpen) {
      closeSummaryDrawer(false);
    }

    if (view === "reading") {
      updateFilterControls();
      renderSummaries();
    }

    if (persist) savePreferences();
  }

  function setGroupFilter(group, persist = true) {
    state.currentGroup = group;
    updateFilterControls();
    renderSummaries();
    if (persist) savePreferences();
  }

  function setSort(sort, persist = true) {
    state.filters.sort = sort;
    updateFilterControls();
    renderSummaries();
    if (persist) savePreferences();
  }

  function togglePeriodFilter(period) {
    if (state.filters.periods.has(period)) {
      state.filters.periods.delete(period);
    } else {
      state.filters.periods.add(period);
    }

    updateFilterControls();
    renderSummaries();
    savePreferences();
  }

  function clearFilters() {
    state.filters.search = "";
    state.filters.sort = "newest";
    state.filters.periods.clear();
    state.currentGroup = "Todas";

    const searchInput = byId("searchInput");
    if (searchInput) searchInput.value = "";

    updateFilterControls();
    renderSummaries();
    savePreferences();
  }

  function renderDrawer(summary) {
    if (!summary) return;

    const drawerTag = byId("drawerTag");
    const metaParts = [
      formatCategory(summary.category),
      formatPeriod(summary.period),
      formatTime(summary.created_at || summary.date),
    ];

    if (summary.sourceCount) {
      metaParts.push(pluralize(summary.sourceCount, "fonte", "fontes"));
    }

    if (drawerTag) {
      setTagState("drawerTag", formatCategory(summary.category), "warn");
    }

    setText("drawerTitle", summary.header || "Resumo");
    setText("drawerMeta", metaParts.join(" · "));

    const bulletsSection = byId("drawerBulletsSection");
    const bulletList = byId("drawerBullets");
    const bullets = Array.isArray(summary.bullets) && summary.bullets.length
      ? summary.bullets
      : ["Nenhum destaque estruturado foi salvo para este resumo."];
    if (bulletList) {
      bulletList.innerHTML = bullets.map((bullet) => `<li>${escapeHtml(bullet)}</li>`).join("");
    }
    if (bulletsSection) bulletsSection.hidden = false;

    const insightSection = byId("drawerInsightSection");
    const insightNode = byId("drawerInsight");
    if (summary.insight) {
      if (insightNode) insightNode.textContent = summary.insight;
      if (insightSection) insightSection.hidden = false;
    } else if (insightSection) {
      insightSection.hidden = true;
    }

    const summarySection = byId("drawerSummarySection");
    const summaryTextNode = byId("drawerSummaryText");
    if (summaryTextNode) {
      summaryTextNode.innerHTML = buildSummaryParagraphs(summary);
    }
    if (summarySection) summarySection.hidden = false;

    const sourcesSection = byId("drawerSourcesSection");
    const sourcesList = byId("drawerSources");
    const sourceUrls = Array.isArray(summary.sourceUrls) ? summary.sourceUrls : [];
    if (sourceUrls.length > 0) {
      if (sourcesList) {
        sourcesList.innerHTML = sourceUrls
          .map(
            (url) =>
              `<li><a href="${escapeHtml(url)}" target="_blank" rel="noreferrer noopener">${escapeHtml(formatSourceLabel(url))}</a></li>`,
          )
          .join("");
      }
      if (sourcesSection) sourcesSection.hidden = false;
    } else if (sourcesSection) {
      sourcesSection.hidden = true;
    }
  }

  function getMotionDuration(durationMs) {
    return state.reducedMotion ? 0 : durationMs;
  }

  function clearPressedCards() {
    document.querySelectorAll(".summary-card.is-pressed").forEach((card) => {
      card.classList.remove("is-pressed");
    });
  }

  function animateSummaryCard(card, pointerInfo) {
    if (!card || state.reducedMotion) return Promise.resolve();

    const rect = card.getBoundingClientRect();
    const pointerX = pointerInfo?.clientX ?? rect.left + rect.width / 2;
    const pointerY = pointerInfo?.clientY ?? rect.top + rect.height / 2;

    card.style.setProperty("--tap-x", `${pointerX - rect.left}px`);
    card.style.setProperty("--tap-y", `${pointerY - rect.top}px`);
    card.classList.remove("is-pressed");
    card.classList.remove("is-opening");

    return new Promise((resolve) => {
      window.requestAnimationFrame(() => {
        card.classList.add("is-opening");
        window.setTimeout(() => {
          card.classList.remove("is-opening");
          resolve();
        }, getMotionDuration(260));
      });
    });
  }

  async function openSummaryById(summaryId, opener, pointerInfo) {
    if (state.drawer.isAnimating) return;

    const summary = getAllSummaries().find((item) => item.id === summaryId);
    if (!summary) return;

    if (state.drawer.isOpen && state.drawer.summaryId === summaryId) {
      byId("drawerClose")?.focus();
      return;
    }

    state.drawer.isAnimating = true;
    await animateSummaryCard(opener, pointerInfo);

    state.drawer.isOpen = true;
    state.drawer.summaryId = summaryId;
    state.drawer.opener = opener || null;

    renderDrawer(summary);

    const drawer = byId("summaryDrawer");
    const backdrop = byId("drawerBackdrop");
    if (drawer) {
      drawer.classList.add("is-open");
      drawer.setAttribute("aria-hidden", "false");
    }
    if (backdrop) {
      backdrop.classList.add("is-open");
      backdrop.setAttribute("aria-hidden", "false");
    }

    syncSelectedSummaryCard();

    window.setTimeout(() => {
      byId("drawerClose")?.focus();
      state.drawer.isAnimating = false;
    }, getMotionDuration(80));
  }

  function closeSummaryDrawer(restoreFocus = true) {
    const drawer = byId("summaryDrawer");
    const backdrop = byId("drawerBackdrop");
    if (drawer) {
      drawer.classList.remove("is-open");
      drawer.setAttribute("aria-hidden", "true");
    }
    if (backdrop) {
      backdrop.classList.remove("is-open");
      backdrop.setAttribute("aria-hidden", "true");
    }

    const opener = state.drawer.opener;
    state.drawer.isOpen = false;
    state.drawer.summaryId = null;
    state.drawer.opener = null;
    state.drawer.isAnimating = false;
    syncSelectedSummaryCard();

    if (restoreFocus && opener && typeof opener.focus === "function" && opener.isConnected) {
      window.setTimeout(() => opener.focus(), 0);
    }
  }

  function handleSummaryPointerDown(event) {
    const card = event.target.closest(".summary-card");
    if (!card) return;

    clearPressedCards();
    card.style.setProperty("--tap-x", `${event.clientX - card.getBoundingClientRect().left}px`);
    card.style.setProperty("--tap-y", `${event.clientY - card.getBoundingClientRect().top}px`);
    if (!state.reducedMotion) {
      card.classList.add("is-pressed");
    }
  }

  function handleSummaryClick(event) {
    const card = event.target.closest(".summary-card");
    if (!card) return;

    event.preventDefault();
    const summaryId = Number(card.dataset.summaryId);
    if (!Number.isFinite(summaryId)) return;

    openSummaryById(summaryId, card, event);
  }

  function handleSummaryKeyDown(event) {
    const card = event.target.closest(".summary-card");
    if (!card) return;

    if (event.key !== "Enter" && event.key !== " ") return;

    event.preventDefault();
    const summaryId = Number(card.dataset.summaryId);
    if (!Number.isFinite(summaryId)) return;

    openSummaryById(summaryId, card, null);
  }

  async function triggerPipeline(period) {
    await runButtonAction(`btn-${period}`, "Executando...", async () => {
      const payload = await fetchJson(`/run-pipeline/${period}`, { method: "POST" });
      showToast(payload.message || `Pipeline ${formatPeriod(period)} iniciado.`);
      window.setTimeout(loadData, 1200);
    });
  }

  async function retryTodayDelivery() {
    await runButtonAction("btn-retry", "Reenviando...", async () => {
      const payload = await fetchJson("/api/retry-delivery/today", { method: "POST" });

      if (payload.status === "completed") {
        showToast(`Reenvio concluído para ${payload.sentSubscribers} assinante(s).`);
      } else {
        showToast(payload.message || "Nenhuma pendência encontrada.");
      }

      await loadData();
    });
  }

  function attachEvents() {
    document.querySelectorAll(".nav-btn").forEach((button) => {
      button.addEventListener("click", () => switchView(button.dataset.view));
    });

    document.querySelectorAll(".cat-nav-btn").forEach((button) => {
      button.addEventListener("click", () => setGroupFilter(button.dataset.group));
    });

    document.querySelectorAll(".sort-chip").forEach((button) => {
      button.addEventListener("click", () => setSort(button.dataset.sort));
    });

    document.querySelectorAll(".period-chip").forEach((button) => {
      button.addEventListener("click", () => togglePeriodFilter(button.dataset.filterPeriod));
    });

    byId("btnRefresh")?.addEventListener("click", loadData);
    byId("btn-clearFilters")?.addEventListener("click", clearFilters);
    byId("btn-retry")?.addEventListener("click", retryTodayDelivery);

    document.querySelectorAll("[data-pipeline-period]").forEach((button) => {
      button.addEventListener("click", () => triggerPipeline(button.dataset.pipelinePeriod));
    });

    const searchInput = byId("searchInput");
    if (searchInput) {
      searchInput.addEventListener("input", (event) => {
        state.filters.search = event.target.value || "";
        renderSummaries();
      });
    }

    const summaryContainer = byId("summaries");
    if (summaryContainer) {
      summaryContainer.addEventListener("pointerdown", handleSummaryPointerDown);
      summaryContainer.addEventListener("click", handleSummaryClick);
      summaryContainer.addEventListener("keydown", handleSummaryKeyDown);
    }

    document.addEventListener("pointerup", clearPressedCards);
    document.addEventListener("pointercancel", clearPressedCards);

    byId("drawerClose")?.addEventListener("click", () => closeSummaryDrawer(true));
    byId("drawerBackdrop")?.addEventListener("click", () => closeSummaryDrawer(true));

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.drawer.isOpen) {
        closeSummaryDrawer(true);
      }
    });

    if (typeof reducedMotionQuery.addEventListener === "function") {
      reducedMotionQuery.addEventListener("change", (event) => {
        state.reducedMotion = !!event.matches;
      });
    }

    const tick = () => {
      setText(
        "clock",
        new Date().toLocaleTimeString("pt-BR", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        }),
      );
      updateCountdown();
    };

    tick();
    window.setInterval(tick, 1000);
  }

  loadPreferences();
  attachEvents();
  applyPreferences();
  updateFilterControls();
  renderSummaries();
  loadData();
  window.setInterval(loadData, AUTO_REFRESH_MS);
})();
