"""Configuração de conexão com o banco de dados.

Prioridade de conexão (produção → dev):
    1. DATABASE_URL   (explícita; pode ser a connection string do Supabase)
    2. SUPABASE_DB_URL (Postgres do Supabase)
    3. DB_HOST/PORT/USER/PASS/NAME (Postgres local/VPS)
    4. sqlite:///./botcashgain.db (fallback de desenvolvimento)

Credenciais Supabase:
    SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY — API REST (PostgREST), usadas
    para health-check; a persistência do ORM usa a connection string Postgres.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Carrega o arquivo .env na raiz do projeto (se existente).
load_dotenv()


class Base(DeclarativeBase):
    """Base declarativa compartilhada por todos os modelos SQLAlchemy."""


@lru_cache(maxsize=1)
def get_database_url() -> str:
    """Monta a URL de conexão a partir das variáveis de ambiente.

    Prioridade (produção → dev):
      1. DATABASE_URL     (explícita; ex.: connection string do Supabase/Postgres)
      2. SUPABASE_DB_URL  (connection string Postgres do Supabase)
      3. DB_HOST/PORT/USER/PASS/NAME (Postgres local/VPS)
      4. sqlite:///./botcashgain.db  (fallback de dev — NÃO usar em produção)

    Observação Supabase:
      SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY são as credenciais da API REST
      (PostgREST), usadas para health-check/administração. A persistência do
      ORM usa a connection string Postgres (DATABASE_URL/SUPABASE_DB_URL),
      que no Supabase tem a forma:
        postgresql+psycopg2://postgres.<ref>:<SENHA>@<host>:5432/postgres
    """
    explicit = os.getenv("DATABASE_URL", "").strip()
    if explicit:
        return explicit

    supabase_db = os.getenv("SUPABASE_DB_URL", "").strip()
    if supabase_db:
        return supabase_db

    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASS", "")
    name = os.getenv("DB_NAME", "telegram_crm")

    # make_url realiza o escaping correto de usuário/senha na URL.
    return make_url(
        f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"
    ).render_as_string(hide_password=False)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Retorna (e memoriza) o Engine SQLAlchemy."""
    from sqlalchemy import event

    url = get_database_url()
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs = {}
    else:
        kwargs.update(pool_size=10, max_overflow=20, pool_recycle=1800)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _enable_sqlite_fks)
    return engine


def _enable_sqlite_fks(dbapi_connection, _record) -> None:
    """Garante que o SQLite respeite Foreign Keys (comportamento do PostgreSQL)."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Retorna a factory de sessões (síncrona) vinculada ao engine."""
    return sessionmaker(
        bind=get_engine(),
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )


def get_db() -> Generator[Session, None, None]:
    """Dependency do FastAPI: fornece uma sessão por request."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


# Alias conveniente para imports diretos (app.database.database.engine).
engine = get_engine()
SessionLocal = get_session_factory()
