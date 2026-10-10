"""Schemas de validação (Pydantic) da API."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# /auth/start
# --------------------------------------------------------------------------
class StartLoginRequest(BaseModel):
    phone: str = Field(..., examples=["+5511999999999"], description="Telefone com DDI")
    api_id: int = Field(..., gt=0, description="api_id do my.telegram.org")
    api_hash: str = Field(..., min_length=8, max_length=64, description="api_hash do my.telegram.org")


class StartLoginResponse(BaseModel):
    phone: str
    phone_code_hash: str
    message: str = "Código enviado. Chame /auth/complete com o code recebido."


# --------------------------------------------------------------------------
# /auth/complete
# --------------------------------------------------------------------------
class CompleteLoginRequest(BaseModel):
    phone: str = Field(..., examples=["+5511999999999"])
    phone_code_hash: str = Field(..., description="Retornado por /auth/start")
    code: Optional[str] = Field(None, description="Código de verificação (5 dígitos)")
    password: Optional[str] = Field(None, description="Senha 2FA, se exigida")


class CompleteLoginResponse(BaseModel):
    id: int
    phone: str
    status: str
    user_id: Optional[int] = None
    username: Optional[str] = None
    message: str = "Conta autenticada e salva."


# --------------------------------------------------------------------------
# /accounts
# --------------------------------------------------------------------------
class AccountOut(BaseModel):
    id: int
    phone: str
    api_id: int
    status: str
    proxy: Optional[str] = None
    has_session: bool
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AccountsResponse(BaseModel):
    total: int
    accounts: list[AccountOut]


# --------------------------------------------------------------------------
# Erros
# --------------------------------------------------------------------------
class ErrorResponse(BaseModel):
    error: str
    message: str
