window.MockData = {
  dashboard: {
    generatedAt: new Date().toISOString(),
    timezone: "America/Sao_Paulo",
    operation: {
      healthScore: 82,
      todaySummaryCount: 42,
      readingWindowSummaryCount: 48,
      pendingSummaryCount: 3,
      failedDeliveryCount: 1,
      inactiveFeedCount: 2,
      subscriberCount: 12,
      bridge: {
        status: "connected",
        connected: true
      },
      nextWindow: {
        period: "afternoon",
        periodLabel: "Tarde",
        scheduledAt: new Date(Date.now() + 2 * 3600 * 1000).toISOString(),
        timeLabel: "14:00",
        isTomorrow: false
      },
      schedule: [
        { period: "morning", periodLabel: "Manhã", timeLabel: "08:00" },
        { period: "afternoon", periodLabel: "Tarde", timeLabel: "14:00" },
        { period: "night", periodLabel: "Noite", timeLabel: "20:00" }
      ],
      feedHealth: [
        { id: "feed_1", state: "healthy" },
        { id: "feed_2", state: "healthy" },
        { id: "feed_3", state: "healthy" },
        { id: "feed_4", state: "degraded" },
        { id: "feed_5", state: "broken" }
      ],
      recentRuns: [
        {
          id: 104,
          period: "morning",
          periodLabel: "Manhã",
          status: "completed",
          articlesCollected: 120,
          summariesGenerated: 42,
          messagesSent: 12,
          durationSeconds: 145,
          startedAt: new Date(Date.now() - 4 * 3600 * 1000).toISOString(),
          startedAtLabel: "Hoje 08:00",
          errorSnippet: null,
          events: [
            { step: "collect", status: "completed", message: "Coletados 120 artigos", createdAt: new Date(Date.now() - 4 * 3600 * 1000).toISOString() },
            { step: "summarize", status: "completed", message: "42 resumos gerados", createdAt: new Date(Date.now() - 3.9 * 3600 * 1000).toISOString() },
            { step: "delivery", status: "completed", message: "12 mensagens enviadas", createdAt: new Date(Date.now() - 3.8 * 3600 * 1000).toISOString() }
          ]
        },
        {
          id: 103,
          period: "night",
          periodLabel: "Noite",
          status: "failed",
          articlesCollected: 85,
          summariesGenerated: 30,
          messagesSent: 0,
          durationSeconds: 120,
          startedAt: new Date(Date.now() - 16 * 3600 * 1000).toISOString(),
          startedAtLabel: "Ontem 20:00",
          errorSnippet: "Bridge timeout",
          events: [
            { step: "collect", status: "completed", message: "Coletados 85 artigos", createdAt: new Date(Date.now() - 16 * 3600 * 1000).toISOString() },
            { step: "summarize", status: "completed", message: "30 resumos gerados", createdAt: new Date(Date.now() - 15.9 * 3600 * 1000).toISOString() },
            { step: "delivery", status: "failed", message: "Timeout na conexão com o WhatsApp", createdAt: new Date(Date.now() - 15.8 * 3600 * 1000).toISOString() }
          ]
        }
      ],
      timeline: [
        {
          period: "night",
          periodLabel: "Noite",
          status: "failed",
          timeLabel: "Ontem 20:00",
          details: { articlesCollected: 85, summariesGenerated: 30 }
        },
        {
          period: "morning",
          periodLabel: "Manhã",
          status: "completed",
          timeLabel: "Hoje 08:00",
          details: { articlesCollected: 120, summariesGenerated: 42 }
        },
        {
          period: "afternoon",
          periodLabel: "Tarde",
          status: "upcoming",
          timeLabel: "Hoje 14:00",
          details: {}
        }
      ],
      failedDeliveries: {
        items: [
          {
            sentAt: new Date(Date.now() - 15.8 * 3600 * 1000).toISOString(),
            errorMessage: "Timeout na conexão com o WhatsApp (Subscriber +55 11 99999-9999)",
            summaryHeader: "Notícias da Noite"
          }
        ]
      }
    },
    reading: {
      summaryCount: 48,
      readingWindowSummaryCount: 48,
      categoryCounts: {
        "politica-brasil": 12,
        "economia-brasil": 15,
        "tech": 8,
        "economia-mundo": 10,
        "geopolitica": 3
      },
      cards: [
        {
          header: "Reforma Tributária e Impactos",
          category: "economia-brasil",
          categoryLabel: "Economia Brasil",
          approvalStatus: "ready",
          modelUsed: "claude-3-haiku",
          createdAt: new Date().toISOString(),
          createdAtLabel: "Hoje 08:05",
          period: "morning",
          periodLabel: "Manhã",
          summaryText: "O senado aprovou o texto-base da nova reforma tributária com foco na simplificação do IVA. Especialistas apontam que setores de serviços podem ter aumento de carga, enquanto a indústria ganha eficiência. A transição deve durar 7 anos.",
          insight: "O impacto imediato é na percepção de risco para fundos imobiliários, mas a simplificação a longo prazo atrai investimento estrangeiro na indústria de base.",
          sourceUrls: ["https://g1.globo.com/economia/noticia/reforma", "https://valor.globo.com/reforma-tributaria"],
          items: [
            {
              title: "Senado aprova texto-base da reforma",
              commandHint: "reforma_texto",
              trustStatus: "high",
              sourceArticleIds: ["art_101", "art_102"]
            },
            {
              title: "Impacto no setor de serviços",
              commandHint: "reforma_servicos",
              trustStatus: "medium",
              sourceArticleIds: ["art_103"]
            }
          ]
        },
        {
          header: "Avanços em Inteligência Artificial",
          category: "tech",
          categoryLabel: "Tech",
          approvalStatus: "pending",
          modelUsed: "gpt-4o-mini",
          createdAt: new Date(Date.now() - 3600 * 1000).toISOString(),
          createdAtLabel: "Hoje 07:15",
          period: "morning",
          periodLabel: "Manhã",
          summaryText: "Novos modelos de linguagem open-source foram lançados esta semana, desafiando a hegemonia das big techs. O foco agora é em modelos menores (SLMs) que rodam localmente com alta eficiência.",
          insight: "A corrida agora não é por tamanho, mas por latência e privacidade. Modelos locais vão dominar aplicações enterprise B2B em 2026.",
          sourceUrls: ["https://techcrunch.com/open-source-ai", "https://news.ycombinator.com/slm-trend"],
          items: [
            {
              title: "O boom dos SLMs",
              commandHint: "tech_slm",
              trustStatus: "high",
              sourceArticleIds: ["art_201"]
            }
          ]
        }
      ]
    }
  },
  subscribers: [
    {
      id: "sub_1",
      name: "João (Dev)",
      phoneNumber: "+55 11 99999-0001",
      active: true,
      lastSentAt: new Date(Date.now() - 4 * 3600 * 1000).toISOString(),
      preferences: { "tech": true, "economia-brasil": true }
    },
    {
      id: "sub_2",
      name: "Grupo Editorial",
      phoneNumber: "120363000000000000@g.us",
      active: true,
      lastSentAt: new Date(Date.now() - 4 * 3600 * 1000).toISOString(),
      preferences: {}
    },
    {
      id: "sub_3",
      name: "Investidor X",
      phoneNumber: "+55 11 99999-0002",
      active: false,
      lastSentAt: new Date(Date.now() - 48 * 3600 * 1000).toISOString(),
      preferences: { "economia-mundo": true, "geopolitica": true }
    }
  ],
  feeds: [
    { id: "feed_1", name: "Valor Econômico", category: "economia-brasil", url: "https://valor.globo.com/rss", active: true, state: "healthy", healthScore: 98, minutesSinceFetch: 15 },
    { id: "feed_2", name: "G1 Política", category: "politica-brasil", url: "https://g1.globo.com/rss/politica", active: true, state: "healthy", healthScore: 100, minutesSinceFetch: 10 },
    { id: "feed_3", name: "TechCrunch", category: "tech", url: "https://techcrunch.com/feed", active: true, state: "healthy", healthScore: 95, minutesSinceFetch: 20 },
    { id: "feed_4", name: "CNN Geopolítica", category: "geopolitica", url: "https://cnn.com/world/rss", active: true, state: "degraded", healthScore: 60, minutesSinceFetch: 120, lastError: "Timeout parcial" },
    { id: "feed_5", name: "Blog Obscuro", category: "tech", url: "https://blogobscuro.net/rss", active: false, state: "paused", healthScore: 0, minutesSinceFetch: 1440, lastError: "Host unreachable" }
  ],
  analytics: {
    tokensByDate: {
      "2026-04-28": 150000,
      "2026-04-29": 180000,
      "2026-04-30": 165000,
      "2026-05-01": 210000,
      "2026-05-02": 190000,
      "2026-05-03": 85000
    }
  },
  llmConfig: {
    model: "claude-3-haiku",
    provider: "anthropic",
    base_url: null
  }
};