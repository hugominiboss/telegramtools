"""Validação do Member Adder (workers/adder.py) — sem Telegram real.

Roda:  python -m tests.test_adder
Cobre:
  * _invite_one despacha InviteToChannelRequest (Channel) e AddChatUserRequest (Chat);
  * add_members adiciona N leads pendentes → viram ADDED no banco;
  * PeerFloodError marca a conta limited (failover) e segue com a próxima;
  * erro de privacidade ("user cannot be added") não trava a fila.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Banco ISOLADO: evita colidir com o botcashgain.db usado por test_pipeline.
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./_adder_check.db")
try:
    os.remove("_adder_check.db")
except OSError:
    pass

from telethon.errors import PeerFloodError, UserPrivacyRestrictedError
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.functions.messages import AddChatUserRequest
from telethon.tl.types import Channel, Chat, InputPeerChannel, InputPeerUser
from unittest.mock import MagicMock

from app.database.database import Base, get_engine, get_session_factory
from app.database.models import Account, AccountStatus, Lead, LeadStatus
from app.workers.adder import _invite_one, run_add_job
from app.workers.registry import create_job, run_job


def _channel():
    """Mock que passa em ``isinstance(x, Channel)`` (supergrupo/canal)."""
    m = MagicMock(spec=Channel)
    m.id = -1001234567890
    m.title = "Grupo Alvo"
    return m


def _chat():
    """Mock que passa em ``isinstance(x, Chat)`` (grupo básico)."""
    m = MagicMock(spec=Chat)
    m.id = 123456789
    m.title = "Grupo Basico"
    return m


class FakeClient:
    """Cliente Telethon fake: registra os requests disparados."""

    def __init__(self) -> None:
        self.requests: list = []
        self._connected = False
        # user_id -> erro a levantar no invite (simulação por membro).
        self.raise_for: dict[int, Exception] = {}

    async def connect(self) -> None:
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected

    async def disconnect(self) -> None:
        self._connected = False

    async def is_user_authorized(self) -> bool:
        return True

    async def get_entity(self, ref):
        return _channel()

    async def get_input_entity(self, ref):
        if isinstance(ref, str) or (isinstance(ref, int) and ref < 0):
            return InputPeerChannel(channel_id=1234567890, access_hash=0)
        return InputPeerUser(user_id=int(ref), access_hash=0)

    async def __call__(self, request):
        err = None
        users = getattr(request, "users", None)
        if users:
            err = self.raise_for.get(getattr(users[0], "user_id", None))
        elif getattr(request, "user_id", None) is not None:
            err = self.raise_for.get(getattr(request.user_id, "user_id", None))
        if err is not None:
            raise err
        self.requests.append(request)
        return object()


def _seed(accounts: int = 1, leads: int = 5) -> None:
    S = get_session_factory()
    s = S()
    s.query(Lead).delete()
    s.query(Account).delete()
    for i in range(accounts):
        s.add(
            Account(
                phone=f"+5511{i:08d}",
                api_id=1,
                api_hash="a" * 32,
                session_string=f"fake-{i}",
                proxy=f"socks5://h:{i}@p:80",
                status=AccountStatus.ACTIVE,
            )
        )
    for i in range(leads):
        s.add(Lead(user_id=1000 + i, username=f"user{i}", first_name=f"Lead{i}",
                   source_group_id=9, status=LeadStatus.PENDING))
    s.commit()
    s.close()



async def main() -> int:
    Base.metadata.create_all(get_engine())

    # ---- 1) _invite_one: Channel → InviteToChannelRequest -------------------
    client = FakeClient()
    target_input = await client.get_input_entity("-100123")
    user_input = await client.get_input_entity(1000)
    await _invite_one(client, _channel(), target_input, user_input)
    assert isinstance(client.requests[-1], InviteToChannelRequest), type(client.requests[-1])
    print("[OK] _invite_one(Channel) -> InviteToChannelRequest")

    # Chat (grupo básico) -> AddChatUserRequest
    client = FakeClient()
    await _invite_one(client, _chat(), target_input, user_input)
    assert isinstance(client.requests[-1], AddChatUserRequest), type(client.requests[-1])
    print("[OK] _invite_one(Chat) -> AddChatUserRequest")

    # ---- 2) add_members: 5 leads pendentes → 5 ADDED ------------------------
    _seed(accounts=1, leads=5)
    fake = FakeClient()
    with patch("app.workers.adder.ClientFactory.from_session_string", return_value=fake), \
         patch("app.workers.adder.human_pause", new=AsyncMock()), \
         patch("app.workers.adder.think", new=AsyncMock()), \
         patch("app.workers.adder.batch_pause", new=AsyncMock()):
        jid = create_job("adder", {"target_group": "-100123", "limit": 5})
        task = await run_job(jid, run_add_job)
        await task

    S = get_session_factory()
    s = S()
    added = s.query(Lead).filter(Lead.status == LeadStatus.ADDED).count()
    s.close()
    assert len(fake.requests) == 5, f"requests={len(fake.requests)}"
    assert added == 5, f"leads ADDED = {added}"
    print(f"[OK] add_members: 5 convites enviados, 5 leads ADDED no banco (job {jid})")

    # ---- 3) Privacidade: erro por-membro não trava a fila -------------------
    _seed(accounts=1, leads=3)
    fake = FakeClient()
    fake.raise_for = {1001: UserPrivacyRestrictedError(None)}  # 1º lead barrado
    with patch("app.workers.adder.ClientFactory.from_session_string", return_value=fake), \
         patch("app.workers.adder.human_pause", new=AsyncMock()), \
         patch("app.workers.adder.think", new=AsyncMock()), \
         patch("app.workers.adder.batch_pause", new=AsyncMock()):
        jid = create_job("adder", {"target_group": "-100123", "limit": 3})
        task = await run_job(jid, run_add_job)
        await task
    # 2 convites passaram (1000 e 1002), o 1001 foi barrado por privacidade.
    assert len(fake.requests) == 2, f"requests={len(fake.requests)}"
    S = get_session_factory()
    s = S()
    added = s.query(Lead).filter(Lead.status == LeadStatus.ADDED).count()
    failed = s.query(Lead).filter(Lead.status == LeadStatus.FAILED).count()
    s.close()
    assert added == 2 and failed == 1, f"added={added} failed={failed}"
    print("[OK] privacidade: 1 membro barrado (FAILED), fila seguiu (2 ADDED)")

    # ---- 4) Failover: PeerFlood marca conta limited -------------------------
    _seed(accounts=2, leads=2)
    fake = FakeClient()
    fake.raise_for = {1000: PeerFloodError(None)}  # 1º lead derruba a conta 1
    with patch("app.workers.adder.ClientFactory.from_session_string", return_value=fake), \
         patch("app.workers.adder.human_pause", new=AsyncMock()), \
         patch("app.workers.adder.think", new=AsyncMock()), \
         patch("app.workers.adder.batch_pause", new=AsyncMock()):
        jid = create_job("adder", {"target_group": "-100123", "limit": 2})
        task = await run_job(jid, run_add_job)
        await task
    S = get_session_factory()
    s = S()
    limited = s.query(Account).filter(Account.status == AccountStatus.LIMITED).count()
    s.close()
    assert limited >= 1, f"contas limited = {limited}"
    print(f"[OK] failover: PeerFloodError marcou {limited} conta(s) como LIMITED")

    print("\nALL ADDER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
