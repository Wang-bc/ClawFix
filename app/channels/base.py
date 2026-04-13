from __future__ import annotations

from typing import Protocol


class Sender(Protocol):
    def __call__(
        self,
        account_id: str,
        peer_id: str,
        text: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        ...

