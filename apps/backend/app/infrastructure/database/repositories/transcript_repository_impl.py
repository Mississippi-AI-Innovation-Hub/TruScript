"""
Concrete SQLAlchemy implementation of TranscriptRepository.

This class lives in the infrastructure layer and is the only place in the
application that knows about SQLAlchemy, async sessions, or ORM models.
All other layers interact with the abstract TranscriptRepository port.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.Transcript import TranscriptVerification
from app.domain.repositories.transcript_repository import TranscriptRepository
from app.infrastructure.database.models.transcript_model import TranscriptVerificationModel


class SQLAlchemyTranscriptRepository(TranscriptRepository):
    """Adapter: maps domain operations to SQLAlchemy async queries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── TranscriptRepository interface ────────────────────────────────────────

    async def create(self, transcript: TranscriptVerification) -> TranscriptVerification:
        model = TranscriptVerificationModel.from_entity(transcript)
        self._session.add(model)
        await self._session.flush()          # write to DB within current TX
        await self._session.refresh(model)   # reload server-generated defaults
        return model.to_entity()

    async def get_by_id(self, verification_id: str) -> TranscriptVerification | None:
        model = await self._fetch_model(verification_id)
        return model.to_entity() if model else None

    async def list_all(
        self,
        skip: int = 0,
        limit: int = 20,
    ) -> list[TranscriptVerification]:
        result = await self._session.execute(
            select(TranscriptVerificationModel)
            .order_by(TranscriptVerificationModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return [row.to_entity() for row in result.scalars().all()]

    async def update(self, transcript: TranscriptVerification) -> TranscriptVerification:
        model = await self._fetch_model(transcript.verification_id)
        if model is None:
            raise ValueError(f"Cannot update: '{transcript.verification_id}' not found.")
        model.apply_entity(transcript)
        await self._session.flush()
        await self._session.refresh(model)
        return model.to_entity()

    async def delete(self, verification_id: str) -> bool:
        model = await self._fetch_model(verification_id)
        if model is None:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_model(
        self, verification_id: str
    ) -> TranscriptVerificationModel | None:
        try:
            uid = uuid.UUID(verification_id)
        except ValueError:
            return None
        result = await self._session.execute(
            select(TranscriptVerificationModel).where(
                TranscriptVerificationModel.verification_id == uid
            )
        )
        return result.scalar_one_or_none()
