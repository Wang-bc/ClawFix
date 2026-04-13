from __future__ import annotations

import asyncio
import importlib
import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.config.settings import Settings


logger = logging.getLogger("clawfix")


class FeishuLongConnection:
    def __init__(
        self,
        settings: Settings,
        payload_handler: Callable[[dict[str, object], str], None],
        sdk_module: object | None = None,
    ) -> None:
        self.settings = settings
        self.payload_handler = payload_handler
        self._sdk_module = sdk_module
        self._client: object | None = None
        self._thread: threading.Thread | None = None
        self._last_error = ""
        self._last_event_at = ""
        self._status = "idle"
        self._stop_event = threading.Event()

    def start(self) -> bool:
        if self.settings.feishu_connection_mode != "websocket":
            self._status = "disabled"
            return False
        if not self.settings.feishu_app_id or not self.settings.feishu_app_secret:
            self._status = "missing_credentials"
            logger.info("Feishu long connection skipped because credentials are missing")
            return False
        if self._thread and self._thread.is_alive():
            return True

        sdk_module = self._sdk_module or self._import_sdk()
        if not sdk_module:
            self._status = "sdk_missing"
            self._last_error = "missing_dependency:lark-oapi"
            logger.warning("Feishu long connection requires `lark-oapi`; install dependencies and restart the service")
            return False

        self._sdk_module = sdk_module
        self._stop_event.clear()
        self._status = "starting"
        self._thread = threading.Thread(target=self._run_forever, name="feishu-long-connection", daemon=True)
        self._thread.start()
        logger.info("Feishu long connection thread started mode=%s", self.settings.feishu_connection_mode)
        return True

    def stop(self) -> None:
        self._stop_event.set()
        client = self._client
        stop = getattr(client, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to stop Feishu long connection client cleanly")

    def status(self) -> dict[str, object]:
        return {
            "mode": self.settings.feishu_connection_mode,
            "configured": bool(self.settings.feishu_app_id and self.settings.feishu_app_secret),
            "status": self._status,
            "running": bool(self._thread and self._thread.is_alive()),
            "last_event_at": self._last_event_at,
            "last_error": self._last_error,
        }

    def _run_forever(self) -> None:
        sdk = self._sdk_module
        if sdk is None:
            self._status = "sdk_missing"
            return

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        if self._sdk_module is None:
            try:
                ws_module = importlib.import_module("lark_oapi.ws.client")
                if hasattr(ws_module, "loop"):
                    ws_module.loop = loop
            except Exception:  # noqa: BLE001
                # Some SDK versions do not expose a global loop reference.
                pass

        try:
            builder = sdk.EventDispatcherHandler.builder("", "")
            event_handler = (
                builder.register_p2_im_message_receive_v1(self._handle_message_event)
                .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(self._ignore_event)
                .register_p2_im_message_message_read_v1(self._ignore_event)
                .build()
            )
            log_level = getattr(getattr(sdk, "LogLevel", object), "INFO", None)
            self._client = sdk.ws.Client(
                self.settings.feishu_app_id,
                self.settings.feishu_app_secret,
                event_handler=event_handler,
                log_level=log_level,
            )
            self._status = "running"
            self._last_error = ""
            logger.info("Feishu long connection connected")
            self._client.start()
        except Exception as exc:  # noqa: BLE001
            self._status = "error"
            self._last_error = str(exc)
            logger.exception("Feishu long connection stopped unexpectedly")
        finally:
            if self._stop_event.is_set() or self._status == "running":
                self._status = "stopped"
            try:
                loop.stop()
            except Exception:  # noqa: BLE001
                pass
            loop.close()

    def _handle_message_event(self, data: object) -> None:
        payload = self._marshal_payload(data)
        self._last_event_at = datetime.now().isoformat(timespec="seconds")
        try:
            self.payload_handler(payload, "websocket")
        except Exception:  # noqa: BLE001
            logger.exception("Feishu long connection payload dispatch failed")

    def _ignore_event(self, data: object) -> None:
        payload = self._marshal_payload(data)
        header = payload.get("header", {})
        event_type = header.get("event_type", "") if isinstance(header, dict) else ""
        logger.info("Ignored Feishu long-connection event event_type=%s", event_type)

    def _marshal_payload(self, data: object) -> dict[str, object]:
        sdk = self._sdk_module
        if sdk is None:
            raise RuntimeError("feishu_long_connection_sdk_unavailable")

        marshal = getattr(getattr(sdk, "JSON", object), "marshal", None)
        if not callable(marshal):
            raise RuntimeError("feishu_long_connection_sdk_json_marshal_missing")

        raw = marshal(data)
        if isinstance(raw, bytes):
            return json.loads(raw.decode("utf-8"))
        if isinstance(raw, str):
            return json.loads(raw)
        if isinstance(raw, dict):
            return raw
        raise RuntimeError(f"unsupported_feishu_payload_type:{type(raw).__name__}")

    def _import_sdk(self) -> object | None:
        try:
            return importlib.import_module("lark_oapi")
        except ImportError:
            return None
