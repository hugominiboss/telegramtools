"""Member Adder Worker — conversão por adição forçada de membros (Fase 4).

Adiciona leads auditados (tabela ``leads``) a um grupo/canal-alvo usando a
conta que já está no pool. Engenharia anti-ban e anti-travamento:

  * Check de privacidade: só membros com dados válidos entram na fila;
  * Jitter humano: pausa aleatória entre convites + "pensamento" antes do
    primeiro + quebra de lote a cada N convites;
  * Rotação de contas (failover): ``PeerFloodError``/sessão morta → marca a
    conta ``limited`` e troca automaticamente para a próxima, sem interromper
    a campanha;
  * Erros de "usuário não pode ser adicionado" (privacidade, banido, já
    participante, etc.) são registrados no ``logs`` e a fila segue — nunca
    travam o job.

Tipos de alvo suportados:
  * supergrupo/canal (``Channel``)  → ``InviteToChannelRequest``;
  * grupo básico (``Chat``)         → ``AddChatUserRequest``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from telethon import functions
from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    FloodWaitError,
    InputUserDeactivatedError,
    PeerFloodError,
    UserAlreadyInvitedError,
    UserAlreadyParticipantError,
    UserBannedInChannelError,
    UserBlockedError,
    UserChannelsTooMuchError,
    UserDeletedError,
    UserIdInvalidError,
    UserInvalidError,
    UserIsBlockedError,
    UserKickedError,
    UserNotMutualContactError,
    UserPrivacyRestrictedError,
    UserRestrictedError,
    UsersTooMuchError,
    YouBlockedUserError,
)
from telethon.tl.types import Channel, Chat

from app.core.jitter import batch_pause, human_pause, is_batch_boundary, think
from app.core.session_manager import ClientFactory, LoginError
from app.core.status import status
from app.database.database import get_session_factory
from app.database.models import AccountStatus, Job, Lead, LeadStatus, Log
from app.workers.failover import (
    is_recoverable_account_error,
    list_active_accounts,
    mark_account,
    pick_next_account,
)
from app.workers.harvester import ensure_proxy, pick_account
from app.workers.registry import _set_job

logger = logging.getLogger(__name__)

# Convites são mais sensíveis que DMs: intervalos mais largos.
INVITE_MIN = 6.0
INVITE_MAX = 16.0

# Erros por-membro: não derrubam a conta nem o job — só marcam o lead e seguem.
_PRIVACY_ERRORS = (
    UserPrivacyRestrictedError,
    UserNotMutualContactError,
    UserAlreadyParticipantError,
    UserAlreadyInvitedError,
    UserBannedInChannelError,
    UserKickedError,
    UserChannelsTooMuchError,
    UserBlockedError,
    YouBlockedUserError,
    UserIsBlockedError,
    UserRestrictedError,
    InputUserDeactivatedError,
    UserDeletedError,
    UserIdInvalidError,
    UserInvalidError,
    UsersTooMuchError,
)


def _log(account_id: Optional[int], action: str, target_id: Optional[int], state: str) -> None:
    """Grava uma linha de auditoria na tabela ``logs``."""
    Session = get_session_factory()
    session = Session()
    try:
        session.add(
            Log(account_id=account_id, action=action, target_id=target_id, status=state)
        )
        session.commit()
    finally:
        session.close()


def _pending_leads(limit: int) -> list[Lead]:
    """Leads pendentes (saída do Audit) — objetos liberados da sessão ORM."""
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


def _mark_lead(
    lead_id: int,
    state: LeadStatus,
    *,
    account_id: Optional[int] = None,
    error: bool = False,
) -> None:
    """Atualiza o status do lead e registra o evento no log."""
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
                action="member_add",
                target_id=lead.user_id,
                status="failed" if error else "success",
            )
        )
        session.commit()
    finally:
        session.close()


async def _invite_one(client, target_entity, target_input, user_input) -> None:
    """Executa o convite de UM usuário, escolhendo o request pelo tipo de alvo."""
    if isinstance(target_entity, Chat):
        # Grupo básico (não-supergrupo): só o dono/admin consegue adicionar.
        await client(
            functions.messages.AddChatUserRequest(
                chat_id=target_entity.id,
                user_id=user_input,
                fwd_limit=10,
            )
        )
    else:
        # Supergrupo/canal (Channel): invite em lote de 1 (jitter por membro).
        await client(
            functions.channels.InviteToChannelRequest(
                channel=target_input,
                users=[user_input],
            )
        )


async def add_members(
    job_id: int,
    target_group: str,
    limit: int = 10,
    account_id: Optional[int] = None,
) -> dict:
    """Adiciona leads pendentes ao ``target_group`` (link ou id numérico)."""
    leads = _pending_leads(limit)
    if account_id:
        accounts = [pick_account(account_id)]
    else:
        accounts = list_active_accounts()
    dead: set[int] = set()

    if not accounts:
        raise LoginError("no_active_account", "Nenhuma conta ativa para adicionar membros.")
    if not leads:
        _set_job(job_id, total=0, processed=0)
        status.step_done("MEMBER ADDER: nenhum lead pendente", 100)
        return {"job_id": job_id, "added": 0, "failed": 0, "total": 0}

    for account in accounts:
        ensure_proxy(account)

    _set_job(job_id, total=len(leads), processed=0, failed=0, error=None)
    status.task(f"MEMBER ADDER: preparando {len(leads)} membros para {target_group}", task_pct=5)

    added = 0
    failed = 0
    idx = 0

    while idx < len(leads):
        lead = leads[idx]
        account = (
            accounts[idx % len(accounts)]
            if accounts
            else pick_next_account(account_id, exclude_ids=dead)
        )
        if account.id in dead:
            account = pick_next_account(account_id, exclude_ids=dead)

        client = ClientFactory.from_session_string(
            account.session_string, account.api_id, account.api_hash, account.proxy
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise LoginError("session_invalid", f"Sessão inválida: {account.phone}")

            target_entity = await client.get_entity(target_group)
            target_input = await client.get_input_entity(target_group)

            if idx == 0:
                await think(f"adder:{account.phone}")

            user_input = await client.get_input_entity(lead.user_id)
            await _invite_one(client, target_entity, target_input, user_input)

            _mark_lead(lead.id, LeadStatus.ADDED, account_id=account.id)
            _log(account.id, "member_add", lead.user_id, "success")
            added += 1
            status.task(
                f"MEMBER ADDER: {added + failed}/{len(leads)} adicionados",
                task_pct=min(95, int((added + failed) * 90 / len(leads))),
            )

            if is_batch_boundary(idx):
                await batch_pause(f"adder:{account.phone}")
            else:
                await human_pause(INVITE_MIN, INVITE_MAX, key=f"adder:{account.phone}")
        except FloodWaitError as exc:
            _mark_lead(lead.id, LeadStatus.FAILED, account_id=account.id, error=True)
            _log(account.id, "member_add", lead.user_id, "flood_wait")
            failed += 1
            logger.warning("FloodWait %ss — aguardando (adder)", exc.seconds)
            await asyncio.sleep(min(exc.seconds, 60))
        except PeerFloodError:
            # Conta limitada pelo Telegram → failover imediato, sem parar a fila.
            mark_account(account.id, AccountStatus.LIMITED)
            dead.add(account.id)
            _mark_lead(lead.id, LeadStatus.FAILED, account_id=account.id, error=True)
            _log(account.id, "member_add", lead.user_id, "peer_flood")
            failed += 1
            try:
                fresh = list_active_accounts(exclude_ids=dead)
                accounts = fresh or accounts
            except Exception:  # noqa: BLE001
                pass
            if not [a for a in accounts if a.id not in dead]:
                raise LoginError("all_limited", "Todas as contas foram limitadas.")
        except (ChatAdminRequiredError, ChannelPrivateError):
            # Erro de setup do grupo-alvo: não adianta tentar outros membros.
            _set_job(
                job_id,
                error=(
                    "Sem permissão para adicionar no grupo-alvo "
                    "(precisa ser admin) ou grupo privado/inacessível."
                ),
            )
            raise
        except _PRIVACY_ERRORS:
            # "User cannot be added" (privacidade, já participante, banido…).
            # Registra e segue — nunca trava a fila.
            _mark_lead(lead.id, LeadStatus.FAILED, account_id=account.id, error=True)
            _log(account.id, "member_add", lead.user_id, "skipped")
            failed += 1
        except Exception as exc:  # noqa: BLE001 — worker nunca pode morrer
            logger.exception("Falha ao adicionar %s", lead.user_id)
            _mark_lead(lead.id, LeadStatus.FAILED, account_id=account.id, error=True)
            _log(account.id, "member_add", lead.user_id, "error")
            failed += 1
            if is_recoverable_account_error(exc):
                mark_account(account.id, AccountStatus.LIMITED)
                dead.add(account.id)
            _set_job(job_id, error=str(exc))
        finally:
            if client.is_connected():
                await client.disconnect()

        idx += 1
        _set_job(job_id, processed=added + failed, failed=failed)

    status.step_done("MEMBER ADDER: adição concluída", 100)
    return {"job_id": job_id, "added": added, "failed": failed, "total": len(leads)}


async def run_add_job(job_id: int) -> None:
    """Entrada assíncrona registrada em ``registry.run_job``."""
    from app.workers.registry import get_job

    info = get_job(job_id) or {}
    params = info.get("params", {})
    result = await add_members(
        job_id=job_id,
        target_group=params.get("target_group", ""),
        limit=int(params.get("limit", 10)),
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


__all__ = ["add_members", "run_add_job"]



