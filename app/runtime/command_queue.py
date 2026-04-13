from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable, TypeVar


T = TypeVar("T")


class SessionCommandQueue:
    """同一 session 串行执行，满足最小并发约束。"""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._guard = threading.Lock()

    def run(self, session_key: str, callback: Callable[[], T]) -> T:
        with self._guard:
            lock = self._locks[session_key]
        with lock:
            return callback()

