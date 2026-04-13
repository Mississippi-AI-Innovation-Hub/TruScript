"""
Local security utilities (password hashing).

Authentication is handled by Keycloak (see app/core/auth.py).
These helpers are available for any future local user management needs.
"""
from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)
