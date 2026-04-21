#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import parse_qs, urlparse

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from minachan_sdk import MinaChanPlugin, run_plugin


EVENT_BROWSER_CONNECTED = 'browser-extension:connected'
EVENT_BROWSER_TABS_SNAPSHOT = 'browser-extension:tabs-snapshot'
COMMAND_GET_TAB_PAGE_INFO = 'browser-extension:get-tab-page-info'
COMMAND_PUSH_BROWSER_STATE = 'browser-extension:push-browser-state'

EVENT_YOUTUBE_INTEREST = 'browser-context:youtube-interest-detected'
EVENT_YOUTUBE_RECORDS_CHANGED = 'browser-context:youtube-records-changed'

CMD_LIST_YOUTUBE_INTERESTS = 'browser-context:youtube-interest-list'
CMD_CLEAR_YOUTUBE_INTERESTS = 'browser-context:youtube-interest-clear'

DEFAULT_INTEREST_DELAY_MS = 3 * 60 * 1000
DEFAULT_RECORD_LIMIT = 50
DEFAULT_TRANSCRIPT_SEGMENTS = 160
DEFAULT_TRANSCRIPT_CHARS = 30000


@dataclass
class TrackedTab:
    tab_id: int
    url: str
    video_key: str
    first_seen_ms: int
    timer_id: int
    collected: bool


class BrowserTabWatcherPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._interest_delay_ms = DEFAULT_INTEREST_DELAY_MS
        self._record_limit = DEFAULT_RECORD_LIMIT
        self._transcript_max_segments = DEFAULT_TRANSCRIPT_SEGMENTS
        self._transcript_max_chars = DEFAULT_TRANSCRIPT_CHARS
        self._tracked_tabs: Dict[int, TrackedTab] = {}
        self._records: List[Dict[str, Any]] = []
        self._collected_keys: Set[str] = set()

    def on_init(self) -> None:
        self._load_settings()
        self._load_records()

        self.add_listener(
            EVENT_BROWSER_CONNECTED,
            self.on_browser_connected,
            listener_id='browser_tab_watcher_connected',
        )
        self.add_listener(
            EVENT_BROWSER_TABS_SNAPSHOT,
            self.on_tabs_snapshot,
            listener_id='browser_tab_watcher_tabs_snapshot',
        )

        self.register_event(
            EVENT_YOUTUBE_INTEREST,
            {
                'en': 'A YouTube video tab stayed open long enough and page context was collected',
                'ru': 'Вкладка YouTube-ролика достаточно долго была открыта, и контекст страницы собран',
            },
        )
        self.register_event(
            EVENT_YOUTUBE_RECORDS_CHANGED,
            {
                'en': 'Collected YouTube interest records changed',
                'ru': 'Список собранных YouTube-интересов изменился',
            },
        )
        self.register_command(
            CMD_LIST_YOUTUBE_INTERESTS,
            {
                'en': 'List collected YouTube browser interests',
                'ru': 'Показать собранные YouTube-интересы из браузера',
            },
        )
        self.register_command(
            CMD_CLEAR_YOUTUBE_INTERESTS,
            {
                'en': 'Clear collected YouTube browser interests',
                'ru': 'Очистить собранные YouTube-интересы из браузера',
            },
        )
        self.add_listener(
            CMD_LIST_YOUTUBE_INTERESTS,
            self.on_list_youtube_interests,
            listener_id='browser_tab_watcher_list',
        )
        self.add_listener(
            CMD_CLEAR_YOUTUBE_INTERESTS,
            self.on_clear_youtube_interests,
            listener_id='browser_tab_watcher_clear',
        )
        self.register_speech_rule(
            CMD_LIST_YOUTUBE_INTERESTS,
            {
                'en': '(youtube history|youtube interests|what did I watch on youtube)',
                'ru': '(что я смотрел на ютубе|ютуб интересы|история ютуб интересов)',
            },
        )
        self.register_speech_rule(
            CMD_CLEAR_YOUTUBE_INTERESTS,
            {
                'en': 'clear youtube interests',
                'ru': '(очисти|сотри) ютуб интересы',
            },
        )
        self._request_browser_state()

    def on_unload(self) -> None:
        self._cancel_all_timers()
        self._save_records()

    def on_browser_connected(self, sender: str, data: Any, tag: str) -> None:
        self._cancel_all_timers()
        self._tracked_tabs = {}
        self._request_browser_state()

    def on_tabs_snapshot(self, sender: str, data: Any, tag: str) -> None:
        tabs = self._extract_tabs(data)
        if tabs is None:
            return

        youtube_tab_ids: Set[int] = set()
        snapshot_ms = self._timestamp_ms(data.get('atMs')) or self._now_ms()
        for raw_tab in tabs:
            tab_id = self._to_int(raw_tab.get('id'))
            if tab_id is None:
                continue
            url = str(raw_tab.get('url') or '').strip()
            video_key = self._youtube_video_key(url)
            if not video_key:
                continue
            youtube_tab_ids.add(tab_id)
            self._sync_youtube_tracking(
                tab_id=tab_id,
                url=url,
                video_key=video_key,
                first_seen_ms=snapshot_ms,
            )

        stale_ids = [
            tab_id
            for tab_id in self._tracked_tabs.keys()
            if tab_id not in youtube_tab_ids
        ]
        for tab_id in stale_ids:
            self._remove_tracking(tab_id)

    def on_list_youtube_interests(self, sender: str, data: Any, tag: str) -> None:
        records = list(self._records)
        self.reply(
            sender,
            {
                'ok': True,
                'count': len(records),
                'records': records,
            },
        )

        if not records:
            self.request_say_direct('Пока не вижу YouTube-роликов, которые держались открытыми достаточно долго.')
            return

        preview = '; '.join(
            self._record_label(record)
            for record in records[-5:]
        )
        self.request_say_direct(f'Собранные YouTube-интересы: {preview}')

    def on_clear_youtube_interests(self, sender: str, data: Any, tag: str) -> None:
        removed = len(self._records)
        self._records = []
        self._collected_keys = set()
        self._save_records()
        self.send_message(
            EVENT_YOUTUBE_RECORDS_CHANGED,
            {
                'count': 0,
                'removed': removed,
                'records': [],
            },
        )
        self.reply(sender, {'ok': True, 'removed': removed})
        self.request_say_direct('Очистила собранные YouTube-интересы.')

    def _load_settings(self) -> None:
        self._interest_delay_ms = self._int_property(
            'youtubeInterestDelayMs',
            DEFAULT_INTEREST_DELAY_MS,
            minimum=10_000,
            maximum=24 * 60 * 60 * 1000,
        )
        self._record_limit = self._int_property(
            'recordLimit',
            DEFAULT_RECORD_LIMIT,
            minimum=1,
            maximum=500,
        )
        self._transcript_max_segments = self._int_property(
            'transcriptMaxSegments',
            DEFAULT_TRANSCRIPT_SEGMENTS,
            minimum=1,
            maximum=1000,
        )
        self._transcript_max_chars = self._int_property(
            'transcriptMaxChars',
            DEFAULT_TRANSCRIPT_CHARS,
            minimum=1000,
            maximum=120000,
        )

    def _load_records(self) -> None:
        raw_records = self._get_property_safe('youtubeRecords', [])
        if not isinstance(raw_records, list):
            raw_records = []
        self._records = [
            dict(item)
            for item in raw_records
            if isinstance(item, dict) and str(item.get('videoKey') or '').strip()
        ][-self._record_limit :]
        self._collected_keys = {
            str(item.get('videoKey') or '').strip()
            for item in self._records
            if str(item.get('videoKey') or '').strip()
        }

    def _save_records(self) -> None:
        try:
            self.set_property('youtubeRecords', self._records[-self._record_limit :])
            self.save_properties()
        except Exception:
            pass

    def _sync_youtube_tracking(
        self,
        *,
        tab_id: int,
        url: str,
        video_key: str,
        first_seen_ms: int,
    ) -> None:
        current = self._tracked_tabs.get(tab_id)
        if current is not None and current.video_key == video_key:
            current.url = url
            return

        if current is not None:
            self._cancel_tracking_timer(current)

        already_collected = video_key in self._collected_keys
        timer_id = -1
        if not already_collected:
            timer_id = self.set_timer_once(
                self._interest_delay_ms,
                self._timer_callback(tab_id),
            )

        self._tracked_tabs[tab_id] = TrackedTab(
            tab_id=tab_id,
            url=url,
            video_key=video_key,
            first_seen_ms=first_seen_ms,
            timer_id=timer_id,
            collected=already_collected,
        )

    def _timer_callback(self, tab_id: int) -> Callable[..., None]:
        def _callback(sender: str = '', data: Any = None, tag: str = '') -> None:
            self._on_interest_timer(tab_id)

        return _callback

    def _on_interest_timer(self, tab_id: int) -> None:
        tracked = self._tracked_tabs.get(tab_id)
        if tracked is None or tracked.collected:
            return

        tracked.timer_id = -1
        self._request_browser(
            COMMAND_GET_TAB_PAGE_INFO,
            {
                'tabId': tab_id,
                'includeTranscript': True,
                'transcriptMaxSegments': self._transcript_max_segments,
                'transcriptMaxChars': self._transcript_max_chars,
            },
            lambda response: self._handle_page_info_response(tab_id, tracked.video_key, response),
        )

    def _handle_page_info_response(
        self,
        tab_id: int,
        expected_video_key: str,
        response: Dict[str, Any],
    ) -> None:
        tracked = self._tracked_tabs.get(tab_id)
        if tracked is None or tracked.video_key != expected_video_key:
            return
        if not response.get('ok'):
            return

        response_url = str(response.get('url') or '').strip()
        if not response_url and isinstance(response.get('tab'), dict):
            response_url = str(response['tab'].get('url') or '').strip()
        response_key = self._youtube_video_key(response_url or tracked.url)
        if response_key != expected_video_key:
            return

        record = self._build_record(tracked, response)
        if not record:
            return

        tracked.collected = True
        self._store_record(record)
        self.send_message(EVENT_YOUTUBE_INTEREST, record)
        self.send_message(
            EVENT_YOUTUBE_RECORDS_CHANGED,
            {
                'count': len(self._records),
                'latest': record,
            },
        )

    def _build_record(
        self,
        tracked: TrackedTab,
        response: Dict[str, Any],
    ) -> Dict[str, Any]:
        youtube = response.get('youtube') if isinstance(response.get('youtube'), dict) else {}
        page = response.get('page') if isinstance(response.get('page'), dict) else {}
        tab = response.get('tab') if isinstance(response.get('tab'), dict) else {}
        transcript = youtube.get('transcript') if isinstance(youtube.get('transcript'), dict) else {}
        collected_at_ms = self._timestamp_ms(response.get('atMs')) or self._now_ms()

        title = (
            str(youtube.get('title') or '').strip()
            or str(page.get('ogTitle') or '').strip()
            or str(page.get('title') or '').strip()
            or str(tab.get('title') or '').strip()
        )
        url = str(response.get('url') or page.get('url') or tab.get('url') or tracked.url).strip()
        video_id = str(youtube.get('videoId') or self._youtube_video_id(url) or '').strip()

        return {
            'type': 'youtube-video',
            'videoKey': tracked.video_key,
            'videoId': video_id,
            'url': url,
            'title': title,
            'author': str(youtube.get('author') or '').strip(),
            'channelId': str(youtube.get('channelId') or '').strip(),
            'channelUrl': str(youtube.get('channelUrl') or '').strip(),
            'durationSec': self._number_or_none(youtube.get('durationSec')),
            'currentTimeSec': self._number_or_none(youtube.get('currentTimeSec')),
            'isLive': youtube.get('isLive') is True,
            'transcript': {
                'ok': transcript.get('ok') is True,
                'error': str(transcript.get('error') or '').strip(),
                'languageCode': str(transcript.get('languageCode') or '').strip(),
                'languageName': str(transcript.get('languageName') or '').strip(),
                'isAutoGenerated': transcript.get('isAutoGenerated') is True,
                'segments': transcript.get('segments') if isinstance(transcript.get('segments'), list) else [],
                'text': str(transcript.get('text') or '').strip(),
            },
            'page': {
                'canonicalUrl': str(page.get('canonicalUrl') or '').strip(),
                'description': str(page.get('description') or '').strip(),
                'lang': str(page.get('lang') or '').strip(),
                'ogImage': str(page.get('ogImage') or '').strip(),
                'ogSiteName': str(page.get('ogSiteName') or '').strip(),
            },
            'tab': {
                'id': tracked.tab_id,
                'windowId': self._to_int(tab.get('windowId')),
                'index': self._to_int(tab.get('index')),
                'active': tab.get('active') is True,
                'audible': tab.get('audible') is True,
                'muted': tab.get('muted') is True,
                'pinned': tab.get('pinned') is True,
            },
            'firstSeenAtMs': tracked.first_seen_ms,
            'collectedAtMs': collected_at_ms,
            'openDurationMs': max(0, collected_at_ms - tracked.first_seen_ms),
        }

    def _store_record(self, record: Dict[str, Any]) -> None:
        key = str(record.get('videoKey') or '').strip()
        if not key:
            return
        self._records = [
            item
            for item in self._records
            if str(item.get('videoKey') or '').strip() != key
        ]
        self._records.append(record)
        self._records = self._records[-self._record_limit :]
        self._collected_keys = {
            str(item.get('videoKey') or '').strip()
            for item in self._records
            if str(item.get('videoKey') or '').strip()
        }
        self._save_records()

    def _request_browser(
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

    def _request_browser_state(self) -> None:
        self._request_browser(
            COMMAND_PUSH_BROWSER_STATE,
            {},
            lambda response: None,
        )

    def _remove_tracking(self, tab_id: int) -> None:
        tracked = self._tracked_tabs.pop(tab_id, None)
        if tracked is not None:
            self._cancel_tracking_timer(tracked)

    def _cancel_tracking_timer(self, tracked: TrackedTab) -> None:
        if tracked.timer_id < 0:
            return
        try:
            self.cancel_timer(tracked.timer_id)
        except Exception:
            pass
        tracked.timer_id = -1

    def _cancel_all_timers(self) -> None:
        for tracked in list(self._tracked_tabs.values()):
            self._cancel_tracking_timer(tracked)

    def _extract_tabs(self, data: Any) -> Optional[List[Dict[str, Any]]]:
        if not isinstance(data, dict):
            return None
        if data.get('ok') is False:
            return None
        tabs = data.get('tabs')
        if not isinstance(tabs, list):
            return None
        return [dict(item) for item in tabs if isinstance(item, dict)]

    def _youtube_video_key(self, url: str) -> str:
        video_id = self._youtube_video_id(url)
        if not video_id:
            return ''
        return f'youtube:{video_id}'

    def _youtube_video_id(self, url: str) -> str:
        text = str(url or '').strip()
        if not text:
            return ''
        try:
            parsed = urlparse(text)
        except Exception:
            return ''

        host = parsed.netloc.lower().split('@')[-1]
        if host.startswith('www.'):
            host = host[4:]
        if host == 'youtu.be':
            return self._clean_video_id(parsed.path.strip('/').split('/')[0] if parsed.path else '')
        if host not in {'youtube.com', 'm.youtube.com', 'music.youtube.com'}:
            return ''

        query = parse_qs(parsed.query)
        if parsed.path == '/watch':
            return self._clean_video_id((query.get('v') or [''])[0])

        path_parts = [part for part in parsed.path.split('/') if part]
        if len(path_parts) >= 2 and path_parts[0] in {'shorts', 'embed', 'live'}:
            return self._clean_video_id(path_parts[1])
        return ''

    def _clean_video_id(self, value: str) -> str:
        text = str(value or '').strip()
        if re.match(r'^[A-Za-z0-9_-]{6,}$', text):
            return text
        return ''

    def _record_label(self, record: Dict[str, Any]) -> str:
        title = str(record.get('title') or '').strip() or 'YouTube'
        author = str(record.get('author') or '').strip()
        if author:
            return f'{title} - {author}'
        return title

    def _get_property_safe(self, key: str, default: Any = None) -> Any:
        try:
            return self.get_property(key, default)
        except Exception:
            return default

    def _int_property(
        self,
        key: str,
        default: int,
        *,
        minimum: int,
        maximum: int,
    ) -> int:
        raw = self._get_property_safe(key, default)
        try:
            parsed = int(float(str(raw).strip()))
        except Exception:
            return default
        return max(minimum, min(maximum, parsed))

    def _timestamp_ms(self, value: Any) -> Optional[int]:
        parsed = self._to_int(value)
        if parsed is None:
            return None
        if abs(parsed) < 1_000_000_000_000:
            parsed *= 1000
        return parsed

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _to_int(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(float(str(value).strip()))
        except Exception:
            return None

    def _number_or_none(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            parsed = float(str(value).strip())
        except Exception:
            return None
        if parsed != parsed:
            return None
        return parsed


if __name__ == '__main__':
    run_plugin(BrowserTabWatcherPlugin)
