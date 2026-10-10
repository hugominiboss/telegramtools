"""Núcleo: SessionManager, clientes Telethon e conexões."""

from app.core.session_manager import (
    ClientFactory,
    LoginError,
    SessionManager,
    parse_proxy,
    session_manager,
)

__all__ = [
    "ClientFactory",
    "LoginError",
    "SessionManager",
    "parse_proxy",
    "session_manager",
]

