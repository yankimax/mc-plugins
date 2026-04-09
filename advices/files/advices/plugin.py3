#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from typing import Any

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from minachan_sdk import MinaChanPlugin, run_plugin


class AdvicesPlugin(MinaChanPlugin):
    def on_init(self) -> None:
        self.add_listener('advices:get', self.on_get_advice, listener_id='advices_get')
        self.register_command(
            'advices:get',
            {
                'en': 'Speak random character-based app usage advice',
                'ru': 'Сказать случайный совет по использованию приложения',
            },
        )
        self.set_event_link(
            'gui:menu-action',
            'advices:get',
            {
                'en': 'Advice/App usage tip',
                'ru': 'Советы/Совет по использованию',
            },
        )

    def on_get_advice(self, sender: str, data: Any, tag: str) -> None:
        self.request_say_intent('APP_USAGE_ADVICE')
        self.reply(sender, {'ok': True, 'intent': 'APP_USAGE_ADVICE'})


if __name__ == '__main__':
    run_plugin(AdvicesPlugin)
