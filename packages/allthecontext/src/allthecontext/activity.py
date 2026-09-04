"""Shared Core activity barrier used by update activation and lifecycle work."""

from __future__ import annotations

import asyncio
import threading
import weakref
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from typing import Any


class _ActivityOwner:
    """Task-bound identity carried through intentional sync/async composition."""

    __slots__ = ("__weakref__", "_gates", "_task_ref")

    def __init__(self, task: asyncio.Task[Any]) -> None:
        self._gates: weakref.WeakSet[CoreActivityGate] = weakref.WeakSet()
        owner_ref = weakref.ref(self)

        def task_collected(_task_ref: weakref.ReferenceType[asyncio.Task[Any]]) -> None:
            owner = owner_ref()
            if owner is not None:
                owner._release()

        self._task_ref = weakref.ref(task, task_collected)
        task.add_done_callback(self._task_done)

    def matches(self, task: asyncio.Task[Any]) -> bool:
        return self._task_ref() is task

    def watch(self, gate: CoreActivityGate) -> None:
        self._gates.add(gate)

    def _task_done(self, _task: asyncio.Future[Any]) -> None:
        self._release()

    def _release(self) -> None:
        for gate in tuple(self._gates):
            gate._release_owner(self)


_activity_owner: ContextVar[_ActivityOwner | None] = ContextVar("atc_activity_owner", default=None)


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
    def _current_task() -> asyncio.Task[Any] | None:
        try:
            return asyncio.current_task()
        except RuntimeError:
            return None

    def _current_owner(self) -> object:
        task = self._current_task()
        owner = _activity_owner.get()
        if task is None:
            if owner is not None:
                owner.watch(self)
                return owner
            return ("thread", threading.get_ident())
        if owner is None or not owner.matches(task):
            # ContextVars are copied into child tasks. Replace an inherited
            # owner as soon as a child uses a synchronous helper so that a
            # later thread handoff cannot accidentally reuse the parent.
            owner = _ActivityOwner(task)
            _activity_owner.set(owner)
        owner.watch(self)
        return owner

    def _release_owner(self, owner: _ActivityOwner) -> None:
        with self._condition:
            active = self._active_by_owner.pop(owner, 0)
            if active:
                self._active_count -= active
            if self._exclusive_owner == owner:
                self._exclusive_owner = None
                self._exclusive_depth = 0
            if active or self._exclusive_owner is None:
                self._condition.notify_all()

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
            active = self._active_by_owner.get(owner, 0)
            if not active:
                return
            self._active_count -= 1
            remaining = active - 1
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

        task = self._current_task()
        if task is None:
            raise RuntimeError("Core async activity requires a running asyncio task")
        owner = _activity_owner.get()
        context_token = None
        entered = False
        if owner is None or not owner.matches(task):
            owner = _ActivityOwner(task)
            context_token = _activity_owner.set(owner)
        owner.watch(self)
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
                # A task destroyed while suspended can close this async
                # generator from a different context. Its gate state has
                # already been released above; the abandoned context is
                # discarded with the task.
                with suppress(ValueError):
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
