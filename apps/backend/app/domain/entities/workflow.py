"""
Workflow domain entities.

These represent the staff review actions taken against AI-generated flags:
  - FlagReview: a staff member's decision (CONFIRM or OVERRIDE) on a single flag.

Annotations already live on TranscriptVerification.annotations (JSONB) and are
managed via StaffAnnotation in Transcript.py.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class FlagReviewAction(str, Enum):
    CONFIRM = "confirm"     # Staff agrees the flag is valid
    OVERRIDE = "override"   # Staff disagrees and overrides the flag


@dataclass
class FlagReview:
    """
    An auditable record of a staff member's decision on a single AI-generated flag.

    Business rules:
      - `justification` is mandatory when action == OVERRIDE.
      - Once created, a review is immutable (new review supersedes the old one).
    """
    review_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    verification_id: str = ""
    flag_id: str = ""
    action: FlagReviewAction = FlagReviewAction.CONFIRM
    staff_user_id: str = ""
    justification: str | None = None   # Required for OVERRIDE
    note: str | None = None            # Optional free-text remark
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.action == FlagReviewAction.OVERRIDE and not self.justification:
            raise ValueError("justification is required when overriding a flag.")

    @classmethod
    def confirm(
        cls,
        verification_id: str,
        flag_id: str,
        staff_user_id: str,
        note: str | None = None,
    ) -> "FlagReview":
        return cls(
            verification_id=verification_id,
            flag_id=flag_id,
            action=FlagReviewAction.CONFIRM,
            staff_user_id=staff_user_id,
            note=note,
        )

    @classmethod
    def override(
        cls,
        verification_id: str,
        flag_id: str,
        staff_user_id: str,
        justification: str,
        note: str | None = None,
    ) -> "FlagReview":
        return cls(
            verification_id=verification_id,
            flag_id=flag_id,
            action=FlagReviewAction.OVERRIDE,
            staff_user_id=staff_user_id,
            justification=justification,
            note=note,
        )
