"""Shared Core activity barrier used by update activation and lifecycle work."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class CoreActivityGate:
    """Writer-priority reentrant activity barrier for update activation."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._active_count = 0
        self._active_by_thread: dict[int, int] = {}
        self._exclusive_owner: int | None = None
        self._exclusive_depth = 0
        self._waiting_exclusive = 0

    @contextmanager
    def activity(self) -> Iterator[None]:
        """Enter a shared Core activity section."""

        thread_id = threading.get_ident()
        with self._condition:
            reentrant = (
                self._active_by_thread.get(thread_id, 0) > 0 or self._exclusive_owner == thread_id
            )
            while not reentrant and (
                self._exclusive_owner is not None or self._waiting_exclusive > 0
            ):
                self._condition.wait()
            self._active_count += 1
            self._active_by_thread[thread_id] = self._active_by_thread.get(thread_id, 0) + 1
        try:
            yield
        finally:
            with self._condition:
                self._active_count -= 1
                remaining = self._active_by_thread[thread_id] - 1
                if remaining:
                    self._active_by_thread[thread_id] = remaining
                else:
                    del self._active_by_thread[thread_id]
                self._condition.notify_all()

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Enter an exclusive section that quiesces and fences new activity."""

        thread_id = threading.get_ident()
        with self._condition:
            if self._exclusive_owner == thread_id:
                self._exclusive_depth += 1
            else:
                self._waiting_exclusive += 1
                try:
                    while self._exclusive_owner is not None or self._active_count:
                        self._condition.wait()
                finally:
                    self._waiting_exclusive -= 1
                self._exclusive_owner = thread_id
                self._exclusive_depth = 1
        try:
            yield
        finally:
            with self._condition:
                if self._exclusive_owner != thread_id:
                    raise RuntimeError("Core activity barrier ownership was lost")
                self._exclusive_depth -= 1
                if self._exclusive_depth == 0:
                    self._exclusive_owner = None
                    self._condition.notify_all()
