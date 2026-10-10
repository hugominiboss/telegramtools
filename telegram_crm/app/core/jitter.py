"""Jitter e mimetismo humano — reduz padrões detectáveis como robóticos.

Regras aplicadas:
  * Intervalos aleatórios (jitter) entre ações, nunca valores fixos.
  * "Pensamento" humano antes de enviar (pausa maior no primeiro envio).
  * Digitação simulada em mensagens longas.
  * Quebra de lote: pausa longa a cada N mensagens.
  * Números de telefone/usernames nunca recebem o mesmo atraso duas vezes
    seguidas (evita assinatura temporal perfeita).
"""

from __future__ import annotations

import asyncio
import random
from typing import Optional

# Faixas (segundos) recomendadas para ações de envio no Telegram.
SEND_MIN = 2.5
SEND_MAX = 9.0
THINK_MIN = 0.8
THINK_MAX = 3.0
BATCH_SIZE = 15
BATCH_PAUSE_MIN = 45.0
BATCH_PAUSE_MAX = 120.0
TYPING_CHARS_PER_SECOND = 12.0
TYPING_MAX_SECONDS = 6.0

_last_delay: dict[str, float] = {}


def jitter(low: float = SEND_MIN, high: float = SEND_MAX, key: str = "") -> float:
    """Retorna um atraso aleatório dentro de [low, high].

    Se ``key`` for informado, garante que o valor não se repita duas vezes
    seguidas para a mesma chave (ex.: telefone da conta).
    """
    if high < low:
        low, high = high, low
    if high - low < 1e-6:
        return low
    for _ in range(8):  # poucas tentativas; colisão é estatisticamente rara
        value = round(random.uniform(low, high), 2)
        if not key or value != _last_delay.get(key):
            break
    if key:
        _last_delay[key] = value
    return value


async def human_pause(
    low: float = SEND_MIN,
    high: float = SEND_MAX,
    key: str = "",
    reason: str = "",
) -> float:
    """Pausa assíncrona com jitter. Retorna o tempo realmente esperado."""
    seconds = jitter(low, high, key)
    if reason:
        from app.core.status import status  # import tardio evita ciclo

        status.task(f"JITTER: {reason}", task_pct=50)
    await asyncio.sleep(seconds)
    return seconds


async def think(account_key: str = "") -> None:
    """Pausa curta 'de raciocínio' antes de uma ação sensível."""
    await human_pause(THINK_MIN, THINK_MAX, key=f"think:{account_key}")


def typing_delay(text: str) -> float:
    """Tempo simulado de digitação, limitado para não atrasar demais."""
    if not text:
        return 0.0
    seconds = len(text) / TYPING_CHARS_PER_SECOND
    return round(min(seconds, TYPING_MAX_SECONDS), 2)


def is_batch_boundary(index: int) -> bool:
    """True quando ``index`` (0-based) fecha um lote — hora de pausa longa."""
    return (index + 1) % BATCH_SIZE == 0


async def batch_pause(account_key: str = "") -> None:
    """Pausa longa entre lotes (simula afastamento do teclado)."""
    await human_pause(BATCH_PAUSE_MIN, BATCH_PAUSE_MAX, key=f"batch:{account_key}")


def next_action_delay(
    index: int, account_key: str = "", first: bool = False
) -> Optional[float]:
    """Calcular (sem dormir) o próximo atraso — útil para prever ETA na API."""
    if first:
        return jitter(THINK_MIN, THINK_MAX, key=f"think:{account_key}")
    if is_batch_boundary(index):
        return jitter(BATCH_PAUSE_MIN, BATCH_PAUSE_MAX, key=f"batch:{account_key}")
    return jitter(SEND_MIN, SEND_MAX, key=account_key)


__all__ = [
    "jitter",
    "human_pause",
    "think",
    "typing_delay",
    "is_batch_boundary",
    "batch_pause",
    "next_action_delay",
    "SEND_MIN",
    "SEND_MAX",
    "BATCH_SIZE",
]
