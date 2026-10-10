# BotCashGain v1.0 — Ecossistema completo e operável

SaaS de extração de leads e disparo de iscas no Telegram: onboarding por
degraus de confiança, pipeline Scraper → DB → Audit → DB → Conversion,
failover automático de contas e painel dark com 7 abas.

## Status atual

- Backend: `http://127.0.0.1:8000` — `/health` ok, `/docs` interativo
- Painel: `http://127.0.0.1:5173` — React + Tailwind + Lucide + Toasts
- Banco: SQLite local `botcashgain.db` com `accounts`, `sessions` (view),
  `leads`, `jobs` — verificado via `GET /admin/infra`
- Validação: `tests/test_pipeline.py` → ALL PASSED ·
  `tests/test_final.py` → ALL FINAL CHECKS PASSED
- Progresso: acompanhe em `.current_status.txt` ou `GET /status`

## Stack

- Python 3.11+ · FastAPI · SQLAlchemy 2.x · Telethon (MTProto)
- SQLite local (dev) → PostgreSQL (VPS) via `DATABASE_URL` / `DB_*`
- StringSession — zero arquivos `.session` físicos
- Proxy SOCKS5 rotativo (WebShare) com rotação round-robin por conta

## Instalação

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env   # ajuste se necessário
```

## Como rodar

```powershell
.\.venv\Scripts\python.exe main.py            # 0.0.0.0:8000
.\.venv\Scripts\python.exe main.py --reload   # hot-reload (dev)
```

Docs interativas: <http://127.0.0.1:8000/docs>

## Feedback visual (`.current_status.txt`)

Enquanto workers rodam, o sistema escreve no arquivo `.current_status.txt`
(na raiz do projeto) uma linha com cor ANSI, tarefa atual, % da tarefa,
barra de progresso total e tempo restante estimado.

```powershell
Get-Content .current_status.txt -Wait     # Windows
# ou
tail -f .current_status.txt               # Git Bash / WSL
```

Também é possível consultar via API: `GET /status` (últimas 20 linhas).

## Fluxo rápido (primeiro teste real)

1. **Inserir conta** (Admin):

   ```powershell
   Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8000/admin/sessions `
     -ContentType 'application/json' -Body '{
       "phone": "+5511999999999",
       "api_id": 12345,
       "api_hash": "0123456789abcdef0123456789abcdef",
       "session_string": "<StringSession do my.telegram.org / Telethon>",
       "proxy": null
     }'
   ```

   Opcional: informe `proxy` no formato `socks5://user:pass@host:port`.
   Se omitido, o sistema atribui automaticamente um proxy do pool rotativo.

2. **Verificar contas** (status Active/Banned/Limited):

   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/admin/accounts
   ```

3. **Testar extração ponta-a-ponta** (retorna JSON no navegador/postman):

   ```powershell
   Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8000/test_extraction `
     -ContentType 'application/json' -Body '{"group":"https://t.me/s/seu_grupo","limit":10}'
   ```

4. **Definir a mensagem de isca** (Client):

   ```powershell
   Invoke-RestMethod -Method PUT -Uri http://127.0.0.1:8000/client/bait `
     -ContentType 'application/json' -Body '{
       "message":"E ai {first_name}! Vi seu perfil (@{username}) no grupo."
     }'
   ```

5. **Disparar iscas** para os leads pendentes:

   ```powershell
   Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8000/client/dispatch `
     -ContentType 'application/json' -Body '{"limit":50}'
   ```

