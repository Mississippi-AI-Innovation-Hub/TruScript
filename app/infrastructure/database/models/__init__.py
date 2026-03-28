"""
Import all ORM models here so Alembic's env.py can find them via Base.metadata.
"""
from app.infrastructure.database.models.flag_review_model import FlagReviewModel  # noqa: F401
from app.infrastructure.database.models.transcript_model import TranscriptVerificationModel  # noqa: F401

__all__ = ["FlagReviewModel", "TranscriptVerificationModel"]
