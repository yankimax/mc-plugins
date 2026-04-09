#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlparse

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from minachan_sdk import MinaChanPlugin, run_plugin


EVENT_BROWSER_CONNECTED = 'browser-extension:connected'
EVENT_BROWSER_TABS_SNAPSHOT = 'browser-extension:tabs-snapshot'
EVENT_BROWSER_ACTIVE_TAB_SNAPSHOT = 'browser-extension:active-tab-snapshot'
COMMAND_GET_ACTIVE_TAB = 'browser-extension:get-active-tab'
INTENT_INCOGNITO_TAB_OPENED = 'BROWSER_INCOGNITO_TAB_OPENED'
INTENT_INCOGNITO_TAB_CLOSED = 'BROWSER_INCOGNITO_TAB_CLOSED'
INTENT_INCOGNITO_TAB_LONG_ACTIVE = 'BROWSER_INCOGNITO_TAB_LONG_ACTIVE'
INTENT_INCOGNITO_TAB_PAGINATION_SPIRAL = 'BROWSER_INCOGNITO_TAB_PAGINATION_SPIRAL'
LONG_ACTIVE_DELAY_MS = 5 * 60 * 1000
PAGINATION_DELTA_THRESHOLD = 10


@dataclass(frozen=True)
class PaginationState:
    tab_id: int
    key: str
    scope: str
    start_value: int
    current_value: int
    notified: bool = False


class BrowserIncognitoNotifierPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._known_incognito_tab_ids: Set[int] = set()
        self._snapshot_ready = False
        self._active_incognito_tab_id: Optional[int] = None
        self._active_incognito_timer_id: int = -1
        self._active_incognito_notified = False
        self._pagination_by_tab_id: Dict[int, PaginationState] = {}

    def on_init(self) -> None:
        self.add_listener(
            EVENT_BROWSER_CONNECTED,
            self.on_browser_connected,
            listener_id='browser_incognito_connected',
        )
        self.add_listener(
            EVENT_BROWSER_TABS_SNAPSHOT,
            self.on_tabs_snapshot,
            listener_id='browser_incognito_tabs_snapshot',
        )
        self.add_listener(
            EVENT_BROWSER_ACTIVE_TAB_SNAPSHOT,
            self.on_active_tab_snapshot,
            listener_id='browser_incognito_active_tab_snapshot',
        )

    def on_browser_connected(self, sender: str, data: Any, tag: str) -> None:
        self._reset_baseline()

    def on_tabs_snapshot(self, sender: str, data: Any, tag: str) -> None:
        incognito_tabs = self._extract_incognito_tabs(data)
        if incognito_tabs is None:
            return
        incognito_ids = {tab_id for tab_id, _ in incognito_tabs}

        if not self._snapshot_ready:
            self._known_incognito_tab_ids = incognito_ids
            self._sync_pagination_tracking(incognito_tabs)
            self._snapshot_ready = True
            return

        new_incognito_ids = incognito_ids - self._known_incognito_tab_ids
        closed_incognito_ids = self._known_incognito_tab_ids - incognito_ids
        self._known_incognito_tab_ids = incognito_ids
        self._sync_pagination_tracking(incognito_tabs)

        if new_incognito_ids:
            count = len(new_incognito_ids)
            self.request_say_intent(
                INTENT_INCOGNITO_TAB_OPENED,
                template_vars={'count': count},
                extra={'count': count},
            )

        if closed_incognito_ids:
            count = len(closed_incognito_ids)
            self.request_say_intent(
                INTENT_INCOGNITO_TAB_CLOSED,
                template_vars={'count': count},
                extra={'count': count},
            )

    def on_active_tab_snapshot(self, sender: str, data: Any, tag: str) -> None:
        tab_id = self._extract_active_incognito_tab_id(data)
        if tab_id is None:
            self._clear_active_incognito_tracking()
            return

        if self._active_incognito_tab_id == tab_id:
            return

        self._replace_active_incognito_tracking(tab_id)

    def _reset_baseline(self) -> None:
        self._known_incognito_tab_ids = set()
        self._snapshot_ready = False
        self._clear_active_incognito_tracking()
        self._pagination_by_tab_id = {}

    def _extract_incognito_tab_ids(self, data: Any) -> Optional[Set[int]]:
        tabs = self._extract_incognito_tabs(data)
        if tabs is None:
            return None
        return {tab_id for tab_id, _ in tabs}

    def _extract_incognito_tabs(self, data: Any) -> Optional[List[Tuple[int, Dict[str, Any]]]]:
        if not isinstance(data, dict):
            return None
        if data.get('ok') is False:
            return None

        raw_tabs = data.get('tabs')
        if not isinstance(raw_tabs, list):
            return None

        out: List[Tuple[int, Dict[str, Any]]] = []
        for item in raw_tabs:
            if not isinstance(item, dict):
                continue
            if item.get('incognito') is not True:
                continue
            tab_id = self._to_int(item.get('id'))
            if tab_id is None:
                continue
            out.append((tab_id, dict(item)))
        return out

    def _extract_active_incognito_tab_id(self, data: Any) -> Optional[int]:
        if not isinstance(data, dict):
            return None
        if data.get('ok') is False:
            return None

        raw_tab = data.get('tab')
        if not isinstance(raw_tab, dict):
            return None
        if raw_tab.get('incognito') is not True:
            return None
        return self._to_int(raw_tab.get('id'))

    def _replace_active_incognito_tracking(self, tab_id: int) -> None:
        self._cancel_active_incognito_timer()
        self._active_incognito_tab_id = tab_id
        self._active_incognito_notified = False
        self._active_incognito_timer_id = self.set_timer_once(
            LONG_ACTIVE_DELAY_MS,
            self._on_active_incognito_long_active_timer,
        )

    def _clear_active_incognito_tracking(self) -> None:
        self._cancel_active_incognito_timer()
        self._active_incognito_tab_id = None
        self._active_incognito_notified = False

    def _cancel_active_incognito_timer(self) -> None:
        if self._active_incognito_timer_id < 0:
            return
        try:
            self.cancel_timer(self._active_incognito_timer_id)
        except Exception:
            pass
        self._active_incognito_timer_id = -1

    def _on_active_incognito_long_active_timer(
        self,
        sender: str = '',
        data: Any = None,
        tag: str = '',
    ) -> None:
        timer_tab_id = self._active_incognito_tab_id
        self._active_incognito_timer_id = -1
        if timer_tab_id is None or self._active_incognito_notified:
            return

        self._request_browser(
            COMMAND_GET_ACTIVE_TAB,
            {},
            lambda response: self._handle_long_active_check_response(timer_tab_id, response),
        )

    def _handle_long_active_check_response(
        self,
        expected_tab_id: int,
        response: Dict[str, Any],
    ) -> None:
        if self._active_incognito_notified:
            return
        if self._active_incognito_tab_id != expected_tab_id:
            return
        active_tab_id = self._extract_active_incognito_tab_id(response)
        if active_tab_id != expected_tab_id:
            return

        self._active_incognito_notified = True
        self.request_say_intent(
            INTENT_INCOGNITO_TAB_LONG_ACTIVE,
            template_vars={'count': 1},
            extra={'count': 1},
        )

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

    def _sync_pagination_tracking(
        self,
        incognito_tabs: List[Tuple[int, Dict[str, Any]]],
    ) -> None:
        active_tab_ids = {tab_id for tab_id, _ in incognito_tabs}
        stale_ids = [
            tab_id
            for tab_id in self._pagination_by_tab_id.keys()
            if tab_id not in active_tab_ids
        ]
        for tab_id in stale_ids:
            self._pagination_by_tab_id.pop(tab_id, None)

        for tab_id, raw_tab in incognito_tabs:
            self._track_tab_pagination(tab_id, raw_tab)

    def _track_tab_pagination(self, tab_id: int, raw_tab: Dict[str, Any]) -> None:
        parsed = self._extract_pagination_state_from_url(
            str(raw_tab.get('url') or '').strip(),
        )
        if parsed is None:
            self._pagination_by_tab_id.pop(tab_id, None)
            return

        key, scope, page_value = parsed
        previous = self._pagination_by_tab_id.get(tab_id)
        if previous is None:
            self._pagination_by_tab_id[tab_id] = PaginationState(
                tab_id=tab_id,
                key=key,
                scope=scope,
                start_value=page_value,
                current_value=page_value,
                notified=False,
            )
            return

        if previous.key != key or previous.scope != scope or page_value < previous.start_value:
            self._pagination_by_tab_id[tab_id] = PaginationState(
                tab_id=tab_id,
                key=key,
                scope=scope,
                start_value=page_value,
                current_value=page_value,
                notified=False,
            )
            return

        updated = PaginationState(
            tab_id=tab_id,
            key=previous.key,
            scope=previous.scope,
            start_value=previous.start_value,
            current_value=page_value,
            notified=previous.notified,
        )
        delta = updated.current_value - updated.start_value
        if delta > PAGINATION_DELTA_THRESHOLD and not previous.notified:
            updated = PaginationState(
                tab_id=updated.tab_id,
                key=updated.key,
                scope=updated.scope,
                start_value=updated.start_value,
                current_value=updated.current_value,
                notified=True,
            )
            self.request_say_intent(
                INTENT_INCOGNITO_TAB_PAGINATION_SPIRAL,
                template_vars={
                    'count': 1,
                    'pageStart': updated.start_value,
                    'pageCurrent': updated.current_value,
                    'pageDelta': delta,
                    'pageKey': updated.key,
                },
                extra={
                    'count': 1,
                    'pageStart': updated.start_value,
                    'pageCurrent': updated.current_value,
                    'pageDelta': delta,
                    'pageKey': updated.key,
                },
            )

        self._pagination_by_tab_id[tab_id] = updated

    def _extract_pagination_state_from_url(
        self,
        url: str,
    ) -> Optional[Tuple[str, str, int]]:
        text = str(url or '').strip()
        if not text:
            return None

        try:
            parsed = urlparse(text)
        except Exception:
            return None

        query_match = self._query_pagination_match(parsed)
        if query_match is not None:
            key, value = query_match
            scope = self._pagination_scope(parsed, normalized_path=parsed.path or '/')
            return (key, scope, value)

        path_match = self._path_pagination_match(parsed.path or '/')
        if path_match is not None:
            key, value, normalized_path = path_match
            scope = self._pagination_scope(parsed, normalized_path=normalized_path)
            return (key, scope, value)

        return None

    def _query_pagination_match(self, parsed) -> Optional[Tuple[str, int]]:
        best: Optional[Tuple[int, str, int]] = None
        for raw_key, raw_value in parse_qsl(parsed.query, keep_blank_values=False):
            key = str(raw_key or '').strip().lower()
            if not key:
                continue
            value = self._parse_positive_int(raw_value)
            if value is None:
                continue
            rank = self._pagination_query_rank(key)
            if rank <= 0:
                continue
            candidate = (rank, key, value)
            if best is None or candidate[0] > best[0]:
                best = candidate
        if best is None:
            return None
        return (best[1], best[2])

    def _path_pagination_match(self, path: str) -> Optional[Tuple[str, int, str]]:
        segments = [segment for segment in str(path or '').split('/') if segment]
        for index, segment in enumerate(segments):
            low = segment.lower().strip()
            if low == 'page' and index + 1 < len(segments):
                value = self._parse_positive_int(segments[index + 1])
                if value is not None:
                    return ('path:page', value, self._segments_to_scope_path(segments[:index]))
            if low.startswith('page-'):
                value = self._parse_positive_int(low[len('page-'):])
                if value is not None:
                    return ('path:page', value, self._segments_to_scope_path(segments[:index]))
            if low.startswith('page'):
                value = self._parse_positive_int(low[len('page'):])
                if value is not None:
                    return ('path:page', value, self._segments_to_scope_path(segments[:index]))
        return None

    def _pagination_scope(self, parsed, normalized_path: str) -> str:
        host = str(parsed.netloc or '').strip().lower()
        path = str(normalized_path or '/').strip() or '/'
        return f'{host}{path}'

    def _segments_to_scope_path(self, segments: List[str]) -> str:
        if not segments:
            return '/'
        return '/' + '/'.join(segments)

    def _pagination_query_rank(self, key: str) -> int:
        low = str(key or '').strip().lower()
        if low in ('page', 'paged', 'page_no', 'page_num', 'pageid', 'pageindex', 'pageno'):
            return 100
        if low in ('p', 'pg'):
            return 80
        if 'page' in low:
            return 60
        return 0

    def _parse_positive_int(self, value: Any) -> Optional[int]:
        text = str(value or '').strip()
        if not text or not text.isdigit():
            return None
        try:
            parsed = int(text)
        except Exception:
            return None
        if parsed < 0:
            return None
        return parsed

    def _to_int(self, value: Any) -> Optional[int]:
        try:
            parsed = int(str(value).strip())
        except Exception:
            return None
        if parsed < 0:
            return None
        return parsed


if __name__ == '__main__':
    run_plugin(BrowserIncognitoNotifierPlugin)
