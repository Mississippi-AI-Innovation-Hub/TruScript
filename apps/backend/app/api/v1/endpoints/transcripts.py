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

import asyncio
from datetime import datetime, timezone
from typing import Annotated, Any
from urllib.parse import quote

import mimetypes
import os

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Security, status
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


class MLResultRequest(BaseModel):
    """Payload posted by the pipeline Lambda when analysis is complete."""
    status: str  # "cleared" | "flagged"
    summary: dict[str, Any]


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


@router.post(
    "/{verification_id}/ml-result",
    response_model=TranscriptResponse,
    include_in_schema=False,  # internal — hide from public Swagger docs
    summary="Receive ML pipeline results (Lambda callback)",
)
async def receive_ml_result(
    verification_id: str,
    body: MLResultRequest,
    x_lambda_secret: str = Header(alias="X-Lambda-Secret", default=""),
    repo: SQLAlchemyTranscriptRepository = Depends(get_repo),
) -> TranscriptResponse:
    """
    Called by the pipeline Lambda when analysis is complete.
    Authenticates via the X-Lambda-Secret header shared secret.
    No JWT required — this is an internal machine-to-machine call.
    """
    expected = os.environ.get("API_CALLBACK_SECRET", "")
    if not expected or x_lambda_secret != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Lambda secret",
        )

    _status_map = {
        "cleared": VerificationStatus.CLEARED,
        "flagged": VerificationStatus.FLAGGED,
        "in_progress": VerificationStatus.IN_PROGRESS,
        "failed": VerificationStatus.FLAGGED,
    }
    verification_status = _status_map.get(
        body.status.lower(), VerificationStatus.FLAGGED
    )

    dto = UpdateTranscriptDTO(
        verification_id=verification_id,
        status=verification_status,
        summary=body.summary,
        completed_at=datetime.now(timezone.utc),
    )
    try:
        result = await UpdateTranscriptUseCase(repo).execute(dto)
    except TranscriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _to_http_response(result)


