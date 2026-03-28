"""
SQLAlchemy declarative base shared by all ORM models.
Import this — never create a second Base.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Single application-wide declarative base."""
