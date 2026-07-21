"""Cross-platform single-instance and shutdown primitives."""

from __future__ import annotations

from types import TracebackType

from filelock import FileLock

from .config import CoreConfig


class CoreInstanceLock:
    def __init__(self, config: CoreConfig, timeout: float = 0.0) -> None:
        config.prepare()
        self._lock = FileLock(str(config.lock_path), timeout=timeout)

    def __enter__(self) -> CoreInstanceLock:
        self._lock.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._lock.release()
