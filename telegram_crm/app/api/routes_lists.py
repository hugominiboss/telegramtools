"""Rotas de listas e segmentação — Fase 3 do blueprint.

Endpoints:
    POST   /lists                    → cria uma lista (201)
    GET    /lists                    → lista as listas + contagem de membros
    GET    /lists/{list_id}          → detalhe da lista
    PATCH  /lists/{list_id}          → renomeia / atualiza descrição (200)
    DELETE /lists/{list_id}          → exclui (204) — ``list_members\" em CASCADE
    POST   /lists/segment            → aplica filtro e materializa (201)
    GET    /lists/preview            → conta + amostra sem gravar nada
    GET    /lists/{list_id}/members  → membros da lista (paginado)
    POST   /lists/{list_id}/members  → adiciona membros (idempotente)
    DELETE /lists/{list_id}/members  → remove membros (idempotente)

Toda a lógica vive em ``workers/filters.py``; aqui fica só validação,
tradução de erros e serialização.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.database.database import get_db
from app.database.models import Campaign, Member, MemberList, list_members
from app.workers.filters import (
    MODE_AND,
    MODE_OR,
    MemberFilter,
    add_members_to_list,
    get_or_create_list,
    materialize_list,
    preview,
    remove_members_from_list,
)

router = APIRouter(prefix="/lists", tags=["lists"])


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class ListIn(BaseModel):
    list_name: str = Field(..., min_length=1, max_length=128, examples=["Leads quentes"])
    description: Optional[str] = Field(None, max_length=2000)


class ListUpdateIn(BaseModel):
    list_name: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=2000)


class SegmentIn(BaseModel):
    """Corpo de ``POST /lists/segment``: critérios + nome da lista destino."""

    has_username: Optional[bool] = None
    status: Optional[str] = Field(None, examples=["active"])
    status_in: Optional[list[str]] = None
    last_seen_after: Optional[str] = Field(None, examples=["2026-01-01T00:00:00+00:00"])
    last_seen_before: Optional[str] = None
    user_id_min: Optional[int] = None
    user_id_max: Optional[int] = None
    # PK interna de ``groups`` (campo ``id`` de GET /admin/groups).
    source_group_id: Optional[int] = None
    in_list_id: Optional[int] = None
    not_in_list_id: Optional[int] = None
    mode: str = Field(MODE_AND, pattern=f"^({MODE_AND}|{MODE_OR})$")
    list_name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=2000)
    replace: bool = Field(True, description="Regrava a lista com o resultado do filtro")


class MembersIn(BaseModel):
    member_ids: list[int] = Field(..., min_length=1, description="PKs de ``members``")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _get_list_or_404(list_id: int, db: DbSession) -> MemberList:
    row = db.get(MemberList, list_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "list_not_found"})
    return row


def _member_ids_in_list(list_id: int, db: DbSession) -> list[int]:
    from app.workers.filters import list_member_ids

    return list_member_ids(list_id, session=db)


def _serialize_list(row: MemberList, db: DbSession) -> dict:
    return {
        "id": row.id,
        "list_name": row.list_name,
        "description": row.description,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "members_count": len(_member_ids_in_list(row.id, db)),
        "campaigns_count": db.query(Campaign)
        .filter(Campaign.target_list_id == row.id)
        .count(),
    }


def _build_filter(spec: FilterSpec) -> MemberFilter:
    """Converte o schema Pydantic em ``MemberFilter`` (parseando datetimes)."""
    from datetime import datetime

    def _parse(value: Optional[str]):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_datetime", "message": f"Data inválida: {value}"},
            ) from exc

    return MemberFilter(
        has_username=spec.has_username,
        status=spec.status,
        status_in=spec.status_in,
        last_seen_after=_parse(spec.last_seen_after),
        last_seen_before=_parse(spec.last_seen_before),
        user_id_min=spec.user_id_min,
        user_id_max=spec.user_id_max,
        source_group_id=spec.source_group_id,
        in_list_id=spec.in_list_id,
        not_in_list_id=spec.not_in_list_id,
        mode=spec.mode,
    )


def _integrity_error(exc: IntegrityError) -> HTTPException:
    """Traduz violação de unique em 409 com corpo padronizado."""
    message = str(getattr(exc, "orig", exc))
    if "list_name" in message or "member_lists" in message:
        return HTTPException(
            status_code=409,
            detail={"error": "list_name_taken", "message": "Já existe uma lista com esse nome."},
        )
def _parse_dt(value: Optional[str], field: str = "data") -> Optional[datetime]:
    """Converte string ISO-8601 em datetime, com erro 422 amigável."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_datetime",
                    "message": f"{field} inválida (use ISO-8601): {value}"},
        ) from exc


