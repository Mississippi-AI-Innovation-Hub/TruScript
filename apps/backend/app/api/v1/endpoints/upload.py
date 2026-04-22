"""
Transcript Upload endpoint — accepts one or more transcript files,
creates a verification record per file, and launches the ML pipeline
(real Lambda if configured, otherwise a built-in mock pipeline).

Flow:
  1. Staff POSTs multipart files + applicant_type
  2. We create TranscriptVerification (status=in_progress) for each file
  3. File uploaded to S3 synchronously (Lambda needs the S3 key)
  4. Pipeline Lambda invoked async (fire-and-forget) when LAMBDA_PIPELINE_ARN is set;
     falls back to built-in mock pipeline if the env var is absent or S3 fails
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
import random
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

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
            # Real Lambda: fire-and-forget async invocation
            await _invoke_pipeline_lambda(
                pipeline_arn=pipeline_arn,
                verification_id=result.verification_id,
                document_id=doc_id,
                s3_key=s3_key,
            )
        else:
            # Mock fallback: in-process simulation
            if not pipeline_arn:
                logger.info(
                    "LAMBDA_PIPELINE_ARN not set — using mock pipeline for %s",
                    result.verification_id,
                )
            else:
                logger.warning(
                    "S3 upload failed for %s — falling back to mock pipeline",
                    result.verification_id,
                )
            background_tasks.add_task(
                _run_mock_pipeline,
                verification_id=result.verification_id,
                filename=filename,
                applicant_type=applicant_type.value,
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


# ── Mock ML Pipeline ──────────────────────────────────────────────────────────
# Simulates the real Lambda pipeline (Textract → Rekognition → Bedrock) with
# realistic timing and deterministic-but-varied mock results.

async def _run_mock_pipeline(
    verification_id: str,
    filename: str,
    applicant_type: str,
) -> None:
    """
    Simulate the full ML fraud-detection pipeline in the background.
    Mimics real pipeline timing:  ~3s OCR + ~4s Rekognition + ~7s Bedrock
    """
    logger.info("Mock pipeline started for %s (%s)", verification_id, filename)

    try:
        # Stage 1 — OCR / Textract
        await asyncio.sleep(4)

        # Stage 2 — Visual analysis / Rekognition
        await asyncio.sleep(5)

        # Stage 3 — Bedrock AI fraud analysis
        await asyncio.sleep(6)

        # Generate mock summary
        summary = _generate_mock_summary(verification_id, filename, applicant_type)
        final_status = (
            VerificationStatus.FLAGGED
            if summary.get("flags")
            else VerificationStatus.CLEARED
        )

        # Persist results via fresh session
        async with AsyncSessionLocal() as session:
            repo = SQLAlchemyTranscriptRepository(session)
            dto = UpdateTranscriptDTO(
                verification_id=verification_id,
                status=final_status,
                summary=summary,
                completed_at=datetime.now(timezone.utc),
            )
            await UpdateTranscriptUseCase(repo).execute(dto)
            await session.commit()

        logger.info(
            "Mock pipeline complete for %s — status=%s, flags=%d",
            verification_id,
            final_status.value,
            len(summary.get("flags", [])),
        )

    except Exception as exc:
        logger.exception("Mock pipeline failed for %s: %s", verification_id, exc)
        # Mark as failed so frontend doesn't spin forever
        try:
            async with AsyncSessionLocal() as session:
                repo = SQLAlchemyTranscriptRepository(session)
                dto = UpdateTranscriptDTO(
                    verification_id=verification_id,
                    status=VerificationStatus.FLAGGED,
                    completed_at=datetime.now(timezone.utc),
                )
                await UpdateTranscriptUseCase(repo).execute(dto)
                await session.commit()
        except Exception:
            pass


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


# ── Mock data generation ──────────────────────────────────────────────────────

# Realistic MSBN-aligned scenarios (LOW / MEDIUM / HIGH risk)
_SCENARIOS: list[dict[str, Any]] = [
    # ── CLEARED (LOW risk) ─────────────────────────────────────────────────
    {
        "risk_level": "LOW",
        "overall_status": "cleared",
        "fraud_score": 0.07,
        "ai_recommendation": (
            "Transcript appears authentic. All required elements present: "
            "graduation confirmation, accredited institution (ACEN), and "
            "complete nursing credit hours. No fraud indicators detected. "
            "Recommend approval pending standard staff review."
        ),
        "extracted_data": {
            "institution_name": "University of Mississippi Medical Center",
            "program_name": "Bachelor of Science in Nursing",
            "degree_awarded": "BSN",
            "graduation_date": "May 2023",
            "graduation_confirmed": True,
            "total_credits": 128.0,
            "nursing_credits": 72.0,
            "accreditation_type": "ACEN",
            "accreditation_body": "Accreditation Commission for Education in Nursing",
            "extraction_confidence": 0.94,
            "extraction_notes": "High-confidence extraction. Institutional seal verified.",
        },
        "flags": [],
        "rules_applied": [
            "GR-001", "GR-002", "PC-001", "PC-002", "PC-003",
            "AC-001", "AC-002", "DI-001", "DI-002", "FI-001",
        ],
    },
    # ── FLAGGED (MEDIUM risk) ──────────────────────────────────────────────
    {
        "risk_level": "MEDIUM",
        "overall_status": "flagged",
        "fraud_score": 0.42,
        "ai_recommendation": (
            "One or more items require staff review. Transcript from an "
            "NLNAC-accredited institution with borderline credit hours. "
            "Missing clinical rotation documentation and graduation date "
            "is ambiguous. Manual review recommended before approval."
        ),
        "extracted_data": {
            "institution_name": "Mississippi Delta Community College",
            "program_name": "Associate Degree in Nursing",
            "degree_awarded": "ADN",
            "graduation_date": "Fall 2022",
            "graduation_confirmed": True,
            "total_credits": 64.0,
            "nursing_credits": 57.0,
            "accreditation_type": "NLNAC",
            "accreditation_body": "National League for Nursing Accrediting Commission",
            "extraction_confidence": 0.79,
            "extraction_notes": "Moderate confidence. Graduation date format non-standard.",
        },
        "flags": [
            {
                "flag_id": str(uuid.uuid4()),
                "category": "program_completion",
                "severity": "warning",
                "rule_code": "PC-003",
                "rule_description": "Minimum nursing credit hours (60) not met for ADN",
                "evidence": "Extracted nursing credits: 57 hrs. Required minimum: 60 hrs.",
                "explanation": (
                    "The transcript shows 57 nursing credit hours. MSBN requires a minimum "
                    "of 60 nursing credit hours for an Associate Degree in Nursing program. "
                    "This may indicate an incomplete program or data extraction error."
                ),
                "source_section": "Course Summary / Credit Totals",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "flag_id": str(uuid.uuid4()),
                "category": "graduation",
                "severity": "warning",
                "rule_code": "GR-002",
                "rule_description": "Graduation date format non-standard or ambiguous",
                "evidence": 'Graduation entry reads: "Fall 2022" (semester only, no specific date)',
                "explanation": (
                    "MSBN requires a specific graduation date. 'Fall 2022' without a month "
                    "or day is ambiguous and may not meet documentation standards."
                ),
                "source_section": "Degree Header",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ],
        "rules_applied": [
            "GR-001", "GR-002", "PC-001", "PC-002", "PC-003",
            "AC-001", "AC-002", "DI-001", "DI-002", "FI-001",
        ],
    },
    # ── FLAGGED (HIGH risk) ────────────────────────────────────────────────
    {
        "risk_level": "HIGH",
        "overall_status": "flagged",
        "fraud_score": 0.81,
        "ai_recommendation": (
            "Multiple critical fraud indicators detected. Official seal is absent, "
            "graduation not confirmed, and institution is not in the MSBN-approved "
            "accreditation list. GPA appears altered (4.0 on a 3.0 scale). "
            "This transcript requires immediate escalation for detailed human review "
            "before any licensure action is taken."
        ),
        "extracted_data": {
            "institution_name": "National Online Nursing Academy",
            "program_name": "Bachelor of Science in Nursing",
            "degree_awarded": "BSN",
            "graduation_date": "",
            "graduation_confirmed": False,
            "total_credits": 120.0,
            "nursing_credits": 68.0,
            "accreditation_type": "OTHER",
            "accreditation_body": "Unknown",
            "extraction_confidence": 0.51,
            "extraction_notes": "Low confidence. Document quality poor. Possible digital alteration.",
        },
        "flags": [
            {
                "flag_id": str(uuid.uuid4()),
                "category": "document_integrity",
                "severity": "critical",
                "rule_code": "DI-001",
                "rule_description": "Official institutional seal or registrar stamp not detected",
                "evidence": "No seal, stamp, or watermark detected in visual analysis of document",
                "explanation": (
                    "Authentic nursing transcripts from accredited institutions include an "
                    "official seal or registrar stamp. Visual analysis detected no such "
                    "authentication mark, which is a primary fraud indicator per MSBN policy."
                ),
                "source_section": "Document Header / Footer",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "flag_id": str(uuid.uuid4()),
                "category": "graduation",
                "severity": "critical",
                "rule_code": "GR-001",
                "rule_description": "Graduation confirmation statement absent",
                "evidence": "No graduation date, degree conferral statement, or completion certificate found",
                "explanation": (
                    "The transcript contains no explicit graduation confirmation. "
                    "MSBN requires documentation that the applicant has completed the nursing "
                    "program and had a degree conferred. This is a critical requirement."
                ),
                "source_section": "Degree Summary",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "flag_id": str(uuid.uuid4()),
                "category": "accreditation",
                "severity": "warning",
                "rule_code": "AC-001",
                "rule_description": "Accreditation body not recognized (ACEN/CCNE/NLNAC required)",
                "evidence": 'Institution accreditation: "Unknown" — not ACEN, CCNE, or NLNAC',
                "explanation": (
                    "MSBN accepts nursing programs accredited by ACEN, CCNE, or NLNAC only. "
                    "The issuing institution does not appear in the approved accreditation list, "
                    "and no recognized accreditation body is cited on the transcript."
                ),
                "source_section": "Institution Header",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "flag_id": str(uuid.uuid4()),
                "category": "fraud_indicator",
                "severity": "critical",
                "rule_code": "FI-002",
                "rule_description": "GPA value outside valid academic range",
                "evidence": "Cumulative GPA: 4.0 / 3.0 (scale maximum exceeded)",
                "explanation": (
                    "The transcript lists a GPA of 4.0 on a 3.0 scale, which is mathematically "
                    "impossible. This is a strong indicator of document tampering or forgery. "
                    "Immediate escalation is recommended."
                ),
                "source_section": "Academic Summary",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ],
        "rules_applied": [
            "GR-001", "GR-002", "PC-001", "PC-002", "PC-003",
            "AC-001", "AC-002", "DI-001", "DI-002", "FI-001", "FI-002", "FI-003",
        ],
    },
]


def _generate_mock_summary(
    verification_id: str,
    filename: str,
    applicant_type: str,
) -> dict[str, Any]:
    """
    Deterministically picks a fraud scenario based on filename hash so that
    the same filename always produces the same result for demo consistency.
    """
    seed = int(hashlib.md5(filename.encode()).hexdigest(), 16)
    scenario = _SCENARIOS[seed % len(_SCENARIOS)]

    # Assign fresh IDs & timestamps so every run is unique
    summary_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Re-stamp flag IDs with fresh UUIDs
    flags = []
    for f in scenario["flags"]:
        flags.append({**f, "flag_id": str(uuid.uuid4()), "created_at": now})

    return {
        "summary_id": summary_id,
        "verification_id": verification_id,
        "risk_level": scenario["risk_level"],
        "fraud_score": scenario["fraud_score"],
        "overall_status": scenario["overall_status"],
        "ai_recommendation": scenario["ai_recommendation"],
        "rules_applied": scenario["rules_applied"],
        "flags": flags,
        "extracted_data": scenario["extracted_data"],
        "created_at": now,
        "pipeline": "mock",  # Distinguishes mock from real Lambda results
    }


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
