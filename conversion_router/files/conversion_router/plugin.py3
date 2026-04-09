#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from minachan_sdk import MinaChanPlugin, run_plugin


CMD_CONVERSION_QUERY = 'conversion:query'
CMD_CONVERSION_QUERY_FALLBACK = 'conversion_router:query:fallback'
CONVERSION_FALLBACK_PRIORITY = -1000
_CONVERSION_TOKEN_RULE = (
    '(мм|см|м|км|миллиметр|сантиметр|метр|километр|'
    'in|inch|дюйм|ft|foot|фут|yd|yard|ярд|mi|mile|миля|'
    'мг|г|гр|кг|т|миллиграм|грам|килограмм|тон|тонна|mg|g|kg|t|oz|ounce|унция|lb|pound|'
    'с|сек|секунда|мин|минута|ч|час|день|неделя|sec|min|h|day|week|'
    'c|celsius|цельсий|f|fahrenheit|фаренгейт|k|kelvin|кельвин|'
    'руб|рубл|доллар|бакс|usd|eur|евр|euro|gbp|cny|юан|yuan|jpy|иен|yen|'
    'rsd|рсд|динар|дианар|dinar)'
)


class ConversionRouterPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._ui_locale = 'ru'

    def on_init(self) -> None:
        self.add_listener(
            CMD_CONVERSION_QUERY_FALLBACK,
            self.on_conversion_query_fallback,
            listener_id='conversion_router_fallback',
        )
        self.register_command(
            CMD_CONVERSION_QUERY,
            {
                'en': 'Route a conversion query to the first compatible converter plugin',
                'ru': 'Маршрутизировать запрос конвертации в первый подходящий плагин-конвертер',
            },
            {
                'amount': 'Numeric amount when the speech rule extracted it',
                'from': 'Source token when the speech rule extracted it',
                'to': 'Target token when the speech rule extracted it',
                'request': 'Free-form conversion request',
                'text': 'Original phrase text',
            },
        )
        self.register_speech_rule(
            CMD_CONVERSION_QUERY,
            {
                'ru': f'{{amount:Number}} {_CONVERSION_TOKEN_RULE} (в|to|in) {_CONVERSION_TOKEN_RULE}',
                'en': f'{{amount:Number}} {_CONVERSION_TOKEN_RULE} (in|to) {_CONVERSION_TOKEN_RULE}',
            },
        )
        self.register_speech_rule(
            CMD_CONVERSION_QUERY,
            {
                'ru': f'сколько {_CONVERSION_TOKEN_RULE} в {_CONVERSION_TOKEN_RULE}',
                'en': f'how (much|many) {_CONVERSION_TOKEN_RULE} in {_CONVERSION_TOKEN_RULE}',
            },
        )
        self.register_speech_rule(
            CMD_CONVERSION_QUERY,
            {
                'ru': '(конвертируй|переведи) {request:Text}',
                'en': 'convert {request:Text}',
            },
        )
        self.register_speech_rule(
            CMD_CONVERSION_QUERY,
            {
                'ru': '(курс сербии|nbs) {request:Text}',
                'en': '(serbia rate|nbs) {request:Text}',
            },
        )
        self.set_alternative(
            CMD_CONVERSION_QUERY,
            CMD_CONVERSION_QUERY_FALLBACK,
            CONVERSION_FALLBACK_PRIORITY,
        )
        self.add_locale_listener(
            self._on_locale_changed,
            default_locale='ru',
        )

    def on_conversion_query_fallback(self, sender: str, data: Any, tag: str) -> None:
        request_text = self._extract_request(data)
        if not request_text:
            self.request_say_direct(
                self._tr(
                    'Tell me what exactly you want to convert.',
                    'Скажи, что именно нужно конвертировать.',
                )
            )
            self._reply(sender, {'ok': False, 'error': 'missing_query'})
            return

        self.request_say_direct(
            self._tr(
                'I could not find a plugin that can handle this conversion request.',
                'Я не нашла плагин, который умеет обработать такой запрос на конвертацию.',
            )
        )
        self._reply(
            sender,
            {
                'ok': False,
                'error': 'unsupported_query',
                'request': request_text,
            },
        )

    def _extract_request(self, data: Any) -> str:
        if isinstance(data, dict):
            for key in ('request', 'text', 'msgData'):
                value = str(data.get(key) or '').strip()
                if value:
                    return value
            return ''
        return self.text(data).strip()

    def _reply(self, sender: str, payload: Dict[str, Any]) -> None:
        if sender:
            self.reply(sender, payload)

    def _on_locale_changed(self, locale: str, chain: List[str]) -> None:
        self._ui_locale = locale

    def _is_ru_locale(self) -> bool:
        return self._ui_locale.startswith('ru')

    def _tr(self, en: str, ru: str) -> str:
        return ru if self._is_ru_locale() else en


if __name__ == '__main__':
    run_plugin(ConversionRouterPlugin)
