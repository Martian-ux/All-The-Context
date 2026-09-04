"""Shared Core activity barrier used by update activation and lifecycle work."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar

_activity_owner: ContextVar[object | None] = ContextVar("atc_activity_owner", default=None)


class CoreActivityGate:
    """Writer-priority reentrant activity barrier for update activation."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._active_count = 0
        self._active_by_owner: dict[object, int] = {}
        self._exclusive_owner: object | None = None
        self._exclusive_depth = 0
        self._waiting_exclusive = 0

    @staticmethod
    def _current_owner() -> object:
        owner = _activity_owner.get()
        if owner is not None:
            return owner
        return ("thread", threading.get_ident())

    def _enter(self, owner: object) -> None:
        with self._condition:
            reentrant = self._active_by_owner.get(owner, 0) > 0 or self._exclusive_owner == owner
            while not reentrant and (
                self._exclusive_owner is not None or self._waiting_exclusive > 0
            ):
                self._condition.wait()
            self._active_count += 1
            self._active_by_owner[owner] = self._active_by_owner.get(owner, 0) + 1

    def _leave(self, owner: object) -> None:
        with self._condition:
            self._active_count -= 1
            remaining = self._active_by_owner[owner] - 1
            if remaining:
                self._active_by_owner[owner] = remaining
            else:
                del self._active_by_owner[owner]
            self._condition.notify_all()

    @contextmanager
    def activity(self) -> Iterator[None]:
        """Enter a shared Core activity section."""

        owner = self._current_owner()
        self._enter(owner)
        try:
            yield
        finally:
            self._leave(owner)

    @asynccontextmanager
    async def activity_async(self) -> AsyncIterator[None]:
        """Enter a shared activity section without blocking an event loop."""

        owner = _activity_owner.get()
        context_token = None
        entered = False
        if owner is None:
            owner = object()
            context_token = _activity_owner.set(owner)
        try:
            while True:
                with self._condition:
                    reentrant = (
                        self._active_by_owner.get(owner, 0) > 0 or self._exclusive_owner == owner
                    )
                    if reentrant or (
                        self._exclusive_owner is None and self._waiting_exclusive == 0
                    ):
                        self._active_count += 1
                        self._active_by_owner[owner] = self._active_by_owner.get(owner, 0) + 1
                        entered = True
                        break
                await asyncio.sleep(0.01)
            yield
        finally:
            if entered:
                self._leave(owner)
            if context_token is not None:
                _activity_owner.reset(context_token)

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Enter an exclusive section that quiesces and fences new activity."""

        owner = self._current_owner()
        with self._condition:
            if self._exclusive_owner == owner:
                self._exclusive_depth += 1
            else:
                self._waiting_exclusive += 1
                try:
                    while self._exclusive_owner is not None or self._active_count:
                        self._condition.wait()
                finally:
                    self._waiting_exclusive -= 1
                self._exclusive_owner = owner
                self._exclusive_depth = 1
        try:
            yield
        finally:
            with self._condition:
                if self._exclusive_owner != owner:
                    raise RuntimeError("Core activity barrier ownership was lost")
                self._exclusive_depth -= 1
                if self._exclusive_depth == 0:
                    self._exclusive_owner = None
                    self._condition.notify_all()