6. **Acompanhar progresso**:

   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/client/progress
   Invoke-RestMethod "http://127.0.0.1:8000/client/leads?status=pending&limit=100"
   ```

## Endpoints principais

| Método | Rota | Descrição |
| --- | --- | --- |
| POST | `/auth/start` | Login em 2 etapas (envia código) |
| POST | `/auth/complete` | Conclui login e salva StringSession |
| GET | `/accounts` | Lista contas |
| POST | `/admin/sessions` | Insere SessionString (upsert por phone) |
| GET | `/admin/accounts` | Status das contas (active/banned/limited) |
| PATCH | `/admin/accounts/{id}` | Altera status manualmente |
| POST | `/admin/workers/harvester` | Dispara extração (job em background) |
| POST | `/admin/workers/sender` | Dispara iscas (job em background) |
| GET | `/admin/jobs` | Lista jobs + progresso |
| GET | `/admin/jobs/{id}` | Detalhe do job (inclui resultado) |
| POST | `/client/extractions` | Solicitação de extração |
| GET | `/client/extractions/{id}` | Progresso da extração |
| PUT | `/client/bait` | Define mensagem de isca ativa |
| GET | `/client/bait` | Lê mensagem de isca ativa |
| POST | `/client/dispatch` | Dispara envio para leads pendentes |
| GET | `/client/progress` | Visão consolidada (leads + jobs) |
| GET | `/client/leads` | Lista leads (filtros: status, group_id) |
| POST | `/test_extraction` | Teste ponta-a-ponta (extrai N leads e devolve JSON) |
| GET | `/onboarding/options` | Degraus de confiança (Opção A/B + quota) |
| POST | `/onboarding/shared/extract` | Extração Risco Zero (teto 100, só públicos) |
| POST | `/admin/workers/audit` | Audit Worker (valida/limpa leads) |
| POST | `/admin/workers/warmup` | Warmup Worker (aquecimento anti-ban) |
| GET | `/admin/infra` | Infra: banco, tabelas, contas, proxies |
| GET | `/status` | Últimas linhas do `.current_status.txt` |
| GET | `/health` | Healthcheck da API + banco |

## Onboarding (conversão)

- **Opção A (Risco Zero):** número compartilhado, só grupos públicos, teto
  de 100 extrações (`SHARED_QUOTA`). Ao bater o teto a API devolve HTTP 402
  + `upgrade_required` e o painel abre o modal de upsell.
- **Opção B (Performance Full):** conta própria via OTP
  (`POST /auth/start` → `POST /auth/complete`) ou SessionString
  (`POST /admin/sessions`) — sem teto, com failover.
- Marque uma conta como compartilhada via env `SHARED_PHONE` ou proxy com
  prefixo `shared:` (ex.: cadastre a conta com `"proxy": "shared:default"`).

## Pipeline e failover

Fluxo: Scraper (harvester) → DB → Audit → DB → Conversion (sender).
O `sender` e o `warmup` usam `app/workers/failover.py`: se a Conta_01 for
limitada/banida no meio do job, a Conta_02 assume sem interromper.
Acompanhe a barra real via `GET /admin/jobs/{id}` (`progress_pct`).

## Painel (7 abas)

| Aba | O que faz |
| --- | --- |
| Dashboard | LED de saúde, cards de leads/contas, atividade recente |
| Começar | Onboarding A/B + modal de upsell |
| Contas | Vincular SessionString + tabela de status |
| Scraper | Extração com contador e progresso em tempo real |
| Audit · Warmup | Pipeline intermediário com barras reais |
| Conversion | Isca + disparo com feedback Enviando/Sucesso |
| Infra | Tabelas BotCashGain, contas e proxies |

```powershell
cd frontend
npm install        # já inclui lucide-react
npm run dev        # http://127.0.0.1:5173
```

## Placeholders da isca

`{username}` · `{first_name}` · `{last_name}` · `{user_id}`

## Segurança / anti-ban (mimetismo humano)

- Jitter aleatório entre mensagens (nunca intervalo fixo).
- Pausa longa a cada 15 mensagens (45–120s).
- Digitação simulada proporcional ao tamanho da mensagem.
- `FloodWaitError` → espera o tempo indicado e retenta.
- `PeerFloodError` → conta marcada como `limited` e rotacionada.

## Validação (offline, sem Telegram real)

```powershell
.\.venv\Scripts\python.exe tests\test_pipeline.py
```

Cobre: harvest mockado (10 leads) → idempotência → disparo mockado (10 iscas)
→ progresso persistido. Espera-se `ALL PASSED`.

## Estrutura

```
app/
├── core/        # status, proxies, jitter, session_manager, serializers
├── api/         # routes_auth, routes_admin, routes_client, schemas
├── database/    # database.py (engine/env) + models.py
├── workers/     # registry (jobs), harvester, sender
└── static/      # interface de controle (dashboard)
main.py          # entrypoint uvicorn
tests/           # validação offline do pipeline
```

## Segurança

- `.env` e `*.db` estão no `.gitignore` — nunca commite credenciais.
- Credenciais de proxy no `.env` via variável `PROXIES` (separada por vírgula).
