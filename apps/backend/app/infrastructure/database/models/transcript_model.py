"""
SQLAlchemy ORM model for TranscriptVerification.

Mapping strategy
----------------
* Scalar fields → individual typed columns (indexed where queried).
* Nested domain objects (documents, summary, annotations) → JSONB columns.
  This keeps the schema simple for the PoC and avoids over-normalisation.
  The mapper layer handles serialisation/deserialisation at the boundary.

The ORM model is deliberately isolated from the domain entity — it belongs
exclusively to the infrastructure layer.
"""
from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.Transcript import (
    ApplicantType,
    TranscriptVerification,
    VerificationStatus,
)
from app.infrastructure.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TranscriptVerificationModel(Base):
    __tablename__ = "transcript_verifications"

    # ── Primary key ───────────────────────────────────────────────────────────
    verification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ── Scalar fields ─────────────────────────────────────────────────────────
    applicant_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    applicant_type: Mapped[str] = mapped_column(String(50), nullable=False)
    assigned_staff_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=VerificationStatus.PENDING.value, index=True
    )

    # ── Nested JSONB fields ───────────────────────────────────────────────────
    documents: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    annotations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Mapper methods ────────────────────────────────────────────────────────

    @classmethod
    def from_entity(cls, entity: TranscriptVerification) -> "TranscriptVerificationModel":
        """Serialise a domain entity to its ORM representation."""
        return cls(
            verification_id=uuid.UUID(entity.verification_id),
            applicant_id=entity.applicant_id,
            applicant_type=entity.applicant_type.value,
            assigned_staff_id=entity.assigned_staff_id,
            status=entity.status.value,
            documents=[
                dataclasses.asdict(d) if dataclasses.is_dataclass(d) else d
                for d in entity.documents
            ],
            summary=(
                dataclasses.asdict(entity.summary)  # type: ignore[arg-type]
                if entity.summary is not None and dataclasses.is_dataclass(entity.summary)
                else entity.summary
            ),
            annotations=[
                dataclasses.asdict(a) if dataclasses.is_dataclass(a) else a
                for a in entity.annotations
            ],
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            completed_at=entity.completed_at,
        )

    def to_entity(self) -> TranscriptVerification:
        """Deserialise an ORM row back into a domain entity."""
        return TranscriptVerification(
            verification_id=str(self.verification_id),
            applicant_id=self.applicant_id,
            applicant_type=ApplicantType(self.applicant_type),
            assigned_staff_id=self.assigned_staff_id,
            status=VerificationStatus(self.status),
            # Nested objects are returned as raw dicts from JSONB.
            # Higher layers (AI pipeline, use cases) reconstruct typed objects
            # when they need them; for pure CRUD the dict is sufficient.
            documents=self.documents or [],
            summary=self.summary,           # type: ignore[arg-type]
            annotations=self.annotations or [],
            created_at=self.created_at,
            updated_at=self.updated_at,
            completed_at=self.completed_at,
        )

    def apply_entity(self, entity: TranscriptVerification) -> None:
        """In-place update from a domain entity (used by the update path)."""
        self.applicant_id = entity.applicant_id
        self.applicant_type = entity.applicant_type.value
        self.assigned_staff_id = entity.assigned_staff_id
        self.status = entity.status.value
        self.documents = [
            dataclasses.asdict(d) if dataclasses.is_dataclass(d) else d
            for d in entity.documents
        ]
        self.summary = (
            dataclasses.asdict(entity.summary)  # type: ignore[arg-type]
            if entity.summary is not None and dataclasses.is_dataclass(entity.summary)
            else entity.summary
        )
        self.annotations = [
            dataclasses.asdict(a) if dataclasses.is_dataclass(a) else a
            for a in entity.annotations
        ]
        self.updated_at = entity.updated_at
        self.completed_at = entity.completed_at
