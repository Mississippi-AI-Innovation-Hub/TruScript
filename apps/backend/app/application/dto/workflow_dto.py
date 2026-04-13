"""
Data Transfer Objects for workflow use cases.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.entities.workflow import FlagReviewAction


# ── Input DTOs ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReviewFlagDTO:
    """Input for the confirm-or-override use case."""
    verification_id: str
    flag_id: str
    action: FlagReviewAction
    staff_user_id: str
    justification: str | None = None   # Required when action == OVERRIDE
    note: str | None = None


@dataclass(frozen=True)
class AddAnnotationDTO:
    """Input for the add-annotation use case."""
    verification_id: str
    staff_user_id: str
    note: str
    overrides_flag_id: str | None = None


# ── Output DTOs ───────────────────────────────────────────────────────────────

@dataclass
class FlagReviewResponseDTO:
    review_id: str
    verification_id: str
    flag_id: str
    action: str
    staff_user_id: str
    justification: str | None
    note: str | None
    created_at: datetime
