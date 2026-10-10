"""Core de autenticação: SessionManager e ClientFactory.

Fluxo de login em duas etapas (assíncrono, sem arquivos .session):

1. ``start_login(phone, api_id, api_hash)``  → conecta, envia o código e
   devolve o ``phone_code_hash`` (guardado em memória no registro pendente).
2. ``complete_login(phone_code_hash, code, password=None)`` → valida o código
   (e 2FA), gera a ``StringSession`` e grava na tabela ``accounts``.

O ``ClientFactory`` instancia clientes prontos a partir do ``session_string``
armazenado no banco — sem novo login.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeEmptyError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from app.database.database import get_session_factory
from app.database.models import Account, AccountStatus

logger = logging.getLogger(__name__)

# Registro pendente expira em 10 minutos (tempo típico de validação do código).
PENDING_TTL_SECONDS = 600


class LoginError(Exception):
    """Erro de login com código estável + mensagem amigável para a API."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}


@dataclass
class PendingLogin:
    """Estado mantido entre as duas etapas do login."""

    client: TelegramClient
    phone: str
    api_id: int
    api_hash: str
    phone_code_hash: str
    created_at: float


# --------------------------------------------------------------------------
# Proxy: "socks5://user:pass@host:port" → tupla aceita pelo Telethon
# --------------------------------------------------------------------------
def parse_proxy(proxy: Optional[str]) -> Optional[tuple]:
    if not proxy:
        return None
    url = urlparse(proxy)
    scheme = url.scheme.lower()
    mapping = {"socks5": "socks5", "socks4": "socks4", "http": "http", "https": "http"}
    if scheme not in mapping:
        raise LoginError("invalid_proxy", f"Protocolo de proxy não suportado: {scheme}")
    if not url.hostname or not url.port:
        raise LoginError("invalid_proxy", "Proxy precisa estar no formato scheme://host:port")
    return (
        mapping[scheme],
        url.hostname,
        url.port,
        True,  # rdns — resolver DNS pelo proxy
        url.username,
        url.password,
    )


# --------------------------------------------------------------------------
# SessionManager
# --------------------------------------------------------------------------
class SessionManager:
    """Gerencia o fluxo de login assíncrono de múltiplas contas."""

    def __init__(self) -> None:
        # phone → registro pendente entre as etapas 1 e 2
        self._pending: dict[str, PendingLogin] = {}
        self._lock = asyncio.Lock()

    # ---------------- etapa 1 ----------------
    async def start_login(self, phone: str, api_id: int, api_hash: str) -> dict[str, str]:
        """Conecta o cliente, solicita o código e retorna o phone_code_hash."""
        self._purge_expired()
        async with self._lock:
            if phone in self._pending:
                old = self._pending.pop(phone)
                await self._safe_disconnect(old.client)

            client = TelegramClient(StringSession(), api_id, api_hash)
            try:
                await client.connect()
                sent = await client.send_code_request(phone)
            except Exception as exc:  # noqa: BLE001
                await self._safe_disconnect(client)
                raise self._map_error(exc) from exc

            phone_code_hash = getattr(sent, "phone_code_hash", None) or ""
            self._pending[phone] = PendingLogin(
                client=client,
                phone=phone,
                api_id=api_id,
                api_hash=api_hash,
                phone_code_hash=phone_code_hash,
                created_at=time.monotonic(),
            )
            logger.info("Login iniciado para %s", phone)
            return {"phone": phone, "phone_code_hash": phone_code_hash}

    # ---------------- etapa 2 ----------------
    async def complete_login(
        self,
        phone: str,
        phone_code_hash: str,
        code: Optional[str] = None,
        password: Optional[str] = None,
    ) -> dict[str, Any]:
        """Valida o código (e 2FA), gera a StringSession e salva em `accounts`."""
        record = self._pending.get(phone)
        if record is None:
            raise LoginError(
                "no_pending_login",
                "Sessão de login não encontrada ou expirada. "
                "Inicie novamente com /auth/start.",
            )

        client = record.client
        try:
            if code:
                try:
                    await client.sign_in(
                        phone=phone,
                        code=code,
                        phone_code_hash=phone_code_hash or record.phone_code_hash,
                    )
                except SessionPasswordNeededError:
                    if not password:
                        raise LoginError(
                            "password_required",
                            "Esta conta possui verificação em duas etapas (2FA). "
                            "Reenvie informando o campo 'password'.",
                        ) from None
                    await client.sign_in(password=password)
            elif password:
                await client.sign_in(password=password)
            else:
                raise LoginError("missing_code", "Informe o 'code' enviado pelo Telegram.")
        except LoginError:
            await self._finish(phone, disconnect=False)
            raise
        except Exception as exc:  # noqa: BLE001
            mapped = self._map_error(exc)
            # Código inválido/expirado: mantém o cliente vivo p/ nova tentativa.
            keep = mapped.code in {
                "invalid_code", "expired_code", "password_required", "invalid_password",
            }
            await self._finish(phone, disconnect=not keep)
            raise mapped from exc

        # Sucesso → serializa a sessão e persiste no banco
        session_string = client.session.save()
        me = await client.get_me()
        account = await asyncio.to_thread(
            self._persist_account, phone, record.api_id, record.api_hash, session_string
        )
        await self._finish(phone, disconnect=True)
        logger.info("Conta %s salva (id=%s)", phone, account.id)
        return {
            "id": account.id,
            "phone": account.phone,
            "status": account.status.value,
            "user_id": getattr(me, "id", None),
            "username": getattr(me, "username", None),
        }

    # ---------------- helpers ----------------
    async def _finish(self, phone: str, *, disconnect: bool) -> None:
        """Encerra o registro pendente.

        ``disconnect=True``  → erro terminal/sucesso: remove e desconecta.
        ``disconnect=False`` → erro retentável (código inválido/2FA): mantém
        o registro vivo para o usuário tentar de novo sem reenviar o código.
        """
        if not disconnect:
            return
        record = self._pending.pop(phone, None)
        if record:
            await self._safe_disconnect(record.client)

    @staticmethod
    async def _safe_disconnect(client: TelegramClient) -> None:
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:  # noqa: BLE001 — desconexão nunca deve quebrar o fluxo
            pass

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [
            phone
            for phone, rec in self._pending.items()
            if now - rec.created_at > PENDING_TTL_SECONDS
        ]
        for phone in expired:
            rec = self._pending.pop(phone)
            try:
                if rec.client.is_connected():
                    asyncio.get_event_loop().create_task(
                        self._safe_disconnect(rec.client)
                    )
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _persist_account(
        phone: str, api_id: int, api_hash: str, session_string: str
    ) -> Account:
        """Upsert por phone (executado em thread — SQLAlchemy é síncrono)."""
        Session = get_session_factory()
        session = Session()
        try:
            account = session.query(Account).filter_by(phone=phone).one_or_none()
            if account is None:
                account = Account(phone=phone, api_id=api_id, api_hash=api_hash)
                session.add(account)
            account.api_id = api_id
            account.api_hash = api_hash
            account.session_string = session_string
            account.status = AccountStatus.ACTIVE
            session.commit()
            session.refresh(account)
            session.expunge(account)
            return account
        finally:
            session.close()

    @staticmethod
    def _map_error(exc: Exception) -> LoginError:
        """Converte exceções do Telethon em mensagens claras para a API."""
        if isinstance(exc, LoginError):
            return exc
        if isinstance(exc, PhoneCodeInvalidError):
            return LoginError("invalid_code", "Código de verificação inválido.")
        if isinstance(exc, PhoneCodeExpiredError):
            return LoginError("expired_code", "Código expirado. Solicite um novo código.")
        if isinstance(exc, PhoneCodeEmptyError):
            return LoginError("missing_code", "Código vazio. Informe o código recebido.")
        if isinstance(exc, SessionPasswordNeededError):
            return LoginError(
                "password_required",
                "Conta com verificação em duas etapas (2FA). Informe o 'password'.",
            )
        if isinstance(exc, PasswordHashInvalidError):
            return LoginError("invalid_password", "Senha 2FA incorreta.")
        if isinstance(exc, PhoneNumberInvalidError):
            return LoginError("invalid_phone", "Número de telefone inválido.")
        if isinstance(exc, ApiIdInvalidError):
            return LoginError("invalid_api", "api_id ou api_hash inválidos.")
        if isinstance(exc, FloodWaitError):
            return LoginError(
                "flood_wait",
                f"Limite do Telegram atingido. Aguarde {exc.seconds}s antes de tentar.",
            )
        return LoginError("telegram_error", f"Erro do Telegram: {exc}")


