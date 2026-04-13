"""Use case: Permanently delete a TranscriptVerification record."""
from __future__ import annotations

from app.domain.exceptions.transcript import TranscriptNotFoundError
from app.domain.repositories.transcript_repository import TranscriptRepository


class DeleteTranscriptUseCase:
    def __init__(self, repository: TranscriptRepository) -> None:
        self._repo = repository

    async def execute(self, verification_id: str) -> None:
        deleted = await self._repo.delete(verification_id)
        if not deleted:
            raise TranscriptNotFoundError(verification_id)
