"""Rotas de autenticação e listagem de contas."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import (
    AccountsResponse,
    AccountOut,
    CompleteLoginRequest,
    CompleteLoginResponse,
    StartLoginRequest,
    StartLoginResponse,
)
from app.core.session_manager import LoginError, session_manager
from app.database.database import get_db
from app.database.models import Account

router = APIRouter(prefix="/auth", tags=["auth"])


def _http_error(exc: LoginError) -> HTTPException:
    """Mapeia LoginError → HTTPException com corpo padronizado {error, message}."""
    status = {
        "invalid_api": 400,
        "invalid_phone": 400,
        "invalid_code": 400,
        "expired_code": 400,
        "missing_code": 400,
        "invalid_password": 400,
        "password_required": 403,
        "no_pending_login": 410,       # registro expirou
        "flood_wait": 429,
        "account_not_found": 404,
        "session_invalid": 401,
    }.get(exc.code, 500)
    return HTTPException(status_code=status, detail=exc.to_dict())


@router.post("/start", response_model=StartLoginResponse)
async def start_login(payload: StartLoginRequest) -> StartLoginResponse:
    """Etapa 1: envia o código e devolve o phone_code_hash."""
    try:
        result = await session_manager.start_login(
            phone=payload.phone, api_id=payload.api_id, api_hash=payload.api_hash
        )
    except LoginError as exc:
        raise _http_error(exc) from exc
    return StartLoginResponse(**result)


@router.post("/complete", response_model=CompleteLoginResponse)
async def complete_login(
    payload: CompleteLoginRequest, db: Session = Depends(get_db)
) -> CompleteLoginResponse:
    """Etapa 2: valida o código (e 2FA), gera a StringSession e salva a conta."""
    # `db` não é usado diretamente (persistência roda em thread própria),
    # mas garante que o pool de conexões está saudável antes do processamento.
    try:
        result = await session_manager.complete_login(
            phone=payload.phone,
            phone_code_hash=payload.phone_code_hash,
            code=payload.code,
            password=payload.password,
        )
    except LoginError as exc:
        raise _http_error(exc) from exc
    return CompleteLoginResponse(**result)


# --------------------------------------------------------------------------
# GET /accounts — lista contas cadastradas
# --------------------------------------------------------------------------
accounts_router = APIRouter(tags=["accounts"])


@accounts_router.get("/accounts", response_model=AccountsResponse)
def list_accounts(db: Session = Depends(get_db)) -> AccountsResponse:
    rows = db.query(Account).order_by(Account.id).all()
    items = [
        AccountOut(
            id=a.id,
            phone=a.phone,
            api_id=a.api_id,
            status=a.status.value if hasattr(a.status, "value") else str(a.status),
            proxy=a.proxy,
            has_session=bool(a.session_string),
            created_at=a.created_at,
        )
        for a in rows
    ]
    return AccountsResponse(total=len(items), accounts=items)

