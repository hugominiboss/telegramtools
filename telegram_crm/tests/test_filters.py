"""Validação da Fase 3 — filtros, segmentação e gestão de listas.

Roda:  python tests/test_filters.py
Cobre (Tarefas 3.1 a 3.4 do blueprint):
  * 3.1 motor de critérios: dataset fixo de 6 membros → cada filtro devolve
    exatamente o subconjunto esperado (AND e OR);
  * 3.2 ``materialize_list`` grava o resultado em ``MemberList`` e a contagem
    bate com o filtro;
  * 3.3 CRUD de listas via handlers das rotas (criar/listar/renomear/excluir
    + ``list_members`` removidas por CASCADE);
  * 3.4 membros da lista: adicionar 2 → GET 2; remover 1 → GET 1; idempotente.

Sem ``httpx`` instalado, os handlers são chamados diretamente com uma sessão
explícita — mesmo padrão já usado nos testes de worker.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Banco ISOLADO: evita colidir com os demais testes.
os.environ.setdefault("DATABASE_URL", "sqlite:///./_filters_test.db")
try:
    os.remove("_filters_test.db")
except OSError:
    pass

import app.api.routes_lists as lists_api  # noqa: E402
import app.database.models as m  # noqa: E402
from app.api.routes_lists import (  # noqa: E402
    MembersIn,
    add_list_members,
    create_list,
    delete_list,
    delete_list_members,
    get_list,
    get_list_members,
    list_lists,
    update_list,
)
from app.database.database import Base, get_engine, get_session_factory  # noqa: E402
from app.workers.filters import (  # noqa: E402
    MODE_OR,
    MemberFilter,
    count_members,
    filter_member_ids,
    materialize_list,
)

# Instantes de referência do dataset (fixos → asserções determinísticas).
D_2025 = datetime(2025, 1, 1, tzinfo=timezone.utc)
D_2026_JAN = datetime(2026, 1, 10, tzinfo=timezone.utc)
D_2026_JUN = datetime(2026, 6, 1, tzinfo=timezone.utc)
D_2026_SEP = datetime(2026, 9, 1, tzinfo=timezone.utc)
CUT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def seed() -> tuple[int, int]:
    """Cria 2 grupos e o dataset fixo de 6 membros. Devolve (G1, G2)."""
    Base.metadata.create_all(get_engine())
    S = get_session_factory()
    s = S()
    s.query(m.Campaign).delete()
    s.execute(m.list_members.delete())
    s.query(m.MemberList).delete()
    s.query(m.Member).delete()
    s.query(m.Group).delete()

    g1 = m.Group(group_id=-1001, group_title="Grupo Um", is_forum=True)
    g2 = m.Group(group_id=-1002, group_title="Grupo Dois")
    s.add_all([g1, g2])
    s.flush()

    s.add_all(
        [
            # 1: com username, ativo, visto em 2026, grupo G1
            m.Member(user_id=1001, username="ana", status="active",
                     last_seen=D_2026_JAN, source_group_id=g1.id),
            # 2: sem username, ativo, visto em 2026, grupo G1
            m.Member(user_id=1002, username=None, status="active",
                     last_seen=D_2026_JUN, source_group_id=g1.id),
            # 3: com username, saiu, nunca visto, grupo G2
            m.Member(user_id=1003, username="carla", status="left",
                     last_seen=None, source_group_id=g2.id),
            # 4: sem username, restrito, visto em 2025, grupo G2
            m.Member(user_id=1004, username=None, status="restricted",
                     last_seen=D_2025, source_group_id=g2.id),
            # 5: com username, ativo, nunca visto, grupo G2
            m.Member(user_id=1005, username="eli", status="active",
                     last_seen=None, source_group_id=g2.id),
            # 6: com username, ativo, visto em 2026, sem grupo de origem
            m.Member(user_id=1006, username="fabi", status="active",
                     last_seen=D_2026_SEP, source_group_id=None),
        ]
    )
    s.commit()
    g1_id, g2_id = g1.id, g2.id
    s.close()
    return g1_id, g2_id


# --------------------------------------------------------------------------
# 3.1 — Motor de critérios (dataset fixo de 6 membros)
# --------------------------------------------------------------------------
async def test_filter_engine(g1: int, g2: int) -> None:
    def ids(**kwargs) -> list[int]:
        return sorted(filter_member_ids(MemberFilter(**kwargs)))

    cases = [
        ("has_username=True (possui @)", dict(has_username=True), [1, 3, 5, 6]),
        ("has_username=False (não possui)", dict(has_username=False), [2, 4]),
        ("status='active'", dict(status="active"), [1, 2, 5, 6]),
        ("status_in=['left','restricted']", dict(status_in=["left", "restricted"]), [3, 4]),
        ("last_seen_after=2026-01-01", dict(last_seen_after=CUT), [1, 2, 6]),
        ("last_seen_before=2026-01-01", dict(last_seen_before=CUT), [4]),
        ("source_group_id=G1", dict(source_group_id=g1), [1, 2]),
        ("source_group_id=G2", dict(source_group_id=g2), [3, 4, 5]),
        ("user_id_min/max faixa", dict(user_id_min=1002, user_id_max=1005), [2, 3, 4, 5]),
        ("AND: username + ativo", dict(has_username=True, status="active"), [1, 5, 6]),
        ("AND: username + G1 + recente",
         dict(has_username=True, source_group_id=g1, last_seen_after=CUT), [1]),
        ("OR: restricted | G1", dict(status="restricted", source_group_id=g1, mode=MODE_OR),
         [1, 2, 4]),
        ("OR: sem username | saiu",
         dict(has_username=False, status="left", mode=MODE_OR), [2, 3, 4]),
    ]
    for label, kwargs, expected in cases:
        got = ids(**kwargs)
        assert got == expected, f"{label}: veio {got}, esperado {expected}"
    print(f"[OK] 3.1 motor de critérios: {len(cases)} filtros com subconjunto exato")

    # Filtro vazio seleciona todos (nenhum critério ativo).
    assert MemberFilter().is_empty() is True
    assert count_members(MemberFilter()) == 6
    print("[OK] 3.1 filtro vazio = sem restrição (6 membros)")


# --------------------------------------------------------------------------
# 3.2 — Segmentação: filtro → MemberList, contagem bate
# --------------------------------------------------------------------------
async def test_segment(g1: int) -> None:
    filt = MemberFilter(has_username=True, status="active")
    expected = sorted(filter_member_ids(filt))
    assert expected == [1, 5, 6], expected

    result = materialize_list(filt, "Ativos com username", "segmentado por worker")
    assert result["created"] is True, result
    assert result["matched"] == 3, result["matched"]
    assert result["added"] == 3, result["added"]
    # A contagem na lista tem de bater exatamente com o filtro aplicado.
    assert result["total"] == 3, result["total"]
    assert result["total"] == result["matched"], result
    print(f"[OK] 3.2 segment: lista #{result['list_id']} com {result['total']} membros "
          "(bate com o filtro)")

    # Idempotência: re-segmentar o mesmo nome não duplica.
    again = materialize_list(filt, "Ativos com username")
    assert again["created"] is False, again
    assert again["total"] == 3, f"duplicou: {again['total']}"
    print("[OK] 3.2 idempotente: re-segmentar mantém 3 membros")

    # replace=True reflete um filtro diferente; replace=False faz união.
    replaced = materialize_list(MemberFilter(source_group_id=g1), "Ativos com username")
    assert replaced["total"] == 2, replaced["total"]
    assert replaced["added"] == 2, replaced["added"]

    # replace=False: preserva {1,2} (G1) e une com os ativos {1,2,5,6} → 4.
    merged = materialize_list(MemberFilter(status="active"), "Ativos com username",
                              replace=False)
    assert merged["total"] == 4, f"união deveria dar 4: {merged['total']}"
    assert merged["added"] == 2, f"deveria acrescentar só 5 e 6: {merged['added']}"
    print("[OK] 3.2 replace=True reflete o filtro; replace=False faz união (4 membros)")


# --------------------------------------------------------------------------
# 3.3 — CRUD de listas (pelos handlers das rotas)
# --------------------------------------------------------------------------
async def test_list_crud() -> None:
    from fastapi import HTTPException

    S = get_session_factory()
    db = S()
    try:
        # criar → 201
        created = create_list(lists_api.ListIn(list_name="Minha Lista", description="x"), db=db)
        list_id = created["id"]
        assert created["list_name"] == "Minha Lista", created
        assert created["members_count"] == 0, created
        print(f"[OK] 3.3 criar → lista #{list_id}")

        # listar → contém
        listing = list_lists(limit=100, offset=0, db=db)
        names = [row["list_name"] for row in listing["lists"]]
        assert "Minha Lista" in names, names
        print(f"[OK] 3.3 listar → {listing['total']} lista(s), contém 'Minha Lista'")

        # renomear → 200
        renamed = update_list(list_id, lists_api.ListUpdateIn(list_name="Lista Renomeada"), db=db)
        assert renamed["list_name"] == "Lista Renomeada", renamed
        print("[OK] 3.3 renomear → 200 com o novo nome")

        # nome duplicado → 409
        try:
            create_list(lists_api.ListIn(list_name="Lista Renomeada"), db=db)
            raise AssertionError("duplicata deveria dar 409")
        except HTTPException as exc:
            assert exc.status_code == 409, exc.status_code
            assert exc.detail["error"] == "list_name_taken", exc.detail
        print("[OK] 3.3 nome duplicado → 409")

        # id inexistente → 404
        try:
            get_list(999999, db=db)
            raise AssertionError("deveria dar 404")
        except HTTPException as exc:
            assert exc.status_code == 404, exc.status_code
        try:
            update_list(999999, lists_api.ListUpdateIn(description="y"), db=db)
            raise AssertionError("deveria dar 404")
        except HTTPException as exc:
            assert exc.status_code == 404, exc.status_code
        print("[OK] 3.3 id inexistente → 404")

        # popula a lista para provar o CASCADE no delete
        member_ids = [r.id for r in db.query(m.Member).order_by(m.Member.id).limit(2).all()]
        add_list_members(list_id, MembersIn(member_ids=member_ids), db=db)
        linked = db.execute(
            m.list_members.select().where(m.list_members.c.member_list_id == list_id)
        ).scalars().all()
        assert len(linked) == 2, f"deveria haver 2 associações: {linked}"

        # excluir → 204 e as associações DA LISTA removidas por CASCADE
        delete_list(list_id, db=db)
        after = db.execute(
            m.list_members.select().where(m.list_members.c.member_list_id == list_id)
        ).scalars().all()
        assert after == [], f"associações da lista deveriam sumir: {after}"
        assert db.get(m.MemberList, list_id) is None
        # Os membros em si não são apagados (só a associação).
        assert db.query(m.Member).count() == 6, "members não deveria ser afetado"
        # A outra lista (da segmentação) permanece intacta.
        assert db.query(m.MemberList).count() == 1, "a lista #1 deveria permanecer"
        print("[OK] 3.3 excluir → 204, associações limpas por CASCADE (members intactos)")
    finally:
        db.close()


# --------------------------------------------------------------------------
# 3.4 — Membros da lista
# --------------------------------------------------------------------------
async def test_list_members() -> None:
    S = get_session_factory()
    db = S()
    try:
        created = create_list(lists_api.ListIn(list_name="Lista de Membros"), db=db)
        list_id = created["id"]
        two = [r.id for r in db.query(m.Member).order_by(m.Member.id).limit(2).all()]

        # adicionar 2 → GET retorna 2
        added = add_list_members(list_id, MembersIn(member_ids=two), db=db)
        assert added["added"] == 2, added
        assert added["total"] == 2, added
        page = get_list_members(list_id, limit=200, offset=0, db=db)
        assert page["total"] == 2, page["total"]
        print(f"[OK] 3.4 adicionar 2 → GET retorna {page['total']}")

        # IDEMPOTENTE: adicionar 2× = 1 linha
        again = add_list_members(list_id, MembersIn(member_ids=two), db=db)
        assert again["added"] == 0, again
        assert again["already_present"] == 2, again
        assert again["total"] == 2, f"duplicou: {again['total']}"
        print("[OK] 3.4 idempotente: adicionar 2× continua com 2 linhas")

        # remover 1 → GET retorna 1
        removed = delete_list_members(list_id, MembersIn(member_ids=[two[0]]), db=db)
        assert removed["removed"] == 1, removed
        assert removed["total"] == 1, removed["total"]
        page = get_list_members(list_id, limit=200, offset=0, db=db)
        assert page["total"] == 1, page["total"]
        print("[OK] 3.4 remover 1 → GET retorna 1")

        # remover quem não está → idempotente (not_in_list)
        noop = delete_list_members(list_id, MembersIn(member_ids=[two[0]]), db=db)
        assert noop["removed"] == 0, noop
        assert noop["not_in_list"] == 1, noop
        assert noop["total"] == 1, noop["total"]
        print("[OK] 3.4 remover ausente → ignorado (idempotente)")

        # membro inexistente → 404
        from fastapi import HTTPException

        try:
            add_list_members(list_id, MembersIn(member_ids=[999999]), db=db)
            raise AssertionError("deveria dar 404")
        except HTTPException as exc:
            assert exc.status_code == 404, exc.status_code
            assert exc.detail["error"] == "member_not_found", exc.detail
        print("[OK] 3.4 membro inexistente → 404")

        delete_list(list_id, db=db)
    finally:
        db.close()


async def main() -> int:
    g1, g2 = seed()
    await test_filter_engine(g1, g2)
    await test_segment(g1)
    await test_list_crud()
    await test_list_members()
    print("\nALL FILTER/LIST TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


