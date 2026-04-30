# NewsBot — Refatoração Completa (Handoff para Próximo Modelo)

## Status: ✅ TODOS OS 5 MILESTONES CONCLUÍDOS
- **199 testes passando** (134 originais + 65 novos)
- `python -m ruff check` limpo
- Nenhum endpoint, formato de digest, ou comportamento operacional alterado

## O que foi feito

### M1: Contratos compartilhados de domínio ✅
**Arquivos criados:**
- `core/__init__.py`
- `core/periods.py` — `VALID_PERIODS`, `VALID_PERIODS_SET`, `is_valid_period()`, `validate_period()`, `period_display_name()`
- `core/whatsapp_identity.py` — `is_group_jid()`, `is_lid_jid()`, `strip_jid_suffix()`, `to_send_jid()`, `canonical_key()`, `destination_priority()`, `is_allowed()`
- `tests/test_periods.py` (16 testes)
- `tests/test_whatsapp_identity.py` (49 testes)

**Arquivos modificados:**
- `app.py` — usa `VALID_PERIODS_SET` e `is_allowed` do core
- `interactions/webhook_handler.py` — usa `strip_jid_suffix()` e `is_group_jid()` do core
- `delivery/whatsapp_sender.py` — usa `canonical_key()`, `destination_priority()`, `is_allowed()`, `to_send_jid()` do core

**Decisão chave:** `canonical_key()` sempre extrai dígitos (inclusive de JIDs de grupo) para preservar o dedup existente onde `120363040996567349@g.us` e `120363040996567349` são o mesmo destino. A allowlist trata grupos com match exato separadamente.

### M2: Separar HTTP/app de regras de negócio ✅
**Arquivos criados:**
- `schemas/__init__.py`, `schemas/webhook.py` — Pydantic models do webhook
- `routers/__init__.py`
- `routers/dashboard.py` — `/`, `/dashboard`, `/api/dashboard`, `/api/whatsapp-status`
- `routers/pipeline.py` — `/api/digest-preview/{period}`, `/run-pipeline/{period}`, `/api/run-pipeline/last-24h`
- `routers/admin_api.py` — subscribers, feeds, analytics, summaries, LLM config, retry delivery
- `routers/webhook.py` — `/webhook/whatsapp` com auth + allowlist

**Arquivos modificados:**
- `app.py` — reduzido de 523 → 108 linhas (composição: lifespan, scheduler, static mount, `/health`, registro de routers)
- `tests/test_webhook.py` — monkeypatches atualizados: `app_module` → `pipeline_module`, `dashboard_module`, `admin_api_module`
- `tests/test_regressions.py` — monkeypatch do pipeline trigger atualizado

**Nota:** `app.py` re-exporta `WhatsAppWebhookPayload` via `from schemas.webhook import WhatsAppWebhookPayload  # noqa: F401` para compatibilidade com testes existentes.

### M3: Refatorar pipeline em step runner ✅
**Arquivos criados:**
- `scheduler/step_runner.py` — `StepResult` dataclass + `execute_step()` + lifecycle helpers (`create_pipeline_run`, `record_pipeline_event`, `update_pipeline_run`, `finish_pipeline_run`, `alert_admin`)

**Arquivos modificados:**
- `scheduler/jobs.py` — pipeline decomposto em funções `_step_*` com contrato `StepResult`. Orquestrador `_run_pipeline_impl` reduzido de ~250 → ~85 linhas. Lock global preservado.

### M4: Melhorar persistência e envio ✅
**Arquivos modificados:**
- `delivery/whatsapp_sender.py`:
  - `_mark_summaries_sent()` — agora usa `UPDATE ... WHERE id IN (...)` em lote ao invés de N `session.get()` individuais
  - `_log_delivery_results()` — agora usa `session.add_all()` para inserção em lote
- `tests/test_whatsapp_sender.py` — mock de `_mark_summaries_sent` adicionado onde necessário

### M5: Limpar LLM/retry sem quebrar comportamento ✅
**Arquivos criados:**
- `core/retry.py` — `RetryPolicy` dataclass + `WHATSAPP_RETRY` e `LLM_RETRY` pré-configurados

**Arquivos modificados:**
- `processor/llm_client.py`:
  - `_chat_async()` → `chat_async()` (público)
  - `_chat_async` mantido como alias para compatibilidade
  - Sync wrappers `chat()` e `chat_json()` documentados como deprecated
  - `chat_json_async()` e internos agora chamam `self.chat_async()` diretamente
- `interactions/question_handler.py` — migrado de `client._chat_async()` para `client.chat_async()`
- `delivery/whatsapp_sender.py` — usa `WHATSAPP_RETRY.max_attempts` e `.backoff_delays`
- `tests/test_llm_client.py` — migrado para `chat_async()`

## Estrutura final do projeto (novos arquivos)

```
newsbot/
├── core/
│   ├── __init__.py
│   ├── periods.py          # Períodos válidos do pipeline
│   ├── retry.py            # Política reutilizável de retry/backoff
│   └── whatsapp_identity.py # Identidade WhatsApp unificada
├── routers/
│   ├── __init__.py
│   ├── admin_api.py        # CRUD admin (subscribers, feeds, LLM, etc)
│   ├── dashboard.py        # Dashboard + status
│   ├── pipeline.py         # Trigger e preview
│   └── webhook.py          # Webhook WhatsApp
├── schemas/
│   ├── __init__.py
│   └── webhook.py          # Pydantic models do webhook
└── scheduler/
    ├── jobs.py             # Orquestrador + steps do pipeline
    └── step_runner.py      # Executor de etapas + lifecycle helpers
```

## Comandos de validação

```bash
# Rodar todos os testes
python -m pytest -q

# Lint
python -m ruff check .

# Resultado esperado: 199 passed, All checks passed
```

## O que NÃO foi alterado (por design)
- Endpoints públicos, payloads do dashboard, formato do digest
- Prompts LLM, definições de feeds, UI/UX
- Comportamento de fallback de digest, placeholders
- Timeouts efetivos (mesmos valores)
- Arquivos em `refatoracao-txts/` (untracked, preservados)

## Próximos passos sugeridos (pós-refatoração)
1. Considerar testes unitários para cada `_step_*` em `scheduler/jobs.py`
2. Considerar wiring programático do `LLM_RETRY` no `_call_with_retry` (hoje documentado, não wired)
3. Remover o alias `_chat_async` após confirmar que nenhum código externo o usa
4. Considerar adicionar testes de integração para o step runner
