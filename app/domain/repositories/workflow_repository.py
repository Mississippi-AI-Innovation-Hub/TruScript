"""
Abstract repository port for FlagReview workflow records.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.entities.workflow import FlagReview


class WorkflowRepository(ABC):
    """Port (interface) for flag review persistence."""

    @abstractmethod
    async def save_review(self, review: FlagReview) -> FlagReview:
        """Persist a new flag review and return the saved entity."""
        raise NotImplementedError

    @abstractmethod
    async def get_reviews_for_verification(
        self, verification_id: str
    ) -> list[FlagReview]:
        """Return all flag reviews for a given transcript verification."""
        raise NotImplementedError

    @abstractmethod
    async def get_reviews_for_flag(
        self, verification_id: str, flag_id: str
    ) -> list[FlagReview]:
        """Return all reviews for a specific flag within a verification."""
        raise NotImplementedError

    @abstractmethod
    async def get_latest_review_for_flag(
        self, verification_id: str, flag_id: str
    ) -> FlagReview | None:
        """Return the most recent review for a specific flag, or None."""
        raise NotImplementedError
