#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sdk_python'))
from minachan_sdk import MinaChanPlugin, run_plugin


SOURCE_ID = 'icalendar'
EXTERNAL_UID_PREFIX = 'ical-v2::'
MANAGED_EXPORT_UID_PREFIX = 'minachan-organizer-v2-'

SYNC_MIN_INTERVAL = 1
SYNC_MAX_INTERVAL = 24 * 60
DEFAULT_SYNC_INTERVAL_MIN = 10

DEFAULT_IMPORT_PAST_DAYS = 14
DEFAULT_IMPORT_FUTURE_DAYS = 365
DEFAULT_REQUEST_TIMEOUT_SEC = 15
DEFAULT_MAX_INSTANCES_PER_EVENT = 500

DAY_MS = 24 * 60 * 60 * 1000


@dataclass
class CalendarSource:
    raw: str
    normalized: str
    calendar_id: str
    local_path: Optional[str]


@dataclass
class RawIcalEvent:
    uid: str
    summary: str
    description: str
    location: str
    categories: List[str]
    status: str
    start_at_ms: int
    due_at_ms: Optional[int]
    duration_ms: Optional[int]
    all_day: bool
    rrule: str
    recurrence_id_ms: Optional[int]
    exdates_ms: Set[int] = field(default_factory=set)
    rdates_ms: Set[int] = field(default_factory=set)


@dataclass
class ExpandedIcalEvent:
    uid: str
    recurrence_key: str
    recurrence_id_ms: Optional[int]
    title: str
    description: str
    location: str
    categories: List[str]
    status: str
    start_at_ms: int
    due_at_ms: Optional[int]
    all_day: bool


class ICalendarPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._calendar_urls: List[str] = []
        self._sync_interval_min = DEFAULT_SYNC_INTERVAL_MIN
        self._auto_sync = True
        self._delete_missing = True
        self._export_local_files = True
        self._import_past_days = DEFAULT_IMPORT_PAST_DAYS
        self._import_future_days = DEFAULT_IMPORT_FUTURE_DAYS
        self._request_timeout_sec = DEFAULT_REQUEST_TIMEOUT_SEC
        self._max_instances_per_event = DEFAULT_MAX_INSTANCES_PER_EVENT

        self._sync_running = False
        self._sync_pending_trigger: Optional[str] = None
        self._timer_id = -1
        self._ui_locale = 'en'

    def on_init(self) -> None:
        self.add_listener('gui:request-panels', self.on_request_panels, listener_id='ical_request_panels')
        self.add_listener('icalendar:update-settings', self.on_update_settings, listener_id='ical_update_settings')
        self.add_listener('icalendar:sync-now', self.on_sync_now, listener_id='ical_sync_now')

        self._load_settings()
        self.add_locale_listener(self._on_locale_changed, default_locale='en')

        self.register_command(
            'icalendar:sync-now',
            {
                'en': 'Sync iCalendar sources with organizer_core',
                'ru': 'Синхронизировать iCalendar-источники с organizer_core',
            },
        )
        self.register_speech_rule(
            'icalendar:sync-now',
            {
                'en': '(sync|refresh) calendar',
                'ru': '(синхронизируй|обнови) календарь',
            },
        )

        self._register_settings_gui()
        self._schedule_next_sync()
        self._start_sync(trigger='startup', speak=False)

    def on_unload(self) -> None:
        if self._timer_id >= 0:
            try:
                self.cancel_timer(self._timer_id)
            except Exception:
                pass
            self._timer_id = -1

    def on_request_panels(self, sender: str, data: Any, tag: str) -> None:
        self._register_settings_gui()

    def on_update_settings(self, sender: str, data: Any, tag: str) -> None:
        payload = data if isinstance(data, dict) else {}

        self._calendar_urls = self._parse_urls_from_payload(payload)
        self._sync_interval_min = self._to_int(
            payload.get('sync_interval_min', payload.get('syncIntervalMin')),
            self._sync_interval_min,
            minimum=SYNC_MIN_INTERVAL,
            maximum=SYNC_MAX_INTERVAL,
        )
        self._auto_sync = self._to_bool(payload.get('auto_sync', payload.get('autoSync')), self._auto_sync)
        self._delete_missing = self._to_bool(
            payload.get('delete_missing', payload.get('deleteMissing')),
            self._delete_missing,
        )
        self._export_local_files = self._to_bool(
            payload.get('export_local_files', payload.get('exportLocalFiles')),
            self._export_local_files,
        )
        self._import_past_days = self._to_int(
            payload.get('import_past_days', payload.get('importPastDays')),
            self._import_past_days,
            minimum=0,
            maximum=3650,
        )
        self._import_future_days = self._to_int(
            payload.get('import_future_days', payload.get('importFutureDays')),
            self._import_future_days,
            minimum=1,
            maximum=3650,
        )
        self._request_timeout_sec = self._to_int(
            payload.get('request_timeout_sec', payload.get('requestTimeoutSec')),
            self._request_timeout_sec,
            minimum=3,
            maximum=120,
        )
        self._max_instances_per_event = self._to_int(
            payload.get('max_instances_per_event', payload.get('maxInstancesPerEvent')),
            self._max_instances_per_event,
            minimum=50,
            maximum=5000,
        )

        self._save_settings()
        self._register_settings_gui()
        self._reschedule_sync_timer()

        self._say(
            self._tr(
                f'iCalendar settings saved. Sources={len(self._calendar_urls)}, interval={self._sync_interval_min} min.',
                f'Настройки iCalendar сохранены. Источников={len(self._calendar_urls)}, интервал={self._sync_interval_min} мин.',
            )
        )

    def on_sync_now(self, sender: str, data: Any, tag: str) -> None:
        ok, message = self._start_sync(trigger='manual', speak=True)
        if not ok:
            self._say(message)

    def _on_locale_changed(self, locale: str, chain: List[str]) -> None:
        self._ui_locale = str(locale or 'en')
        self._register_settings_gui()

    def _start_sync(self, trigger: str, speak: bool) -> Tuple[bool, str]:
        if self._sync_running:
            self._sync_pending_trigger = trigger
            return False, self._tr('Sync is already running.', 'Синхронизация уже выполняется.')

        now_ms = self._now_ms()
        window_start = now_ms - self._import_past_days * DAY_MS
        window_end = now_ms + self._import_future_days * DAY_MS

        desired: Dict[str, Dict[str, Any]] = {}
        configured_calendar_ids: Set[str] = set()
        synced_calendar_ids: Set[str] = set()
        failed_calendars: List[Dict[str, str]] = []
        local_export_paths: List[str] = []

        for source in self._calendar_sources():
            configured_calendar_ids.add(source.calendar_id)
            if source.local_path:
                local_export_paths.append(source.local_path)

            content = self._load_ical_content(source)
            if content is None:
                failed_calendars.append(
                    {
                        'calendarId': source.calendar_id,
                        'url': source.normalized,
                        'reason': self._tr('load failed', 'не удалось загрузить'),
                    }
                )
                continue

            try:
                calendar_name, events = self._parse_calendar_content(content, window_start, window_end)
            except Exception as error:
                failed_calendars.append(
                    {
                        'calendarId': source.calendar_id,
                        'url': source.normalized,
                        'reason': str(error),
                    }
                )
                continue

            synced_calendar_ids.add(source.calendar_id)
            for event in events:
                desired_item = self._build_desired_item(
                    source=source,
                    calendar_name=calendar_name,
                    event=event,
                )
                existing = desired.get(desired_item['externalUid'])
                if existing is None:
                    desired[desired_item['externalUid']] = desired_item
                    continue
                # Deterministic conflict policy for malformed feeds with duplicate identities.
                if int(desired_item.get('startAtMs') or 0) >= int(existing.get('startAtMs') or 0):
                    desired[desired_item['externalUid']] = desired_item

        self._sync_running = True
        context: Dict[str, Any] = {
            'trigger': trigger,
            'speak': bool(speak),
            'startedAtMs': now_ms,
            'desired': desired,
            'configuredCalendarIds': configured_calendar_ids,
            'syncedCalendarIds': synced_calendar_ids,
            'failedCalendars': failed_calendars,
            'localExportPaths': sorted(set(local_export_paths)),
            'errors': [],
            'plan': {
                'upserts': [],
                'deletes': [],
                'unchanged': 0,
            },
            'stats': {
                'created': 0,
                'updated': 0,
                'deleted': 0,
                'unchanged': 0,
                'failedOperations': 0,
                'failedCalendars': len(failed_calendars),
                'exportedFiles': 0,
            },
        }

        self._request_core(
            'organizer-core:upsert-dictionary-entry',
            {
                'kind': 'source',
                'id': SOURCE_ID,
                'name': 'iCalendar',
                'isSystem': True,
            },
            lambda response: self._on_source_ready(context, response),
        )
        return True, self._tr('Sync started.', 'Синхронизация запущена.')

    def _on_source_ready(self, context: Dict[str, Any], response: Dict[str, Any]) -> None:
        if not response.get('ok'):
            context['errors'].append(self._tr('Cannot ensure source dictionary entry.', 'Не удалось подтвердить source в словаре.'))
            context['stats']['failedOperations'] += 1

        self._fetch_all_items(
            {
                'source': SOURCE_ID,
                'includeTerminal': True,
                'sort': 'updated_desc',
            },
            lambda ok, items, error: self._on_existing_items_loaded(context, ok, items, error),
        )

    def _on_existing_items_loaded(
        self,
        context: Dict[str, Any],
        ok: bool,
        items: List[Dict[str, Any]],
        error: str,
    ) -> None:
        if not ok:
            self._finish_sync(
                context,
                success=False,
                summary=self._tr(
                    f'iCalendar sync failed: {error}',
                    f'Синхронизация iCalendar не удалась: {error}',
                ),
            )
            return

        upserts, deletes, unchanged = self._build_sync_plan(
            existing_items=items,
            desired=context['desired'],
            synced_calendar_ids=context['syncedCalendarIds'],
            configured_calendar_ids=context['configuredCalendarIds'],
        )
        context['plan']['upserts'] = upserts
        context['plan']['deletes'] = deletes
        context['plan']['unchanged'] = unchanged
        context['stats']['unchanged'] = unchanged

        self._process_upsert_queue(context, index=0)

    def _process_upsert_queue(self, context: Dict[str, Any], index: int) -> None:
        queue = context['plan']['upserts']
        if index >= len(queue):
            self._process_delete_queue(context, index=0)
            return

        payload = dict(queue[index])
        self._request_core(
            'organizer-core:upsert-external-item',
            payload,
            lambda response: self._on_upsert_response(context, index, response),
        )

    def _on_upsert_response(self, context: Dict[str, Any], index: int, response: Dict[str, Any]) -> None:
        if response.get('ok'):
            operation = str(response.get('op') or '').strip().lower()
            if operation == 'created':
                context['stats']['created'] += 1
            else:
                context['stats']['updated'] += 1
        else:
            context['stats']['failedOperations'] += 1
            context['errors'].append(self._format_error(response))

        self._process_upsert_queue(context, index=index + 1)

    def _process_delete_queue(self, context: Dict[str, Any], index: int) -> None:
        queue = context['plan']['deletes']
        if index >= len(queue):
            self._maybe_export_local_files(context)
            return

        payload = dict(queue[index])
        self._request_core(
            'organizer-core:delete-item',
            {'id': payload.get('id')},
            lambda response: self._on_delete_response(context, index, response),
        )

    def _on_delete_response(self, context: Dict[str, Any], index: int, response: Dict[str, Any]) -> None:
        if response.get('ok'):
            context['stats']['deleted'] += 1
        else:
            context['stats']['failedOperations'] += 1
            context['errors'].append(self._format_error(response))
        self._process_delete_queue(context, index=index + 1)

    def _maybe_export_local_files(self, context: Dict[str, Any]) -> None:
        paths: List[str] = context.get('localExportPaths') or []
        if not self._export_local_files or not paths:
            self._finish_sync(context, success=True, summary=self._build_sync_summary(context))
            return

        self._fetch_all_items(
            {
                'includeTerminal': False,
                'sort': 'start_asc',
            },
            lambda ok, items, error: self._on_export_items_loaded(context, ok, items, error),
        )

    def _on_export_items_loaded(
        self,
        context: Dict[str, Any],
        ok: bool,
        items: List[Dict[str, Any]],
        error: str,
    ) -> None:
        if not ok:
            context['stats']['failedOperations'] += 1
            context['errors'].append(error)
            self._finish_sync(context, success=True, summary=self._build_sync_summary(context))
            return

        export_items = self._build_export_items(items)
        exported = 0
        for path in context.get('localExportPaths') or []:
            try:
                if self._write_local_calendar(path, export_items):
                    exported += 1
            except Exception as write_error:
                context['stats']['failedOperations'] += 1
                context['errors'].append(f'{path}: {write_error}')
        context['stats']['exportedFiles'] = exported
        self._finish_sync(context, success=True, summary=self._build_sync_summary(context))

    def _finish_sync(self, context: Dict[str, Any], success: bool, summary: str) -> None:
        self._sync_running = False

        if success:
            self.send_message('core-events:log', {'message': summary})
        else:
            self.send_message('core-events:error', {'message': summary})

        if context.get('speak'):
            self._say(summary)

        self._schedule_next_sync()

        pending = self._sync_pending_trigger
        self._sync_pending_trigger = None
        if pending:
            self._start_sync(trigger=pending, speak=False)

    def _build_sync_plan(
        self,
        existing_items: Sequence[Dict[str, Any]],
        desired: Dict[str, Dict[str, Any]],
        synced_calendar_ids: Set[str],
        configured_calendar_ids: Set[str],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
        existing_by_external: Dict[str, Dict[str, Any]] = {}
        managed_calendar_ids: Set[str] = set()

        for item in existing_items:
            external_uid = str(item.get('externalUid') or '').strip()
            if not external_uid.startswith(EXTERNAL_UID_PREFIX):
                continue
            calendar_id = self._calendar_id_from_external_uid(external_uid)
            if not calendar_id:
                continue
            managed_calendar_ids.add(calendar_id)
            existing_by_external[external_uid] = dict(item)

        upserts: List[Dict[str, Any]] = []
        unchanged = 0
        for external_uid, desired_item in desired.items():
            current = existing_by_external.get(external_uid)
            if current is not None and self._is_item_up_to_date(current, desired_item):
                unchanged += 1
                continue
            upserts.append(dict(desired_item))

        deletes: List[Dict[str, Any]] = []
        if self._delete_missing:
            deletion_scope = set(synced_calendar_ids)
            deletion_scope.update(managed_calendar_ids - set(configured_calendar_ids))

            for external_uid, current in existing_by_external.items():
                if external_uid in desired:
                    continue
                calendar_id = self._calendar_id_from_external_uid(external_uid)
                if not calendar_id or calendar_id not in deletion_scope:
                    continue
                item_id = self._to_int(current.get('id'), -1, minimum=-1, maximum=10_000_000)
                if item_id <= 0:
                    continue
                deletes.append({'id': item_id, 'externalUid': external_uid})

        upserts.sort(key=lambda value: str(value.get('externalUid') or ''))
        deletes.sort(key=lambda value: int(value.get('id') or 0))
        return upserts, deletes, unchanged

    def _is_item_up_to_date(self, item: Dict[str, Any], desired_item: Dict[str, Any]) -> bool:
        if str(item.get('title') or '') != str(desired_item.get('title') or ''):
            return False
        if str(item.get('description') or '') != str(desired_item.get('description') or ''):
            return False

        current_start = self._to_optional_int(item.get('startAtMs'))
        current_due = self._to_optional_int(item.get('dueAtMs'))
        desired_start = self._to_optional_int(desired_item.get('startAtMs'))
        desired_due = self._to_optional_int(desired_item.get('dueAtMs'))
        if current_start != desired_start or current_due != desired_due:
            return False

        desired_state = str(desired_item.get('state') or '').strip()
        if desired_state and str(item.get('state') or '').strip() != desired_state:
            return False

        payload = item.get('payload') if isinstance(item.get('payload'), dict) else {}
        desired_payload = desired_item.get('payload') if isinstance(desired_item.get('payload'), dict) else {}
        return str(payload.get('fingerprint') or '') == str(desired_payload.get('fingerprint') or '')

    def _build_sync_summary(self, context: Dict[str, Any]) -> str:
        stats = context.get('stats') if isinstance(context.get('stats'), dict) else {}
        created = int(stats.get('created') or 0)
        updated = int(stats.get('updated') or 0)
        deleted = int(stats.get('deleted') or 0)
        unchanged = int(stats.get('unchanged') or 0)
        failed_calendars = int(stats.get('failedCalendars') or 0)
        failed_operations = int(stats.get('failedOperations') or 0)
        exported_files = int(stats.get('exportedFiles') or 0)

        if self._is_ru_locale():
            return (
                f'iCalendar sync: +{created}, ~{updated}, ={unchanged}, -{deleted}, '
                f'календари-ошибки={failed_calendars}, операции-ошибки={failed_operations}, '
                f'экспорт={exported_files}.'
            )
        return (
            f'iCalendar sync: +{created}, ~{updated}, ={unchanged}, -{deleted}, '
            f'failedCalendars={failed_calendars}, failedOperations={failed_operations}, '
            f'exported={exported_files}.'
        )

    def _request_core(
        self,
        command: str,
        payload: Dict[str, Any],
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        completed = {'value': False}

        def _complete(response: Dict[str, Any]) -> None:
            if completed['value']:
                return
            completed['value'] = True
            try:
                callback(response)
            except Exception as error:
                self.send_message('core-events:error', {'message': f'icalendar callback error: {error}'})

        def _on_response(sender: str, data: Any, tag: str) -> None:
            if isinstance(data, dict):
                _complete(dict(data))
                return
            _complete(
                {
                    'ok': False,
                    'error': {
                        'code': 'invalid_response',
                        'message': f'Invalid response type for {command}',
                    },
                }
            )

        def _on_complete(sender: str, data: Any, tag: str) -> None:
            _complete(
                {
                    'ok': False,
                    'error': {
                        'code': 'no_response',
                        'message': f'No response for {command}',
                    },
                }
            )

        seq = self.send_message_with_response(
            command,
            payload,
            on_response=_on_response,
            on_complete=_on_complete,
        )
        if seq < 0:
            _complete(
                {
                    'ok': False,
                    'error': {
                        'code': 'send_failed',
                        'message': f'Failed to send {command}',
                    },
                }
            )

    def _fetch_all_items(
        self,
        base_payload: Dict[str, Any],
        callback: Callable[[bool, List[Dict[str, Any]], str], None],
    ) -> None:
        items: List[Dict[str, Any]] = []
        limit = self._to_int(base_payload.get('limit'), 200, minimum=1, maximum=1000)

        def _step(offset: int) -> None:
            payload = dict(base_payload)
            payload['limit'] = limit
            payload['offset'] = offset
            self._request_core(
                'organizer-core:list-items',
                payload,
                lambda response: _on_page(offset, response),
            )

        def _on_page(offset: int, response: Dict[str, Any]) -> None:
            if not response.get('ok'):
                callback(False, items, self._format_error(response))
                return

            page_raw = response.get('items')
            page: List[Dict[str, Any]] = []
            if isinstance(page_raw, list):
                for item in page_raw:
                    if isinstance(item, dict):
                        page.append(dict(item))
            items.extend(page)

            count = self._to_int(response.get('count'), len(page), minimum=0, maximum=1000)
            total = self._to_int(response.get('total'), len(items), minimum=0, maximum=2_000_000)
            next_offset = offset + max(count, len(page))

            if page and next_offset < total:
                _step(next_offset)
                return
            callback(True, items, '')

        _step(0)

    def _calendar_sources(self) -> List[CalendarSource]:
        out: List[CalendarSource] = []
        seen: Set[str] = set()
        for raw in self._calendar_urls:
            normalized = self._normalize_calendar_url(raw)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            local_path = self._as_local_path(normalized)
            calendar_id = hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:12]
            out.append(
                CalendarSource(
                    raw=raw,
                    normalized=normalized,
                    calendar_id=calendar_id,
                    local_path=local_path,
                )
            )
        return out

    def _normalize_calendar_url(self, raw: Any) -> str:
        value = str(raw or '').strip()
        if not value:
            return ''

        if value.startswith('webcal://'):
            value = 'https://' + value[len('webcal://'):]

        if re.match(r'^https?://', value, flags=re.IGNORECASE):
            return value

        if value.startswith('file://'):
            parsed = urllib.parse.urlparse(value)
            path = urllib.parse.unquote(parsed.path or '')
            if not path:
                return ''
            path = os.path.abspath(os.path.expanduser(path))
            return 'file://' + urllib.parse.quote(path)

        path = os.path.abspath(os.path.expanduser(value))
        return 'file://' + urllib.parse.quote(path)

    def _load_ical_content(self, source: CalendarSource) -> Optional[str]:
        if source.local_path:
            if not os.path.exists(source.local_path):
                return None
            with open(source.local_path, 'r', encoding='utf-8', errors='replace') as fp:
                return fp.read()

        try:
            request = urllib.request.Request(
                source.normalized,
                headers={'User-Agent': 'MinaChan-iCalendar/2.0'},
            )
            with urllib.request.urlopen(request, timeout=self._request_timeout_sec) as response:
                raw_data = response.read()
                charset = response.headers.get_content_charset() or 'utf-8'
                try:
                    return raw_data.decode(charset, errors='replace')
                except Exception:
                    return raw_data.decode('utf-8', errors='replace')
        except Exception:
            return None

    def _parse_calendar_content(
        self,
        text: str,
        window_start_ms: int,
        window_end_ms: int,
    ) -> Tuple[str, List[ExpandedIcalEvent]]:
        lines = self._unfold_ical_lines(text)
        if not lines:
            return '', []

        global_props: Dict[str, List[Tuple[Dict[str, str], str]]] = {}
        current_event: Optional[Dict[str, List[Tuple[Dict[str, str], str]]]] = None
        raw_events: List[RawIcalEvent] = []

        for line in lines:
            upper = line.strip().upper()
            if upper == 'BEGIN:VEVENT':
                current_event = {}
                continue
            if upper == 'END:VEVENT':
                if current_event:
                    parsed = self._raw_event_from_properties(current_event)
                    if parsed is not None:
                        raw_events.append(parsed)
                current_event = None
                continue

            name, params, value = self._parse_property_line(line)
            if not name:
                continue
            if current_event is None:
                global_props.setdefault(name, []).append((params, value))
                continue
            current_event.setdefault(name, []).append((params, value))

        calendar_name = self._global_calendar_name(global_props)
        events = self._expand_raw_events(raw_events, window_start_ms, window_end_ms)
        return calendar_name, events

    def _raw_event_from_properties(
        self,
        props: Dict[str, List[Tuple[Dict[str, str], str]]],
    ) -> Optional[RawIcalEvent]:
        dtstart_prop = self._first_property(props, 'DTSTART')
        if dtstart_prop is None:
            return None
        start_ms, all_day = self._parse_datetime_property(dtstart_prop)
        if start_ms is None:
            return None

        dtend_prop = self._first_property(props, 'DTEND')
        due_at_ms: Optional[int] = None
        if dtend_prop is not None:
            due_at_ms, _ = self._parse_datetime_property(dtend_prop)

        duration_ms: Optional[int] = None
        duration_prop = self._first_property(props, 'DURATION')
        if duration_prop is not None:
            duration_ms = self._parse_duration(duration_prop[1])

        if due_at_ms is None and duration_ms is not None:
            due_at_ms = start_ms + duration_ms
        if due_at_ms is not None and due_at_ms <= start_ms:
            due_at_ms = None

        recurrence_prop = self._first_property(props, 'RECURRENCE-ID')
        recurrence_id_ms: Optional[int] = None
        if recurrence_prop is not None:
            recurrence_id_ms, _ = self._parse_datetime_property(recurrence_prop)

        summary = self._unescape_ical_text(self._first_text_property(props, 'SUMMARY', 'iCal event')).strip() or 'iCal event'
        description = self._unescape_ical_text(self._first_text_property(props, 'DESCRIPTION', '')).strip()
        location = self._unescape_ical_text(self._first_text_property(props, 'LOCATION', '')).strip()

        status = self._first_text_property(props, 'STATUS', '').strip().upper()

        uid = self._unescape_ical_text(self._first_text_property(props, 'UID', '')).strip()
        if not uid:
            digest = hashlib.sha1(
                f'{summary}|{start_ms}|{description}'.encode('utf-8', errors='replace')
            ).hexdigest()[:16]
            uid = f'auto-{digest}'

        categories: List[str] = []
        for params, value in props.get('CATEGORIES', []):
            for part in value.split(','):
                text_value = self._unescape_ical_text(part).strip()
                if text_value:
                    categories.append(text_value)
        categories = self._dedupe_list(categories)

        rrule = self._first_text_property(props, 'RRULE', '').strip().upper()
        exdates = self._collect_multi_datetime_property(props.get('EXDATE', []))
        rdates = self._collect_multi_datetime_property(props.get('RDATE', []))

        return RawIcalEvent(
            uid=uid,
            summary=summary,
            description=description,
            location=location,
            categories=categories,
            status=status,
            start_at_ms=start_ms,
            due_at_ms=due_at_ms,
            duration_ms=duration_ms,
            all_day=all_day,
            rrule=rrule,
            recurrence_id_ms=recurrence_id_ms,
            exdates_ms=exdates,
            rdates_ms=rdates,
        )

    def _expand_raw_events(
        self,
        raw_events: Sequence[RawIcalEvent],
        window_start_ms: int,
        window_end_ms: int,
    ) -> List[ExpandedIcalEvent]:
        grouped: Dict[str, List[RawIcalEvent]] = {}
        for event in raw_events:
            grouped.setdefault(event.uid, []).append(event)

        out: List[ExpandedIcalEvent] = []
        canceled_status = {'CANCELLED', 'CANCELED'}

        for uid, events in grouped.items():
            masters: List[RawIcalEvent] = []
            overrides: Dict[int, RawIcalEvent] = {}
            cancelled_instances: Set[int] = set()

            for event in events:
                if event.recurrence_id_ms is None:
                    masters.append(event)
                    continue
                if event.recurrence_id_ms is None:
                    continue
                if event.status in canceled_status:
                    cancelled_instances.add(event.recurrence_id_ms)
                    continue
                overrides[event.recurrence_id_ms] = event

            if not masters:
                for _, override in sorted(overrides.items()):
                    if self._is_within_window(override.start_at_ms, window_start_ms, window_end_ms):
                        out.append(self._expanded_from_override(override))
                continue

            for master in masters:
                if master.status in canceled_status and not master.rrule and not master.rdates_ms:
                    continue

                occurrence_ids = self._expand_occurrences(master, window_start_ms, window_end_ms)
                for occurrence_id in occurrence_ids:
                    if occurrence_id in master.exdates_ms or occurrence_id in cancelled_instances:
                        continue

                    override = overrides.pop(occurrence_id, None)
                    if override is not None:
                        if override.status in canceled_status:
                            continue
                        if not self._is_within_window(override.start_at_ms, window_start_ms, window_end_ms):
                            continue
                        out.append(self._expanded_from_override(override, occurrence_id))
                        continue

                    start_at_ms = int(occurrence_id)
                    due_at_ms = None
                    if master.duration_ms is not None:
                        due_at_ms = start_at_ms + int(master.duration_ms)
                    elif master.due_at_ms is not None:
                        due_at_ms = start_at_ms + (int(master.due_at_ms) - int(master.start_at_ms))
                    if due_at_ms is not None and due_at_ms <= start_at_ms:
                        due_at_ms = None

                    if not self._is_within_window(start_at_ms, window_start_ms, window_end_ms):
                        continue

                    recurrence_key = 'single'
                    recurrence_id_ms: Optional[int] = None
                    if master.rrule or master.rdates_ms:
                        recurrence_key = str(occurrence_id)
                        recurrence_id_ms = int(occurrence_id)

                    out.append(
                        ExpandedIcalEvent(
                            uid=master.uid,
                            recurrence_key=recurrence_key,
                            recurrence_id_ms=recurrence_id_ms,
                            title=master.summary,
                            description=master.description,
                            location=master.location,
                            categories=list(master.categories),
                            status=master.status,
                            start_at_ms=start_at_ms,
                            due_at_ms=due_at_ms,
                            all_day=master.all_day,
                        )
                    )

            for recurrence_id, override in sorted(overrides.items()):
                if override.status in canceled_status:
                    continue
                if self._is_within_window(override.start_at_ms, window_start_ms, window_end_ms):
                    out.append(self._expanded_from_override(override, recurrence_id))

        out.sort(key=lambda item: (item.start_at_ms, item.uid, item.recurrence_key))
        return out

    def _expanded_from_override(
        self,
        event: RawIcalEvent,
        recurrence_id: Optional[int] = None,
    ) -> ExpandedIcalEvent:
        identity = recurrence_id if recurrence_id is not None else event.recurrence_id_ms
        recurrence_key = str(identity) if identity is not None else 'single'
        return ExpandedIcalEvent(
            uid=event.uid,
            recurrence_key=recurrence_key,
            recurrence_id_ms=identity,
            title=event.summary,
            description=event.description,
            location=event.location,
            categories=list(event.categories),
            status=event.status,
            start_at_ms=event.start_at_ms,
            due_at_ms=event.due_at_ms,
            all_day=event.all_day,
        )

    def _expand_occurrences(
        self,
        event: RawIcalEvent,
        window_start_ms: int,
        window_end_ms: int,
    ) -> List[int]:
        occurrences: Set[int] = set()

        if not event.rrule:
            occurrences.add(int(event.start_at_ms))
        else:
            occurrences.update(self._expand_rrule_occurrences(event, window_start_ms, window_end_ms))

        for rdate_ms in event.rdates_ms:
            if rdate_ms >= event.start_at_ms:
                occurrences.add(int(rdate_ms))

        if not occurrences:
            occurrences.add(int(event.start_at_ms))

        filtered = [
            value
            for value in occurrences
            if value <= window_end_ms and value >= (window_start_ms - DAY_MS)
        ]
        filtered.sort()
        return filtered

    def _expand_rrule_occurrences(
        self,
        event: RawIcalEvent,
        window_start_ms: int,
        window_end_ms: int,
    ) -> List[int]:
        rule = self._parse_rrule(event.rrule)
        freq = str(rule.get('FREQ') or '').upper()
        if freq not in {'DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'}:
            return [int(event.start_at_ms)]

        interval = self._positive_int(rule.get('INTERVAL'), 1)
        count = self._positive_int(rule.get('COUNT'), 0)
        until_ms: Optional[int] = None
        until_raw = str(rule.get('UNTIL') or '').strip()
        if until_raw:
            until_ms = self._parse_ical_datetime(until_raw, tzid=None, value_type=None)

        if until_ms is not None and until_ms < event.start_at_ms:
            return []

        max_end_ms = window_end_ms
        if until_ms is not None:
            max_end_ms = min(max_end_ms, until_ms)

        start_dt = self._timestamp_to_local_dt(event.start_at_ms)
        max_end_dt = self._timestamp_to_local_dt(max_end_ms)
        emitted_total = 0
        out: List[int] = []
        seen: Set[int] = set()
        min_emit_ms = window_start_ms - DAY_MS

        def _push(candidate: datetime) -> bool:
            nonlocal emitted_total
            candidate_ms = int(candidate.timestamp() * 1000)
            if candidate_ms < event.start_at_ms:
                return False
            if until_ms is not None and candidate_ms > until_ms:
                return True
            emitted_total += 1
            if count > 0 and emitted_total > count:
                return True
            if candidate_ms >= min_emit_ms and candidate_ms not in seen:
                seen.add(candidate_ms)
                out.append(candidate_ms)
            return count > 0 and emitted_total >= count

        stop = False
        safety = 0
        limit = max(
            5000,
            int(max(0, window_end_ms - event.start_at_ms) / DAY_MS) + self._max_instances_per_event + 2000,
        )

        if freq == 'DAILY':
            current = start_dt
            while not stop and current <= max_end_dt:
                stop = _push(current)
                current += timedelta(days=interval)
                safety += 1
                if safety > limit:
                    break

        elif freq == 'WEEKLY':
            byday = self._parse_byday(rule.get('BYDAY'))
            if not byday:
                byday = [start_dt.weekday()]
            byday = sorted(set(byday))

            week_start = start_dt.date() - timedelta(days=start_dt.weekday())
            while not stop:
                for weekday in byday:
                    occ_date = week_start + timedelta(days=weekday)
                    candidate = self._combine_local_date_with_start_time(occ_date, start_dt)
                    if candidate > max_end_dt:
                        stop = True
                        break
                    if candidate < start_dt:
                        continue
                    stop = _push(candidate)
                    if stop:
                        break
                week_start += timedelta(days=7 * interval)
                safety += 1
                if safety > limit:
                    break
                first_next = self._combine_local_date_with_start_time(week_start, start_dt)
                if first_next > max_end_dt:
                    break

        elif freq == 'MONTHLY':
            bymonthday = self._parse_int_list(rule.get('BYMONTHDAY'))
            if not bymonthday:
                bymonthday = [start_dt.day]
            bymonthday = sorted(set(value for value in bymonthday if 1 <= value <= 31))
            if not bymonthday:
                bymonthday = [start_dt.day]

            year = start_dt.year
            month = start_dt.month
            while not stop:
                for monthday in bymonthday:
                    candidate = self._make_local_datetime(year, month, monthday, start_dt)
                    if candidate is None:
                        continue
                    if candidate > max_end_dt:
                        stop = True
                        break
                    if candidate < start_dt:
                        continue
                    stop = _push(candidate)
                    if stop:
                        break
                year, month = self._add_months(year, month, interval)
                safety += 1
                if safety > limit:
                    break
                check = self._make_local_datetime(year, month, 1, start_dt)
                if check is not None and check > max_end_dt:
                    break

        else:  # YEARLY
            bymonth = self._parse_int_list(rule.get('BYMONTH'))
            if not bymonth:
                bymonth = [start_dt.month]
            bymonth = sorted(set(value for value in bymonth if 1 <= value <= 12))
            if not bymonth:
                bymonth = [start_dt.month]

            bymonthday = self._parse_int_list(rule.get('BYMONTHDAY'))
            if not bymonthday:
                bymonthday = [start_dt.day]
            bymonthday = sorted(set(value for value in bymonthday if 1 <= value <= 31))
            if not bymonthday:
                bymonthday = [start_dt.day]

            year = start_dt.year
            while not stop:
                for month in bymonth:
                    for monthday in bymonthday:
                        candidate = self._make_local_datetime(year, month, monthday, start_dt)
                        if candidate is None:
                            continue
                        if candidate > max_end_dt:
                            stop = True
                            break
                        if candidate < start_dt:
                            continue
                        stop = _push(candidate)
                        if stop:
                            break
                    if stop:
                        break
                year += interval
                safety += 1
                if safety > limit:
                    break
                check = self._make_local_datetime(year, bymonth[0], 1, start_dt)
                if check is not None and check > max_end_dt:
                    break

        if int(event.start_at_ms) not in seen:
            if int(event.start_at_ms) >= min_emit_ms:
                out.append(int(event.start_at_ms))

        out = sorted(set(out))
        if len(out) > self._max_instances_per_event:
            return out[-self._max_instances_per_event :]
        return out

    def _build_desired_item(
        self,
        source: CalendarSource,
        calendar_name: str,
        event: ExpandedIcalEvent,
    ) -> Dict[str, Any]:
        external_uid = self._build_external_uid(source.calendar_id, event.uid, event.recurrence_key)
        tags = self._normalize_categories(event.categories)

        payload_meta: Dict[str, Any] = {
            'integration': 'icalendar',
            'schema': 2,
            'calendar': {
                'id': source.calendar_id,
                'url': source.normalized,
                'name': calendar_name,
            },
            'event': {
                'uid': event.uid,
                'recurrenceKey': event.recurrence_key,
                'recurrenceIdMs': event.recurrence_id_ms,
                'allDay': bool(event.all_day),
                'location': event.location,
                'status': event.status,
                'categories': list(tags),
            },
        }
        fingerprint = hashlib.sha1(
            json.dumps(
                {
                    'title': event.title,
                    'description': event.description,
                    'startAtMs': event.start_at_ms,
                    'dueAtMs': event.due_at_ms,
                    'categories': tags,
                    'location': event.location,
                    'status': event.status,
                    'allDay': event.all_day,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode('utf-8')
        ).hexdigest()
        payload_meta['fingerprint'] = fingerprint

        desired_item = {
            'source': SOURCE_ID,
            'externalUid': external_uid,
            'title': event.title,
            'description': event.description,
            'startAtMs': event.start_at_ms,
            'dueAtMs': event.due_at_ms,
            'tags': tags,
            'payload': payload_meta,
        }
        mapped_state = self._mapped_state_for_event(source, calendar_name, event)
        if mapped_state:
            desired_item['state'] = mapped_state
        return desired_item

    def _mapped_state_for_event(
        self,
        source: CalendarSource,
        calendar_name: str,
        event: ExpandedIcalEvent,
    ) -> str:
        if not self._is_todoist_calendar(source, calendar_name):
            return ''
        if str(event.title or '').startswith('✓ '):
            return 'done'
        return ''

    def _is_todoist_calendar(self, source: CalendarSource, calendar_name: str) -> bool:
        name = str(calendar_name or '').strip().lower()
        if 'todoist' in name:
            return True
        normalized = str(source.normalized or '').strip().lower()
        if 'todoist' in normalized:
            return True
        return False

    def _build_external_uid(self, calendar_id: str, uid: str, recurrence_key: str) -> str:
        digest = hashlib.sha1(
            f'{uid}|{recurrence_key}'.encode('utf-8', errors='replace')
        ).hexdigest()[:24]
        return f'{EXTERNAL_UID_PREFIX}{calendar_id}:{digest}'

    def _calendar_id_from_external_uid(self, external_uid: str) -> str:
        if not external_uid.startswith(EXTERNAL_UID_PREFIX):
            return ''
        value = external_uid[len(EXTERNAL_UID_PREFIX):]
        if ':' not in value:
            return ''
        return value.split(':', 1)[0].strip()

    def _build_export_items(self, items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        now_ms = self._now_ms()
        min_start = now_ms - self._import_past_days * DAY_MS
        out: List[Dict[str, Any]] = []
        for item in items:
            source = str(item.get('source') or '').strip()
            if source == SOURCE_ID:
                continue

            start_ms = self._to_optional_int(item.get('startAtMs'))
            due_ms = self._to_optional_int(item.get('dueAtMs'))
            anchor = start_ms if start_ms is not None else due_ms
            if anchor is None or anchor < min_start:
                continue

            title = str(item.get('title') or '').strip() or 'Organizer item'
            description = str(item.get('description') or '').strip()
            item_id = self._to_int(item.get('id'), -1, minimum=-1, maximum=10_000_000)
            if item_id <= 0:
                continue

            out.append(
                {
                    'id': item_id,
                    'title': title,
                    'description': description,
                    'startAtMs': start_ms if start_ms is not None else due_ms,
                    'dueAtMs': due_ms,
                }
            )

        out.sort(key=lambda value: int(value.get('startAtMs') or 0))
        return out

    def _write_local_calendar(self, path: str, export_items: Sequence[Dict[str, Any]]) -> bool:
        existing = ''
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8', errors='replace') as fp:
                existing = fp.read()

        managed_blocks = self._build_managed_export_blocks(export_items)
        merged = self._merge_calendar_with_managed_events(existing, managed_blocks)
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fp:
            fp.write(merged)
        return True

    def _build_managed_export_blocks(self, items: Sequence[Dict[str, Any]]) -> List[List[str]]:
        blocks: List[List[str]] = []
        dtstamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        for item in items:
            start_at_ms = self._to_optional_int(item.get('startAtMs'))
            if start_at_ms is None:
                continue
            due_at_ms = self._to_optional_int(item.get('dueAtMs'))
            if due_at_ms is not None and due_at_ms <= start_at_ms:
                due_at_ms = None

            uid = f'{MANAGED_EXPORT_UID_PREFIX}{int(item.get("id"))}@minachan'
            summary = self._escape_ical_text(str(item.get('title') or 'Organizer item'))
            description = self._escape_ical_text(str(item.get('description') or ''))
            start_text = self._format_ics_utc(start_at_ms)

            block = [
                'BEGIN:VEVENT',
                f'UID:{uid}',
                f'DTSTAMP:{dtstamp}',
                f'DTSTART:{start_text}',
                f'SUMMARY:{summary}',
                'X-MINACHAN-MANAGED:1',
            ]
            if due_at_ms is not None:
                block.append(f'DTEND:{self._format_ics_utc(due_at_ms)}')
            if description:
                block.append(f'DESCRIPTION:{description}')
            block.append('END:VEVENT')
            blocks.append(block)
        return blocks

    def _merge_calendar_with_managed_events(self, existing_text: str, managed_blocks: List[List[str]]) -> str:
        if not existing_text.strip():
            return self._build_calendar_from_blocks(managed_blocks)

        lines = existing_text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        has_begin = False
        has_end = False
        inserted = False

        out: List[str] = []
        event_lines: List[str] = []
        in_event = False

        for line in lines:
            upper = line.strip().upper()
            if upper == 'BEGIN:VCALENDAR':
                has_begin = True

            if not in_event and upper == 'BEGIN:VEVENT':
                in_event = True
                event_lines = [line]
                continue

            if in_event:
                event_lines.append(line)
                if upper == 'END:VEVENT':
                    in_event = False
                    uid = self._extract_uid_from_event_lines(event_lines)
                    if not uid.startswith(MANAGED_EXPORT_UID_PREFIX):
                        out.extend(event_lines)
                    event_lines = []
                continue

            if upper == 'END:VCALENDAR':
                if not inserted:
                    for block in managed_blocks:
                        out.extend(block)
                    inserted = True
                has_end = True
            out.append(line)

        if not has_begin:
            return self._build_calendar_from_blocks(managed_blocks)

        if not has_end:
            if not inserted:
                for block in managed_blocks:
                    out.extend(block)
            out.append('END:VCALENDAR')

        return self._join_ics_lines(out)

    def _build_calendar_from_blocks(self, blocks: List[List[str]]) -> str:
        lines = [
            'BEGIN:VCALENDAR',
            'VERSION:2.0',
            'PRODID:-//MinaChan//iCalendar Sync v2//EN',
            'CALSCALE:GREGORIAN',
        ]
        for block in blocks:
            lines.extend(block)
        lines.append('END:VCALENDAR')
        return self._join_ics_lines(lines)

    def _extract_uid_from_event_lines(self, event_lines: Sequence[str]) -> str:
        unfolded: List[str] = []
        for line in event_lines:
            if unfolded and (line.startswith(' ') or line.startswith('\t')):
                unfolded[-1] += line[1:]
            else:
                unfolded.append(line)

        for line in unfolded:
            name, params, value = self._parse_property_line(line)
            if name == 'UID':
                return self._unescape_ical_text(value).strip()
        return ''

    def _unfold_ical_lines(self, text: str) -> List[str]:
        src = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        out: List[str] = []
        for line in src:
            if not line:
                continue
            if out and (line.startswith(' ') or line.startswith('\t')):
                out[-1] += line[1:]
            else:
                out.append(line)
        return out

    def _parse_property_line(self, line: str) -> Tuple[str, Dict[str, str], str]:
        if ':' not in line:
            return '', {}, ''
        left, value = line.split(':', 1)
        parts = left.split(';')
        name = str(parts[0] or '').strip().upper()
        if not name:
            return '', {}, ''

        params: Dict[str, str] = {}
        for part in parts[1:]:
            if '=' not in part:
                continue
            key, raw_value = part.split('=', 1)
            params[key.strip().upper()] = raw_value.strip().strip('"')
        return name, params, value

    def _first_property(
        self,
        props: Dict[str, List[Tuple[Dict[str, str], str]]],
        key: str,
    ) -> Optional[Tuple[Dict[str, str], str]]:
        values = props.get(key.upper())
        if not values:
            return None
        return values[0]

    def _first_text_property(
        self,
        props: Dict[str, List[Tuple[Dict[str, str], str]]],
        key: str,
        default: str,
    ) -> str:
        found = self._first_property(props, key)
        if found is None:
            return default
        return str(found[1] or default)

    def _parse_datetime_property(self, prop: Tuple[Dict[str, str], str]) -> Tuple[Optional[int], bool]:
        params, value = prop
        value_type = str(params.get('VALUE') or '').strip().upper() or None
        tzid = str(params.get('TZID') or '').strip() or None
        parsed = self._parse_ical_datetime(value, tzid=tzid, value_type=value_type)
        all_day = bool(value_type == 'DATE' or re.fullmatch(r'\d{8}', str(value or '').strip()))
        return parsed, all_day

    def _collect_multi_datetime_property(
        self,
        values: Sequence[Tuple[Dict[str, str], str]],
    ) -> Set[int]:
        out: Set[int] = set()
        for params, value in values:
            value_type = str(params.get('VALUE') or '').strip().upper() or None
            tzid = str(params.get('TZID') or '').strip() or None
            for chunk in str(value or '').split(','):
                parsed = self._parse_ical_datetime(chunk.strip(), tzid=tzid, value_type=value_type)
                if parsed is not None:
                    out.add(int(parsed))
        return out

    def _parse_ical_datetime(
        self,
        value: str,
        tzid: Optional[str],
        value_type: Optional[str],
    ) -> Optional[int]:
        raw = str(value or '').strip()
        if not raw:
            return None

        if value_type == 'DATE' or re.fullmatch(r'\d{8}', raw):
            try:
                date_value = datetime.strptime(raw[:8], '%Y%m%d').date()
                dt = datetime(
                    date_value.year,
                    date_value.month,
                    date_value.day,
                    0,
                    0,
                    0,
                    tzinfo=self._local_tz(),
                )
                return int(dt.timestamp() * 1000)
            except Exception:
                return None

        if re.fullmatch(r'\d{8}T\d{6}Z', raw):
            try:
                dt = datetime.strptime(raw, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except Exception:
                return None

        if re.fullmatch(r'\d{8}T\d{4}Z', raw):
            try:
                dt = datetime.strptime(raw, '%Y%m%dT%H%MZ').replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except Exception:
                return None

        for fmt in ('%Y%m%dT%H%M%S%z', '%Y%m%dT%H%M%z'):
            try:
                dt = datetime.strptime(raw, fmt)
                return int(dt.timestamp() * 1000)
            except Exception:
                pass

        formats = ['%Y%m%dT%H%M%S', '%Y%m%dT%H%M']
        for fmt in formats:
            try:
                base = datetime.strptime(raw, fmt)
                tzinfo = self._resolve_timezone(tzid)
                dt = base.replace(tzinfo=tzinfo)
                return int(dt.timestamp() * 1000)
            except Exception:
                continue
        return None

    def _parse_duration(self, value: str) -> Optional[int]:
        raw = str(value or '').strip().upper()
        if not raw:
            return None
        match = re.fullmatch(
            r'(?P<sign>[+-])?P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?',
            raw,
        )
        if not match:
            return None

        sign = -1 if match.group('sign') == '-' else 1
        weeks = int(match.group('weeks') or 0)
        days = int(match.group('days') or 0)
        hours = int(match.group('hours') or 0)
        minutes = int(match.group('minutes') or 0)
        seconds = int(match.group('seconds') or 0)
        total_seconds = weeks * 7 * 24 * 3600 + days * 24 * 3600 + hours * 3600 + minutes * 60 + seconds
        if total_seconds <= 0:
            return None
        return int(sign * total_seconds * 1000)

    def _parse_rrule(self, rule: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for chunk in str(rule or '').split(';'):
            if '=' not in chunk:
                continue
            key, value = chunk.split('=', 1)
            key = key.strip().upper()
            value = value.strip().upper()
            if key:
                out[key] = value
        return out

    def _parse_byday(self, value: Any) -> List[int]:
        mapping = {
            'MO': 0,
            'TU': 1,
            'WE': 2,
            'TH': 3,
            'FR': 4,
            'SA': 5,
            'SU': 6,
        }
        out: List[int] = []
        for chunk in str(value or '').split(','):
            token = chunk.strip().upper()
            if len(token) >= 2:
                token = token[-2:]
            if token in mapping:
                out.append(mapping[token])
        return out

    def _parse_int_list(self, value: Any) -> List[int]:
        out: List[int] = []
        for chunk in str(value or '').split(','):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                out.append(int(chunk))
            except Exception:
                continue
        return out

    def _positive_int(self, raw: Any, default: int) -> int:
        try:
            value = int(raw)
            return value if value > 0 else default
        except Exception:
            return default

    def _resolve_timezone(self, tzid: Optional[str]):
        if tzid and ZoneInfo is not None:
            try:
                return ZoneInfo(tzid)
            except Exception:
                pass
        return self._local_tz()

    def _local_tz(self):
        return datetime.now().astimezone().tzinfo or timezone.utc

    def _timestamp_to_local_dt(self, ts_ms: int) -> datetime:
        return datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=self._local_tz())

    def _combine_local_date_with_start_time(self, day_value, start_dt: datetime) -> datetime:
        return datetime(
            day_value.year,
            day_value.month,
            day_value.day,
            start_dt.hour,
            start_dt.minute,
            start_dt.second,
            start_dt.microsecond,
            tzinfo=start_dt.tzinfo,
        )

    def _make_local_datetime(
        self,
        year: int,
        month: int,
        day: int,
        template: datetime,
    ) -> Optional[datetime]:
        try:
            return datetime(
                year,
                month,
                day,
                template.hour,
                template.minute,
                template.second,
                template.microsecond,
                tzinfo=template.tzinfo,
            )
        except Exception:
            return None

    def _add_months(self, year: int, month: int, delta: int) -> Tuple[int, int]:
        index = (year * 12 + (month - 1)) + delta
        return index // 12, (index % 12) + 1

    def _is_within_window(self, ts_ms: int, window_start_ms: int, window_end_ms: int) -> bool:
        return window_start_ms <= int(ts_ms) <= window_end_ms

    def _normalize_categories(self, values: Iterable[str]) -> List[str]:
        out: List[str] = []
        seen: Set[str] = set()
        for value in values:
            text = str(value or '').strip()
            if not text:
                continue
            normalized = re.sub(r'\s+', '_', text.lower(), flags=re.UNICODE)
            normalized = re.sub(r'[^\w.-]+', '_', normalized, flags=re.UNICODE)
            normalized = re.sub(r'_+', '_', normalized).strip('_.-')
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out[:16]

    def _global_calendar_name(self, props: Dict[str, List[Tuple[Dict[str, str], str]]]) -> str:
        for key in ('X-WR-CALNAME', 'NAME'):
            value = self._first_text_property(props, key, '').strip()
            if value:
                return self._unescape_ical_text(value)
        return ''

    def _escape_ical_text(self, text: str) -> str:
        value = str(text or '')
        value = value.replace('\\', '\\\\')
        value = value.replace(';', '\\;').replace(',', '\\,')
        value = value.replace('\r\n', '\n').replace('\r', '\n')
        value = value.replace('\n', '\\n')
        return value

    def _unescape_ical_text(self, text: str) -> str:
        value = str(text or '')
        value = value.replace('\\N', '\n').replace('\\n', '\n')
        value = value.replace('\\,', ',').replace('\\;', ';')
        value = value.replace('\\\\', '\\')
        return value

    def _join_ics_lines(self, lines: Sequence[str]) -> str:
        out = [str(line) for line in lines]
        while out and out[-1] == '':
            out.pop()
        return '\r\n'.join(out) + '\r\n'

    def _format_ics_utc(self, ts_ms: int) -> str:
        return datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc).strftime('%Y%m%dT%H%M%SZ')

    def _format_error(self, response: Dict[str, Any]) -> str:
        error = response.get('error')
        if isinstance(error, dict):
            code = str(error.get('code') or '').strip()
            message = str(error.get('message') or '').strip()
            if code and message:
                return f'{code}: {message}'
            return code or message or self._tr('unknown error', 'неизвестная ошибка')
        if error is None:
            return self._tr('unknown error', 'неизвестная ошибка')
        return str(error)

    def _parse_urls_from_payload(self, payload: Dict[str, Any]) -> List[str]:
        raw = payload.get('calendar_urls', payload.get('calendarUrls'))
        if isinstance(raw, list):
            return [str(value).strip() for value in raw if str(value).strip()]
        text = str(raw or '')
        return self._parse_urls_text(text)

    def _parse_urls_text(self, text: str) -> List[str]:
        out: List[str] = []
        for line in str(text or '').splitlines():
            value = line.strip()
            if not value or value.startswith('#'):
                continue
            out.append(value)
        return out

    def _load_settings(self) -> None:
        urls_raw = self.get_property('calendarUrls', [])
        if isinstance(urls_raw, list):
            self._calendar_urls = [str(value).strip() for value in urls_raw if str(value).strip()]
        elif isinstance(urls_raw, str):
            self._calendar_urls = self._parse_urls_text(urls_raw)
        else:
            self._calendar_urls = []

        self._sync_interval_min = self._to_int(
            self.get_property('syncIntervalMin', DEFAULT_SYNC_INTERVAL_MIN),
            DEFAULT_SYNC_INTERVAL_MIN,
            minimum=SYNC_MIN_INTERVAL,
            maximum=SYNC_MAX_INTERVAL,
        )
        self._auto_sync = self._to_bool(self.get_property('autoSync', True), True)
        self._delete_missing = self._to_bool(self.get_property('deleteMissing', True), True)
        self._export_local_files = self._to_bool(self.get_property('exportLocalFiles', True), True)
        self._import_past_days = self._to_int(
            self.get_property('importPastDays', DEFAULT_IMPORT_PAST_DAYS),
            DEFAULT_IMPORT_PAST_DAYS,
            minimum=0,
            maximum=3650,
        )
        self._import_future_days = self._to_int(
            self.get_property('importFutureDays', DEFAULT_IMPORT_FUTURE_DAYS),
            DEFAULT_IMPORT_FUTURE_DAYS,
            minimum=1,
            maximum=3650,
        )
        self._request_timeout_sec = self._to_int(
            self.get_property('requestTimeoutSec', DEFAULT_REQUEST_TIMEOUT_SEC),
            DEFAULT_REQUEST_TIMEOUT_SEC,
            minimum=3,
            maximum=120,
        )
        self._max_instances_per_event = self._to_int(
            self.get_property('maxInstancesPerEvent', DEFAULT_MAX_INSTANCES_PER_EVENT),
            DEFAULT_MAX_INSTANCES_PER_EVENT,
            minimum=50,
            maximum=5000,
        )

    def _save_settings(self) -> None:
        self.set_property('calendarUrls', list(self._calendar_urls))
        self.set_property('syncIntervalMin', int(self._sync_interval_min))
        self.set_property('autoSync', bool(self._auto_sync))
        self.set_property('deleteMissing', bool(self._delete_missing))
        self.set_property('exportLocalFiles', bool(self._export_local_files))
        self.set_property('importPastDays', int(self._import_past_days))
        self.set_property('importFutureDays', int(self._import_future_days))
        self.set_property('requestTimeoutSec', int(self._request_timeout_sec))
        self.set_property('maxInstancesPerEvent', int(self._max_instances_per_event))
        self.save_properties()

    def _register_settings_gui(self) -> None:
        text = self._ui_texts()
        self.setup_options_panel(
            panel_id='icalendar_settings',
            name=text['panel_name'],
            msg_tag='icalendar:update-settings',
            controls=[
                {
                    'id': 'description',
                    'type': 'label',
                    'label': text['description'],
                },
                {
                    'id': 'calendar_urls',
                    'type': 'textarea',
                    'label': text['calendar_urls_label'],
                    'value': '\n'.join(self._calendar_urls),
                },
                {
                    'id': 'sync_interval_min',
                    'type': 'spinner',
                    'label': text['sync_interval_label'],
                    'min': SYNC_MIN_INTERVAL,
                    'max': SYNC_MAX_INTERVAL,
                    'step': 1,
                    'value': int(self._sync_interval_min),
                },
                {
                    'id': 'auto_sync',
                    'type': 'checkbox',
                    'label': text['auto_sync_label'],
                    'value': bool(self._auto_sync),
                },
                {
                    'id': 'delete_missing',
                    'type': 'checkbox',
                    'label': text['delete_missing_label'],
                    'value': bool(self._delete_missing),
                },
                {
                    'id': 'export_local_files',
                    'type': 'checkbox',
                    'label': text['export_local_files_label'],
                    'value': bool(self._export_local_files),
                },
                {
                    'id': 'import_past_days',
                    'type': 'spinner',
                    'label': text['import_past_days_label'],
                    'min': 0,
                    'max': 3650,
                    'step': 1,
                    'value': int(self._import_past_days),
                },
                {
                    'id': 'import_future_days',
                    'type': 'spinner',
                    'label': text['import_future_days_label'],
                    'min': 1,
                    'max': 3650,
                    'step': 1,
                    'value': int(self._import_future_days),
                },
                {
                    'id': 'request_timeout_sec',
                    'type': 'spinner',
                    'label': text['request_timeout_label'],
                    'min': 3,
                    'max': 120,
                    'step': 1,
                    'value': int(self._request_timeout_sec),
                },
                {
                    'id': 'max_instances_per_event',
                    'type': 'spinner',
                    'label': text['max_instances_label'],
                    'min': 50,
                    'max': 5000,
                    'step': 10,
                    'value': int(self._max_instances_per_event),
                },
                {
                    'id': 'sync_hint',
                    'type': 'label',
                    'label': text['sync_hint'],
                },
            ],
        )

    def _schedule_next_sync(self) -> None:
        if not self._auto_sync:
            if self._timer_id >= 0:
                try:
                    self.cancel_timer(self._timer_id)
                except Exception:
                    pass
                self._timer_id = -1
            return

        if self._timer_id >= 0 or self._sync_running:
            return
        delay_ms = int(max(SYNC_MIN_INTERVAL, self._sync_interval_min) * 60 * 1000)
        self._timer_id = self.set_timer_once(delay_ms, self._on_sync_timer)

    def _reschedule_sync_timer(self) -> None:
        if self._timer_id >= 0:
            try:
                self.cancel_timer(self._timer_id)
            except Exception:
                pass
            self._timer_id = -1
        self._schedule_next_sync()

    def _on_sync_timer(self, sender: str, data: Any, tag: str) -> None:
        self._timer_id = -1
        if self._auto_sync:
            self._start_sync(trigger='timer', speak=False)

    def _ui_texts(self) -> Dict[str, str]:
        if self._is_ru_locale():
            return {
                'panel_name': 'iCalendar Интеграция',
                'description': (
                    'Новый движок синхронизации iCalendar через organizer_core API.\n'
                    'Формат источников: одна строка = один календарь (https://, webcal://, file://, локальный путь).'
                ),
                'calendar_urls_label': 'Источники календарей',
                'sync_interval_label': 'Интервал автосинхронизации (мин)',
                'auto_sync_label': 'Включить автосинхронизацию',
                'delete_missing_label': 'Удалять события, исчезнувшие в источнике',
                'export_local_files_label': 'Экспортировать organizer в локальные файлы календарей',
                'import_past_days_label': 'Окно импорта назад (дней)',
                'import_future_days_label': 'Окно импорта вперед (дней)',
                'request_timeout_label': 'Таймаут HTTP-запроса (сек)',
                'max_instances_label': 'Максимум повторов на событие',
                'sync_hint': 'Ручной запуск: команда icalendar:sync-now',
            }
        return {
            'panel_name': 'iCalendar Integration',
            'description': (
                'New iCalendar sync engine backed by organizer_core API.\n'
                'One source per line (https://, webcal://, file://, local path).'
            ),
            'calendar_urls_label': 'Calendar sources',
            'sync_interval_label': 'Auto sync interval (min)',
            'auto_sync_label': 'Enable auto sync',
            'delete_missing_label': 'Delete events missing from source calendars',
            'export_local_files_label': 'Export organizer items into local calendar files',
            'import_past_days_label': 'Import window backward (days)',
            'import_future_days_label': 'Import window forward (days)',
            'request_timeout_label': 'HTTP request timeout (sec)',
            'max_instances_label': 'Max recurrence instances per event',
            'sync_hint': 'Manual sync command: icalendar:sync-now',
        }

    def _as_local_path(self, normalized_url: str) -> Optional[str]:
        value = str(normalized_url or '').strip()
        if not value:
            return None
        if value.startswith('file://'):
            parsed = urllib.parse.urlparse(value)
            path = urllib.parse.unquote(parsed.path or '')
            if not path:
                return None
            return os.path.abspath(path)
        if re.match(r'^https?://', value, flags=re.IGNORECASE):
            return None
        return os.path.abspath(os.path.expanduser(value))

    def _is_ru_locale(self) -> bool:
        return str(self._ui_locale or '').lower().startswith('ru')

    def _tr(self, en: str, ru: str) -> str:
        return ru if self._is_ru_locale() else en

    def _say(self, text: str) -> None:
        self.request_say_direct(str(text or ''))

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _to_int(
        self,
        raw: Any,
        default: int,
        minimum: Optional[int] = None,
        maximum: Optional[int] = None,
    ) -> int:
        try:
            value = int(raw)
        except Exception:
            value = int(default)
        if minimum is not None and value < minimum:
            value = minimum
        if maximum is not None and value > maximum:
            value = maximum
        return value

    def _to_optional_int(self, raw: Any) -> Optional[int]:
        if raw is None:
            return None
        try:
            return int(raw)
        except Exception:
            return None

    def _to_bool(self, raw: Any, default: bool) -> bool:
        if isinstance(raw, bool):
            return raw
        if raw is None:
            return bool(default)
        if isinstance(raw, str):
            text = raw.strip().lower()
            if text in {'1', 'true', 'yes', 'on'}:
                return True
            if text in {'0', 'false', 'no', 'off'}:
                return False
            return bool(default)
        try:
            return bool(raw)
        except Exception:
            return bool(default)

    def _dedupe_list(self, values: Iterable[str]) -> List[str]:
        out: List[str] = []
        seen: Set[str] = set()
        for value in values:
            text = str(value or '').strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out


if __name__ == '__main__':
    run_plugin(ICalendarPlugin)