@router.post(
    "/{verification_id}/retry",
    response_model=TranscriptResponse,
    summary="Re-run the ML pipeline on an already-uploaded transcript",
)
async def retry_verification(
    verification_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
    repo: SQLAlchemyTranscriptRepository = Depends(get_repo),
) -> TranscriptResponse:
    """
    Re-queues the pipeline for a flagged/failed verification without requiring
    the staff member to re-upload the file.  Resets status → in_progress and
    clears the previous summary so the UI shows a fresh processing state.
    """
    try:
        transcript = await GetTranscriptUseCase(repo).execute(verification_id)
    except TranscriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    # Locate document details from the first document record
    s3_key = ""
    doc_id = ""
    storage_path = ""
    filename = ""
    for d in transcript.documents:
        raw = d if isinstance(d, dict) else {}
        doc_id = doc_id or raw.get("document_id", "")
        s3_key = s3_key or raw.get("s3_key", "")
        storage_path = storage_path or raw.get("storage_path", "")
        filename = filename or raw.get("original_filename", "")
        if s3_key:
            break

    pipeline_arn = os.environ.get("LAMBDA_PIPELINE_ARN", "")

    # If no S3 key stored, try to recover it:
    # 1. The file may already be in S3 under the expected path (uploads uploaded before
    #    s3_key was persisted to the DB). Check S3 using the canonical key pattern.
    # 2. If not in S3 but still on local disk, upload it now.
    if pipeline_arn and not s3_key and filename:
        import boto3 as _boto3
        import json as _json
        _bucket = os.environ.get("S3_DOCUMENTS_BUCKET", "")
        _region = os.environ.get("AWS_REGION_NAME", "us-east-1")
        candidate_key = f"uploads/{verification_id}/{filename}"

        recovered = False
        if _bucket:
            # Check if the file already exists in S3 at the expected path
            try:
                def _head():
                    _boto3.client("s3", region_name=_region).head_object(
                        Bucket=_bucket, Key=candidate_key
                    )
                await asyncio.to_thread(_head)
                s3_key = candidate_key  # found in S3 — use it
                recovered = True
            except Exception:
                pass  # not found in S3 — try local disk below

            # Fall back to local disk upload if file is still there
            if not recovered and storage_path and os.path.isfile(storage_path):
                from app.api.v1.endpoints.upload import _upload_to_s3
                with open(storage_path, "rb") as fh:
                    content = fh.read()
                if await _upload_to_s3(content, candidate_key):
                    s3_key = candidate_key
                    recovered = True

        # Persist the recovered key back into the DB document record so future
        # retries don't have to rediscover it.
        if recovered:
            updated_docs = []
            for d in transcript.documents:
                raw = dict(d) if isinstance(d, dict) else {}
                if not raw.get("s3_key") and (
                    raw.get("document_id") == doc_id or not doc_id
                ):
                    raw["s3_key"] = s3_key
                updated_docs.append(raw)
            from sqlalchemy import text as sa_text
            await repo._session.execute(
                sa_text(
                    "UPDATE transcript_verifications "
                    "SET documents = cast(:docs as jsonb) "
                    "WHERE verification_id = :vid"
                ),
                {"docs": _json.dumps(updated_docs), "vid": verification_id},
            )

    # Reset the record: clear old results, set back to in_progress
    reset_dto = UpdateTranscriptDTO(
        verification_id=verification_id,
        status=VerificationStatus.IN_PROGRESS,
        clear_summary=True,
        clear_completed_at=True,
    )
    try:
        result = await UpdateTranscriptUseCase(repo).execute(reset_dto)
    except TranscriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    # Re-invoke the pipeline
    if pipeline_arn and s3_key:
        from app.api.v1.endpoints.upload import _invoke_pipeline_lambda
        await _invoke_pipeline_lambda(
            pipeline_arn=pipeline_arn,
            verification_id=verification_id,
            document_id=doc_id,
            s3_key=s3_key,
        )
    else:
        reason = "LAMBDA_PIPELINE_ARN not configured" if not pipeline_arn \
            else "file no longer on disk and no S3 key recorded — please re-upload"
        from app.api.v1.endpoints.upload import _mark_pipeline_failed
        background_tasks.add_task(
            _mark_pipeline_failed,
            verification_id=verification_id,
            reason=reason,
        )

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

    # Guess mime if not stored
    if not mime_type:
        guessed, _ = mimetypes.guess_type(filename)
        mime_type = guessed or "application/octet-stream"

    # Serve from local disk if the file still exists
    if storage_path and os.path.isfile(storage_path):
        return FileResponse(
            path=storage_path,
            media_type=mime_type,
            filename=filename,
            headers={"Content-Disposition": _content_disposition(filename)},
        )

    # Fall back to S3 (ECS containers lose /tmp on restart)
    s3_key: str = doc.get("s3_key", "")
    if s3_key:
        try:
            return await _serve_from_s3(s3_key, filename, mime_type)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to retrieve file from S3: {exc}",
            )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="File not found on server storage and no S3 key recorded.",
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _content_disposition(filename: str) -> str:
    """
    Build a Content-Disposition header value that is safe for HTTP/1.1.

    HTTP headers are latin-1 by default (RFC 7230).  Filenames that contain
    non-latin-1 characters (e.g. Unicode spaces, accented letters, CJK) will
    raise a UnicodeEncodeError in Starlette/uvicorn.

    We emit both the ASCII fallback (stripped) and the RFC 5987 UTF-8 form so
    every browser gets the right name:
        inline; filename="ascii_name.pdf"; filename*=UTF-8''url-encoded-name.pdf
    """
    # ASCII fallback: replace anything outside latin-1 with '_'
    ascii_name = filename.encode("latin-1", errors="replace").decode("latin-1")
    # RFC 5987 UTF-8 form
    utf8_name = quote(filename, safe="!#$&+-.^_`|~")  # safe chars per RFC 5987
    return f'inline; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'


# ── S3 preview helper ─────────────────────────────────────────────────────────

async def _serve_from_s3(s3_key: str, filename: str, mime_type: str) -> Response:
    """Download an object from S3 and return it as an inline HTTP response."""
    import boto3  # type: ignore

    bucket = os.environ.get("S3_DOCUMENTS_BUCKET", "")
    region = os.environ.get("AWS_REGION_NAME", "us-east-1")
    if not bucket:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="S3_DOCUMENTS_BUCKET not configured",
        )

    def _get_bytes() -> bytes:
        s3 = boto3.client("s3", region_name=region)
        obj = s3.get_object(Bucket=bucket, Key=s3_key)
        return obj["Body"].read()

    content = await asyncio.to_thread(_get_bytes)
    return Response(
        content=content,
        media_type=mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
