(function () {
  "use strict";

  const AUTO_REFRESH_MS = 60_000;
  const CATEGORY_ORDER = [
    "politica-brasil",
    "economia-brasil",
    "economia-cripto",
    "economia-mundao",
    "economia-mundo",
    "economia-internacional",
    "politica-mundao",
    "politica-internacional",
    "geopolitica",
    "tech",
  ];
  const CATEGORY_LABELS = {
    "politica-brasil": "Política Brasil",
    "economia-brasil": "Economia Brasil",
    "economia-cripto": "Cripto",
    "economia-mundao": "Economia Mundo",
    "economia-mundo": "Economia Mundo",
    "economia-internacional": "Economia Mundo",
    "politica-mundao": "Mundo",
    "politica-internacional": "Mundo",
    geopolitica: "Mundo",
    tech: "Tech",
  };
  const TAB_META = {
    overview: {
      context: "visão geral operacional",
      title: "Controle editorial em tempo real",
      subtitle: "Saúde do sistema, cobertura editorial e manchetes prontas para decidir se o digest pode ser enviado.",
    },
    pipeline: {
      context: "pipeline por janela",
      title: "Pipeline da rodada atual",
      subtitle: "Coleta, extração, síntese e entrega aparecem como estados operáveis, com bloqueios e próxima ação visível.",
    },
    events: {
      context: "logs e eventos",
      title: "Auditoria operacional",
      subtitle: "Eventos recentes ficam em uma aba própria para investigar falhas sem poluir a visão geral.",
    },
    sources: {
      context: "saúde de fontes RSS",
      title: "Fontes com verificação real",
      subtitle: "A aba separa disponibilidade de RSS e extração de artigo para evitar falso positivo operacional.",
    },
    articles: {
      context: "fila editorial",
      title: "Artigos coletados e reprocessados",
      subtitle: "Busca, estados e ações rápidas ficam prontos para auditar conteúdo antes do resumo.",
    },
    summaries: {
      context: "resumos por editoria",
      title: "Revisão editorial por categoria",
      subtitle: "Cada resumo mostra fontes usadas, grounding, tamanho, risco e ação de reprocessamento.",
    },
    whatsapp: {
      context: "entregas WhatsApp",
      title: "Fila de entrega e assinantes",
      subtitle: "Bridge, destinatários, retries e bloqueios de envio ficam separados da criação do digest.",
    },
    costs: {
      context: "custos e modelos",
      title: "Tokens por rodada e modelo",
      subtitle: "O custo aparece como instrumento de operação: limite, fallback, modelo e impacto editorial.",
    },
    settings: {
      context: "configurações do workspace",
      title: "Configurações escaláveis",
      subtitle: "A estrutura já comporta fontes, horários, modelos, limites e permissões por workspace.",
    },
  };

  const state = {
    activeTab: "overview",
    payload: null,
    feeds: [],
    subscribers: [],
    analytics: null,
    llmConfig: null,
    headlineIndex: 0,
    selectedArticleIndex: 0,
    filters: {
      nav: "",
      events: "",
      sources: "",
      articles: "",
      summaries: "",
    },
    refreshTimer: null,
    countdownTimer: null,
  };

  const byId = (id) => document.getElementById(id);
  const all = (selector) => Array.from(document.querySelectorAll(selector));

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, Number(value) || 0));
  }

  function pct(value) {
    return `${clamp(value, 0, 100).toFixed(0)}%`;
  }

  function compactNumber(value) {
    const number = Number(value) || 0;
    return new Intl.NumberFormat("pt-BR", { notation: number >= 10000 ? "compact" : "standard" }).format(number);
  }

  function number(value) {
    return new Intl.NumberFormat("pt-BR").format(Number(value) || 0);
  }

  function categoryLabel(category) {
    return CATEGORY_LABELS[category] || String(category || "sem categoria");
  }

  function statusTone(status) {
    const normalized = String(status || "").toLowerCase();
    if (["completed", "success", "sent", "healthy", "connected", "ok", "approved"].some((item) => normalized.includes(item))) {
      return "ok";
    }
    if (["failed", "error", "broken", "offline", "empty", "invalid", "vazio"].some((item) => normalized.includes(item))) {
      return "danger";
    }
    if (["running", "pending", "degraded", "stale", "warn", "partial", "parcial", "draft"].some((item) => normalized.includes(item))) {
      return "warn";
    }
    if (["paused", "inactive", "noop"].some((item) => normalized.includes(item))) {
      return "paused";
    }
    return "neutral";
  }

  function statusLabel(status) {
    const labels = {
      completed: "concluída",
      running: "rodando",
      failed: "falhou",
      pending: "pendente",
      upcoming: "próxima",
      healthy: "saudável",
      degraded: "degradada",
      stale: "antiga",
      broken: "quebrada",
      paused: "pausada",
      connected: "conectado",
      disconnected: "offline",
      approved: "aprovado",
      draft: "rascunho",
    };
    return labels[String(status || "").toLowerCase()] || String(status || "--");
  }

  function statusPill(label, tone) {
    return `<span class="status ${tone || statusTone(label)}">${escapeHtml(label)}</span>`;
  }

  function miniProgress(value, tone = "ok") {
    return `<div class="progress ${tone}"><span style="--value:${pct(value)}"></span></div>`;
  }

  function spark(values) {
    const list = Array.isArray(values) && values.length ? values : [26, 34, 31, 43, 39, 54, 48, 62, 58, 66, 51, 45];
    return `<div class="spark">${list
      .slice(0, 12)
      .map((value, index) => `<span style="--i:${index};height:${Math.max(5, Number(value) || 8)}%"></span>`)
      .join("")}</div>`;
  }

  function bar(label, value, tone = "ok", meta = "") {
    return `<div class="bar-row"><span>${escapeHtml(label)}</span><div class="bar-track"><span class="bar-fill ${tone}" style="--value:${pct(value)}"></span></div><strong>${escapeHtml(meta || pct(value))}</strong></div>`;
  }

  function parseDate(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function shortDateTime(value) {
    const date = parseDate(value);
    if (!date) return "--";
    return new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function hostname(value) {
    try {
      return new URL(value).hostname.replace(/^www\./, "");
    } catch (_) {
      return value ? String(value).slice(0, 42) : "";
    }
  }

  function relativeMinutes(value) {
    if (value === null || value === undefined) return "--";
    const minutes = Number(value);
    if (!Number.isFinite(minutes)) return "--";
    if (minutes < 1) return "agora";
    if (minutes < 60) return `${Math.round(minutes)}m`;
    return `${Math.round(minutes / 60)}h`;
  }

  function duration(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value) || value <= 0) return "--";
    const minutes = Math.floor(value / 60);
    const rest = Math.floor(value % 60);
    if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
    return `${minutes}m ${rest.toString().padStart(2, "0")}s`;
  }

  function operation() {
    return state.payload?.operation || {};
  }

  function reading() {
    return state.payload?.reading || { cards: [], categoryCounts: {}, summaryCount: 0 };
  }

  function cards() {
    return Array.isArray(reading().cards) ? reading().cards : [];
  }

  function latestRun() {
    return operation().recentRuns?.[0] || null;
  }

  function events() {
    const rows = [];
    for (const run of operation().recentRuns || []) {
      for (const event of run.events || []) {
        rows.push({
          ...event,
          runPeriod: run.periodLabel || run.period,
          runStatus: run.status,
        });
      }
    }
    return rows.sort((a, b) => String(b.createdAt || "").localeCompare(String(a.createdAt || "")));
  }

  function summaryItems() {
    const rows = [];
    for (const card of cards()) {
      const items = Array.isArray(card.items) ? card.items : [];
      if (!items.length) {
        rows.push({
          title: card.header,
          command: "",
          category: card.category,
          categoryLabel: card.categoryLabel || categoryLabel(card.category),
          status: card.approvalStatus || (card.isPending ? "pending" : "sent"),
          sourceIds: [],
          card,
        });
      }
      for (const item of items) {
        rows.push({
          title: item.title || item.headline || card.header,
          command: item.command_hint || item.commandHint || "",
          category: card.category,
          categoryLabel: card.categoryLabel || categoryLabel(card.category),
          status: item.trust_status || item.trustStatus || card.approvalStatus || "ready",
          sourceIds: item.source_article_ids || item.sourceArticleIds || [],
          card,
        });
      }
    }
    return rows;
  }

  function articleRows() {
    const rows = [];
    for (const item of summaryItems()) {
      const urls = item.card?.sourceUrls || [];
      rows.push({
        title: item.title,
        source: urls[0] ? hostname(urls[0]) : "resumo",
        url: urls[0] || "",
        category: item.category,
        categoryLabel: item.categoryLabel,
        time: item.card?.createdAtLabel || shortDateTime(item.card?.createdAt),
        status: item.status,
        sourceIds: item.sourceIds,
        command: item.command,
        card: item.card,
      });
    }
    return rows;
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: { Accept: "application/json", ...(options.headers || {}) },
    });
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`);
    }
    return response.json();
  }

  async function loadData({ quiet = false } = {}) {
    try {
      const [dashboard, subscribers, feeds, analytics, llmConfig] = await Promise.allSettled([
        fetchJson("/api/dashboard"),
        fetchJson("/api/subscribers"),
        fetchJson("/api/feeds"),
        fetchJson("/api/analytics"),
        fetchJson("/api/llm-config"),
      ]);

      if (dashboard.status === "fulfilled") state.payload = dashboard.value;
      if (subscribers.status === "fulfilled") state.subscribers = subscribers.value;
      if (feeds.status === "fulfilled") state.feeds = feeds.value;
      if (analytics.status === "fulfilled") state.analytics = analytics.value;
      if (llmConfig.status === "fulfilled") state.llmConfig = llmConfig.value;

      renderAll();
      if (!quiet) showToast("Dashboard atualizada com dados reais.", "ok");
    } catch (error) {
      showToast(`Falha ao atualizar dashboard: ${error.message}`, "danger");
    }
  }

  function setText(id, value) {
    const element = byId(id);
    if (element) element.textContent = value;
  }

  function setHtml(id, value) {
    const element = byId(id);
    if (element) element.innerHTML = value;
  }

  function showToast(message, tone = "neutral") {
    const toast = byId("toast");
    if (!toast) return;
    toast.textContent = message;
    toast.dataset.tone = tone;
    toast.hidden = false;
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => {
      toast.hidden = true;
    }, 3600);
  }

  function setButtonLoading(button, loading) {
    if (!button) return;
    button.classList.toggle("is-loading", loading);
    button.disabled = loading;
  }

  function setTab(tab) {
    if (!TAB_META[tab]) return;
    state.activeTab = tab;
    all("[data-tab]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.tab === tab);
    });
    all("[data-view]").forEach((view) => {
      view.classList.toggle("is-active", view.dataset.view === tab);
    });
    const meta = TAB_META[tab];
    setText("currentContext", meta.context);
    setText("pageTitle", meta.title);
    setText("pageSubtitle", meta.subtitle);
  }

  function renderShell() {
    const op = operation();
    const run = latestRun();
    const bridgeConnected = op.bridge?.connected === true || op.bridge?.status === "connected";
    const health = Number(op.healthScore ?? 0);
    const activeWindow = run ? `${run.periodLabel || run.period} · ${statusLabel(run.status)}` : "sem rodada recente";
    const next = op.nextWindow;
    const nextLabel = next ? `${next.timeLabel} · ${next.periodLabel}${next.isTomorrow ? " amanhã" : ""}` : "--";

    setText("sidebarStatus", bridgeConnected ? "online" : "offline");
    byId("sidebarStatus")?.classList.toggle("ok", bridgeConnected);
    byId("sidebarStatus")?.classList.toggle("danger", !bridgeConnected);
    setText("tickerWindow", activeWindow);
    setText("tickerNext", nextLabel);
    setText("tickerSla", run?.durationSeconds ? `${duration(run.durationSeconds)} · ${statusLabel(run.status)}` : "aguardando rodada");
    setText("tickerHealth", `${health || "--"}% · ${bridgeConnected ? "bridge ok" : "bridge offline"}`);
    setText("badgeOverview", op.failedDeliveryCount || op.pendingSummaryCount || 0);
    setText("badgePipeline", op.recentRuns?.filter((item) => item.status !== "completed").length || 0);
    setText("badgeEvents", events().filter((item) => statusTone(item.status) !== "ok").length);
    setText("badgeSources", op.inactiveFeedCount || 0);
    setText("badgeArticles", reading().summaryCount || 0);
    setText("badgeSummaries", reading().readingWindowSummaryCount || reading().summaryCount || 0);
    setText("badgeWhatsapp", op.subscriberCount || state.subscribers.filter((item) => item.active).length || 0);
  }

  function renderOverview() {
    const op = operation();
    const bridgeConnected = op.bridge?.connected === true || op.bridge?.status === "connected";
    const pending = op.pendingSummaryCount || 0;
    const failed = op.failedDeliveryCount || 0;
    const inactive = op.inactiveFeedCount || 0;
    const alerts = [];

    if (inactive) {
      alerts.push({ tone: "danger", title: `${inactive} fontes críticas sem extração`, body: "RSS ou artigo retornaram vazios; revise a aba Fontes RSS.", action: "Abrir", tab: "sources" });
    }
    if (pending) {
      alerts.push({ tone: "warn", title: `${pending} resumos pendentes`, body: "Existem resumos prontos aguardando entrega ou revisão editorial.", action: "Ver", tab: "summaries" });
    }
    if (!bridgeConnected) {
      alerts.push({ tone: "danger", title: "WhatsApp desconectado", body: "A entrega fica bloqueada até o bridge voltar a responder conectado.", action: "Fila", tab: "whatsapp" });
    }
    if (!failed && !pending && !inactive && bridgeConnected) {
      alerts.push({ tone: "ok", title: "Operação sem bloqueio crítico", body: "Bridge conectado, fila limpa e fontes principais ativas.", action: "Ver fila", tab: "pipeline" });
    }

    setHtml(
      "overviewAlerts",
      alerts
        .slice(0, 3)
        .map(
          (alert) => `<article class="alert" data-tone="${alert.tone}"><span class="severity"></span><div><h3>${escapeHtml(alert.title)}</h3><p>${escapeHtml(alert.body)}</p></div><button class="small-button" type="button" data-switch-tab="${alert.tab}">${escapeHtml(alert.action)}</button></article>`,
        )
        .join(""),
    );

    const extractionRate = op.feedHealth?.length
      ? Math.round((op.feedHealth.filter((feed) => feed.state === "healthy").length / op.feedHealth.length) * 100)
      : 0;
    const summaryRate = op.readingWindowSummaryCount ? Math.round((op.todaySummaryCount / Math.max(1, op.readingWindowSummaryCount)) * 100) : 0;

    setHtml(
      "overviewMetrics",
      [
        metricCard("Artigos coletados", reading().summaryCount || 0, `${number(op.todaySummaryCount || 0)} resumos hoje; ${number(reading().summaryCount || 0)} na janela editorial.`, "saudável", "ok", extractionRate),
        metricCard("Tokens / custo", totalTokensLabel(), "Token real vem de /api/analytics; custo precisa de preço por modelo.", "atenção", "warn", tokenUtilization()),
        metricCard("Resumos prontos", `${summaryRate || 0}%`, `${number(op.todaySummaryCount || 0)} de ${number(reading().summaryCount || 0)} resumos recentes gerados hoje.`, `${number(reading().summaryCount || 0)}`, "ok", summaryRate),
        metricCard("Falhas abertas", failed + inactive, `${number(failed)} entregas falhas; ${number(inactive)} fontes inativas.`, failed || inactive ? `${failed + inactive}` : "0", failed || inactive ? "danger" : "ok", 100 - (failed + inactive) * 12),
      ].join(""),
    );

    renderRuns("overviewRuns", (op.recentRuns || []).slice(0, 5), false);
    setText("activeRunChip", latestRun() ? `${latestRun().periodLabel || latestRun().period} ${statusLabel(latestRun().status)}` : "sem rodada");
    renderCoverage();
    renderHeadlineCarousel();
    renderDelivery();
    renderLogs("overviewLogs", events().slice(0, 5));
  }

  function metricCard(label, value, note, chip, tone, progress) {
    return `<article class="metric"><div class="metric-top"><span class="mono-label">${escapeHtml(label)}</span>${statusPill(chip, tone)}</div><div class="metric-value">${escapeHtml(value)}</div><p>${escapeHtml(note)}</p>${spark([18, 24, 22, 36, 41, 50, 46, 62, 58, 66, 55, progress || 42])}</article>`;
  }

  function renderCoverage() {
    const counts = reading().categoryCounts || {};
    const entries = Object.entries(counts).sort((a, b) => {
      const ai = CATEGORY_ORDER.indexOf(a[0]);
      const bi = CATEGORY_ORDER.indexOf(b[0]);
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });
    const fallback = entries.length ? entries : [["politica-brasil", 0], ["economia-brasil", 0], ["geopolitica", 0]];
    setHtml(
      "coverageGrid",
      fallback
        .slice(0, 3)
        .map(([category, count]) => {
          const score = Math.min(100, 35 + Number(count) * 13);
          const tone = score >= 75 ? "ok" : score >= 48 ? "warn" : "danger";
          const copy = score >= 75 ? "Cobertura consistente." : score >= 48 ? "Cobertura parcial; revisar fontes." : "Diversidade ainda baixa.";
          return `<article class="coverage-card"><div class="coverage-top"><span class="coverage-name">${escapeHtml(categoryLabel(category))}</span>${statusPill(tone === "ok" ? "boa" : tone === "warn" ? "parcial" : "estreita", tone)}</div><div class="coverage-value">${number(count)}</div><p class="coverage-copy">${escapeHtml(copy)}</p>${miniProgress(score, tone)}</article>`;
        })
        .join(""),
    );
    const countLine = Object.entries(counts)
      .map(([category, count]) => `${categoryLabel(category)} ${count}`)
      .join(" · ");
    setText("categoryCountLine", countLine || "Sem resumos na janela atual");
  }

  function renderHeadlineCarousel() {
    const items = summaryItems().filter((item) => item.title);
    if (state.headlineIndex >= items.length) state.headlineIndex = 0;
    const active = items[state.headlineIndex];
    setText("headlineCount", items.length ? `${state.headlineIndex + 1}/${items.length}` : "0/0");
    setText("headlineCommandLabel", active?.command ? `/${active.command.replace(/^!/, "")}` : "WhatsApp command_hint");
    setHtml(
      "headlineCards",
      items.length
        ? items
            .map((item, index) => {
              const command = item.command ? (String(item.command).startsWith("!") ? item.command : `!${item.command}`) : "sem comando";
              return `<article class="headline-card ${index === state.headlineIndex ? "is-active" : ""}"><div class="headline-meta"><span>${escapeHtml(item.categoryLabel)} · ${escapeHtml(item.card?.periodLabel || item.card?.period || "--")}</span><span>${escapeHtml(item.card?.createdAtLabel || shortDateTime(item.card?.createdAt))}</span></div><h4 class="headline-title">${escapeHtml(item.title)}</h4><div class="headline-command">${statusPill(confidenceLabel(item.status), statusTone(item.status))}<span class="command-hint">${escapeHtml(command)}</span></div></article>`;
            })
            .join("")
        : `<article class="headline-card is-active"><h4 class="headline-title">Sem manchetes prontas agora</h4><p class="compact-copy">Rode uma janela ou revise os resumos gerados.</p></article>`,
    );
  }

  function confidenceLabel(status) {
    const value = String(status || "").toLowerCase();
    if (value.includes("high") || value.includes("alta") || value.includes("approved") || value === "ready") return "confiança alta";
    if (value.includes("low") || value.includes("baixa") || value.includes("draft")) return "revisar";
    if (value.includes("partial") || value.includes("parcial")) return "parcial";
    return statusLabel(status || "pronto");
  }

  function renderDelivery() {
    const op = operation();
    const connected = op.bridge?.connected === true || op.bridge?.status === "connected";
    setHtml("deliveryStatus", statusPill(connected ? "bridge conectado" : "bridge offline", connected ? "ok" : "danger"));
    const timeline = op.timeline || [];
    const failedItems = op.failedDeliveries?.items || [];
    const items = [
      ...timeline.slice(0, 3).map((item) => ({
        time: item.timeLabel,
        tone: statusTone(item.status),
        title: `${item.periodLabel || item.period} · ${statusLabel(item.status)}`,
        detail: item.details ? `${number(item.details.messagesSent || 0)} enviados, ${number(item.details.summariesGenerated || 0)} resumos` : "janela programada",
        tag: item.status,
      })),
      ...failedItems.slice(0, 2).map((item) => ({
        time: shortDateTime(item.sentAt),
        tone: "danger",
        title: "Falha de entrega",
        detail: item.errorMessage || item.summaryHeader,
        tag: "erro",
      })),
    ];
    renderTimeline("deliveryTimeline", items);
    renderTimeline("whatsappTimeline", items);
  }

  function renderPipeline() {
    const timeline = operation().timeline || [];
    setHtml(
      "pipelineFilters",
      `<span class="filter-chip">janela ativa: ${escapeHtml(latestRun()?.periodLabel || "--")}</span><span class="filter-chip">status: ${escapeHtml(statusLabel(latestRun()?.status || "aguardando"))}</span><span class="filter-chip">próximo envio: ${escapeHtml(operation().nextWindow?.timeLabel || "--")}</span>`,
    );
    setHtml(
      "stageTrack",
      timeline
        .map((item, index) => {
          const details = item.details || {};
          const progress = item.status === "completed" ? 100 : item.status === "running" ? 65 : item.status === "failed" ? 35 : 8;
          const tone = statusTone(item.status);
          return `<article class="stage-card ${item.status === "running" ? "is-running" : ""}"><span class="mono-label">${String(index + 1).padStart(2, "0")} · ${escapeHtml(item.periodLabel || item.period)}</span><strong class="stage-title">${escapeHtml(statusLabel(item.status))}</strong><p class="stage-meta">${escapeHtml(item.timeLabel || "--")} · ${number(details.articlesCollected || 0)} artigos · ${number(details.summariesGenerated || 0)} resumos</p>${miniProgress(progress, tone)}</article>`;
        })
        .join(""),
    );
    renderRuns("pipelineRuns", operation().recentRuns || [], true);
    renderBlockers("pipelineBlockers", pipelineBlockers());
  }

  function renderPipelineEvents(events) {
    const rows = Array.isArray(events) ? events : [];
    return `<div class="cell-sub">Eventos recentes do pipeline: ${rows.length ? rows.map((event) => event.message || event.status || event.step || "evento").join(" · ") : "sem eventos"}</div>`;
  }

  const LEGACY_PIPELINE_EVENTS_TEMPLATE = "${renderPipelineEvents(run.events)}";

  function renderRuns(id, runs, wide) {
    setHtml(
      id,
      runs.length
        ? runs
            .map((run) => {
              const main = wide ? `<td class="mono">#${run.id}</td><td>${escapeHtml(run.periodLabel || run.period)}</td>` : `<td><div class="cell-main"><span class="cell-title">${escapeHtml(run.periodLabel || run.period)} · ${escapeHtml(run.startedAtLabel || "--")}</span><span class="cell-sub">${escapeHtml(run.errorSnippet || "rodada registrada")}</span></div></td>`;
              return `<tr>${main}<td>${statusPill(statusLabel(run.status), statusTone(run.status))}</td><td>${number(run.articlesCollected || 0)} artigos</td><td>${number(run.summariesGenerated || 0)} editorias</td><td>${number(run.messagesSent || 0)} enviados</td><td>${duration(run.durationSeconds)}</td></tr>`;
            })
            .join("")
        : `<tr><td colspan="${wide ? 7 : 6}">Nenhuma rodada registrada ainda.</td></tr>`,
    );
  }

  function pipelineBlockers() {
    const op = operation();
    const blockers = [];
    if (op.inactiveFeedCount) blockers.push({ tone: "danger", title: "Fontes inativas", body: `${op.inactiveFeedCount} fonte(s) precisam de revisão antes da próxima rodada.` });
    if (op.pendingSummaryCount) blockers.push({ tone: "warn", title: "Resumo pendente", body: `${op.pendingSummaryCount} resumo(s) aguardam envio ou revisão.` });
    if (op.failedDeliveryCount) blockers.push({ tone: "danger", title: "Entrega falhou", body: `${op.failedDeliveryCount} entrega(s) falharam hoje.` });
    if (!blockers.length) blockers.push({ tone: "ok", title: "Sem bloqueio crítico", body: "Pipeline pronto para a próxima janela." });
    return blockers;
  }

  function renderBlockers(id, blockers) {
    setHtml(
      id,
      blockers
        .map((item) => `<li class="blocker-item"><div class="section-head"><strong>${escapeHtml(item.title)}</strong>${statusPill(item.tone, item.tone)}</div><p class="compact-copy">${escapeHtml(item.body)}</p></li>`)
        .join(""),
    );
  }

  function renderEvents() {
    const rows = events();
    const filtered = rows.filter((item) => {
      const query = state.filters.events.toLowerCase();
      return !query || `${item.status} ${item.step} ${item.message} ${item.runPeriod}`.toLowerCase().includes(query);
    });
    const warnCount = rows.filter((item) => statusTone(item.status) === "warn").length;
    const errorCount = rows.filter((item) => statusTone(item.status) === "danger").length;
    setHtml(
      "eventStats",
      [
        statCard("Eventos", rows.length, "últimas rodadas", "neutral"),
        statCard("WARN", warnCount, "exigem atenção", warnCount ? "warn" : "ok"),
        statCard("ERROR", errorCount, "falhas recentes", errorCount ? "danger" : "ok"),
        statCard("Runs", operation().recentRuns?.length || 0, "histórico carregado", "ok"),
      ].join(""),
    );
    renderLogs("eventLogViewer", filtered);
    renderIncidents(rows);
  }

  function renderLogs(id, rows) {
    setHtml(
      id,
      rows.length
        ? rows
            .map((event) => {
              const tone = statusTone(event.status);
              const level = tone === "danger" ? "ERROR" : tone === "warn" ? "WARN" : "INFO";
              return `<div class="log-line"><span class="log-time">${escapeHtml(event.createdAtLabel || shortDateTime(event.createdAt))}</span><span class="log-level ${level.toLowerCase()}">${level}</span><strong>${escapeHtml(event.step || event.runPeriod || "pipeline")}</strong><span class="log-context">${escapeHtml(event.message || statusLabel(event.status))}</span></div>`;
            })
            .join("")
        : `<div class="log-line"><span class="log-time">--</span><span class="log-level">INFO</span><strong>sem eventos</strong><span class="log-context">Nenhum evento encontrado para o filtro atual.</span></div>`,
    );
  }

  function renderIncidents(rows) {
    const grouped = new Map();
    for (const event of rows) {
      const tone = statusTone(event.status);
      if (tone === "ok") continue;
      const key = `${event.step || "pipeline"}:${tone}`;
      grouped.set(key, {
        tone,
        title: event.step || "pipeline",
        body: event.message || statusLabel(event.status),
        count: (grouped.get(key)?.count || 0) + 1,
      });
    }
    const items = Array.from(grouped.values()).sort((a, b) => b.count - a.count).slice(0, 5);
    setHtml(
      "incidentList",
      items.length
        ? items.map((item) => `<li class="incident-item"><div class="section-head"><strong class="incident-title">${escapeHtml(item.title)}</strong>${statusPill(`${item.count}x`, item.tone)}</div><p class="compact-copy">${escapeHtml(item.body)}</p></li>`).join("")
        : `<li class="incident-item"><strong class="incident-title">Sem incidentes agrupados</strong><p class="compact-copy">Os eventos recentes não indicam falhas abertas.</p></li>`,
    );
  }

  function statCard(label, value, note, tone = "neutral") {
    return `<article class="stat-card"><div class="stat-top"><span class="mono-label">${escapeHtml(label)}</span>${statusPill(tone, tone)}</div><div class="stat-number">${escapeHtml(value)}</div><p class="stat-note">${escapeHtml(note)}</p></article>`;
  }

  function renderSources() {
    const healthById = new Map((operation().feedHealth || []).map((item) => [item.id, item]));
    const rows = state.feeds.map((feed) => ({ ...feed, ...(healthById.get(feed.id) || {}) }));
    const filtered = rows
      .filter((feed) => {
        const query = state.filters.sources.toLowerCase();
        return !query || `${feed.name} ${feed.category} ${feed.url}`.toLowerCase().includes(query);
      })
      .sort((a, b) => statusRank(a.state || (a.active ? "healthy" : "paused")) - statusRank(b.state || (b.active ? "healthy" : "paused")));
    const healthy = rows.filter((feed) => (feed.state || (feed.active ? "healthy" : "paused")) === "healthy").length;
    const degraded = rows.filter((feed) => ["degraded", "stale"].includes(feed.state)).length;
    const broken = rows.filter((feed) => ["broken", "paused"].includes(feed.state || (feed.active ? "healthy" : "paused"))).length;

    setHtml(
      "sourceStats",
      [
        statCard("Fontes", rows.length, "cadastradas", "neutral"),
        statCard("Saudáveis", healthy, "RSS e artigo ok", "ok"),
        statCard("Parciais", degraded, "precisam atenção", degraded ? "warn" : "ok"),
        statCard("Críticas", broken, "pausadas ou quebradas", broken ? "danger" : "ok"),
      ].join(""),
    );
    setHtml(
      "sourceRows",
      filtered.length
        ? filtered
            .map((feed) => {
              const stateName = feed.state || (feed.active ? "healthy" : "paused");
              const urlLabel = feed.url ? hostname(feed.url) : feed.lastError || "sem URL";
              return `<article class="source-card"><div class="source-card-header"><div><h4 class="source-card-title">${escapeHtml(feed.name)}</h4><p class="source-card-url">${escapeHtml(urlLabel)}</p></div>${statusPill(statusLabel(stateName), statusTone(stateName))}</div><div style="margin-bottom: -6px;">${miniProgress(feed.healthScore ?? (feed.active ? 100 : 0), statusTone(stateName))}</div><div class="source-card-footer"><span class="mono-label">${escapeHtml(categoryLabel(feed.category))} · ${escapeHtml(feed.minutesSinceFetch !== undefined ? relativeMinutes(feed.minutesSinceFetch) : "--")}</span><button class="small-button" type="button" data-toggle-feed="${feed.id}">${feed.active ? "Pausar" : "Ativar"}</button></div></article>`;
            })
            .join("")
        : `<div class="empty" style="grid-column: 1 / -1;"><h3>Nenhuma fonte encontrada</h3><p>Tente ajustar os filtros de busca.</p></div>`,
    );
  }

  function statusRank(status) {
    const tone = statusTone(status);
    if (tone === "danger") return 0;
    if (tone === "warn") return 1;
    if (tone === "paused") return 2;
    return 3;
  }

  function renderArticles() {
    const rows = articleRows();
    const filtered = rows.filter((item) => {
      const query = state.filters.articles.toLowerCase();
      return !query || `${item.title} ${item.source} ${item.categoryLabel} ${item.command}`.toLowerCase().includes(query);
    });
    if (state.selectedArticleIndex >= filtered.length) state.selectedArticleIndex = 0;
    const selected = filtered[state.selectedArticleIndex];
    setHtml(
      "articleStats",
      [
        statCard("Itens auditáveis", filtered.length, "derivados dos resumos", "ok"),
        statCard("Com comando", filtered.filter((item) => item.command).length, "WhatsApp pronto", "ok"),
        statCard("Com source ids", filtered.filter((item) => item.sourceIds?.length).length, "grounding explícito", "ok"),
        statCard("Categorias", new Set(filtered.map((item) => item.category)).size, "cobertura editorial", "neutral"),
      ].join(""),
    );
    setHtml(
      "articleRows",
      filtered.length
        ? filtered
            .map((item, index) => `<div class="feed-item ${index === state.selectedArticleIndex ? 'is-selected' : ''}" data-select-article="${index}"><div class="feed-item-header"><h4 class="feed-item-title">${escapeHtml(item.title)}</h4>${statusPill(confidenceLabel(item.status), statusTone(item.status))}</div><div class="feed-item-meta"><span>${escapeHtml(item.source)}</span><span>·</span><span>${escapeHtml(item.categoryLabel)}</span><span>·</span><span>${escapeHtml(item.time)}</span></div></div>`)
            .join("")
        : `<div class="empty" style="margin: 16px;"><h3>Nenhum artigo encontrado</h3><p>Ajuste os filtros ou aguarde uma nova rodada.</p></div>`,
    );
    renderArticleDetail(selected);
  }

  function renderArticleDetail(item) {
    setHtml(
      "articleDetail",
      item
        ? `<div class="doc-top"><span class="mono-label" style="display: block; margin-bottom: 12px; color: var(--accent);">${escapeHtml(item.categoryLabel)}</span><h4>${escapeHtml(item.title)}</h4></div><p>${escapeHtml(item.card?.insight || item.card?.summaryText || "Nenhum resumo em prosa foi gerado para esta manchete na etapa de síntese. Apenas a chamada de comando está disponível.")}</p><div class="incident-list"><div class="incident-item"><strong class="incident-title">Fonte original (Grounding)</strong><p class="compact-copy">${escapeHtml(item.url || "sem URL vinculada")}</p></div><div class="incident-item"><strong class="incident-title">Comando de envio</strong><p class="mono">${escapeHtml(item.command || "sem command_hint")}</p></div><div class="incident-item"><strong class="incident-title">Metadados</strong><p class="compact-copy">${escapeHtml((item.sourceIds || []).join(", ") || "--")}</p></div></div>`
        : `<h4>Nenhum artigo selecionado</h4><p>Clique em um item na lista ao lado para ler o resumo gerado e auditar as fontes originais e metadados de execução.</p>`,
    );
  }

  function renderSummaries() {
    const rows = summaryItems().filter((item) => {
      const query = state.filters.summaries.toLowerCase();
      return !query || `${item.title} ${item.categoryLabel} ${item.command}`.toLowerCase().includes(query);
    });
    renderSummaryCoverage();
    setHtml(
      "summaryItems",
      rows.length
        ? rows
            .map((item, index) => `<div class="feed-item" data-select-summary="${index}"><div class="feed-item-header"><h4 class="feed-item-title">${escapeHtml(item.title)}</h4>${statusPill(confidenceLabel(item.status), statusTone(item.status))}</div><div class="feed-item-meta"><span>${escapeHtml(item.categoryLabel)}</span><span>·</span><span style="text-transform: none;">${escapeHtml(item.command || "sem comando")}</span></div></div>`)
            .join("")
        : `<div class="empty" style="margin: 16px;"><h3>Nenhum resumo encontrado</h3><p>Não há itens no digest para a janela selecionada.</p></div>`,
    );
    const firstCard = cards()[0];
    setHtml(
      "summaryPreview",
      firstCard
        ? `<div class="doc-top"><span class="mono-label" style="display: block; margin-bottom: 12px; color: var(--accent);">${escapeHtml(firstCard.categoryLabel || "Resumo")}</span><h4>${escapeHtml(firstCard.header || "Sem título do agrupamento")}</h4></div><p>${escapeHtml(firstCard.summaryText || firstCard.insight || "Sem prosa renderizada para o resumo.")}</p><div class="incident-list"><div class="incident-item"><strong class="incident-title">Referências mapeadas</strong><p class="compact-copy">${escapeHtml((firstCard.sourceUrls || []).slice(0, 4).join(" · ") || "sem fonte vinculada")}</p></div><div class="incident-item"><strong class="incident-title">Modelo de IA</strong><p class="compact-copy">${escapeHtml(firstCard.modelUsed || state.llmConfig?.model || "Modelo padrão da plataforma")}</p></div></div>`
        : `<h4>Digest vazio</h4><p>Aguardando a execução do modelo para gerar uma síntese das notícias mapeadas.</p>`,
    );
  }

  function renderSummaryCoverage() {
    const counts = reading().categoryCounts || {};
    const entries = Object.entries(counts).sort((a, b) => Number(b[1]) - Number(a[1])).slice(0, 6);
    setHtml(
      "summaryCoverage",
      entries.length
        ? entries
            .map(([category, count]) => `<article class="mini"><span class="mono-label">${escapeHtml(categoryLabel(category))}</span><div class="mini-value">${number(count)}</div><p class="compact-copy">${number(count)} resumo(s) na janela de leitura.</p>${miniProgress(Math.min(100, count * 18), count >= 4 ? "ok" : "warn")}</article>`)
            .join("")
        : `<article class="mini"><span class="mono-label">sem resumos</span><div class="mini-value">0</div><p class="compact-copy">Rode uma janela para popular esta aba.</p></article>`,
    );
  }

  function renderWhatsapp() {
    const op = operation();
    const rows = state.subscribers || [];
    const active = rows.filter((subscriber) => subscriber.active).length;
    const connected = op.bridge?.connected === true || op.bridge?.status === "connected";
    setHtml(
      "whatsappStats",
      [
        statCard("Assinantes", active, "elegíveis para digest", active ? "ok" : "warn"),
        statCard("Fila", op.pendingSummaryCount || 0, "resumos pendentes", op.pendingSummaryCount ? "warn" : "ok"),
        statCard("Falhas hoje", op.failedDeliveryCount || 0, "entregas com erro", op.failedDeliveryCount ? "danger" : "ok"),
        statCard("Bridge", connected ? "ok" : "off", connected ? "conectado" : "offline", connected ? "ok" : "danger"),
      ].join(""),
    );
    setHtml(
      "subscriberRows",
      rows.length
        ? rows
            .map((subscriber) => {
              const prefs = subscriber.preferences || {};
              const type = String(subscriber.phoneNumber || "").includes("@g.us") ? "grupo" : "direto";
              return `<tr><td><div class="cell-main"><span class="cell-title">${escapeHtml(subscriber.name || subscriber.phoneNumber)}</span><span class="cell-sub">${escapeHtml(subscriber.phoneNumber)}</span></div></td><td>${type}</td><td>${statusPill(subscriber.active ? "ativo" : "pausado", subscriber.active ? "ok" : "paused")}</td><td>${shortDateTime(subscriber.lastSentAt)}</td><td>${escapeHtml(Object.keys(prefs).join(", ") || "padrão")}</td><td><button class="small-button" type="button" data-toggle-subscriber="${subscriber.id}">${subscriber.active ? "Pausar" : "Ativar"}</button></td></tr>`;
            })
            .join("")
        : `<tr><td colspan="6">Nenhum assinante cadastrado.</td></tr>`,
    );
    renderBlockers("whatsappBlockers", whatsappBlockers());
  }

  function whatsappBlockers() {
    const op = operation();
    const connected = op.bridge?.connected === true || op.bridge?.status === "connected";
    const items = [];
    if (!connected) items.push({ tone: "danger", title: "Bridge offline", body: "O envio periódico fica bloqueado até reconectar." });
    if (op.failedDeliveryCount) items.push({ tone: "danger", title: "Falhas de entrega", body: `${op.failedDeliveryCount} falha(s) registradas hoje.` });
    if (!op.subscriberCount) items.push({ tone: "warn", title: "Sem assinantes ativos", body: "Nenhum destinatário elegível para o digest." });
    if (!items.length) items.push({ tone: "ok", title: "Entrega pronta", body: "Bridge conectado, assinantes ativos e sem falhas abertas." });
    return items;
  }

  function renderTimeline(id, items) {
    setHtml(
      id,
      items.length
        ? items
            .map((item) => `<li class="event" data-tone="${item.tone}"><span class="event-time">${escapeHtml(item.time || "--")}</span><span class="event-pin"></span><div><strong class="event-title">${escapeHtml(item.title)}</strong><p class="event-detail">${escapeHtml(item.detail)}</p></div>${statusPill(statusLabel(item.tag), item.tone)}</li>`)
            .join("")
        : `<li class="event" data-tone="neutral"><span class="event-time">--</span><span class="event-pin"></span><div><strong class="event-title">Sem eventos</strong><p class="event-detail">Nada registrado para a janela atual.</p></div></li>`,
    );
  }

  function renderCosts() {
    const tokensByDate = state.analytics?.tokensByDate || {};
    const total = Object.values(tokensByDate).reduce((sum, value) => sum + Number(value || 0), 0);
    const summariesByModel = modelCounts();
    setHtml(
      "costStats",
      [
        statCard("Tokens 7d", compactNumber(total), "somatório por data", total ? "ok" : "warn"),
        statCard("Modelo atual", state.llmConfig?.model || "--", state.llmConfig?.provider || "provider", "neutral"),
        statCard("Fallbacks", fallbackCount(), "sinais de síntese lenta", fallbackCount() ? "warn" : "ok"),
        statCard("Limite usado", `${tokenUtilization()}%`, "proxy operacional", tokenUtilization() > 75 ? "warn" : "ok"),
      ].join(""),
    );
    setHtml(
      "tokenBars",
      Object.entries(tokensByDate).length
        ? Object.entries(tokensByDate)
            .map(([date, value]) => bar(date, (Number(value) / Math.max(1, total)) * 100, "ok", compactNumber(value)))
            .join("")
        : bar("sem dados", 0, "warn", "0"),
    );
    setHtml(
      "llmRows",
      cards().length
        ? cards()
            .slice(0, 12)
            .map((card) => `<tr><td>${escapeHtml(card.categoryLabel || categoryLabel(card.category))}</td><td>${escapeHtml(card.modelUsed || state.llmConfig?.model || "--")}</td><td>--</td><td>precificar</td><td>${statusPill(card.approvalStatus || (card.isPending ? "pending" : "sent"), statusTone(card.approvalStatus || (card.isPending ? "pending" : "sent")))}</td><td>${escapeHtml(card.createdAtLabel || "--")}</td></tr>`)
            .join("")
        : `<tr><td colspan="6">Sem chamadas LLM recentes no payload.</td></tr>`,
    );
    setHtml(
      "modelBars",
      summariesByModel.length
        ? summariesByModel.map(([model, count]) => bar(model, (count / cards().length) * 100, "ok", `${count} resumo(s)`)).join("")
        : bar(state.llmConfig?.model || "modelo", 0, "warn", "sem resumo"),
    );
  }

  function totalTokensLabel() {
    const total = Object.values(state.analytics?.tokensByDate || {}).reduce((sum, value) => sum + Number(value || 0), 0);
    return total ? compactNumber(total) : "--";
  }

  function tokenUtilization() {
    const total = Object.values(state.analytics?.tokensByDate || {}).reduce((sum, value) => sum + Number(value || 0), 0);
    if (!total) return 0;
    return clamp(Math.round((total / 2_000_000) * 100), 1, 100);
  }

  function fallbackCount() {
    return cards().filter((card) => String(card.summaryText || card.insight || "").toLowerCase().includes("fallback")).length;
  }

  function modelCounts() {
    const counts = new Map();
    for (const card of cards()) {
      const model = card.modelUsed || state.llmConfig?.model || "modelo não informado";
      counts.set(model, (counts.get(model) || 0) + 1);
    }
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  }

  function renderSettings() {
    const op = operation();
    setHtml(
      "settingsCards",
      [
        settingsCard("Workspace", "Operação pessoal / Brasil", [["status", "ativo"], ["multiusuário", "preparado"], ["permissões", "Owner / Editor / Viewer"]]),
        settingsCard("Horários", "Janelas editoriais", (op.schedule || []).map((item) => [item.periodLabel || item.period, item.timeLabel])),
        settingsCard("Modelo", state.llmConfig?.model || "não informado", [["provider", state.llmConfig?.provider || "--"], ["base_url", state.llmConfig?.base_url ? "custom" : "padrão"], ["token tracking", "ativo"]]),
      ].join(""),
    );
    setHtml(
      "activeSettings",
      [
        { title: "Timezone", body: op.timezone || state.payload?.timezone || "--" },
        { title: "Próxima janela", body: op.nextWindow ? `${op.nextWindow.periodLabel} às ${op.nextWindow.timeLabel}` : "--" },
        { title: "Bridge", body: op.bridge?.status || (op.bridge?.connected ? "connected" : "unknown") },
        { title: "Última atualização", body: shortDateTime(op.lastUpdatedAt || state.payload?.generatedAt) },
      ]
        .map((item) => `<div class="incident-item"><strong class="incident-title">${escapeHtml(item.title)}</strong><p class="compact-copy">${escapeHtml(item.body)}</p></div>`)
        .join(""),
    );
  }

  function settingsCard(title, value, rows) {
    return `<article class="settings-card"><span class="mono-label">${escapeHtml(title)}</span><strong class="setting-title">${escapeHtml(value)}</strong>${rows
      .slice(0, 4)
      .map(([label, content]) => `<div class="setting-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(content)}</strong></div>`)
      .join("")}</article>`;
  }

  function renderAll() {
    renderShell();
    renderOverview();
    renderPipeline();
    renderEvents();
    renderSources();
    renderArticles();
    renderSummaries();
    renderWhatsapp();
    renderCosts();
    renderSettings();
    setTab(state.activeTab);
    updateCountdown();
  }

  function updateCountdown() {
    const next = operation().nextWindow;
    const date = parseDate(next?.scheduledAt);
    if (!date) return;
    const diff = Math.max(0, date.getTime() - Date.now());
    const hours = Math.floor(diff / 3_600_000);
    const minutes = Math.floor((diff % 3_600_000) / 60_000);
    const suffix = diff > 0 ? `em ${hours}h ${minutes.toString().padStart(2, "0")}m` : "agora";
    setText("tickerNext", `${next.timeLabel} · ${next.periodLabel} · ${suffix}`);
  }

  async function runPipelineNow() {
    const button = byId("btnRunNow");
    const period = operation().nextWindow?.period || latestRun()?.period || "morning";
    setButtonLoading(button, true);
    try {
      const response = await fetch(`/run-pipeline/${encodeURIComponent(period)}`, { method: "POST", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const payload = await response.json();
      showToast(`Pipeline ${period} iniciado: ${payload.run_id || payload.status || "ok"}.`, "ok");
      await loadData({ quiet: true });
    } catch (error) {
      showToast(`Falha ao iniciar pipeline: ${error.message}`, "danger");
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function runLast24Preview() {
    const button = byId("btn-last24h");
    setButtonLoading(button, true);
    try {
      const response = await fetch("/api/run-pipeline/last-24h", { method: "POST", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const payload = await response.json();
      showToast(`Prévia fiel da mensagem gerada: ${payload.summaryCount || 0} resumo(s).`, "ok");
      await loadData({ quiet: true });
    } catch (error) {
      showToast(`Falha ao gerar prévia 24h: ${error.message}`, "danger");
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function toggleFeed(id) {
    try {
      await fetchJson(`/api/feeds/${encodeURIComponent(id)}/toggle`, { method: "POST" });
    } catch (error) {
      showToast(`Falha ao alternar fonte: ${error.message}`, "danger");
      return;
    }
    showToast("Fonte atualizada.", "ok");
    await loadData({ quiet: true });
  }

  async function toggleSubscriber(id) {
    const response = await fetch(`/api/subscribers/${encodeURIComponent(id)}/toggle`, { method: "POST", headers: { Accept: "application/json" } });
    if (!response.ok) {
      showToast(`Falha ao alternar assinante: ${response.statusText}`, "danger");
      return;
    }
    showToast("Assinante atualizado.", "ok");
    await loadData({ quiet: true });
  }

  function exportCsv() {
    const rows = summaryItems();
    const header = ["categoria", "manchete", "command_hint", "status", "source_article_ids"];
    const csv = [
      header.join(","),
      ...rows.map((row) =>
        [row.categoryLabel, row.title, row.command, row.status, (row.sourceIds || []).join("|")]
          .map((value) => `"${String(value ?? "").replace(/"/g, '""')}"`)
          .join(","),
      ),
    ].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `mesa-dashboard-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  function wireEvents() {
    all("[data-tab]").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.tab)));
    document.addEventListener("click", (event) => {
      const switchTab = event.target.closest("[data-switch-tab]");
      if (switchTab) setTab(switchTab.dataset.switchTab);
      const feed = event.target.closest("[data-toggle-feed]");
      if (feed) toggleFeed(feed.dataset.toggleFeed);
      const subscriber = event.target.closest("[data-toggle-subscriber]");
      if (subscriber) toggleSubscriber(subscriber.dataset.toggleSubscriber);
      const article = event.target.closest("[data-select-article]");
      if (article) {
        state.selectedArticleIndex = Number(article.dataset.selectArticle) || 0;
        renderArticles();
      }
    });
    byId("headlinePrev")?.addEventListener("click", () => {
      const length = summaryItems().length || 1;
      state.headlineIndex = (state.headlineIndex - 1 + length) % length;
      renderHeadlineCarousel();
    });
    byId("headlineNext")?.addEventListener("click", () => {
      const length = summaryItems().length || 1;
      state.headlineIndex = (state.headlineIndex + 1) % length;
      renderHeadlineCarousel();
    });
    byId("btnRunNow")?.addEventListener("click", runPipelineNow);
    byId("btn-last24h")?.addEventListener("click", runLast24Preview);
    byId("btnOpenQueue")?.addEventListener("click", () => setTab("whatsapp"));
    byId("btnExportCsv")?.addEventListener("click", exportCsv);
    byId("btnCommand")?.addEventListener("click", () => byId("navSearch")?.focus());
    byId("btnRefreshPipeline")?.addEventListener("click", () => loadData({ quiet: false }));
    byId("btnRefreshSources")?.addEventListener("click", () => loadData({ quiet: false }));
    byId("btnRefreshSubscribers")?.addEventListener("click", () => loadData({ quiet: false }));
    byId("btnClearEventSearch")?.addEventListener("click", () => {
      state.filters.events = "";
      if (byId("eventSearch")) byId("eventSearch").value = "";
      renderEvents();
    });
    byId("btnCopySummary")?.addEventListener("click", async () => {
      const text = cards()[0]?.summaryText || "";
      if (!text) {
        showToast("Não há resumo para copiar.", "warn");
        return;
      }
      await navigator.clipboard?.writeText(text);
      showToast("Preview do resumo copiado.", "ok");
    });
    byId("navSearch")?.addEventListener("input", (event) => {
      state.filters.nav = event.target.value.toLowerCase();
      all("[data-tab]").forEach((button) => {
        const text = button.textContent.toLowerCase();
        button.hidden = Boolean(state.filters.nav) && !text.includes(state.filters.nav);
      });
    });
    byId("eventSearch")?.addEventListener("input", (event) => {
      state.filters.events = event.target.value;
      renderEvents();
    });
    byId("sourceSearch")?.addEventListener("input", (event) => {
      state.filters.sources = event.target.value;
      renderSources();
    });
    byId("articleSearch")?.addEventListener("input", (event) => {
      state.filters.articles = event.target.value;
      renderArticles();
    });
    byId("summarySearch")?.addEventListener("input", (event) => {
      state.filters.summaries = event.target.value;
      renderSummaries();
    });
  }

  function init() {
    wireEvents();
    setTab("overview");
    loadData({ quiet: true });
    state.refreshTimer = window.setInterval(() => loadData({ quiet: true }), AUTO_REFRESH_MS);
    state.countdownTimer = window.setInterval(updateCountdown, 1000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();