#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sdk_python'))
from minachan_sdk import MinaChanPlugin, run_plugin


class DaytimeFilterPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._timer_id = -1

    def on_init(self) -> None:
        self.add_listener('daytime_filter:apply-now', self.on_apply_now, listener_id='apply_now')
        self.register_command(
            'daytime_filter:apply-now',
            {
                'en': 'Apply daytime color filter now',
                'ru': 'Применить дневной цветовой фильтр',
            },
        )
        self._apply_filter()
        self._timer_id = self.set_timer_callback(60_000, 0, self.on_tick)

    def on_unload(self) -> None:
        if self._timer_id >= 0:
            self.cancel_timer(self._timer_id)
            self._timer_id = -1

    def on_apply_now(self, sender: str, data, tag: str) -> None:
        self._apply_filter()

    def on_tick(self, sender: str, data, tag: str) -> None:
        self._apply_filter()

    def _apply_filter(self) -> None:
        h = dt.datetime.now().hour
        if 7 <= h < 18:
            value = {'brightness': 1.0, 'saturation': 1.0, 'temperature': 0}
        elif 18 <= h < 23:
            value = {'brightness': 0.92, 'saturation': 0.95, 'temperature': -10}
        else:
            value = {'brightness': 0.8, 'saturation': 0.9, 'temperature': -20}
        self.send_message('gui:set-skin-filter', value)


if __name__ == '__main__':
    run_plugin(DaytimeFilterPlugin)
