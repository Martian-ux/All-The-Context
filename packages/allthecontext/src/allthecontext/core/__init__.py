"""Local authoritative Core service."""

from .app import create_app
from .service import CoreService

__all__ = ["CoreService", "create_app"]
