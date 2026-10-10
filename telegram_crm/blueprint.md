# 🗺️ Blueprint — Telegram CRM & Engajamento (Plano de Voo)

> **Papel:** Arquiteto & Desenvolvedor Chefe.
> **Metodologia autônoma:** `Implemente → Teste → Documente → Avance`.
> Cada tarefa tem checklist `[ ]` e uma **Definição de Pronto (DoD)** objetiva.
> Marca-se `[x]` apenas após o teste passar e o log de execução ser atualizado.

---

## 0. Visão Geral

**Objetivo:** SaaS de CRM e engajamento para Telegram — múltiplas contas, scraping de
grupos/membros, listas segmentadas, disparos em massa e dashboard.

**Stack (inegociável):**
- Python 3.11+ (alvo 3.14) · **FastAPI** · **SQLAlchemy 2.x** · **Telethon (MTProto)**
- **Supabase (PostgreSQL)** — banco oficial de produção via `DATABASE_URL`
  (connection string do Supabase) · `python-dotenv`
- **SQLite** — apenas fallback de dev local (sem Supabase configurado)
- **StringSession** — zero arquivos `.session` físicos
- **AsyncIO** em todo o fluxo de trabalho (suporte a múltiplas contas)

**Estrutura (mantida):**
```
app/
├── core/       # SessionManager, ClientFactory, rate-limits, helpers Telethon
├── api/        # rotas FastAPI + schemas Pydantic
├── database/   # database.py (engine/env) + models.py (8 tabelas)
├── workers/    # filas e processadores: scrapers, filtros, disparos
└── static/     # interface de controle (dashboard)
```

**Banco (já modelado):** `accounts`, `groups`, `topics`, `members`, `member_lists`,
`list_members` (N:N), `campaigns`, `logs`.

**Fluxo de Pipeline (ordem obrigatória):**
```
Scraper  →  Audit  →  Warmup  →  Conversion (DM / Adder)
(harvest)   (limpa)   (aquece)     (sender.py / adder.py)
```
1. **Scraper** (`workers/harvester.py`): extrai membros de um grupo → upsert em `leads`.
2. **Audit** (`workers/audit.py`): remove duplicados, bots e inválidos → leads `pending` limpos.
3. **Warmup** (`workers/warmup.py`): aquece as contas (anti-ban) antes de converter.
4. **Conversion**: dois caminhos —
   - **DM** (`workers/sender.py`): envia isca com placeholders → lead `sent`.
   - **Member Adder** (`workers/adder.py`): adiciona membros a um grupo-alvo → lead `added`.
   Ambos usam **jitter humano + rotação de contas (failover)** em toda etapa.

---

## 1. Análise Técnica por Domínio

### 1.1 Login / Autenticação (`core` + `api`)
- Fluxo em 2 etapas: `start_login` envia código e guarda `phone_code_hash` (registro
  pendente em memória com TTL 10 min) → `complete_login` valida código/2FA, serializa
  `StringSession` e faz **upsert por phone** em `accounts` (grava `api_id`, `api_hash`).
- `ClientFactory` reconstrói cliente conectado a partir do banco — sem novo login.
- Erros do Telethon traduzidos para `{error, message}` + status HTTP apropriado.
- **Estado: CONCLUÍDO (ver Fase 1).**

### 1.2 Scrapping (`workers/scrapers.py`)
- **Grupos:** listar diálogos → gravar/upsert em `groups` (`group_id`, `group_title`,
  `is_forum`, `last_scraped`).
- **Tópicos:** para fóruns, `messages.GetForumTopicsRequest` → `topics`.
- **Membros:** `iter_participants` com paginação → `members`
  (upsert por `(user_id, source_group_id)`), `last_seen`/`status` quando disponível.
- Respeitar `FloodWaitError` (esperar `e.seconds`), retentativas e idempotência.
- Tudo assíncrono, usando cliente vindo do `ClientFactory`.

### 1.3 Filtragem (`workers/filters.py`)
- Critérios sobre `members`: possui/não `username`, `status`, `last_seen` recência,
  faixa de `user_id`, grupo de origem, pertence/não a outra lista.
- Composição de filtros (AND/OR) → retorna queryset/materializa IDs.
- Resultado pode ser salvo como nova `MemberList` (segmentação).

### 1.4 Gestão de Listas (`api` + `database`)
- CRUD de `member_lists` (criar, listar, renomear, excluir).
- Gerenciar `list_members`: adicionar/remover membros, importar por filtro, contagem.
- Extrair membros de um grupo para uma lista (reaproveita o scraper).

