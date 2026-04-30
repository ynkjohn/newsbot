# NewsBot — Documentação de Funcionalidades

## Visão Geral

NewsBot é um sistema automatizado que coleta notícias de feeds RSS, gera resumos via LLM (OpenRouter) e entrega os resumos pelo WhatsApp. O projeto roda 100% via Docker com dois containers: `newsbot` (Python/FastAPI) e `whatsapp-bridge` (Node.js/Baileys).

---

## Arquitetura

```
[Feeds RSS] → [Coletor] → [Banco SQLite] → [Sumarizador LLM] → [WhatsApp Bridge] → [Assinantes]
                                                                        ↑
                                                              [Webhook mensagens recebidas]
```

**Containers:**

- `newsbot` — porta 8000 — API FastAPI + scheduler
- `whatsapp-bridge` — porta 3000 — Baileys (WhatsApp Web)

**Autenticação entre serviços:** defina `WHATSAPP_BRIDGE_TOKEN` no `.env`. O Compose injeta o mesmo valor no `whatsapp-bridge` (cabeçalho `Authorization: Bearer` nas chamadas ao webhook e proteção do `POST /send`) e no `newsbot` (validação do webhook e chamadas ao `/send`). O contentor `newsbot` define `NEWSBOT_USE_ALEMBIC_ONLY=1` para criar/atualizar o schema só via Alembic em vez de `create_all`.

---

## Funcionalidades

### 1. Coleta de Notícias (RSS)

- Busca artigos de feeds RSS configurados a cada execução do pipeline
- Filtra artigos das últimas 12 horas
- Desduplicação por hash de conteúdo
- Extrai conteúdo completo dos artigos via scraping
- Feeds com 3 erros consecutivos são desativados automaticamente

**Feeds ativos por categoria:**
| Categoria | Fontes |
|---|---|
| `politica-brasil` | Poder360 |
| `economia-brasil` | InfoMoney |
| `economia-cripto` | CoinDesk, Cointelegraph |
| `economia-mundao` | BBC World |
| `politica-mundao` | BBC Business |
| `tech` | TechCrunch, The Verge |

---

### 2. Sumarização com LLM

- Agrupa artigos por categoria
- Gera um resumo por categoria via OpenRouter
- Modelo primário: `deepseek/deepseek-chat-v3-0324`
- Modelo fallback: `openai/gpt-4o-mini`
- Resumo inclui: cabeçalho, bullets (3-5 pontos), insight e texto completo
- Salva resumos no banco SQLite com data, categoria e período

---

### 3. Agendamento (Scheduler)

Pipelines executados automaticamente via APScheduler:

| Job                  | Horário   | Descrição                                  |
| -------------------- | --------- | ------------------------------------------ |
| `morning_pipeline`   | 07:00     | Coleta + resume + envia digest matutino    |
| `midday_pipeline`    | 12:00     | Coleta + resume + envia digest do meio-dia |
| `afternoon_pipeline` | 17:00     | Coleta + resume + envia digest vespertino  |
| `evening_pipeline`   | 21:00     | Coleta + resume + envia digest noturno     |
| `cleanup`            | 00:30     | Remove artigos com mais de 7 dias          |
| `feed_health`        | A cada 6h | Verifica feeds inativos e alerta admin     |

Horários configuráveis via `PIPELINE_HOURS` (ex: `7,12,17,21`).

---

### 4. Entrega via WhatsApp

- Envia digest formatado para todos os assinantes ativos
- Rate limiting: 1 mensagem/segundo (configurável via `SEND_RATE_LIMIT`)
- Mensagens longas são divididas automaticamente em partes
- Retry automático em caso de falha
- Registra cada entrega no banco (`DeliveryLog`)
- Permite gerar prévia fiel do digest antes do envio, usando o mesmo formatador e a mesma divisão em partes do WhatsApp

---

### 5. Interação via WhatsApp (Comandos)

Os assinantes podem enviar comandos diretamente pelo WhatsApp:

