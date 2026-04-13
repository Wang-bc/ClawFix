from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from app.channels.feishu_long_connection import FeishuLongConnection
from app.config.settings import Settings


class _FakeBuilder:
    def __init__(self) -> None:
        self.callback = None
        self.ignored_callbacks: list[object] = []

    def register_p2_im_message_receive_v1(self, callback):  # noqa: ANN001
        self.callback = callback
        return self

    def register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(self, callback):  # noqa: ANN001
        self.ignored_callbacks.append(callback)
        return self

    def register_p2_im_message_message_read_v1(self, callback):  # noqa: ANN001
        self.ignored_callbacks.append(callback)
        return self

    def build(self):  # noqa: ANN201
        return self.callback


class _FakeEventDispatcherHandler:
    @staticmethod
    def builder(*args):  # noqa: ANN003, ANN201
        return _FakeBuilder()


class _FakeJSON:
    @staticmethod
    def marshal(data):  # noqa: ANN001, ANN201
        return json.dumps(data, ensure_ascii=False)


class _FakeLogLevel:
    INFO = "INFO"


class _FakeWSClient:
    instance = None

    def __init__(self, app_id, app_secret, event_handler, log_level=None):  # noqa: ANN001
        self.app_id = app_id
        self.app_secret = app_secret
        self.event_handler = event_handler
        self.log_level = log_level
        self.stopped = False
        _FakeWSClient.instance = self

    def start(self) -> None:
        self.event_handler(
            {
                "schema": "2.0",
                "header": {"event_type": "im.message.receive_v1"},
                "event": {
                    "sender": {
                        "sender_id": {"open_id": "ou_test"},
                        "sender_type": "user",
                    },
                    "message": {
                        "message_id": "om_test",
                        "chat_id": "oc_test",
                        "message_type": "text",
                        "content": json.dumps({"text": "hello"}, ensure_ascii=False),
                        "create_time": "1712640000000",
                    },
                },
            }
        )

    def stop(self) -> None:
        self.stopped = True


class _FakeWSNamespace:
    Client = _FakeWSClient


class _FakeSDK:
    JSON = _FakeJSON
    LogLevel = _FakeLogLevel
    EventDispatcherHandler = _FakeEventDispatcherHandler
    ws = _FakeWSNamespace


class FeishuLongConnectionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.settings = Settings(
            workspace_dir=root / "workspace",
            public_dir=root / "public",
            feishu_app_id="cli_test",
            feishu_app_secret="secret_test",
            feishu_connection_mode="websocket",
        )
        self.settings.ensure_directories()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_websocket_connection_dispatches_payload(self) -> None:
        received: list[tuple[dict[str, object], str]] = []
        signal = threading.Event()

        def handler(payload: dict[str, object], source: str) -> None:
            received.append((payload, source))
            signal.set()

        connection = FeishuLongConnection(self.settings, handler, sdk_module=_FakeSDK)

        started = connection.start()

        self.assertTrue(started)
        self.assertTrue(signal.wait(timeout=2))
        self.assertEqual(1, len(received))
        self.assertEqual("websocket", received[0][1])
        self.assertEqual("oc_test", received[0][0]["event"]["message"]["chat_id"])
        connection.stop()
        self.assertTrue(_FakeWSClient.instance.stopped)

    def test_non_websocket_mode_does_not_start(self) -> None:
        settings = Settings(
            workspace_dir=self.settings.workspace_dir,
            public_dir=self.settings.public_dir,
            feishu_app_id="cli_test",
            feishu_app_secret="secret_test",
            feishu_connection_mode="webhook",
        )
        settings.ensure_directories()
        connection = FeishuLongConnection(settings, lambda payload, source: None, sdk_module=_FakeSDK)

        started = connection.start()

        self.assertFalse(started)
        self.assertEqual("disabled", connection.status()["status"])


if __name__ == "__main__":
    unittest.main()
