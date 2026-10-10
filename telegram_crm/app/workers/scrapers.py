"""Scrapers Worker — estrutura rica do grupo (groups / topics / members).

Complementa o Harvester (pipeline de ``leads``) persistindo o schema
relacional já modelado no blueprint (§1.2):

  * ``groups``  — upsert por ``group_id`` do Telegram (título, ``is_forum``,
                  ``last_scraped``). É a espinha dorsal: ``topics`` e
                  ``members`` referenciam o **id interno** de ``groups``.
  * ``topics``  — tópicos de fóruns via ``messages.GetForumTopicsRequest``
                  com paginação; upsert por ``(group_id, topic_id)``.
  * ``members`` — participantes via ``iter_participants`` com metadados
                  ricos (``status``/``last_seen``); upsert por
                  ``(user_id, source_group_id)``.

Engenharia:
  * **Idempotente por construção** — upsert em chaves naturais: rodar 2× não
    duplica nenhuma linha.
  * ``FloodWaitError`` é respeitado (espera ``e.seconds``) antes de seguir.
  * Jitter humano entre páginas (``human_pause``) para não assinar robô.
  * Grupo que não é fórum simplesmente não gera ``topics`` (0 linhas, sem erro).
  * **Separação de pipelines:** este worker NUNCA escreve em ``leads`` — o
    ciclo de conversão (Sender/Adder) continua dono daquela tabela.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    FloodWaitError,
)
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.tl.types import (
    ForumTopic,
    User,
    UserStatusEmpty,
    UserStatusLastMonth,
    UserStatusLastWeek,
    UserStatusOffline,
    UserStatusOnline,
    UserStatusRecently,
)

from app.core.jitter import human_pause
from app.core.session_manager import ClientFactory, LoginError
from app.core.status import status
from app.database.database import get_session_factory
from app.database.models import Group, Job, Log, Member, Topic
from app.workers.harvester import ensure_proxy, pick_account
from app.workers.registry import _set_job

logger = logging.getLogger(__name__)

# Respiro entre páginas de tópicos/membros (jitter humano).
TOPIC_PAGE_PAUSE = (0.4, 1.4)
MEMBER_PAGE_PAUSE = (0.5, 1.8)

# Tetos padrão do worker de estrutura.
DEFAULT_TOPIC_LIMIT = 100
DEFAULT_MEMBER_LIMIT = 500

# Tamanho máximo de página aceito pelo Telegram para GetForumTopicsRequest.
_TOPIC_PAGE = 50


# --------------------------------------------------------------------------
# Persistência (upserts idempotentes)
# --------------------------------------------------------------------------
def _log(account_id: Optional[int], action: str, target_id: Optional[int], state: str) -> None:
    """Grava uma linha de auditoria na tabela ``logs``."""
    Session = get_session_factory()
    session = Session()
    try:
        session.add(Log(account_id=account_id, action=action, target_id=target_id, status=state))
        session.commit()
    finally:
        session.close()


def upsert_group(group_id: Any, group_title: Optional[str], is_forum: bool = False) -> int:
    """Cria/atualiza a linha em ``groups`` e devolve o **id interno** (PK).

    ``topics.group_id`` e ``members.source_group_id`` apontam para essa PK,
    não para o id do Telegram — por isso ela é devolvida aos chamadores.
    """
    Session = get_session_factory()
    session = Session()
    try:
        row = session.query(Group).filter(Group.group_id == int(group_id)).first()
        if row is None:
            row = Group(
                group_id=int(group_id),
                group_title=(group_title or str(group_id))[:512],
                is_forum=bool(is_forum),
            )
            session.add(row)
            session.flush()
        else:
            if group_title:
                row.group_title = group_title[:512]
            # Uma vez fórum, sempre fórum (não regride a flag em re-syncs).
            row.is_forum = bool(row.is_forum or is_forum)
        row.last_scraped = datetime.now(timezone.utc)
        session.commit()
        return int(row.id)
    finally:
        session.close()


def _upsert_topic(group_row_id: int, topic_id: Any, topic_name: Optional[str]) -> bool:
    """Grava o tópico se ainda não existir em ``(group_id, topic_id)``. True = novo."""
    Session = get_session_factory()
    session = Session()
    try:
        existing = (
            session.query(Topic)
            .filter(Topic.group_id == group_row_id, Topic.topic_id == int(topic_id))
            .first()
        )
        if existing is not None:
            if topic_name and existing.topic_name != topic_name:
                existing.topic_name = topic_name[:512]
            session.commit()
            return False
        session.add(
            Topic(
                group_id=group_row_id,
                topic_id=int(topic_id),
                topic_name=(topic_name or f"Tópico {topic_id}")[:512],
            )
        )
        session.commit()
        return True
    finally:
        session.close()


def _upsert_member(
    group_row_id: int, user: User, status_text: str, last_seen: Optional[datetime]
) -> bool:
    """Grava o membro se ainda não existir em ``(user_id, source_group_id)``. True = novo."""
    Session = get_session_factory()
    session = Session()
    try:
        existing = (
            session.query(Member)
            .filter(Member.user_id == user.id, Member.source_group_id == group_row_id)
            .first()
        )
        username = getattr(user, "username", None)
        if existing is not None:
            # Re-sync atualiza metadados voláteis (status/última vez visto).
            if username and not existing.username:
                existing.username = username
            existing.status = status_text
            if last_seen is not None:
                existing.last_seen = last_seen
            session.commit()
            return False
        session.add(
            Member(
                user_id=user.id,
                username=username,
                status=status_text,
                last_seen=last_seen,
                source_group_id=group_row_id,
            )
        )
        session.commit()
        return True
    finally:
        session.close()


def member_status(user: User) -> tuple[str, Optional[datetime]]:
    """Traduz o ``UserStatus`` do Telethon em ``(status, last_seen)``.

    ``status`` é livre no modelo ('active' | 'left' | 'restricted' | ...):
      * ``UserStatusOnline``  → online agora (``last_seen`` = agora);
      * ``UserStatusOffline`` → ``last_seen`` = ``was_online`` real;
      * ``Recently/LastWeek/LastMonth`` → ativo, sem timestamp exato;
      * conta deletada/restrita → ``deleted`` / ``restricted``.
    """
    now = datetime.now(timezone.utc)
    if getattr(user, "deleted", False):
        return "deleted", None
    if getattr(user, "restricted", False):
        return "restricted", None

    user_status = getattr(user, "status", None)
    if isinstance(user_status, UserStatusOnline):
        return "active", now
    if isinstance(user_status, UserStatusOffline):
        return "active", user_status.was_online
    if isinstance(user_status, (UserStatusRecently, UserStatusLastWeek, UserStatusLastMonth)):
        return "active", None
    if isinstance(user_status, UserStatusEmpty):
        return "active", None
    return "active", None


# --------------------------------------------------------------------------
# Sync de tópicos (fóruns)
# --------------------------------------------------------------------------
async def sync_topics(
    client: Any,
    group: Any,
    limit: int = DEFAULT_TOPIC_LIMIT,
    *,
    job_id: Optional[int] = None,
    group_row_id: Optional[int] = None,
    account_key: str = "",
) -> dict:
    """Sincroniza os tópicos de um fórum para a tabela ``topics``.

    ``group`` é a entidade do Telegram (possui ``id``/``title``/``forum``).
    Grupos que não são fórum retornam imediatamente com ``forum: False``.

    Paginação idempotente via ``offset_topic``/``offset_id``/``offset_date``:
    um conjunto ``seen`` interno garante que nenhum tópico seja contado duas
    vezes mesmo que o Telegram reenvie a página de fronteira.
    """
    group_pk = group_row_id if group_row_id is not None else _entity_group_pk(group)
    is_forum = bool(getattr(group, "forum", False))

    result: dict = {
        "group_id": getattr(group, "id", None),
        "group_row_id": group_pk,
        "forum": is_forum,
        "requested": limit,
        "synced": 0,
        "topics": [],
    }
    if not is_forum:
        return result

    captured: list[dict] = []
    seen: set[int] = set()
    offset_topic = 0
    offset_id = 0
    offset_date: Optional[datetime] = None
    page = min(max(limit, 1), _TOPIC_PAGE)

    while len(captured) < limit:
        response = await client(
            GetForumTopicsRequest(
                peer=group,
                offset_date=offset_date,
                offset_id=offset_id,
                offset_topic=offset_topic,
                limit=page,
            )
        )
        raw = [t for t in (getattr(response, "topics", None) or []) if isinstance(t, ForumTopic)]
        if not raw:
            break

        fresh = [t for t in raw if int(t.id) not in seen]
        if not fresh:
            break  # só repetiu a fronteira — não há mais o que extrair

        for topic in fresh:
            seen.add(int(topic.id))
            if len(captured) >= limit:
                break
            name = getattr(topic, "title", None)
            is_new = await asyncio.to_thread(_upsert_topic, group_pk, topic.id, name)
            captured.append({"topic_id": int(topic.id), "topic_name": name, "is_new": is_new})
            if job_id is not None:
                _set_job(job_id, processed=len(captured))
            if len(captured) % 10 == 0:
                pct = min(95, 30 + int(len(captured) * 65 / max(limit, 1)))
                status.task(f"SCRAPER: {len(captured)}/{limit} tópicos", task_pct=pct)

        last = raw[-1]
        offset_topic = int(getattr(last, "id", 0) or 0)
        offset_id = int(getattr(last, "top_message", 0) or 0)
        offset_date = getattr(last, "date", None)
        if len(raw) < page:
            break  # última página
        await human_pause(*TOPIC_PAGE_PAUSE, key=f"topics:{account_key}")

    result["synced"] = len(captured)
    result["topics"] = captured
    return result


def _entity_group_pk(group: Any) -> int:
    """Atalho: garante a linha em ``groups`` a partir da entidade e devolve a PK."""
    return upsert_group(
        getattr(group, "id", 0),
        getattr(group, "title", None),
        bool(getattr(group, "forum", False)),
    )


# --------------------------------------------------------------------------
# Sync de membros (metadados ricos)
# --------------------------------------------------------------------------
async def sync_members(
    client: Any,
    group: Any,
    limit: int = DEFAULT_MEMBER_LIMIT,
    *,
    job_id: Optional[int] = None,
    group_row_id: Optional[int] = None,
    account_key: str = "",
) -> dict:
    """Sincroniza participantes para ``members`` com ``status``/``last_seen``.

    Paralelo ao ``harvest`` (que alimenta ``leads``): aqui o alvo é a tabela
    relacional ``members``, com metadados de presença. Bots e a própria conta
    são ignorados; o pipeline ``leads`` não é tocado.
    """
    group_pk = group_row_id if group_row_id is not None else _entity_group_pk(group)

    captured: list[dict] = []
    processed = 0
    async for user in client.iter_participants(group, limit=limit):
        if not isinstance(user, User):
            continue
        if getattr(user, "bot", False) or getattr(user, "self", False):
            continue

        status_text, last_seen = member_status(user)
        is_new = await asyncio.to_thread(_upsert_member, group_pk, user, status_text, last_seen)
        captured.append(
            {
                "user_id": user.id,
                "username": getattr(user, "username", None),
                "status": status_text,
                "last_seen": last_seen.isoformat() if last_seen else None,
                "is_new": is_new,
            }
        )
        processed += 1
        if job_id is not None:
            _set_job(job_id, processed=processed)
        if processed % 25 == 0:
            pct = min(95, 30 + int(processed * 65 / max(limit, 1)))
            status.task(f"SCRAPER: {processed}/{limit} membros", task_pct=pct)
        await human_pause(*MEMBER_PAGE_PAUSE, key=f"members:{account_key}")

    return {
        "group_id": getattr(group, "id", None),
        "group_row_id": group_pk,
        "requested": limit,
        "synced": len(captured),
        "members": captured,
    }


# --------------------------------------------------------------------------
# Job completo (groups → topics → members)
# --------------------------------------------------------------------------
async def scrape(
    job_id: int,
    group_ref: str,
    member_limit: int = DEFAULT_MEMBER_LIMIT,
    topic_limit: int = DEFAULT_TOPIC_LIMIT,
    account_id: Optional[int] = None,
    *,
    sync_topics_enabled: bool = True,
    sync_members_enabled: bool = True,
) -> dict:
    """Executa o scraper de estrutura: resolve o grupo e persiste tudo.

    Ordem: ``pick_account`` → ``ClientFactory`` → ``upsert_group`` →
    ``sync_topics`` (só fóruns) → ``sync_members`` → ``logs``.
    """
    account = pick_account(account_id)
    ensure_proxy(account)

    total = (topic_limit if sync_topics_enabled else 0) + (
        member_limit if sync_members_enabled else 0
    )
    _set_job(job_id, total=max(total, 1), processed=0, failed=0, error=None)
    status.task(f"SCRAPER: conectando conta {account.phone}", task_pct=5)

    client = ClientFactory.from_session_string(
        account.session_string, account.api_id, account.api_hash, account.proxy
    )

    topics_result: dict = {"forum": False, "synced": 0, "topics": []}
    members_result: dict = {"synced": 0, "members": []}
    group_pk: Optional[int] = None
    group_id: Optional[int] = None
    group_title: Optional[str] = None

    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise LoginError(
                "session_invalid",
                f"Sessão da conta {account.phone} inválida/revogada.",
            )

        status.task(f"SCRAPER: resolvendo grupo {group_ref}", task_pct=15)
        entity = await client.get_entity(group_ref)
        group_id = getattr(entity, "id", None)
        group_title = getattr(entity, "title", None) or getattr(entity, "first_name", None)

        group_pk = await asyncio.to_thread(
            upsert_group, group_id, group_title, bool(getattr(entity, "forum", False))
        )
        status.task(f"SCRAPER: grupo {group_title or group_ref} registrado", task_pct=25)

        if sync_topics_enabled:
            status.task(f"SCRAPER: lendo tópicos de {group_title or group_ref}", task_pct=30)
            topics_result = await sync_topics(
                client,
                entity,
                topic_limit,
                job_id=job_id,
                group_row_id=group_pk,
                account_key=account.phone,
            )

        if sync_members_enabled:
            status.task(f"SCRAPER: extraindo membros de {group_title or group_ref}", task_pct=40)
            members_result = await sync_members(
                client,
                entity,
                member_limit,
                job_id=job_id,
                group_row_id=group_pk,
                account_key=account.phone,
            )

        _log(account.id, "scrape_group", group_id, "success")
        _set_job(job_id, processed=max(total, 1), total=max(total, 1))
        status.step_done("SCRAPER: estrutura sincronizada", 100)
        return {
            "job_id": job_id,
            "group_id": group_id,
            "group_row_id": group_pk,
            "group_title": group_title,
            "is_forum": topics_result.get("forum", False),
            "topics_synced": topics_result.get("synced", 0),
            "members_synced": members_result.get("synced", 0),
            "topics": topics_result.get("topics", []),
            "members": members_result.get("members", []),
        }
    except FloodWaitError as exc:
        logger.warning("FloodWait de %ss no scraper", exc.seconds)
        _log(account.id, "scrape_group", group_id, "failed")
        _set_job(job_id, error=f"FloodWait {exc.seconds}s — aguardando")
        await asyncio.sleep(min(exc.seconds, 60))
        raise
    except (ChatAdminRequiredError, ChannelPrivateError) as exc:
        _log(account.id, "scrape_group", group_id, "failed")
        _set_job(job_id, error=str(exc))
        raise LoginError(
            "group_unavailable",
            f"Sem acesso ao grupo {group_ref}: {exc.__class__.__name__}",
        ) from exc
    finally:
        if client.is_connected():
            await client.disconnect()


async def run_scrape_job(job_id: int) -> None:
    """Entrada assíncrona registrada em ``registry.run_job``."""
    from app.workers.registry import get_job

    info = get_job(job_id) or {}
    params = info.get("params", {})
    result = await scrape(
        job_id=job_id,
        group_ref=params.get("group") or "",
        member_limit=int(params.get("member_limit", DEFAULT_MEMBER_LIMIT)),
        topic_limit=int(params.get("topic_limit", DEFAULT_TOPIC_LIMIT)),
        account_id=params.get("account_id"),
        sync_topics_enabled=bool(params.get("topics", True)),
        sync_members_enabled=bool(params.get("members", True)),
    )

    # Persiste o resultado completo (visível via GET /admin/jobs/{id}).
    Session = get_session_factory()
    session = Session()
    try:
        job = session.get(Job, job_id)
        if job is not None:
            merged = json.loads(job.params or "{}")
            merged["result"] = result
            job.params = json.dumps(merged, ensure_ascii=False)
            session.commit()
    finally:
        session.close()


__all__ = [
    "sync_topics",
    "sync_members",
    "scrape",
    "run_scrape_job",
    "upsert_group",
    "member_status",
    "DEFAULT_TOPIC_LIMIT",
    "DEFAULT_MEMBER_LIMIT",
]



