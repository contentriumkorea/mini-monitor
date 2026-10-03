from __future__ import annotations

import threading
import time
from typing import Generic, TypeVar


T = TypeVar("T")


class QueueClosed(RuntimeError):
    pass


class LatestWinsQueue(Generic[T]):
    """A single-slot queue: a new pending item replaces the stale one."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._pending: T | None = None
        self._has_pending = False
        self._closed = False
        self._replaced = 0

    @property
    def pending_count(self) -> int:
        with self._condition:
            return int(self._has_pending)

    @property
    def replaced_count(self) -> int:
        with self._condition:
            return self._replaced

    def put(self, item: T) -> None:
        with self._condition:
            if self._closed:
                raise QueueClosed("queue is closed")
            if self._has_pending:
                self._replaced += 1
            self._pending = item
            self._has_pending = True
            self._condition.notify()

    def get(self, timeout: float | None = None) -> T | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._has_pending and not self._closed:
                if deadline is None:
                    self._condition.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    self._condition.wait(remaining)
            if not self._has_pending:
                if self._closed:
                    raise QueueClosed("queue is closed")
                return None
            item = self._pending
            self._pending = None
            self._has_pending = False
            return item

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._has_pending = False
            self._condition.notify_all()

    def __len__(self) -> int:
        return self.pending_count

