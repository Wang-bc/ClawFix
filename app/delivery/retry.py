from __future__ import annotations

from datetime import datetime, timedelta


def next_attempt_at(retry_count: int) -> str:
    seconds = min(300, 2**retry_count)
    return (datetime.now() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def due_for_retry(next_attempt_at_value: str) -> bool:
    return datetime.fromisoformat(next_attempt_at_value) <= datetime.now()
