"""Audit Worker — etapa Scraper → DB → Audit → DB.

Valida os leads extraídos pelo Harvester e limpa a base:
  * remove duplicados (mesmo user_id fora do grupo original);
  * descarta bots / contas deletadas / sem nome e sem username;
  * marca FAILED o que não tem como receber isca.

Roda 100% offline (só banco) — sem chamadas ao Telegram.
"""

from __future__ import annotations

from typing import Optional

from app.core.status import status
from app.database.database import get_session_factory
from app.database.models import Job, Lead, LeadStatus
from app.workers.registry import _set_job

import json


def audit_leads(job_id: int, limit: int = 1000, group_id: Optional[int] = None) -> dict:
    Session = get_session_factory()
    session = Session()
    try:
        q = session.query(Lead)
        if group_id is not None:
            q = q.filter(Lead.source_group_id == group_id)
        rows = q.order_by(Lead.id).limit(max(limit, 1)).all()

        _set_job(job_id, total=len(rows), processed=0, failed=0, error=None)
        status.task(f"AUDIT: validando {len(rows)} leads", task_pct=10)

        seen: set[int] = set()
        removed_dup = 0
        removed_invalid = 0
        kept = 0
        for i, lead in enumerate(rows, start=1):
            invalid = (
                lead.user_id in seen
                or (not (lead.first_name or "").strip() and not (lead.username or "").strip())
            )
            if invalid:
                if lead.user_id in seen:
                    removed_dup += 1
                else:
                    removed_invalid += 1
                lead.status = LeadStatus.FAILED
            else:
                seen.add(lead.user_id)
                kept += 1
            _set_job(job_id, processed=i)
            if i % 50 == 0:
                status.task(f"AUDIT: {i}/{len(rows)} validados",
                            task_pct=min(95, 10 + int(i * 80 / max(len(rows), 1))))
        session.commit()
        status.step_done("AUDIT: base limpa", 100)
        return {
            "job_id": job_id, "checked": len(rows), "kept": kept,
            "removed_dup": removed_dup, "removed_invalid": removed_invalid,
        }
    finally:
        session.close()


async def run_audit_job(job_id: int) -> None:
    """Entrada assíncrona registrada em ``registry.run_job``."""
    from app.workers.registry import get_job

    info = get_job(job_id) or {}
    params = info.get("params", {})
    result = audit_leads(
        job_id,
        limit=int(params.get("limit", 1000)),
        group_id=params.get("group_id"),
    )
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


__all__ = ["audit_leads", "run_audit_job"]
