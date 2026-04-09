#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from minachan_sdk import MinaChanPlugin, run_plugin


class EchoPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._echo_enabled = os.getenv("MINACHAN_ECHO_LOG", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def on_init(self) -> None:
        self.add_listener("MinaChan:say", self.on_say, listener_id="echo_say_listener")

    def on_say(self, sender: str, data, tag: str) -> None:
        if not self._echo_enabled:
            return

        text = self.message_text(data, key="text", fallback_keys=["msgData", "value"])
        if not text:
            text = self.text(data).strip()
        self.send_message("core-events:log", f"echo heard: {text}")


if __name__ == "__main__":
    run_plugin(EchoPlugin)
