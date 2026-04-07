#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk_python"))
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
