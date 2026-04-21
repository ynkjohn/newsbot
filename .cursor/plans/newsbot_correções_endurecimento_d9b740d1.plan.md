---
name: NewsBot correções endurecimento
overview: Plano focado em correções críticas de integração, consistência de dados/configuração, alinhamento DB/migrações e expansão de testes — sem novas features de produto, conforme a sua priorização.
todos:
  - id: p0-webhook-bearer
    content: Corrigir whatsapp-bridge + docker-compose para enviar Bearer; alinhar env do token; teste de webhook 401/200
    status: completed
  - id: p1-llm-singleton
    content: Corrigir get_llm_client / lock e docstring para construção segura
    status: completed
  - id: p1-source-article-ids
    content: Alinhar tipo e uso de source_article_ids (modelo + question_handler + summarizer)
    status: completed
  - id: p1-dashboard-hours
    content: nextSend e mensagens de subscribe baseados em settings.pipeline_hours
    status: completed
  - id: p1-alembic-strategy
    content: Definir estratégia única create_all vs alembic upgrade; ajustar init/entrypoint e CI
    status: completed
  - id: p2-async-sender
    content: (Opcional) Reduzir bloqueio do event loop no envio WhatsApp (httpx async ou to_thread)
    status: completed
  - id: p2-bridge-send-auth
    content: (Opcional) Autenticar POST /send no whatsapp-bridge
    status: completed
  - id: tests-hardening
    content: Cobrir parse_message, format_digest (4 períodos + vazio), webhook Bearer
    status: completed
  - id: docs-sync
    content: Atualizar FUNCIONALIDADES.md e PLANO_CORRECOES.md com estado e novo bug
    status: completed
isProject: false
---

# Plano: correções e endurecimento do NewsBot

## Contexto e estado do código

O ficheiro [PLANO_CORRECOES.md](c:\Users\joaop\projects\newsbot\PLANO_CORRECOES.md) descreve uma revisão anterior; vários itens já estão **obsoletos ou corrigidos** no código atual:

- **BUG-03 (`key_takeaways`)**: [processor/summarizer.py](c:\Users\joaop\projects\newsbot\processor\summarizer.py) grava `{"bullets", "insight}`; [app.py](c:\Users\joaop\projects\newsbot\app.py) (linhas 185–187) já trata `dict` e compatibilidade com `list`.
- **DEBT-02**: O sumarizador usa `client.chat_json_async()` (API pública) em [processor/summarizer.py](c:\Users\joaop\projects\newsbot\processor\summarizer.py) linha 87.
- **DEBT-05**: Mensagens de subscrição em [interactions/subscriber_manager.py](c:\Users\joaop\projects\newsbot\interactions\subscriber_manager.py) já referem 4 envios e `!geopolitica` (horários nos textos ainda são literais 8h/12h/17h/21h vs `PIPELINE_HOURS` — ver abaixo).
- **Testes**: Existe pasta [tests/](c:\Users\joaop\projects\newsbot\tests) com vários ficheiros; o plano antigo que dizia `tests/` vazio está desatualizado.

```mermaid
Alvo
"JSON sem Bearer"
"401 Unauthorized"
"Authorization: Bearer token"
flowchart LR
  subgraph hoje [Problema atual]
    Bridge[whatsapp-bridge fetch]
    Newsbot[POST /webhook/whatsapp]
    Bridge -->|"JSON sem Bearer"| Newsbot
    Newsbot -->|"401 Unauthorized"| Bridge
  end
  subgraph alvo [Alvo]
    Bridge2[whatsapp-bridge]
    Newsbot2[newsbot]
    Bridge2 -->|"Authorization: Bearer token"| Newsbot2
    Newsbot2 -->|"200 queued"| Bridge2
  end
```



---

## P0 — Integração webhook (bloqueante)

