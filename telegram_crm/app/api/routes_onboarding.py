"""ONBOARDING — Degraus de Confiança (conversão).

Opção A (Risco Zero): usa o número compartilhado da plataforma
  (conta compartilhada pré-cadastrada). Acesso limitado: só grupos
  públicos e teto de 100 extrações por conta compartilhada.

Opção B (Performance Full): conecta o próprio número via fluxo
  OTP/SessionString (reaproveita /auth/start + /auth/complete e
  /admin/sessions) — acesso total, sem teto.

Upsell: quando a conta compartilhada bate no teto, a API devolve
  HTTP 402 + ``upgrade_required`` e o frontend abre o modal sugerindo
  conectar a conta própria.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.core.serializers import sv
from app.database.database import get_db
from app.database.models import Account, AccountStatus, Lead

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

# Teto da conta compartilhada (Opção A). Sobrescreva via env SHARED_QUOTA.
SHARED_QUOTA = int(os.getenv("SHARED_QUOTA", "100"))
SHARED_TAG = "shared"


class SharedIn(BaseModel):
    group: str = Field(..., description="Link público do grupo (só públicos na Opção A)")
    limit: int = Field(10, ge=1, le=100)


def _shared_account(db: DbSession) -> Account | None:
    shared_phone = os.getenv("SHARED_PHONE", "")
    rows = (
        db.query(Account)
        .filter(Account.status == AccountStatus.ACTIVE)
        .order_by(Account.id)
        .all()
    )
    for acc in rows:
        if (acc.proxy or "").startswith(SHARED_TAG + ":"):
            return acc
    if shared_phone:
        for acc in rows:
            if acc.phone == shared_phone:
                return acc
    return None


def _shared_usage(db: DbSession, account_id: int) -> int:
    return db.query(Lead).filter(Lead.account_id == account_id).count()


def _is_public_group(group: str) -> bool:
    g = (group or "").strip().lower()
    return g.startswith("https://t.me/") and "/+" not in g and "joinchat" not in g


@router.get("/options")
def onboarding_options(db: DbSession = Depends(get_db)) -> dict:
    """Degraus de confiança: descreve as duas opções + uso atual da compartilhada."""
    shared = _shared_account(db)
    used = _shared_usage(db, shared.id) if shared else 0
    return {
        "option_a": {
            "id": "shared",
            "title": "Usar número compartilhado da plataforma",
            "description": "Risco zero. Só grupos públicos, até 100 extrações.",
            "quota": SHARED_QUOTA,
            "used": used,
            "remaining": max(SHARED_QUOTA - used, 0),
            "available": shared is not None,
        },
        "option_b": {
            "id": "own",
            "title": "Conectar meu próprio número",
            "description": "Performance full via OTP/SessionString. Sem teto.",
            "flow": ["POST /auth/start", "POST /auth/complete", "POST /admin/sessions"],
        },
    }


@router.post("/shared/extract", status_code=202)
async def shared_extract(payload: SharedIn, db: DbSession = Depends(get_db)) -> dict:
    """Extração Risco Zero pela conta compartilhada (teto + só públicos)."""
    if not _is_public_group(payload.group):
        raise HTTPException(
            status_code=403,
            detail={"error": "private_group",
                    "message": "A conta compartilhada só acessa grupos públicos. Conecte sua conta (Opção B).",
                    "upgrade_required": True},
        )
    shared = _shared_account(db)
    if shared is None:
        raise HTTPException(
            status_code=409,
            detail={"error": "no_shared_account",
                    "message": "Nenhuma conta compartilhada ativa. Conecte sua conta (Opção B).",
                    "upgrade_required": True},
        )
    used = _shared_usage(db, shared.id)
    if used + payload.limit > SHARED_QUOTA:
        raise HTTPException(
            status_code=402,
            detail={"error": "shared_quota_exceeded",
                    "message": f"Limite da conta compartilhada atingido ({used}/{SHARED_QUOTA}). Conecte seu número para acesso total.",
                    "upgrade_required": True,
                    "used": used, "quota": SHARED_QUOTA},
        )
    from app.workers.harvester import run_harvest_job
    from app.workers.registry import create_job, run_job

    job_id = create_job("harvester", {"group": payload.group, "limit": payload.limit,
                                      "account_id": shared.id, "onboarding": "shared"})
    await run_job(job_id, run_harvest_job)
    return {"job_id": job_id, "status": "accepted", "mode": "shared",
            "account_id": shared.id, "phone": shared.phone,
            "quota": {"used": used, "limit": payload.limit, "quota": SHARED_QUOTA},
            "account": {"id": shared.id, "status": sv(shared.status)}}


__all__ = ["router", "SHARED_QUOTA"]