def _build_filter(
    has_username: Optional[bool] = None,
    status: Optional[str] = None,
    status_in: Optional[list[str]] = None,
    last_seen_after: Optional[str] = None,
    last_seen_before: Optional[str] = None,
    user_id_min: Optional[int] = None,
    user_id_max: Optional[int] = None,
    source_group_id: Optional[int] = None,
    in_list_id: Optional[int] = None,
    not_in_list_id: Optional[int] = None,
    mode: str = MODE_AND,
) -> MemberFilter:
    """Monta o ``MemberFilter`` a partir dos parâmetros crus da request."""
    return MemberFilter(
        has_username=has_username,
        status=status,
        status_in=status_in,
        last_seen_after=_parse_dt(last_seen_after, "last_seen_after"),
        last_seen_before=_parse_dt(last_seen_before, "last_seen_before"),
        user_id_min=user_id_min,
        user_id_max=user_id_max,
        source_group_id=source_group_id,
        in_list_id=in_list_id,
        not_in_list_id=not_in_list_id,
        mode=mode,
    )


def _integrity_error(exc: IntegrityError) -> HTTPException:
    """Traduz violação de unique em 409 com corpo padronizado."""
    message = str(getattr(exc, "orig", exc))
    if "list_name" in message or "member_lists" in message:
        return HTTPException(
            status_code=409,
            detail={"error": "list_name_taken",
                    "message": "Já existe uma lista com esse nome."},
        )
    return HTTPException(
        status_code=409,
        detail={"error": "integrity_error",
                "message": "Conflito de integridade no banco."},
    )


# --------------------------------------------------------------------------
# CRUD de listas (Tarefa 3.3)
# --------------------------------------------------------------------------
@router.post("", status_code=201)
def create_list(payload: ListIn, db: DbSession = Depends(get_db)) -> dict:
    """Cria uma lista vazia. ``list_name`` é único → 409 em duplicata."""
    # Diferente de ``materialize_list`` (idempotente por design), criar lista
    # explicitamente com nome repetido é erro do cliente, não um upsert.
    if db.query(MemberList).filter(MemberList.list_name == payload.list_name).first():
        raise HTTPException(
            status_code=409,
            detail={
                "error": "list_name_taken",
                "message": "Já existe uma lista com esse nome.",
            },
        )
    row = MemberList(list_name=payload.list_name, description=payload.description)
    try:
        db.add(row)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _integrity_error(exc) from exc
    return _serialize_list(row, db)


@router.get("")
def list_lists(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: DbSession = Depends(get_db),
) -> dict:
    """Todas as listas com a contagem de membros de cada uma."""
    query = db.query(MemberList)
    total = query.count()
    rows = query.order_by(MemberList.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "lists": [_serialize_list(r, db) for r in rows],
    }


# --------------------------------------------------------------------------
# Segmentação (Tarefa 3.2) — rotas estáticas antes de /{list_id}
# --------------------------------------------------------------------------
@router.get("/preview")
def preview_filter(
    has_username: Optional[bool] = None,
    status: Optional[str] = None,
    status_in: Optional[list[str]] = Query(None),
    last_seen_after: Optional[str] = None,
    last_seen_before: Optional[str] = None,
    user_id_min: Optional[int] = None,
    user_id_max: Optional[int] = None,
    source_group_id: Optional[int] = None,
    in_list_id: Optional[int] = None,
    not_in_list_id: Optional[int] = None,
    mode: str = Query(MODE_AND, pattern=f"^({MODE_AND}|{MODE_OR})$"),
) -> dict:
    """Conta + amostra do filtro, sem gravar nada (dry-run da segmentação)."""
    filt = _build_filter(
        has_username=has_username,
        status=status,
        status_in=status_in,
        last_seen_after=last_seen_after,
        last_seen_before=last_seen_before,
        user_id_min=user_id_min,
        user_id_max=user_id_max,
        source_group_id=source_group_id,
        in_list_id=in_list_id,
        not_in_list_id=not_in_list_id,
        mode=mode,
    )
    return preview(filt)


