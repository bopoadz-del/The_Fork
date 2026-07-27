"""Tests for the IngestionJob model and ingestion_jobs table."""

from __future__ import annotations

import uuid

from app.core.db import engine
from app.core.models import IngestionJob
from sqlalchemy.orm import Session


def test_ingestion_job_table_exists_and_can_be_queried():
    """The IngestionJob model maps to a table that can be created and queried."""
    IngestionJob.__table__.create(bind=engine, checkfirst=True)

    job_id = uuid.uuid4()
    with Session(engine) as session:
        job = IngestionJob(
            id=job_id,
            project_id="proj_123",
            document_id="doc_456",
            status="pending",
            chunks=None,
            error=None,
        )
        session.add(job)
        session.commit()

        result = session.query(IngestionJob).filter_by(id=job_id).one()
        assert result.project_id == "proj_123"
        assert result.document_id == "doc_456"
        assert result.status == "pending"
        assert result.chunks is None
        assert result.error is None
        assert result.created_at is not None
        assert result.updated_at is not None
