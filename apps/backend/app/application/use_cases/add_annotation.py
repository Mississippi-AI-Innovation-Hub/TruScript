"""
Use case: Staff adds a free-text annotation or justification to a transcript.

Annotations are appended to the transcript's `annotations` JSONB list and
persisted via the existing TranscriptRepository.  If `overrides_flag_id` is
provided the annotation is linked to a specific flag as its justification.
"""
from __future__ import annotations

from app.application.dto.transcript_dto import TranscriptResponseDTO
from app.application.dto.workflow_dto import AddAnnotationDTO
from app.application.use_cases.create_transcript import _to_response_dto
from app.domain.entities.Transcript import StaffAnnotation
from app.domain.exceptions.transcript import TranscriptNotFoundError
from app.domain.repositories.transcript_repository import TranscriptRepository


class AddAnnotationUseCase:
    def __init__(self, transcript_repository: TranscriptRepository) -> None:
        self._repo = transcript_repository

    async def execute(self, dto: AddAnnotationDTO) -> TranscriptResponseDTO:
        transcript = await self._repo.get_by_id(dto.verification_id)
        if transcript is None:
            raise TranscriptNotFoundError(dto.verification_id)

        annotation = StaffAnnotation(
            staff_user_id=dto.staff_user_id,
            note=dto.note,
            overrides_flag_id=dto.overrides_flag_id,
        )
        transcript.add_annotation(annotation)  # also calls _touch()

        updated = await self._repo.update(transcript)
        return _to_response_dto(updated)
