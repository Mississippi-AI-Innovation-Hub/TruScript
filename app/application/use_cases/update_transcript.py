"""
Use case: Partially update a TranscriptVerification record (PATCH semantics).

Only fields explicitly provided in the DTO (non-None) are applied.
All domain business rules are enforced through entity methods.
"""
from __future__ import annotations

from app.application.dto.transcript_dto import TranscriptResponseDTO, UpdateTranscriptDTO
from app.application.use_cases.create_transcript import _to_response_dto
from app.domain.entities.Transcript import StaffAnnotation, TranscriptDocument
from app.domain.exceptions.transcript import TranscriptNotFoundError
from app.domain.repositories.transcript_repository import TranscriptRepository


class UpdateTranscriptUseCase:
    def __init__(self, repository: TranscriptRepository) -> None:
        self._repo = repository

    async def execute(self, dto: UpdateTranscriptDTO) -> TranscriptResponseDTO:
        entity = await self._repo.get_by_id(dto.verification_id)
        if entity is None:
            raise TranscriptNotFoundError(dto.verification_id)

        if dto.assigned_staff_id is not None:
            entity.assigned_staff_id = dto.assigned_staff_id

        if dto.status is not None:
            entity.status = dto.status

        if dto.completed_at is not None:
            entity.completed_at = dto.completed_at

        # Attach or replace the AI verification summary via the domain method
        if dto.summary is not None:
            from app.domain.entities.Transcript import VerificationSummary
            summary = VerificationSummary(**{
                k: v for k, v in dto.summary.items()
                if k in VerificationSummary.__dataclass_fields__
            })
            entity.attach_summary(summary)

        for doc_data in dto.documents_to_append:
            doc = TranscriptDocument(**{
                k: v for k, v in doc_data.items()
                if k in TranscriptDocument.__dataclass_fields__
            })
            entity.add_document(doc)

        for ann_data in dto.annotations_to_append:
            ann = StaffAnnotation(**{
                k: v for k, v in ann_data.items()
                if k in StaffAnnotation.__dataclass_fields__
            })
            entity.add_annotation(ann)

        entity._touch()
        updated = await self._repo.update(entity)
        return _to_response_dto(updated)
