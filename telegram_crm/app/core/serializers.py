"""Serialização de enums SQLAlchemy/Python para strings estáveis (``active``)."""

from __future__ import annotations

import enum


def sv(value: object) -> str:
    """Retorna o valor textual de um Enum (ou a própria string)."""
    if isinstance(value, enum.Enum):
        return str(value.value)
    if value is None:
        return ""
    text = str(value)
    # "AccountStatus.ACTIVE" -> "ACTIVE" (fallback para enums não resolvidos)
    if "." in text and not text.startswith("{"):
        text = text.rsplit(".", 1)[-1]
    return text.lower() if text.isupper() else text


__all__ = ["sv"]