**Problema:** [app.py](c:\Users\joaop\projects\newsbot\app.py) exige cabeçalho `Authorization: Bearer <WHATSAPP_BRIDGE_TOKEN>`. [whatsapp-bridge/index.js](c:\Users\joaop\projects\newsbot\whatsapp-bridge\index.js) (linhas 155–159) envia apenas `Content-Type: application/json`. Com token configurado no `newsbot`, **todas as mensagens recebidas falham com 401**. Se o token estiver vazio, o endpoint devolve **500** (“misconfigured”), pelo que o fluxo também não funciona sem decisão explícita.

**Correção recomendada (mínima e coerente):**

1. Adicionar variável de ambiente no bridge, por exemplo `WEBHOOK_AUTH_TOKEN` (ou reutilizar o mesmo nome `WHATSAPP_BRIDGE_TOKEN`), lida em `process.env`.
2. No `fetch` para `WEBHOOK_URL`, incluir `headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token }` quando o token estiver definido.
3. Em [docker-compose.yml](c:\Users\joaop\projects\newsbot\docker-compose.yml), passar o **mesmo** valor para `newsbot` (já via `env_file: .env`) e para `whatsapp-bridge` (`environment:`), para o container do Node receber o token sem duplicar segredos em ficheiros diferentes (ou documentar `env_file` também no serviço bridge).

**Teste:** Estender [tests/test_webhook.py](c:\Users\joaop\projects\newsbot\tests\test_webhook.py) (ou criar caso) que com token configurado, pedido **sem** Bearer recebe 401 e **com** Bearer válido prossegue (mock de `handle_incoming_message` se necessário).

---

## P1 — Consistência, configuração e modelo de dados


| Item                                    | Onde                                                                                                                                               | O quê                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Lock do singleton LLM**               | [processor/llm_client.py](c:\Users\joaop\projects\newsbot\processor\llm_client.py)                                                                 | Existe `_client_lock` mas `get_llm_client()` não o usa; o docstring fala em thread-safe. Em arranque async concorrente, pode criar-se mais do que uma instância. **Correção:** `async def get_llm_client()` com `async with _client_lock` ou inicialização lazy segura alinhada a quem chama (hoje é só sync `get_llm_client()` — avaliar `asyncio.Lock` apenas se houver `await` no caminho, ou usar `threading.Lock` para construção sync). |
| **Tipo `source_article_ids`**           | [db/models.py](c:\Users\joaop\projects\newsbot\db\models.py) vs [processor/summarizer.py](c:\Users\joaop\projects\newsbot\processor\summarizer.py) | Modelo declara `Mapped[dict]` mas persiste-se uma **lista** de IDs. **Correção:** alterar anotação para `list[int]` (e JSON compatível) ou normalizar para dict estável (`{"ids": [...]}`) e ajustar [interactions/question_handler.py](c:\Users\joaop\projects\newsbot\interactions\question_handler.py) linha 127. Preferir **lista + tipo honesto** para menos mudanças.                                                                   |
| `**nextSend` no dashboard**             | [app.py](c:\Users\joaop\projects\newsbot\app.py) linha 179                                                                                         | Valor fixo `"08:00 / 17:00"` não reflete os 4 pipelines nem `settings.pipeline_hours`. **Correção:** construir string a partir de `settings.pipeline_hours` e `settings.timezone` (ou expor lista estruturada para o [static/dashboard.html](c:\Users\joaop\projects\newsbot\static\dashboard.html) formatar).                                                                                                                                |
| **Textos de subscrição vs horas reais** | [interactions/subscriber_manager.py](c:\Users\joaop\projects\newsbot\interactions\subscriber_manager.py)                                           | Horários hardcoded (8h, 12h, …) divergem do default `7,12,17,21` em [config/settings.py](c:\Users\joaop\projects\newsbot\config\settings.py). **Correção:** interpolar a partir de `settings.pipeline_hours` (parse igual ao do scheduler) para uma única fonte de verdade.                                                                                                                                                                   |


---

