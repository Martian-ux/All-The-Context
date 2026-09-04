"""Shared Core activity barrier used by update activation and lifecycle work."""

from __future__ import annotations

import asyncio
import threading
import weakref
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


class _ActivityOwner:
    """An owner tied to one exact asyncio task, never to copied context."""

    __slots__ = ("__weakref__", "_gate_ref", "_task_ref")

    def __init__(self, gate: CoreActivityGate, task: asyncio.Task[Any]) -> None:
        self._gate_ref = weakref.ref(gate)
        owner_ref = weakref.ref(self)

        def task_collected(_task_ref: weakref.ReferenceType[asyncio.Task[Any]]) -> None:
            owner = owner_ref()
            if owner is not None:
                owner._release()

        self._task_ref = weakref.ref(task, task_collected)
        task.add_done_callback(self._task_done)

    def _task_done(self, _task: asyncio.Future[Any]) -> None:
        self._release()

    def _release(self) -> None:
        gate = self._gate_ref()
        if gate is not None:
            gate._release_owner(self)


class _ActivityLease:
    """One non-replayable admission handoff to one synchronous worker."""

    __slots__ = (
        "_cancelled_before_start",
        "_gate",
        "_lock",
        "_owner",
        "_released",
        "_worker_thread_id",
    )

    def __init__(self, gate: CoreActivityGate, owner: _ActivityOwner) -> None:
        self._gate = gate
        self._owner = owner
        self._lock = threading.Lock()
        self._released = False
        self._cancelled_before_start = False
        self._worker_thread_id: int | None = None

    @property
    def owner(self) -> _ActivityOwner:
        return self._owner

    def start_worker(self) -> bool:
        """Claim the lease for its one worker, or reject a canceled dispatch."""

        with self._lock:
            if self._released or self._cancelled_before_start:
                return False
            if self._worker_thread_id is not None:
                raise RuntimeError("Core activity lease was dispatched more than once")
            self._worker_thread_id = threading.get_ident()
            return True

    def cancel_before_start(self) -> bool:
        """Cancel only if the worker has not begun executing."""

        with self._lock:
            if self._worker_thread_id is not None or self._released:
                return False
            self._cancelled_before_start = True
            return True

    def usable_in_current_thread(self) -> bool:
        with self._lock:
            return not self._released and self._worker_thread_id == threading.get_ident()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._gate._release_lease(self)


# This is deliberately thread-local rather than a ContextVar. Context copying
# must not copy a worker's handoff authority into an unrelated callback/task.
_activity_thread_state = threading.local()


