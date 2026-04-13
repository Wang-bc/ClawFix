from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from uuid import uuid4

from app.config.models import InboundMessage


class InboundPipeline:
    def __init__(self, max_cache_size: int = 2000) -> None:
        self.max_cache_size = max_cache_size
        self._seen: "OrderedDict[str, str]" = OrderedDict()

    def accept(self, message_id: str) -> bool:
        if message_id in self._seen:
            return False
        self._seen[message_id] = datetime.now().isoformat(timespec="seconds")
        while len(self._seen) > self.max_cache_size:
            self._seen.popitem(last=False)
        return True

    def build_inbound(
        self,
        *,
        channel: str,
        account_id: str,
        peer_id: str,
        sender_id: str,
        sender_name: str | None,
        text: str,
        raw_payload: dict[str, object],
        parent_peer_id: str | None = None,
        reply_to_message_id: str | None = None,
        message_id: str | None = None,
    ) -> InboundMessage:
        return InboundMessage(
            message_id=message_id or uuid4().hex,
            channel=channel,
            account_id=account_id,
            peer_id=peer_id,
            parent_peer_id=parent_peer_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text.strip(),
            raw_payload=raw_payload,
            received_at=datetime.now().isoformat(timespec="seconds"),
            reply_to_message_id=reply_to_message_id,
        )

