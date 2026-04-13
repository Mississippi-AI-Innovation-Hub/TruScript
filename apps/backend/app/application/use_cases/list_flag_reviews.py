"""Use case: Retrieve all flag reviews for a transcript (audit trail)."""
from __future__ import annotations

from app.application.dto.workflow_dto import FlagReviewResponseDTO
from app.application.use_cases.review_flag import _to_dto
from app.domain.exceptions.transcript import TranscriptNotFoundError
from app.domain.repositories.transcript_repository import TranscriptRepository
from app.domain.repositories.workflow_repository import WorkflowRepository


class ListFlagReviewsUseCase:
    def __init__(
        self,
        workflow_repository: WorkflowRepository,
        transcript_repository: TranscriptRepository,
    ) -> None:
        self._workflow_repo = workflow_repository
        self._transcript_repo = transcript_repository

    async def execute(self, verification_id: str) -> list[FlagReviewResponseDTO]:
        transcript = await self._transcript_repo.get_by_id(verification_id)
        if transcript is None:
            raise TranscriptNotFoundError(verification_id)

        reviews = await self._workflow_repo.get_reviews_for_verification(verification_id)
        return [_to_dto(r) for r in reviews]
