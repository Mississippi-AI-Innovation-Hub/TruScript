"""
Workflow endpoints — staff review actions on AI-generated flags.

Three operations:
  1. POST /transcripts/{id}/flags/{flag_id}/reviews  → confirm or override a flag
  2. GET  /transcripts/{id}/reviews                  → full audit trail for a transcript
  3. POST /transcripts/{id}/annotations              → add a free-text annotation

Each handler is thin: deserialise → build DTO → call use case → serialise.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Security, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.transcript_dto import TranscriptResponseDTO
from app.application.dto.workflow_dto import AddAnnotationDTO, ReviewFlagDTO
from app.application.use_cases.add_annotation import AddAnnotationUseCase
from app.application.use_cases.list_flag_reviews import ListFlagReviewsUseCase
from app.application.use_cases.review_flag import ReviewFlagUseCase
from app.core.auth import get_current_user
from app.domain.entities.workflow import FlagReviewAction
from app.domain.exceptions.transcript import TranscriptNotFoundError
from app.domain.exceptions.workflow import FlagNotFoundError, WorkflowValidationError
from app.infrastructure.database.repositories.transcript_repository_impl import (
    SQLAlchemyTranscriptRepository,
)
from app.infrastructure.database.repositories.workflow_repository_impl import (
    SQLAlchemyWorkflowRepository,
)
from app.infrastructure.database.session import get_async_session

router = APIRouter(prefix="/transcripts", tags=["Workflow – Staff Review"])

DbSession = Annotated[AsyncSession, Depends(get_async_session)]
CurrentUser = Annotated[dict, Security(get_current_user)]


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class FlagReviewRequest(BaseModel):
    action: FlagReviewAction = Field(
        ...,
        description="'confirm' to agree with the AI flag, 'override' to dismiss it",
    )
    staff_user_id: str = Field(..., min_length=1, max_length=255)
    justification: str | None = Field(
        None,
        min_length=1,
        description="Required when action is 'override'. Explain why the flag is being dismissed.",
    )
    note: str | None = Field(None, description="Optional free-text remark")


class FlagReviewResponse(BaseModel):
    review_id: str
    verification_id: str
    flag_id: str
    action: str
    staff_user_id: str
    justification: str | None
    note: str | None
    created_at: datetime


class AnnotationRequest(BaseModel):
    staff_user_id: str = Field(..., min_length=1, max_length=255)
    note: str = Field(..., min_length=1, description="The annotation or justification text")
    overrides_flag_id: str | None = Field(
        None, description="Link this annotation to a specific flag it addresses"
    )


class TranscriptResponse(BaseModel):
    """Slim transcript response returned after annotation is appended."""
    verification_id: str
    applicant_id: str
    status: str
    annotations: list[dict]
    updated_at: datetime


# ── Dependency factories ──────────────────────────────────────────────────────

def get_transcript_repo(session: DbSession) -> SQLAlchemyTranscriptRepository:
    return SQLAlchemyTranscriptRepository(session)


def get_workflow_repo(session: DbSession) -> SQLAlchemyWorkflowRepository:
    return SQLAlchemyWorkflowRepository(session)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_override_justification(body: FlagReviewRequest) -> None:
    if body.action == FlagReviewAction.OVERRIDE and not body.justification:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="justification is required when action is 'override'.",
        )


def _review_to_http(dto) -> FlagReviewResponse:
    return FlagReviewResponse(
        review_id=dto.review_id,
        verification_id=dto.verification_id,
        flag_id=dto.flag_id,
        action=dto.action,
        staff_user_id=dto.staff_user_id,
        justification=dto.justification,
        note=dto.note,
        created_at=dto.created_at,
    )


def _transcript_to_slim(dto: TranscriptResponseDTO) -> TranscriptResponse:
    return TranscriptResponse(
        verification_id=dto.verification_id,
        applicant_id=dto.applicant_id,
        status=dto.status,
        annotations=dto.annotations,
        updated_at=dto.updated_at,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/{verification_id}/flags/{flag_id}/reviews",
    response_model=FlagReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Confirm or override an AI-generated flag",
    description=(
        "Staff action endpoint. Use `action=confirm` to agree with the AI flag "
        "or `action=override` to dismiss it (justification required). "
        "An OVERRIDE also updates the transcript status to `overridden`."
    ),
)
async def review_flag(
    verification_id: str,
    flag_id: str,
    body: FlagReviewRequest,
    current_user: CurrentUser,
    transcript_repo: SQLAlchemyTranscriptRepository = Depends(get_transcript_repo),
    workflow_repo: SQLAlchemyWorkflowRepository = Depends(get_workflow_repo),
) -> FlagReviewResponse:
    _validate_override_justification(body)

    dto = ReviewFlagDTO(
        verification_id=verification_id,
        flag_id=flag_id,
        action=body.action,
        staff_user_id=body.staff_user_id,
        justification=body.justification,
        note=body.note,
    )
    try:
        result = await ReviewFlagUseCase(workflow_repo, transcript_repo).execute(dto)
    except TranscriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except FlagNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except (ValueError, WorkflowValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    return _review_to_http(result)


@router.get(
    "/{verification_id}/reviews",
    response_model=list[FlagReviewResponse],
    summary="List all flag reviews for a transcript (audit trail)",
    description=(
        "Returns every CONFIRM/OVERRIDE decision made by staff on the "
        "AI-generated flags for this transcript, in chronological order."
    ),
)
async def list_flag_reviews(
    verification_id: str,
    current_user: CurrentUser,
    transcript_repo: SQLAlchemyTranscriptRepository = Depends(get_transcript_repo),
    workflow_repo: SQLAlchemyWorkflowRepository = Depends(get_workflow_repo),
) -> list[FlagReviewResponse]:
    try:
        results = await ListFlagReviewsUseCase(workflow_repo, transcript_repo).execute(
            verification_id
        )
    except TranscriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return [_review_to_http(r) for r in results]


@router.post(
    "/{verification_id}/annotations",
    response_model=TranscriptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a staff annotation or justification to a transcript",
    description=(
        "Appends a free-text annotation to the transcript. "
        "Use `overrides_flag_id` to link the annotation to a specific flag "
        "when providing justification for an override decision."
    ),
)
async def add_annotation(
    verification_id: str,
    body: AnnotationRequest,
    current_user: CurrentUser,
    transcript_repo: SQLAlchemyTranscriptRepository = Depends(get_transcript_repo),
) -> TranscriptResponse:
    dto = AddAnnotationDTO(
        verification_id=verification_id,
        staff_user_id=body.staff_user_id,
        note=body.note,
        overrides_flag_id=body.overrides_flag_id,
    )
    try:
        result = await AddAnnotationUseCase(transcript_repo).execute(dto)
    except TranscriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _transcript_to_slim(result)
