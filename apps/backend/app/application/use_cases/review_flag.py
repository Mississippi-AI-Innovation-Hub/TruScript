"""
Use case: Staff confirms or overrides a single AI-generated flag.

Responsibilities:
  1. Verify the parent transcript exists.
  2. Verify the flag_id is present in the transcript's AI summary.
  3. Create an immutable FlagReview record via the domain factory method.
  4. When action == OVERRIDE, invoke TranscriptVerification.override() to
     update the aggregate status and auto-create the system annotation.
  5. Persist both the review and (if overridden) the updated transcript.
  6. Return an auditable FlagReviewResponseDTO.
"""
from __future__ import annotations

from app.application.dto.workflow_dto import FlagReviewResponseDTO, ReviewFlagDTO
from app.domain.entities.workflow import FlagReview, FlagReviewAction
from app.domain.exceptions.transcript import TranscriptNotFoundError
from app.domain.exceptions.workflow import FlagNotFoundError
from app.domain.repositories.transcript_repository import TranscriptRepository
from app.domain.repositories.workflow_repository import WorkflowRepository


class ReviewFlagUseCase:
    """Handles both CONFIRM and OVERRIDE actions — separated by DTO.action."""

    def __init__(
        self,
        workflow_repository: WorkflowRepository,
        transcript_repository: TranscriptRepository,
    ) -> None:
        self._workflow_repo = workflow_repository
        self._transcript_repo = transcript_repository

    async def execute(self, dto: ReviewFlagDTO) -> FlagReviewResponseDTO:
        # ── 1. Load the transcript aggregate ─────────────────────────────────
        transcript = await self._transcript_repo.get_by_id(dto.verification_id)
        if transcript is None:
            raise TranscriptNotFoundError(dto.verification_id)

        # ── 2. Validate the flag exists in the AI summary ─────────────────────
        self._assert_flag_exists(transcript, dto.flag_id)

        # ── 3. Build the domain FlagReview entity ─────────────────────────────
        if dto.action == FlagReviewAction.CONFIRM:
            review = FlagReview.confirm(
                verification_id=dto.verification_id,
                flag_id=dto.flag_id,
                staff_user_id=dto.staff_user_id,
                note=dto.note,
            )
        else:
            # Override — justification is validated inside FlagReview.__post_init__
            review = FlagReview.override(
                verification_id=dto.verification_id,
                flag_id=dto.flag_id,
                staff_user_id=dto.staff_user_id,
                justification=dto.justification or "",
                note=dto.note,
            )
            # ── 4. Apply override to the aggregate and persist ────────────────
            transcript.override(
                staff_user_id=dto.staff_user_id,
                justification=dto.justification or "",
            )
            await self._transcript_repo.update(transcript)

        # ── 5. Persist the review ─────────────────────────────────────────────
        saved = await self._workflow_repo.save_review(review)
        return _to_dto(saved)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _assert_flag_exists(transcript, flag_id: str) -> None:
        """
        Flags can live in two places depending on pipeline stage:
          a) transcript.summary.flags  (list of dicts or TranscriptFlag objects)
          b) transcript.summary itself as a dict with a 'flags' key

        We accept both shapes for PoC flexibility.
        """
        summary = transcript.summary
        if summary is None:
            raise FlagNotFoundError(flag_id, transcript.verification_id)

        # Summary may be a dict (from JSONB) or a VerificationSummary dataclass
        flags: list = []
        if isinstance(summary, dict):
            flags = summary.get("flags", [])
        elif hasattr(summary, "flags"):
            flags = summary.flags

        known_ids = {
            (f.get("flag_id") if isinstance(f, dict) else getattr(f, "flag_id", None))
            for f in flags
        }
        if flag_id not in known_ids:
            raise FlagNotFoundError(flag_id, transcript.verification_id)


# ── Mapper ────────────────────────────────────────────────────────────────────

def _to_dto(review: FlagReview) -> FlagReviewResponseDTO:
    return FlagReviewResponseDTO(
        review_id=review.review_id,
        verification_id=review.verification_id,
        flag_id=review.flag_id,
        action=review.action.value,
        staff_user_id=review.staff_user_id,
        justification=review.justification,
        note=review.note,
        created_at=review.created_at,
    )
