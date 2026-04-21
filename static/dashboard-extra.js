(function () {
  const CAT_COLORS = {
    tech: "var(--cat-tech)",
    "economia-cripto": "var(--cat-cripto)",
    "economia-brasil": "var(--cat-economia)",
    "economia-mundao": "var(--cat-mundao)",
    "politica-brasil": "var(--cat-politica)",
    "politica-mundao": "var(--cat-mundao)",
  };

  const CAT_LABELS = {
    tech: "Tecnologia",
    "economia-cripto": "Criptoativos",
    "economia-brasil": "Economia Brasil",
    "economia-mundao": "Economia Global",
    "politica-brasil": "Política Brasil",
    "politica-mundao": "Geopolítica",
  };

  const PERIOD_LABELS = {
    morning: "matutino",
    midday: "meio-dia",
    afternoon: "vespertino",
    evening: "noturno",
  };

  const PERIOD_ORDER = ["morning", "midday", "afternoon", "evening"];
  const STORAGE_KEY = "newsbot-dashboard-ui-v2";
  const AUTO_REFRESH_MS = 60000;
  const STALE_MS = 2 * AUTO_REFRESH_MS + 5000;

  const state = {
    filters: {
      category: "all",
      period: "all",
      search: "",
      sort: "newest",
      view: "grid",
    },
    dashboard: null,
    whatsapp: { connected: false, status: "carregando" },
    lastLoadAt: null,
    loading: false,
    staleTimer: null,
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

  function setText(id, value) {
    const node = byId(id);
    if (node) {
      node.textContent = value;
    }
  }

  function loadPreferences() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw);
      state.filters = {
        ...state.filters,
        ...saved,
      };
    } catch (error) {
      console.warn("Falha ao ler preferências da dashboard", error);
    }
  }

  function savePreferences() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state.filters));
    } catch (error) {
      console.warn("Falha ao salvar preferências da dashboard", error);
    }
  }

  function fmtClock(date) {
    return date.toLocaleTimeString("pt-BR", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }

  function fmtTime(iso) {
    if (!iso) return "--:--";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "--:--";
    return date.toLocaleTimeString("pt-BR", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function fmtRelativeTime(iso) {
    if (!iso) return "sem horário";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "sem horário";

    const deltaMinutes = Math.round((date.getTime() - Date.now()) / 60000);
    const abs = Math.abs(deltaMinutes);

    if (abs < 1) return "agora";
    if (abs < 60) return deltaMinutes > 0 ? `em ${abs} min` : `${abs} min atrás`;

    const hours = Math.round(abs / 60);
    if (hours < 24) return deltaMinutes > 0 ? `em ${hours} h` : `${hours} h atrás`;

    const days = Math.round(hours / 24);
    return deltaMinutes > 0 ? `em ${days} dia(s)` : `${days} dia(s) atrás`;
  }

  function updateClock() {
    setText("clock", fmtClock(new Date()));
  }

  function showToast(message, isError = false) {
    const toast = byId("toast");
    if (!toast) return;

    toast.textContent = message;
    toast.style.borderColor = isError
      ? "rgba(255, 131, 146, 0.32)"
      : "rgba(243, 196, 95, 0.32)";
    toast.classList.add("show");

    window.clearTimeout(showToast._timer);
    showToast._timer = window.setTimeout(() => {
      toast.classList.remove("show");
    }, 3400);
  }

  function normalizeText(value) {
    return String(value ?? "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();
  }

  function getSummaries() {
    return Array.isArray(state.dashboard?.summaries) ? state.dashboard.summaries.slice() : [];
  }

  function getSummaryCategory(summary) {
    return CAT_LABELS[summary.category] || summary.category || "Sem categoria";
  }

  function getSummaryPeriod(summary) {
    return PERIOD_LABELS[summary.period] || summary.period || "sem período";
  }

  function getFilteredSummaries() {
    const search = normalizeText(state.filters.search);
    let items = getSummaries();

    if (state.filters.category !== "all") {
      items = items.filter((summary) => summary.category === state.filters.category);
    }

    if (state.filters.period !== "all") {
      items = items.filter((summary) => summary.period === state.filters.period);
    }

    if (search) {
      items = items.filter((summary) => {
        const haystack = normalizeText([
          summary.header,
          summary.insight,
          summary.category,
          summary.period,
          ...(summary.bullets || []),
        ].join(" "));
        return haystack.includes(search);
      });
    }

    const sorted = items.sort((a, b) => {
      const aTime = new Date(a.created_at || 0).getTime() || 0;
      const bTime = new Date(b.created_at || 0).getTime() || 0;

      switch (state.filters.sort) {
        case "oldest":
          return aTime - bTime;
        case "category": {
          const labelCompare = getSummaryCategory(a).localeCompare(getSummaryCategory(b), "pt-BR");
          return labelCompare || bTime - aTime;
        }
        case "period": {
          const aOrder = PERIOD_ORDER.indexOf(a.period);
          const bOrder = PERIOD_ORDER.indexOf(b.period);
          return (aOrder === -1 ? 999 : aOrder) - (bOrder === -1 ? 999 : bOrder) || bTime - aTime;
        }
        case "newest":
        default:
          return bTime - aTime;
      }
    });

    return sorted;
  }

  function getLatestSummary() {
    return getSummaries().sort((a, b) => {
      return (new Date(b.created_at || 0).getTime() || 0) - (new Date(a.created_at || 0).getTime() || 0);
    })[0] || null;
  }

  function getCategoryStats() {
    return getSummaries().reduce((acc, summary) => {
      const key = summary.category || "other";
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
  }

  function getCoverageStats() {
    const summaries = getSummaries();
    const latestByPeriod = Object.fromEntries(PERIOD_ORDER.map((period) => [period, null]));

    summaries.forEach((summary) => {
      if (!latestByPeriod.hasOwnProperty(summary.period)) return;
      if (!latestByPeriod[summary.period]) {
        latestByPeriod[summary.period] = summary;
        return;
      }
      const currentTime = new Date(latestByPeriod[summary.period].created_at || 0).getTime() || 0;
      const incomingTime = new Date(summary.created_at || 0).getTime() || 0;
      if (incomingTime > currentTime) {
        latestByPeriod[summary.period] = summary;
      }
    });

    let completed = 0;
    const items = PERIOD_ORDER.map((period) => {
      const summary = latestByPeriod[period];
      const pending = state.dashboard?.pendingSummaries ?? 0;
      const isLatest = summary && getLatestSummary() === summary;
      const status = summary
        ? pending > 0 && isLatest
          ? "attention"
          : "done"
        : "waiting";

      if (summary) completed += 1;

      return {
        period,
        label: PERIOD_LABELS[period],
        status,
        summary,
      };
    });

    return { completed, items };
  }

  function computeHealthScore() {
    const connected = Boolean(state.whatsapp?.connected);
    const pending = state.dashboard?.pendingSummaries ?? 0;
    const summaries = getSummaries().length;

    let score = 100;
    if (!connected) score -= 45;
    score -= Math.min(35, pending * 12);
    if (summaries === 0) score -= 15;
    if (state.lastLoadAt && Date.now() - state.lastLoadAt > STALE_MS) score -= 12;
    return Math.max(0, score);
  }

  function describeHealth(score) {
    if (score >= 85) return "estável";
    if (score >= 65) return "atenção leve";
    if (score >= 40) return "em risco";
    return "crítico";
  }

  function setStaleWarning(isStale) {
    const pill = byId("stalePill");
    if (!pill) return;
    pill.classList.toggle("hidden", !isStale);
    pill.textContent = isStale ? "dados podem estar desatualizados" : "";
  }

  function scheduleStaleCheck() {
    window.clearTimeout(state.staleTimer);
    const check = () => {
      const isStale = state.lastLoadAt ? Date.now() - state.lastLoadAt > STALE_MS : false;
      setStaleWarning(isStale);
      state.staleTimer = window.setTimeout(check, 15000);
    };
    check();
  }

  function updateTopMetrics() {
    const dashboard = state.dashboard || {};
    const subscribers = dashboard.subscribers ?? 0;
    const todaySummaries = dashboard.todaySummaries ?? getSummaries().length;
    const nextSend = dashboard.nextSend ?? "--";
    const pending = dashboard.pendingSummaries ?? 0;
    const score = computeHealthScore();

    setText("subscriberCount", subscribers);
    setText("summaryCount", todaySummaries);
    setText("nextSend", nextSend);
    setText("healthScore", `${score}%`);

    setText(
      "subscriberFoot",
      subscribers > 0
        ? `${subscribers} contato(s) prontos para a próxima rodada.`
        : "Sem base carregada para envios agora."
    );
    setText(
      "summaryFoot",
      todaySummaries > 0
        ? `${todaySummaries} lote(s) renderizado(s) nesta data.`
        : "Nenhum resumo publicado ainda hoje."
    );
    setText(
      "nextSendFoot",
      nextSend && nextSend !== "--"
        ? `Próxima janela prevista para ${nextSend}.`
        : "Sem horário exposto pelo scheduler principal."
    );
    setText(
      "healthFoot",
      `Saúde ${describeHealth(score)} · ${pending === 0 ? "fila limpa" : `${pending} pendente(s)`}.`
    );
  }

  function updateBridgeState() {
    const bridgePill = byId("bridgePill");
    const connected = Boolean(state.whatsapp?.connected);
    const statusText = state.whatsapp?.status || "desconhecido";

    if (bridgePill) {
      bridgePill.dataset.state = connected ? "ok" : "danger";
      bridgePill.textContent = connected ? "bridge online" : `bridge ${statusText}`;
    }

    setText("whatsappStatus", connected ? "Bridge conectado" : `Bridge ${statusText}`);
    setText(
      "whatsappStatusNote",
      connected
        ? "Mensagens podem ser entregues e perguntas em grupo já passam pelo relay."
        : "O bridge não confirmou conexão. Se cair, revise pareamento, sessão e logs do relay."
    );

    setText("heroBridge", connected ? "bridge online" : "bridge offline");
    setText(
      "heroBridgeNote",
      connected
        ? "Relay conectado e apto a responder."
        : "Sem confirmação de conexão com o WhatsApp."
    );

    setText("opsBridgeShort", connected ? "online" : statusText);
    setText(
      "opsBridgeShortNote",
      connected ? "Canal pronto para entrega." : "Vale revisar sessão e estabilidade do relay."
    );
  }

  function updatePendingState() {
    const pending = state.dashboard?.pendingSummaries ?? 0;
    const summaries = getSummaries().length;

    setText("pendingStatus", `pendentes: ${pending}`);
    setText(
      "pendingStatusNote",
      pending === 0
        ? "Todos os resumos do dia já foram marcados como enviados."
        : "Há resumos pendentes. O botão de retry roda somente esse subconjunto."
    );

    setText("heroPending", pending === 0 ? "fila limpa" : `${pending} pendente(s)`);
    setText(
      "heroPendingNote",
      pending === 0
        ? "Nenhum resumo aguardando retry agora."
        : "Ainda existem entregas do dia sem confirmação."
    );

    setText("heroWindow", state.dashboard?.nextSend ?? "--");
    setText(
      "heroWindowNote",
      summaries > 0
        ? `Último lote ${fmtRelativeTime(getLatestSummary()?.created_at)}.`
        : "Sem lote publicado ainda hoje."
    );

    setText("opsPendingShort", pending === 0 ? "zerada" : `${pending}`);
    setText(
      "opsPendingShortNote",
      pending === 0 ? "Sem ação manual urgente." : "Retry recomendado se o bridge estiver estável."
    );
  }

  function updateHero() {
    const summary = getLatestSummary();
    const dashboard = state.dashboard || {};

    if (!summary) {
      setText("heroCategory", "sem resumo");
      setText("heroMeta", "aguardando novo lote");
      setText("heroAge", dashboard.nextSend ? `próxima janela ${dashboard.nextSend}` : "sem atualização");
      setText(
        "heroHeader",
        "Nenhum resumo publicado hoje ainda. O painel continua monitorando a próxima janela."
      );
      setText(
        "heroInsight",
        "Assim que um lote for gerado, este bloco passa a destacar o resumo mais recente com contexto de categoria, horário e densidade do conteúdo."
      );
      return;
    }

    const firstBullet = (summary.bullets || []).find(Boolean);
    const supportingText =
      summary.insight || firstBullet || "Resumo publicado e pronto para leitura.";

    setText("heroCategory", getSummaryCategory(summary));
    setText("heroMeta", `${getSummaryPeriod(summary)} · ${fmtTime(summary.created_at)}`);
    setText("heroAge", fmtRelativeTime(summary.created_at));
    setText("heroHeader", summary.header || `Resumo ${getSummaryCategory(summary)}`);
    setText("heroInsight", supportingText);

    const heroCategory = byId("heroCategory");
    if (heroCategory) {
      heroCategory.style.color = CAT_COLORS[summary.category] || "var(--accent-2)";
      heroCategory.style.borderColor = "rgba(255,255,255,0.08)";
      heroCategory.style.background = "rgba(255,255,255,0.04)";
    }

    setText("opsLatestShort", fmtTime(summary.created_at));
    setText(
      "opsLatestShortNote",
      `${getSummaryCategory(summary)} · ${getSummaryPeriod(summary)}.`
    );
  }

  function renderCoverage() {
    const { completed, items } = getCoverageStats();
    const container = byId("coverageGrid");
    if (!container) return;

    setText("coverageTag", `${completed}/4 janelas`);
    setText(
      "coverageIntro",
      completed === 4
        ? "Todas as janelas já geraram resumo nesta data."
        : completed === 0
          ? "Nenhum período gerou resumo ainda."
          : `${completed} período(s) já geraram resumo; os demais seguem em observação.`
    );

    container.innerHTML = items
      .map((item) => {
        const text = item.summary
          ? `${fmtTime(item.summary.created_at)} · ${getSummaryCategory(item.summary)}`
          : "ainda sem publicação";
        return `
          <div class="coverage-item" data-state="${item.status}">
            <strong>${escapeHtml(item.label)}</strong>
            <span>${escapeHtml(text)}</span>
          </div>`;
      })
      .join("");
  }

  function renderCategoryFilters() {
    const stats = getCategoryStats();
    const container = byId("categoryFilters");
    if (!container) return;

    const buttons = [
      {
        key: "all",
        label: "Todas",
        count: getSummaries().length,
      },
      ...Object.keys(stats)
        .sort((a, b) => getSummaryCategory({ category: a }).localeCompare(getSummaryCategory({ category: b }), "pt-BR"))
        .map((key) => ({
          key,
          label: CAT_LABELS[key] || key,
          count: stats[key],
        })),
    ];

    container.innerHTML = buttons
      .map((item) => {
        const active = state.filters.category === item.key ? "active" : "";
        const color = item.key === "all" ? "var(--accent)" : (CAT_COLORS[item.key] || "var(--accent)");
        return `
          <button class="chip-btn ${active}" type="button" data-category="${escapeHtml(item.key)}" style="--legend-color:${color}">
            ${item.key === "all" ? "Todas" : `<span class="category-chip" style="padding:0; border:0; min-height:auto; background:none; color:inherit; --legend-color:${color};">${escapeHtml(item.label)}</span>`}
            <b>${item.count}</b>
          </button>`;
      })
      .join("");
  }

  function renderLegend() {
    const stats = getCategoryStats();
    const container = byId("legendRow");
    if (!container) return;

    const entries = Object.keys(stats)
      .sort((a, b) => stats[b] - stats[a])
      .slice(0, 4);

    if (entries.length === 0) {
      container.innerHTML = '<span class="tag">sem categorias</span>';
      return;
    }

    container.innerHTML = entries
      .map((key) => {
        const color = CAT_COLORS[key] || "var(--accent)";
        return `<span class="legend-pill" style="--legend-color:${color}">${escapeHtml(CAT_LABELS[key] || key)} · ${stats[key]}</span>`;
      })
      .join("");
  }

  function updateToolbarMeta(filtered) {
    const total = getSummaries().length;
    const hasFilter = state.filters.category !== "all" || state.filters.period !== "all" || Boolean(state.filters.search);

    setText(
      "summaryMeta",
      `${filtered.length} ${filtered.length === 1 ? "cartão" : "cartões"}${hasFilter ? ` de ${total}` : ""}`
    );

    setText(
      "summaryToolbarCopy",
      filtered.length === 0
        ? "Nenhum cartão corresponde aos filtros atuais."
        : hasFilter
          ? `Mostrando ${filtered.length} cartão(ões) após aplicar os filtros.`
          : `Mostrando todos os ${filtered.length} cartões do dia em uma leitura contínua.`
    );
  }

  function renderSummaries() {
    const container = byId("summaries");
    if (!container) return;

    const filtered = getFilteredSummaries();
    updateToolbarMeta(filtered);
    renderLegend();

    container.dataset.view = state.filters.view;

    if (filtered.length === 0) {
      container.innerHTML = `
        <div class="empty-state" style="grid-column:1 / -1;">
          <div>
            <h3>Nenhum cartão encontrado</h3>
            <p>Tente limpar os filtros, trocar a ordenação ou buscar por outro termo para recuperar a visão completa.</p>
          </div>
        </div>`;
      return;
    }

    container.innerHTML = filtered
      .map((summary, index) => {
        const color = CAT_COLORS[summary.category] || "var(--accent)";
        const bullets = (summary.bullets || []).slice(0, 3);
        const bulletHtml = bullets.length
          ? bullets.map((bullet) => `<li>${escapeHtml(bullet)}</li>`).join("")
          : '<li>Sem bullets registrados para este resumo.</li>';

        const insightText = summary.insight || "Sem insight adicional salvo no payload.";
        const densityLabel = bullets.length >= 3 ? "denso" : bullets.length === 2 ? "médio" : "enxuto";
        const footerNote = summary.insight ? "com insight" : "sem insight";

        return `
          <article class="summary-card" style="--cat-color:${color}; animation-delay:${0.04 + index * 0.04}s">
            <div class="summary-main">
              <div class="summary-card-top">
                <div>
                  <span class="tag">${escapeHtml(getSummaryCategory(summary))}</span>
                </div>
                <time>${escapeHtml(getSummaryPeriod(summary))} · ${fmtTime(summary.created_at)}</time>
              </div>
              <h3>${escapeHtml(summary.header || "Resumo do dia")}</h3>
              <ul class="summary-bullets">${bulletHtml}</ul>
              <div class="summary-card-footer">
                <span class="summary-footer-note">${escapeHtml(fmtRelativeTime(summary.created_at))} · ${footerNote}</span>
                <span class="tag">${bullets.length} bullet(s)</span>
              </div>
            </div>
            <div class="summary-side">
              <div class="signal-card">
                <label>Leitura</label>
                <strong>${escapeHtml(insightText)}</strong>
                <span>Resumo concentrado para leitura rápida do lote.</span>
              </div>
              <div class="density-card">
                <label>Densidade</label>
                <strong>${escapeHtml(densityLabel)}</strong>
                <span>${escapeHtml(getSummaryCategory(summary))} · ${escapeHtml(getSummaryPeriod(summary))}</span>
              </div>
            </div>
          </article>`;
      })
      .join("");
  }

  function renderTimeline() {
    const list = byId("timelineList");
    if (!list) return;

    const summaries = getSummaries().sort((a, b) => {
      return (new Date(b.created_at || 0).getTime() || 0) - (new Date(a.created_at || 0).getTime() || 0);
    });

    const pending = state.dashboard?.pendingSummaries ?? 0;
    const items = [];

    if (summaries[0]) {
      items.push({
        label: "Último lote",
        title: summaries[0].header || `Resumo ${getSummaryCategory(summaries[0])}`,
        detail: `${getSummaryCategory(summaries[0])} · ${getSummaryPeriod(summaries[0])} · ${fmtRelativeTime(summaries[0].created_at)}`,
      });
    }

    const coverage = getCoverageStats();
    items.push({
      label: "Cobertura",
      title: coverage.completed === 4 ? "Todas as janelas já publicaram" : `${coverage.completed}/4 janelas concluídas`,
      detail: coverage.completed === 0 ? "Ainda sem resumos do dia." : "Use o grid lateral para ver quais períodos faltam.",
    });

    items.push({
      label: "Entrega",
      title: pending === 0 ? "Fila de envio está limpa" : `${pending} pendência(s) esperando retry`,
      detail: pending === 0 ? "Sem pressão operacional imediata na entrega." : "Se o bridge estiver online, o retry já pode ser disparado.",
    });

    const categoryStats = getCategoryStats();
    const topCategory = Object.keys(categoryStats).sort((a, b) => categoryStats[b] - categoryStats[a])[0];
    items.push({
      label: "Densidade",
      title: topCategory ? `${CAT_LABELS[topCategory] || topCategory} lidera o dia` : "Sem categoria dominante",
      detail: topCategory ? `${categoryStats[topCategory]} cartão(ões) nesta categoria.` : "Aguardando publicações para montar densidade.",
    });

    setText("timelineTag", `${items.length} sinais`);
    setText(
      "timelineIntro",
      summaries.length > 0
        ? "Use o radar para checar o lote mais novo, lacunas de cobertura e a pressão sobre a fila de entrega."
        : "Sem resumos publicados ainda. O radar destaca cobertura, entrega e sinais assim que surgirem dados."
    );

    list.innerHTML = items
      .map((item) => {
        return `
          <article class="timeline-card">
            <label>${escapeHtml(item.label)}</label>
            <strong>${escapeHtml(item.title)}</strong>
            <span>${escapeHtml(item.detail)}</span>
          </article>`;
      })
      .join("");
  }

  function syncFilterInputs() {
    const searchInput = byId("searchInput");
    const periodSelect = byId("periodSelect");
    const sortSelect = byId("sortSelect");
    const gridBtn = byId("viewGridBtn");
    const listBtn = byId("viewListBtn");

    if (searchInput && searchInput.value !== state.filters.search) {
      searchInput.value = state.filters.search;
    }
    if (periodSelect) {
      periodSelect.value = state.filters.period;
    }
    if (sortSelect) {
      sortSelect.value = state.filters.sort;
    }
    if (gridBtn) gridBtn.classList.toggle("active", state.filters.view === "grid");
    if (listBtn) listBtn.classList.toggle("active", state.filters.view === "list");
  }

  function populatePeriodSelect() {
    const periodSelect = byId("periodSelect");
    if (!periodSelect) return;

    const existing = new Set(getSummaries().map((summary) => summary.period).filter(Boolean));
    const options = [
      '<option value="all">Todos os períodos</option>',
      ...PERIOD_ORDER.filter((period) => existing.has(period) || true).map(
        (period) => `<option value="${period}">${PERIOD_LABELS[period]}</option>`
      ),
    ];
    periodSelect.innerHTML = options.join("");
    periodSelect.value = state.filters.period;
  }

  function markRefresh() {
    state.lastLoadAt = Date.now();
    const now = new Date();
    setText("lastSync", `última leitura ${fmtTime(now.toISOString())}`);
    scheduleStaleCheck();
  }

  function setLoadingState(isLoading) {
    state.loading = isLoading;
    const refreshButton = byId("btnRefresh");
    if (refreshButton) {
      refreshButton.disabled = isLoading;
      refreshButton.textContent = isLoading ? "atualizando..." : "recarregar";
    }
  }

  function renderAll() {
    updateTopMetrics();
    updateBridgeState();
    updatePendingState();
    updateHero();
    renderCoverage();
    populatePeriodSelect();
    syncFilterInputs();
    renderCategoryFilters();
    renderSummaries();
    renderTimeline();
    setText("refreshMeta", "auto-refresh 60s");
    setText("opsStatusTag", describeHealth(computeHealthScore()));
  }

  function setActionButtonState(buttonId, title, description, busy) {
    const button = byId(buttonId);
    if (!button) return;

    button.disabled = busy;
    button.innerHTML = busy
      ? `<strong><span class="spin">◌</span> ${escapeHtml(title)}</strong><span>${escapeHtml(description)}</span>`
      : `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(description)}</span>`;
  }

  window.loadData = async function loadData() {
    setLoadingState(true);

    try {
      const [dashboardRes, whatsappRes] = await Promise.all([
        fetch("/api/dashboard", { cache: "no-store" }),
        fetch("/api/whatsapp-status", { cache: "no-store" }),
      ]);

      if (!dashboardRes.ok) {
        throw new Error(`Dashboard respondeu ${dashboardRes.status}`);
      }

      const dashboard = await dashboardRes.json();
      const whatsapp = whatsappRes.ok
        ? await whatsappRes.json()
        : { status: "erro", connected: false };

      state.dashboard = dashboard;
      state.whatsapp = whatsapp;
      markRefresh();
      renderAll();
    } catch (error) {
      console.error(error);
      if (!state.dashboard) {
        const container = byId("summaries");
        if (container) {
          container.innerHTML = `
            <div class="empty-state" style="grid-column:1 / -1;">
              <div>
                <h3>Falha ao carregar</h3>
                <p>Os dados não puderam ser atualizados agora. Tente recarregar em alguns segundos.</p>
              </div>
            </div>`;
        }
      }

      const bridgePill = byId("bridgePill");
      if (bridgePill) {
        bridgePill.dataset.state = "danger";
        bridgePill.textContent = "erro de leitura";
      }

      setText("whatsappStatus", "Erro ao carregar dados");
      setText("whatsappStatusNote", "A dashboard não conseguiu atualizar agora.");
      showToast("Erro ao atualizar dashboard", true);
    } finally {
      setLoadingState(false);
    }
  };

  window.triggerPipeline = async function triggerPipeline(period) {
    const labels = {
      morning: {
        title: "Rodando matutino...",
        description: "Coletando e resumindo o lote da manhã.",
      },
      midday: {
        title: "Rodando meio-dia...",
        description: "Executando a janela intermediária.",
      },
      afternoon: {
        title: "Rodando vespertino...",
        description: "Atualizando os artigos da tarde.",
      },
      evening: {
        title: "Rodando noturno...",
        description: "Fechando o lote final do dia.",
      },
    };

    const current = labels[period];
    setActionButtonState(`btn-${period}`, current.title, current.description, true);

    try {
      const response = await fetch(`/run-pipeline/${period}`, { method: "POST" });
      if (!response.ok) {
        showToast("Falha ao iniciar pipeline", true);
        return;
      }

      showToast(`Pipeline ${PERIOD_LABELS[period]} iniciado.`);
      window.setTimeout(window.loadData, 2200);
    } catch (error) {
      console.error(error);
      showToast("Erro de conexão ao iniciar pipeline", true);
    } finally {
      const resetLabels = {
        morning: ["Rodar matutino", "Coleta e resume o lote da manhã."],
        midday: ["Rodar meio-dia", "Executa a janela intermediária de notícias."],
        afternoon: ["Rodar vespertino", "Atualiza o lote da tarde com novos artigos."],
        evening: ["Rodar noturno", "Dispara a janela final de resumos do dia."],
      };

      window.setTimeout(() => {
        const [title, description] = resetLabels[period];
        setActionButtonState(`btn-${period}`, title, description, false);
      }, 1500);
    }
  };

  window.retryTodayDelivery = async function retryTodayDelivery() {
    setActionButtonState(
      "btn-retry",
      "Reenviando pendentes...",
      "Tentando entregar apenas os resumos ainda em aberto.",
      true
    );

    try {
      const response = await fetch("/api/retry-delivery/today", { method: "POST" });
      const payload = await response.json();

      if (!response.ok) {
        showToast("Falha ao reenviar pendentes", true);
        return;
      }

      if (payload.status === "completed") {
        showToast(`Reenvio concluído para ${payload.sentSubscribers || 0} assinante(s).`);
      } else {
        showToast(payload.message || "Não havia nada pendente para reenviar.");
      }

      window.setTimeout(window.loadData, 1200);
    } catch (error) {
      console.error(error);
      showToast("Erro de conexão ao reenviar", true);
    } finally {
      window.setTimeout(() => {
        setActionButtonState(
          "btn-retry",
          "Reenviar pendentes",
          "Roda apenas os resumos de hoje que ainda não foram marcados como enviados.",
          false
        );
      }, 1500);
    }
  };

  function attachEvents() {
    const searchInput = byId("searchInput");
    const periodSelect = byId("periodSelect");
    const sortSelect = byId("sortSelect");
    const clearButton = byId("btn-clearFilters");
    const focusLatestButton = byId("btnFocusLatest");
    const categoryFilters = byId("categoryFilters");

    if (searchInput) {
      searchInput.addEventListener("input", (event) => {
        state.filters.search = event.target.value || "";
        savePreferences();
        renderSummaries();
      });
    }

    if (periodSelect) {
      periodSelect.addEventListener("change", (event) => {
        state.filters.period = event.target.value;
        savePreferences();
        renderSummaries();
      });
    }

    if (sortSelect) {
      sortSelect.addEventListener("change", (event) => {
        state.filters.sort = event.target.value;
        savePreferences();
        renderSummaries();
      });
    }

    [byId("viewGridBtn"), byId("viewListBtn")].forEach((button) => {
      if (!button) return;
      button.addEventListener("click", () => {
        state.filters.view = button.dataset.view || "grid";
        savePreferences();
        syncFilterInputs();
        renderSummaries();
      });
    });

    if (clearButton) {
      clearButton.addEventListener("click", () => {
        state.filters = {
          ...state.filters,
          category: "all",
          period: "all",
          search: "",
          sort: "newest",
        };
        savePreferences();
        syncFilterInputs();
        renderCategoryFilters();
        renderSummaries();
        showToast("Filtros limpos.");
      });
    }

    if (focusLatestButton) {
      focusLatestButton.addEventListener("click", () => {
        const latest = getLatestSummary();
        if (!latest) {
          showToast("Ainda não há lote para focar.", true);
          return;
        }
        state.filters.category = latest.category || "all";
        state.filters.period = latest.period || "all";
        state.filters.search = "";
        savePreferences();
        syncFilterInputs();
        renderCategoryFilters();
        renderSummaries();
        showToast(`Foco aplicado em ${getSummaryCategory(latest)} · ${getSummaryPeriod(latest)}.`);
      });
    }

    if (categoryFilters) {
      categoryFilters.addEventListener("click", (event) => {
        const button = event.target.closest("[data-category]");
        if (!button) return;
        state.filters.category = button.dataset.category || "all";
        savePreferences();
        renderCategoryFilters();
        renderSummaries();
      });
    }

    document.addEventListener("keydown", (event) => {
      const target = event.target;
      const isTyping = target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if (isTyping) return;

      if (event.key.toLowerCase() === "r") {
        event.preventDefault();
        window.loadData();
      }

      if (event.key === "/") {
        event.preventDefault();
        searchInput?.focus();
      }
    });
  }

  loadPreferences();
  attachEvents();
  updateClock();
  window.setInterval(updateClock, 1000);
  window.loadData();
  window.setInterval(window.loadData, AUTO_REFRESH_MS);
})();
