"""Failover e rotação automática de contas — Conta_01 cai → Conta_02 assume.

O worker nunca interrompe o job: ao detectar conta limitada/banida ou
sessão inválida, marca o status no banco e devolve a próxima conta ativa.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.core.session_manager import LoginError
from app.database.database import get_session_factory
from app.database.models import Account, AccountStatus

logger = logging.getLogger(__name__)


def list_active_accounts(exclude_ids: set[int] | None = None) -> list[Account]:
    """Todas as contas ativas ordenadas por id (objetos destacados da sessão)."""
    Session = get_session_factory()
    session = Session()
    try:
        q = session.query(Account).filter(Account.status == AccountStatus.ACTIVE)
        if exclude_ids:
            q = q.filter(~Account.id.in_(exclude_ids))
        rows = q.order_by(Account.id).all()
        for row in rows:
            session.expunge(row)
        return rows
    finally:
        session.close()


def mark_account(account_id: int, new_status: AccountStatus) -> None:
    Session = get_session_factory()
    session = Session()
    try:
        row = session.get(Account, account_id)
        if row is not None and row.status != new_status:
            row.status = new_status
            session.commit()
            logger.warning("Conta #%s marcada como %s (failover)", account_id, new_status.value)
    finally:
        session.close()


def pick_next_account(preferred_id: Optional[int] = None,
                      exclude_ids: set[int] | None = None) -> Account:
    """Escolhe a próxima conta ativa respeitando exclusões (failover).

    Raises:
        LoginError: ``no_active_account`` quando não resta nenhuma conta.
    """
    exclude = set(exclude_ids or set())
    Session = get_session_factory()
    session = Session()
    try:
        if preferred_id is not None and preferred_id not in exclude:
            acc = (
                session.query(Account)
                .filter(Account.id == preferred_id,
                        Account.status == AccountStatus.ACTIVE)
                .first()
            )
            if acc is not None:
                session.expunge(acc)
                return acc
            # Preferida indisponível → cai para rotação geral.
            exclude.add(preferred_id)
        rows = (
            session.query(Account)
            .filter(Account.status == AccountStatus.ACTIVE)
            .order_by(Account.id)
            .all()
        )
        for row in rows:
            if row.id not in exclude:
                session.expunge(row)
                return row
        raise LoginError(
            "no_active_account",
            "Todas as contas caíram ou foram limitadas. Cadastre outra via /admin/sessions.",
        )
    finally:
        session.close()


def is_recoverable_account_error(exc: Exception) -> bool:
    """Erros que indicam que devemos trocar de conta sem matar o job."""
    from telethon.errors import (
        AuthKeyUnregisteredError,
        PeerFloodError,
        UserDeactivatedError,
    )

    if isinstance(exc, LoginError):
        return exc.code in ("session_invalid", "account_limited", "all_limited")
    return isinstance(exc, (PeerFloodError, AuthKeyUnregisteredError, UserDeactivatedError))


__all__ = ["list_active_accounts", "mark_account", "pick_next_account", "is_recoverable_account_error"]
