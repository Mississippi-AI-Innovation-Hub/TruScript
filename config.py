"""
Root-level re-export for backward compatibility.
Canonical implementation lives in app/core/config.py.
"""
from app.core.config import Settings, get_settings  # noqa: F401

__all__ = ["Settings", "get_settings"]
