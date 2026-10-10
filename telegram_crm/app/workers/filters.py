"""Filtros e segmentação de membros — Fase 3 do blueprint.

Motor de critérios sobre ``members`` (§1.3) + materialização em ``MemberList``
(§1.4). Roda 100% offline (só banco) — nenhuma chamada ao Telegram.

Critérios suportados:
    ``has_username``     → possui (True) / não possui (False) ``@username``
    ``status``           → igualdade exata ('active' | 'left' | 'restricted' | …)
    ``last_seen_after``  → visto *depois* do instante (recência)
    ``last_seen_before`` → visto *antes* do instante
    ``user_id_min/max``  → faixa de ``user_id``
    ``source_group_id``  → grupo de origem (**PK interna** de ``groups``)
    ``in_list_id``       → já pertence a outra lista
    ``not_in_list_id``   → não pertence a outra lista

Composição: ``mode='and'`` (padrão, interseção) ou ``mode='or'`` (união).

Nota de nomenclatura: ``source_group_id`` filtra a coluna ``members.source_group_id``,
que é FK para ``groups.id`` (PK interna) — **não** o id do Telegram. A API expõe
esse PK como ``id`` em ``GET /admin/groups``.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import and_, not_, or_, select
from sqlalchemy.orm import Session

from app.database.database import get_session_factory
from app.database.models import Member, MemberList, list_members

logger = logging.getLogger(__name__)

# Modos de composição aceitos.
MODE_AND = "and"
MODE_OR = "or"
_MODES = (MODE_AND, MODE_OR)


@dataclass
class MemberFilter:
    """Critérios de segmentação. ``None``/vazio = critério desligado."""

    has_username: Optional[bool] = None
    status: Optional[str] = None
    status_in: Optional[Sequence[str]] = None
    last_seen_after: Optional[datetime] = None
    last_seen_before: Optional[datetime] = None
    user_id_min: Optional[int] = None
    user_id_max: Optional[int] = None
    source_group_id: Optional[int] = None
    in_list_id: Optional[int] = None
    not_in_list_id: Optional[int] = None
    mode: str = MODE_AND

    # ------------------------------------------------------------------
    # Montagem das cláusulas SQL
    # ------------------------------------------------------------------
    def clauses(self) -> list[Any]:
        """Devolve as cláusulas SQLAlchemy dos critérios ativos (AND entre si)."""
        out: list[Any] = []

        if self.has_username is True:
            # NULL e string vazia não contam como "tem username".
            out.append(and_(Member.username.isnot(None), Member.username != ""))
        elif self.has_username is False:
            out.append(or_(Member.username.is_(None), Member.username == ""))

        if self.status:
            out.append(Member.status == self.status)
        if self.status_in:
            out.append(Member.status.in_(list(self.status_in)))

        if self.last_seen_after is not None:
            # Só quem tem last_seen conhecido pode ser "mais recente que X".
            out.append(Member.last_seen.isnot(None))
            out.append(Member.last_seen > self.last_seen_after)
        if self.last_seen_before is not None:
            out.append(Member.last_seen.isnot(None))
            out.append(Member.last_seen < self.last_seen_before)

        if self.user_id_min is not None:
            out.append(Member.user_id >= int(self.user_id_min))
        if self.user_id_max is not None:
            out.append(Member.user_id <= int(self.user_id_max))

        if self.source_group_id is not None:
            out.append(Member.source_group_id == int(self.source_group_id))

        if self.in_list_id is not None:
            out.append(
                Member.id.in_(
                    select(list_members.c.member_id).where(
                        list_members.c.member_list_id == int(self.in_list_id)
                    )
                )
            )
        if self.not_in_list_id is not None:
            out.append(
                not_(
                    Member.id.in_(
                        select(list_members.c.member_id).where(
                            list_members.c.member_list_id == int(self.not_in_list_id)
                        )
                    )
                )
            )
        return out

    def condition(self) -> Optional[Any]:
        """Combina as cláusulas no ``mode`` escolhido. ``None`` = sem filtro."""
        clauses = self.clauses()
        if not clauses:
            return None
        if self.mode == MODE_OR:
            return or_(*clauses)
        return and_(*clauses)

    def is_empty(self) -> bool:
        """True quando nenhum critério está ativo (selecionaria todos)."""
        return not self.clauses()

    def to_dict(self) -> dict:
        """Serializa para JSON (datetimes em ISO) — usado em logs/params."""
        data = asdict(self)
        for key in ("last_seen_after", "last_seen_before"):
            value = data.get(key)
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        if isinstance(data.get("status_in"), (list, tuple)):
            data["status_in"] = list(data["status_in"])
        return data


# --------------------------------------------------------------------------
# Consulta (materializa IDs / conta)
# --------------------------------------------------------------------------
def filter_members(
    filt: MemberFilter,
    *,
    limit: Optional[int] = None,
    offset: int = 0,
    session: Optional[Session] = None,
) -> list[Member]:
    """Aplica o filtro e devolve os ``Member`` (ordenados por id)."""
    own = session is None
    db = session or get_session_factory()()
    try:
        query = db.query(Member)
        condition = filt.condition()
        if condition is not None:
            query = query.filter(condition)
        query = query.order_by(Member.id)
        if offset:
            query = query.offset(max(offset, 0))
        if limit is not None:
            query = query.limit(max(limit, 1))
        rows = query.all()
        if own:
            # Devolve objetos destacados da sessão (usáveis após o close).
            for row in rows:
                db.expunge(row)
        return rows
    finally:
        if own:
            db.close()


def filter_member_ids(filt: MemberFilter, *, session: Optional[Session] = None) -> list[int]:
    """Só os IDs que casam com o filtro (barato para contagens/upserts)."""
    own = session is None
    db = session or get_session_factory()()
    try:
        stmt = select(Member.id)
        condition = filt.condition()
        if condition is not None:
            stmt = stmt.where(condition)
        return [int(i) for i in db.execute(stmt.order_by(Member.id)).scalars().all()]
    finally:
        if own:
            db.close()


def count_members(filt: MemberFilter, *, session: Optional[Session] = None) -> int:
    """Quantos membros casam com o filtro."""
    own = session is None
    db = session or get_session_factory()()
    try:
        query = db.query(Member)
        condition = filt.condition()
        if condition is not None:
            query = query.filter(condition)
        return int(query.count())
    finally:
        if own:
            db.close()


def preview(filt: MemberFilter, sample: int = 5) -> dict:
    """Resumo do filtro: total + amostra (útil para a UI antes de segmentar)."""
    total = count_members(filt)
    rows = filter_members(filt, limit=sample)
    return {
        "filter": filt.to_dict(),
        "total": total,
        "sample": [
            {
                "id": m.id,
                "user_id": m.user_id,
                "username": m.username,
                "status": m.status,
                "last_seen": m.last_seen.isoformat() if m.last_seen else None,
                "source_group_id": m.source_group_id,
            }
            for m in rows
        ],
    }


# --------------------------------------------------------------------------
# Materialização (Tarefa 3.2)
# --------------------------------------------------------------------------
def get_or_create_list(
    list_name: str,
    description: Optional[str] = None,
    *,
    session: Session,
) -> tuple[MemberList, bool]:
    """Devolve ``(lista, criada)`` — ``list_name`` é único no modelo."""
    row = session.query(MemberList).filter(MemberList.list_name == list_name).first()
    if row is not None:
        if description is not None:
            row.description = description
        return row, False
    row = MemberList(list_name=list_name, description=description)
    session.add(row)
    session.flush()
    return row, True


def materialize_list(
    filt: MemberFilter,
    list_name: str,
    description: Optional[str] = None,
    *,
    replace: bool = True,
) -> dict:
    """Aplica o filtro e grava o resultado em uma ``MemberList``.

    ``replace=True`` (padrão) zera as associações antigas antes de regravar —
    re-segmentar o mesmo nome reflete o filtro atual. ``replace=False`` faz
    unão com o que já existia.

    Idempotente: a PK composta de ``list_members`` impede duplicatas.
    """
    ids = filter_member_ids(filt)

    Session = get_session_factory()
    session = Session()
    try:
        member_list, created = get_or_create_list(list_name, description, session=session)
        if replace:
            session.execute(
                list_members.delete().where(
                    list_members.c.member_list_id == member_list.id
                )
            )
            session.flush()

        existing = set(list_member_ids(member_list.id, session=session))
        to_add = [mid for mid in ids if int(mid) not in existing]
        if to_add:
            session.execute(
                list_members.insert(),
                [{"member_list_id": member_list.id, "member_id": int(m)} for m in to_add],
            )
        session.commit()
        return {
            "list_id": member_list.id,
            "list_name": member_list.list_name,
            "created": created,
            "matched": len(ids),
            "added": len(to_add),
            "total": len(list_member_ids(member_list.id, session=session)),
            "filter": filt.to_dict(),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# --------------------------------------------------------------------------
# Pertencimento (Tarefas 3.3 e 3.4)
# --------------------------------------------------------------------------
def list_member_ids(list_id: int, *, session: Optional[Session] = None) -> list[int]:
    """IDs dos membros de uma lista (ordenados)."""
    own = session is None
    db = session or get_session_factory()()
    try:
        return [
            int(i)
            for i in db.execute(
                select(list_members.c.member_id)
                .where(list_members.c.member_list_id == int(list_id))
                .order_by(list_members.c.member_id)
            ).scalars().all()
        ]
    finally:
        if own:
            db.close()


def add_members_to_list(list_id: int, member_ids: Iterable[int]) -> dict:
    """Adiciona membros à lista de forma idempotente (PK composta)."""
    wanted = [int(m) for m in dict.fromkeys(member_ids or [])]

    Session = get_session_factory()
    session = Session()
    try:
        existing = set(list_member_ids(list_id, session=session))
        to_add = [m for m in wanted if m not in existing]
        if to_add:
            session.execute(
                list_members.insert(),
                [{"member_list_id": int(list_id), "member_id": m} for m in to_add],
            )
            session.commit()
        return {
            "list_id": int(list_id),
            "requested": len(wanted),
            "added": len(to_add),
            "already_present": len(wanted) - len(to_add),
            "total": len(list_member_ids(list_id, session=session)),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def remove_members_from_list(list_id: int, member_ids: Iterable[int]) -> dict:
    """Remove membros da lista. IDs ausentes são ignorados (idempotente)."""
    wanted = [int(m) for m in dict.fromkeys(member_ids or [])]

    Session = get_session_factory()
    session = Session()
    try:
        existing = set(list_member_ids(list_id, session=session))
        to_remove = [m for m in wanted if m in existing]
        if to_remove:
            session.execute(
                list_members.delete().where(
                    and_(
                        list_members.c.member_list_id == int(list_id),
                        list_members.c.member_id.in_(to_remove),
                    )
                )
            )
            session.commit()
        return {
            "list_id": int(list_id),
            "requested": len(wanted),
            "removed": len(to_remove),
            "not_in_list": len(wanted) - len(to_remove),
            "total": len(list_member_ids(list_id, session=session)),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "MemberFilter",
    "MODE_AND",
    "MODE_OR",
    "filter_members",
    "filter_member_ids",
    "count_members",
    "preview",
    "get_or_create_list",
    "materialize_list",
    "list_member_ids",
    "add_members_to_list",
    "remove_members_from_list",
]



