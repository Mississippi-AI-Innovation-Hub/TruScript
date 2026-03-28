"""
SQLAlchemy ORM model for FlagReview workflow records.

Each row is an immutable audit entry: one staff member's CONFIRM or OVERRIDE
decision on a single AI-generated flag. A new review row supersedes prior ones.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.workflow import FlagReview, FlagReviewAction
from app.infrastructure.database.base import Base


class FlagReviewModel(Base):
    __tablename__ = "flag_reviews"

    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    verification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transcript_verifications.verification_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    flag_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    staff_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # ── Mapper methods ────────────────────────────────────────────────────────

    @classmethod
    def from_entity(cls, entity: FlagReview) -> "FlagReviewModel":
        return cls(
            review_id=uuid.UUID(entity.review_id),
            verification_id=uuid.UUID(entity.verification_id),
            flag_id=entity.flag_id,
            action=entity.action.value,
            staff_user_id=entity.staff_user_id,
            justification=entity.justification,
            note=entity.note,
            created_at=entity.created_at,
        )

    def to_entity(self) -> FlagReview:
        return FlagReview(
            review_id=str(self.review_id),
            verification_id=str(self.verification_id),
            flag_id=self.flag_id,
            action=FlagReviewAction(self.action),
            staff_user_id=self.staff_user_id,
            justification=self.justification,
            note=self.note,
            created_at=self.created_at,
        )
