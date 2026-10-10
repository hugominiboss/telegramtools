"""Ponto de entrada FastAPI do BotCashGain v1.0 — Control Plane."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.routes_admin import router as admin_router
from app.api.routes_auth import accounts_router, router as auth_router
from app.api.routes_client import router as client_router
from app.api.routes_lists import router as lists_router
from app.api.routes_onboarding import router as onboarding_router
from app.core.session_manager import LoginError
from app.core.status import status
from app.database.database import get_engine
from app.database.database import Base  # noqa: F401 — popula metadata dos models
import app.database.models  # noqa: F401 — registra as tabelas no Base

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Provisiona o banco no startup (tabelas + VIEW sessions)."""
    from sqlalchemy import text as _text

    engine = get_engine()
    Base.metadata.create_all(engine)
    if str(engine.url).startswith("sqlite"):
        with engine.begin() as conn:
            conn.execute(_text(
                "CREATE VIEW IF NOT EXISTS sessions AS "
                "SELECT id, phone, api_id, api_hash, session_string, status, "
                "proxy AS proxy_url, created_at FROM accounts"
            ))
    status.start(total_tasks=5, estimate_seconds=5 * 60)
    status.step_done("SERVIDOR ONLINE", 100)
    status.finish()
    yield


app = FastAPI(
    title="BotCashGain v1.0 — Control Plane",
    version="1.0.0",
    description="API assíncrona: contas, proxies, Harvester/Mass Sender workers.",
    lifespan=lifespan,
)

# CORS — permite que o painel React (Vite em :5173) chame a API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rotas de autenticação, admin (contas/workers), client (extração/isca),
# listas/segmentação e onboarding
app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(admin_router)
app.include_router(client_router)
app.include_router(lists_router)
app.include_router(onboarding_router)

# Interface de controle servida em /
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/health")
def health() -> dict:
    """Healthcheck da API + conectividade com o banco."""
    db_ok = False
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:  # noqa: BLE001 — healthcheck nunca pode derrubar a API
        return {"status": "degraded", "database": False, "error": str(exc)}
    return {"status": "ok", "database": db_ok}


# --------------------------------------------------------------------------
# Endpoint de teste ponta-a-ponta: /test_extraction
# --------------------------------------------------------------------------
class TestExtractionIn(BaseModel):
    group: str = Field(
        ...,
        examples=["https://t.me/s/exemplo"],
        description="Link ou id do grupo de teste",
    )
    limit: int = Field(10, ge=1, le=100, description="Padrão: 10 leads")
    account_id: Optional[int] = None
    wait: bool = Field(True, description="Aguarda o job e devolve os leads no JSON")


@app.post("/test_extraction")
async def test_extraction(payload: TestExtractionIn) -> dict:
    """Extrai N leads de um grupo com uma conta de teste e devolve o JSON.

    Fluxo: valida conta ativa → cria job → dispara Harvester → (opcional)
    aguarda conclusão → retorna status + leads extraídos.
    """
    from app.workers.harvester import pick_account, run_harvest_job
    from app.workers.registry import create_job, get_job, run_job

    try:
        pick_account(payload.account_id)
    except LoginError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc

    job_id = create_job(
        "harvester",
        {
            "group": payload.group,
            "limit": payload.limit,
            "account_id": payload.account_id,
        },
    )
    await run_job(job_id, run_harvest_job)

    if not payload.wait:
        return {
            "job_id": job_id,
            "status": "accepted",
            "progress_url": f"/admin/jobs/{job_id}",
        }

    # Aguarda o job finalizar (timeout: 30s fixos + 30s por lead).
    deadline = 30 + payload.limit * 30
    info: dict = {}
    for _ in range(deadline):
        await asyncio.sleep(1)
        info = get_job(job_id) or {}
        if info.get("status") in ("done", "failed", "cancelled"):
            break

    result = (info.get("params") or {}).get("result") or {}
    return {
        "endpoint": "/test_extraction",
        "group": payload.group,
        "requested": payload.limit,
        "job": info,
        "leads": result.get("leads", []),
        "result": result,
    }


@app.get("/status")
def current_status() -> dict:
    """Devolve as últimas linhas do arquivo `.current_status.txt`."""
    try:
        lines = status.path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    return {"file": str(status.path), "last": lines[-20:]}

