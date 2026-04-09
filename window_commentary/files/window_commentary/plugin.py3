#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from minachan_sdk import MinaChanPlugin, run_plugin


DEFAULT_INTERVAL_SEC = 180
MIN_INTERVAL_SEC = 30
MAX_INTERVAL_SEC = 3600
STARTUP_GRACE_SEC = 45

CMD_BROWSER_GET_OPEN_TABS = 'browser-extension:get-open-tabs'
CMD_BROWSER_GET_ACTIVE_TAB_META = 'browser-extension:get-active-tab-meta'
CMD_SYSTEM_GET_WINDOW_TITLES = 'system-runtime:get-window-titles'

COMMENTARY_INTENTS = {
    'steam_only': 'WINDOW_COMMENTARY_STEAM_ONLY',
    'gaming_social': 'WINDOW_COMMENTARY_GAMING_SOCIAL',
    'coding_mode': 'WINDOW_COMMENTARY_CODING_MODE',
    'work_mode': 'WINDOW_COMMENTARY_WORK_MODE',
    'browser_youtube': 'WINDOW_COMMENTARY_BROWSER_YOUTUBE',
    'browser_social': 'WINDOW_COMMENTARY_BROWSER_SOCIAL',
    'browser_only': 'WINDOW_COMMENTARY_BROWSER_ONLY',
    'too_many_windows': 'WINDOW_COMMENTARY_TOO_MANY_WINDOWS',
    'no_windows': 'WINDOW_COMMENTARY_NO_WINDOWS',
    'mixed': 'WINDOW_COMMENTARY_MIXED',
}

BROWSER_APP_MARKERS = (
    'chrome',
    'chromium',
    'firefox',
    'brave',
    'vivaldi',
    'edge',
    'opera',
    'safari',
    'browser',
)

CODE_MARKERS = (
    'code',
    'pycharm',
    'intellij',
    'idea',
    'clion',
    'webstorm',
    'android studio',
    'neovim',
    'vim',
    'emacs',
    'sublime',
)

TERMINAL_MARKERS = (
    'terminal',
    'konsole',
    'xterm',
    'alacritty',
    'kitty',
    'gnome-terminal',
    'powershell',
    'cmd.exe',
)

CHAT_MARKERS = (
    'discord',
    'telegram',
    'slack',
    'teams',
    'whatsapp',
    'messenger',
)

YOUTUBE_DOMAINS = (
    'youtube.com',
    'youtu.be',
    'music.youtube.com',
)

SOCIAL_DOMAIN_LABELS = {
    'vk.com': 'VK',
    'vkontakte.ru': 'VK',
    'facebook.com': 'Facebook',
    'instagram.com': 'Instagram',
    'twitter.com': 'X',
    'x.com': 'X',
    'tiktok.com': 'TikTok',
    'reddit.com': 'Reddit',
    'ok.ru': 'OK',
    'discord.com': 'Discord',
    'web.telegram.org': 'Telegram',
}


@dataclass(frozen=True)
class BrowserTabState:
    title: str
    url: str
    domain: str
    site: str
    kind: str
    active: bool = False


@dataclass(frozen=True)
class BrowserContext:
    tabs: Tuple[BrowserTabState, ...] = ()
    active_tab: Optional[BrowserTabState] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.tabs)

    @property
    def active_domain(self) -> str:
        return self.active_tab.domain if self.active_tab is not None else ''

    @property
    def active_kind(self) -> str:
        return self.active_tab.kind if self.active_tab is not None else ''

    @property
    def active_site(self) -> str:
        return self.active_tab.site if self.active_tab is not None else ''

    @property
    def active_title(self) -> str:
        return self.active_tab.title if self.active_tab is not None else ''

    @property
    def active_url(self) -> str:
        return self.active_tab.url if self.active_tab is not None else ''

    def has_social_tabs(self) -> bool:
        return any(tab.kind == 'social' for tab in self.tabs)

    def top_sites_text(self, limit: int = 3) -> str:
        names: List[str] = []
        seen: Set[str] = set()
        for tab in self.tabs:
            candidate = tab.site or tab.domain
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            names.append(candidate)
            if len(names) >= limit:
                break
        return ', '.join(names)

    def signature(self) -> str:
        parts: List[str] = []
        if self.active_kind:
            parts.append(self.active_kind)
        if self.active_domain:
            parts.append(self.active_domain)
        unique_sites: List[str] = []
        seen: Set[str] = set()
        for tab in self.tabs:
            candidate = tab.domain or tab.site
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            unique_sites.append(candidate)
            if len(unique_sites) >= 3:
                break
        parts.extend(unique_sites)
        parts.append(str(self.count))
        return '|'.join(part for part in parts if part)


class WindowCommentaryPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._ui_locale = 'ru'
        self._interval_sec = DEFAULT_INTERVAL_SEC
        self._enabled = True
        self._include_browser_tabs = True
        self._timer_armed = False
        self._last_signature = ''
        self._window_provider_available = True
        self._browser_provider_available = False
        self._analysis_seq = 0

    def on_init(self) -> None:
        self.add_listener(
            'window-commentary:tick',
            self.on_tick,
            listener_id='window_commentary_tick',
        )
        self.add_listener(
            'window-commentary:check-now',
            self.on_check_now,
            listener_id='window_commentary_check_now',
        )
        self.add_listener(
            'window-commentary:update-settings',
            self.on_update_settings,
            listener_id='window_commentary_update_settings',
        )
        self.add_listener(
            'gui:request-panels',
            self.on_request_panels,
            listener_id='window_commentary_request_panels',
        )

        self.register_command(
            'window-commentary:check-now',
            'Analyze open windows and browser tabs, then speak one comment',
        )

        self._load_settings()
        self.add_locale_listener(
            self._on_locale_changed,
            default_locale='ru',
        )
        self._schedule_startup_tick()

    def on_tick(self, sender: str, data: Any, tag: str) -> None:
        self._timer_armed = False
        try:
            self._check_and_comment(force=False)
        finally:
            self._schedule_tick()

    def on_check_now(self, sender: str, data: Any, tag: str) -> None:
        self._check_and_comment(force=True)

    def on_request_panels(self, sender: str, data: Any, tag: str) -> None:
        self._register_settings_gui()

    def on_update_settings(self, sender: str, data: Any, tag: str) -> None:
        if not isinstance(data, dict):
            return

        interval = self._to_int(data.get('interval_sec'), self._interval_sec)
        self._interval_sec = self._clamp_interval(interval)
        self._enabled = self._to_bool(data.get('enabled'), self._enabled)
        self._include_browser_tabs = self._to_bool(
            data.get('include_browser_tabs'),
            self._include_browser_tabs,
        )

        self.set_property('intervalSec', self._interval_sec)
        self.set_property('enabled', self._enabled)
        self.set_property('includeBrowserTabs', self._include_browser_tabs)
        self.save_properties()
        self._register_settings_gui()
        self._schedule_tick()

        if self._is_ru_locale():
            browser_text = 'включен' if self._include_browser_tabs else 'выключен'
            self.request_say_direct(
                f'Настройки комментариев обновлены. Интервал: {self._interval_sec} сек., браузерный анализ: {browser_text}.',
            )
        else:
            browser_text = 'enabled' if self._include_browser_tabs else 'disabled'
            self.request_say_direct(
                f'Window commentary settings updated. Interval: {self._interval_sec} sec, browser analysis: {browser_text}.',
            )

    def _on_locale_changed(self, locale: str, chain: List[str]) -> None:
        self._ui_locale = locale
        self._register_settings_gui()

    def _load_settings(self) -> None:
        self._interval_sec = self._clamp_interval(
            self._to_int(self.get_property('intervalSec', DEFAULT_INTERVAL_SEC)),
        )
        self._enabled = self._to_bool(self.get_property('enabled', True), True)
        self._include_browser_tabs = self._to_bool(
            self.get_property('includeBrowserTabs', True),
            True,
        )

    def _register_settings_gui(self) -> None:
        texts = self._ui_texts()
        self.setup_options_panel(
            panel_id='window_commentary_settings',
            name=texts['panel_name'],
            msg_tag='window-commentary:update-settings',
            controls=[
                {
                    'id': 'description',
                    'type': 'label',
                    'label': texts['description'],
                },
                {
                    'id': 'enabled',
                    'type': 'checkbox',
                    'label': texts['enabled_label'],
                    'value': self._enabled,
                },
                {
                    'id': 'interval_sec',
                    'type': 'text',
                    'label': texts['interval_label'],
                    'value': str(self._interval_sec),
                },
                {
                    'id': 'include_browser_tabs',
                    'type': 'checkbox',
                    'label': texts['browser_tabs_label'],
                    'value': self._include_browser_tabs,
                },
                {
                    'id': 'browser_status',
                    'type': 'label',
                    'label': self._browser_status_text(texts),
                },
                {
                    'id': 'hint',
                    'type': 'label',
                    'label': texts['hint'],
                },
            ],
        )

    def _browser_status_text(self, texts: Dict[str, str]) -> str:
        if not self._include_browser_tabs:
            return texts['browser_status_disabled']
        if self._browser_provider_available:
            return texts['browser_status_connected']
        return texts['browser_status_waiting']

    def _is_ru_locale(self) -> bool:
        return self._ui_locale.startswith('ru')

    def _schedule_tick(self) -> None:
        if self._timer_armed:
            return
        delay_ms = int(self._interval_sec * 1000)
        self.set_timer(delay_ms, 1, 'window_commentary_tick')
        self._timer_armed = True

    def _schedule_startup_tick(self) -> None:
        if self._timer_armed:
            return
        delay_ms = int(STARTUP_GRACE_SEC * 1000)
        self.set_timer(delay_ms, 1, 'window_commentary_tick')
        self._timer_armed = True

    def _check_and_comment(self, force: bool) -> None:
        if not self._enabled and not force:
            return

        self._analysis_seq += 1
        analysis_id = self._analysis_seq
        self._request_window_titles(
            lambda titles: self._request_browser_context(
                lambda browser_context: self._finalize_commentary(
                    analysis_id=analysis_id,
                    force=force,
                    titles=titles,
                    browser_context=browser_context,
                ),
            ),
        )

    def _request_window_titles(
        self,
        callback: Callable[[List[str]], None],
    ) -> None:
        def _on_response(response: Dict[str, Any]) -> None:
            titles, provider_available = self._parse_window_titles_response(response)
            self._window_provider_available = provider_available
            callback(titles)

        self._request_runtime(CMD_SYSTEM_GET_WINDOW_TITLES, {}, _on_response)

    def _parse_window_titles_response(
        self,
        response: Dict[str, Any],
    ) -> Tuple[List[str], bool]:
        if not isinstance(response, dict):
            return ([], False)

        titles_raw = response.get('titles')
        titles: List[str] = []
        if isinstance(titles_raw, list):
            for item in titles_raw:
                text = str(item or '').strip()
                if text:
                    titles.append(text)

        provider_available = bool(response.get('providerAvailable') is True)
        if response.get('ok') is True:
            provider_available = True
        if titles:
            provider_available = True

        return (self._cleanup_titles(titles), provider_available)

    def _request_browser_context(
        self,
        callback: Callable[[Optional[BrowserContext]], None],
    ) -> None:
        if not self._include_browser_tabs:
            callback(None)
            return

        def _on_tabs(response: Dict[str, Any]) -> None:
            if not response.get('ok'):
                self._browser_provider_available = False
                self._register_settings_gui()
                callback(None)
                return

            tabs_payload = dict(response)

            def _on_active(response_active: Dict[str, Any]) -> None:
                browser_context = self._build_browser_context(
                    tabs_payload,
                    response_active if isinstance(response_active, dict) else {},
                )
                self._browser_provider_available = browser_context is not None
                self._register_settings_gui()
                callback(browser_context)

            self._request_runtime(
                CMD_BROWSER_GET_ACTIVE_TAB_META,
                {},
                _on_active,
            )

        self._request_runtime(CMD_BROWSER_GET_OPEN_TABS, {}, _on_tabs)

    def _request_runtime(
        self,
        command: str,
        payload: Dict[str, Any],
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
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

    def _finalize_commentary(
        self,
        *,
        analysis_id: int,
        force: bool,
        titles: Sequence[str],
        browser_context: Optional[BrowserContext],
    ) -> None:
        if analysis_id != self._analysis_seq:
            return

        if not titles and browser_context is None:
            if not self._window_provider_available:
                return

        category, signature = self._classify(titles, browser_context)
        if not force and signature == self._last_signature:
            return

        intent = COMMENTARY_INTENTS.get(category, COMMENTARY_INTENTS.get('mixed') or '')
        if not intent:
            return

        vars_payload = self._build_commentary_vars(titles, browser_context)
        self._last_signature = signature
        self.request_say_intent(intent, template_vars=vars_payload, extra=vars_payload)

    def _build_commentary_vars(
        self,
        titles: Sequence[str],
        browser_context: Optional[BrowserContext],
    ) -> Dict[str, Any]:
        window_titles = [str(item or '').strip() for item in titles if str(item or '').strip()]
        top_titles = ', '.join(window_titles[:3])
        if not top_titles and browser_context is not None:
            top_titles = browser_context.active_title or browser_context.top_sites_text()

        payload: Dict[str, Any] = {
            'windows': top_titles,
            'count': len(window_titles),
        }

        if browser_context is not None:
            payload.update(
                {
                    'browser_site': browser_context.active_site,
                    'browser_domain': browser_context.active_domain,
                    'browser_title': browser_context.active_title,
                    'browser_url': browser_context.active_url,
                    'browser_tabs_count': browser_context.count,
                    'browser_sites': browser_context.top_sites_text(),
                },
            )
            page = browser_context.meta.get('page')
            if isinstance(page, dict):
                payload['browser_description'] = str(page.get('description') or '').strip()

        return payload

    def _cleanup_titles(self, titles: Sequence[str]) -> List[str]:
        seen: Set[str] = set()
        out: List[str] = []
        for raw in titles:
            title = str(raw or '').strip()
            if not title:
                continue
            low = title.lower()
            if low in ('desktop', 'program manager'):
                continue
            if title in seen:
                continue
            seen.add(title)
            out.append(title)
        return out

    def _build_browser_context(
        self,
        tabs_response: Dict[str, Any],
        active_response: Dict[str, Any],
    ) -> Optional[BrowserContext]:
        tabs_raw = tabs_response.get('tabs')
        tabs: List[BrowserTabState] = []
        if isinstance(tabs_raw, list):
            for item in tabs_raw:
                tab = self._normalize_browser_tab(item)
                if tab is not None:
                    tabs.append(tab)

        active_tab = self._normalize_browser_tab(active_response.get('tab'))
        if active_tab is None:
            for tab in tabs:
                if tab.active:
                    active_tab = tab
                    break

        meta = {}
        raw_meta = active_response.get('meta')
        if isinstance(raw_meta, dict):
            meta = dict(raw_meta)

        if not tabs and active_tab is None:
            return None

        return BrowserContext(
            tabs=tuple(tabs),
            active_tab=active_tab,
            meta=meta,
        )

    def _normalize_browser_tab(self, raw: Any) -> Optional[BrowserTabState]:
        if not isinstance(raw, dict):
            return None
        title = str(raw.get('title') or '').strip()
        url = str(raw.get('url') or '').strip()
        domain = self._domain_from_url(url)
        kind, site = self._browser_site_kind(domain)
        return BrowserTabState(
            title=title,
            url=url,
            domain=domain,
            site=site,
            kind=kind,
            active=bool(raw.get('active') is True),
        )

    def _domain_from_url(self, url: str) -> str:
        try:
            host = urlparse(url).netloc.lower().strip()
        except Exception:
            return ''
        if host.startswith('www.'):
            host = host[4:]
        return host

    def _browser_site_kind(self, domain: str) -> Tuple[str, str]:
        if self._domain_matches(domain, YOUTUBE_DOMAINS):
            return 'youtube', 'YouTube'

        for candidate, label in SOCIAL_DOMAIN_LABELS.items():
            if self._domain_matches(domain, (candidate,)):
                return 'social', label

        if domain:
            return 'browser', domain
        return 'browser', ''

    def _domain_matches(self, domain: str, patterns: Sequence[str]) -> bool:
        normalized = domain.lower().strip()
        if not normalized:
            return False
        for candidate in patterns:
            needle = str(candidate or '').lower().strip()
            if not needle:
                continue
            if normalized == needle or normalized.endswith('.' + needle):
                return True
        return False

    def _classify(
        self,
        titles: Sequence[str],
        browser_context: Optional[BrowserContext],
    ) -> Tuple[str, str]:
        if not titles and browser_context is None:
            return 'no_windows', 'no_windows'

        joined_parts = [str(item or '') for item in titles]
        if browser_context is not None:
            if browser_context.active_title:
                joined_parts.append(browser_context.active_title)
            if browser_context.active_site:
                joined_parts.append(browser_context.active_site)
            if browser_context.active_domain:
                joined_parts.append(browser_context.active_domain)

        joined = ' | '.join(joined_parts).lower()
        has_steam = self._contains_any(joined, ('steam',))
        has_browser = self._contains_any(joined, BROWSER_APP_MARKERS) or (
            browser_context is not None and browser_context.count > 0
        )
        has_code = self._contains_any(joined, CODE_MARKERS)
        has_terminal = self._contains_any(joined, TERMINAL_MARKERS)
        has_chat = self._contains_any(joined, CHAT_MARKERS)
        has_social_tabs = browser_context.has_social_tabs() if browser_context is not None else False

        only_steam = has_steam and len(titles) <= 2 and not has_code and not has_terminal

        if only_steam:
            return 'steam_only', 'steam_only'
        if has_code and has_terminal:
            return 'coding_mode', 'coding_mode'
        if has_steam and (has_chat or has_social_tabs):
            return 'gaming_social', 'gaming_social'

        if browser_context is not None and not has_code and not has_terminal and not has_steam:
            if browser_context.active_kind == 'youtube':
                return 'browser_youtube', 'browser_youtube|' + browser_context.signature()
            if browser_context.active_kind == 'social':
                return 'browser_social', 'browser_social|' + browser_context.signature()

        if has_code:
            return 'work_mode', 'work_mode'
        if has_browser and len(titles) <= 1:
            if browser_context is not None:
                return 'browser_only', 'browser_only|' + browser_context.signature()
            return 'browser_only', 'browser_only'
        if len(titles) >= 6:
            return 'too_many_windows', 'too_many_windows'

        signature_parts = sorted(self._name_buckets(titles))
        if browser_context is not None:
            browser_signature = browser_context.signature()
            if browser_signature:
                signature_parts.append('browser:' + browser_signature)
        signature = '|'.join(signature_parts) if signature_parts else 'mixed'
        return 'mixed', signature

    def _name_buckets(self, titles: Sequence[str]) -> List[str]:
        out: List[str] = []
        for title in titles:
            low = title.lower()
            token = low.split(' - ')[-1].strip() if ' - ' in low else low
            token = token[:40]
            if token:
                out.append(token)
        return out

    def _contains_any(self, text: str, needles: Sequence[str]) -> bool:
        return any(needle in text for needle in needles)

    def _to_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(str(value).strip())
        except Exception:
            return default

    def _to_bool(self, value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or '').strip().lower()
        if text in ('1', 'true', 'yes', 'on', 'да', 'истина'):
            return True
        if text in ('0', 'false', 'no', 'off', 'нет', 'ложь'):
            return False
        return default

    def _clamp_interval(self, value: int) -> int:
        return max(MIN_INTERVAL_SEC, min(MAX_INTERVAL_SEC, int(value)))

    def _ui_texts(self) -> Dict[str, str]:
        if self._is_ru_locale():
            return {
                'panel_name': 'Комментарии по окнам',
                'description': (
                    'Плагин анализирует открытые окна и, при наличии браузерного bridge, '
                    'учитывает активную вкладку и список вкладок для более точных комментариев.'
                ),
                'enabled_label': 'Включено',
                'interval_label': 'Интервал проверки (секунды)',
                'browser_tabs_label': 'Учитывать браузерные вкладки',
                'browser_status_connected': 'Browser bridge отвечает, детали вкладок доступны.',
                'browser_status_waiting': 'Browser bridge пока не отвечает. Комментарии будут только по окнам.',
                'browser_status_disabled': 'Браузерный анализ выключен в настройках.',
                'hint': (
                    'Команда для ручной проверки: window-commentary:check-now\n'
                    'Для детальных браузерных комментариев подключите расширение MinaChan Browser Bridge.'
                ),
            }
        return {
            'panel_name': 'Window Commentary',
            'description': (
                'Analyzes open windows and, when the browser bridge is available, '
                'uses the active tab and open tabs for more precise commentary.'
            ),
            'enabled_label': 'Enabled',
            'interval_label': 'Check interval (seconds)',
            'browser_tabs_label': 'Include browser tabs',
            'browser_status_connected': 'Browser bridge is responding, tab details are available.',
            'browser_status_waiting': 'Browser bridge is not responding yet. Commentary will use windows only.',
            'browser_status_disabled': 'Browser analysis is disabled in settings.',
            'hint': (
                'Manual trigger command: window-commentary:check-now\n'
                'Connect MinaChan Browser Bridge for detailed browser commentary.'
            ),
        }


if __name__ == '__main__':
    run_plugin(WindowCommentaryPlugin)