@router.post("/segment", status_code=201)
def segment(payload: SegmentIn) -> dict:
    """Aplica o filtro e materializa o resultado em uma ``MemberList``.

    A contagem devolvida bate exatamente com o filtro aplicado.
    """
    filt = MemberFilter(
        has_username=payload.has_username,
        status=payload.status,
        status_in=payload.status_in,
        last_seen_after=_parse_dt(payload.last_seen_after, "last_seen_after"),
        last_seen_before=_parse_dt(payload.last_seen_before, "last_seen_before"),
        user_id_min=payload.user_id_min,
        user_id_max=payload.user_id_max,
        source_group_id=payload.source_group_id,
        in_list_id=payload.in_list_id,
        not_in_list_id=payload.not_in_list_id,
        mode=payload.mode,
    )
    return materialize_list(
        filt, payload.list_name, payload.description, replace=payload.replace
    )


# --------------------------------------------------------------------------
# Detalhe / update / delete (Tarefa 3.3)
# --------------------------------------------------------------------------
@router.get("/{list_id}")
def get_list(list_id: int, db: DbSession = Depends(get_db)) -> dict:
    """Detalhe de uma lista."""
    return _serialize_list(_get_list_or_404(list_id, db), db)


@router.patch("/{list_id}")
def update_list(list_id: int, payload: ListUpdateIn, db: DbSession = Depends(get_db)) -> dict:
    """Renomeia a lista e/ou atualiza a descrição."""
    row = _get_list_or_404(list_id, db)
    if payload.list_name is not None:
        row.list_name = payload.list_name
    if payload.description is not None:
        row.description = payload.description
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _integrity_error(exc) from exc
    return _serialize_list(row, db)


@router.delete("/{list_id}", status_code=204)
def delete_list(list_id: int, db: DbSession = Depends(get_db)) -> None:
    """Exclui a lista — as linhas de ``list_members`` saem por CASCADE.

    409 se existir campanha apontando para ela (FK ``RESTRICT`` no modelo).
    """
    row = _get_list_or_404(list_id, db)
    campaigns = db.query(Campaign).filter(Campaign.target_list_id == row.id).count()
    if campaigns:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "list_in_use",
                "message": f"A lista é alvo de {campaigns} campanha(s). Remova-as antes.",
                "campaigns": campaigns,
            },
        )
    try:
        # O worker insere/remove em ``list_members`` via Core, então a
        # collection cacheada no ORM pode estar defasada e o cascade tentaria
        # apagar linhas que já não existem (StaleDataError). Apagar as
        # associações e expirar o objeto força o ORM a reler do banco.
        db.execute(
            list_members.delete().where(list_members.c.member_list_id == row.id)
        )
        db.flush()
        db.expire(row, ["members"])
        db.delete(row)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _integrity_error(exc) from exc


# --------------------------------------------------------------------------
# Membros da lista (Tarefa 3.4)
# --------------------------------------------------------------------------
@router.get("/{list_id}/members")
def get_list_members(
    list_id: int,
    status: Optional[str] = None,
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: DbSession = Depends(get_db),
) -> dict:
    """Membros da lista, opcionalmente filtrados por ``status``."""
    row = _get_list_or_404(list_id, db)
    member_ids = _member_ids_in_list(row.id, db)

    query = db.query(Member).filter(Member.id.in_(member_ids)) if member_ids \
        else db.query(Member).filter(False)
    if status:
        query = query.filter(Member.status == status)
    total = query.count()
    rows = query.order_by(Member.id).offset(offset).limit(limit).all()
    return {
        "list_id": row.id,
        "list_name": row.list_name,
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
                "source_group_id": m.source_group_id,
            }
            for m in rows
        ],
    }


@router.post("/{list_id}/members", status_code=200)
def add_list_members(list_id: int, payload: MembersIn, db: DbSession = Depends(get_db)) -> dict:
    """Adiciona membros à lista. Idempotente: repetir não cria linha nova."""
    row = _get_list_or_404(list_id, db)
    unknown = [mid for mid in payload.member_ids if db.get(Member, mid) is None]
    if unknown:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "member_not_found",
                "message": "Membro(s) inexistente(s).",
                "member_ids": unknown,
            },
        )
    result = add_members_to_list(row.id, payload.member_ids)
    result["list_name"] = row.list_name
    return result


@router.delete("/{list_id}/members", status_code=200)
def delete_list_members(
    list_id: int, payload: MembersIn, db: DbSession = Depends(get_db)
) -> dict:
    """Remove membros da lista. Idempotente: ID ausente é ignorado."""
    row = _get_list_or_404(list_id, db)
    result = remove_members_from_list(row.id, payload.member_ids)
    result["list_name"] = row.list_name
    return result


__all__ = ["router"]



