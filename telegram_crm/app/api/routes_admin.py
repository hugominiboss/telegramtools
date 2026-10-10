"""ADMIN PANEL — API de gestão de contas, sessões e disparo de workers.

Endpoints:
    POST /admin/sessions         → insere SessionString (conta GGMax)
    GET  /admin/accounts         → status das contas (active/banned/limited)
    PATCH /admin/accounts/{id}   → altera status manualmente
    POST /admin/workers/harvester→ dispara o Harvester Worker
    POST /admin/workers/sender   → dispara o Mass Sender Worker
    POST /admin/workers/scraper  → dispara o Scraper de estrutura (groups/topics/members)
    GET  /admin/groups           → grupos sincronizados (+ contagens)
    GET  /admin/groups/{id}      → detalhe do grupo e seus tópicos
    GET  /admin/groups/{id}/members → membros sincronizados (status/last_seen)
    GET  /admin/jobs             → lista jobs + progresso
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.core.proxies import proxy_pool
from app.core.serializers import sv
from app.core.session_manager import LoginError
from app.database.database import get_db
from app.database.models import Account, AccountStatus
from app.workers.harvester import run_harvest_job
from app.workers.registry import create_job, get_job, list_jobs, run_job
from app.workers.sender import run_send_job

router = APIRouter(prefix="/admin", tags=["admin"])


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class SessionIn(BaseModel):
    phone: str = Field(..., examples=["+5511999999999"])
    api_id: int = Field(..., gt=0)
    api_hash: str = Field(..., min_length=8, max_length=64)
    session_string: str = Field(..., min_length=20)
    proxy: Optional[str] = Field(
        None, description="socks5://user:pass@host:port (usa o pool se vazio)"
    )


class AccountStatusIn(BaseModel):
    status: AccountStatus


class HarvesterIn(BaseModel):
    group: str = Field(..., examples=["https://t.me/s/grupo"], description="Link ou id do grupo")
    limit: int = Field(10, ge=1, le=5000)
    account_id: Optional[int] = None


class SenderIn(BaseModel):
    limit: int = Field(10, ge=1, le=5000)
    account_id: Optional[int] = None
    message: Optional[str] = Field(None, description="Sobrescreve a isca ativa")


class ScraperIn(BaseModel):
    group: str = Field(
        ...,
        examples=["https://t.me/s/meu_grupo", "-1001234567890"],
        description="Link ou id do grupo a sincronizar",
    )
    topic_limit: int = Field(100, ge=1, le=1000, description="Teto de tópicos (só fóruns)")
    member_limit: int = Field(500, ge=1, le=10000, description="Teto de membros")
    account_id: Optional[int] = None
    topics: bool = Field(True, description="Sincronizar tópicos do fórum")
    members: bool = Field(True, description="Sincronizar membros (status/last_seen)")


# --------------------------------------------------------------------------
# Sessions / Accounts
# --------------------------------------------------------------------------
@router.post("/sessions", status_code=201)
def create_session(payload: SessionIn, db: DbSession = Depends(get_db)) -> dict:
    """Insere uma SessionString (StringSession) no banco — sem login interativo."""
    existing = db.query(Account).filter(Account.phone == payload.phone).first()
    proxy = payload.proxy or proxy_pool.next_for(f"account:{payload.phone}")

    if existing:
        existing.api_id = payload.api_id
        existing.api_hash = payload.api_hash
        existing.session_string = payload.session_string
        existing.proxy = proxy
        existing.status = AccountStatus.ACTIVE
        account = existing
        created = False
    else:
        account = Account(
            phone=payload.phone,
            api_id=payload.api_id,
            api_hash=payload.api_hash,
            session_string=payload.session_string,
            proxy=proxy,
            status=AccountStatus.ACTIVE,
        )
        db.add(account)
        created = True
    db.commit()
    db.refresh(account)
    return {
        "id": account.id,
        "phone": account.phone,
        "status": sv(account.status),
        "proxy": account.proxy,
        "created": created,
    }


@router.get("/accounts")
def admin_list_accounts(db: DbSession = Depends(get_db)) -> dict:
    """Monitora status das contas (Active / Banned / Limited)."""
    rows = db.query(Account).order_by(Account.id).all()
    return {
        "total": len(rows),
        "accounts": [
            {
                "id": a.id,
                "phone": a.phone,
                "status": sv(a.status),
                "proxy": a.proxy,
                "has_session": bool(a.session_string),
            }
            for a in rows
        ],
    }


@router.patch("/accounts/{account_id}")
def update_account_status(
    account_id: int, payload: AccountStatusIn, db: DbSession = Depends(get_db)
) -> dict:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    account.status = payload.status
    db.commit()
    return {"id": account.id, "phone": account.phone, "status": sv(account.status)}


# --------------------------------------------------------------------------
# Workers (Harvester / Mass Sender / Audit / Warmup)
# --------------------------------------------------------------------------
@router.post("/workers/harvester", status_code=202)
async def start_harvester(payload: HarvesterIn) -> dict:
    """Dispara o Harvester Worker (extração de leads) em background."""
    # Validação síncrona: evita criar job que falharia imediatamente.
    from app.workers.harvester import pick_account

    try:
        pick_account(payload.account_id)
    except LoginError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc

    job_id = create_job("harvester", {"group": payload.group, "limit": payload.limit,
                                      "account_id": payload.account_id})
    await run_job(job_id, run_harvest_job)
    return {"job_id": job_id, "status": "accepted", "group": payload.group, "limit": payload.limit}


class AuditIn(BaseModel):
    limit: int = Field(1000, ge=1, le=10000)
    group_id: Optional[int] = None


class WarmupIn(BaseModel):
    rounds: int = Field(2, ge=1, le=10)
    account_id: Optional[int] = None


class AdderIn(BaseModel):
    target_group: str = Field(
        ...,
        examples=["https://t.me/s/meu_grupo", "-1001234567890"],
        description="Link ou id do grupo/canal-alvo (precisa ser admin para adicionar)",
    )
    limit: int = Field(10, ge=1, le=5000)
    account_id: Optional[int] = None


@router.post("/workers/audit", status_code=202)
async def start_audit(payload: AuditIn) -> dict:
    """Dispara o Audit Worker (valida/limpa leads — Scraper → DB → Audit)."""
    from app.workers.audit import run_audit_job

    job_id = create_job("audit", {"limit": payload.limit, "group_id": payload.group_id})
    await run_job(job_id, run_audit_job)
    return {"job_id": job_id, "status": "accepted", "limit": payload.limit}


@router.post("/workers/warmup", status_code=202)
async def start_warmup(payload: WarmupIn) -> dict:
    """Dispara o Warmup Worker (aquecimento anti-ban das contas)."""
    from app.workers.harvester import pick_account
    from app.workers.warmup import run_warmup_job

    if payload.account_id is not None:
        try:
            pick_account(payload.account_id)
        except LoginError as exc:
            raise HTTPException(status_code=409, detail=exc.to_dict()) from exc

    job_id = create_job("warmup", {"rounds": payload.rounds, "account_id": payload.account_id})
    await run_job(job_id, run_warmup_job)
    return {"job_id": job_id, "status": "accepted", "rounds": payload.rounds}


@router.post("/workers/adder", status_code=202)
async def start_adder(payload: AdderIn) -> dict:
    """Dispara o Member Adder Worker (adição forçada de membros a um grupo)."""
    from app.workers.adder import run_add_job
    from app.workers.harvester import pick_account

    if payload.account_id is not None:
        try:
            pick_account(payload.account_id)
        except LoginError as exc:
            raise HTTPException(status_code=409, detail=exc.to_dict()) from exc

    job_id = create_job(
        "adder",
        {
            "target_group": payload.target_group,
            "limit": payload.limit,
            "account_id": payload.account_id,
        },
    )
    await run_job(job_id, run_add_job)
    return {
        "job_id": job_id,
        "status": "accepted",
        "target_group": payload.target_group,
        "limit": payload.limit,
    }


@router.post("/workers/scraper", status_code=202)
async def start_scraper(payload: ScraperIn) -> dict:
    """Dispara o Scraper de estrutura (groups → topics → members) em background.

    Diferente do Harvester (que alimenta ``leads``), este worker persiste o
    schema relacional rico: ``groups``, ``topics`` (só fóruns) e ``members``
    com ``status``/``last_seen``.
    """
    from app.workers.harvester import pick_account
    from app.workers.scrapers import run_scrape_job

    try:
        pick_account(payload.account_id)
    except LoginError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc

    job_id = create_job(
        "scraper",
        {
            "group": payload.group,
            "topic_limit": payload.topic_limit,
            "member_limit": payload.member_limit,
            "account_id": payload.account_id,
            "topics": payload.topics,
            "members": payload.members,
        },
    )
    await run_job(job_id, run_scrape_job)
    return {
        "job_id": job_id,
        "status": "accepted",
        "group": payload.group,
        "topic_limit": payload.topic_limit,
        "member_limit": payload.member_limit,
    }


# --------------------------------------------------------------------------
# Leitura da estrutura sincronizada (groups / topics / members)
# --------------------------------------------------------------------------
@router.get("/groups")
def admin_list_groups(
    limit: int = 100,
    offset: int = 0,
    db: DbSession = Depends(get_db),
) -> dict:
    """Grupos sincronizados com contagem de tópicos e membros."""
    from app.database.models import Group as GroupModel
    from app.database.models import Member as MemberModel
    from app.database.models import Topic as TopicModel

    query = db.query(GroupModel)
    total = query.count()
    rows = query.order_by(GroupModel.id.desc()).offset(max(offset, 0)).limit(limit).all()

    def _count(model, **criteria: object) -> int:
        return db.query(model).filter_by(**criteria).count()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "groups": [
            {
                "id": g.id,
                "group_id": g.group_id,
                "group_title": g.group_title,
                "is_forum": bool(g.is_forum),
                "last_scraped": g.last_scraped.isoformat() if g.last_scraped else None,
                "topics_count": _count(TopicModel, group_id=g.id),
                "members_count": _count(MemberModel, source_group_id=g.id),
            }
            for g in rows
        ],
    }


@router.get("/groups/{group_row_id}")
def admin_group_detail(group_row_id: int, db: DbSession = Depends(get_db)) -> dict:
    """Detalhe de um grupo interno (PK) + seus tópicos."""
    from app.database.models import Group as GroupModel
    from app.database.models import Member as MemberModel
    from app.database.models import Topic as TopicModel

    group = db.get(GroupModel, group_row_id)
    if group is None:
        raise HTTPException(status_code=404, detail={"error": "group_not_found"})

    topics = (
        db.query(TopicModel)
        .filter(TopicModel.group_id == group.id)
        .order_by(TopicModel.topic_id)
        .all()
    )
    return {
        "id": group.id,
        "group_id": group.group_id,
        "group_title": group.group_title,
        "is_forum": bool(group.is_forum),
        "last_scraped": group.last_scraped.isoformat() if group.last_scraped else None,
        "members_count": db.query(MemberModel)
        .filter(MemberModel.source_group_id == group.id)
        .count(),
        "topics": [
            {"id": t.id, "topic_id": t.topic_id, "topic_name": t.topic_name} for t in topics
        ],
    }


@router.get("/groups/{group_row_id}/members")
def admin_group_members(
    group_row_id: int,
    status: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    db: DbSession = Depends(get_db),
) -> dict:
    """Membros sincronizados de um grupo (filtráveis por ``status``)."""
    from app.database.models import Group as GroupModel
    from app.database.models import Member as MemberModel

    group = db.get(GroupModel, group_row_id)
    if group is None:
        raise HTTPException(status_code=404, detail={"error": "group_not_found"})

    query = db.query(MemberModel).filter(MemberModel.source_group_id == group.id)
    if status:
        query = query.filter(MemberModel.status == status)
    total = query.count()
    rows = query.order_by(MemberModel.id).offset(max(offset, 0)).limit(limit).all()
    return {
        "group_id": group.group_id,
        "group_row_id": group.id,
        "group_title": group.group_title,
        "total": total,
        "limit": limit,
        "offset": offset,
        "members": [
            {
                "id": m.id,
                "user_id": m.user_id,
                "username": m.username,
                "status": m.status,
                "last_seen": m.last_seen.isoformat() if m.last_seen else None,
            }
            for m in rows
        ],
    }


@router.get("/infra")
def infra_status(db: DbSession = Depends(get_db)) -> dict:
    """Saúde da infraestrutura: banco, tabelas BotCashGain, contas e proxies."""
    from sqlalchemy import inspect

    from app.core.proxies import proxy_pool
    from app.database.database import get_engine
    from app.database.models import Job as JobModel, Lead as LeadModel

    insp = inspect(get_engine())
    tables = sorted(set(insp.get_table_names()) | set(insp.get_view_names()))
    required = ["accounts", "sessions", "leads", "jobs"]
    return {
        "database": {"url": str(get_engine().url).split("@")[-1], "tables": tables,
                     "required": {t: (t in tables) for t in required}},
        "accounts": {
            "total": db.query(Account).count(),
            "active": db.query(Account).filter(Account.status == AccountStatus.ACTIVE).count(),
        },
        "leads": {"total": db.query(LeadModel).count()},
        "jobs": {"total": db.query(JobModel).count()},
        "proxies": {"total": len(proxy_pool.proxies)},
    }


@router.post("/workers/sender", status_code=202)
async def start_sender(payload: SenderIn) -> dict:
    """Dispara o Mass Sender Worker (disparo de iscas) em background."""
    from app.workers.harvester import pick_account

    try:
        pick_account(payload.account_id)
    except LoginError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc

    job_id = create_job(
        "mass_sender",
        {"limit": payload.limit, "account_id": payload.account_id, "message": payload.message},
    )
    await run_job(job_id, run_send_job)
    return {"job_id": job_id, "status": "accepted", "limit": payload.limit}


@router.get("/jobs")
def admin_jobs(limit: int = 50) -> dict:
    jobs = list_jobs(limit)
    return {"total": len(jobs), "jobs": jobs}


@router.get("/jobs/{job_id}")
def admin_job(job_id: int) -> dict:
    info = get_job(job_id)
    if info is None:
        raise HTTPException(status_code=404, detail={"error": "job_not_found"})
    return info

