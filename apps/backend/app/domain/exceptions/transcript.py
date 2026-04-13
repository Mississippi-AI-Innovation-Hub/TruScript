"""Transcript verification domain exceptions."""


class TranscriptNotFoundError(Exception):
    """Raised when a TranscriptVerification record cannot be located."""

    def __init__(self, verification_id: str) -> None:
        super().__init__(f"Transcript verification '{verification_id}' not found.")
        self.verification_id = verification_id


class TranscriptAlreadyExistsError(Exception):
    """Raised on an attempt to create a duplicate record."""

    def __init__(self, verification_id: str) -> None:
        super().__init__(f"Transcript verification '{verification_id}' already exists.")
        self.verification_id = verification_id


class TranscriptValidationError(Exception):
    """Raised when business-rule validation fails on a transcript record."""
