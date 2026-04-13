"""Workflow domain exceptions."""


class FlagNotFoundError(Exception):
    """Raised when a flag_id cannot be located in the transcript's summary."""

    def __init__(self, flag_id: str, verification_id: str) -> None:
        super().__init__(
            f"Flag '{flag_id}' not found in transcript verification '{verification_id}'."
        )
        self.flag_id = flag_id
        self.verification_id = verification_id


class FlagAlreadyReviewedError(Exception):
    """Raised when a staff member attempts to re-review a flag they already reviewed."""

    def __init__(self, flag_id: str, staff_user_id: str) -> None:
        super().__init__(
            f"Flag '{flag_id}' has already been reviewed by staff '{staff_user_id}'."
        )
        self.flag_id = flag_id
        self.staff_user_id = staff_user_id


class WorkflowValidationError(Exception):
    """Raised when workflow business-rule validation fails."""
