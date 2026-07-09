"""SQLAlchemy ORM models for the unified The Fork schema."""

from __future__ import annotations

import os
from typing import Optional, Type

import numpy as np
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    desc,
    text as sa_text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Legacy constant kept for old imports/tests that reference it. The actual
# dimension used for new tables is supplied to make_embedding_vector().
EMBEDDING_DIM = 256


def make_embedding_vector(dim: int):
    """Return a TypeDecorator that stores ``dim``-dim float32 vectors.

    PostgreSQL uses pgvector ``vector(dim)``; SQLite stores raw float32 bytes.
    The dimension is bound at factory time so each namespace/table can have the
    correct width for its embedder.
    """

    class DynamicEmbeddingVector(TypeDecorator):
        impl = LargeBinary
        cache_ok = True

        def load_dialect_impl(self, dialect):
            if dialect.name == "postgresql":
                return dialect.type_descriptor(Vector(dim))
            return dialect.type_descriptor(LargeBinary())

        def process_bind_param(self, value, dialect):
            if value is None:
                return None
            arr = np.asarray(value, dtype=np.float32)
            if dialect.name == "postgresql":
                return arr.tolist()
            return arr.tobytes()

        def process_result_value(self, value, dialect):
            if value is None:
                return None
            if dialect.name == "postgresql":
                return np.asarray(value, dtype=np.float32)
            return np.frombuffer(value, dtype=np.float32)

    return DynamicEmbeddingVector


class EmbeddingVector(make_embedding_vector(EMBEDDING_DIM)):
    """Legacy 256-dim vector type used by the original ``chunks`` table."""

    pass


# Registry so the vector store can request the same dynamic class for the
# same namespace without re-declaring it on SQLAlchemy's Base.
_RAG_CHUNK_CLASS_CACHE: dict[tuple[str, int, str, bool], type] = {}


def rag_chunk_table_name(namespace: str) -> str:
    """Table name for a RAG vector namespace.

    The legacy ``chunks`` table uses namespace ``""``. Every other namespace
    gets ``chunks_<namespace>``. The old contaminated table is retired in
    place — new writes go to namespaced tables only.
    """
    return "chunks" if not namespace else f"chunks_{namespace}"


def make_rag_chunk_class(
    namespace: str,
    dim: int,
    model_name: str,
    normalized: bool = True,
) -> type:
    """Return a SQLAlchemy model class for ``chunks_<namespace>``.

    The class is cached per (namespace, dim) so repeated calls return the
    same mapped class. ``model_name`` only affects the default value of the
    ``embedding_model`` column and the attached identity metadata; it does
    NOT change the table schema, so a namespace opened with a different
    model reuses the same class and relies on ``VectorStore`` to fail loud
    on identity mismatch.

    The legacy namespace (empty string) reuses the static ``RagChunk`` class
    so the original ``chunks`` table has exactly one mapped class.
    """
    if namespace == "":
        return RagChunk

    schema_key = (namespace, dim)
    if schema_key in _RAG_CHUNK_CLASS_CACHE:
        return _RAG_CHUNK_CLASS_CACHE[schema_key]

    table_name = rag_chunk_table_name(namespace)
    vector_type = make_embedding_vector(dim)
    class_name = f"RagChunk_{namespace or 'legacy'}_{dim}"

    def _repr(self):
        return f"<{class_name}(chunk_id={self.chunk_id!r})>"

    attrs = {
        "__tablename__": table_name,
        "__table_args__": (
            UniqueConstraint(
                "project_id", "doc_id", "chunk_index",
                name=f"uq_{table_name}_project_doc_index",
            ),
            Index(f"idx_{table_name}_project", "project_id"),
            Index(f"idx_{table_name}_doc", "project_id", "doc_id"),
        ),
        "__repr__": _repr,
        "chunk_id": mapped_column(String, primary_key=True),
        "project_id": mapped_column(String, nullable=False),
        "doc_id": mapped_column(String, nullable=False),
        "chunk_index": mapped_column(Integer, nullable=False),
        "text": mapped_column(Text, nullable=False),
        "embedding": mapped_column(vector_type, nullable=False),
        "created_at": mapped_column(String, nullable=False),
        # Embedding identity — stamped on every row and verified at startup.
        "embedding_model": mapped_column(String, nullable=False, default=model_name),
        "embedding_dim": mapped_column(Integer, nullable=False, default=dim),
        "embedding_normalized": mapped_column(Boolean, nullable=False, default=normalized),
        "embedding_identity": {
            "model": model_name,
            "dim": dim,
            "normalized": normalized,
        },
    }

    DynamicRagChunk = type(class_name, (Base,), attrs)
    _RAG_CHUNK_CLASS_CACHE[schema_key] = DynamicRagChunk
    return DynamicRagChunk


class Base(DeclarativeBase):
    pass