| Comando                    | Descrição                             |
| -------------------------- | ------------------------------------- |
| `!start` / `!inscrever`    | Inscrever-se para receber o digest    |
| `!stop` / `!sair`          | Cancelar inscrição                    |
| `!hoje`                    | Todos os resumos do dia               |
| `!politica`                | Resumo de política Brasil             |
| `!economia`                | Resumo de economia Brasil             |
| `!cripto`                  | Resumo de criptomoedas                |
| `!mundao` / `!geopolitica` | Resumo de economia e política mundial |
| `!tech`                    | Resumo de tecnologia                  |
| `!help`                    | Lista todos os comandos               |

Além dos comandos, o usuário pode **fazer perguntas em linguagem natural** sobre notícias e o bot responde usando o LLM.

---

### 6. API HTTP (FastAPI)

Endpoints disponíveis em `http://localhost:8000`:

| Método | Endpoint                         | Descrição                                                                  |
| ------ | -------------------------------- | -------------------------------------------------------------------------- |
| `GET`  | `/health`                        | Status do serviço                                                          |
| `GET`  | `/dashboard`                     | Interface web protegida por autenticação admin                             |
| `GET`  | `/api/dashboard`                 | Dados operacionais do dashboard (requer auth)                              |
| `GET`  | `/api/whatsapp-status`           | Status atual da ponte WhatsApp                                             |
| `GET`  | `/api/digest-preview/{period}`   | Prévia fiel do digest por janela (`morning`, `midday`, `afternoon`, `evening`) |
| `POST` | `/run-pipeline/{period}`         | Dispara pipeline manualmente (`morning`, `midday`, `afternoon`, `evening`) |
| `POST` | `/api/run-pipeline/last-24h`     | Compatibilidade do botão de prévia 24h; retorna prévia do digest matutino  |
| `POST` | `/api/retry-delivery/today`      | Reenvia resumos pendentes de hoje                                          |
| `GET`  | `/api/subscribers`               | Lista assinantes                                                           |
| `POST` | `/api/subscribers/{id}/toggle`   | Ativa/desativa assinante                                                   |
| `GET`  | `/api/feeds`                     | Lista feeds RSS                                                            |
| `POST` | `/api/feeds/{id}/toggle`         | Ativa/desativa feed                                                        |
| `GET`  | `/api/analytics`                 | Métricas recentes de volume e uso                                          |
| `POST` | `/api/summaries/{id}/approve`    | Aprova resumo para envio                                                   |
| `POST` | `/webhook/whatsapp`              | Recebe mensagens do whatsapp-bridge                                        |

---

### 7. Dashboard Operacional

O dashboard em `/dashboard` funciona como centro de comando local para acompanhar a operação sem depender de logs manuais.

**Dados exibidos:**

- Saúde geral da operação com explicação das penalidades (`healthBreakdown`)
- Status da WhatsApp Bridge e próxima janela programada
- Contadores de assinantes, resumos do dia, resumos pendentes, falhas de entrega e feeds inativos
- Timeline diária das janelas `morning`, `midday`, `afternoon` e `evening`
- Histórico dos pipelines recentes com duração, métricas de coleta/resumo/envio e eventos por etapa
- Health de feeds (`healthy`, `degraded`, `stale`, `paused`, `broken`)
- Drilldown de falhas recentes de entrega com assinante, resumo e mensagem de erro
- Leitura dos resumos com fontes (`sourceUrls`), status editorial, sentimento, risco, bullets e corpo completo

**Ações disponíveis:**

- Recarregar dados do dashboard
- Alternar tema claro/escuro
- Gerar prévia do WhatsApp antes do envio
- Disparar manualmente uma janela do pipeline
- Reenviar pendências de hoje
- Ativar/desativar assinantes
- Ativar/desativar feeds
- Aprovar resumo para liberação

---

### 8. Flight Recorder do Pipeline

Cada execução do pipeline é registrada em `pipeline_runs` e pode ter eventos detalhados em `pipeline_events`.