class CoreActivityGate:
    """Writer-priority reentrant activity barrier for update activation."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._active_count = 0
        self._active_by_owner: dict[object, int] = {}
        self._direct_by_owner: dict[_ActivityOwner, int] = {}
        self._owners_by_task: weakref.WeakKeyDictionary[asyncio.Task[Any], _ActivityOwner] = (
            weakref.WeakKeyDictionary()
        )
        self._delegated_workers: set[asyncio.Future[Any]] = set()
        self._exclusive_owner: object | None = None
        self._exclusive_depth = 0
        self._waiting_exclusive = 0

    @staticmethod
    def _current_task() -> asyncio.Task[Any] | None:
        try:
            return asyncio.current_task()
        except RuntimeError:
            return None

    def _owner_for_task(self, task: asyncio.Task[Any]) -> _ActivityOwner:
        with self._condition:
            owner = self._owners_by_task.get(task)
            if owner is None:
                owner = _ActivityOwner(self, task)
                self._owners_by_task[task] = owner
            return owner

    def _current_owner(self) -> tuple[object, _ActivityLease | None]:
        task = self._current_task()
        if task is not None:
            # A task is the only ambient authority. ContextVar copies retain no
            # authority because the owner is resolved from the exact task.
            return self._owner_for_task(task), None

        lease = getattr(_activity_thread_state, "lease", None)
        if lease is not None and lease._gate is self and lease.usable_in_current_thread():
            return lease.owner, lease

        # No-task callbacks and copied contexts use an ordinary thread owner.
        # They cannot inherit a task's admission accidentally.
        return ("thread", threading.get_ident()), None

    def _release_owner(self, owner: _ActivityOwner) -> None:
        with self._condition:
            active = self._direct_by_owner.pop(owner, 0)
            if active:
                self._active_count -= active
                remaining = self._active_by_owner.get(owner, 0) - active
                if remaining:
                    self._active_by_owner[owner] = remaining
                else:
                    self._active_by_owner.pop(owner, None)
            if self._exclusive_owner == owner:
                self._exclusive_owner = None
                self._exclusive_depth = 0
            if active or self._exclusive_owner is None:
                self._condition.notify_all()

    def _release_lease(self, lease: _ActivityLease) -> None:
        with self._condition:
            active = self._active_by_owner.get(lease.owner, 0)
            if not active:
                return
            self._active_count -= 1
            remaining = active - 1
            if remaining:
                self._active_by_owner[lease.owner] = remaining
            else:
                self._active_by_owner.pop(lease.owner, None)
            self._condition.notify_all()

    def _enter(self, owner: object, lease: _ActivityLease | None = None) -> None:
        with self._condition:
            reentrant = self._active_by_owner.get(owner, 0) > 0 or self._exclusive_owner == owner
            while not reentrant and (
                self._exclusive_owner is not None or self._waiting_exclusive > 0
            ):
                self._condition.wait()
            self._active_count += 1
            self._active_by_owner[owner] = self._active_by_owner.get(owner, 0) + 1
            if lease is None and isinstance(owner, _ActivityOwner):
                self._direct_by_owner[owner] = self._direct_by_owner.get(owner, 0) + 1

    def _leave(self, owner: object, lease: _ActivityLease | None = None) -> None:
        with self._condition:
            if (
                lease is None
                and isinstance(owner, _ActivityOwner)
                and not self._direct_by_owner.get(owner, 0)
            ):
                return
            active = self._active_by_owner.get(owner, 0)
            if not active:
                return
            self._active_count -= 1
            remaining = active - 1
            if remaining:
                self._active_by_owner[owner] = remaining
            else:
                del self._active_by_owner[owner]
            if lease is None and isinstance(owner, _ActivityOwner):
                direct = self._direct_by_owner.get(owner, 0)
                if direct <= 1:
                    self._direct_by_owner.pop(owner, None)
                else:
                    self._direct_by_owner[owner] = direct - 1
            self._condition.notify_all()

    @contextmanager
    def activity(self) -> Iterator[None]:
        """Enter a shared Core activity section."""

        owner, lease = self._current_owner()
        self._enter(owner, lease)
        try:
            yield
        finally:
            self._leave(owner, lease)

    @asynccontextmanager
    async def activity_async(self) -> AsyncIterator[None]:
        """Enter a shared activity section without blocking an event loop."""

        task = self._current_task()
        if task is None:
            raise RuntimeError("Core async activity requires a running asyncio task")
        owner = self._owner_for_task(task)
        entered = False
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
                        self._direct_by_owner[owner] = self._direct_by_owner.get(owner, 0) + 1
                        entered = True
                        break
                await asyncio.sleep(0.01)
            yield
        finally:
            if entered:
                self._leave(owner)

    async def run_in_threadpool(
        self,
        func: Callable[P, R],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        """Run a sync helper with an explicit, completion-bound admission lease."""

        async with self.activity_async():
            task = self._current_task()
            if task is None:  # pragma: no cover - activity_async already checks this
                raise RuntimeError("Core threadpool handoff requires a running task")
            owner = self._owner_for_task(task)
            lease = self._acquire_lease(owner)
            worker_task = asyncio.create_task(
                asyncio.to_thread(self._run_with_lease, lease, func, args, kwargs)
            )

            def worker_done(completed: asyncio.Future[Any]) -> None:
                self._delegated_workers.discard(completed)
                if completed.cancelled():
                    # A loop shutdown can cancel the asyncio wrapper while
                    # the underlying thread is still running. Release only a
                    # dispatch that never started; a started worker releases
                    # from its own finally block.
                    if lease.cancel_before_start():
                        lease.release()
                else:
                    lease.release()
                    # A canceled awaiter may never retrieve the worker's
                    # exception; observing it here prevents a shutdown-time
                    # "Task exception was never retrieved" report.
                    completed.exception()

            self._delegated_workers.add(worker_task)
            worker_task.add_done_callback(worker_done)
            try:
                # The worker must outlive cancellation of the awaiting task.
                return await asyncio.shield(worker_task)
            except asyncio.CancelledError:
                if lease.cancel_before_start():
                    worker_task.cancel()
                raise

    def _acquire_lease(self, owner: _ActivityOwner) -> _ActivityLease:
        lease = _ActivityLease(self, owner)
        with self._condition:
            if self._active_by_owner.get(owner, 0) <= 0:
                raise RuntimeError("Core activity lease requires task-owned admission")
            self._active_count += 1
            self._active_by_owner[owner] = self._active_by_owner.get(owner, 0) + 1
        return lease

    @staticmethod
    def _run_with_lease(
        lease: _ActivityLease,
        func: Callable[P, R],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> R:
        if not lease.start_worker():
            lease.release()
            raise asyncio.CancelledError
        previous_lease = getattr(_activity_thread_state, "lease", None)
        _activity_thread_state.lease = lease
        try:
            return func(*args, **kwargs)
        finally:
            if previous_lease is None:
                del _activity_thread_state.lease
            else:
                _activity_thread_state.lease = previous_lease
            lease.release()

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Enter an exclusive section that quiesces and fences new activity."""

        owner, _lease = self._current_owner()
        with self._condition:
            if self._exclusive_owner == owner:
                self._exclusive_depth += 1
            else:
                self._waiting_exclusive += 1
                try:
                    while self._exclusive_owner is not None or (
                        self._active_count - self._active_by_owner.get(owner, 0)
                    ):
                        self._condition.wait()
                finally:
                    self._waiting_exclusive -= 1
                # If this owner had active shared work, the wait above ignores
                # only that owner's holds. This is an upgrade, not a second
                # independent writer, so it cannot wait on its own lease.
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
