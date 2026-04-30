# Plano De Refatoracao NewsBot

## Resumo

Executar a refatoracao em 5 milestones pequenos e verificaveis, preservando comportamento atual de API, dashboard, pipeline agendado, digest manual e WhatsApp. A ordem escolhida reduz risco antes de mexer no nucleo: primeiro contratos/normalizacao, depois extracao de rotas, depois pipeline, depois persistencia/performance, e por fim limpeza de LLM/retry.

## Mudancas Principais

### M1: Contratos compartilhados de dominio

- Criar modulo unico para periodos validos do pipeline: `morning`, `midday`, `afternoon`, `evening`.
- Criar utilitario unico de identidade WhatsApp com funcoes para normalizar JID, detectar grupo, montar destino de envio, gerar chave canonica e aplicar allowlist.
- Substituir usos duplicados em `app.py`, `interactions/webhook_handler.py` e `delivery/whatsapp_sender.py`.
- Manter compatibilidade com `@s.whatsapp.net`, `@g.us`, `@lid`, numero puro e `whatsapp:`.

### M2: Separar HTTP/app de regras de negocio

- Manter `app.py` como composicao: criacao do `FastAPI`, lifespan, scheduler, static mount e registro de routers.
- Extrair rotas administrativas, dashboard, feeds, subscribers, LLM config, pipeline trigger e webhook para routers internos.
- Mover schemas do webhook para modulo de schemas.
- Preservar todos os paths atuais: `/health`, `/dashboard`, `/api/dashboard`, `/api/digest-preview/{period}`, `/run-pipeline/{period}`, `/api/run-pipeline/last-24h`, `/webhook/whatsapp`, etc.

### M3: Refatorar pipeline em step runner

- Transformar `_run_pipeline_impl` em orquestrador curto.
- Criar executor de etapa com contrato comum: `step`, `status`, `message`, `metadata`, `error_log`.
- Extrair etapas: coleta, deduplicacao, extracao/salvamento, sumarizacao, busca de assinantes e entrega.
- Centralizar gravacao de `PipelineEvent`, atualizacao de `PipelineRun`, tratamento de timeout e alerta admin.
- Preservar o lock global e nao disparar pipeline real durante validacao sem confirmacao explicita.

### M4: Melhorar persistencia e envio

- Trocar marcacao de summaries enviadas por update em lote.
- Trocar logs de entrega por insercao em lote por ciclo logico.
- Manter semantica atual: summary so recebe `sent_at` quando houve entrega real; falhas totais nao marcam como enviada.
- Preservar deduplicacao de destinatarios, preferencia por grupo sobre identificador legado e filtros por preferencias do assinante.

### M5: Limpar LLM/retry sem quebrar comportamento

- Expor `chat_async` publico e migrar chamadas externas que hoje usam `_chat_async`.
- Manter wrappers sincronos apenas por compatibilidade, documentados como nao recomendados no runtime async.
- Introduzir politica reutilizavel de retry/backoff para WhatsApp e LLM, sem mudar inicialmente os tempos efetivos.
- Nao alterar comportamento de fallback de digest, placeholders ou knobs ja existentes de digest.

## Interfaces E Contratos

- Novo contrato interno para periodos: uma fonte unica usada por validacao HTTP, scheduler, preview e trigger manual.
- Novo contrato interno para identidade WhatsApp:
  - entrada aceita: JID completo, numero puro, grupo, `@lid`, `whatsapp:`.
  - saida para envio: JID canonico compativel com bridge.
  - chave de comparacao: estavel para allowlist/dedup.
- Novo contrato interno para pipeline steps:
  - cada etapa retorna sucesso com payload ou falha com codigo/mensagem.
  - o executor e responsavel por eventos, status e logs.
- Nenhuma alteracao planejada em endpoints publicos, payloads do dashboard ou formato do digest.

## Testes E Validacao

- Adicionar testes unitarios para identidade WhatsApp cobrindo numero puro, `@s.whatsapp.net`, `@g.us`, `@lid`, `whatsapp:` e allowlist.
- Atualizar testes de webhook para garantir que DM, grupo e JID legado continuam aceitos/ignorados como hoje.
- Atualizar testes de sender para dedup, allowlist, multipart, falha total e marcacao de `sent_at`.
- Adicionar testes do step runner cobrindo sucesso, timeout, falha recuperavel, falha inesperada e gravacao de `PipelineEvent`.
- Atualizar testes de LLM para usar `chat_async` publico sem perder cobertura de retry/fallback.
- Rodar, nessa ordem: testes focados por modulo, `pytest -q`, `ruff check .`; se houver alteracao de dashboard/static ou backend em Docker, rebuild do servico `newsbot`.

## Assumptions

- Implementar em PRs pequenos na ordem M1 -> M5.
- Nao mexer em UI, feeds, categorias ou prompts editoriais nesta refatoracao.
- Nao rodar pipeline manual real como teste automatico, porque pode duplicar envio WhatsApp.
- A pasta `refatoracao-txts/` esta untracked e deve ser preservada; nao incluir em commit salvo pedido explicito.
