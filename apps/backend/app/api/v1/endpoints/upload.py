"""
Transcript Upload endpoint — accepts one or more transcript files,
creates a verification record per file, and launches the ML pipeline.

Flow:
  1. Staff POSTs multipart files + applicant_type
  2. We create TranscriptVerification (status=in_progress) for each file
  3. File uploaded to S3 synchronously (Lambda needs the S3 key)
  4. Pipeline Lambda invoked async (fire-and-forget) via LAMBDA_PIPELINE_ARN
  5. Response returned immediately with verification_id list
  6. Frontend polls GET /transcripts/{id} for status changes
  7. Lambda POSTs results to POST /transcripts/{id}/ml-result with X-Lambda-Secret
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Security,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.transcript_dto import UpdateTranscriptDTO
from app.application.use_cases.create_transcript import CreateTranscriptUseCase
from app.application.dto.transcript_dto import CreateTranscriptDTO
from app.application.use_cases.update_transcript import UpdateTranscriptUseCase
from app.core.auth import get_current_user
from app.domain.entities.Transcript import (
    ApplicantType,
    DocumentStatus,
    TranscriptDocument,
    VerificationStatus,
)
from app.infrastructure.database.repositories.transcript_repository_impl import (
    SQLAlchemyTranscriptRepository,
)
from app.infrastructure.database.session import AsyncSessionLocal, get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transcripts", tags=["Upload"])

CurrentUser = Annotated[dict, Security(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_async_session)]

# ── MIME type allow-list ──────────────────────────────────────────────────────

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/tiff",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".pdf", ".docx", ".doc"}
MAX_FILE_SIZE_MB = 25


# ── Response schema ───────────────────────────────────────────────────────────

class UploadItemResponse(BaseModel):
    verification_id: str
    filename: str
    status: str
    file_size_bytes: int
    mime_type: str


# ── Upload endpoint ───────────────────────────────────────────────────────────

@router.post(
    "/upload",
    response_model=list[UploadItemResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Upload one or more transcript files to start fraud verification",
)
async def upload_transcripts(
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
    session: DbSession,
    files: list[UploadFile] = File(
        ...,
        description="Transcript files — JPEG, PNG, PDF, DOCX (max 25 MB each)",
    ),
    applicant_type: ApplicantType = Form(
        ...,
        description="first_time | endorsement",
    ),
) -> list[UploadItemResponse]:
    if not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one file is required.",
        )

    repo = SQLAlchemyTranscriptRepository(session)
    responses: list[UploadItemResponse] = []

    for file in files:
        filename = file.filename or "unknown"
        ext = os.path.splitext(filename)[1].lower()

        # ── Validate file extension ───────────────────────────────────────────
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"File type not allowed: {filename}. Use JPEG, PNG, PDF, or DOCX.",
            )

        # ── Read content ──────────────────────────────────────────────────────
        content = await file.read()
        size_mb = len(content) / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File {filename} exceeds {MAX_FILE_SIZE_MB} MB limit.",
            )

        # ── Create verification record ────────────────────────────────────────
        verification_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        applicant_id = f"upload-{verification_id[:8]}"

        # Save file locally
        upload_dir = f"/tmp/msbn_uploads/{verification_id}"
        os.makedirs(upload_dir, exist_ok=True)
        storage_path = f"{upload_dir}/{filename}"
        with open(storage_path, "wb") as fh:
            fh.write(content)

        mime_type = file.content_type or _guess_mime(ext)

        # Create via use case (status is forced to IN_PROGRESS below)
        create_dto = CreateTranscriptDTO(
            applicant_id=applicant_id,
            applicant_type=applicant_type,
            assigned_staff_id=None,
        )
        result = await CreateTranscriptUseCase(repo).execute(create_dto)

        # ── Upload to S3 synchronously (Lambda needs the key before it runs) ──
        s3_key = f"uploads/{result.verification_id}/{filename}"
        s3_uploaded = await _upload_to_s3(content, s3_key)

        # Immediately set to in_progress and attach document
        doc_dict = {
            "document_id": doc_id,
            "verification_id": result.verification_id,
            "original_filename": filename,
            "mime_type": mime_type,
            "file_size_bytes": len(content),
            "storage_path": storage_path,
            "s3_key": s3_key if s3_uploaded else "",
            "checksum_sha256": hashlib.sha256(content).hexdigest(),
            "status": DocumentStatus.PROCESSING.value,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        update_dto = UpdateTranscriptDTO(
            verification_id=result.verification_id,
            status=VerificationStatus.IN_PROGRESS,
            documents_to_append=[doc_dict],
        )
        await UpdateTranscriptUseCase(repo).execute(update_dto)

        # ── Launch ML pipeline ────────────────────────────────────────────────
        pipeline_arn = os.environ.get("LAMBDA_PIPELINE_ARN", "")
        if pipeline_arn and s3_uploaded:
            await _invoke_pipeline_lambda(
                pipeline_arn=pipeline_arn,
                verification_id=result.verification_id,
                document_id=doc_id,
                s3_key=s3_key,
            )
        else:
            # Cannot run the pipeline — mark failed immediately so the UI
            # doesn't spin indefinitely.
            reason = "LAMBDA_PIPELINE_ARN not configured" if not pipeline_arn else "S3 upload failed"
            logger.error(
                "Pipeline cannot start for %s: %s", result.verification_id, reason
            )
            background_tasks.add_task(
                _mark_pipeline_failed,
                verification_id=result.verification_id,
                reason=reason,
            )

        responses.append(
            UploadItemResponse(
                verification_id=result.verification_id,
                filename=filename,
                status=VerificationStatus.IN_PROGRESS.value,
                file_size_bytes=len(content),
                mime_type=mime_type,
            )
        )

    return responses


# ── Pipeline failure marker ───────────────────────────────────────────────────

async def _mark_pipeline_failed(verification_id: str, reason: str = "") -> None:
    """
    Called when the pipeline cannot start (no Lambda ARN or S3 failure).
    Marks the transcript as flagged with a clear error summary so the UI
    shows an actionable state rather than spinning indefinitely.
    """
    logger.error("Marking pipeline as failed for %s: %s", verification_id, reason)
    try:
        async with AsyncSessionLocal() as session:
            repo = SQLAlchemyTranscriptRepository(session)
            dto = UpdateTranscriptDTO(
                verification_id=verification_id,
                status=VerificationStatus.FLAGGED,
                completed_at=datetime.now(timezone.utc),
                summary={
                    "overall_status": "flagged",
                    "risk_level": "HIGH",
                    "fraud_score": 0.0,
                    "ai_recommendation": f"Pipeline error: {reason}. Please retry or contact an administrator.",
                    "flags": [
                        {
                            "flag_id": str(uuid.uuid4()),
                            "category": "document_integrity",
                            "severity": "critical",
                            "rule_code": "PIPELINE_ERROR",
                            "rule_description": "Verification pipeline could not start",
                            "evidence": reason,
                            "explanation": f"The automated verification pipeline failed to start: {reason}",
                            "source_section": "pipeline",
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        }
                    ],
                    "extracted_data": {},
                    "rules_applied": [],
                },
            )
            await UpdateTranscriptUseCase(repo).execute(dto)
            await session.commit()
    except Exception as exc:
        logger.exception("Failed to mark pipeline error for %s: %s", verification_id, exc)


async def _upload_to_s3(content: bytes, s3_key: str) -> bool:
    """Upload file bytes to S3. Returns True on success, False on failure."""
    import io
    try:
        import boto3  # type: ignore
        bucket = os.environ.get("S3_DOCUMENTS_BUCKET", "")
        region = os.environ.get("AWS_REGION_NAME", "us-east-1")
        if not bucket:
            logger.warning("S3_DOCUMENTS_BUCKET not configured — skipping S3 upload")
            return False

        def _do_upload() -> None:
            s3 = boto3.client("s3", region_name=region)
            s3.upload_fileobj(io.BytesIO(content), bucket, s3_key)

        await asyncio.to_thread(_do_upload)
        logger.info("Uploaded → s3://%s/%s", bucket, s3_key)
        return True
    except Exception as exc:
        logger.error("S3 upload failed for key %s: %s", s3_key, exc)
        return False


async def _invoke_pipeline_lambda(
    pipeline_arn: str,
    verification_id: str,
    document_id: str,
    s3_key: str,
) -> None:
    """Fire-and-forget async Lambda invocation (InvocationType=Event)."""
    try:
        import boto3  # type: ignore
        region = os.environ.get("AWS_REGION_NAME", "us-east-1")
        payload = json.dumps({
            "verification_id": verification_id,
            "document_id": document_id,
            "s3_key": s3_key,
        }).encode()

        def _do_invoke() -> None:
            lam = boto3.client("lambda", region_name=region)
            lam.invoke(
                FunctionName=pipeline_arn,
                InvocationType="Event",  # async — returns 202 immediately
                Payload=payload,
            )

        await asyncio.to_thread(_do_invoke)
        logger.info(
            "Pipeline Lambda invoked async for verification=%s", verification_id
        )
    except Exception as exc:
        logger.error(
            "Lambda invocation failed for verification=%s: %s", verification_id, exc
        )


def _guess_mime(ext: str) -> str:
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".tiff": "image/tiff",
        ".pdf": "application/pdf",
        ".docx": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        ".doc": "application/msword",
    }
    return mapping.get(ext, "application/octet-stream")
