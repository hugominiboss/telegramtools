"""Registro de jobs em background (asyncio) — dispara e acompanha workers.

Cada job roda como ``asyncio.Task`` no event loop do uvicorn. O estado
(pending → running → done/failed) é persistido na tabela ``jobs`` para que
as APIs de Admin/Client consultem o progresso sem polling em memória.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional

from app.core.serializers import sv
from app.database.database import get_session_factory
from app.database.models import Job, JobStatus, JobType

logger = logging.getLogger(__name__)

# job_id → asyncio.Task
_tasks: dict[int, asyncio.Task] = {}


def _set_job(job_id: int, **fields: Any) -> None:
    """Atualização atômica de colunas do job (roda em thread p/ não bloquear)."""
    Session = get_session_factory()

    def _write() -> None:
        session = Session()
        try:
            job = session.get(Job, job_id)
            if job is None:
                return
            for key, value in fields.items():
                setattr(job, key, value)
            session.commit()
        finally:
            session.close()

    try:
        _write()
    except Exception:  # noqa: BLE001 — persistência não pode matar o loop
        logger.exception("Falha ao atualizar job %s", job_id)


def create_job(job_type: "JobType | str", params: Optional[dict] = None) -> int:
    kind = job_type.value if hasattr(job_type, "value") else str(job_type)
    Session = get_session_factory()
    session = Session()
    try:
        job = Job(job_type=kind, status=JobStatus.PENDING, params=json.dumps(params or {}))
        session.add(job)
        session.commit()
        return int(job.id)
    finally:
        session.close()


def get_job(job_id: int) -> Optional[dict]:
    Session = get_session_factory()
    session = Session()
    try:
        job = session.get(Job, job_id)
        if job is None:
            return None
        return {
            "id": job.id,
            "job_type": sv(job.job_type),
            "status": sv(job.status),
            "total": job.total,
            "processed": job.processed,
            "failed": job.failed,
            "error": job.error,
            "progress_pct": job.progress_pct,
            "params": json.loads(job.params) if job.params else {},
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }
    finally:
        session.close()


def list_jobs(limit: int = 50) -> list[dict]:
    Session = get_session_factory()
    session = Session()
    try:
        rows = session.query(Job).order_by(Job.id.desc()).limit(limit).all()
        return [
            {
                "id": j.id,
                "job_type": sv(j.job_type),
                "status": sv(j.status),
                "total": j.total,
                "processed": j.processed,
                "failed": j.failed,
                "progress_pct": j.progress_pct,
            }
            for j in rows
        ]
    finally:
        session.close()


async def run_job(
    job_id: int,
    coro_factory: Callable[[int], Awaitable[None]],
) -> asyncio.Task:
    """Envolve a rotina do worker em uma Task com tratamento de erro/estado."""

    async def _runner() -> None:
        _set_job(job_id, status=JobStatus.RUNNING.value)
        try:
            await coro_factory(job_id)
        except asyncio.CancelledError:
            _set_job(job_id, status=JobStatus.CANCELLED.value)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Job %s falhou", job_id)
            _set_job(job_id, status=JobStatus.FAILED.value, error=str(exc))
        else:
            _set_job(job_id, status=JobStatus.DONE.value)

    task = asyncio.create_task(_runner(), name=f"job-{job_id}")
    _tasks[job_id] = task
    task.add_done_callback(lambda _t: _tasks.pop(job_id, None))
    return task


def cancel_job(job_id: int) -> bool:
    task = _tasks.get(job_id)
    if task and not task.done():
        task.cancel()
        return True
    return False


def is_running(job_id: int) -> bool:
    task = _tasks.get(job_id)
    return bool(task and not task.done())


__all__ = [
    "create_job",
    "get_job",
    "list_jobs",
    "run_job",
    "cancel_job",
    "is_running",
]
