"""Harvester Worker — extração de leads (membros) de um grupo.

Fluxo:
  1. Seleciona uma conta ``active`` (rotação de proxy automática).
  2. Conecta via ``ClientFactory`` (StringSession, sem arquivo .session).
  3. Itera ``iter_participants`` com ``FloodWaitError`` respeitado.
  4. Persiste cada lead de forma idempotente (upsert por user_id+grupo).
  5. Atualiza ``jobs.processed`` em tempo real (progresso visível na API).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from telethon.errors import ChannelPrivateError, ChatAdminRequiredError, FloodWaitError
from telethon.tl.types import User

from app.core.jitter import human_pause
from app.core.proxies import proxy_pool
from app.core.serializers import sv
from app.core.session_manager import ClientFactory, LoginError
from app.core.status import status
from app.database.database import get_session_factory
from app.database.models import Account, AccountStatus, Job, Lead, Log
from app.workers.registry import _set_job

logger = logging.getLogger(__name__)

# Respiro entre leituras de participantes (jitter humano).
PAGE_PAUSE = (0.4, 1.6)


def pick_account(preferred_id: Optional[int] = None) -> Account:
    """Escolhe uma conta ativa (e libera o objeto da sessão ORM)."""
    Session = get_session_factory()
    session = Session()
    try:
        query = session.query(Account).filter(Account.status == AccountStatus.ACTIVE)
        account = (
            query.filter(Account.id == preferred_id).first()
            if preferred_id
            else query.order_by(Account.id).first()
        )
        if account is None:
            raise LoginError(
                "no_active_account",
                "Nenhuma conta ativa cadastrada. Insira uma SessionString via /admin/sessions.",
            )
        session.expunge(account)
        return account
    finally:
        session.close()


def ensure_proxy(account: Account) -> str:
    """Garante que a conta tenha um proxy do pool (rotação round-robin)."""
    if not account.proxy:
        account.proxy = proxy_pool.next_for(f"account:{account.phone}")
        Session = get_session_factory()
        session = Session()
        try:
            row = session.get(Account, account.id)
            if row is not None:
                row.proxy = account.proxy
                session.commit()
        finally:
            session.close()
    return account.proxy


def _upsert_lead(
    user: User,
    *,
    group_id: Optional[int],
    group_title: Optional[str],
    job_id: Optional[int],
    account_id: Optional[int],
) -> bool:
    """Grava o lead se ainda não existir para esse grupo. True = novo."""
    Session = get_session_factory()
    session = Session()
    try:
        existing = (
            session.query(Lead)
            .filter(Lead.user_id == user.id, Lead.source_group_id == group_id)
            .first()
        )
        if existing is not None:
            if not existing.username and getattr(user, "username", None):
                existing.username = user.username
            session.commit()
            return False
        session.add(
            Lead(
                user_id=user.id,
                username=getattr(user, "username", None),
                first_name=getattr(user, "first_name", None),
                last_name=getattr(user, "last_name", None),
                phone=getattr(user, "phone", None),
                source_group_id=group_id,
                source_group_title=group_title,
                job_id=job_id,
                account_id=account_id,
            )
        )
        session.commit()
        return True
    finally:
        session.close()


def _log(account_id: Optional[int], action: str, target_id: Optional[int], state: str) -> None:
    Session = get_session_factory()
    session = Session()
    try:
        session.add(Log(account_id=account_id, action=action, target_id=target_id, status=state))
        session.commit()
    finally:
        session.close()
async def harvest(
    job_id: int,
    group_ref: str,
    limit: int = 10,
    account_id: Optional[int] = None,
) -> dict:
    """Extrai até ``limit`` leads do grupo ``group_ref`` (link ou id)."""
    account = pick_account(account_id)
    ensure_proxy(account)

    _set_job(job_id, total=limit, processed=0, error=None)
    status.task(f"HARVESTER: conectando conta {account.phone}", task_pct=5)

    client = ClientFactory.from_session_string(
        account.session_string, account.api_id, account.api_hash, account.proxy
    )

    captured: list[dict] = []
    group_id: Optional[int] = None
    group_title: Optional[str] = None

    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise LoginError(
                "session_invalid",
                f"Sessão da conta {account.phone} inválida/revogada.",
            )

        status.task(f"HARVESTER: resolvendo grupo {group_ref}", task_pct=15)
        entity = await client.get_entity(group_ref)
        group_id = getattr(entity, "id", None)
        group_title = getattr(entity, "title", None) or getattr(entity, "first_name", None)

        processed = 0
        status.task(f"HARVESTER: extraindo leads de {group_title or group_ref}", task_pct=30)

        async for user in client.iter_participants(entity, limit=limit):
            if not isinstance(user, User) or getattr(user, "bot", False):
                continue
            if getattr(user, "self", False) or getattr(user, "deleted", False):
                continue
            is_new = await asyncio.to_thread(
                _upsert_lead,
                user,
                group_id=group_id,
                group_title=group_title,
                job_id=job_id,
                account_id=account.id,
            )
            captured.append(
                {
                    "user_id": user.id,
                    "username": getattr(user, "username", None),
                    "first_name": getattr(user, "first_name", None),
                    "last_name": getattr(user, "last_name", None),
                    "is_new": is_new,
                }
            )
            processed += 1
            _set_job(job_id, processed=processed)
            pct = min(95, 30 + int(processed * 65 / max(limit, 1)))
            status.task(f"HARVESTER: {processed}/{limit} leads", task_pct=pct)
            await human_pause(*PAGE_PAUSE, key=f"harvest:{account.phone}")

        _log(account.id, "harvest_leads", group_id, "success")
        _set_job(job_id, processed=len(captured), total=max(limit, len(captured)))
        status.step_done("HARVESTER: extração concluída", 100)
        return {
            "job_id": job_id,
            "group_id": group_id,
            "group_title": group_title,
            "requested": limit,
            "extracted": len(captured),
            "leads": captured,
        }
    except FloodWaitError as exc:
        # Respeita o limite do Telegram e tenta de novo após a espera.
        logger.warning("FloodWait de %ss no harvester", exc.seconds)
        _set_job(job_id, error=f"FloodWait {exc.seconds}s — aguardando")
        _log(account.id, "harvest_leads", group_id, "failed")
        await asyncio.sleep(min(exc.seconds, 60))
        raise
    except (ChatAdminRequiredError, ChannelPrivateError) as exc:
        _log(account.id, "harvest_leads", group_id, "failed")
        _set_job(job_id, error=str(exc))
        raise LoginError(
            "group_unavailable",
            f"Sem acesso ao grupo {group_ref}: {exc.__class__.__name__}",
        ) from exc
    finally:
        if client.is_connected():
            await client.disconnect()


async def run_harvest_job(job_id: int) -> None:
    """Entrada assíncrona registrada em ``registry.run_job``."""
    from app.workers.registry import get_job

    info = get_job(job_id) or {}
    params = info.get("params", {})
    result = await harvest(
        job_id=job_id,
        group_ref=params.get("group") or "",
        limit=int(params.get("limit", 10)),
        account_id=params.get("account_id"),
    )
    # Persiste o resultado completo no parâmetro (visível via GET /jobs/{id}).
    Session = get_session_factory()
    session = Session()
    try:
        job = session.get(Job, job_id)
        if job is not None:
            merged = json.loads(job.params or "{}")
            merged["result"] = result
            job.params = json.dumps(merged, ensure_ascii=False)
            session.commit()
    finally:
        session.close()


__all__ = ["harvest", "run_harvest_job", "pick_account", "ensure_proxy"]

