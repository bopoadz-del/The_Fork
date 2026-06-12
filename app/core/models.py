"""SQLAlchemy ORM models for the unified The Fork schema."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