**Etapas instrumentadas:**

- `pipeline`
- `fetch_feeds`
- `deduplicate`
- `extract_articles`
- `summarize`
- `delivery`

Cada evento guarda `step`, `status`, `message`, `metadata` e horário de criação. Runs manuais propagam um `run_id` como `requestId` nos eventos para facilitar correlação entre clique no dashboard, logs e histórico.

---

### 9. WhatsApp Bridge (Node.js/Baileys)

Microserviço leve que gerencia a conexão com o WhatsApp Web:

| Método | Endpoint  | Descrição                                                   |
| ------ | --------- | ----------------------------------------------------------- |
| `POST` | `/send`   | Envia mensagem `{ number, text }`                           |
| `GET`  | `/qrcode` | Retorna QR code em base64 para autenticação                 |
| `GET`  | `/status` | Status da conexão (`connected`, `qr_ready`, `disconnected`) |

- Sessão persistida em volume Docker (`whatsapp_session`)
- QR code exibido no terminal na primeira execução
- Reconexão automática em caso de queda

---

## Configuração (.env)

```env
OPENROUTER_API_KEY=       # Chave da API OpenRouter
LLM_MODEL_PRIMARY=        # Modelo principal (ex: deepseek/deepseek-v3.2)
LLM_MODEL_FALLBACK=       # Modelo fallback (ex: openai/gpt-4o-mini)
WHATSAPP_NUMBER=          # Número conectado (55XXXXXXXXXXX)
WHATSAPP_BRIDGE_TOKEN=    # Obrigatório em produção: webhook + POST /send (bridge e newsbot)
ADMIN_PHONE=              # Número para alertas de erro
ADMIN_USERNAME=admin      # Usuário para dashboard/API
ADMIN_PASSWORD=           # Senha para dashboard/API (obrigatório)
PIPELINE_HOURS=7,12,17,21 # Horas dos 4 pipelines (separadas por vírgula)
TIMEZONE=America/Sao_Paulo
SEND_RATE_LIMIT=1.0       # Mensagens por segundo
DATABASE_URL=             # SQLite ou PostgreSQL
ALLOWED_NUMBERS=          # Whitelist de números (vazio = todos)
```

---

## Como Rodar

```bash
# Subir os containers
docker compose up -d --build

# Ver QR code para conectar WhatsApp
docker compose logs whatsapp-bridge -f

# Testar pipeline manualmente
curl -u admin:SUA_SENHA -X POST http://localhost:8000/run-pipeline/morning

# Gerar prévia do digest matutino
curl -u admin:SUA_SENHA http://localhost:8000/api/digest-preview/morning

# Compatibilidade do botão "Prévia 24h"
curl -u admin:SUA_SENHA -X POST http://localhost:8000/api/run-pipeline/last-24h

# Ver logs em tempo real
docker compose logs newsbot -f
```

---

## Banco de Dados (SQLite)

Tabelas principais:

- `feed_sources` — fontes RSS cadastradas
- `news_articles` — artigos coletados e extraídos
- `summaries` — resumos gerados pelo LLM
- `subscribers` — assinantes do digest
- `delivery_logs` — histórico de envios
- `user_interactions` — histórico de comandos recebidos
- `pipeline_runs` — histórico de execuções do pipeline
- `pipeline_events` — eventos por etapa da execução, com status, mensagem e metadata

---

## Problemas Conhecidos e Soluções

| Problema                           | Causa                    | Solução                                  |
| ---------------------------------- | ------------------------ | ---------------------------------------- |
| `DetachedInstanceError` SQLAlchemy | Lazy load fora de sessão | Usar `selectinload` ao buscar artigos    |
| Modelo LLM inválido                | ID errado no `.env`      | Usar `deepseek/deepseek-chat-v3-0324`    |
| Feed com 404/500                   | URL desatualizada        | Atualizar `sources.py` e limpar banco    |
| QR code sumiu                      | Volume apagado           | `docker compose logs whatsapp-bridge -f` |