### 1.5 Disparos em Massa (`workers/dispatcher.py`)
- `campaigns` com `message_template` (placeholders `{username}`, `{first_name}`, `{user_id}`),
  `target_list_id` e `account_mode` (`single`/`pool`).
- Fila assíncrona (`asyncio.Queue`) + loop de workers; seleção de conta ativa
  (`status=active`), rate-limit por conta (delay entre mensagens), rotação no modo `pool`.
- Tratar `PeerFloodError` → marca conta `limited`; `UserIsBlockedError`/`ChatWriteForbiddenError`
  → log `failed`; `FloodWaitError` → espera. Cada envio gera linha em `logs`.
- Progresso consultável (sucesso/falta/pendentes).

### 1.5b Member Adder — Adição Forçada (`workers/adder.py`)
- **Input:** leads `pending` (filtrados pelo Audit) + `target_group_id`/link do grupo-alvo.
- **Check de privacidade:** valida o alvo e o membro; erros de privacidade (`UserPrivacyRestricted`,
  `UserNotMutualContact`, `UserBannedInChannel`, `UserAlreadyParticipant`, deletados, etc.)
  são **logados e pulados** — nunca travam a fila.
- **Jitter/Delay humano:** pausa aleatória (`INVITE_MIN..INVITE_MAX`) entre convites,
  `think()` antes do primeiro e `batch_pause()` a cada lote.
- **Rotação de contas (Failover):** `PeerFloodError`/sessão morta → marca a conta
  `limited` no banco e troca automaticamente para a próxima conta ativa, sem
  interromper a campanha.
- **Request por tipo de alvo:** `Channel` → `InviteToChannelRequest`; `Chat` básico
  → `AddChatUserRequest`. Cada convite bem-sucedido marca o lead `added` e grava em `logs`.

### 1.6 Dashboard (`api` + `static`)
- Endpoint de agregação: contas por status, grupos, membros, listas, campanhas,
  taxa de sucesso dos últimos envios, últimas ações de `logs`.
- UI estática (`app/static/index.html`) consumindo a API via `fetch`.

### 1.7 Infra transversal
- Fila de jobs com estado em memória (fase dev) + tabelas `logs`.
- Singleton de rate-limit global e por conta (`core/rate_limit.py`).
- Healthcheck `/health` (já existe) + criação automática de tabelas no startup.

---

## 2. Fases e Tarefas

### FASE 0 — Fundação ✅
- [x] **Tarefa 0.1** — Estrutura de pastas (`core`, `api`, `database`, `workers`, `static`).
  - **DoD:** pastas existem com `__init__.py` e o app importa sem erro.
- [x] **Tarefa 0.2** — `database.py` com engine via env (`DB_HOST/DB_USER/DB_PASS/...`) e `Base`.
  - **DoD:** `get_database_url()` lê `.env`; `DATABASE_URL` sobrescreve; FK ON no SQLite.
- [x] **Tarefa 0.3** — `models.py` com as 8 entidades + relações e constraints.
  - **DoD:** `Base.metadata.create_all` cria 8 tabelas; M2M `list_members` e RESTRICT validados.

### FASE 1 — Autenticação (SessionManager)
- [x] **Tarefa 1.1** — `SessionManager.start_login` + `complete_login` (código + 2FA) com upsert em `accounts`.
  - **DoD:** fluxo start→complete devolve conta `active` com `session_string` persistida no banco.
- [x] **Tarefa 1.2** — `ClientFactory` (StringSession → cliente conectado; proxy; erros claros).
  - **DoD:** cliente reconstruído do banco tem sessão idêntica; string inválida → `LoginError`.
- [x] **Tarefa 1.3** — Endpoints `POST /auth/start`, `POST /auth/complete`, `GET /accounts`.
  - **DoD:** start→200+hash; código errado→400; 2FA sem senha→403; sucesso→200 e conta visível em `GET /accounts`.
- [x] **Tarefa 1.4** — Script de teste dos endpoints (mock Telethon) + registro no log.
  - **DoD:** script `tests/` roda e reporta ALL PASSED; `blueprint.md` atualizado.

### FASE 2 — Scrapping (grupos, tópicos, membros)
- [x] **Tarefa 2.1** — Fila de jobs assíncrona (`workers/registry.py`): enqueue, worker loop, status por job.
  - **DoD:** submeter 1 job processa até o fim; `GET /jobs/{id}` retorna `done` com resultado.
