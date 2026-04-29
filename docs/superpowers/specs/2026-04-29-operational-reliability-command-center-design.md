# NewsBot Operational Reliability Command Center Design

## Objetivo

Transformar o NewsBot de um pipeline que apenas roda e registra status geral em um centro operacional rastreável, acionável e seguro. A evolução cobre confiabilidade do pipeline, diagnóstico no dashboard, qualidade de preview do digest, envio WhatsApp mais observável e ações manuais com menor risco de duplicidade.

## Escopo ampliado

O foco principal é confiabilidade operacional, mas o desenho inclui pontos de produto que aumentam a utilidade diária:

- Pipeline com rastreabilidade por etapa.
- Dashboard com cards de execução, health de fontes, falhas de entrega e preview de digest.
- WhatsApp com preview fiel, reenvio mais controlado e status mais claro.
- Resumo com contrato estruturado exposto no dashboard para decisão editorial.
- Correções P0 de inconsistências atuais frontend/backend.

Ficam fora deste ciclo: troca de provedor LLM, autenticação multiusuário, deploy público, filas distribuídas e redesign completo visual.

## Problemas atuais observados

1. A dashboard chama `"/api/run-pipeline/last-24h"`, mas o backend expõe `POST /run-pipeline/{period}` apenas para janelas fixas.
2. `_summary_card()` não expõe todos os campos que `renderDrawer()` e `renderReading()` esperam, como `approvalStatus`, `items` e `sentiment`.
3. `todaySummaryCount` conta a janela de leitura de 7 dias, não apenas os resumos de hoje.
4. Falhas de entrega aparecem como contagem, sem drilldown por assinante, resumo ou erro.
5. `PipelineRun` registra status geral, mas não registra etapas, tempos, falhas parciais nem origem da execução.
6. A execução manual retorna um `run_id` de rastreio textual que não é correlacionado de forma persistente com o `PipelineRun` real.
7. O dashboard não mostra claramente se a pipeline está ocupada, aguardando lock, travada em uma fase ou degradada por fonte/bridge.
8. O preview de resumo não garante que o operador veja exatamente o texto que será enviado no WhatsApp.

## Abordagem escolhida

A evolução será incremental e compatível com o runtime local atual.

### P0 — Contratos e bugs visíveis

Corrigir inconsistências existentes sem mudar a arquitetura:

- Remover ou corrigir a ação `last24h` para chamar um endpoint real.
- Expor `approvalStatus`, `items`, `sentiment` e `riskLevel` em `_summary_card()`.
- Corrigir `todaySummaryCount` para contar somente `Summary.date == today`.
- Tornar `sentimentIcon()` visível para `renderDrawer()`.
- Adicionar testes de contrato para evitar regressões.

### P1 — Flight recorder do pipeline

Adicionar persistência mínima de eventos operacionais:

- Nova tabela `pipeline_events` associada a `pipeline_runs`.
- Campos novos em `PipelineRun` para `trigger_id`, `triggered_by`, `requested_at`, `current_step`, `step_index`, `step_count` e `last_message`.
- Funções auxiliares em `scheduler/jobs.py` para registrar início/fim/falha de etapas.
- Payload do dashboard com timeline por run e progresso segmentado.

Etapas padronizadas:

1. `fetch`: coleta RSS.
2. `dedup`: deduplicação e filtro de artigos novos.
3. `extract`: extração de conteúdo completo.
4. `summarize`: sumarização e persistência.
5. `deliver`: envio WhatsApp.

### P1 — Drilldown operacional no dashboard

Adicionar dados úteis sem exigir UI complexa:

- `operation.failedDeliveries.items[]` com assinante, summary, categoria, período, erro e retryability.
- `operation.feedHealth[]` com estado `healthy`, `degraded`, `stale`, `paused` ou `broken`.
- `operation.healthBreakdown[]` explicando penalidades do health score.
- `operation.recentRuns[]` enriquecido com progresso e eventos recentes.

### P1 — Preview fiel do WhatsApp

Criar preview que usa o mesmo formatter do envio:

- Endpoint `GET /api/summaries/{summary_id}/preview` para resumo individual.
- Endpoint `GET /api/digest-preview?period=evening&date=YYYY-MM-DD` para digest de janela.
- Payload inclui texto final, tamanho, número estimado de partes, summaries incluídos e avisos.

### P2 — Ações manuais seguras

Evoluir execução manual sem bloquear uso local:

- Aceitar `requestId` opcional no `POST /run-pipeline/{period}`.
- Persistir `trigger_id` em `PipelineRun`.
- Se já houver run com mesmo `trigger_id`, retornar a run existente.
- Retornar `locked: true` ou `queued: true` quando o pipeline estiver ocupado.
- Adicionar confirmação/cooldown no frontend.

