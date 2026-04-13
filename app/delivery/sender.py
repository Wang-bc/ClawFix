from __future__ import annotations

from app.channels.base import Sender


class SenderRegistry:
    def __init__(self) -> None:
        self._senders: dict[str, Sender] = {}

    def register(self, channel: str, sender: Sender) -> None:
        self._senders[channel] = sender

    def send(
        self,
        channel: str,
        account_id: str,
        peer_id: str,
        text: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        sender = self._senders[channel]
        return sender(account_id, peer_id, text, metadata)

