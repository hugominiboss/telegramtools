"""Modelos SQLAlchemy do CRM de Telegram.

Entidades:
    accounts, groups, topics, members, member_lists, list_members (associação),
    campaigns, logs.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [str(member.value) for member in enum_cls]


def status_enum(enum_cls: type[enum.Enum], length: int = 16) -> Enum:
    """Enum persistido como VARCHAR com CHECK, gravando o valor (ex.: 'active')."""
    return Enum(
        enum_cls,
        name=f"{enum_cls.__name__.lower()}_enum",
        native_enum=False,
        length=length,
        values_callable=_enum_values,
    )


class AccountStatus(str, enum.Enum):
    ACTIVE = "active"
    BANNED = "banned"
    LIMITED = "limited"


class AccountMode(str, enum.Enum):
    SINGLE = "single"
    POOL = "pool"


class LogStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"


class Account(Base):
    """Conta Telegram autenticada via StringSession (sem arquivos .session)."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    api_id: Mapped[int] = mapped_column(Integer, nullable=False)
    api_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    session_string: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AccountStatus] = mapped_column(
        status_enum(AccountStatus, length=16),
        nullable=False,
        default=AccountStatus.ACTIVE,
        server_default=AccountStatus.ACTIVE.value,
    )
    # Formato: socks5://user:pass@host:port (ou http://...)
    proxy: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    logs: Mapped[list["Log"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", lazy="selectin"
    )


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ID numérico do supergrupo/forum no Telegram (pode ser negativo).
    group_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    group_title: Mapped[str] = mapped_column(String(512), nullable=False)
    is_forum: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    last_scraped: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    topics: Mapped[list["Topic"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", lazy="selectin"
    )
    members: Mapped[list["Member"]] = relationship(
        back_populates="source_group", passive_deletes=True
    )


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (UniqueConstraint("group_id", "topic_id", name="uq_topics_group_topic"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # thread_id do tópico dentro do fórum.
    topic_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic_name: Mapped[str] = mapped_column(String(512), nullable=False)

    group: Mapped["Group"] = relationship(back_populates="topics")


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (
        UniqueConstraint("user_id", "source_group_id", name="uq_members_user_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # 'active' | 'left' | 'restricted' | 'banned' — valor livre para o worker.
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active"
    )
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    source_group_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True
    )

    source_group: Mapped[Optional["Group"]] = relationship(back_populates="members")
    lists: Mapped[list["MemberList"]] = relationship(
        secondary=lambda: list_members, back_populates="members", lazy="selectin"
    )


class MemberList(Base):
    __tablename__ = "member_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    list_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    members: Mapped[list["Member"]] = relationship(
        secondary=lambda: list_members,
        back_populates="lists",
        lazy="selectin",
        # ``list_members`` tem ON DELETE CASCADE no schema: delegar ao banco
        # evita que o ORM tente apagar as linhas secundárias com contagem
        # dessincronizada (StaleDataError) quando o worker usa Core DML.
        passive_deletes=True,
    )
    campaigns: Mapped[list["Campaign"]] = relationship(
        back_populates="target_list", lazy="selectin", passive_deletes=True
    )


# Tabela de associação Many-to-Many: member_lists <-> members
list_members = Table(
    "list_members",
    Base.metadata,
    Column(
        "member_list_id",
        Integer,
        ForeignKey("member_lists.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "member_id",
        Integer,
        ForeignKey("members.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("added_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    message_template: Mapped[str] = mapped_column(Text, nullable=False)
    target_list_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("member_lists.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    account_mode: Mapped[AccountMode] = mapped_column(
        status_enum(AccountMode, length=16),
        nullable=False,
        default=AccountMode.SINGLE,
        server_default=AccountMode.SINGLE.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    target_list: Mapped["MemberList"] = relationship(back_populates="campaigns")


class Log(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # user_id / group_id / chat_id do alvo (pode ser negativo no Telegram).
    target_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=LogStatus.PENDING.value,
        server_default=LogStatus.PENDING.value,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    account: Mapped[Optional["Account"]] = relationship(back_populates="logs")


# --------------------------------------------------------------------------
# BotCashGain v1.0 — Jobs (workers), Leads (extração) e Settings (isca)
# --------------------------------------------------------------------------
class JobType(str, enum.Enum):
    HARVESTER = "harvester"      # extração de leads (Scraper)
    MASS_SENDER = "mass_sender"  # disparo de iscas (Conversion)
    AUDIT = "audit"              # Scraper → DB → Audit: valida/limpa leads
    WARMUP = "warmup"            # aquecimento anti-ban das contas
    ADDER = "adder"              # adição forçada de membros a um grupo alvo
    SCRAPER = "scraper"          # estrutura rica: groups/topics/members


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(Base):
    """Registro de execução de um worker (Harvester ou Mass Sender)."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(
        status_enum(JobType, length=16), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        status_enum(JobStatus, length=16),
        nullable=False,
        default=JobStatus.PENDING,
        server_default=JobStatus.PENDING.value,
        index=True,
    )
    # Parâmetros do job em JSON textual (grupo, limite, mensagem, etc.)
    params: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def progress_pct(self) -> int:
        if self.total <= 0:
            return 0 if self.status != JobStatus.DONE else 100
        return min(100, int(self.processed * 100 / self.total))


class LeadStatus(str, enum.Enum):
    PENDING = "pending"   # extraído, ainda não recebeu isca
    SENT = "sent"         # isca enviada
    FAILED = "failed"     # falha no envio
    REPLIED = "replied"   # respondeu (sinal quente)
    ADDED = "added"       # membro adicionado a um grupo-alvo (Member Adder)


class Lead(Base):
    """Lead extraído de um grupo pelo Harvester Worker."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    source_group_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    source_group_title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(
        status_enum(LeadStatus, length=16),
        nullable=False,
        default=LeadStatus.PENDING,
        server_default=LeadStatus.PENDING.value,
        index=True,
    )
    job_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Um mesmo user_id não pode ser lead duplicado dentro do mesmo grupo.
    __table_args__ = (
        UniqueConstraint("user_id", "source_group_id", name="uq_lead_user_group"),
    )


class Settings(Base):
    """Configuração chave-valor (mensagem de isca ativa, jitter, etc.)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


__all__ = [
    "Account",
    "AccountMode",
    "AccountStatus",
    "Campaign",
    "Group",
    "Job",
    "JobStatus",
    "JobType",
    "Lead",
    "LeadStatus",
    "Log",
    "LogStatus",
    "Member",
    "MemberList",
    "Settings",
    "Topic",
    "list_members",
]

