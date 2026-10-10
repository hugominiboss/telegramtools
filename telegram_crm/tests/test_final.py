"""Validação final do ecossistema BotCashGain (audit + failover + onboarding)."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./_final_check.db")
try:
    os.remove("_final_check.db")
except OSError:
    pass

from app.database.database import get_engine, Base  # noqa: E402
import app.database.models  # noqa: E402,F401
from app.database.database import get_session_factory  # noqa: E402
from app.database.models import Account, Lead  # noqa: E402
from app.workers.audit import audit_leads  # noqa: E402
from app.workers.failover import pick_next_account  # noqa: E402
from app.workers.registry import create_job, get_job  # noqa: E402
from app.core.session_manager import LoginError  # noqa: E402

Base.metadata.create_all(get_engine())
S = get_session_factory()

s = S()
s.add(Account(phone="+55001", api_id=1, api_hash="h" * 16, session_string="x" * 30, status="active"))
s.add(Account(phone="+55002", api_id=1, api_hash="h" * 16, session_string="y" * 30, status="active"))
s.commit()
# 3 leads: 1 válido, 1 duplicado, 1 inválido (sem nome/username)
s.add(Lead(user_id=1, username="ana", first_name="Ana", source_group_id=9, status="pending"))
s.add(Lead(user_id=1, username="ana", first_name="Ana", source_group_id=99, status="pending"))
s.add(Lead(user_id=2, source_group_id=9, status="pending"))
s.commit()
s.close()

jid = create_job("audit", {"limit": 100})
res = audit_leads(jid, limit=100)
assert res["kept"] == 1 and res["removed_dup"] == 1 and res["removed_invalid"] == 1, res
print("[OK] audit:", res)

# Failover: exclui a conta 1 → devolve a 2; exclui tudo → LoginError
a2 = pick_next_account(exclude_ids={1})
assert a2.phone == "+55002", a2.phone
print("[OK] failover: Conta_01 caiu, assumiu", a2.phone)
try:
    pick_next_account(exclude_ids={1, 2})
    raise SystemExit("failover deveria falhar sem contas")
except LoginError as e:
    assert e.code == "no_active_account"
    print("[OK] failover sem contas:", e.code)

info = get_job(jid)
assert info["status"] == "pending"  # audit direto não muda status (run_job faz isso)
print("[OK] jobs registry respondendo:", info["id"], info["job_type"])

# Onboarding: importa e checa quota
from app.api.routes_onboarding import SHARED_QUOTA  # noqa: E402
assert SHARED_QUOTA == 100, SHARED_QUOTA
print("[OK] onboarding quota:", SHARED_QUOTA)
print("\nALL FINAL CHECKS PASSED")