# --------------------------------------------------------------------------
# ClientFactory — clientes prontos a partir do banco (sem novo login)
# --------------------------------------------------------------------------
class ClientFactory:
    """Instancia clientes Telethon autenticados a partir de `accounts`."""

    @staticmethod
    def from_session_string(
        session_string: str, api_id: int, api_hash: str, proxy: Optional[str] = None
    ) -> TelegramClient:
        """Cria (sem conectar) um cliente a partir da StringSession do banco."""
        if not session_string:
            raise LoginError("empty_session", "session_string vazia no banco de dados.")
        try:
            return TelegramClient(
                StringSession(session_string),
                api_id,
                api_hash,
                proxy=parse_proxy(proxy),
            )
        except (ValueError, struct.error) as exc:
            # StringSession corrompida/inválida → erro claro em vez de crash
            raise LoginError(
                "session_invalid",
                f"session_string inválida no banco ({exc}). Refaça o login.",
            ) from exc

    @classmethod
    def from_account(cls, account: Account) -> TelegramClient:
        return cls.from_session_string(
            account.session_string, account.api_id, account.api_hash, account.proxy
        )

    @classmethod
    async def connect_from_account(cls, account: Account) -> TelegramClient:
        """Retorna um cliente já conectado e autorizado (sem interagir login)."""
        client = cls.from_account(account)
        if not client.is_connected():
            await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise LoginError(
                "session_invalid",
                f"Sessão da conta {account.phone} inválida ou revogada. Refaça o login.",
            )
        return client

    @classmethod
    async def connect_by_phone(cls, phone: str) -> TelegramClient:
        """Busca a conta pelo phone no banco e devolve o cliente conectado."""
        Session = get_session_factory()
        session = Session()
        try:
            account = session.query(Account).filter_by(phone=phone).one_or_none()
            if account is None:
                raise LoginError("account_not_found", f"Conta {phone} não cadastrada.")
            session.expunge(account)
        finally:
            session.close()
        return await cls.connect_from_account(account)


# Instância compartilhada pelos endpoints (singleton por processo).
session_manager = SessionManager()

