#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sdk_python'))
from minachan_sdk import MinaChanPlugin, run_plugin


CMD_DOWNLOAD_BY_LINK = 'download:by-link'
CMD_DOWNLOAD_BY_LINK_FALLBACK = 'download_router:by-link:fallback'
DOWNLOAD_FALLBACK_PRIORITY = -1000


class DownloadRouterPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._ui_locale = 'ru'

    def on_init(self) -> None:
        self.add_listener(
            CMD_DOWNLOAD_BY_LINK_FALLBACK,
            self.on_download_by_link_fallback,
            listener_id='download_router_fallback',
        )
        self.register_command(
            CMD_DOWNLOAD_BY_LINK,
            {
                'en': 'Download supported content by direct link',
                'ru': 'Скачать поддерживаемый контент по прямой ссылке',
            },
            {
                'request': 'URL text',
                'url': 'Direct URL',
                'text': 'Free-form link text',
            },
        )
        self.register_speech_rule(
            CMD_DOWNLOAD_BY_LINK,
            {'ru': 'скачай {request:Text}', 'en': 'download {request:Text}'},
        )
        self.set_alternative(
            CMD_DOWNLOAD_BY_LINK,
            CMD_DOWNLOAD_BY_LINK_FALLBACK,
            DOWNLOAD_FALLBACK_PRIORITY,
        )
        self.add_locale_listener(
            self._on_locale_changed,
            default_locale='ru',
        )

    def on_download_by_link_fallback(self, sender: str, data: Any, tag: str) -> None:
        request_text = self._extract_request(data)
        if not request_text:
            message = self._tr(
                'Please send a direct link to the page you want to download.',
                'Пришли прямую ссылку на страницу, которую нужно скачать.',
            )
            self.request_say_direct(message)
            self._reply(sender, {'ok': False, 'error': 'missing_url'})
            return

        message = self._tr(
            'I do not have a downloader plugin for this link yet.',
            'У меня пока нет плагина-загрузчика для этой ссылки.',
        )
        self.request_say_direct(message)
        self._reply(
            sender,
            {
                'ok': False,
                'error': 'unsupported_url',
                'request': request_text,
            },
        )

    def _extract_request(self, data: Any) -> str:
        if isinstance(data, dict):
            for key in ('request', 'url', 'link', 'text', 'msgData'):
                value = str(data.get(key) or '').strip()
                if value:
                    return value
        for key in ('request', 'url', 'link', 'text', 'msgData'):
            value = self.message_text(data, key=key, fallback_keys=['value', 'target']).strip()
            if value:
                return value
        if isinstance(data, dict):
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
    run_plugin(DownloadRouterPlugin)
