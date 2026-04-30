# DeepSeek e seleção de provedor LLM pela dashboard

## Contexto

O newsbot usa hoje um cliente LLM centralizado para gerar resumos. A configuração atual vem principalmente do `.env`, com OpenRouter como caminho primário e fallback compatível com OpenAI. A dashboard já concentra controle operacional do pipeline, então ela é o lugar adequado para expor a seleção de provedor/modelo e facilitar a migração para DeepSeek.

## Objetivo

Adicionar uma configuração LLM persistente e editável pela dashboard para permitir usar todas as opções relevantes no momento:

- OpenRouter
- DeepSeek via API oficial
- OpenAI compatível

A escolha deve ser permanente, sobreviver a reinícios do app/Docker e permitir editar API key, base URL e modelo pela dashboard.

## Fora de escopo

- A/B test por execução de pipeline.
- Histórico de custos, tokens ou latência por provedor.
- Criptografia local da API key.
- Permissões granulares além da proteção admin já existente.
- Remover o fallback atual antes de a nova configuração estar validada.

## Arquitetura

A configuração LLM será separada em uma unidade própria, sem misturar estado de provedor/modelo com o fluxo do pipeline.

- `config/settings.py` continua fornecendo defaults a partir do `.env`.
- Um novo serviço/repositório local de configuração mantém a configuração ativa persistida, preferencialmente em `data/llm_config.json`.
- Se não houver configuração persistida, o app usa os defaults atuais do `.env`.
- A configuração persistida sobrescreve os defaults do `.env` para provider, modelo, base URL e chave.
- `processor/llm_client.py` passa a montar o cliente a partir da configuração ativa.
- Ao salvar configuração pela dashboard, o backend invalida o singleton atual do `LLMClient`, garantindo que as próximas sumarizações usem o provider/modelo novos.

## Provedores suportados

### OpenRouter

Usa base URL compatível com OpenAI e modelos OpenRouter. A lista inicial deve incluir modelos DeepSeek via OpenRouter e os modelos já usados hoje pelo projeto.

Modelos sugeridos:

- `deepseek/deepseek-v3.2`
- `deepseek/deepseek-chat`
- modelo Qwen atual do projeto
- fallback OpenAI atual usado via OpenRouter, se aplicável
- campo customizado para qualquer model id válido

### DeepSeek direto

Usa a API oficial da DeepSeek com cliente compatível com OpenAI.

Modelos sugeridos:

- `deepseek-chat`
- `deepseek-reasoner`
- campo customizado

### OpenAI

Mantém suporte compatível com o fallback existente.

Modelos sugeridos:

- `gpt-4o-mini`
- campo customizado

## Endpoints admin

Todos os endpoints novos devem continuar protegidos por `require_admin`.

### `GET /api/llm-config`

Retorna a configuração pública atual:

- provider ativo
- modelo ativo
- base URL ativa
- status de configuração por provider
- opções conhecidas de provider/modelo
- indicação mascarada de API key configurada

Nunca retorna API key em texto claro.

### `POST /api/llm-config`

Salva a configuração LLM persistente.

Valida:

- provider conhecido
- modelo não vazio
- base URL válida e compatível com HTTP/HTTPS
- chave obrigatória quando o provider ativo não tem chave já configurada

Regras para API key:

- Se vier uma chave nova, substitui a existente daquele provider.
- Se vier campo vazio ou valor mascarado, mantém a chave já persistida/configurada.
- Não registra payload completo em logs.

Após salvar:

- persiste em `data/llm_config.json`
- invalida/recria o singleton do `LLMClient`
- retorna a configuração pública atualizada

### `POST /api/llm-config/test`

Testa uma configuração sem necessariamente salvá-la.

Comportamento:

- aceita provider, modelo, base URL e chave opcional
- usa chave enviada ou chave já configurada
- executa uma chamada curta e barata ao provedor
- retorna sucesso/erro objetivo
- não expõe segredo nem stack trace

