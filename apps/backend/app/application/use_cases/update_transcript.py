"""
Use case: Partially update a TranscriptVerification record (PATCH semantics).

Only fields explicitly provided in the DTO (non-None) are applied.
All domain business rules are enforced through entity methods.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.application.dto.transcript_dto import TranscriptResponseDTO, UpdateTranscriptDTO
from app.application.use_cases.create_transcript import _to_response_dto
from app.domain.entities.Transcript import (
    ExtractedTranscriptData,
    FlagCategory,
    FlagSeverity,
    StaffAnnotation,
    TranscriptDocument,
    TranscriptFlag,
    VerificationStatus,
    VerificationSummary,
)
from app.domain.exceptions.transcript import TranscriptNotFoundError
from app.domain.repositories.transcript_repository import TranscriptRepository


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _parse_flag(flag_data: dict) -> TranscriptFlag:
    severity_raw = str(flag_data.get("severity", FlagSeverity.WARNING.value)).lower()
    category_raw = str(flag_data.get("category", FlagCategory.DOCUMENT_INTEGRITY.value)).lower()

    try:
        severity = FlagSeverity(severity_raw)
    except ValueError:
        severity = FlagSeverity.WARNING

    try:
        category = FlagCategory(category_raw)
    except ValueError:
        category = FlagCategory.DOCUMENT_INTEGRITY

    created_at = _parse_datetime(flag_data.get("created_at"))

    return TranscriptFlag(
        flag_id=str(flag_data.get("flag_id", "")),
        category=category,
        severity=severity,
        rule_code=str(flag_data.get("rule_code", "")),
        rule_description=str(flag_data.get("rule_description", "")),
        evidence=str(flag_data.get("evidence", "")),
        explanation=str(flag_data.get("explanation", "")),
        source_section=str(flag_data.get("source_section", "")),
        created_at=created_at if created_at else datetime.now(timezone.utc),
    )


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_summary(summary_data: dict) -> VerificationSummary:
    extracted_data_raw = summary_data.get("extracted_data", {}) or {}
    extracted_data = ExtractedTranscriptData(
        institution_name=str(extracted_data_raw.get("institution_name", "")),
        program_name=str(extracted_data_raw.get("program_name", "")),
        degree_awarded=str(extracted_data_raw.get("degree_awarded", "")),
        graduation_date=str(extracted_data_raw.get("graduation_date", "")),
        graduation_confirmed=bool(extracted_data_raw.get("graduation_confirmed", False)),
        total_credits=_to_float(extracted_data_raw.get("total_credits", 0.0), 0.0),
        nursing_credits=_to_float(extracted_data_raw.get("nursing_credits", 0.0), 0.0),
        courses=extracted_data_raw.get("courses", []) or [],
        accreditation_type=str(extracted_data_raw.get("accreditation_type", "")),
        accreditation_body=str(extracted_data_raw.get("accreditation_body", "")),
        raw_text=str(extracted_data_raw.get("raw_text", "")),
        extraction_confidence=_to_float(extracted_data_raw.get("extraction_confidence", 0.0), 0.0),
        extraction_notes=str(extracted_data_raw.get("extraction_notes", "")),
    )

    flags_raw = summary_data.get("flags", []) or []
    flags = [_parse_flag(f) for f in flags_raw if isinstance(f, dict)]

    status_raw = str(summary_data.get("overall_status", VerificationStatus.PENDING.value)).lower()
    try:
        overall_status = VerificationStatus(status_raw)
    except ValueError:
        overall_status = VerificationStatus.PENDING

    created_at = _parse_datetime(summary_data.get("created_at"))

    return VerificationSummary(
        summary_id=str(summary_data.get("summary_id", "")),
        verification_id=str(summary_data.get("verification_id", "")),
        rules_applied=summary_data.get("rules_applied", []) or [],
        flags=flags,
        extracted_data=extracted_data,
        overall_status=overall_status,
        ai_recommendation=str(summary_data.get("ai_recommendation", "")),
        created_at=created_at if created_at else datetime.now(timezone.utc),
    )


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

        if dto.clear_completed_at:
            entity.completed_at = None
        elif dto.completed_at is not None:
            entity.completed_at = dto.completed_at

        if dto.clear_summary:
            entity.summary = None
        elif dto.summary is not None:
            summary = _parse_summary(dto.summary)
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
