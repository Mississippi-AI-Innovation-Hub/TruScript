"""
Use case: Create a new TranscriptVerification record.

Single Responsibility — this class does exactly one thing.
It depends on the abstract TranscriptRepository port (DIP).
"""
from __future__ import annotations

import dataclasses

from app.application.dto.transcript_dto import CreateTranscriptDTO, TranscriptResponseDTO
from app.domain.entities.Transcript import TranscriptVerification
from app.domain.repositories.transcript_repository import TranscriptRepository


class CreateTranscriptUseCase:
    def __init__(self, repository: TranscriptRepository) -> None:
        self._repo = repository

    async def execute(self, dto: CreateTranscriptDTO) -> TranscriptResponseDTO:
        entity = TranscriptVerification(
            applicant_id=dto.applicant_id,
            applicant_type=dto.applicant_type,
            assigned_staff_id=dto.assigned_staff_id,
        )
        saved = await self._repo.create(entity)
        return _to_response_dto(saved)


# ── Shared mapper (package-private) ──────────────────────────────────────────

def _to_response_dto(entity: TranscriptVerification) -> TranscriptResponseDTO:
    """Convert a domain entity to the output DTO used by all use cases."""
    summary_dict: dict | None = None
    if entity.summary is not None:
        summary_dict = dataclasses.asdict(entity.summary) if dataclasses.is_dataclass(entity.summary) else entity.summary  # type: ignore[arg-type]

    docs = [
        dataclasses.asdict(d) if dataclasses.is_dataclass(d) else d
        for d in entity.documents
    ]
    annotations = [
        dataclasses.asdict(a) if dataclasses.is_dataclass(a) else a
        for a in entity.annotations
    ]

    return TranscriptResponseDTO(
        verification_id=entity.verification_id,
        applicant_id=entity.applicant_id,
        applicant_type=entity.applicant_type.value,
        status=entity.status.value,
        assigned_staff_id=entity.assigned_staff_id,
        documents=docs,
        summary=summary_dict,
        annotations=annotations,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        completed_at=entity.completed_at,
    )
