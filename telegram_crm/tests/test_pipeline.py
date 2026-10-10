"""Validação offline do pipeline BotCashGain (sem rede / sem Telegram real).

Roda:  python tests/test_pipeline.py
Cobre: harvest (mock) → leads persistidos → send_bait (mock) → progresso.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unittest.mock import AsyncMock, patch

from telethon.tl.types import User

from app.database.database import Base, get_engine
from app.database.models import Account, Lead, LeadStatus
from app.workers.registry import create_job, get_job, run_job
from app.workers.harvester import run_harvest_job
from app.workers.sender import run_send_job, set_bait_message


def make_user(uid: int, username: str, first: str) -> User:
    return User(
        id=uid,
        first_name=first,
        last_name=None,
        username=username,
        phone=None,
        bot=False,
    )


class FakeClient:
    """Cliente Telethon fake: devolve 10 participantes e 'envia' mensagens."""

    def __init__(self, users: list[User]) -> None:
        self._users = users
        self.sent: list[tuple[int, str]] = []
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected

    async def disconnect(self) -> None:
        self._connected = False

    async def is_user_authorized(self) -> bool:
        return True

    async def get_entity(self, ref):
        class _Entity:
            id = -1001234567890
            title = "Grupo de Teste"

        return _Entity()

    async def iter_participants(self, entity, limit=None):
        for user in (self._users[:limit] if limit else self._users):
            yield user

    async def send_message(self, peer, text, **kwargs):
        self.sent.append((peer, text))
        return True


async def wait_job(job_id: int, timeout: float = 20.0) -> dict:
    """Aguarda o job sair de pending/running (poll no registry)."""
    from app.workers.registry import get_job

    info: dict = {}
    for _ in range(int(timeout * 5)):
        await asyncio.sleep(0.2)
        info = get_job(job_id) or {}
        if info.get("status") not in ("pending", "running"):
            return info
    return info


async def main() -> int:
    Base.metadata.create_all(get_engine())

    users = [make_user(1000 + i, f"user{i}", f"Lead{i}") for i in range(10)]

    # ---- limpa estado anterior -------------------------------------------
    from app.database.database import get_session_factory

    S = get_session_factory()
    s = S()
    s.query(Lead).delete()
    s.commit()
    if not s.query(Account).first():
        s.add(
            Account(
                phone="+5511000000001",
                api_id=1,
                api_hash="a" * 32,
                session_string="fake-session",
                proxy="socks5://aumhbgky-rotate:n1bozkgro41m@p.webshare.io:80",
            )
        )
        s.commit()
    s.close()

    set_bait_message("Oi {first_name}, tudo bem? (@{username})")

    fake = FakeClient(users)

    # ---- 1) HARVEST -------------------------------------------------------
    with patch("app.workers.harvester.ClientFactory.from_session_string", return_value=fake), \
         patch("app.workers.harvester.human_pause", new=AsyncMock()):
        job_id = create_job("harvester", {"group": "https://t.me/s/teste", "limit": 10})
        task = await run_job(job_id, run_harvest_job)
        await task
    info = await wait_job(job_id, timeout=2)
    assert info["status"] == "done", f"harvest status={info['status']} err={info['error']}"
    assert info["processed"] == 10, f"processed={info['processed']}"

    s = S()
    total_leads = s.query(Lead).count()
    s.close()
    assert total_leads == 10, f"leads no banco = {total_leads}"
    print(f"[OK] harvest: 10 leads extraídos (job {job_id})")

    # ---- 2) HARVEST IDEMPOTENTE ------------------------------------------
    with patch("app.workers.harvester.ClientFactory.from_session_string", return_value=fake), \
         patch("app.workers.harvester.human_pause", new=AsyncMock()):
        job2 = create_job("harvester", {"group": "https://t.me/s/teste", "limit": 10})
        task2 = await run_job(job2, run_harvest_job)
        await task2
    info2 = await wait_job(job2, timeout=2)
    assert info2["status"] == "done", f"harvest2 status={info2['status']} err={info2['error']}"

    s = S()
    total_leads = s.query(Lead).count()
    s.close()
    assert total_leads == 10, f"idempotência quebrou: {total_leads} leads"
    print("[OK] harvest idempotente: sem duplicatas")

    # ---- 3) SEND ----------------------------------------------------------
    with patch("app.workers.sender.ClientFactory.from_session_string", return_value=fake), \
         patch("app.workers.sender.human_pause", new=AsyncMock()), \
         patch("app.workers.sender.think", new=AsyncMock()), \
         patch("app.workers.sender.batch_pause", new=AsyncMock()), \
         patch("app.workers.sender.typing_delay", return_value=0):
        job3 = create_job("mass_sender", {"limit": 10})
        task3 = await run_job(job3, run_send_job)
        await task3
    info3 = await wait_job(job3, timeout=2)
    assert info3["status"] == "done", f"sender status={info3['status']} err={info3['error']}"
    assert len(fake.sent) == 10, f"enviados = {len(fake.sent)}"
    assert "Oi Lead0" in fake.sent[0][1], fake.sent[0][1]

    s = S()
    sent = s.query(Lead).filter(Lead.status == LeadStatus.SENT).count()
    s.close()
    assert sent == 10, f"leads sent = {sent}"
    print(f"[OK] sender: 10 iscas enviadas (job {job3})")

    print("\nALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
