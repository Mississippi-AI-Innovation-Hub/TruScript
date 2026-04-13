"""
Abstract repository port for TranscriptVerification.

The domain layer defines the contract; the infrastructure layer fulfils it.
This satisfies the Dependency Inversion Principle — high-level use cases
depend only on this abstraction, never on SQLAlchemy or any ORM directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.entities.Transcript import TranscriptVerification


class TranscriptRepository(ABC):
    """Port (interface) for transcript verification persistence."""

    @abstractmethod
    async def create(self, transcript: TranscriptVerification) -> TranscriptVerification:
        """Persist a new transcript verification and return the saved entity."""
        raise NotImplementedError

    @abstractmethod
    async def get_by_id(self, verification_id: str) -> TranscriptVerification | None:
        """Return the entity with the given ID, or None if not found."""
        raise NotImplementedError

    @abstractmethod
    async def list_all(
        self,
        skip: int = 0,
        limit: int = 20,
    ) -> list[TranscriptVerification]:
        """Return a paginated list of all transcript verifications."""
        raise NotImplementedError

    @abstractmethod
    async def update(self, transcript: TranscriptVerification) -> TranscriptVerification:
        """Persist changes to an existing entity and return the updated version."""
        raise NotImplementedError

    @abstractmethod
    async def delete(self, verification_id: str) -> bool:
        """
        Remove the entity permanently.
        Returns True if deleted, False if not found.
        """
        raise NotImplementedError