## P1 — Base de dados: `create_all` vs Alembic

Hoje [db/engine.py](c:\Users\joaop\projects\newsbot\db\engine.py) `init_db()` usa `Base.metadata.create_all`, enquanto existem revisões em [alembic/versions/](c:\Users\joaop\projects\newsbot\alembic\versions) (`0001`, `0002`). Isto gera **dois caminhos de schema** (risco de índices/migrations aplicados só num deles).

**Endurecimento recomendado (escolher uma estratégia e documentar no README interno ou comentário em `init_db`):**

- **Opção A (simples dev):** Manter `create_all` apenas em desenvolvimento; em Docker/prod chamar `alembic upgrade head` no entrypoint ou Dockerfile antes de `python app.py`.
- **Opção B:** Remover `create_all` e passar a **só** Alembic no startup (exige garantir que DB vazio aplica `0001`+`0002`).

Incluir verificação CI: `alembic check` ou `alembic upgrade head` sobre SQLite temporário para garantir que as migrations aplicam.

---

## P2 — Endurecimento adicional (sem “features” de produto)

- `**delivery/whatsapp_sender.py`:** `_send_whatsapp_message` usa `requests` síncrono com `time.sleep` — bloqueia o event loop do FastAPI quando chamado a partir de corrotinas. Plano: migrar envio para **httpx AsyncClient** (ou `asyncio.to_thread` em volta do post existente) de forma incremental, mantendo a mesma semântica de retry.
- **Segurança da bridge:** O endpoint `POST /send` em [whatsapp-bridge/index.js](c:\Users\joaop\projects\newsbot\whatsapp-bridge\index.js) está **sem autenticação** (rede Docker apenas). Endurecimento: header partilhado (mesmo token ou outro) validado no Express antes de `sendMessage`.
- `**processor/llm_client.py` — `_call_with_retry`:** Erros `APIConnectionError` / `APIStatusError` que **não** são 5xx caem no ramo que faz `raise` sem retry; para falhas transitórias de rede pode fazer sentido alinhar com a política de retry do WhatsApp (documentar ou alargar retry só para erros sem `status_code`).

---

## Testes (expandir cobertura mínima)

Já existem testes em [tests/](c:\Users\joaop\projects\newsbot\tests); alinhar com a checklist do [PLANO_CORRECOES.md](c:\Users\joaop\projects\newsbot\PLANO_CORRECOES.md) onde ainda faltar:

- `**parse_message`:** comandos conhecidos/desconhecidos, grupo vs DM, string vazia — ficheiro dedicado ou extensão de testes do router.
- `**format_digest`:** os quatro períodos + lista vazia ([delivery/message_formatter.py](c:\Users\joaop\projects\newsbot\delivery\message_formatter.py)).
- **Webhook:** Bearer obrigatório após correção P0.

Correr `pytest` com dependências `dev` em [pyproject.toml](c:\Users\joaop\projects\newsbot\pyproject.toml) no CI ou localmente após alterações.

---

## Documentação (ajustes pontuais)

- Atualizar [FUNCIONALIDADES.md](c:\Users\joaop\projects\newsbot\FUNCIONALIDADES.md) secção Docker: variável do token também no serviço `whatsapp-bridge`.
- Atualizar [PLANO_CORRECOES.md](c:\Users\joaop\projects\newsbot\PLANO_CORRECOES.md): marcar itens já resolvidos e acrescentar **BUG webhook Bearer / bridge** como novo achado crítico.

---

## Ordem de execução sugerida

1. P0 webhook (bridge + compose + teste).
2. P1 lock LLM + `source_article_ids` + dashboard `nextSend` + textos de horário.
3. Decisão Alembic vs `create_all` + script/entrypoint.
4. P2 e testes extra conforme tempo.

Nada disto adiciona funcionalidades de utilizador novas; apenas corrige rotas quebradas, alinha tipos/config e aumenta garantias automáticas.