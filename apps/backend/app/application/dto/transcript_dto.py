"""
Data Transfer Objects for the Transcript Verification use cases.

DTOs cross layer boundaries (Application ↔ Presentation) and are plain
dataclasses with no ORM or framework coupling — pure data containers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.entities.Transcript import ApplicantType, VerificationStatus


# ── Input DTOs ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CreateTranscriptDTO:
    """Carries validated data from the API layer to the CreateTranscript use case."""
    applicant_id: str
    applicant_type: ApplicantType
    assigned_staff_id: str | None = None
    requesting_user_id: str | None = None


@dataclass(frozen=True)
class UpdateTranscriptDTO:
    """
    Carries a partial update from the API layer to the UpdateTranscript use case.
    Only non-None fields are applied; this enables true PATCH semantics.
    """
    verification_id: str
    assigned_staff_id: str | None = None
    status: VerificationStatus | None = None
    # Nested JSONB payloads — validated at the infrastructure boundary
    summary: dict[str, Any] | None = None
    annotations_to_append: list[dict[str, Any]] = field(default_factory=list)
    documents_to_append: list[dict[str, Any]] = field(default_factory=list)
    completed_at: datetime | None = None
    requesting_user_id: str | None = None


# ── Output DTO ────────────────────────────────────────────────────────────────

@dataclass
class TranscriptResponseDTO:
    """Structured response returned from every use case to the API layer."""
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
