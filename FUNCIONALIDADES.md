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

| Método | Endpoint                 | Descrição                                                                  |
| ------ | ------------------------ | -------------------------------------------------------------------------- |
| `GET`  | `/health`                | Status do serviço                                                          |
| `GET`  | `/api/dashboard`         | Dados do dashboard (requer auth)                                           |
| `POST` | `/run-pipeline/{period}` | Dispara pipeline manualmente (`morning`, `midday`, `afternoon`, `evening`) |
| `POST` | `/webhook/whatsapp`      | Recebe mensagens do whatsapp-bridge                                        |

---

### 7. WhatsApp Bridge (Node.js/Baileys)

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
curl -X POST http://localhost:8000/run-pipeline/morning

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

---

## Problemas Conhecidos e Soluções

| Problema                           | Causa                    | Solução                                  |
| ---------------------------------- | ------------------------ | ---------------------------------------- |
| `DetachedInstanceError` SQLAlchemy | Lazy load fora de sessão | Usar `selectinload` ao buscar artigos    |
| Modelo LLM inválido                | ID errado no `.env`      | Usar `deepseek/deepseek-chat-v3-0324`    |
| Feed com 404/500                   | URL desatualizada        | Atualizar `sources.py` e limpar banco    |
| QR code sumiu                      | Volume apagado           | `docker compose logs whatsapp-bridge -f` |
