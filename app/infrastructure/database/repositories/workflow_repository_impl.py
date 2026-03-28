"""
Concrete SQLAlchemy implementation of WorkflowRepository.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.workflow import FlagReview
from app.domain.repositories.workflow_repository import WorkflowRepository
from app.infrastructure.database.models.flag_review_model import FlagReviewModel


class SQLAlchemyWorkflowRepository(WorkflowRepository):

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_review(self, review: FlagReview) -> FlagReview:
        model = FlagReviewModel.from_entity(review)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return model.to_entity()

    async def get_reviews_for_verification(
        self, verification_id: str
    ) -> list[FlagReview]:
        try:
            uid = uuid.UUID(verification_id)
        except ValueError:
            return []
        result = await self._session.execute(
            select(FlagReviewModel)
            .where(FlagReviewModel.verification_id == uid)
            .order_by(FlagReviewModel.created_at.asc())
        )
        return [row.to_entity() for row in result.scalars().all()]

    async def get_reviews_for_flag(
        self, verification_id: str, flag_id: str
    ) -> list[FlagReview]:
        try:
            uid = uuid.UUID(verification_id)
        except ValueError:
            return []
        result = await self._session.execute(
            select(FlagReviewModel)
            .where(
                FlagReviewModel.verification_id == uid,
                FlagReviewModel.flag_id == flag_id,
            )
            .order_by(FlagReviewModel.created_at.asc())
        )
        return [row.to_entity() for row in result.scalars().all()]

    async def get_latest_review_for_flag(
        self, verification_id: str, flag_id: str
    ) -> FlagReview | None:
        try:
            uid = uuid.UUID(verification_id)
        except ValueError:
            return None
        result = await self._session.execute(
            select(FlagReviewModel)
            .where(
                FlagReviewModel.verification_id == uid,
                FlagReviewModel.flag_id == flag_id,
            )
            .order_by(FlagReviewModel.created_at.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None
