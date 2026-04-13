from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime

from app.config.models import InboundMessage
from app.config.settings import Settings
from app.gateway.inbound_pipeline import InboundPipeline


class FeishuChannel:
    def __init__(self, settings: Settings, inbound_pipeline: InboundPipeline) -> None:
        self.settings = settings
        self.inbound_pipeline = inbound_pipeline
        self._token_cache: dict[str, object] = {"token": "", "expire_at": 0.0}

    def parse_event(self, payload: dict[str, object]) -> InboundMessage | None:
        if "challenge" in payload:
            return None

        event = payload.get("event", {})
        if not isinstance(event, dict):
            return None
        message = event.get("message", {})
        sender = event.get("sender", {})
        if not isinstance(message, dict):
            return None
        if isinstance(sender, dict) and str(sender.get("sender_type", "")).lower() == "app":
            return None
        if not message or message.get("message_type") != "text":
            return None

        content_text = self._extract_text(message.get("content", ""))
        if not content_text:
            return None
        message_id = str(message.get("message_id", ""))
        if not message_id or not self.inbound_pipeline.accept(message_id):
            return None

        sender_id_info = sender.get("sender_id", {})
        sender_id = str(
            sender_id_info.get("open_id")
            or sender_id_info.get("user_id")
            or sender_id_info.get("union_id")
            or "unknown-feishu-user"
        )
        raw_time = str(message.get("create_time", ""))
        received_at = self._format_timestamp(raw_time)
        inbound = self.inbound_pipeline.build_inbound(
            channel="feishu",
            account_id=self.settings.feishu_app_id or "local-feishu",
            peer_id=str(message.get("chat_id", "")),
            parent_peer_id=None,
            sender_id=sender_id,
            sender_name=None,
            text=content_text,
            raw_payload=payload,
            reply_to_message_id=None,
            message_id=message_id,
        )
        inbound["received_at"] = received_at
        return inbound

    def send_text(
        self,
        account_id: str,
        peer_id: str,
        text: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        _ = (account_id, metadata)
        if not self.settings.feishu_app_id or not self.settings.feishu_app_secret:
            return {
                "sent": False,
                "channel": "feishu",
                "reason": "missing_credentials",
                "preview": text[:120],
            }

        token = self._get_tenant_access_token()
        url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
        payload = {
            "receive_id": peer_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = self._read_response_body(response)
        except urllib.error.HTTPError as exc:
            return {
                "sent": False,
                "channel": "feishu",
                "status": exc.code,
                "error": self._read_error_body(exc),
            }

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return {"sent": False, "channel": "feishu", "error": f"invalid_response: {body[:200]}"}
        if int(data.get("code", 0)) != 0:
            return {
                "sent": False,
                "channel": "feishu",
                "code": data.get("code"),
                "msg": str(data.get("msg", "")).strip() or body[:200],
                "body": body,
            }
        return {"sent": True, "channel": "feishu", "body": body}

    def _get_tenant_access_token(self) -> str:
        if self._token_cache["token"] and float(self._token_cache["expire_at"]) > time.time():
            return str(self._token_cache["token"])

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.settings.feishu_app_id,
            "app_secret": self.settings.feishu_app_secret,
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(self._read_response_body(response))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"feishu_token_request_failed: {exc.code} {self._read_error_body(exc)}") from exc
        if int(data.get("code", 0)) != 0:
            raise RuntimeError(f"feishu_token_request_failed: {data.get('code')} {data.get('msg', '')}".strip())
        token = str(data["tenant_access_token"])
        expire = int(data.get("expire", 7200))
        self._token_cache = {"token": token, "expire_at": time.time() + expire - 60}
        return token

    def _extract_text(self, content: str) -> str:
        try:
            data = json.loads(content)
            return str(data.get("text", "")).strip()
        except json.JSONDecodeError:
            return str(content).strip()

    def _format_timestamp(self, value: str) -> str:
        if value.isdigit():
            seconds = int(value) / 1000 if len(value) > 10 else int(value)
            return datetime.fromtimestamp(seconds).isoformat(timespec="seconds")
        return datetime.now().isoformat(timespec="seconds")

    def _read_response_body(self, response) -> str:  # noqa: ANN001
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")

    def _read_error_body(self, exc: urllib.error.HTTPError) -> str:
        charset = exc.headers.get_content_charset() or "utf-8"
        return exc.read().decode(charset, errors="replace")
