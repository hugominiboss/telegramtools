"""Validação do Scraper de estrutura (workers/scrapers.py) — sem Telegram real.

Roda:  python tests/test_scrapers.py
Cobre (Tarefas 2.3 e 2.4 do blueprint):
  * sync_topics: mock com 2 tópicos → 2 linhas únicas ``(group_id, topic_id)``;
  * idempotência: 2ª execução mantém 2 linhas (não duplica);
  * grupo que NÃO é fórum → 0 tópicos gravados (sem erro);
  * sync_members: participantes com ``status``/``last_seen`` em ``members``;
  * separação de pipelines: ``leads`` permanece intocada.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Banco ISOLADO: evita colidir com o botcashgain.db usado por test_pipeline.
os.environ.setdefault("DATABASE_URL", "sqlite:///./_scraper_check.db")
try:
    os.remove("_scraper_check.db")
except OSError:
    pass

from telethon.tl.functions.messages import GetForumTopicsRequest  # noqa: E402
from telethon.tl.types import (  # noqa: E402
    ForumTopic,
    User,
    UserStatusOffline,
    UserStatusOnline,
    UserStatusRecently,
)
from telethon.tl.types.messages import ForumTopics  # noqa: E402

from app.database.database import Base, get_engine, get_session_factory  # noqa: E402
from app.database.models import (  # noqa: E402
    Account,
    Group,
    Lead,
    Member,
    Topic,
)
from app.workers.registry import create_job, get_job, run_job  # noqa: E402
from app.workers.scrapers import (  # noqa: E402
    member_status,
    run_scrape_job,
    sync_members,
    sync_topics,
    upsert_group,
)

GROUP_TG_ID = -1001234567890


def make_topic(topic_id: int, title: str) -> MagicMock:
    """Mock que passa em ``isinstance(x, ForumTopic)`` (campos mínimos)."""
    topic = MagicMock(spec=ForumTopic)
    topic.id = topic_id
    topic.title = title
    topic.date = datetime.now(timezone.utc)
    topic.top_message = topic_id * 10
    return topic


def make_user(uid: int, username: str | None, status) -> User:
    return User(
        id=uid,
        first_name=f"User{uid}",
        last_name=None,
        username=username,
        phone=None,
        bot=False,
        status=status,
    )


class FakeForumEntity:
    """Entidade de fórum (supergrupo com tópicos)."""

    def __init__(self, forum: bool = True) -> None:
        self.id = GROUP_TG_ID
        self.title = "Fórum de Teste"
        self.forum = forum
        self.megagroup = True


class FakeClient:
    """Cliente Telethon fake: devolve tópicos e participantes."""

    def __init__(self, topics=None, users=None, forum: bool = True) -> None:
        self._topics = topics or []
        self._users = users or []
        self._forum = forum
        self.requests: list = []
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
        return FakeForumEntity(forum=self._forum)

    async def __call__(self, request):
        assert isinstance(request, GetForumTopicsRequest), type(request)
        self.requests.append(request)
        return ForumTopics(
            count=len(self._topics),
            topics=self._topics,
            messages=[],
            chats=[],
            users=[],
            pts=0,
        )

    async def iter_participants(self, entity, limit=None):
        for user in (self._users[:limit] if limit else self._users):
            yield user


def _seed_account() -> None:
    S = get_session_factory()
    s = S()
    s.query(Lead).delete()
    s.query(Member).delete()
    s.query(Topic).delete()
    s.query(Group).delete()
    s.query(Account).delete()
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


async def wait_job(job_id: int, timeout: float = 20.0) -> dict:
    info: dict = {}
    for _ in range(int(timeout * 5)):
        await asyncio.sleep(0.2)
        info = get_job(job_id) or {}
        if info.get("status") not in ("pending", "running"):
            return info
    return info


# --------------------------------------------------------------------------
# 1) TAREFA 2.3 — sync_topics: 2 tópicos → 2 linhas únicas
# --------------------------------------------------------------------------
async def test_sync_topics() -> None:
    group_pk = upsert_group(GROUP_TG_ID, "Fórum de Teste", True)
    topics = [make_topic(11, "Geral"), make_topic(12, "Dúvidas")]
    fake = FakeClient(topics=topics)

    with patch("app.workers.scrapers.human_pause", new=AsyncMock()):
        result = await sync_topics(fake, FakeForumEntity(), limit=10, group_row_id=group_pk)

    assert result["forum"] is True, result
    assert result["synced"] == 2, f"synced={result['synced']}"
    assert len(fake.requests) == 1, f"requests={len(fake.requests)}"

    S = get_session_factory()
    s = S()
    rows = s.query(Topic).filter(Topic.group_id == group_pk).all()
    s.close()
    assert len(rows) == 2, f"linhas em topics = {len(rows)}"
    keys = {(r.group_id, r.topic_id) for r in rows}
    assert keys == {(group_pk, 11), (group_pk, 12)}, keys
    print("[OK] 2.3 sync_topics: 2 tópicos → 2 linhas únicas (group_id, topic_id)")

    # ---- IDEMPOTÊNCIA: 2ª execução mantém 2 -------------------------------
    with patch("app.workers.scrapers.human_pause", new=AsyncMock()):
        again = await sync_topics(fake, FakeForumEntity(), limit=10, group_row_id=group_pk)

    s = S()
    total = s.query(Topic).filter(Topic.group_id == group_pk).count()
    s.close()
    assert total == 2, f"idempotência quebrada: {total} tópicos"
    assert again["synced"] == 2, again["synced"]
    assert all(t["is_new"] is False for t in again["topics"]), again["topics"]
    print("[OK] 2.3 idempotente: 2ª execução mantém 2 linhas (nenhuma nova)")


# --------------------------------------------------------------------------
# 2) Grupo que NÃO é fórum → 0 tópicos (sem erro)
# --------------------------------------------------------------------------
async def test_non_forum_group() -> None:
    group_pk = upsert_group(-100999, "Grupo Comum", False)
    fake = FakeClient(topics=[make_topic(1, "Ignorado")])

    with patch("app.workers.scrapers.human_pause", new=AsyncMock()):
        result = await sync_topics(fake, FakeForumEntity(forum=False), limit=10,
                                   group_row_id=group_pk)

    assert result["forum"] is False, result
    assert result["synced"] == 0, result["synced"]
    assert len(fake.requests) == 0, "não deveria chamar o Telegram em grupo não-fórum"

    S = get_session_factory()
    s = S()
    total = s.query(Topic).filter(Topic.group_id == group_pk).count()
    s.close()
    assert total == 0, f"topics gravados indevidamente: {total}"
    print("[OK] 2.3 grupo não-fórum: 0 tópicos, nenhuma chamada ao Telegram")


# --------------------------------------------------------------------------
# 3) TAREFA 2.4 — sync_members: status/last_seen em ``members``
# --------------------------------------------------------------------------
async def test_sync_members() -> None:
    group_pk = upsert_group(GROUP_TG_ID, "Fórum de Teste", True)
    now = datetime.now(timezone.utc)
    users = [
        make_user(2001, "ana", UserStatusOffline(was_online=now)),
        make_user(2002, None, UserStatusRecently()),
        make_user(2003, "carla", UserStatusOnline(expires=now)),
    ]
    fake = FakeClient(users=users)

    with patch("app.workers.scrapers.human_pause", new=AsyncMock()):
        result = await sync_members(fake, FakeForumEntity(), limit=10,
                                    group_row_id=group_pk)

    assert result["synced"] == 3, f"synced={result['synced']}"

    S = get_session_factory()
    s = S()
    rows = s.query(Member).filter(Member.source_group_id == group_pk).all()
    s.close()
    assert len(rows) == 3, f"linhas em members = {len(rows)}"
    assert {r.user_id for r in rows} == {2001, 2002, 2003}

    by_user = {r.user_id: r for r in rows}
    # UserStatusOffline → last_seen é o was_online real.
    assert by_user[2001].status == "active", by_user[2001].status
    assert by_user[2001].last_seen is not None, "last_seen deveria vir de was_online"
    # Recently → ativo, sem timestamp exato.
    assert by_user[2002].last_seen is None, by_user[2002].last_seen
    # Online → last_seen = agora.
    assert by_user[2003].last_seen is not None, "online deveria marcar last_seen"
    assert by_user[2003].username == "carla"
    print("[OK] 2.4 sync_members: 3 membros com status/last_seen persistidos")

    # ---- IDEMPOTÊNCIA: 2ª execução não duplica ----------------------------
    with patch("app.workers.scrapers.human_pause", new=AsyncMock()):
        again = await sync_members(fake, FakeForumEntity(), limit=10,
                                   group_row_id=group_pk)

    s = S()
    total = s.query(Member).filter(Member.source_group_id == group_pk).count()
    s.close()
    assert total == 3, f"idempotência quebrada: {total} membros"
    assert again["synced"] == 3, again["synced"]
    assert all(m["is_new"] is False for m in again["members"]), again["members"]
    print("[OK] 2.4 idempotente: 2ª execução mantém 3 linhas (nenhuma nova)")


# --------------------------------------------------------------------------
# 4) member_status: tradução dos UserStatus do Telethon
# --------------------------------------------------------------------------
async def test_member_status_mapping() -> None:
    now = datetime.now(timezone.utc)
    assert member_status(make_user(1, None, UserStatusOffline(was_online=now))) == (
        "active", now,
    )
    assert member_status(make_user(2, None, UserStatusRecently())) == ("active", None)

    online_status, online_seen = member_status(
        make_user(3, None, UserStatusOnline(expires=now))
    )
    assert online_status == "active" and online_seen is not None

    deleted = User(id=4, first_name="X", username=None, phone=None, bot=False, deleted=True)
    assert member_status(deleted)[0] == "deleted"
    print("[OK] 2.4 member_status: Offline/Recently/Online/deleted mapeados")


# --------------------------------------------------------------------------
# 5) Separação de pipelines: ``leads`` permanece intocada
# --------------------------------------------------------------------------
async def test_leads_untouched() -> None:
    _seed_account()  # limpa a base e garante conta ativa para o job
    S = get_session_factory()
    s = S()
    before = s.query(Lead).count()
    s.close()

    fake = FakeClient(
        topics=[make_topic(21, "Geral")],
        users=[make_user(3001, "dan", UserStatusRecently())],
    )
    with patch("app.workers.scrapers.ClientFactory.from_session_string", return_value=fake), \
         patch("app.workers.scrapers.human_pause", new=AsyncMock()):
        job_id = create_job("scraper", {"group": "-1001234567890",
                                        "topic_limit": 10, "member_limit": 10})
        await run_job(job_id, run_scrape_job)
        info = await wait_job(job_id, timeout=10)

    assert info["status"] == "done", f"status={info['status']} err={info['error']}"

    s = S()
    after = s.query(Lead).count()
    s.close()
    assert after == before, f"scraper escreveu em leads: {before} → {after}"
    print(f"[OK] separação de pipelines: leads intacto ({after} linhas)")


# --------------------------------------------------------------------------
# 6) Job completo: groups → topics → members
# --------------------------------------------------------------------------
async def test_full_scrape_job() -> None:
    _seed_account()  # limpa a base e garante conta ativa para o job
    topics = [make_topic(31, "Regras"), make_topic(32, "Off-topic")]
    users = [
        make_user(4001, "eli", UserStatusOffline(was_online=datetime.now(timezone.utc))),
        make_user(4002, "fabi", UserStatusRecently()),
    ]
    fake = FakeClient(topics=topics, users=users)

    with patch("app.workers.scrapers.ClientFactory.from_session_string", return_value=fake), \
         patch("app.workers.scrapers.human_pause", new=AsyncMock()):
        job_id = create_job("scraper", {"group": "https://t.me/s/teste",
                                        "topic_limit": 10, "member_limit": 10})
        task = await run_job(job_id, run_scrape_job)
        await task

    info = await wait_job(job_id, timeout=10)
    assert info["status"] == "done", f"status={info['status']} err={info['error']}"
    result = (info.get("params") or {}).get("result") or {}
    assert result.get("is_forum") is True, result
    assert result.get("topics_synced") == 2, result.get("topics_synced")
    assert result.get("members_synced") == 2, result.get("members_synced")
    assert result.get("group_title") == "Fórum de Teste", result.get("group_title")

    S = get_session_factory()
    s = S()
    groups = s.query(Group).count()
    group = s.query(Group).filter(Group.group_id == GROUP_TG_ID).first()
    assert groups == 1, f"groups duplicados: {groups}"
    assert group is not None and group.is_forum, "group deveria existir e ser fórum"
    assert s.query(Topic).filter(Topic.group_id == group.id).count() == 2
    assert s.query(Member).filter(Member.source_group_id == group.id).count() == 2
    s.close()
    print(f"[OK] job completo: 1 grupo + 2 tópicos + 2 membros (job {job_id})")


async def main() -> int:
    Base.metadata.create_all(get_engine())
    await test_sync_topics()
    await test_non_forum_group()
    await test_sync_members()
    await test_member_status_mapping()
    # Os testes de job precisam de uma conta ativa (pick_account) — cada um
    # chama ``_seed_account()`` para partir de uma base limpa.
    await test_leads_untouched()
    await test_full_scrape_job()
    print("\nALL SCRAPER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))