class User(Base):
    """users table — see the_fork_schema.sql."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    salt: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False, default="user")
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class Project(Base):
    """projects table — see the_fork_schema.sql."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    client: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    aconex_connected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        default="system",
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    # PR A — admin-approved-projects: true for user-created + admin-
    # approved-from-Drive rows (the projects users should see); false
    # for detected-but-not-yet-approved rows the admin still needs to
    # rule on. Defaults to true so the column is safe to add to legacy
    # rows + safe to omit on inserts from older code paths.
    is_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_text("TRUE"),
    )
    # PR B — discriminates how the project came into being. Admin page
    # filters on origin='admin_drive_approved' so user-created rows
    # (chadi, bopo, etc.) don't show in the admin's approved list.
    # Values: 'user_create' | 'admin_drive_approved' | 'user_drive_import'.
    origin: Mapped[str] = mapped_column(
        String(32), nullable=False, default="user_create",
        server_default=sa_text("'user_create'"),
    )


class Document(Base):
    """documents table — see the_fork_schema.sql."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "doc_role IN ("
            "'baseline_schedule', 'daily_report', 'weekly_report', 'other'"
            ")",
            name="ck_documents_doc_role",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    original_name: Mapped[str] = mapped_column(String, nullable=False)
    stored_as: Mapped[str | None] = mapped_column(String, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String, nullable=True)
    doc_type: Mapped[str] = mapped_column(String, nullable=False, default="document")
    doc_role: Mapped[str] = mapped_column(String, nullable=False, default="other")
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_at: Mapped[str] = mapped_column(String, nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    # ``metadata`` is a reserved word in SQLAlchemy Declarative; the DB column
    # is still named ``metadata`` (JSONB on Postgres), but the ORM attribute is
    # ``metadata_`` to avoid clashing with ``Base.metadata``.
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSON, nullable=True
    )


class ProjectFact(Base):
    """project_facts table — see the_fork_schema.sql."""

    __tablename__ = "project_facts"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_project_facts_project_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source_document: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class Workflow(Base):
    """workflows table — see the_fork_schema.sql."""

    __tablename__ = "workflows"
    __table_args__ = (
        Index("idx_workflows_owner_created", "owner_id", "created_at"),
        Index("idx_workflows_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    owner_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    steps: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class Conversation(Base):
    """conversations table — see the_fork_schema.sql."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("idx_conversations_agent_updated", "agent_name", "updated_at"),
        Index("idx_conversations_project_updated", "project_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class Message(Base):
    """messages table — see the_fork_schema.sql."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="ck_messages_role",
        ),
        Index("idx_messages_conv", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class DocIndex(Base):
    """doc_index table — per-project text index blob (see the_fork_schema.sql)."""

    __tablename__ = "doc_index"

    project_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    index_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class UsageRun(Base):
    """runs table — see the_fork_schema.sql (usage tracker)."""

    __tablename__ = "runs"
    __table_args__ = (Index("idx_runs_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    agent_name: Mapped[str | None] = mapped_column(String, nullable=True)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class HydrationRun(Base):
    """hydration_runs table — see the_fork_schema.sql."""

    __tablename__ = "hydration_runs"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('project', 'global')",
            name="ck_hydration_runs_scope",
        ),
        Index("idx_hydration_lookup", "scope", "project_id", desc("run_date")),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_date: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    summary_md: Mapped[str] = mapped_column(Text, nullable=False)
    facts_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class RagBudget(Base):
    """rag_budget table — see the_fork_schema.sql."""

    __tablename__ = "rag_budget"

    day: Mapped[str] = mapped_column(String, primary_key=True)
    consumed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RagChunk(Base):
    """chunks table — RAG vector store (see the_fork_schema.sql).

    ORM omits FK constraints so SQLite test DBs accept arbitrary
    project/doc ids; Alembic applies FK on PostgreSQL deployments.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "doc_id", "chunk_index", name="uq_chunks_project_doc_index"
        ),
        Index("idx_chunks_project", "project_id"),
        Index("idx_chunks_doc", "project_id", "doc_id"),
    )

    chunk_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    doc_id: Mapped[str] = mapped_column(String, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[np.ndarray] = mapped_column(EmbeddingVector(), nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    # NOTE: the hybrid BM25 leg uses a ``text_search`` tsvector column on
    # PostgreSQL — added by Alembic migration 0003 as GENERATED ALWAYS
    # AS STORED, with a GIN index. It is intentionally NOT declared on
    # the ORM model. Reason: a postgresql.TSVECTOR column with a
    # Computed expression breaks SQLite's
    # ``RagChunk.__table__.create(checkfirst=True)`` path (used by the
    # SQLite test/dev fallback to bootstrap a fresh DB), and SQLite's
    # BM25 leg goes through FTS5 anyway (see vector_store._ensure_fts5_sqlite).
    # vector_store._bm25_postgres references the column via raw SQL
    # (``c.text_search @@ plainto_tsquery(...)``), bypassing the ORM.


class AgentFact(Base):
    """agent_facts table — see the_fork_schema.sql.

    Project-less scope is stored as '' (NOT NULL per schema); the public API
    also uses ''.
    """

    __tablename__ = "agent_facts"
    __table_args__ = (
        UniqueConstraint(
            "agent_name", "project_id", "key", name="uq_agent_facts_scope_key"
        ),
        Index("idx_agent_facts_agent_project", "agent_name", "project_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
