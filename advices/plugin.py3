#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sdk_python'))
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
