"""Pool de proxies SOCKS5 com rotação automática por conta.

Proxy padrão (WebShare — rotação de IP a cada conexão):

    Domain : p.webshare.io
    Port   : 80
    User   : aumhbgky-rotate
    Pass   : n1bozkgro41m

Formato interno/gravado em ``accounts.proxy``::

    socks5://user:pass@host:port

A rotação acontece de duas formas:
  1. O usuário ``-rotate`` do WebShare já troca o IP a cada conexão.
  2. O ``ProxyPool`` distribui as entradas cadastradas em round-robin para
     cada conta (rotação *por conta*), garantindo que duas contas nunca
     compartilhem o mesmo endpoint ao mesmo tempo quando há mais de um.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

DEFAULT_HOST = "p.webshare.io"
DEFAULT_PORT = 80
DEFAULT_USER = "aumhbgky-rotate"
DEFAULT_PASS = "n1bozkgro41m"

# PROXIES — lista separada por vírgula, ex.:
#   socks5://user:pass@host:port,socks5://user:pass@host2:port2
PROXIES_ENV = "PROXIES"


def _default_entry() -> str:
    return f"socks5://{DEFAULT_USER}:{DEFAULT_PASS}@{DEFAULT_HOST}:{DEFAULT_PORT}"


class ProxyPool:
    """Pool round-robin de proxies, com override por env ``PROXIES``."""

    def __init__(self, proxies: Optional[list[str]] = None) -> None:
        if proxies is None:
            raw = os.getenv(PROXIES_ENV, "").strip()
            proxies = [p.strip() for p in raw.split(",") if p.strip()]
        self._proxies: list[str] = proxies or [_default_entry()]
        self._index = 0
        self._assigned: dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    @property
    def proxies(self) -> list[str]:
        return list(self._proxies)

    def add(self, proxy: str) -> None:
        proxy = proxy.strip()
        if not proxy:
            return
        with self._lock:
            if proxy not in self._proxies:
                self._proxies.append(proxy)

    # ------------------------------------------------------------------
    def next_for(self, key: str) -> str:
        """Retorna (e memoriza) o proxy da conta/worker identificado por ``key``.

        Round-robin: cada nova chave recebe o próximo endpoint da lista.
        """
        with self._lock:
            assigned = self._assigned.get(key)
            if assigned and assigned in self._proxies:
                return assigned
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index += 1
            self._assigned[key] = proxy
            return proxy

    def release(self, key: str) -> None:
        with self._lock:
            self._assigned.pop(key, None)

    def as_url(self, proxy: str) -> str:
        """Normaliza para ``socks5://user:pass@host:port`` (formato do Telethon)."""
        return proxy if "://" in proxy else f"socks5://{proxy}"

    def as_tuple(self, proxy: Optional[str]) -> Optional[tuple]:
        """Converte a URL em tupla aceita pelo ``proxy=`` do Telethon."""
        if not proxy:
            return None
        from urllib.parse import urlparse

        url = urlparse(self.as_url(proxy))
        if not url.hostname:
            return None
        return (
            "socks5" if url.scheme in ("socks5", "") else url.scheme,
            url.hostname,
            url.port or DEFAULT_PORT,
            True,  # rdns
            url.username,
            url.password,
        )


# Instância compartilhada.
proxy_pool = ProxyPool()

__all__ = ["ProxyPool", "proxy_pool", "DEFAULT_HOST", "DEFAULT_PORT"]