## Dashboard

Adicionar uma seção operacional de “Configurações LLM”, idealmente em card ou aba dentro da dashboard existente.

Campos:

- Provider ativo: OpenRouter, DeepSeek direto ou OpenAI
- Status de chave por provider: configurado/sem chave
- API key editável e mascarada depois de salva
- Base URL editável, com default por provider
- Modelo com lista conhecida + opção custom

Ações:

- `Testar conexão`: chama `POST /api/llm-config/test` e mostra resultado.
- `Salvar`: chama `POST /api/llm-config` e atualiza a tela com a configuração pública retornada.
- Troca de provider deve atualizar sugestões de base URL e lista de modelos sem apagar a chave já salva de outros providers.

## Fluxo de dados

1. Dashboard carrega `GET /api/llm-config` junto com os dados operacionais.
2. Usuário seleciona provider/modelo e edita base URL/API key quando necessário.
3. Usuário testa conexão opcionalmente.
4. Usuário salva a configuração.
5. Backend valida e persiste a configuração.
6. Backend invalida o cliente LLM em memória.
7. Próximas execuções do pipeline usam a configuração nova.

## Tratamento de erros

- Provider sem chave configurada: dashboard mostra alerta claro e backend retorna 400 ao tentar salvar/usar.
- Teste de conexão com falha: dashboard mostra mensagem curta, sem stack trace e sem segredo.
- Configuração inválida: backend retorna 400 com mensagem objetiva.
- Arquivo persistido corrompido: backend deve ignorar a configuração persistida inválida, usar defaults do `.env` e registrar erro seguro.
- Falha ao salvar arquivo: backend retorna erro 500 objetivo sem expor caminhos sensíveis além do necessário para operação local.

## Segurança operacional

O projeto é local-only no momento, então persistir a API key em arquivo local é aceitável para este ciclo. Ainda assim:

- `data/llm_config.json` não deve ser commitado.
- API keys nunca devem aparecer em respostas da API, logs ou mensagens de erro.
- Endpoints novos devem exigir admin.
- Payloads com segredo não devem ser logados.
- A UI deve mostrar apenas máscara/status da chave após salvar.

## Testes

### Serviço de configuração

- Carrega defaults do `.env` quando não existe arquivo persistido.
- Salva e recarrega provider, modelo e base URL.
- Mantém API key existente quando payload não envia chave nova.
- Substitui API key quando payload envia chave nova.
- Mascara chave em respostas públicas.
- Ignora arquivo corrompido e volta aos defaults com erro seguro.

### Endpoints

- `GET /api/llm-config` exige admin e não vaza chave.
- `POST /api/llm-config` valida provider, modelo e base URL.
- `POST /api/llm-config` invalida o cliente LLM após salvar.
- `POST /api/llm-config/test` simula sucesso e falha sem gravar configuração.

### `LLMClient`

- Monta cliente correto para OpenRouter, DeepSeek direto e OpenAI.
- Usa o modelo ativo da configuração persistida.
- Volta aos defaults quando não existe configuração persistida.
- Falha com mensagem clara quando a chave do provider ativo está ausente.

### Dashboard

- Renderiza provider/modelo atuais.
- Alterna lista de modelos conforme provider.
- Permite modelo customizado.
- Mantém API key mascarada.
- Mostra feedback de teste e salvamento.

## Critérios de aceite

- Usuário consegue selecionar DeepSeek direto pela dashboard, salvar e reiniciar o app sem perder a escolha.
- Usuário consegue editar API key, base URL e modelo pela dashboard.
- OpenRouter, DeepSeek direto e OpenAI ficam disponíveis como opções.
- API key nunca é retornada em texto claro pela API.
- Próxima execução do pipeline usa a configuração salva sem exigir restart manual.
- Testes cobrem configuração, endpoints e montagem do cliente LLM.
