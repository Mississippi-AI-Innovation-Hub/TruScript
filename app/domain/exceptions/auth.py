"""Authentication-related domain exceptions."""


class InvalidTokenError(Exception):
    """Raised when a JWT token cannot be decoded or is malformed."""


class TokenExpiredError(Exception):
    """Raised when a JWT token has passed its expiration time."""
