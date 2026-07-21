"""Portable sortable identifiers and UTC timestamps."""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime
from uuid import UUID


def new_id() -> str:
    """Return a UUIDv7-compatible identifier without third-party dependencies."""
    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    random_bits = int.from_bytes(secrets.token_bytes(10), "big")
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= (random_bits >> 68) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return str(UUID(int=value))


def utc_now() -> str:
    """Return a stable, lexically sortable UTC timestamp."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
