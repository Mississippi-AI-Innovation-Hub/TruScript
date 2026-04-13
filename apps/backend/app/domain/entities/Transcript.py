"""
Domain entities for the MSBN Transcript Verification system.

These are pure-Python dataclasses with no ORM or framework coupling.
They represent the core business concepts and encapsulate business rules.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


# ── Value Objects / Enums ─────────────────────────────────────────────────────

class ApplicantType(str, Enum):
    FIRST_TIME = "first_time"
    ENDORSEMENT = "endorsement"


class VerificationStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    FLAGGED = "flagged"
    CLEARED = "cleared"
    OVERRIDDEN = "overridden"


class FlagSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class FlagCategory(str, Enum):
    GRADUATION = "graduation"
    PROGRAM_COMPLETION = "program_completion"
    ACCREDITATION = "accreditation"
    DOCUMENT_INTEGRITY = "document_integrity"
    FRAUD_INDICATOR = "fraud_indicator"
    FORMATTING = "formatting"


class AccreditationType(str, Enum):
    ACEN = "ACEN"
    CCNE = "CCNE"
    NLNAC = "NLNAC"
    OTHER = "OTHER"


class DocumentStatus(str, Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


# ── Domain Entities ───────────────────────────────────────────────────────────

@dataclass
class TranscriptFlag:
    """
    A single flag raised during transcript verification.
    Immutable record of an anomaly or concern.
    """
    flag_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    category: FlagCategory = FlagCategory.DOCUMENT_INTEGRITY
    severity: FlagSeverity = FlagSeverity.WARNING
    rule_code: str = ""
    rule_description: str = ""
    evidence: str = ""          # Excerpt from transcript that triggered the flag
    explanation: str = ""       # Human-readable rationale
    source_section: str = ""    # Section of transcript (e.g., "Course List", "Degree Header")
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_critical(self) -> bool:
        return self.severity == FlagSeverity.CRITICAL


@dataclass
class StaffAnnotation:
    """Staff member's review note on a verification or flag."""
    annotation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    staff_user_id: str = ""
    note: str = ""
    overrides_flag_id: str | None = None   # If set, this annotation overrides a specific flag
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ExtractedTranscriptData:
    """
    Structured data extracted from a raw transcript by the AI pipeline.
    Represents what the AI parsed, not the verification result.
    """
    institution_name: str = ""
    program_name: str = ""
    degree_awarded: str = ""
    graduation_date: str = ""
    graduation_confirmed: bool = False
    total_credits: float = 0.0
    nursing_credits: float = 0.0
    courses: list[dict] = field(default_factory=list)
    accreditation_type: str = ""
    accreditation_body: str = ""
    raw_text: str = ""
    extraction_confidence: float = 0.0   # 0.0–1.0
    extraction_notes: str = ""


@dataclass
class VerificationSummary:
    """
    Explainable, audit-ready result of a transcript verification pass.
    Contains every rule applied, all flags, and the final determination.
    """
    summary_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    verification_id: str = ""
    rules_applied: list[str] = field(default_factory=list)
    flags: list[TranscriptFlag] = field(default_factory=list)
    extracted_data: ExtractedTranscriptData = field(default_factory=ExtractedTranscriptData)
    overall_status: VerificationStatus = VerificationStatus.PENDING
    ai_recommendation: str = ""   # Advisory text only — never a decision
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ── Business rules ────────────────────────────────────────────────────────

    def has_critical_flags(self) -> bool:
        return any(f.is_critical() for f in self.flags)

    def get_flags_by_category(self, category: FlagCategory) -> list[TranscriptFlag]:
        return [f for f in self.flags if f.category == category]

    def flag_count_by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {s.value: 0 for s in FlagSeverity}
        for flag in self.flags:
            counts[flag.severity.value] += 1
        return counts

    def compute_status(self) -> VerificationStatus:
        """Derive status from flags — pure business logic, no side effects."""
        if not self.flags:
            return VerificationStatus.CLEARED
        if self.has_critical_flags():
            return VerificationStatus.FLAGGED
        return VerificationStatus.FLAGGED


@dataclass
class TranscriptDocument:
    """
    Uploaded transcript document entity. Holds metadata; the file itself
    lives in the storage layer.
    """
    document_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    verification_id: str = ""
    original_filename: str = ""
    mime_type: str = ""
    file_size_bytes: int = 0
    storage_path: str = ""
    checksum_sha256: str = ""
    status: DocumentStatus = DocumentStatus.UPLOADED
    uploaded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processed_at: datetime | None = None


@dataclass
class TranscriptVerification:
    """
    Aggregate root — the central entity that ties together an applicant's
    transcript submission, extracted data, flags, annotations, and status.
    """
    verification_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    applicant_id: str = ""
    applicant_type: ApplicantType = ApplicantType.FIRST_TIME
    assigned_staff_id: str | None = None
    documents: list[TranscriptDocument] = field(default_factory=list)
    summary: VerificationSummary | None = None
    annotations: list[StaffAnnotation] = field(default_factory=list)
    status: VerificationStatus = VerificationStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    # ── Aggregate business rules ──────────────────────────────────────────────

    def add_document(self, document: TranscriptDocument) -> None:
        document.verification_id = self.verification_id
        self.documents.append(document)
        self._touch()

    def attach_summary(self, summary: VerificationSummary) -> None:
        summary.verification_id = self.verification_id
        self.summary = summary
        self.status = summary.compute_status()
        self._touch()

    def add_annotation(self, annotation: StaffAnnotation) -> None:
        self.annotations.append(annotation)
        self._touch()

    def override(self, staff_user_id: str, justification: str) -> None:
        """Staff manually overrides the AI recommendation."""
        self.status = VerificationStatus.OVERRIDDEN
        note = StaffAnnotation(
            staff_user_id=staff_user_id,
            note=f"[OVERRIDE] {justification}",
        )
        self.annotations.append(note)
        self._touch()

    def _touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)


@dataclass
class NursingSchool:
    """Mississippi-accredited nursing school from the MSBN reference list."""
    school_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    program_type: str = ""          # e.g., "BSN", "ADN"
    accreditation_type: AccreditationType = AccreditationType.ACEN
    accreditation_body: str = ""
    is_active: bool = True
    state: str = "MS"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class User:
    """MSBN staff user who reviews verifications."""
    user_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    email: str = ""
    full_name: str = ""
    hashed_password: str = ""
    role: str = "reviewer"       # reviewer | admin
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))