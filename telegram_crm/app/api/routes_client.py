"""CLIENT PANEL — API para o cliente solicitar extração, definir isca e ver progresso.

Endpoints:
    POST /client/extractions        → solicita extração de leads (job harvester)
    GET  /client/extractions/{id}   → progresso do job de extração
    PUT  /client/bait               → define a mensagem de isca ativa
    GET  /client/bait               → lê a mensagem de isca ativa
    POST /client/dispatch           → dispara o envio da isca para os leads
    GET  /client/progress           → visão consolidada de leads e jobs
    GET  /client/leads              → lista leads com filtros
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.core.serializers import sv
from app.core.session_manager import LoginError
from app.database.database import get_db
from app.database.models import Lead, LeadStatus
from app.workers.harvester import run_harvest_job
from app.workers.registry import create_job, get_job, run_job
from app.workers.sender import DEFAULT_BAIT, get_bait_message, run_send_job, set_bait_message

router = APIRouter(prefix="/client", tags=["client"])


class ExtractionRequest(BaseModel):
    group: str = Field(..., examples=["https://t.me/s/meu_grupo"])
    limit: int = Field(50, ge=1, le=5000)
    account_id: Optional[int] = None


class BaitRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000,
                         examples=["Oi {first_name}, vi você no grupo..."])


class DispatchRequest(BaseModel):
    limit: int = Field(50, ge=1, le=5000)
    account_id: Optional[int] = None


@router.post("/extractions", status_code=202)
async def request_extraction(payload: ExtractionRequest) -> dict:
    """Cliente solicita extração → job harvester criado e disparado."""
    from app.workers.harvester import pick_account

    try:
        pick_account(payload.account_id)
    except LoginError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc

    job_id = create_job(
        "harvester",
        {"group": payload.group, "limit": payload.limit, "account_id": payload.account_id},
    )
    await run_job(job_id, run_harvest_job)
    return {"job_id": job_id, "status": "accepted", "group": payload.group, "limit": payload.limit}


@router.get("/extractions/{job_id}")
def extraction_progress(job_id: int) -> dict:
    info = get_job(job_id)
    if info is None:
        raise HTTPException(status_code=404, detail={"error": "job_not_found"})
    return info


@router.put("/bait")
def update_bait(payload: BaitRequest) -> dict:
    """Define a mensagem de isca ativa (placeholders: {first_name}, {username})."""
    set_bait_message(payload.message)
    return {"ok": True, "message": get_bait_message()}


@router.get("/bait")
def read_bait() -> dict:
    return {"message": get_bait_message(), "default": DEFAULT_BAIT}


@router.post("/dispatch", status_code=202)
async def dispatch(payload: DispatchRequest) -> dict:
    """Dispara o Mass Sender para os leads pendentes."""
    from app.workers.harvester import pick_account

    try:
        pick_account(payload.account_id)
    except LoginError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc

    job_id = create_job(
        "mass_sender", {"limit": payload.limit, "account_id": payload.account_id}
    )
    await run_job(job_id, run_send_job)
    return {"job_id": job_id, "status": "accepted", "limit": payload.limit}


@router.get("/progress")
def client_progress(db: DbSession = Depends(get_db)) -> dict:
    """Visão consolidada: contagem de leads por status + jobs ativos."""
    counts: dict[str, int] = {s.value: 0 for s in LeadStatus}
    for status_enum in LeadStatus:
        counts[status_enum.value] = (
            db.query(Lead).filter(Lead.status == status_enum).count()
        )
    from app.workers.registry import list_jobs

    active = [j for j in list_jobs(10) if j["status"] in ("pending", "running")]
    return {
        "leads": counts,
        "total_leads": sum(counts.values()),
        "active_jobs": active,
        "bait_message": get_bait_message(),
    }


@router.get("/leads")
def list_leads(
    status: Optional[LeadStatus] = Query(None, description="Filtro por status"),
    group_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: DbSession = Depends(get_db),
) -> dict:
    query = db.query(Lead)
    if status is not None:
        query = query.filter(Lead.status == status)
    if group_id is not None:
        query = query.filter(Lead.source_group_id == group_id)
    total = query.count()
    rows = (
        query.order_by(Lead.id.desc()).offset(offset).limit(limit).all()
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "leads": [
            {
                "id": l.id,
                "user_id": l.user_id,
                "username": l.username,
                "first_name": l.first_name,
                "last_name": l.last_name,
                "status": sv(l.status),
                "source_group_id": l.source_group_id,
                "source_group_title": l.source_group_title,
                "sent_at": l.sent_at.isoformat() if l.sent_at else None,
            }
            for l in rows
        ],
    }

