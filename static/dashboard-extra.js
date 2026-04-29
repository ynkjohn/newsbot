(function () {
  const STORAGE_KEY = "newsbot-dashboard-v10";
  const THEME_STORAGE_KEY = "newsbot-dashboard-theme-v1";
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
    { value: "geopolitica", label: "Geopolítica" },
    { value: "economia-mundo", label: "Economia Internacional" },
    { value: "cripto-tech", label: "Cripto/Tech" },
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

  const CATEGORY_DOT_CLASS = {
    "cripto-tech": "dot-crypto",
    tech: "dot-tech",
    "economia-mundo": "dot-econ",
    "economia-internacional": "dot-econ",
    "economia-brasil": "dot-econ",
    "politica-brasil": "dot-br",
    "politica-internacional": "dot-world",
    "economia-cripto": "dot-crypto",
    geopolitica: "dot-world",
  };

  const CATEGORY_LABEL_OVERRIDES = {
    "politica-brasil": "Política Nacional",
    "economia-brasil": "Economia Nacional",
    geopolitica: "Geopolítica",
    "economia-mundo": "Economia Internacional",
    "cripto-tech": "Cripto/Tech",
    "politica-internacional": "Política Internacional",
    "economia-internacional": "Economia Internacional",
    "economia-cripto": "Criptoativos",
    tech: "Tecnologia",
  };

  const state = {
    loading: true,
    theme: "light",
    view: "ops",
    payload: null,
    currentDate: "all",
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

  function preferredTheme() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
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

  function sentimentIcon(sentiment) {
    if (sentiment === "positive") return "▲";
    if (sentiment === "negative") return "▼";
    if (sentiment === "mixed") return "◆";
    return "•";
  }

  function titleCaseWords(value) {
    return String(value || "")
      .split(" ")
      .filter(Boolean)
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ");
  }

  function displayCategoryLabel(category, fallbackLabel = "") {
    if (CATEGORY_LABEL_OVERRIDES[category]) {
      return CATEGORY_LABEL_OVERRIDES[category];
    }

    const fallback = String(fallbackLabel || "").trim();
    if (fallback && normalizeText(fallback) !== normalizeText(category)) {
      return fallback;
    }

    return titleCaseWords(String(category || "").replaceAll("-", " "));
  }

  function displayPeriodLabel(period, fallbackLabel = "") {
    return PERIOD_OPTIONS.find((option) => option.value === period)?.label || fallbackLabel || "Janela";
  }

  function timezone() {
    return state.payload?.timezone || DEFAULT_TIMEZONE;
  }

  function applyTheme(theme, { persist = true } = {}) {
    state.theme = theme === "dark" ? "dark" : "light";
    document.body.dataset.theme = state.theme;

    if (persist) {
      try {
        localStorage.setItem(THEME_STORAGE_KEY, state.theme);
      } catch (error) {
        console.debug("Falha ao salvar tema", error);
      }
    }
  }

  function loadThemePreference() {
    try {
      const savedTheme = localStorage.getItem(THEME_STORAGE_KEY);
      applyTheme(savedTheme === "dark" || savedTheme === "light" ? savedTheme : preferredTheme(), { persist: false });
    } catch (error) {
      console.debug("Falha ao carregar tema", error);
      applyTheme(preferredTheme(), { persist: false });
    }
  }

  function parseDate(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
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

  function formatLongDate(value) {
    return new Intl.DateTimeFormat("pt-BR", {
      timeZone: timezone(),
      weekday: "long",
      year: "numeric",
      month: "long",
      day: "numeric",
    })
      .format(value)
      .toUpperCase();
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

  function setTag(node, text, tone = "") {
    if (!node) return;
    node.className = tone ? `tag-premium is-${tone}` : "tag-premium";
    node.textContent = text;
  }

  function setStatusBadge(node, text, tone = "") {
    if (!node) return;
    const mappedTone =
      tone === "danger" ? "err" : tone === "ok" ? "ok" : tone === "warn" ? "warn" : "neutral";
    node.className = `status-badge ${mappedTone}`;
    node.textContent = text;
  }

  function renderThemeToggle() {
    const button = byId("btnThemeToggle");
    const icon = byId("themeIcon");
    const label = byId("themeLabel");
    const isDark = state.theme === "dark";

    if (button) {
      button.setAttribute("aria-pressed", String(isDark));
    }
    if (icon) {
      icon.textContent = isDark ? "☀" : "☾";
    }
    if (label) {
      label.textContent = isDark ? "Modo claro" : "Modo noturno";
    }
  }

  function setText(id, text) {
    const node = byId(id);
    if (node) {
      node.textContent = text;
    }
  }

  function renderDateDisplay() {
    const node = byId("dateDisplay");
    if (!node) return;
    node.textContent = formatLongDate(new Date());
  }

  function updateClock() {
    const clock = byId("clock");
    if (clock) {
      clock.textContent = new Intl.DateTimeFormat("pt-BR", {
        timeZone: timezone(),
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }).format(new Date());
    }

    renderDateDisplay();
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
    }, 2800);
  }

  function loadPreferences() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;

      const saved = JSON.parse(raw);
      state.view = ["reading", "subscribers", "feeds", "analytics"].includes(saved.view) ? saved.view : "ops";
      state.currentDate = saved.currentDate || "all";
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
          currentDate: state.currentDate,
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

  function dateOptions() {
    const cards = allCards();
    const counts = {};
    cards.forEach((card) => {
      if (!card.isPlaceholder) {
        counts[card.date] = (counts[card.date] || 0) + 1;
      }
    });

    const datesSet = new Set();
    cards.forEach((card) => {
      if (card.date) {
        datesSet.add(card.date);
      }
    });

    const dates = Array.from(datesSet).sort((a, b) => b.localeCompare(a));
    const realCards = cards.filter((c) => !c.isPlaceholder);

    const options = [
      {
        value: "all",
        label: "Todos os dias",
        count: realCards.length,
      }
    ];

    dates.forEach((dateStr) => {
      const parts = dateStr.split("-");
      let label = dateStr;
      if (parts.length === 3) {
        label = `${parts[2]}/${parts[1]}`;
      }
      options.push({
        value: dateStr,
        label: label,
        count: counts[dateStr] || 0,
      });
    });

    return options;
  }

  function ensureValidState() {
    const validDates = new Set(dateOptions().map((option) => option.value));
    if (!validDates.has(state.currentDate)) {
      state.currentDate = validDates.size > 1 ? Array.from(validDates)[1] : "all";
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
    let cards = allCards().filter((card) => !card.isPlaceholder);

    if (state.currentDate !== "all") {
      cards = cards.filter((card) => card.date === state.currentDate);
    }

    if (state.filters.periods.size > 0) {
      cards = cards.filter((card) => state.filters.periods.has(card.period));
    }

    if (state.filters.statuses.size > 0) {
      cards = cards.filter((card) => state.filters.statuses.has(card.isPending ? "pending" : "sent"));
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
      state.currentDate !== "all" ||
      Boolean(state.filters.search) ||
      state.filters.periods.size > 0 ||
      state.filters.statuses.size > 0 ||
      state.filters.sourceMin > 0 ||
      state.filters.insightOnly ||
      state.filters.sort !== "newest"
    );
  }

  function clearFilters() {
    state.currentDate = dateOptions().length > 1 ? dateOptions()[1].value : "all";
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
    state.view = ["reading", "subscribers", "feeds", "analytics"].includes(view) ? view : "ops";
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
      drawer.classList.remove("open");
      drawer.setAttribute("aria-hidden", "true");
    }
    if (backdrop) {
      backdrop.classList.remove("active");
    }
  }

  function openDrawer(cardId) {
    state.drawer.open = true;
    state.drawer.cardId = cardId;
    render();
  }

  function activeContextLabel() {
    const parts = [];
    const selectedDate = dateOptions().find((option) => option.value === state.currentDate);

    parts.push(selectedDate && selectedDate.value !== "all" ? selectedDate.label : "Todos os dias");

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

  function categoryDotClass(category) {
    return CATEGORY_DOT_CLASS[category] || "dot-crypto";
  }

  function summaryPreview(card) {
    const preview =
      card.insight ||
      (Array.isArray(card.bullets) && card.bullets.length ? card.bullets[0] : "") ||
      (Array.isArray(card.bodySections) && card.bodySections.length ? card.bodySections[0].content : "") ||
      card.summaryText ||
      "";

    return String(preview).replace(/\s+/g, " ").trim();
  }

  function groupCardsByPeriod(cards) {
    const groups = [];
    const seen = new Map();

    // Ensure they follow the PERIOD_OPTIONS order
    const orderedPeriods = PERIOD_OPTIONS.map(p => p.value);

    cards.forEach((card) => {
      const key = card.period || "unknown";
      if (!seen.has(key)) {
        const group = {
          key,
          label: displayPeriodLabel(card.period, card.periodLabel),
          cards: [],
        };
        seen.set(key, group);
        groups.push(group);
      }
      seen.get(key).cards.push(card);
    });

    // Sort groups by the defined period order
    groups.sort((a, b) => {
      const idxA = orderedPeriods.indexOf(a.key);
      const idxB = orderedPeriods.indexOf(b.key);
      return (idxA === -1 ? 99 : idxA) - (idxB === -1 ? 99 : idxB);
    });

    return groups;
  }

  function periodDeliveryLabel(cards) {
    const pendingCount = cards.filter((card) => card.isPending).length;

    if (!pendingCount) return "Tudo enviado";
    if (pendingCount === cards.length) return "Tudo pendente";
    return pluralize(pendingCount, "pendente", "pendentes");
  }

  function buildMetricCards(operation) {
    const nextWindowText = operation.nextWindow
      ? `${operation.nextWindow.timeLabel}${operation.nextWindow.isTomorrow ? " amanhã" : ""}`
      : "--:--";
    const nextWindowLabel = operation.nextWindow
      ? displayPeriodLabel(operation.nextWindow.period, operation.nextWindow.periodLabel)
      : "";

    return [
      {
        label: "Assinantes ativos",
        value: operation.subscriberCount,
        meta: "Base pronta para o próximo envio.",
        action: "focus:metricsRow",
        tone: "",
      },
      {
        label: "Lotes de hoje",
        value: operation.todaySummaryCount,
        meta: "Abre a leitura dos resumos gerados.",
        action: "view:reading",
        tone: "",
      },
      {
        label: "Próxima janela",
        value: nextWindowText,
        meta: operation.nextWindow
          ? `${nextWindowLabel}${operation.nextWindow.isTomorrow ? " de amanhã" : " de hoje"}`
          : "Agenda ainda não carregada.",
        action: "focus:miniTimeline",
        tone: operation.nextWindow ? "warn" : "",
        inverted: true,
        countdownTarget: operation.nextWindow?.scheduledAt || "",
      },
      {
        label: "Health score",
        value: `${operation.healthScore}%`,
        meta: operation.inactiveFeedCount
          ? `${pluralize(operation.inactiveFeedCount, "feed em atenção", "feeds em atenção")}.`
          : "Sem alerta estrutural agora.",
        action: "focus:feedAlerts",
        tone: healthTone(operation.healthScore),
      },
    ];
  }

  function buildNarrative(operation) {
    const bridge = bridgeMeta(operation.bridge);
    const fragments = [bridge.label];

    if (operation.nextWindow) {
      fragments.push(
        `Próxima janela ${displayPeriodLabel(operation.nextWindow.period, operation.nextWindow.periodLabel)} às ${operation.nextWindow.timeLabel}${operation.nextWindow.isTomorrow ? " amanhã" : ""}`,
      );
    }
    if (operation.failedDeliveryCount > 0) {
      fragments.push(pluralize(operation.failedDeliveryCount, "falha de entrega", "falhas de entrega"));
    }
    if (operation.pendingSummaryCount > 0) {
      fragments.push(pluralize(operation.pendingSummaryCount, "pendência aberta", "pendências abertas"));
    }
    if (operation.inactiveFeedCount > 0) {
      fragments.push(pluralize(operation.inactiveFeedCount, "feed em atenção", "feeds em atenção"));
    }
    if (
      operation.failedDeliveryCount === 0 &&
      operation.pendingSummaryCount === 0 &&
      operation.inactiveFeedCount === 0
    ) {
      fragments.push("sem alertas críticos");
    }

    return fragments.join(" · ");
  }

  function buildHeroBrief(operation) {
    const summaryBits = [
      pluralize(operation.subscriberCount, "assinante ativo", "assinantes ativos"),
      pluralize(operation.todaySummaryCount, "lote hoje", "lotes hoje"),
    ];

    if (operation.pendingSummaryCount > 0) {
      summaryBits.push(pluralize(operation.pendingSummaryCount, "pendência aberta", "pendências abertas"));
    }

    return `${summaryBits.join(" · ")}. Abra leitura, agenda, histórico e alertas sem sair do turno.`;
  }

  function renderTopbar() {
    const operation = operationData();

    if (operation) {
      const bridge = bridgeMeta(operation.bridge);
      setStatusBadge(byId("bridgePill"), bridge.label, bridge.tone);
      setStatusBadge(byId("healthPill"), `Health ${operation.healthScore}%`, healthTone(operation.healthScore));
    } else {
      setStatusBadge(byId("bridgePill"), "Bridge", "");
      setStatusBadge(byId("healthPill"), "Health", "");
    }

    const refreshButton = byId("btnRefresh");
    const refreshLabel = byId("refreshLabel");
    if (refreshButton) {
      refreshButton.disabled = state.loading;
    }
    if (refreshLabel) {
      refreshLabel.textContent = state.loading ? "Atualizando..." : "Recarregar";
    }

    renderThemeToggle();
    document.body.setAttribute("data-view", state.view);
    byId("nav-ops")?.classList.toggle("is-active", state.view === "ops");
    byId("nav-reading")?.classList.toggle("is-active", state.view === "reading");
    byId("nav-subscribers")?.classList.toggle("is-active", state.view === "subscribers");
    byId("nav-feeds")?.classList.toggle("is-active", state.view === "feeds");
    byId("nav-analytics")?.classList.toggle("is-active", state.view === "analytics");
  }

  function renderAlertBanner(operation) {
    const banner = byId("globalAlertBanner");
    if (!banner) return;

    if (!operation) {
      banner.className = "alert-banner is-warn";
      setText("opsNarrative", "Carregando panorama operacional...");
      setText("opsNarrativeLong", "Resumo rápido da operação será carregado aqui.");
      return;
    }

    const tone =
      operation.failedDeliveryCount > 0 || !operation.bridge?.connected
        ? "danger"
        : operation.pendingSummaryCount > 0 || operation.inactiveFeedCount > 0
          ? "warn"
          : "ok";

    const narrative = buildNarrative(operation);
    banner.className = `alert-banner is-${tone}`;
    setText("opsNarrative", narrative);
    setText("opsNarrativeLong", narrative);
  }

  function renderMiniTimeline(timeline) {
    const node = byId("miniTimeline");
    if (!node) return;

    if (!timeline.length) {
      node.innerHTML = '<div class="empty-state is-compact">Agenda do dia indisponível.</div>';
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
            <strong>${escapeHtml(displayPeriodLabel(item.period, item.periodLabel))}</strong>
            <small>${escapeHtml(item.timeLabel)} · ${escapeHtml(meta.label)}</small>
            <p>${escapeHtml(caption)}</p>
          </article>
        `;
      })
      .join("");
  }

  function renderPipelineEvents(events) {
    const recentEvents = Array.isArray(events) ? events.slice(-4) : [];
    if (!recentEvents.length) return "";

    return `
      <div class="run-events" aria-label="Eventos recentes do pipeline">
        ${recentEvents
          .map((event) => {
            const requestId = event.metadata?.requestId ? ` · ${event.metadata.requestId}` : "";
            const label = [event.createdAtLabel, event.step, event.status].filter(Boolean).join(" · ");
            return `
              <div class="run-event is-${escapeHtml(event.status || "pending")}">
                <span>${escapeHtml(label || "Evento")}${escapeHtml(requestId)}</span>
                <p>${escapeHtml(event.message || "Sem mensagem")}</p>
              </div>
            `;
          })
          .join("")}
      </div>
    `;
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
          run.finishedAtLabel && run.finishedAtLabel !== "--" ? `Fim ${run.finishedAtLabel}` : null,
          run.durationSeconds ? `${run.durationSeconds}s` : null,
        ]
          .filter(Boolean)
          .join(" · ");

        const runMetrics = `${run.articlesCollected || 0} art. · ${run.summariesGenerated || 0} resumos · ${run.messagesSent || 0} envios`;

        return `
          <article class="run-item">
            <div>
              <div class="run-title-row">
                <strong>${escapeHtml(displayPeriodLabel(run.period, run.periodLabel))}</strong>
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
            ${renderPipelineEvents(run.events)}
          </article>
        `;
      })
      .join("");
  }

  function renderFeedAlerts(feeds) {
    const node = byId("feedAlerts");
    if (!node) return;

    const attentionFeeds = feeds.filter((feed) => feed.state && feed.state !== "healthy");
    if (!attentionFeeds.length) {
      node.innerHTML = '<div class="empty-state">Nenhum feed em atenção no momento.</div>';
      return;
    }

    node.innerHTML = attentionFeeds
      .map((feed) => {
        const stateLabel = {
          paused: "Pausado",
          broken: "Quebrado",
          degraded: "Instável",
          stale: "Sem atualização",
        }[feed.state] || feed.state;
        const detail = feed.lastError || `${feed.minutesSinceFetch ?? 0} min desde a última coleta`;
        return `
          <article class="feed-item">
            <strong>${escapeHtml(feed.name)}</strong>
            <span>${escapeHtml(feed.category || "Categoria não informada")} · ${escapeHtml(stateLabel)} · ${escapeHtml(feed.healthScore)}%</span>
            <p>${escapeHtml(detail)}</p>
          </article>
        `;
      })
      .join("");
  }

  function renderFailedDeliveryItems(failedDeliveries) {
    const items = Array.isArray(failedDeliveries?.items) ? failedDeliveries.items : [];
    if (!items.length) return "";

    return items
      .slice(0, 3)
      .map(
        (item) => `
          <article class="run-item">
            <div>
              <div class="run-title-row">
                <strong>${escapeHtml(item.subscriber || "Assinante")}</strong>
                <span class="tag-premium is-danger">Falha</span>
              </div>
              <span class="run-time">${escapeHtml(item.summaryHeader || `${item.category || "Resumo"} — ${item.period || "janela"}`)}</span>
              <span class="run-time">${escapeHtml(item.errorMessage || "Erro não informado")}</span>
            </div>
            <div class="run-metrics">${escapeHtml(formatDateTime(item.sentAt))}</div>
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
      renderAlertBanner(null);
      setTag(byId("opsStatusTag"), "Status");
      setTag(byId("scheduleTag"), "Agenda");
      setTag(byId("lastUpdatedAt"), "Atualizado --");
      setText("opsNarrativeLong", "Use as métricas para navegar rápido pela operação.");
      setText("pendingSummaries", "--");
      setText("failedDeliveries", "--");
      setText("bridgeStatusText", "--");
      setText("nextSendLabel", "--");
      renderMiniTimeline([]);
      renderRecentRuns([]);
      renderFeedAlerts([]);
      return;
    }

    renderAlertBanner(operation);
    setText("opsNarrativeLong", buildHeroBrief(operation));

    const metrics = buildMetricCards(operation);
    metricsRow.innerHTML = metrics
      .map((metric) => {
        const classes = [
          "ops-metric",
          metric.inverted ? "is-inverted" : "",
          metric.tone ? `is-${metric.tone}` : "",
        ]
          .filter(Boolean)
          .join(" ");

        return `
          <button class="${classes}" type="button" data-metric-action="${escapeHtml(metric.action)}">
            <span class="ops-metric-label">${escapeHtml(metric.label)}</span>
            <div class="ops-metric-val">${escapeHtml(metric.value)}</div>
            <div class="ops-metric-sub">
              ${escapeHtml(metric.meta)}
              ${
                metric.countdownTarget
                  ? `<span data-countdown-target="${escapeHtml(metric.countdownTarget)}"></span>`
                  : ""
              }
            </div>
          </button>
        `;
      })
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
    );
    setTag(byId("lastUpdatedAt"), `Atualizado ${formatDateTime(operation.lastUpdatedAt)}`);

    setText("pendingSummaries", pluralize(operation.pendingSummaryCount, "resumo", "resumos"));
    setText("failedDeliveries", pluralize(operation.failedDeliveryCount, "falha", "falhas"));
    setText("bridgeStatusText", operation.bridge?.connected ? "Online" : operation.bridge?.status || "Offline");
    setText(
      "nextSendLabel",
      operation.nextWindow
        ? `${displayPeriodLabel(operation.nextWindow.period, operation.nextWindow.periodLabel)} às ${operation.nextWindow.timeLabel}${operation.nextWindow.isTomorrow ? " amanhã" : ""}`
        : "--",
    );

    renderMiniTimeline(operation.timeline || []);
    renderRecentRuns(operation.recentRuns || []);
    renderFeedAlerts(operation.feedHealth || []);
    const failedHtml = renderFailedDeliveryItems(operation.failedDeliveries);
    if (failedHtml) {
      const recentRuns = byId("recentRuns");
      if (recentRuns) recentRuns.insertAdjacentHTML("afterbegin", failedHtml);
    }
  }

  function renderDateNav() {
    const node = byId("dateNav");
    if (!node) return;

    const options = dateOptions();
    node.innerHTML = options
      .map(
        (option) => `
          <button
            class="cat-nav-btn${state.currentDate === option.value ? " is-active" : ""}"
            type="button"
            data-date="${escapeHtml(option.value)}"
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

    // Static contract requirement: handle unknown categories from payload
    const canonicalValues = new Set(CATEGORY_ORDER.map((c) => c.value));
    const payloadCategories = Object.keys(readingData().categoryCounts || {});
    const extraOptions = payloadCategories.filter((category) => !canonicalValues.has(category));

    if (extraOptions.length > 0) {
      console.debug("Categorias extras no payload:", extraOptions);
      // Aqui poderíamos adicionar botões extras dinamicamente se necessário
      extraOptions.forEach((cat) => {
        // Reservado para expansão futura de filtros dinâmicos
      });
    }

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

  function renderDrawer(card) {
    const drawer = byId("summaryDrawer");
    const backdrop = byId("drawerBackdrop");
    if (!drawer || !backdrop) return;

    if (!card) {
      state.drawer.open = false;
      state.drawer.cardId = null;
      drawer.classList.remove("open");
      drawer.setAttribute("aria-hidden", "true");
      backdrop.classList.remove("active");
      setTag(byId("drawerTag"), "Categoria");
      setText("drawerTitle", "Selecione um resumo");
      setText("drawerMeta", "—");
      const footer = byId("drawerFooter");
      if (footer) footer.hidden = true;
      return;
    }

    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    backdrop.classList.add("active");

    const isDraft = card.approvalStatus === "draft";
    const footer = byId("drawerFooter");
    if (footer) {
      footer.hidden = !isDraft;
      const btn = byId("btnApproveSummary");
      if (btn) btn.dataset.approveId = card.id;
    }

    setTag(
      byId("drawerTag"),
      `${displayCategoryLabel(card.category, card.categoryLabel)} · ${displayPeriodLabel(card.period, card.periodLabel)}`,
      isDraft ? "warn" : (card.isPending ? "warn" : "ok"),
    );
    setText("drawerTitle", card.header || "Resumo");
    setText(
      "drawerMeta",
      [
        card.createdAtLabel ? `Criado às ${card.createdAtLabel}` : null,
        card.sentAtLabel ? `Enviado às ${card.sentAtLabel}` : "Ainda pendente de envio",
        `${card.sourceCount} ${card.sourceCount === 1 ? "fonte" : "fontes"}`,
        card.modelUsed || null,
      ]
        .filter(Boolean)
        .join(" · "),
    );

    const bulletsNode = byId("drawerBullets");
    if (bulletsNode) {
      bulletsNode.innerHTML = (card.items || []).length
        ? card.items.map((item) => `<li>${sentimentIcon(item.sentiment)} ${escapeHtml(item.title)}</li>`).join("")
        : "<li>Nenhum destaque estruturado disponível.</li>";
    }

    const insightSection = byId("drawerInsightSection");
    if (insightSection) {
      insightSection.hidden = !card.insight;
    }
    setText("drawerInsight", card.insight || "");

    const sectionsNode = byId("drawerSections");
    const sections = Array.isArray(card.bodySections) && card.bodySections.length ? card.bodySections : [];
    if (sectionsNode) {
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
    }

    const sourcesSection = byId("drawerSourcesSection");
    const sourcesNode = byId("drawerSources");
    if (sourcesSection) {
      sourcesSection.hidden = !(card.sourceUrls || []).length;
    }
    if (sourcesNode) {
      sourcesNode.innerHTML = (card.sourceUrls || [])
        .map(
          (url) => `
            <li>
              <a href="${escapeHtml(url)}" target="_blank" rel="noreferrer noopener">${escapeHtml(url)}</a>
            </li>
          `,
        )
        .join("");
    }
  }

  function renderReading() {
    byId("view-reading")?.classList.toggle("active", state.view === "reading");

    renderDateNav();
    renderFilters();

    const title = byId("readingResultsTitle");
    const count = byId("readingResultCount");
    const contextTag = byId("readingContextTag");
    const selectionHint = byId("readingSelectionHint");
    const list = byId("summaries");
    const cards = filteredCards();
    const groups = groupCardsByPeriod(cards);

    const dateOption = dateOptions().find((option) => option.value === state.currentDate);
    if (title) {
      title.textContent =
        state.currentDate === "all"
          ? "Todos os dias"
          : dateOption?.label || "Resumos";
    }
    if (count) {
      count.textContent = pluralize(cards.length, "resumo", "resumos");
    }
    if (contextTag) {
      contextTag.textContent = activeContextLabel();
    }
    if (selectionHint) {
      selectionHint.textContent = cards.length
        ? "Agrupado por janela para leitura rápida."
        : "Use busca e filtros para montar a fila de leitura.";
    }

    if (!list) return;

    if (!state.payload) {
      list.innerHTML = Array.from({ length: 4 }, () => '<div class="skeleton-state">Carregando resumos...</div>').join("");
      renderDrawer(null);
      return;
    }

    if (!cards.length) {
      list.innerHTML = '<div class="empty-state">Nenhum resumo encontrado. Ajuste os filtros ou limpe a busca.</div>';
      renderDrawer(null);
      return;
    }

    list.innerHTML = groups
      .map(
        (group) => `
          <div class="summary-period-header-clean">
            <h3>Janela: ${escapeHtml(group.label)}</h3>
            <span>${escapeHtml(pluralize(group.cards.length, "lote", "lotes"))} · ${escapeHtml(periodDeliveryLabel(group.cards))}</span>
          </div>
          <div class="summary-list-clean">
            ${group.cards
              .map(
                (card) => `
                  <button
                    class="summary-row${state.drawer.cardId === card.id ? " is-selected" : ""}"
                    type="button"
                    data-card-id="${escapeHtml(card.id)}"
                  >
                    <span class="summary-row-dot ${categoryDotClass(card.category)}"></span>
                    <div class="summary-row-content">
                      <div class="summary-row-meta">
                        <strong>${escapeHtml(displayCategoryLabel(card.category, card.categoryLabel))}</strong>
                        <span class="meta-divider">·</span>
                        ${card.hasInsight ? 'Insight <span class="meta-divider">·</span>' : ""}
                        ${escapeHtml(pluralize(Number(card.sourceCount || 0), "fonte", "fontes"))}
                        <span class="meta-divider">·</span>
                        ${sentimentIcon(card.sentiment)}
                      </div>
                      <div class="summary-row-title">${escapeHtml(card.header)}</div>
                    </div>
                    <span class="summary-row-badge${card.approvalStatus === "draft" ? " pending" : (card.isPending ? " pending" : "")}">
                      ${escapeHtml(card.approvalStatus === "draft" ? "Rascunho" : (card.isPending ? "Pendente" : "Enviado"))}
                    </span>
                  </button>
                `,
              )
              .join("")}
          </div>
        `,
      )
      .join("");

    renderDrawer(selectedCard(cards));
  }

  function renderSubscribers() {
    byId("view-subscribers")?.classList.toggle("active", state.view === "subscribers");
    if (state.view !== "subscribers") return;

    const list = byId("subscribersList");
    const tag = byId("subscribersTotalTag");
    
    if (!state.subscribers) {
      if (list) list.innerHTML = '<div class="skeleton-state">Carregando assinantes...</div>';
      return;
    }
    
    if (tag) tag.textContent = pluralize(state.subscribers.length, "assinante", "assinantes");

    if (!list) return;

    if (!state.subscribers.length) {
      list.innerHTML = '<div class="empty-state">Nenhum assinante cadastrado.</div>';
      return;
    }

    list.innerHTML = state.subscribers
      .map(
        (sub) => {
          const isPending = !sub.active;
          const tone = isPending ? "danger" : "ok";
          const statusLabel = isPending ? "Pausado" : "Ativo";
          
          let prefLabel = "Todas as categorias";
          if (sub.preferences && sub.preferences.categories && sub.preferences.categories.length) {
            prefLabel = sub.preferences.categories.map(c => displayCategoryLabel(c)).join(", ");
          }

          return `
            <div class="summary-row" style="cursor: default;">
              <div class="summary-row-content">
                <div class="summary-row-meta">
                  <strong>${escapeHtml(sub.phoneNumber)}</strong>
                  <span class="meta-divider">·</span>
                  Inscrito em ${escapeHtml(formatDateTime(sub.subscribedAt))}
                </div>
                <div class="summary-row-title">${escapeHtml(prefLabel)}</div>
              </div>
              
              <button 
                class="summary-row-badge${isPending ? " pending" : ""}" 
                style="cursor: pointer; border: 1px solid var(--line-strong);"
                data-toggle-subscriber="${escapeHtml(sub.id)}"
                title="Clique para pausar ou ativar"
              >
                ${escapeHtml(statusLabel)}
              </button>
            </div>
          `;
        }
      )
      .join("");
  }

  function renderFeeds() {
    byId("view-feeds")?.classList.toggle("active", state.view === "feeds");
    if (state.view !== "feeds") return;

    const list = byId("feedsList");
    const tag = byId("feedsTotalTag");
    
    if (!state.feeds) {
      if (list) list.innerHTML = '<div class="skeleton-state">Carregando fontes...</div>';
      return;
    }
    
    if (tag) tag.textContent = pluralize(state.feeds.length, "fonte", "fontes");

    if (!list) return;

    if (!state.feeds.length) {
      list.innerHTML = '<div class="empty-state">Nenhuma fonte cadastrada.</div>';
      return;
    }

    list.innerHTML = state.feeds
      .map(
        (feed) => {
          const isPending = !feed.active;
          const statusLabel = isPending ? "Pausado" : "Ativo";
          const errCount = feed.consecutive_errors || 0;

          return `
            <div class="summary-row" style="cursor: default;">
              <div class="summary-row-content">
                <div class="summary-row-meta">
                  <strong>${escapeHtml(displayCategoryLabel(feed.category))}</strong>
                  <span class="meta-divider">·</span>
                  ${escapeHtml(feed.url)}
                </div>
                <div class="summary-row-title">
                  ${escapeHtml(feed.name)}
                  ${errCount > 0 ? `<span style="color: var(--status-err); font-size: 0.7rem; margin-left: 8px;">(${errCount} erros)</span>` : ""}
                </div>
              </div>
              
              <button 
                class="summary-row-badge${isPending ? " pending" : ""}" 
                style="cursor: pointer; border: 1px solid var(--line-strong);"
                data-toggle-feed="${escapeHtml(feed.id)}"
                title="Clique para pausar ou ativar"
              >
                ${escapeHtml(statusLabel)}
              </button>
            </div>
          `;
        }
      )
      .join("");
  }

  function render() {
    renderTopbar();
    renderOperation();
    renderReading();
    renderSubscribers();
    renderFeeds();
    renderAnalytics();
    updateClock();
  }

  async function fetchSubscribers() {
    try {
      const response = await fetch("/api/subscribers", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Falha ao carregar assinantes.");
      state.subscribers = await response.json();
      renderSubscribers();
    } catch (error) {
      console.debug(error);
      showToast("Erro ao listar assinantes.", "danger");
    }
  }

  async function toggleSubscriber(id) {
    try {
      const response = await fetch(`/api/subscribers/${id}/toggle`, { method: "POST" });
      if (!response.ok) throw new Error("Falha ao alterar assinante.");
      await fetchSubscribers();
      showToast("Status alterado com sucesso.", "ok");
    } catch (error) {
      showToast(error.message, "danger");
    }
  }

  async function fetchFeeds() {
    try {
      const response = await fetch("/api/feeds", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Falha ao carregar fontes.");
      state.feeds = await response.json();
      renderFeeds();
    } catch (error) {
      console.debug(error);
      showToast("Erro ao listar fontes.", "danger");
    }
  }

  async function toggleFeed(id) {
    try {
      const response = await fetch(`/api/feeds/${id}/toggle`, { method: "POST" });
      if (!response.ok) throw new Error("Falha ao alterar fonte.");
      await fetchFeeds();
      showToast("Status da fonte alterado.", "ok");
    } catch (error) {
      showToast(error.message, "danger");
    }
  }

  async function approveSummary(id) {
    try {
      const response = await fetch(`/api/summaries/${id}/approve`, { method: "POST" });
      if (!response.ok) throw new Error("Falha ao aprovar resumo.");
      showToast("Resumo aprovado com sucesso.", "ok");
      await fetchDashboard();
    } catch (error) {
      showToast(error.message, "danger");
    }
  }

  let charts = {
    categories: null,
    tokens: null,
  };

  function renderAnalytics() {
    byId("view-analytics")?.classList.toggle("active", state.view === "analytics");
    if (state.view !== "analytics") return;

    if (!state.analytics) {
      return;
    }

    const canvasCat = byId("chartCategories");
    const canvasTok = byId("chartTokens");
    if (!canvasCat || !canvasTok) return;

    const ctxCat = canvasCat.getContext("2d");
    const ctxTok = canvasTok.getContext("2d");

    if (charts.categories) charts.categories.destroy();
    if (charts.tokens) charts.tokens.destroy();

    const catData = state.analytics.articlesByCategory || {};
    charts.categories = new Chart(ctxCat, {
      type: "doughnut",
      data: {
        labels: Object.keys(catData).map(c => displayCategoryLabel(c)),
        datasets: [{
          data: Object.values(catData),
          backgroundColor: ["#f59e0b", "#10b981", "#3b82f6", "#ef4444", "#8b5cf6"],
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "bottom" } }
      }
    });

    const tokData = state.analytics.tokensByDate || {};
    charts.tokens = new Chart(ctxTok, {
      type: "line",
      data: {
        labels: Object.keys(tokData),
        datasets: [{
          label: "Tokens Consumidos",
          data: Object.values(tokData),
          borderColor: "#3b82f6",
          tension: 0.3,
          fill: true,
          backgroundColor: "rgba(59, 130, 246, 0.1)"
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: { y: { beginAtZero: true } }
      }
    });
  }

  async function fetchAnalytics() {
    try {
      const response = await fetch("/api/analytics", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Falha ao carregar métricas.");
      state.analytics = await response.json();
      renderAnalytics();
    } catch (error) {
      console.debug(error);
      showToast("Erro ao listar métricas.", "danger");
    }
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
        throw new Error(payload.error || payload.message || payload.detail || "Não foi possível carregar a dashboard.");
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
    if (period === "last24h") {
      try {
        const response = await fetch("/api/digest-preview/morning");
        const payload = await parseResponse(response);
        if (!response.ok) {
          throw new Error(payload.error || payload.detail || "Falha ao gerar prévia.");
        }
        showToast(`Prévia WhatsApp: ${payload.summaryCount} resumos, ${payload.partCount} parte(s), ${payload.charCount} caracteres.`, "ok");
      } catch (error) {
        showToast(error.message || "Falha ao gerar prévia.", "danger");
      }
      return;
    }

    const endpoint = period === "retry" ? "/api/retry-delivery/today" : `/run-pipeline/${period}`;

    try {
      const response = await fetch(endpoint, { method: "POST" });
      const payload = await parseResponse(response);

      if (!response.ok) {
        throw new Error(payload.error || payload.message || payload.detail || "Falha ao executar a ação.");
      }

      const requestLabel = payload.run_id ? ` · ${payload.run_id}` : "";
      showToast(`${payload.message || "Ação executada."}${requestLabel}`, "ok");
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
      state.currentDate = "all";
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
    byId("nav-subscribers")?.addEventListener("click", () => {
      setView("subscribers");
      fetchSubscribers();
    });
    byId("nav-feeds")?.addEventListener("click", () => {
      setView("feeds");
      fetchFeeds();
    });
    byId("nav-analytics")?.addEventListener("click", () => {
      setView("analytics");
      fetchAnalytics();
    });
    byId("btnThemeToggle")?.addEventListener("click", () => {
      applyTheme(state.theme === "dark" ? "light" : "dark");
      renderThemeToggle();
    });
    byId("btnRefresh")?.addEventListener("click", fetchDashboard);
    byId("btnApproveSummary")?.addEventListener("click", () => {
      const id = byId("btnApproveSummary").dataset.approveId;
      if (id) approveSummary(id);
    });
    byId("btn-retry")?.addEventListener("click", () => runManualAction("retry"));
    byId("btn-last24h")?.addEventListener("click", () => runManualAction("last24h"));
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

    byId("dateNav")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-date]");
      if (!button) return;
      state.currentDate = button.dataset.date || "all";
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

    byId("view-ops")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-quick-action]");
      if (!button) return;
      handleUiAction(button.dataset.quickAction);
    });

    byId("summaries")?.addEventListener("click", (event) => {
      const card = event.target.closest("[data-card-id]");
      if (!card) return;
      const rawId = card.dataset.cardId;
      const cardId = Number.isNaN(Number(rawId)) ? rawId : Number(rawId);
      openDrawer(cardId);
    });

    byId("subscribersList")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-toggle-subscriber]");
      if (!button) return;
      const subId = button.dataset.toggleSubscriber;
      if (subId) toggleSubscriber(subId);
    });

    byId("feedsList")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-toggle-feed]");
      if (!button) return;
      const feedId = button.dataset.toggleFeed;
      if (feedId) toggleFeed(feedId);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.drawer.open) {
        hideDrawer();
      }
    });
  }

  loadThemePreference();
  loadPreferences();
  startClock();
  bindEvents();
  render();
  fetchDashboard();
  window.setInterval(fetchDashboard, AUTO_REFRESH_MS);
})();
