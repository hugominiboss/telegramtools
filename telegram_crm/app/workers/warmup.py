"""Warmup Worker — aquecimento anti-ban das contas.

Ações leves e humanas (GetMe + leitura de diálogos) com jitter, em
round-robin sobre as contas ativas. Conta que falhar é marcada via
failover sem interromper o job. Sem envio de mensagens.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.core.jitter import human_pause
from app.core.session_manager import ClientFactory, LoginError
from app.core.status import status
from app.database.models import AccountStatus, Log
from app.database.database import get_session_factory
from app.workers.failover import is_recoverable_account_error, list_active_accounts, mark_account
from app.workers.harvester import ensure_proxy
from app.workers.registry import _set_job

logger = logging.getLogger(__name__)


def _log(account_id: Optional[int], action: str, ok: bool) -> None:
    Session = get_session_factory()
    session = Session()
    try:
        session.add(Log(account_id=account_id, action=action,
                        status="success" if ok else "failed"))
        session.commit()
    finally:
        session.close()


async def warmup(job_id: int, rounds: int = 2,
                 account_id: Optional[int] = None) -> dict:
    from app.workers.harvester import pick_account

    if account_id:
        accounts = [pick_account(account_id)]
    else:
        accounts = list_active_accounts()
    if not accounts:
        raise LoginError("no_active_account", "Nenhuma conta ativa para warmup.")
    for account in accounts:
        ensure_proxy(account)

    total = len(accounts) * max(rounds, 1)
    _set_job(job_id, total=total, processed=0, failed=0, error=None)
    status.task(f"WARMUP: aquecendo {len(accounts)} conta(s)", task_pct=10)

    done = 0
    failed = 0
    dead: set[int] = set()
    for rnd in range(max(rounds, 1)):
        for account in list(accounts):
            if account.id in dead:
                continue
            client = ClientFactory.from_account(account)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    raise LoginError("session_invalid", f"Sessão inválida: {account.phone}")
                me = await client.get_me()
                # Leitura leve de diálogos (não envia nada).
                async for _dlg in client.iter_dialogs(limit=5):
                    break
                _log(account.id, f"warmup_round{rnd + 1}:{getattr(me, 'id', '?')}", True)
                done += 1
                status.task(f"WARMUP: conta {account.phone} ok ({done}/{total})",
                            task_pct=min(95, 10 + int(done * 85 / max(total, 1))))
                await human_pause(1.5, 5.0, key=f"warmup:{account.phone}")
            except Exception as exc:  # noqa: BLE001 — failover, nunca mata o job
                failed += 1
                _log(account.id, "warmup", False)
                if is_recoverable_account_error(exc):
                    mark_account(account.id, AccountStatus.LIMITED)
                    dead.add(account.id)
                    logger.warning("Warmup: conta %s caiu, failover (%s)", account.phone, exc)
                else:
                    logger.exception("Warmup falhou na conta %s", account.phone)
                    _set_job(job_id, error=str(exc))
            finally:
                if client.is_connected():
                    await client.disconnect()
            _set_job(job_id, processed=done + failed, failed=failed)
            await asyncio.sleep(0)

    status.step_done("WARMUP: aquecimento concluído", 100)
    return {"job_id": job_id, "warmed": done, "failed": failed, "total": total}


async def run_warmup_job(job_id: int) -> None:
    """Entrada assíncrona registrada em ``registry.run_job``."""
    import json

    from app.database.models import Job
    from app.workers.registry import get_job

    info = get_job(job_id) or {}
    params = info.get("params", {})
    result = await warmup(
        job_id,
        rounds=int(params.get("rounds", 2)),
        account_id=params.get("account_id"),
    )
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


__all__ = ["warmup", "run_warmup_job"]
