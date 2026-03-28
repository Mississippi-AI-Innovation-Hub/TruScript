"""Use case: Return a paginated list of TranscriptVerification records."""
from __future__ import annotations

from app.application.dto.transcript_dto import TranscriptResponseDTO
from app.application.use_cases.create_transcript import _to_response_dto
from app.domain.repositories.transcript_repository import TranscriptRepository


class ListTranscriptsUseCase:
    def __init__(self, repository: TranscriptRepository) -> None:
        self._repo = repository

    async def execute(self, skip: int = 0, limit: int = 20) -> list[TranscriptResponseDTO]:
        entities = await self._repo.list_all(skip=skip, limit=limit)
        return [_to_response_dto(e) for e in entities]
