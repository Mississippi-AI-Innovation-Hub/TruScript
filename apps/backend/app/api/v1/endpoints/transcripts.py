"""
Transcript Verification CRUD endpoints — presentation layer only.

Each handler is deliberately thin:
  1. Deserialise the HTTP request into a DTO.
  2. Instantiate the use case with the concrete repository.
  3. Call execute() and return the result as an HTTP response.

No business logic lives here. Domain exceptions are caught and mapped to
appropriate HTTP status codes at this boundary.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import mimetypes
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.transcript_dto import (
    CreateTranscriptDTO,
    TranscriptResponseDTO,
    UpdateTranscriptDTO,
)
from app.application.use_cases.create_transcript import CreateTranscriptUseCase
from app.application.use_cases.delete_transcript import DeleteTranscriptUseCase
from app.application.use_cases.get_transcript import GetTranscriptUseCase
from app.application.use_cases.list_transcripts import ListTranscriptsUseCase
from app.application.use_cases.update_transcript import UpdateTranscriptUseCase
from app.core.auth import get_current_user, require_role
from app.domain.entities.Transcript import ApplicantType, VerificationStatus
from app.domain.exceptions.transcript import TranscriptNotFoundError
from app.infrastructure.database.repositories.transcript_repository_impl import (
    SQLAlchemyTranscriptRepository,
)
from app.infrastructure.database.session import get_async_session

router = APIRouter(prefix="/transcripts", tags=["Transcript Verifications"])

# ── Shared auth dependency ────────────────────────────────────────────────────

CurrentUser = Annotated[dict, Security(get_current_user)]

# ── Pydantic request / response schemas (presentation layer) ──────────────────

class TranscriptCreateRequest(BaseModel):
    applicant_id: str = Field(..., min_length=1, max_length=255, description="Unique applicant identifier")
    applicant_type: ApplicantType = Field(..., description="first_time | endorsement")
    assigned_staff_id: str | None = Field(None, max_length=255, description="Staff reviewer ID")


class TranscriptUpdateRequest(BaseModel):
    assigned_staff_id: str | None = Field(None, max_length=255)
    status: VerificationStatus | None = None
    summary: dict[str, Any] | None = Field(None, description="AI verification summary payload")
    annotations_to_append: list[dict[str, Any]] = Field(default_factory=list)
    documents_to_append: list[dict[str, Any]] = Field(default_factory=list)
    completed_at: datetime | None = None


class TranscriptResponse(BaseModel):
    verification_id: str
    applicant_id: str
    applicant_type: str
    status: str
    assigned_staff_id: str | None
    documents: list[dict[str, Any]]
    summary: dict[str, Any] | None
    annotations: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class TranscriptListResponse(BaseModel):
    items: list[TranscriptResponse]
    total: int
    skip: int
    limit: int


# ── Dependency: wire the concrete repository into the session ─────────────────

DbSession = Annotated[AsyncSession, Depends(get_async_session)]


def get_repo(session: DbSession) -> SQLAlchemyTranscriptRepository:
    return SQLAlchemyTranscriptRepository(session)


# ── Helper: DTO → response schema ────────────────────────────────────────────

def _to_http_response(dto: TranscriptResponseDTO) -> TranscriptResponse:
    return TranscriptResponse(
        verification_id=dto.verification_id,
        applicant_id=dto.applicant_id,
        applicant_type=dto.applicant_type,
        status=dto.status,
        assigned_staff_id=dto.assigned_staff_id,
        documents=dto.documents,
        summary=dto.summary,
        annotations=dto.annotations,
        created_at=dto.created_at,
        updated_at=dto.updated_at,
        completed_at=dto.completed_at,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=TranscriptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a new transcript verification record",
)
async def create_transcript(
    body: TranscriptCreateRequest,
    current_user: CurrentUser,
    repo: SQLAlchemyTranscriptRepository = Depends(get_repo),
) -> TranscriptResponse:
    dto = CreateTranscriptDTO(
        applicant_id=body.applicant_id,
        applicant_type=body.applicant_type,
        assigned_staff_id=body.assigned_staff_id,
    )
    result = await CreateTranscriptUseCase(repo).execute(dto)
    return _to_http_response(result)


@router.get(
    "",
    response_model=TranscriptListResponse,
    summary="List all transcript verification records (paginated)",
)
async def list_transcripts(
    current_user: CurrentUser,
    skip: int = Query(default=0, ge=0, description="Records to skip"),
    limit: int = Query(default=20, ge=1, le=100, description="Max records to return"),
    repo: SQLAlchemyTranscriptRepository = Depends(get_repo),
) -> TranscriptListResponse:
    results = await ListTranscriptsUseCase(repo).execute(skip=skip, limit=limit)
    return TranscriptListResponse(
        items=[_to_http_response(r) for r in results],
        total=len(results),
        skip=skip,
        limit=limit,
    )


@router.get(
    "/{verification_id}",
    response_model=TranscriptResponse,
    summary="Retrieve a single transcript verification by ID",
)
async def get_transcript(
    verification_id: str,
    current_user: CurrentUser,
    repo: SQLAlchemyTranscriptRepository = Depends(get_repo),
) -> TranscriptResponse:
    try:
        result = await GetTranscriptUseCase(repo).execute(verification_id)
    except TranscriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _to_http_response(result)


@router.patch(
    "/{verification_id}",
    response_model=TranscriptResponse,
    summary="Partially update a transcript verification record",
)
async def update_transcript(
    verification_id: str,
    body: TranscriptUpdateRequest,
    current_user: CurrentUser,
    repo: SQLAlchemyTranscriptRepository = Depends(get_repo),
) -> TranscriptResponse:
    dto = UpdateTranscriptDTO(
        verification_id=verification_id,
        assigned_staff_id=body.assigned_staff_id,
        status=body.status,
        summary=body.summary,
        annotations_to_append=body.annotations_to_append,
        documents_to_append=body.documents_to_append,
        completed_at=body.completed_at,
    )
    try:
        result = await UpdateTranscriptUseCase(repo).execute(dto)
    except TranscriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _to_http_response(result)


@router.delete(
    "/{verification_id}",
    summary="Permanently delete a transcript verification record",
    dependencies=[Depends(require_role("msbn-admin"))],
)
async def delete_transcript(
    verification_id: str,
    current_user: CurrentUser,
    repo: SQLAlchemyTranscriptRepository = Depends(get_repo),
) -> dict:
    try:
        await DeleteTranscriptUseCase(repo).execute(verification_id)
    except TranscriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"deleted": True, "verification_id": verification_id}


@router.get(
    "/{verification_id}/documents/{document_id}/preview",
    summary="Stream a transcript document file for preview",
    description=(
        "Returns the raw file bytes so the frontend can render images and PDFs "
        "inline.  The file is read from the server-local storage path recorded "
        "at upload time.  For DOCX files (not browser-previewable) a 415 is "
        "returned so the client can show a fallback message."
    ),
)
async def preview_document(
    verification_id: str,
    document_id: str,
    current_user: CurrentUser,
    repo: SQLAlchemyTranscriptRepository = Depends(get_repo),
) -> FileResponse:
    # Load transcript
    try:
        transcript = await GetTranscriptUseCase(repo).execute(verification_id)
    except TranscriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    # Find the document in the JSONB array
    doc: dict | None = None
    for d in transcript.documents:
        raw = d if isinstance(d, dict) else {}
        if raw.get("document_id") == document_id:
            doc = raw
            break

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found on verification {verification_id}.",
        )

    storage_path: str = doc.get("storage_path", "")
    mime_type: str = doc.get("mime_type", "") or ""
    filename: str = doc.get("original_filename", "document")

    # Reject file types the browser cannot render inline
    non_previewable = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    }
    if mime_type in non_previewable:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="DOCX files cannot be previewed in the browser. Please download the file.",
        )

    if not storage_path or not os.path.isfile(storage_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found on server storage. It may have been cleaned up.",
        )

    # Guess mime if not stored
    if not mime_type:
        guessed, _ = mimetypes.guess_type(filename)
        mime_type = guessed or "application/octet-stream"

    return FileResponse(
        path=storage_path,
        media_type=mime_type,
        filename=filename,
        # inline disposition so browsers render rather than download
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