- [x] **Tarefa 2.2** — `workers/harvester.py::harvest` → upsert de participantes em `leads`.
  - **DoD:** mock com 10 participantes → 10 linhas; idempotente; `FloodWaitError` tratado. **Prova: `tests/test_pipeline.py` ALL PASSED (processed=10).**
- [x] **Tarefa 2.3** — `sync_topics(client, group)` → `topics` para fóruns.
  - **DoD:** mock com 2 tópicos grava 2 linhas únicas `(group_id, topic_id)`; 2ª execução mantém 2. **Prova: `tests/test_scrapers.py` (2 linhas únicas + idempotente; grupo não-fórum = 0 linhas sem chamada ao Telegram).**
- [x] **Tarefa 2.4** — Sincronizar `members` (metadados ricos) além de `leads`.
  - **DoD:** gravar `last_seen`/`status` em `members`, separado do pipeline `leads`. **Prova: `tests/test_scrapers.py` (3 membros com `status`/`last_seen`, idempotente; `leads` permanece intocado).**
- [x] **Tarefa 2.5** — Endpoints de scraping (`POST /admin/workers/harvester`, `POST /test_extraction`).
  - **DoD:** POST → 202 com `job_id`; polling até `done`; dados no banco; sem conta ativa → 409.
- [x] **Tarefa 2.6** — Integração com `logs` (ação `harvest`, status, target).
  - **DoD:** após harvesting, `logs` registra a ação com `success`.

### FASE 3 — Filtragem e Gestão de Listas
- [ ] **Tarefa 3.1** — `workers/filters.py`: motor de critérios (`has_username`, `status`, `last_seen_after`, `source_group`, `not_in_list`).
  - **DoD:** unitário: dataset fixo de 6 membros → cada filtro devolve o subconjunto esperado.
- [ ] **Tarefa 3.2** — Aplicar filtro e materializar em `MemberList` (segmentação).
  - **DoD:** `POST /lists/segment` cria lista com os membros filtrados; contagem bate com o filtro.
- [ ] **Tarefa 3.3** — CRUD de listas: `POST/GET/DELETE /lists` (+ rename).
  - **DoD:** criar→201, listar→contém, renomear→200, excluir→204 e linhas de `list_members` removidas.
- [ ] **Tarefa 3.4** — Membros da lista: `GET/POST/DELETE /lists/{id}/members`.
  - **DoD:** adicionar 2 membros → `GET` retorna 2; remover 1 → retorna 1; idempotente (adicionar 2× = 1 linha).

### FASE 4 — Conversão (DM / Member Adder)
- [x] **Tarefa 4.1** — `workers/sender.py`: fila de leads `pending`, render de template (`{first_name}`/`{username}`) e loop com rate-limit.
  - **DoD:** mock com 10 leads → 10 `send_message` e 10 leads `sent` no banco. **Prova: `tests/test_pipeline.py` (sent==10).**
- [x] **Tarefa 4.2** — Tratamento de erros no DM: `PeerFloodError`→ conta `limited`, `FloodWaitError`→ espera, bloqueio→ `failed`.
  - **DoD:** mocks produzem `limited`/`failed` sem derrubar o worker. **Prova: failover testado em `tests/test_final.py`.**
- [ ] **Tarefa 4.3** — Modo `pool` (Campaigns): rotação entre contas ativas + endpoints `/campaigns`.
  - **DoD:** `POST/GET /campaigns`; start→202 com `job_id`; status→`sent/failed/total`; finalizado→`done`.

### FASE 4b — MEMBER ADDER (Adição Forçada) — PRIORIDADE MÁXIMA
- [x] **Tarefa 4b.1** — `workers/adder.py`: itera leads auditados → `InviteToChannelRequest`/`AddChatUserRequest`.
  - **DoD:** 5 convites → 5 leads `added` no banco. **Prova: `tests/test_adder.py`.**
- [x] **Tarefa 4b.2** — Check de privacidade: erros de "usuário não pode ser adicionado" logados e a fila segue.
  - **DoD:** membro barrado vira `failed` + log `skipped`; fila não trava. **Prova: `tests/test_adder.py`.**
- [x] **Tarefa 4b.3** — Jitter humano + rotação de contas (failover): `PeerFloodError` marca `limited` e troca de conta.
  - **DoD:** conta limitada trocada automaticamente; job não interrompido. **Prova: `tests/test_adder.py`.**
- [x] **Tarefa 4b.4** — Endpoint `POST /admin/workers/adder` + `api.startAdder` no frontend.
  - **DoD:** rota registrada (202); client `api.startAdder` chamando `/admin/workers/adder`.

