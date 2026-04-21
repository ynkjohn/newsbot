(function () {
  const STORAGE_KEY = "newsbot-dashboard-v9";
  const AUTO_REFRESH_MS = 60_000;
  const CLOCK_REFRESH_MS = 1_000;
  const DEFAULT_TIMEZONE = "America/Sao_Paulo";

  const SORT_OPTIONS = [
    { value: "newest", label: "Mais novos" },
    { value: "oldest", label: "Mais antigos" },
  ];

  const PERIOD_OPTIONS = [
    { value: "morning", label: "Manhã" },
    { value: "midday", label: "Meio-dia" },
    { value: "afternoon", label: "Tarde" },
    { value: "evening", label: "Noite" },
  ];

  const STATUS_OPTIONS = [
    { value: "pending", label: "Pendentes" },
    { value: "sent", label: "Enviados" },
  ];

  const SOURCE_OPTIONS = [
    { value: 0, label: "Todas as fontes" },
    { value: 1, label: "1+ fonte" },
    { value: 3, label: "3+ fontes" },
    { value: 5, label: "5+ fontes" },
  ];

  const CATEGORY_ORDER = [
    { value: "all", label: "Todas" },
    { value: "politica-brasil", label: "Política Nacional" },
    { value: "economia-brasil", label: "Economia Nacional" },
    { value: "politica-mundao", label: "Geopolítica" },
    { value: "economia-mundao", label: "Economia Global" },
    { value: "economia-cripto", label: "Criptoativos" },
    { value: "tech", label: "Tecnologia" },
  ];

  const STATUS_META = {
    connected: { label: "Bridge online", tone: "ok" },
    connected_false: { label: "Bridge instável", tone: "warn" },
    unreachable: { label: "Bridge offline", tone: "danger" },
    completed: { label: "Concluído", tone: "ok" },
    running: { label: "Em execução", tone: "warn" },
    upcoming: { label: "Próximo", tone: "warn" },
    failed: { label: "Falhou", tone: "danger" },
    pending: { label: "Pendente", tone: "warn" },
    sent: { label: "Enviado", tone: "ok" },
  };

  const state = {
    loading: true,
    view: "ops",
    payload: null,
    currentCategory: "all",
    filters: {
      search: "",
      sort: "newest",
      periods: new Set(),
      statuses: new Set(),
      sourceMin: 0,
      insightOnly: false,
    },
    drawer: {
      open: false,
      cardId: null,
    },
    toastTimer: null,
    clockTimer: null,
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value ?? "")
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
      .toLowerCase()
      .trim();
  }

  function pluralize(value, singular, plural) {
    return `${value} ${value === 1 ? singular : plural}`;
  }

  function timezone() {
    return state.payload?.timezone || DEFAULT_TIMEZONE;
  }

  function parseDate(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatTime(value) {
    const date = parseDate(value);
    if (!date) return "--";
    return new Intl.DateTimeFormat("pt-BR", {
      timeZone: timezone(),
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function formatDateTime(value) {
    const date = parseDate(value);
    if (!date) return "--";
    return new Intl.DateTimeFormat("pt-BR", {
      timeZone: timezone(),
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function formatCountdown(target) {
    const date = parseDate(target);
    if (!date) return "";

    const diffMs = date.getTime() - Date.now();
    if (diffMs <= 0) {
      return "Disparo em andamento";
    }

    const totalSeconds = Math.floor(diffMs / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;

    return `Faltam ${String(hours).padStart(2, "0")}h ${String(minutes).padStart(2, "0")}m ${String(seconds).padStart(2, "0")}s`;
  }

  function bridgeMeta(bridge) {
    if (bridge?.connected) return STATUS_META.connected;
    if (normalizeText(bridge?.status) === "connected") return STATUS_META.connected_false;
    return STATUS_META.unreachable;
  }

  function statusMeta(status) {
    return STATUS_META[status] || { label: status || "Indefinido", tone: "warn" };
  }

  function healthTone(score) {
    if (score >= 80) return "ok";
    if (score >= 60) return "warn";
    return "danger";
  }

  function operationData() {
    return state.payload?.operation || null;
  }

  function readingData() {
    return state.payload?.reading || { summaryCount: 0, categoryCounts: {}, cards: [] };
  }

  function allCards() {
    return Array.isArray(readingData().cards) ? readingData().cards : [];
  }

  function setTag(node, text, tone) {
    if (!node) return;
    node.className = tone ? `tag-premium is-${tone}` : "tag-premium";
    node.textContent = text;
  }

  function updateClock() {
    const clock = byId("clock");
    if (!clock) return;
    clock.textContent = new Intl.DateTimeFormat("pt-BR", {
      timeZone: timezone(),
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date());
    updateCountdowns();
  }

  function startClock() {
    updateClock();
    if (state.clockTimer) return;
    state.clockTimer = window.setInterval(updateClock, CLOCK_REFRESH_MS);
  }

  function updateCountdowns() {
    document.querySelectorAll("[data-countdown-target]").forEach((node) => {
      const text = formatCountdown(node.dataset.countdownTarget);
      node.textContent = text;
      node.style.display = text ? "block" : "none";
    });
  }

  function showToast(message, tone) {
    const toast = byId("toast");
    if (!toast) return;

    toast.textContent = message;
    toast.classList.remove("error");
    if (tone === "danger") {
      toast.classList.add("error");
    }
    toast.classList.add("show");

    if (state.toastTimer) {
      window.clearTimeout(state.toastTimer);
    }

    state.toastTimer = window.setTimeout(() => {
      toast.classList.remove("show", "error");
    }, 2600);
  }

  function loadPreferences() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;

      const saved = JSON.parse(raw);
      state.view = saved.view === "reading" ? "reading" : "ops";
      state.currentCategory = saved.currentCategory || "all";
      state.filters.search = saved.search || "";
      state.filters.sort = saved.sort === "oldest" ? "oldest" : "newest";
      state.filters.periods = new Set(saved.periods || []);
      state.filters.statuses = new Set(saved.statuses || []);
      state.filters.sourceMin = Number(saved.sourceMin || 0);
      state.filters.insightOnly = Boolean(saved.insightOnly);
    } catch (error) {
      console.debug("Falha ao carregar preferências", error);
    }
  }

  function savePreferences() {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          view: state.view,
          currentCategory: state.currentCategory,
          search: state.filters.search,
          sort: state.filters.sort,
          periods: Array.from(state.filters.periods),
          statuses: Array.from(state.filters.statuses),
          sourceMin: state.filters.sourceMin,
          insightOnly: state.filters.insightOnly,
        }),
      );
    } catch (error) {
      console.debug("Falha ao salvar preferências", error);
    }
  }

  function categoryOptions() {
    const cards = allCards();
    const counts = readingData().categoryCounts || {};
    const firstCardByCategory = new Map();

    cards.forEach((card) => {
      if (!firstCardByCategory.has(card.category)) {
        firstCardByCategory.set(card.category, card);
      }
    });

    const canonicalValues = new Set(CATEGORY_ORDER.map((option) => option.value));
    const options = CATEGORY_ORDER.map((option) => {
      if (option.value === "all") {
        return {
          value: option.value,
          label: option.label,
          count: readingData().summaryCount || cards.length,
        };
      }

      const card = firstCardByCategory.get(option.value);
      return {
        value: option.value,
        label: card?.categoryLabel || option.label,
        count: counts[option.value] || 0,
      };
    });

    const extras = cards
      .filter((card) => !canonicalValues.has(card.category))
      .filter((card, index, list) => list.findIndex((candidate) => candidate.category === card.category) === index)
      .sort((left, right) => (left.categoryLabel || left.category).localeCompare(right.categoryLabel || right.category, "pt-BR"))
      .map((card) => ({
        value: card.category,
        label: card.categoryLabel || card.category,
        count: counts[card.category] || 0,
      }));

    return [...options, ...extras];
  }

  function ensureValidState() {
    const validCategories = new Set(categoryOptions().map((option) => option.value));
    if (!validCategories.has(state.currentCategory)) {
      state.currentCategory = "all";
    }

    if (state.drawer.open && !allCards().some((card) => card.id === state.drawer.cardId)) {
      state.drawer.open = false;
      state.drawer.cardId = null;
    }
  }

  function buildSearchHaystack(card) {
    const sectionText = (card.bodySections || [])
      .map((section) => `${section.title || ""} ${section.content || ""}`)
      .join(" ");

    return normalizeText(
      [
        card.header,
        card.categoryLabel,
        card.periodLabel,
        ...(card.bullets || []),
        card.insight || "",
        sectionText,
        card.summaryText || "",
      ].join(" "),
    );
  }

  function filteredCards() {
    const query = normalizeText(state.filters.search);
    let cards = [...allCards()];

    if (state.currentCategory !== "all") {
      cards = cards.filter((card) => card.category === state.currentCategory);
    }

    if (state.filters.periods.size > 0) {
      cards = cards.filter((card) => state.filters.periods.has(card.period));
    }

    if (state.filters.statuses.size > 0) {
      cards = cards.filter((card) => {
        const status = card.isPending ? "pending" : "sent";
        return state.filters.statuses.has(status);
      });
    }

    if (state.filters.sourceMin > 0) {
      cards = cards.filter((card) => Number(card.sourceCount || 0) >= state.filters.sourceMin);
    }

    if (state.filters.insightOnly) {
      cards = cards.filter((card) => Boolean(card.hasInsight));
    }

    if (query) {
      cards = cards.filter((card) => buildSearchHaystack(card).includes(query));
    }

    cards.sort((left, right) => {
      const leftTime = parseDate(left.createdAt || left.date)?.getTime() || 0;
      const rightTime = parseDate(right.createdAt || right.date)?.getTime() || 0;
      return state.filters.sort === "oldest" ? leftTime - rightTime : rightTime - leftTime;
    });

    return cards;
  }

  function selectedCard(cards) {
    if (!state.drawer.open) return null;
    return cards.find((card) => card.id === state.drawer.cardId) || null;
  }

  function hasActiveFilters() {
    return (
      state.currentCategory !== "all" ||
      Boolean(state.filters.search) ||
      state.filters.periods.size > 0 ||
      state.filters.statuses.size > 0 ||
      state.filters.sourceMin > 0 ||
      state.filters.insightOnly ||
      state.filters.sort !== "newest"
    );
  }

  function clearFilters() {
    state.currentCategory = "all";
    state.filters.search = "";
    state.filters.sort = "newest";
    state.filters.periods.clear();
    state.filters.statuses.clear();
    state.filters.sourceMin = 0;
    state.filters.insightOnly = false;
    savePreferences();
    render();
  }

  function setView(view) {
    state.view = view === "reading" ? "reading" : "ops";
    if (state.view !== "reading") {
      hideDrawer();
    }
    savePreferences();
    render();
  }

  function hideDrawer() {
    state.drawer.open = false;
    state.drawer.cardId = null;

    const drawer = byId("summaryDrawer");
    const backdrop = byId("drawerBackdrop");
    if (drawer) {
      drawer.classList.remove("is-open");
      drawer.setAttribute("aria-hidden", "true");
    }
    if (backdrop) {
      backdrop.classList.remove("is-open");
    }
  }

  function openDrawer(cardId) {
    state.drawer.open = true;
    state.drawer.cardId = cardId;
    render();
  }

  function activeContextLabel() {
    const parts = [];
    const selectedCategory = categoryOptions().find((option) => option.value === state.currentCategory);

    if (selectedCategory && selectedCategory.value !== "all") {
      parts.push(selectedCategory.label);
    } else {
      parts.push("Todas");
    }

    if (state.filters.periods.size > 0) {
      parts.push(
        Array.from(state.filters.periods)
          .map((period) => PERIOD_OPTIONS.find((option) => option.value === period)?.label || period)
          .join(", "),
      );
    }

    if (state.filters.statuses.size > 0) {
      parts.push(
        Array.from(state.filters.statuses)
          .map((status) => STATUS_OPTIONS.find((option) => option.value === status)?.label || status)
          .join(", "),
      );
    }

    if (state.filters.insightOnly) {
      parts.push("Com insight");
    }

    if (state.filters.sourceMin > 0) {
      parts.push(`${state.filters.sourceMin}+ fontes`);
    }

    if (state.filters.search) {
      parts.push(`Busca: "${state.filters.search}"`);
    }

    return parts.join(" · ");
  }

  function buildMetricCards(operation) {
    const nextWindowText = operation.nextWindow
      ? `${operation.nextWindow.timeLabel}${operation.nextWindow.isTomorrow ? " amanhã" : ""}`
      : "--:--";

    return [
      {
        label: "Assinantes ativos",
        value: operation.subscriberCount,
        meta: "Base pronta para receber o próximo envio.",
        action: "focus:metricsRow",
        tone: "",
      },
      {
        label: "Lotes de hoje",
        value: operation.todaySummaryCount,
        meta: "Abre a leitura completa dos resumos já gerados.",
        action: "view:reading",
        tone: "",
      },
      {
        label: "Próxima janela",
        value: nextWindowText,
        meta: operation.nextWindow
          ? `${operation.nextWindow.periodLabel}${operation.nextWindow.isTomorrow ? " de amanhã" : " de hoje"}`
          : "Agenda ainda não carregada.",
        action: "focus:miniTimeline",
        tone: operation.nextWindow ? "warn" : "",
        countdownTarget: operation.nextWindow?.scheduledAt || "",
      },
      {
        label: "Health score",
        value: `${operation.healthScore}%`,
        meta: operation.inactiveFeedCount
          ? `${pluralize(operation.inactiveFeedCount, "feed em atenção", "feeds em atenção")}.`
          : "Operação sem alerta estrutural no momento.",
        action: "focus:feedAlerts",
        tone: healthTone(operation.healthScore) === "danger" ? "danger" : healthTone(operation.healthScore) === "warn" ? "warn" : "",
      },
    ];
  }

  function buildNarrative(operation) {
    const bridge = bridgeMeta(operation.bridge);
    const attention = [];

    if (operation.pendingSummaryCount > 0) {
      attention.push(pluralize(operation.pendingSummaryCount, "pendência aberta", "pendências abertas"));
    }
    if (operation.failedDeliveryCount > 0) {
      attention.push(pluralize(operation.failedDeliveryCount, "falha de entrega", "falhas de entrega"));
    }
    if (operation.inactiveFeedCount > 0) {
      attention.push(pluralize(operation.inactiveFeedCount, "feed em atenção", "feeds em atenção"));
    }

    const nextWindow = operation.nextWindow
      ? `${operation.nextWindow.periodLabel} às ${operation.nextWindow.timeLabel}${operation.nextWindow.isTomorrow ? " amanhã" : ""}`
      : "sem próxima janela definida";

    const suffix = attention.length
      ? ` Pontos de atenção: ${attention.join(", ")}.`
      : " Nenhum alerta crítico no momento.";

    return `${bridge.label}. ${pluralize(
      operation.subscriberCount,
      "assinante ativo",
      "assinantes ativos",
    )} e ${pluralize(operation.todaySummaryCount, "lote gerado hoje", "lotes gerados hoje")}. Próxima janela: ${nextWindow}.${suffix}`;
  }

  function renderTopbar() {
    const operation = operationData();

    if (operation) {
      const bridge = bridgeMeta(operation.bridge);
      setTag(byId("bridgePill"), bridge.label, bridge.tone);
      setTag(byId("healthPill"), `Health ${operation.healthScore}%`, healthTone(operation.healthScore));
    } else {
      setTag(byId("bridgePill"), "Bridge: ...", "");
      setTag(byId("healthPill"), "Health --", "");
    }

    const refreshButton = byId("btnRefresh");
    if (refreshButton) {
      refreshButton.disabled = state.loading;
      refreshButton.textContent = state.loading ? "Atualizando..." : "Recarregar";
    }

    byId("nav-ops")?.classList.toggle("is-active", state.view === "ops");
    byId("nav-reading")?.classList.toggle("is-active", state.view === "reading");
  }

  function renderMiniTimeline(timeline) {
    const node = byId("miniTimeline");
    if (!node) return;

    if (!timeline.length) {
      node.innerHTML = '<div class="empty-state">Agenda do dia indisponível.</div>';
      return;
    }

    node.innerHTML = timeline
      .map((item) => {
        const meta = statusMeta(item.status);
        let caption = "Sem detalhe adicional.";

        if (item.details) {
          caption = `${item.details.articlesCollected || 0} art. · ${item.details.summariesGenerated || 0} res. · ${item.details.messagesSent || 0} env.`;
        } else if (item.status === "upcoming") {
          caption = "Janela futura ainda não executada.";
        } else if (item.status === "pending") {
          caption = "Janela vencida sem run registrada.";
        }

        return `
          <article class="mini-slot is-${escapeHtml(item.status)}${item.isCurrentWindow ? " is-current" : ""}">
            <strong>${escapeHtml(item.periodLabel)}</strong>
            <small>${escapeHtml(item.timeLabel)} · ${escapeHtml(meta.label)}</small>
            <p>${escapeHtml(caption)}</p>
          </article>
        `;
      })
      .join("");
  }

  function renderRecentRuns(runs) {
    const node = byId("recentRuns");
    if (!node) return;

    if (!runs.length) {
      node.innerHTML = '<div class="empty-state">Nenhuma execução recente registrada.</div>';
      return;
    }

    node.innerHTML = runs
      .map((run) => {
        const meta = statusMeta(run.status);
        const timeBits = [
          run.startedAtLabel && run.startedAtLabel !== "--" ? `Início ${run.startedAtLabel}` : null,
          run.finishedAtLabel && run.finishedAtLabel !== "--" ? `fim ${run.finishedAtLabel}` : null,
          run.durationSeconds ? `${run.durationSeconds}s` : null,
        ]
          .filter(Boolean)
          .join(" · ");

        const runMetrics = `${run.articlesCollected || 0} art. · ${run.summariesGenerated || 0} resumos · ${run.messagesSent || 0} envios`;

        return `
          <article class="run-item">
            <div>
              <div class="run-title-row">
                <strong>${escapeHtml(run.periodLabel)}</strong>
                <span class="tag-premium is-${escapeHtml(meta.tone)}">${escapeHtml(meta.label)}</span>
              </div>
              <span class="run-time">${escapeHtml(timeBits || "Horário indisponível")}</span>
              ${
                run.errorSnippet
                  ? `<span class="run-time">${escapeHtml(run.errorSnippet)}</span>`
                  : ""
              }
            </div>
            <div class="run-metrics">${escapeHtml(runMetrics)}</div>
          </article>
        `;
      })
      .join("");
  }

  function renderFeedAlerts(feeds) {
    const node = byId("feedAlerts");
    if (!node) return;

    if (!feeds.length) {
      node.innerHTML = '<div class="empty-state">Nenhum feed em atenção no momento.</div>';
      return;
    }

    node.innerHTML = feeds
      .map(
        (feed) => `
          <article class="feed-item">
            <strong>${escapeHtml(feed.name)}</strong>
            <span>${escapeHtml(feed.category || "Categoria não informada")}</span>
            <p>${escapeHtml(feed.lastError || "Fonte inativa sem detalhe adicional registrado.")}</p>
          </article>
        `,
      )
      .join("");
  }

  function renderOperation() {
    byId("view-ops")?.classList.toggle("active", state.view === "ops");

    const metricsRow = byId("metricsRow");
    const operation = operationData();

    if (!metricsRow) return;

    if (!operation) {
      metricsRow.innerHTML = Array.from({ length: 4 }, () => '<div class="skeleton-state">Carregando...</div>').join("");
      byId("opsNarrative").textContent = "Carregando panorama operacional...";
      setTag(byId("opsStatusTag"), "Status: ...", "");
      setTag(byId("scheduleTag"), "Agenda: --", "");
      setTag(byId("lastUpdatedAt"), "Atualizado: --", "");
      byId("pendingSummaries").textContent = "--";
      byId("failedDeliveries").textContent = "--";
      byId("bridgeStatusText").textContent = "--";
      byId("nextSendLabel").textContent = "--";
      renderMiniTimeline([]);
      renderRecentRuns([]);
      renderFeedAlerts([]);
      return;
    }

    const metrics = buildMetricCards(operation);
    metricsRow.innerHTML = metrics
      .map(
        (metric) => `
          <button
            class="metric-card${metric.tone ? ` is-${escapeHtml(metric.tone)}` : ""}"
            type="button"
            data-metric-action="${escapeHtml(metric.action)}"
          >
            <span class="metric-label">${escapeHtml(metric.label)}</span>
            <span class="metric-value">${escapeHtml(metric.value)}</span>
            ${
              metric.countdownTarget
                ? `<span class="countdown-label" data-countdown-target="${escapeHtml(metric.countdownTarget)}"></span>`
                : ""
            }
            <div class="metric-meta">${escapeHtml(metric.meta)}</div>
          </button>
        `,
      )
      .join("");

    const scoreTone = healthTone(operation.healthScore);
    const scoreText =
      scoreTone === "ok"
        ? "Operação estável"
        : scoreTone === "warn"
          ? "Atenção moderada"
          : "Atenção imediata";

    setTag(byId("opsStatusTag"), scoreText, scoreTone);
    setTag(
      byId("scheduleTag"),
      operation.schedule?.map((item) => item.timeLabel).join(" · ") || "Agenda indisponível",
      "",
    );
    setTag(byId("lastUpdatedAt"), `Atualizado ${formatDateTime(operation.lastUpdatedAt)}`, "");

    byId("opsNarrative").textContent = buildNarrative(operation);
    byId("pendingSummaries").textContent = pluralize(operation.pendingSummaryCount, "resumo", "resumos");
    byId("failedDeliveries").textContent = pluralize(operation.failedDeliveryCount, "falha", "falhas");
    byId("bridgeStatusText").textContent = operation.bridge?.connected
      ? "Online"
      : operation.bridge?.status || "Offline";
    byId("nextSendLabel").textContent = operation.nextWindow
      ? `${operation.nextWindow.periodLabel} às ${operation.nextWindow.timeLabel}${operation.nextWindow.isTomorrow ? " amanhã" : ""}`
      : "--";

    renderMiniTimeline(operation.timeline || []);
    renderRecentRuns(operation.recentRuns || []);
    renderFeedAlerts(operation.inactiveFeeds || []);
  }

  function renderCategoryNav() {
    const node = byId("categoryNav");
    if (!node) return;

    const options = categoryOptions();
    node.innerHTML = options
      .map(
        (option) => `
          <button
            class="cat-nav-btn${state.currentCategory === option.value ? " is-active" : ""}"
            type="button"
            data-category="${escapeHtml(option.value)}"
          >
            <span>${escapeHtml(option.label)}</span>
            <span class="cat-count">${escapeHtml(option.count)}</span>
          </button>
        `,
      )
      .join("");
  }

  function renderFilters() {
    const sortControl = byId("sortControl");
    const periodControl = byId("periodControl");
    const statusControl = byId("statusControl");
    const sourceControl = byId("sourceControl");
    const searchInput = byId("searchInput");
    const clearButton = byId("btn-clearFilters");

    if (searchInput && searchInput.value !== state.filters.search) {
      searchInput.value = state.filters.search;
    }

    if (sortControl) {
      sortControl.innerHTML = SORT_OPTIONS.map(
        (option) => `
          <button
            class="sort-chip${state.filters.sort === option.value ? " is-active" : ""}"
            type="button"
            data-filter-kind="sort"
            data-filter-value="${escapeHtml(option.value)}"
          >
            ${escapeHtml(option.label)}
          </button>
        `,
      ).join("");
    }

    if (periodControl) {
      periodControl.innerHTML = PERIOD_OPTIONS.map(
        (option) => `
          <button
            class="period-chip${state.filters.periods.has(option.value) ? " is-active" : ""}"
            type="button"
            data-filter-kind="period"
            data-filter-value="${escapeHtml(option.value)}"
          >
            ${escapeHtml(option.label)}
          </button>
        `,
      ).join("");
    }

    if (statusControl) {
      statusControl.innerHTML = STATUS_OPTIONS.map(
        (option) => `
          <button
            class="status-chip${state.filters.statuses.has(option.value) ? " is-active" : ""}"
            type="button"
            data-filter-kind="status"
            data-filter-value="${escapeHtml(option.value)}"
          >
            ${escapeHtml(option.label)}
          </button>
        `,
      ).join("");
    }

    if (sourceControl) {
      sourceControl.innerHTML = [
        `
          <button
            class="source-chip${state.filters.insightOnly ? " is-active" : ""}"
            type="button"
            data-filter-kind="insight"
            data-filter-value="true"
          >
            Com insight
          </button>
        `,
        ...SOURCE_OPTIONS.map(
          (option) => `
            <button
              class="source-chip${state.filters.sourceMin === option.value ? " is-active" : ""}"
              type="button"
              data-filter-kind="source"
              data-filter-value="${escapeHtml(option.value)}"
            >
              ${escapeHtml(option.label)}
            </button>
          `,
        ),
      ].join("");
    }

    if (clearButton) {
      clearButton.disabled = !hasActiveFilters();
    }
  }

  function buildCardPreview(card) {
    if (Array.isArray(card.bullets) && card.bullets.length > 0) {
      return card.bullets[0];
    }
    if (card.insight) {
      return card.insight;
    }
    if (Array.isArray(card.bodySections) && card.bodySections.length > 0) {
      return card.bodySections[0].content || "Sem prévia disponível.";
    }
    return card.summaryText || "Sem prévia disponível.";
  }

  function renderDrawer(card) {
    const drawer = byId("summaryDrawer");
    const backdrop = byId("drawerBackdrop");

    if (!drawer || !backdrop) return;

    if (!card) {
      state.drawer.open = false;
      state.drawer.cardId = null;
      drawer.classList.remove("is-open");
      drawer.setAttribute("aria-hidden", "true");
      backdrop.classList.remove("is-open");
      return;
    }

    drawer.classList.add("is-open");
    drawer.setAttribute("aria-hidden", "false");
    backdrop.classList.add("is-open");

    setTag(byId("drawerTag"), `${card.categoryLabel} · ${card.periodLabel}`, card.isPending ? "warn" : "ok");
    byId("drawerTitle").textContent = card.header || "Resumo";
    byId("drawerMeta").textContent = [
      card.createdAtLabel ? `Criado às ${card.createdAtLabel}` : null,
      card.sentAtLabel ? `Enviado às ${card.sentAtLabel}` : "Ainda pendente de envio",
      `${card.sourceCount} ${card.sourceCount === 1 ? "fonte" : "fontes"}`,
      card.modelUsed || null,
    ]
      .filter(Boolean)
      .join(" · ");

    byId("drawerBullets").innerHTML = (card.bullets || []).length
      ? card.bullets.map((bullet) => `<li>${escapeHtml(bullet)}</li>`).join("")
      : "<li>Nenhum destaque estruturado disponível.</li>";

    const insightSection = byId("drawerInsightSection");
    if (card.insight) {
      insightSection.style.display = "";
      byId("drawerInsight").textContent = card.insight;
    } else {
      insightSection.style.display = "none";
      byId("drawerInsight").textContent = "";
    }

    const sectionsNode = byId("drawerSections");
    const sections = Array.isArray(card.bodySections) && card.bodySections.length ? card.bodySections : [];
    sectionsNode.innerHTML = sections.length
      ? sections
          .map(
            (section) => `
              <article class="drawer-text-block">
                <strong>${escapeHtml(section.title || "Resumo")}</strong>
                <p>${escapeHtml(section.content || "")}</p>
              </article>
            `,
          )
          .join("")
      : `
          <article class="drawer-text-block">
            <strong>Leitura completa</strong>
            <p>${escapeHtml(card.summaryText || "Sem conteúdo adicional disponível.")}</p>
          </article>
        `;

    const sourcesSection = byId("drawerSourcesSection");
    if ((card.sourceUrls || []).length > 0) {
      sourcesSection.style.display = "";
      byId("drawerSources").innerHTML = card.sourceUrls
        .map(
          (url) => `
            <li>
              <a href="${escapeHtml(url)}" target="_blank" rel="noreferrer noopener">
                ${escapeHtml(url)}
              </a>
            </li>
          `,
        )
        .join("");
    } else {
      sourcesSection.style.display = "none";
      byId("drawerSources").innerHTML = "";
    }
  }

  function renderReading() {
    byId("view-reading")?.classList.toggle("active", state.view === "reading");

    renderCategoryNav();
    renderFilters();

    const title = byId("readingResultsTitle");
    const count = byId("readingResultCount");
    const contextTag = byId("readingContextTag");
    const list = byId("summaries");
    const cards = filteredCards();

    const category = categoryOptions().find((option) => option.value === state.currentCategory);
    if (title) {
      title.textContent = state.currentCategory === "all" ? "Resumos do dia" : category?.label || "Resumos";
    }
    if (count) {
      count.textContent = pluralize(cards.length, "resumo", "resumos");
    }
    if (contextTag) {
      contextTag.textContent = activeContextLabel();
    }

    if (!list) return;

    if (!state.payload) {
      list.innerHTML = Array.from({ length: 4 }, () => '<div class="skeleton-state">Carregando resumos...</div>').join("");
      renderDrawer(null);
      return;
    }

    if (!cards.length) {
      list.innerHTML =
        '<div class="empty-state">Nenhum resumo encontrado. Ajuste os filtros ou limpe a busca.</div>';
      renderDrawer(null);
      return;
    }

    list.innerHTML = cards
      .map((card) => {
        const preview = buildCardPreview(card);
        const footItems = [
          card.createdAtLabel ? `Criado ${card.createdAtLabel}` : null,
          `${card.sourceCount} ${card.sourceCount === 1 ? "fonte" : "fontes"}`,
          card.hasInsight ? "Insight" : null,
          card.modelUsed || null,
        ].filter(Boolean);

        return `
          <button
            class="summary-card${card.isPending ? " is-pending" : ""}${state.drawer.cardId === card.id ? " is-selected" : ""}"
            type="button"
            data-card-id="${escapeHtml(card.id)}"
          >
            <div class="summary-card-meta">
              <span>${escapeHtml(card.categoryLabel)} · ${escapeHtml(card.periodLabel)}</span>
              <span>${escapeHtml(card.isPending ? "Pendente" : "Enviado")}</span>
            </div>
            <p class="summary-card-title">${escapeHtml(card.header)}</p>
            <div class="summary-card-preview">${escapeHtml(preview)}</div>
            <div class="summary-card-foot">
              ${footItems.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
            </div>
          </button>
        `;
      })
      .join("");

    renderDrawer(selectedCard(cards));
  }

  function render() {
    renderTopbar();
    renderOperation();
    renderReading();
    updateClock();
  }

  async function parseResponse(response) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      return response.json();
    }
    return { message: await response.text() };
  }

  async function fetchDashboard() {
    state.loading = true;
    render();

    try {
      const response = await fetch("/api/dashboard", {
        headers: { Accept: "application/json" },
      });
      const payload = await parseResponse(response);

      if (!response.ok) {
        throw new Error(payload.error || payload.message || "Não foi possível carregar a dashboard.");
      }

      state.payload = payload;
      ensureValidState();
      state.loading = false;
      render();
    } catch (error) {
      state.loading = false;
      render();
      showToast(error.message || "Falha ao carregar dashboard.", "danger");
    }
  }

  async function runManualAction(period) {
    const endpoint = period === "retry" ? "/api/retry-delivery/today" : `/run-pipeline/${period}`;

    try {
      const response = await fetch(endpoint, { method: "POST" });
      const payload = await parseResponse(response);

      if (!response.ok) {
        throw new Error(payload.error || payload.message || "Falha ao executar a ação.");
      }

      showToast(payload.message || "Ação executada.", "ok");
      window.setTimeout(fetchDashboard, 900);
    } catch (error) {
      showToast(error.message || "Falha ao executar a ação.", "danger");
    }
  }

  function focusSection(id) {
    if (state.view !== "ops") {
      setView("ops");
    }

    window.setTimeout(() => {
      byId(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 40);
  }

  function toggleSetEntry(setRef, value) {
    if (setRef.has(value)) {
      setRef.delete(value);
    } else {
      setRef.add(value);
    }
  }

  function handleUiAction(action) {
    if (!action) return;

    if (action === "view:reading") {
      setView("reading");
      return;
    }

    if (action === "reading:pending") {
      state.currentCategory = "all";
      state.filters.statuses = new Set(["pending"]);
      state.view = "reading";
      savePreferences();
      render();
      return;
    }

    if (action.startsWith("focus:")) {
      focusSection(action.replace("focus:", ""));
    }
  }

  function handleFilterClick(event) {
    const button = event.target.closest("[data-filter-kind]");
    if (!button) return;

    const kind = button.dataset.filterKind;
    const value = button.dataset.filterValue;

    if (kind === "sort") {
      state.filters.sort = value === "oldest" ? "oldest" : "newest";
    }
    if (kind === "period") {
      toggleSetEntry(state.filters.periods, value);
    }
    if (kind === "status") {
      toggleSetEntry(state.filters.statuses, value);
    }
    if (kind === "source") {
      state.filters.sourceMin = Number(value || 0);
    }
    if (kind === "insight") {
      state.filters.insightOnly = !state.filters.insightOnly;
    }

    savePreferences();
    render();
  }

  function bindEvents() {
    byId("nav-ops")?.addEventListener("click", () => setView("ops"));
    byId("nav-reading")?.addEventListener("click", () => setView("reading"));
    byId("btnRefresh")?.addEventListener("click", fetchDashboard);
    byId("btn-retry")?.addEventListener("click", () => runManualAction("retry"));
    byId("drawerClose")?.addEventListener("click", hideDrawer);
    byId("drawerBackdrop")?.addEventListener("click", hideDrawer);
    byId("btn-clearFilters")?.addEventListener("click", clearFilters);

    document.querySelectorAll("[data-pipeline-period]").forEach((button) => {
      button.addEventListener("click", () => runManualAction(button.dataset.pipelinePeriod));
    });

    byId("searchInput")?.addEventListener("input", (event) => {
      state.filters.search = event.target.value || "";
      savePreferences();
      render();
    });

    byId("categoryNav")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-category]");
      if (!button) return;
      state.currentCategory = button.dataset.category || "all";
      savePreferences();
      render();
    });

    byId("sortControl")?.addEventListener("click", handleFilterClick);
    byId("periodControl")?.addEventListener("click", handleFilterClick);
    byId("statusControl")?.addEventListener("click", handleFilterClick);
    byId("sourceControl")?.addEventListener("click", handleFilterClick);

    byId("metricsRow")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-metric-action]");
      if (!button) return;
      handleUiAction(button.dataset.metricAction);
    });

    document.querySelector(".ops-report-grid")?.addEventListener("click", (event) => {
      const item = event.target.closest("[data-quick-action]");
      if (!item) return;
      handleUiAction(item.dataset.quickAction);
    });

    document.querySelector(".ops-report-grid")?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const item = event.target.closest("[data-quick-action]");
      if (!item) return;
      event.preventDefault();
      handleUiAction(item.dataset.quickAction);
    });

    byId("summaries")?.addEventListener("click", (event) => {
      const card = event.target.closest("[data-card-id]");
      if (!card) return;
      openDrawer(Number(card.dataset.cardId));
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.drawer.open) {
        hideDrawer();
      }
    });
  }

  loadPreferences();
  startClock();
  bindEvents();
  render();
  fetchDashboard();
  window.setInterval(fetchDashboard, AUTO_REFRESH_MS);
})();
