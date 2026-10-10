"""Mass Sender Worker — disparo de iscas com jitter e mimetismo humano.

Características:
  * Processa leads ``pending`` em paralelo (semáforo por conta).
  * Intervalo aleatório (jitter) entre mensagens + pausa longa a cada lote.
  * Rotação de conta no modo ``pool`` (round-robin entre contas ativas).
  * Erros do Telethon mapeados: FloodWait → espera; UserIsBlocked → failed;
    PeerFlood → conta marcada como ``limited``.
  * Progresso persistido em ``jobs`` (processed/total) para a API de cliente.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from telethon.errors import (
    FloodWaitError,
    PeerFloodError,
    UserIsBlockedError,
    ChatWriteForbiddenError,
    UserPrivacyRestrictedError,
)

from app.core.jitter import batch_pause, human_pause, is_batch_boundary, think, typing_delay
from app.core.session_manager import ClientFactory, LoginError
from app.core.status import status
from app.database.database import get_session_factory
from app.database.models import (
    Account,
    AccountStatus,
    Job,
    Lead,
    LeadStatus,
    Log,
    Settings,
)
from app.workers.harvester import ensure_proxy, pick_account
from app.workers.failover import (
    is_recoverable_account_error,
    list_active_accounts,
    mark_account,
    pick_next_account,
)
from app.workers.registry import _set_job

logger = logging.getLogger(__name__)

BAIT_KEY = "bait_message"
DEFAULT_BAIT = "Oi! Vi seu nome no grupo, posso te mostrar como está faturando todo dia?"


def get_bait_message() -> str:
    """Lê a mensagem de isca ativa nas settings (fallback: mensagem padrão)."""
    Session = get_session_factory()
    session = Session()
    try:
        row = session.get(Settings, BAIT_KEY)
        return (row.value if row else DEFAULT_BAIT) or DEFAULT_BAIT
    finally:
        session.close()


def set_bait_message(text: str) -> None:
    Session = get_session_factory()
    session = Session()
    try:
        row = session.get(Settings, BAIT_KEY)
        if row is None:
            session.add(Settings(key=BAIT_KEY, value=text))
        else:
            row.value = text
        session.commit()
    finally:
        session.close()


def _render(template: str, lead: Lead) -> str:
    """Substitui placeholders pela dados do lead."""
    return template.format(
        username=lead.username or "",
        first_name=lead.first_name or "",
        last_name=lead.last_name or "",
        user_id=lead.user_id,
    )


def _active_accounts() -> list[Account]:
    Session = get_session_factory()
    session = Session()
    try:
        rows = (
            session.query(Account)
            .filter(Account.status == AccountStatus.ACTIVE)
            .order_by(Account.id)
            .all()
        )
        for row in rows:
            session.expunge(row)
        return rows
    finally:
        session.close()


def _mark_lead(
    lead_id: int,
    state: LeadStatus,
    *,
    account_id: Optional[int] = None,
    error: bool = False,
) -> None:
    Session = get_session_factory()
    session = Session()
    try:
        lead = session.get(Lead, lead_id)
        if lead is None:
            return
        lead.status = state
        if state == LeadStatus.SENT:
            lead.sent_at = datetime.now(timezone.utc)
        if account_id is not None:
            lead.account_id = account_id
        session.add(
            Log(
                account_id=account_id,
                action="send_bait",
                target_id=lead.user_id,
                status="failed" if error else "success",
            )
        )
        session.commit()
    finally:
        session.close()
def _pending_leads(limit: int) -> list[Lead]:
    Session = get_session_factory()
    session = Session()
    try:
        rows = (
            session.query(Lead)
            .filter(Lead.status == LeadStatus.PENDING)
            .order_by(Lead.id)
            .limit(limit)
            .all()
        )
        for row in rows:
            session.expunge(row)
        return rows
    finally:
        session.close()


async def send_bait(
    job_id: int,
    limit: int = 10,
    account_id: Optional[int] = None,
    message: Optional[str] = None,
) -> dict:
    """Envia a isca para até ``limit`` leads pendentes.

    Contas são escolhidas em round-robin (modo pool). Cada envio respeita
    jitter + pausa de lote para mimetizar comportamento humano.
    """
    template = message or get_bait_message()
    leads = _pending_leads(limit)
    # Rotação automática com failover: Conta_01 cai → Conta_02 assume sem
    # interromper o job. A conta preferida (se informada) é tentada primeiro.
    if account_id:
        accounts = [pick_account(account_id)]
    else:
        accounts = list_active_accounts()
    dead: set[int] = set()

    if not accounts:
        raise LoginError("no_active_account", "Nenhuma conta ativa para disparo.")
    if not leads:
        _set_job(job_id, total=0, processed=0)
        status.step_done("MASS SENDER: nenhum lead pendente", 100)
        return {"job_id": job_id, "sent": 0, "failed": 0, "message": "Nenhum lead pendente."}

    for account in accounts:
        ensure_proxy(account)

    _set_job(job_id, total=len(leads), processed=0, failed=0, error=None)
    status.task(f"MASS SENDER: preparando {len(leads)} leads", task_pct=5)

    sent = 0
    failed = 0
    idx = 0

    while idx < len(leads):
        lead = leads[idx]
        account = accounts[idx % len(accounts)] if accounts else pick_next_account(
            account_id, exclude_ids=dead
        )
        # Se a conta sorteada morreu no meio do job, troca na hora.
        if account.id in dead:
            account = pick_next_account(account_id, exclude_ids=dead)
        client = ClientFactory.from_session_string(
            account.session_string, account.api_id, account.api_hash, account.proxy
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise LoginError("session_invalid", f"Sessão inválida: {account.phone}")

            # Mimetismo: pausa de "raciocínio" antes do primeiro envio da tarefa.
            if idx == 0:
                await think(f"sender:{account.phone}")

            text = _render(template, lead)
            # Digitação simulada proporcional ao tamanho da mensagem.
            await asyncio.sleep(typing_delay(text))

            await client.send_message(lead.user_id, text, no_webpage=True)
            _mark_lead(lead.id, LeadStatus.SENT, account_id=account.id)
            sent += 1
            status.task(
                f"MASS SENDER: {sent + failed}/{len(leads)} enviados",
                task_pct=min(95, int((sent + failed) * 90 / len(leads))),
            )

            if is_batch_boundary(idx):
                await batch_pause(f"sender:{account.phone}")
            else:
                await human_pause(key=f"sender:{account.phone}")

        except FloodWaitError as exc:
            _mark_lead(lead.id, LeadStatus.FAILED, account_id=account.id, error=True)
            failed += 1
            logger.warning("FloodWait %ss — aguardando", exc.seconds)
            await asyncio.sleep(min(exc.seconds, 60))
        except PeerFloodError:
            # Conta limitada pelo Telegram → failover: marca e troca de conta
            # sem interromper o job (a próxima iteração já usa outra conta).
            mark_account(account.id, AccountStatus.LIMITED)
            dead.add(account.id)
            _set_account_status(account.id, AccountStatus.LIMITED)
            _mark_lead(lead.id, LeadStatus.FAILED, account_id=account.id, error=True)
            failed += 1
            try:
                fresh = list_active_accounts(exclude_ids=dead)
                accounts = fresh or accounts
            except Exception:  # noqa: BLE001 — lista antiga continua valendo
                pass
            if not [a for a in accounts if a.id not in dead]:
                raise LoginError("all_limited", "Todas as contas foram limitadas.")
        except (UserIsBlockedError, ChatWriteForbiddenError, UserPrivacyRestrictedError):
            _mark_lead(lead.id, LeadStatus.FAILED, account_id=account.id, error=True)
            failed += 1
        except Exception as exc:  # noqa: BLE001 — worker nunca pode morrer
            logger.exception("Falha ao enviar para %s", lead.user_id)
            _mark_lead(lead.id, LeadStatus.FAILED, account_id=account.id, error=True)
            failed += 1
            # Sessão morta / conta banida no meio do disparo → failover também.
            if is_recoverable_account_error(exc):
                mark_account(account.id, AccountStatus.LIMITED)
                dead.add(account.id)
            _set_job(job_id, error=str(exc))
        finally:
            if client.is_connected():
                await client.disconnect()

        idx += 1
        _set_job(job_id, processed=sent + failed, failed=failed)

    status.step_done("MASS SENDER: disparo concluído", 100)
    return {"job_id": job_id, "sent": sent, "failed": failed, "total": len(leads)}

def _set_account_status(account_id: int, new_status: AccountStatus) -> None:
    """Atualiza o status da conta (active/banned/limited)."""
    Session = get_session_factory()
    session = Session()
    try:
        account = session.get(Account, account_id)
        if account is not None:
            account.status = new_status
            session.commit()
    finally:
        session.close()


async def run_send_job(job_id: int) -> None:
    """Entrada assíncrona registrada em ``registry.run_job``."""
    from app.workers.registry import get_job

    info = get_job(job_id) or {}
    params = info.get("params", {})
    result = await send_bait(
        job_id=job_id,
        limit=int(params.get("limit", 10)),
        account_id=params.get("account_id"),
        message=params.get("message"),
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


__all__ = [
    "send_bait",
    "run_send_job",
    "get_bait_message",
    "set_bait_message",
    "BAIT_KEY",
    "DEFAULT_BAIT",
]