## Modelo de dados

### `pipeline_runs` novos campos

- `trigger_id: str | None`: idempotency/correlation id vindo da execução manual.
- `triggered_by: str`: `scheduler`, `manual`, `retry`.
- `requested_at: datetime | None`: quando a execução foi solicitada.
- `current_step: str | None`: etapa atual ou última etapa executada.
- `step_index: int`: posição atual.
- `step_count: int`: total de etapas.
- `last_message: str | None`: mensagem curta para dashboard.

### Nova tabela `pipeline_events`

- `id`
- `run_id`
- `step`
- `status`: `started`, `completed`, `failed`, `skipped`, `info`
- `message`
- `details` JSON
- `started_at`
- `finished_at`
- `duration_ms`
- `created_at`

Esse modelo evita transformar `PipelineRun` em um blob gigante e permite drilldown progressivo.

## Contratos de API

### `/api/dashboard`

Adicionar em `operation`:

```json
{
  "todaySummaryCount": 4,
  "readingWindowSummaryCount": 18,
  "healthBreakdown": [
    {"label": "Bridge online", "impact": 0},
    {"label": "1 entrega falhou", "impact": -5}
  ],
  "feedHealth": [
    {
      "id": 1,
      "name": "Fonte X",
      "category": "tech",
      "state": "degraded",
      "healthScore": 70,
      "consecutiveErrors": 2,
      "lastError": "Timeout",
      "minutesSinceFetch": 180
    }
  ],
  "failedDeliveries": {
    "count": 2,
    "items": []
  }
}
```

Adicionar em cada recent run:

```json
{
  "id": 10,
  "triggerId": "manual-abc",
  "triggeredBy": "manual",
  "currentStep": "summarize",
  "stepIndex": 4,
  "stepCount": 5,
  "lastMessage": "Gerando resumos",
  "events": []
}
```

Adicionar em cada card de leitura:

```json
{
  "approvalStatus": "draft",
  "sentiment": "mixed",
  "riskLevel": "normal",
  "items": []
}
```

## Dashboard

A dashboard mantém a estrutura atual e ganha melhorias incrementais:

- Cards superiores passam a usar contadores corretos.
- Histórico de runs mostra progresso por etapa quando disponível.
- Alertas de feeds incluem degraded/stale/broken, não só inactive.
- Drawer de resumo mostra itens estruturados e preview WhatsApp.
- Ação `last24h` vira preview de digest/execução sem endpoint quebrado ou é removida até existir implementação real.

## WhatsApp

A confiabilidade de envio melhora por observabilidade antes de mudar transporte:

- Preview usa `delivery/message_formatter.py`.
- Mensagens longas exibem número de partes antes do envio.
- Falhas de `DeliveryLog` aparecem com erro e escopo de retry.
- Próximo ciclo pode adicionar retry individual por `DeliveryLog.id`.

## Resumos

Sem trocar prompt neste ciclo, o dashboard passa a usar melhor o que já existe:

- `items` estruturados de `summary_format.py` e `summarizer.py` aparecem no drawer.
- `sentiment`, `importance_score`, `novelty`, `trust_status` e `command_hint` podem alimentar badges.
- Isso prepara melhorias futuras como ranking editorial, diversity score e digest readiness.

## Testes

Testes obrigatórios:

- Contrato de dashboard para `approvalStatus`, `items`, `sentiment` e contadores.
- Correção da ação `last24h` ou ausência segura dela.
- Payload de health de feeds.
- Drilldown de falhas de entrega.
- Preview de digest usando formatter real.
- Registro de eventos do pipeline.
- Idempotência de `requestId` em execução manual.

## Plano de rollout

1. P0: corrigir contratos e testes sem migração.
2. P1a: adicionar migration/modelos de eventos.
3. P1b: instrumentar pipeline por etapa.
4. P1c: enriquecer dashboard payload e UI.
5. P1d: adicionar preview WhatsApp.
6. P2: ações manuais idempotentes e confirmações.

## Riscos e mitigação

- **Migração local SQLite:** usar Alembic incremental com colunas nullable/defaults seguros.
- **Dashboard quebrar por contrato antigo:** manter fallback JS para campos ausentes.
- **Pipeline ficar mais verboso:** encapsular eventos em helpers pequenos.
- **Escopo crescer demais:** implementar em fases independentes, cada uma testável.

## Critério de sucesso

O operador deve conseguir responder pela dashboard:

- O pipeline está rodando, travado ou finalizou?
- Em qual etapa falhou?
- Quais fontes estão degradadas?
- Quais entregas falharam e para quem?
- O que exatamente será enviado no WhatsApp?
- Uma ação manual duplicada foi evitada?
