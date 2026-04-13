from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable, TypeVar


T = TypeVar("T")


class LaneQueue:
    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

    def run(self, lane_name: str, callback: Callable[[], T]) -> T:
        with self._locks[lane_name]:
            return callback()
