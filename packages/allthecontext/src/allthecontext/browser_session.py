"""Origin-bound browser handoff and short-lived in-memory dashboard sessions."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

BROWSER_AUTH_SCHEME = "Browser"
BROWSER_STORAGE_KEY = "atc.browserSession"
DASHBOARD_REQUEST_HEADER = "X-ATC-Dashboard"
LEGACY_BROWSER_COOKIE = "atc_browser_session"


@dataclass(frozen=True, slots=True)
class _Ticket:
    credential: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class _BrowserSession:
    credential: str
    expires_at: float


class BrowserSessionTickets:
    """Exchange an authenticated desktop launch for a one-use browser ticket."""

    def __init__(self, *, lifetime_seconds: float = 60.0) -> None:
        self.lifetime_seconds = lifetime_seconds
        self._tickets: dict[str, _Ticket] = {}
        self._lock = threading.Lock()

    def issue(self, credential: str) -> str:
        ticket = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._discard_expired(now)
            self._tickets[ticket] = _Ticket(credential, now + self.lifetime_seconds)
        return ticket

    def consume(self, ticket: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            self._discard_expired(now)
            stored = self._tickets.pop(ticket, None)
        if stored is None or stored.expires_at <= now:
            return None
        return stored.credential

    def _discard_expired(self, now: float) -> None:
        expired = [key for key, value in self._tickets.items() if value.expires_at <= now]
        for key in expired:
            del self._tickets[key]


class BrowserSessions:
    """Keep browser-only capabilities in Core memory instead of durable cookies."""

    def __init__(self, *, lifetime_seconds: float = 8 * 60 * 60) -> None:
        self.lifetime_seconds = lifetime_seconds
        self._sessions: dict[str, _BrowserSession] = {}
        self._lock = threading.Lock()

    def issue(self, credential: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._discard_expired(now)
            self._sessions[token] = _BrowserSession(
                credential=credential,
                expires_at=now + self.lifetime_seconds,
            )
        return token

    def resolve(self, token: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            self._discard_expired(now)
            session = self._sessions.get(token)
        return session.credential if session is not None else None

    def revoke(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def _discard_expired(self, now: float) -> None:
        expired = [key for key, value in self._sessions.items() if value.expires_at <= now]
        for key in expired:
            del self._sessions[key]
