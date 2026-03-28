"""Use case: Retrieve a single TranscriptVerification by ID."""
from __future__ import annotations

from app.application.dto.transcript_dto import TranscriptResponseDTO
from app.application.use_cases.create_transcript import _to_response_dto
from app.domain.exceptions.transcript import TranscriptNotFoundError
from app.domain.repositories.transcript_repository import TranscriptRepository


class GetTranscriptUseCase:
    def __init__(self, repository: TranscriptRepository) -> None:
        self._repo = repository

    async def execute(self, verification_id: str) -> TranscriptResponseDTO:
        entity = await self._repo.get_by_id(verification_id)
        if entity is None:
            raise TranscriptNotFoundError(verification_id)
        return _to_response_dto(entity)
