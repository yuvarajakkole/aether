"""Database schema.

Every tenant-owned row carries ``workspace_id`` so the platform can host many teams
(or companies) in one deployment. Trials double as the job queue: workers claim
``queued`` rows with an atomic conditional update and hold a lease while running.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aether.db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Provider(TimestampMixin, Base):
    __tablename__ = "providers"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(40))
    base_url: Mapped[str | None] = mapped_column(String(500))
    api_key_enc: Mapped[str | None] = mapped_column(Text)
    api_key_env: Mapped[str | None] = mapped_column(String(100))
    headers: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    models: Mapped[list["LLMModel"]] = relationship(back_populates="provider", cascade="all, delete-orphan")


class LLMModel(TimestampMixin, Base):
    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("provider_id", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str | None] = mapped_column(String(200))
    input_cost_per_mtok: Mapped[float] = mapped_column(Float, default=0.0)
    output_cost_per_mtok: Mapped[float] = mapped_column(Float, default=0.0)
    rpm_limit: Mapped[int | None] = mapped_column(Integer)
    tpm_limit: Mapped[int | None] = mapped_column(Integer)
    supports_tools: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    provider: Mapped[Provider] = relationship(back_populates="models", lazy="joined")


class Agent(TimestampMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("workspace_id", "slug"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    # builtin: Aether runs the loop. http: external agent runs its own loop (Aether agent protocol).
    # openai_chat: external endpoint speaks OpenAI chat-completions; Aether runs the tool loop.
    kind: Mapped[str] = mapped_column(String(30), default="builtin")
    endpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    owner: Mapped[str | None] = mapped_column(String(200))
    gate: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    baseline_run_id: Mapped[str | None] = mapped_column(String(32))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

    versions: Mapped[list["AgentVersion"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan", order_by="AgentVersion.version"
    )


class AgentVersion(TimestampMixin, Base):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(200))
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    default_model_id: Mapped[str | None] = mapped_column(ForeignKey("models.id", ondelete="SET NULL"))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)

    agent: Mapped[Agent] = relationship(back_populates="versions")


class Suite(TimestampMixin, Base):
    __tablename__ = "suites"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    generation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    tasks: Mapped[list["Task"]] = relationship(back_populates="suite", cascade="all, delete-orphan", order_by="Task.seq")


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    suite_id: Mapped[str] = mapped_column(ForeignKey("suites.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[str] = mapped_column(String(60), default="everyday")
    input: Mapped[str] = mapped_column(Text)
    scenario: Mapped[str | None] = mapped_column(Text)
    max_turns: Mapped[int] = mapped_column(Integer, default=1)
    fixtures: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expected: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    inject: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    suite: Mapped[Suite] = relationship(back_populates="tasks")


class Run(TimestampMixin, Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer, default=0)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("suites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    failure_clusters: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_by: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analyzing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class Trial(TimestampMixin, Base):
    __tablename__ = "trials"
    __table_args__ = (
        Index("ix_trials_queue", "status", "lease_until"),
        Index("ix_trials_run_variant", "run_id", "variant"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    variant: Mapped[str] = mapped_column(String(80))
    model_id: Mapped[str | None] = mapped_column(String(32))
    version_id: Mapped[str | None] = mapped_column(String(32))
    trial_index: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(100))

    passed: Mapped[bool | None] = mapped_column(Boolean)
    score: Mapped[float | None] = mapped_column(Float)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    judge: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    failure_category: Mapped[str | None] = mapped_column(String(120))
    trace: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    output: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    eval_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    human_verdict: Mapped[str | None] = mapped_column(String(10))
    human_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(200))

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(TimestampMixin, Base):
    """Background work other than trials (suite generation, run analysis)."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_queue", "status", "lease_until"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(100))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(TimestampMixin, Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(32))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
