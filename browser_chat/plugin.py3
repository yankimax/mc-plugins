#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import quote_plus, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sdk_python'))
from minachan_sdk import MinaChanPlugin, run_plugin


BRIDGE_GET_TABS = 'browser-extension:get-open-tabs'
BRIDGE_GET_ACTIVE_TAB_META = 'browser-extension:get-active-tab-meta'
BRIDGE_OPEN_TAB = 'browser-extension:open-tab'
BRIDGE_ACTIVATE_TAB = 'browser-extension:activate-tab'
BRIDGE_CLOSE_TAB = 'browser-extension:close-tab'


@dataclass(frozen=True)
class BrowserTab:
    tab_id: int
    window_id: int
    index: int
    title: str
    url: str
    domain: str
    active: bool


class BrowserChatPlugin(MinaChanPlugin):
    CMD_LIST = 'browser:tabs-list'
    CMD_ACTIVE = 'browser:active-tab'
    CMD_OPEN = 'browser:tab-open'
    CMD_ACTIVATE = 'browser:tab-activate'
    CMD_CLOSE = 'browser:tab-close'

    def __init__(self) -> None:
        super().__init__()
        self._ui_locale = 'ru'

    def on_init(self) -> None:
        self.add_listener(self.CMD_LIST, self.on_list_tabs, listener_id='browser_chat_list')
        self.add_listener(self.CMD_ACTIVE, self.on_active_tab, listener_id='browser_chat_active')
        self.add_listener(self.CMD_OPEN, self.on_open_tab, listener_id='browser_chat_open')
        self.add_listener(self.CMD_ACTIVATE, self.on_activate_tab, listener_id='browser_chat_activate')
        self.add_listener(self.CMD_CLOSE, self.on_close_tab, listener_id='browser_chat_close')

        self.register_command(
            self.CMD_LIST,
            {
                'en': 'List open browser tabs',
                'ru': 'Показать открытые вкладки браузера',
            },
        )
        self.register_command(
            self.CMD_ACTIVE,
            {
                'en': 'Show active browser tab',
                'ru': 'Показать активную вкладку браузера',
            },
        )
        self.register_command(
            self.CMD_OPEN,
            {
                'en': 'Open browser tab by URL or search query',
                'ru': 'Открыть вкладку браузера по URL или поисковому запросу',
            },
            {
                'request': {
                    'type': 'Text',
                    'label': {
                        'en': 'URL, domain or search query',
                        'ru': 'URL, домен или поисковый запрос',
                    },
                },
            },
        )
        self.register_command(
            self.CMD_ACTIVATE,
            {
                'en': 'Switch to an open browser tab by name',
                'ru': 'Переключиться на открытую вкладку браузера по имени',
            },
            {
                'query': {
                    'type': 'Text',
                    'label': {
                        'en': 'Part of title, domain or 1-based tab number',
                        'ru': 'Часть названия, домен или номер вкладки с единицы',
                    },
                },
            },
        )
        self.register_command(
            self.CMD_CLOSE,
            {
                'en': 'Close an open browser tab by name',
                'ru': 'Закрыть открытую вкладку браузера по имени',
            },
            {
                'query': {
                    'type': 'Text',
                    'label': {
                        'en': 'Part of title, domain or 1-based tab number',
                        'ru': 'Часть названия, домен или номер вкладки с единицы',
                    },
                },
            },
        )

        self.register_speech_rule(
            self.CMD_LIST,
            {'ru': 'браузер вкладки', 'en': 'browser tabs'},
        )
        self.register_speech_rule(
            self.CMD_LIST,
            {'ru': 'какие вкладки открыты', 'en': 'what tabs are open'},
        )
        self.register_speech_rule(
            self.CMD_ACTIVE,
            {'ru': 'текущая вкладка', 'en': 'active tab'},
        )
        self.register_speech_rule(
            self.CMD_ACTIVE,
            {'ru': 'активная вкладка', 'en': 'current tab'},
        )
        self.register_speech_rule(
            self.CMD_OPEN,
            {'ru': 'браузер открой {request:Text}', 'en': 'browser open {request:Text}'},
        )
        self.register_speech_rule(
            self.CMD_OPEN,
            {'ru': 'вкладка открой {request:Text}', 'en': 'open tab {request:Text}'},
        )
        self.register_speech_rule(
            self.CMD_ACTIVATE,
            {'ru': 'браузер переключи {query:Text}', 'en': 'browser switch {query:Text}'},
        )
        self.register_speech_rule(
            self.CMD_ACTIVATE,
            {'ru': 'переключи вкладку {query:Text}', 'en': 'switch tab {query:Text}'},
        )
        self.register_speech_rule(
            self.CMD_CLOSE,
            {'ru': 'браузер закрой {query:Text}', 'en': 'browser close {query:Text}'},
        )
        self.register_speech_rule(
            self.CMD_CLOSE,
            {'ru': 'закрой вкладку {query:Text}', 'en': 'close tab {query:Text}'},
        )

        self.add_locale_listener(
            self._on_locale_changed,
            default_locale='ru',
        )

    def _on_locale_changed(self, locale: str, chain: List[str]) -> None:
        self._ui_locale = locale

    def on_list_tabs(self, sender: str, data: Any, tag: str) -> None:
        self._request_browser(
            BRIDGE_GET_TABS,
            {},
            lambda response: self._handle_list_tabs_response(sender, response),
        )

    def on_active_tab(self, sender: str, data: Any, tag: str) -> None:
        self._request_browser(
            BRIDGE_GET_ACTIVE_TAB_META,
            {},
            lambda response: self._handle_active_tab_response(sender, response),
        )

    def on_open_tab(self, sender: str, data: Any, tag: str) -> None:
        request = self._extract_request(data, keys=('request', 'url', 'query'))
        if not request:
            self._respond(
                sender,
                ok=False,
                text=self._usage_open_text(),
                extra={'mode': 'open'},
            )
            return

        target_url = self._normalize_open_target(request)
        self._request_browser(
            BRIDGE_OPEN_TAB,
            {'url': target_url},
            lambda response: self._handle_open_response(
                sender=sender,
                response=response,
                original=request,
                target_url=target_url,
            ),
        )

    def on_activate_tab(self, sender: str, data: Any, tag: str) -> None:
        query = self._extract_request(data, keys=('query', 'request', 'text'))
        if not query:
            self._respond(
                sender,
                ok=False,
                text=self._usage_switch_text(),
                extra={'mode': 'activate'},
            )
            return

        self._request_browser(
            BRIDGE_GET_TABS,
            {},
            lambda response: self._handle_targeted_tab_action(
                sender=sender,
                response=response,
                query=query,
                action='activate',
            ),
        )

    def on_close_tab(self, sender: str, data: Any, tag: str) -> None:
        query = self._extract_request(data, keys=('query', 'request', 'text'))
        if not query:
            self._respond(
                sender,
                ok=False,
                text=self._usage_close_text(),
                extra={'mode': 'close'},
            )
            return

        self._request_browser(
            BRIDGE_GET_TABS,
            {},
            lambda response: self._handle_targeted_tab_action(
                sender=sender,
                response=response,
                query=query,
                action='close',
            ),
        )

    def _handle_list_tabs_response(self, sender: str, response: Dict[str, Any]) -> None:
        if not response.get('ok'):
            self._respond(
                sender,
                ok=False,
                text=self._bridge_unavailable_text(response),
                extra={'mode': 'list', 'reason': response.get('error')},
            )
            return

        tabs = self._tabs_from_response(response)
        if not tabs:
            self._respond(
                sender,
                ok=True,
                text=self._tr('No open browser tabs.', 'Открытых вкладок браузера нет.'),
                extra={'mode': 'list', 'tabs': []},
            )
            return

        preview = '; '.join(
            f'{index + 1}. {self._tab_label(tab)}'
            for index, tab in enumerate(tabs[:5])
        )
        if len(tabs) > 5:
            preview += self._tr('; and more.', '; и еще.')
        text = self._tr(
            f'Open tabs: {len(tabs)}. {preview}',
            f'Открыто вкладок: {len(tabs)}. {preview}',
        )
        self._respond(
            sender,
            ok=True,
            text=text,
            extra={
                'mode': 'list',
                'count': len(tabs),
                'tabs': [self._serialize_tab(tab) for tab in tabs],
            },
        )

    def _handle_active_tab_response(self, sender: str, response: Dict[str, Any]) -> None:
        if not response.get('ok'):
            self._respond(
                sender,
                ok=False,
                text=self._bridge_unavailable_text(response),
                extra={'mode': 'active', 'reason': response.get('error')},
            )
            return

        tab = self._tab_from_map(response.get('tab'))
        if tab is None:
            self._respond(
                sender,
                ok=False,
                text=self._tr('No active browser tab.', 'Не вижу активную вкладку браузера.'),
                extra={'mode': 'active'},
            )
            return

        page = response.get('meta', {})
        description = ''
        if isinstance(page, dict):
            page_data = page.get('page')
            if isinstance(page_data, dict):
                description = str(page_data.get('description') or '').strip()

        text = self._tr(
            f'Active tab: {self._tab_label(tab)}.',
            f'Активная вкладка: {self._tab_label(tab)}.',
        )
        if description:
            text += ' ' + self._tr(
                f'Description: {description}',
                f'Описание: {description}',
            )

        self._respond(
            sender,
            ok=True,
            text=text,
            extra={
                'mode': 'active',
                'tab': self._serialize_tab(tab),
                'description': description,
            },
        )

    def _handle_open_response(
        self,
        *,
        sender: str,
        response: Dict[str, Any],
        original: str,
        target_url: str,
    ) -> None:
        if response.get('ok'):
            text = self._tr(
                f'Opening browser tab: {original}',
                f'Открываю вкладку браузера: {original}',
            )
            self._respond(
                sender,
                ok=True,
                text=text,
                extra={
                    'mode': 'open',
                    'request': original,
                    'url': target_url,
                    'tab': response.get('tab'),
                },
            )
            return

        self._respond(
            sender,
            ok=False,
            text=self._tr(
                f'Failed to open browser tab: {response.get("error") or "unknown error"}',
                f'Не удалось открыть вкладку браузера: {response.get("error") or "неизвестная ошибка"}',
            ),
            extra={
                'mode': 'open',
                'request': original,
                'url': target_url,
                'reason': response.get('error'),
            },
        )

    def _handle_targeted_tab_action(
        self,
        *,
        sender: str,
        response: Dict[str, Any],
        query: str,
        action: str,
    ) -> None:
        if not response.get('ok'):
            self._respond(
                sender,
                ok=False,
                text=self._bridge_unavailable_text(response),
                extra={'mode': action, 'reason': response.get('error'), 'query': query},
            )
            return

        tabs = self._tabs_from_response(response)
        target = self._find_best_tab_match(tabs, query)
        if target is None:
            self._respond(
                sender,
                ok=False,
                text=self._tr(
                    f'I could not find a tab matching "{query}".',
                    f'Не нашла вкладку по запросу "{query}".',
                ),
                extra={'mode': action, 'query': query, 'tabs': [self._serialize_tab(tab) for tab in tabs[:10]]},
            )
            return

        bridge_command = BRIDGE_ACTIVATE_TAB if action == 'activate' else BRIDGE_CLOSE_TAB
        self._request_browser(
            bridge_command,
            {'tabId': target.tab_id},
            lambda target_response: self._handle_targeted_action_response(
                sender=sender,
                response=target_response,
                query=query,
                tab=target,
                action=action,
            ),
        )

    def _handle_targeted_action_response(
        self,
        *,
        sender: str,
        response: Dict[str, Any],
        query: str,
        tab: BrowserTab,
        action: str,
    ) -> None:
        action_text_en = 'Switching to tab' if action == 'activate' else 'Closing tab'
        action_text_ru = 'Переключаю на вкладку' if action == 'activate' else 'Закрываю вкладку'
        failed_text_en = 'Failed to switch tab' if action == 'activate' else 'Failed to close tab'
        failed_text_ru = 'Не удалось переключить вкладку' if action == 'activate' else 'Не удалось закрыть вкладку'

        if response.get('ok'):
            self._respond(
                sender,
                ok=True,
                text=self._tr(
                    f'{action_text_en}: {self._tab_label(tab)}',
                    f'{action_text_ru}: {self._tab_label(tab)}',
                ),
                extra={
                    'mode': action,
                    'query': query,
                    'tab': self._serialize_tab(tab),
                },
            )
            return

        self._respond(
            sender,
            ok=False,
            text=self._tr(
                f'{failed_text_en}: {response.get("error") or "unknown error"}',
                f'{failed_text_ru}: {response.get("error") or "неизвестная ошибка"}',
            ),
            extra={
                'mode': action,
                'query': query,
                'tab': self._serialize_tab(tab),
                'reason': response.get('error'),
            },
        )

    def _request_browser(self, command: str, payload: Dict[str, Any], callback: Callable[[Dict[str, Any]], None]) -> None:
        responded = {'value': False}

        def _finish(response: Dict[str, Any]) -> None:
            if responded['value']:
                return
            responded['value'] = True
            callback(response)

        def _on_response(sender: str, data: Any, tag: str) -> None:
            if isinstance(data, dict):
                _finish(dict(data))
                return
            _finish({'ok': False, 'error': f'Invalid response for {command}'})

        def _on_complete(sender: str, data: Any, tag: str) -> None:
            _finish({'ok': False, 'error': f'No response for {command}'})

        seq = self.send_message_with_response(
            command,
            payload,
            on_response=_on_response,
            on_complete=_on_complete,
        )
        if seq < 0:
            _finish({'ok': False, 'error': f'Failed to send {command}'})

    def _extract_request(self, data: Any, *, keys: Sequence[str]) -> str:
        if isinstance(data, dict):
            for key in keys:
                value = str(data.get(key) or '').strip()
                if value:
                    return value
        for key in keys:
            value = self.message_text(data, key=key, fallback_keys=['text', 'value', 'target']).strip()
            if value:
                return value
        return self.text(data).strip()

    def _tabs_from_response(self, response: Dict[str, Any]) -> List[BrowserTab]:
        tabs: List[BrowserTab] = []
        raw_tabs = response.get('tabs')
        if not isinstance(raw_tabs, list):
            return tabs
        for item in raw_tabs:
            tab = self._tab_from_map(item)
            if tab is not None:
                tabs.append(tab)
        tabs.sort(key=lambda tab: (tab.window_id, tab.index))
        return tabs

    def _tab_from_map(self, raw: Any) -> Optional[BrowserTab]:
        if not isinstance(raw, dict):
            return None
        tab_id = self._to_int(raw.get('id'), -1)
        if tab_id < 0:
            return None
        url = str(raw.get('url') or '').strip()
        return BrowserTab(
            tab_id=tab_id,
            window_id=max(0, self._to_int(raw.get('windowId'), 0)),
            index=max(0, self._to_int(raw.get('index'), 0)),
            title=str(raw.get('title') or '').strip(),
            url=url,
            domain=self._domain_from_url(url),
            active=bool(raw.get('active') is True),
        )

    def _find_best_tab_match(self, tabs: Sequence[BrowserTab], query: str) -> Optional[BrowserTab]:
        normalized_query = str(query or '').strip().lower()
        if not normalized_query:
            return None

        if normalized_query.isdigit():
            index = int(normalized_query) - 1
            if 0 <= index < len(tabs):
                return tabs[index]

        best_tab: Optional[BrowserTab] = None
        best_score = -1
        for position, tab in enumerate(tabs):
            haystack = ' '.join(
                part for part in [tab.title.lower(), tab.domain.lower(), tab.url.lower()] if part
            )
            score = self._match_score(normalized_query, haystack, tab)
            if score < 0:
                continue
            score += max(0, 20 - position)
            if best_tab is None or score > best_score:
                best_tab = tab
                best_score = score
        return best_tab

    def _match_score(self, query: str, haystack: str, tab: BrowserTab) -> int:
        if not haystack:
            return -1
        if query == tab.domain.lower():
            return 400
        if query == tab.title.lower():
            return 380
        if query in tab.domain.lower():
            return 300
        if query in tab.title.lower():
            return 260
        if query in haystack:
            return 220

        query_words = [item for item in re.split(r'\s+', query) if item]
        if query_words and all(word in haystack for word in query_words):
            return 180 + len(query_words) * 10
        return -1

    def _normalize_open_target(self, request: str) -> str:
        text = str(request or '').strip()
        if not text:
            return ''
        if re.match(r'^[a-z][a-z0-9+.-]*://', text, flags=re.IGNORECASE):
            return text
        if self._looks_like_url(text):
            return 'https://' + text
        return 'https://www.google.com/search?q=' + quote_plus(text)

    def _looks_like_url(self, text: str) -> bool:
        candidate = str(text or '').strip()
        if not candidate or ' ' in candidate:
            return False
        if candidate.startswith(('localhost:', '127.0.0.1:', '0.0.0.0:')):
            return True
        if re.match(r'^\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?(?:/.*)?$', candidate):
            return True
        if re.match(r'^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?::\d+)?(?:/.*)?$', candidate, flags=re.IGNORECASE):
            return True
        return False

    def _domain_from_url(self, url: str) -> str:
        try:
            host = urlparse(url).netloc.lower().strip()
        except Exception:
            return ''
        if host.startswith('www.'):
            host = host[4:]
        return host

    def _tab_label(self, tab: BrowserTab) -> str:
        title = tab.title or self._tr('Untitled tab', 'Безымянная вкладка')
        if tab.domain:
            return f'{title} ({tab.domain})'
        return title

    def _serialize_tab(self, tab: BrowserTab) -> Dict[str, Any]:
        return {
            'id': tab.tab_id,
            'windowId': tab.window_id,
            'index': tab.index,
            'title': tab.title,
            'url': tab.url,
            'domain': tab.domain,
            'active': tab.active,
        }

    def _bridge_unavailable_text(self, response: Dict[str, Any]) -> str:
        reason = str(response.get('error') or '').strip()
        if reason:
            return self._tr(
                f'Browser bridge is unavailable: {reason}',
                f'Browser bridge недоступен: {reason}',
            )
        return self._tr(
            'Browser bridge is unavailable.',
            'Browser bridge недоступен.',
        )

    def _usage_open_text(self) -> str:
        return self._tr(
            'Usage: browser open <url or search query>',
            'Использование: браузер открой <url или поисковый запрос>',
        )

    def _usage_switch_text(self) -> str:
        return self._tr(
            'Usage: browser switch <tab name or number>',
            'Использование: браузер переключи <имя вкладки или номер>',
        )

    def _usage_close_text(self) -> str:
        return self._tr(
            'Usage: browser close <tab name or number>',
            'Использование: браузер закрой <имя вкладки или номер>',
        )

    def _respond(
        self,
        sender: str,
        *,
        ok: bool,
        text: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        message = str(text or '').strip()
        if message:
            self.request_say_direct(message)
        if sender:
            payload: Dict[str, Any] = {'ok': bool(ok), 'text': message}
            if isinstance(extra, dict):
                payload.update(extra)
            self.reply(sender, payload)

    def _to_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(str(value).strip())
        except Exception:
            return default

    def _tr(self, en: str, ru: str) -> str:
        return ru if self._is_ru_locale() else en

    def _is_ru_locale(self) -> bool:
        return self._ui_locale.startswith('ru')


if __name__ == '__main__':
    run_plugin(BrowserChatPlugin)
