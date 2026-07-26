"""Browser handoff ticket and short-lived session unit coverage (B-202 readiness)."""

from __future__ import annotations

import time

from allthecontext.browser_session import BrowserSessions, BrowserSessionTickets


def test_ticket_is_one_use_and_non_replayable() -> None:
    tickets = BrowserSessionTickets(lifetime_seconds=60.0)
    ticket = tickets.issue("core-credential")
    assert tickets.consume(ticket) == "core-credential"
    assert tickets.consume(ticket) is None


def test_ticket_expires() -> None:
    tickets = BrowserSessionTickets(lifetime_seconds=0.01)
    ticket = tickets.issue("core-credential")
    time.sleep(0.03)
    assert tickets.consume(ticket) is None


def test_browser_session_expires_and_revokes() -> None:
    sessions = BrowserSessions(lifetime_seconds=0.01)
    token = sessions.issue("core-credential")
    assert sessions.resolve(token) == "core-credential"
    time.sleep(0.03)
    assert sessions.resolve(token) is None

    token = sessions.issue("core-credential")
    sessions.revoke(token)
    assert sessions.resolve(token) is None


def test_browser_session_revoke_all() -> None:
    sessions = BrowserSessions(lifetime_seconds=3600.0)
    first = sessions.issue("a")
    second = sessions.issue("b")
    assert sessions.active_count() == 2
    assert sessions.revoke_all() == 2
    assert sessions.resolve(first) is None
    assert sessions.resolve(second) is None
    assert sessions.active_count() == 0
