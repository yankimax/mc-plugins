#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from minachan_sdk import MinaChanPlugin, run_plugin


class OrganizerUiPlugin(MinaChanPlugin):
    PANEL_ID = 'organizer_ui.panel'
    EDITOR_PANEL_ID = 'organizer_ui.editor.panel'
    WINDOW_ID = 'organizer_ui'
    EDITOR_WINDOW_ID = 'organizer_ui_editor'

    CMD_OPEN = 'organizer-ui:open'
    CMD_TOGGLE = 'organizer-ui:toggle'
    CMD_REFRESH = 'organizer-ui:refresh'
    CMD_EDITOR_OPEN = 'organizer-ui:editor-open'
    CMD_EDITOR_TOGGLE = 'organizer-ui:editor-toggle'

    TAG_APPLY_FILTERS = 'organizer-ui:apply-filters'
    TAG_LOAD_ITEM = 'organizer-ui:item-load'
    TAG_CREATE_ITEM = 'organizer-ui:item-create'
    TAG_UPDATE_ITEM = 'organizer-ui:item-update'
    TAG_DELETE_ITEM = 'organizer-ui:item-delete'
    TAG_RESET_ITEM_NOTIFICATIONS = 'organizer-ui:item-reset-notifications'

    TAG_APPLY_SETTINGS = 'organizer-ui:settings-apply'
    TAG_PROCESS_NOTIFICATIONS = 'organizer-ui:process-notifications'

    TAG_SELECT_DICTIONARY_KIND = 'organizer-ui:dictionary-select-kind'
    TAG_UPSERT_DICTIONARY = 'organizer-ui:dictionary-upsert'
    TAG_DELETE_DICTIONARY = 'organizer-ui:dictionary-delete'

    TAG_GUI_REQUEST_PANELS = 'gui:request-panels'

    EVT_CONFIG_CHANGED = 'organizer-core:config-changed'
    EVT_ITEM_CREATED = 'organizer-core:item-created'
    EVT_ITEM_UPDATED = 'organizer-core:item-updated'
    EVT_ITEM_DELETED = 'organizer-core:item-deleted'
    EVT_ITEM_UPCOMING = 'organizer-core:item-upcoming'
    EVT_ITEM_STARTED = 'organizer-core:item-started'

    _STATE_KEY = 'uiState'
    _SORT_VALUES = {
        'created_asc',
        'created_desc',
        'updated_asc',
        'updated_desc',
        'start_asc',
        'start_desc',
        'due_asc',
        'due_desc',
        'priority_asc',
        'priority_desc',
    }

    def __init__(self) -> None:
        super().__init__()

        self._ui_locale = 'en'
        self._main_panel_registered = False
        self._editor_panel_registered = False

        self._settings: Dict[str, Any] = {
            'tickIntervalSec': 15,
            'defaultUpcomingLeadMs': 900000,
            'notifyViaCore': True,
            'notifyUpcoming': True,
            'notifyStarted': True,
            'notifyPriority': 7000,
            'notifyIntent': 'ALARM',
        }
        self._dictionaries: Dict[str, List[Dict[str, Any]]] = {
            'state': [],
            'priority': [],
            'tag': [],
            'source': [],
        }

        self._items: List[Dict[str, Any]] = []
        self._items_count = 0
        self._items_total = 0
        self._items_limit = 100
        self._items_offset = 0
        self._metrics_items: List[Dict[str, Any]] = []

        self._filters: Dict[str, Any] = {
            'search': '',
            'state': '',
            'priority': '',
            'source': '',
            'tag': '',
            'tagsAny': [],
            'externalUid': '',
            'includeTerminal': False,
            'sort': 'start_asc',
            'limit': 100,
            'hasStart': None,
            'startFromMs': None,
            'startToMs': None,
            'dueFromMs': None,
            'dueToMs': None,
            'hasDue': None,
            'completedOnly': False,
        }
        self._form: Dict[str, Any] = {
            'itemPick': '',
            'itemId': '',
            'title': '',
            'description': '',
            'state': '',
            'priority': '',
            'source': '',
            'externalUid': '',
            'startAt': '',
            'dueAt': '',
            'upcomingLeadMin': '',
            'tags': [],
            'payload': '',
        }

        self._dictionary_editor: Dict[str, Any] = {
            'kind': 'state',
            'id': '',
            'name': '',
            'order': '0',
            'color': '',
            'icon': '',
            'isDefault': False,
            'isSystem': False,
            'terminal': False,
            'force': False,
            'replaceWith': '',
        }

        self._status_text = 'Initializing organizer UI...'
        self._status_level = 'info'

        self._refresh_token = 0
        self._core_request_seq = 0

    def on_init(self) -> None:
        self._load_state()

        self.add_listener(self.CMD_OPEN, self.on_open, listener_id='organizer_ui_open')
        self.add_listener(self.CMD_TOGGLE, self.on_toggle, listener_id='organizer_ui_toggle')
        self.add_listener(self.CMD_REFRESH, self.on_refresh, listener_id='organizer_ui_refresh')
        self.add_listener(self.CMD_EDITOR_OPEN, self.on_editor_open, listener_id='organizer_ui_editor_open')
        self.add_listener(self.CMD_EDITOR_TOGGLE, self.on_editor_toggle, listener_id='organizer_ui_editor_toggle')

        self.add_listener(self.TAG_APPLY_FILTERS, self.on_apply_filters, listener_id='organizer_ui_apply_filters')
        self.add_listener(self.TAG_LOAD_ITEM, self.on_load_item, listener_id='organizer_ui_load_item')
        self.add_listener(self.TAG_CREATE_ITEM, self.on_create_item, listener_id='organizer_ui_create_item')
        self.add_listener(self.TAG_UPDATE_ITEM, self.on_update_item, listener_id='organizer_ui_update_item')
        self.add_listener(self.TAG_DELETE_ITEM, self.on_delete_item, listener_id='organizer_ui_delete_item')
        self.add_listener(
            self.TAG_RESET_ITEM_NOTIFICATIONS,
            self.on_reset_item_notifications,
            listener_id='organizer_ui_reset_item_notifications',
        )

        self.add_listener(self.TAG_APPLY_SETTINGS, self.on_apply_settings, listener_id='organizer_ui_apply_settings')
        self.add_listener(
            self.TAG_PROCESS_NOTIFICATIONS,
            self.on_process_notifications,
            listener_id='organizer_ui_process_notifications',
        )

        self.add_listener(
            self.TAG_SELECT_DICTIONARY_KIND,
            self.on_select_dictionary_kind,
            listener_id='organizer_ui_select_dictionary_kind',
        )
        self.add_listener(
            self.TAG_UPSERT_DICTIONARY,
            self.on_upsert_dictionary,
            listener_id='organizer_ui_upsert_dictionary',
        )
        self.add_listener(
            self.TAG_DELETE_DICTIONARY,
            self.on_delete_dictionary,
            listener_id='organizer_ui_delete_dictionary',
        )

        self.add_listener(self.TAG_GUI_REQUEST_PANELS, self.on_gui_request_panels, listener_id='organizer_ui_request_panels')

        self.add_listener(self.EVT_CONFIG_CHANGED, self.on_core_event, listener_id='organizer_ui_evt_config_changed')
        self.add_listener(self.EVT_ITEM_CREATED, self.on_core_event, listener_id='organizer_ui_evt_item_created')
        self.add_listener(self.EVT_ITEM_UPDATED, self.on_core_event, listener_id='organizer_ui_evt_item_updated')
        self.add_listener(self.EVT_ITEM_DELETED, self.on_core_event, listener_id='organizer_ui_evt_item_deleted')
        self.add_listener(self.EVT_ITEM_UPCOMING, self.on_core_event, listener_id='organizer_ui_evt_item_upcoming')
        self.add_listener(self.EVT_ITEM_STARTED, self.on_core_event, listener_id='organizer_ui_evt_item_started')

        self.add_locale_listener(self._on_locale_changed, default_locale='en')
        self._register_contract()

        self._publish_ui(force_set=True)
        self._refresh(reason='init')

    def on_unload(self) -> None:
        try:
            self.ui_window_delete(self.WINDOW_ID, close=True)
        except Exception:
            pass
        try:
            self.ui_window_delete(self.EDITOR_WINDOW_ID, close=True)
        except Exception:
            pass
        try:
            self.remove_panel(self.PANEL_ID)
        except Exception:
            pass
        try:
            self.remove_panel(self.EDITOR_PANEL_ID)
        except Exception:
            pass

    def on_gui_request_panels(self, sender: str, data: Any, tag: str) -> None:
        self._publish_ui(force_set=True)

    def on_open(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        self._consume_panel_values(payload)

        item_id = self._extract_item_id_from_payload_only(payload)
        if item_id is not None:
            self._open_editor_window(item_id=item_id, clear_new=False)
            return

        self._publish_ui(force_set=False)
        self.ui_window_open(self.WINDOW_ID)

        self._refresh(reason='open-command')

    def on_toggle(self, sender: str, data: Any, tag: str) -> None:
        self._publish_ui(force_set=False)
        self.ui_window_toggle(self.WINDOW_ID)

    def on_refresh(self, sender: str, data: Any, tag: str) -> None:
        self._consume_panel_values(self._as_map(data))
        self._refresh(reason='manual-refresh')

    def on_editor_open(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        self._consume_panel_values(payload)
        item_id = self._extract_item_id_from_payload_only(payload)
        self._open_editor_window(item_id=item_id, clear_new=item_id is None)

    def on_editor_toggle(self, sender: str, data: Any, tag: str) -> None:
        self._publish_ui(force_set=False)
        self.ui_window_toggle(self.EDITOR_WINDOW_ID)

    def _open_editor_window(self, item_id: Optional[int], clear_new: bool) -> None:
        self._publish_ui(force_set=False)
        self.ui_window_open(self.EDITOR_WINDOW_ID)

        if item_id is not None:
            self._load_item_to_form(item_id)
            return

        if clear_new:
            self._clear_form_item_fields()
            self._set_status(
                self._tr('Opened new task card.', 'Открыта карточка новой задачи.'),
                level='info',
            )
            self._save_state()
            self._publish_ui(force_set=False)

    def on_apply_filters(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        self._consume_panel_values(payload, reset_drilldown=bool(payload.get('filter_drilldown') != True))
        self._save_state()
        self._refresh(reason='apply-filters')

    def on_load_item(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        self._consume_panel_values(payload)

        item_id = self._extract_item_id(payload)
        if item_id is None:
            self._set_status(self._tr('Select item id to load.', 'Выберите id задачи для загрузки.'), level='warning')
            self._publish_ui(force_set=False)
            return

        self._load_item_to_form(item_id)

    def on_create_item(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        self._consume_panel_values(payload)

        item_payload = self._build_create_item_payload()
        if item_payload is None:
            self._publish_ui(force_set=False)
            return

        self._request_core(
            'organizer-core:create-item',
            item_payload,
            self._on_create_item_response,
        )

    def on_update_item(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        self._consume_panel_values(payload)

        item_payload = self._build_update_item_payload()
        if item_payload is None:
            self._publish_ui(force_set=False)
            return

        self._request_core(
            'organizer-core:update-item',
            item_payload,
            self._on_update_item_response,
        )

    def on_delete_item(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        self._consume_panel_values(payload)

        item_id = self._extract_item_id(payload)
        if item_id is None:
            self._set_status(self._tr('Select item id to delete.', 'Выберите id задачи для удаления.'), level='warning')
            self._publish_ui(force_set=False)
            return

        self._request_core(
            'organizer-core:delete-item',
            {'id': item_id},
            self._on_delete_item_response,
        )

    def on_reset_item_notifications(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        self._consume_panel_values(payload)

        item_id = self._extract_item_id(payload)
        if item_id is None:
            self._set_status(
                self._tr('Select item id to reset notifications.', 'Выберите id задачи для сброса уведомлений.'),
                level='warning',
            )
            self._publish_ui(force_set=False)
            return

        self._request_core(
            'organizer-core:reset-item-notifications',
            {'id': item_id, 'upcoming': True, 'started': True},
            self._on_reset_item_notifications_response,
        )

    def on_apply_settings(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        self._consume_panel_values(payload)

        settings_payload = self._build_settings_payload(payload)
        if settings_payload is None:
            self._publish_ui(force_set=False)
            return

        self._request_core(
            'organizer-core:update-settings',
            settings_payload,
            self._on_apply_settings_response,
        )

    def on_process_notifications(self, sender: str, data: Any, tag: str) -> None:
        self._consume_panel_values(self._as_map(data))
        self._request_core(
            'organizer-core:process-notifications',
            {},
            self._on_process_notifications_response,
        )

    def on_select_dictionary_kind(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        self._consume_panel_values(payload)
        kind = self._string(payload.get('dict_kind'), self._dictionary_editor.get('kind'))
        if kind in ('state', 'priority', 'tag', 'source'):
            self._dictionary_editor['kind'] = kind
            self._dictionary_editor['replaceWith'] = ''
            self._set_status(
                self._tr(
                    f'Dictionary switched to "{kind}".',
                    f'Справочник переключен на "{kind}".',
                ),
                level='info',
            )
        self._save_state()
        self._publish_ui(force_set=False)

    def on_upsert_dictionary(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        self._consume_panel_values(payload)

        dictionary_payload = self._build_dictionary_upsert_payload()
        if dictionary_payload is None:
            self._publish_ui(force_set=False)
            return

        self._request_core(
            'organizer-core:upsert-dictionary-entry',
            dictionary_payload,
            self._on_upsert_dictionary_response,
        )

    def on_delete_dictionary(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        self._consume_panel_values(payload)

        dictionary_payload = self._build_dictionary_delete_payload()
        if dictionary_payload is None:
            self._publish_ui(force_set=False)
            return

        self._request_core(
            'organizer-core:delete-dictionary-entry',
            dictionary_payload,
            self._on_delete_dictionary_response,
        )

    def on_core_event(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)

        if tag == self.EVT_ITEM_UPCOMING:
            item = self._as_map(payload.get('item'))
            title = self._string(item.get('title'), 'Untitled') or 'Untitled'
            self._set_status(
                self._tr(
                    f'Upcoming: {title}',
                    f'Скоро начнется: {title}',
                ),
                level='info',
            )
            self._publish_ui(force_set=False)
            return

        if tag == self.EVT_ITEM_STARTED:
            item = self._as_map(payload.get('item'))
            title = self._string(item.get('title'), 'Untitled') or 'Untitled'
            self._set_status(
                self._tr(
                    f'Started: {title}',
                    f'Началось: {title}',
                ),
                level='info',
            )
            self._publish_ui(force_set=False)
            return

        if tag in {self.EVT_CONFIG_CHANGED, self.EVT_ITEM_CREATED, self.EVT_ITEM_UPDATED, self.EVT_ITEM_DELETED}:
            self._refresh(reason=f'core-event:{tag}')

    def _on_locale_changed(self, locale: str, chain: List[str]) -> None:
        self._ui_locale = locale or 'en'
        self._publish_ui(force_set=self._main_panel_registered or self._editor_panel_registered)

    def _register_contract(self) -> None:
        self.register_command(
            self.CMD_OPEN,
            {
                'en': 'Open organizer UI window',
                'ru': 'Открыть окно organizer UI',
            },
        )
        self.register_command(
            self.CMD_TOGGLE,
            {
                'en': 'Toggle organizer UI window',
                'ru': 'Переключить окно organizer UI',
            },
        )
        self.register_command(
            self.CMD_REFRESH,
            {
                'en': 'Refresh organizer UI data from organizer_core',
                'ru': 'Обновить данные organizer UI из organizer_core',
            },
        )
        self.register_command(
            self.CMD_EDITOR_OPEN,
            {
                'en': 'Open organizer task card (new or by item id)',
                'ru': 'Открыть карточку задачи органайзера (новая или по item id)',
            },
        )
        self.register_command(
            self.CMD_EDITOR_TOGGLE,
            {
                'en': 'Toggle organizer task card window',
                'ru': 'Переключить окно карточки задачи органайзера',
            },
        )
        for rule in (
            {'en': '(open|show) organizer', 'ru': '(открой|покажи) органайзер'},
            {'en': '(open|show) tasks', 'ru': '(открой|покажи) задачи'},
            {'en': 'task list', 'ru': 'список дел'},
            {'en': 'my tasks', 'ru': 'мои дела'},
        ):
            self.register_speech_rule(self.CMD_OPEN, rule)

    def _request_core(
        self,
        command: str,
        payload: Dict[str, Any],
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        self._core_request_seq += 1
        request_id = self._core_request_seq
        responded = {'value': False}

        def _on_response(sender: str, data: Any, tag: str) -> None:
            if responded['value']:
                return
            responded['value'] = True
            if isinstance(data, dict):
                callback(dict(data))
                return
            self.log(
                'organizer_ui core response invalid type '
                f'(req#{request_id}, command={command}, sender={sender}, tag={tag}, type={type(data).__name__})'
            )
            callback(
                {
                    'ok': False,
                    'error': {
                        'code': 'invalid_response',
                        'message': f'Invalid response type for {command}',
                    },
                }
            )

        def _on_complete(sender: str, data: Any, tag: str) -> None:
            if responded['value']:
                return
            responded['value'] = True
            self.log(
                'organizer_ui core request completed without response '
                f'(req#{request_id}, command={command}, sender={sender}, tag={tag}, seq_pending)'
            )
            callback(
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
            self.log(
                'organizer_ui send_message_with_response returned -1 '
                f'(req#{request_id}, command={command}, payloadKeys={sorted(list(payload.keys()))})'
            )
        if seq < 0 and not responded['value']:
            responded['value'] = True
            callback(
                {
                    'ok': False,
                    'error': {
                        'code': 'send_failed',
                        'message': f'Failed to send {command}',
                    },
                }
            )

    def _refresh(self, reason: str) -> None:
        self._refresh_token += 1
        token = self._refresh_token

        self._set_status(
            self._tr(
                f'Refreshing organizer data ({reason})...',
                f'Обновляю данные органайзера ({reason})...',
            ),
            level='info',
        )
        self._publish_ui(force_set=False)

        self._request_core('organizer-core:get-config', {}, lambda response: self._on_refresh_config(token, response))

    def _on_refresh_config(self, token: int, response: Dict[str, Any]) -> None:
        if token != self._refresh_token:
            return

        if response.get('ok'):
            self._settings = self._as_map(response.get('settings'))
            dictionaries = self._as_map(response.get('dictionaries'))
            normalized: Dict[str, List[Dict[str, Any]]] = {}
            for kind in ('state', 'priority', 'tag', 'source'):
                raw_list = dictionaries.get(kind)
                out: List[Dict[str, Any]] = []
                if isinstance(raw_list, list):
                    for item in raw_list:
                        if isinstance(item, dict):
                            out.append(dict(item))
                normalized[kind] = out
            self._dictionaries = normalized
            self._ensure_defaults_from_dictionaries()
        else:
            self.log(
                'organizer_ui config load failed '
                f'(token={token}, error={self._format_error(response)})'
            )
            self._set_status(
                self._tr(
                    f'Config load failed: {self._format_error(response)}',
                    f'Ошибка загрузки конфигурации: {self._format_error(response)}',
                ),
                level='error',
            )

        self._metrics_items = []
        self._request_metrics_items(token, offset=0, collected=[])

    def _request_metrics_items(self, token: int, offset: int, collected: List[Dict[str, Any]]) -> None:
        payload = {
            'includeTerminal': True,
            'sort': 'created_desc',
            'limit': 1000,
            'offset': max(0, offset),
        }
        self._request_core(
            'organizer-core:list-items',
            payload,
            lambda response: self._on_refresh_metrics_items(token, offset, collected, response),
        )

    def _on_refresh_metrics_items(
        self,
        token: int,
        offset: int,
        collected: List[Dict[str, Any]],
        response: Dict[str, Any],
    ) -> None:
        if token != self._refresh_token:
            return

        if not response.get('ok'):
            self.log(
                'organizer_ui metrics load failed '
                f'(token={token}, error={self._format_error(response)})'
            )
            self._metrics_items = list(collected)
            self._request_filtered_items(token)
            return

        raw_items = response.get('items')
        page_items: List[Dict[str, Any]] = []
        if isinstance(raw_items, list):
            for item in raw_items:
                if isinstance(item, dict):
                    page_items.append(dict(item))

        collected.extend(page_items)
        count = self._int(response.get('count'), len(page_items))
        total = self._int(response.get('total'), len(collected))
        next_offset = offset + max(count, len(page_items))

        if page_items and next_offset < total:
            self._request_metrics_items(token, offset=next_offset, collected=collected)
            return

        self._metrics_items = list(collected)
        self._request_filtered_items(token)

    def _request_filtered_items(self, token: int) -> None:
        list_payload = self._build_list_items_payload()
        self._request_core('organizer-core:list-items', list_payload, lambda response: self._on_refresh_items(token, response))

    def _on_refresh_items(self, token: int, response: Dict[str, Any]) -> None:
        if token != self._refresh_token:
            return

        if response.get('ok'):
            raw_items = response.get('items')
            items: List[Dict[str, Any]] = []
            if isinstance(raw_items, list):
                for item in raw_items:
                    if isinstance(item, dict):
                        items.append(dict(item))
            self._items = items
            self._items_count = self._int(response.get('count'), len(items))
            self._items_total = self._int(response.get('total'), len(items))
            self._items_limit = self._int(response.get('limit'), self._filters.get('limit'))
            self._items_offset = self._int(response.get('offset'), 0)
            self._set_status(
                self._tr(
                    f'Loaded {self._items_count} item(s), total {self._items_total}.',
                    f'Загружено задач: {self._items_count}, всего: {self._items_total}.',
                ),
                level='success',
            )
        else:
            self.log(
                'organizer_ui items load failed '
                f'(token={token}, error={self._format_error(response)})'
            )
            self._set_status(
                self._tr(
                    f'Items load failed: {self._format_error(response)}',
                    f'Ошибка загрузки задач: {self._format_error(response)}',
                ),
                level='error',
            )

        self._publish_ui(force_set=False)

    def _load_item_to_form(self, item_id: int) -> None:
        self._request_core('organizer-core:get-item', {'id': item_id}, self._on_load_item_response)

    def _on_load_item_response(self, response: Dict[str, Any]) -> None:
        if not response.get('ok'):
            self._set_status(
                self._tr(
                    f'Load item failed: {self._format_error(response)}',
                    f'Ошибка загрузки задачи: {self._format_error(response)}',
                ),
                level='error',
            )
            self._publish_ui(force_set=False)
            return

        item = self._as_map(response.get('item'))
        if not item:
            self._set_status(self._tr('Item payload is empty.', 'Пустой payload задачи.'), level='error')
            self._publish_ui(force_set=False)
            return

        self._apply_item_to_form(item)
        self._set_status(
            self._tr(
                f'Loaded item #{self._form.get("itemId")}.',
                f'Задача #{self._form.get("itemId")} загружена.',
            ),
            level='success',
        )
        self._save_state()
        self._publish_ui(force_set=False)

    def _on_create_item_response(self, response: Dict[str, Any]) -> None:
        if not response.get('ok'):
            self._set_status(
                self._tr(
                    f'Create failed: {self._format_error(response)}',
                    f'Создание не удалось: {self._format_error(response)}',
                ),
                level='error',
            )
            self._publish_ui(force_set=False)
            return

        item = self._as_map(response.get('item'))
        self._apply_item_to_form(item)
        self._set_status(
            self._tr(
                f'Item #{self._form.get("itemId")} created.',
                f'Задача #{self._form.get("itemId")} создана.',
            ),
            level='success',
        )
        self._save_state()
        self._refresh(reason='create-item')

    def _on_update_item_response(self, response: Dict[str, Any]) -> None:
        if not response.get('ok'):
            self._set_status(
                self._tr(
                    f'Update failed: {self._format_error(response)}',
                    f'Обновление не удалось: {self._format_error(response)}',
                ),
                level='error',
            )
            self._publish_ui(force_set=False)
            return

        item = self._as_map(response.get('item'))
        self._apply_item_to_form(item)
        self._set_status(
            self._tr(
                f'Item #{self._form.get("itemId")} updated.',
                f'Задача #{self._form.get("itemId")} обновлена.',
            ),
            level='success',
        )
        self._save_state()
        self._refresh(reason='update-item')

    def _on_delete_item_response(self, response: Dict[str, Any]) -> None:
        if not response.get('ok'):
            self._set_status(
                self._tr(
                    f'Delete failed: {self._format_error(response)}',
                    f'Удаление не удалось: {self._format_error(response)}',
                ),
                level='error',
            )
            self._publish_ui(force_set=False)
            return

        deleted = self._as_map(response.get('item'))
        deleted_id = self._int_or_none(deleted.get('id'))
        self._set_status(
            self._tr(
                f'Item #{deleted_id or "?"} deleted.',
                f'Задача #{deleted_id or "?"} удалена.',
            ),
            level='success',
        )

        if deleted_id is not None and self._int_or_none(self._form.get('itemId')) == deleted_id:
            self._clear_form_item_fields()

        self._save_state()
        self._refresh(reason='delete-item')

    def _on_reset_item_notifications_response(self, response: Dict[str, Any]) -> None:
        if not response.get('ok'):
            self._set_status(
                self._tr(
                    f'Reset failed: {self._format_error(response)}',
                    f'Сброс не удался: {self._format_error(response)}',
                ),
                level='error',
            )
            self._publish_ui(force_set=False)
            return

        item = self._as_map(response.get('item'))
        self._apply_item_to_form(item)
        self._set_status(
            self._tr(
                'Notification flags reset.',
                'Флаги уведомлений сброшены.',
            ),
            level='success',
        )
        self._save_state()
        self._refresh(reason='reset-notifications')

    def _on_apply_settings_response(self, response: Dict[str, Any]) -> None:
        if not response.get('ok'):
            self._set_status(
                self._tr(
                    f'Settings update failed: {self._format_error(response)}',
                    f'Не удалось обновить настройки: {self._format_error(response)}',
                ),
                level='error',
            )
            self._publish_ui(force_set=False)
            return

        settings = self._as_map(response.get('settings'))
        if settings:
            self._settings = settings
        self._set_status(
            self._tr('Core settings updated.', 'Настройки ядра обновлены.'),
            level='success',
        )
        self._refresh(reason='apply-settings')

    def _on_process_notifications_response(self, response: Dict[str, Any]) -> None:
        if not response.get('ok'):
            self._set_status(
                self._tr(
                    f'Processing failed: {self._format_error(response)}',
                    f'Запуск цикла уведомлений не удался: {self._format_error(response)}',
                ),
                level='error',
            )
            self._publish_ui(force_set=False)
            return

        processed = self._as_map(response.get('processed'))
        upcoming = self._int(processed.get('upcoming'), 0)
        started = self._int(processed.get('started'), 0)
        self._set_status(
            self._tr(
                f'Processed notifications: upcoming={upcoming}, started={started}.',
                f'Обработано уведомлений: upcoming={upcoming}, started={started}.',
            ),
            level='success',
        )
        self._refresh(reason='process-notifications')

    def _on_upsert_dictionary_response(self, response: Dict[str, Any]) -> None:
        if not response.get('ok'):
            self._set_status(
                self._tr(
                    f'Dictionary save failed: {self._format_error(response)}',
                    f'Сохранение записи словаря не удалось: {self._format_error(response)}',
                ),
                level='error',
            )
            self._publish_ui(force_set=False)
            return

        entry = self._as_map(response.get('entry'))
        entry_id = self._string(entry.get('id'))
        self._dictionary_editor['id'] = entry_id
        self._dictionary_editor['name'] = self._string(entry.get('name'))
        self._dictionary_editor['replaceWith'] = ''
        self._set_status(
            self._tr(
                f'Dictionary entry "{entry_id}" saved.',
                f'Запись словаря "{entry_id}" сохранена.',
            ),
            level='success',
        )
        self._save_state()
        self._refresh(reason='upsert-dictionary')

    def _on_delete_dictionary_response(self, response: Dict[str, Any]) -> None:
        if not response.get('ok'):
            self._set_status(
                self._tr(
                    f'Dictionary delete failed: {self._format_error(response)}',
                    f'Удаление записи словаря не удалось: {self._format_error(response)}',
                ),
                level='error',
            )
            self._publish_ui(force_set=False)
            return

        deleted_id = self._string(response.get('deletedId'))
        self._dictionary_editor['id'] = ''
        self._dictionary_editor['name'] = ''
        self._dictionary_editor['replaceWith'] = ''
        self._set_status(
            self._tr(
                f'Dictionary entry "{deleted_id}" deleted.',
                f'Запись словаря "{deleted_id}" удалена.',
            ),
            level='success',
        )
        self._save_state()
        self._refresh(reason='delete-dictionary')

    def _publish_ui(self, force_set: bool) -> None:
        main_panel_name = self._tr('Organizer', 'Органайзер')
        main_controls = self._build_main_controls()
        editor_panel_name = self._tr('Task Card', 'Карточка задачи')
        editor_controls = self._build_editor_controls()

        if force_set or not self._main_panel_registered:
            self.set_panel(
                panel_id=self.PANEL_ID,
                name=main_panel_name,
                msg_tag='',
                controls=main_controls,
                panel_type='window',
                extra=self._panel_extra(),
            )
            self._main_panel_registered = True
        else:
            self.update_panel(
                panel_id=self.PANEL_ID,
                name=main_panel_name,
                msg_tag='',
                controls=main_controls,
                panel_type='window',
                extra=self._panel_extra(),
            )

        if force_set or not self._editor_panel_registered:
            self.set_panel(
                panel_id=self.EDITOR_PANEL_ID,
                name=editor_panel_name,
                msg_tag='',
                controls=editor_controls,
                panel_type='window',
                extra=self._editor_panel_extra(),
            )
            self._editor_panel_registered = True
        else:
            self.update_panel(
                panel_id=self.EDITOR_PANEL_ID,
                name=editor_panel_name,
                msg_tag='',
                controls=editor_controls,
                panel_type='window',
                extra=self._editor_panel_extra(),
            )

        self.ui_window_create(
            window=self.WINDOW_ID,
            panel_id=self.PANEL_ID,
            title=self._tr('Organizer', 'Органайзер'),
            geometry_kind='organizer_ui',
            open_on_create=False,
            width=1040,
            height=860,
        )
        self.ui_window_create(
            window=self.EDITOR_WINDOW_ID,
            panel_id=self.EDITOR_PANEL_ID,
            title=self._tr('Task Card', 'Карточка задачи'),
            geometry_kind='organizer_ui_editor',
            open_on_create=False,
            width=780,
            height=860,
        )

    def _panel_extra(self) -> Dict[str, Any]:
        return {
            'scope': 'window',
            'sections': [
                {
                    'id': 'overview',
                    'label': self._tr('Overview', 'Обзор'),
                    'hint': '',
                    'icon': 'dashboard',
                    'order': 10,
                    'columns': 4,
                    'minTileWidth': 200,
                    'compact': False,
                },
                {
                    'id': 'tasks',
                    'label': self._tr('Tasks', 'Задачи'),
                    'hint': '',
                    'icon': 'list',
                    'order': 20,
                    'columns': 3,
                    'minTileWidth': 220,
                },
                {
                    'id': 'settings',
                    'label': self._tr('Core Settings', 'Настройки ядра'),
                    'hint': self._tr(
                        'Notification engine behavior',
                        'Поведение движка уведомлений',
                    ),
                    'icon': 'settings',
                    'order': 30,
                    'columns': 2,
                    'minTileWidth': 240,
                },
                {
                    'id': 'dictionaries',
                    'label': self._tr('Dictionaries', 'Справочники'),
                    'hint': self._tr(
                        'State / priority / tag / source',
                        'Статусы / приоритеты / теги / источники',
                    ),
                    'icon': 'dictionary',
                    'order': 40,
                    'columns': 2,
                    'minTileWidth': 240,
                },
            ],
        }

    def _editor_panel_extra(self) -> Dict[str, Any]:
        return {
            'scope': 'window',
            'sections': [
                {
                    'id': 'editor',
                    'label': self._tr('Task Card', 'Карточка задачи'),
                    'hint': self._tr(
                        'Create, update, delete task',
                        'Создание, обновление, удаление задачи',
                    ),
                    'icon': 'edit',
                    'order': 10,
                    'columns': 2,
                    'minTileWidth': 240,
                },
            ],
        }

    def _metrics(self) -> Dict[str, int]:
        items = self._metrics_items if self._metrics_items else self._items
        terminal_states = {
            self._string(entry.get('id'))
            for entry in (self._dictionaries.get('state') or [])
            if bool(entry.get('terminal'))
        }

        now = datetime.now()
        now_ms = int(now.timestamp() * 1000)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        # Current week window: Monday 00:00 to next Monday 00:00 (includes Sunday).
        week_start = today_start - timedelta(days=today_start.weekday())
        week_end = week_start + timedelta(days=7)

        all_metrics = self._collect_metrics(items, terminal_states, now_ms, None, None)
        week_metrics = self._collect_metrics(
            items,
            terminal_states,
            now_ms,
            int(week_start.timestamp() * 1000),
            int(week_end.timestamp() * 1000),
        )
        today_metrics = self._collect_metrics(
            items,
            terminal_states,
            now_ms,
            int(today_start.timestamp() * 1000),
            int(today_end.timestamp() * 1000),
        )

        return {
            'all_total': all_metrics['total'],
            'all_completed': all_metrics['completed'],
            'all_planned': all_metrics['planned'],
            'all_overdue': all_metrics['overdue'],
            'all_no_due': all_metrics['no_due'],
            'week_total': week_metrics['total'],
            'week_completed': week_metrics['completed'],
            'week_planned': week_metrics['planned'],
            'week_overdue': week_metrics['overdue'],
            'week_no_due': week_metrics['no_due'],
            'today_total': today_metrics['total'],
            'today_completed': today_metrics['completed'],
            'today_planned': today_metrics['planned'],
            'today_overdue': today_metrics['overdue'],
            'today_no_due': today_metrics['no_due'],
        }

    def _collect_metrics(
        self,
        items: List[Dict[str, Any]],
        terminal_states: set,
        now_ms: int,
        start_ms: Optional[int],
        end_ms: Optional[int],
    ) -> Dict[str, int]:
        total = 0
        completed = 0
        planned = 0
        overdue = 0
        no_due = 0

        for item in items:
            reference_ts = self._metric_reference_timestamp(item)
            if start_ms is not None and (reference_ts is None or reference_ts < start_ms):
                continue
            if end_ms is not None and (reference_ts is None or reference_ts >= end_ms):
                continue

            state = self._string(item.get('state'))
            due_at = self._int_or_none(item.get('dueAtMs'))
            is_completed = bool(state) and state in terminal_states
            is_overdue = due_at is not None and due_at < now_ms and not is_completed
            is_planned = not is_completed

            total += 1
            if is_completed:
                completed += 1
            if is_planned:
                planned += 1
            if is_overdue:
                overdue += 1
            if due_at is None:
                no_due += 1

        return {
            'total': total,
            'completed': completed,
            'planned': planned,
            'overdue': overdue,
            'no_due': no_due,
        }

    def _metric_reference_timestamp(self, item: Dict[str, Any]) -> Optional[int]:
        for key in ('dueAtMs', 'startAtMs', 'createdAtMs'):
            value = self._int_or_none(item.get(key))
            if value is None:
                continue
            if abs(value) < 1_000_000_000_000:
                value *= 1000
            return value
        return None

    def _completion_rate(self, completed: int, total: int) -> int:
        if total <= 0:
            return 0
        value = int(round((completed * 100.0) / total))
        return max(0, min(100, value))

    def _terminal_state_ids(self) -> List[str]:
        return [
            self._string(entry.get('id'))
            for entry in (self._dictionaries.get('state') or [])
            if bool(entry.get('terminal')) and self._string(entry.get('id'))
        ]

    def _base_overview_drilldown_preset(self) -> Dict[str, Any]:
        return {
            'filter_search': '',
            'filter_state': '',
            'filter_priority': '',
            'filter_source': '',
            'filter_tag': '',
            'filter_include_terminal': False,
            'filter_sort': 'due_asc',
            'filter_due_from_ms': None,
            'filter_due_to_ms': None,
            'filter_has_due': None,
            'filter_completed_only': False,
            'filter_drilldown': True,
        }

    def _metric_cards(self, metrics: Dict[str, int]) -> List[Dict[str, Any]]:
        all_total = self._int(metrics.get('all_total'), 0)
        all_completed = self._int(metrics.get('all_completed'), 0)
        completion_rate = self._completion_rate(all_completed, all_total)
        completion_hint = self._tr(
            f'Completed {all_completed} of {all_total}',
            f'Выполнено {all_completed} из {all_total}',
        )
        now_ms = int(datetime.now().timestamp() * 1000)
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        week_start = today_start - timedelta(days=today_start.weekday())
        week_end = week_start + timedelta(days=7)

        today_from_ms = int(today_start.timestamp() * 1000)
        today_to_ms = int(today_end.timestamp() * 1000) - 1
        week_from_ms = int(week_start.timestamp() * 1000)
        week_to_ms = int(week_end.timestamp() * 1000) - 1

        drilldown_base = self._base_overview_drilldown_preset()

        def preset(overrides: Dict[str, Any]) -> Dict[str, Any]:
            out = dict(drilldown_base)
            out.update(overrides)
            return out

        return [
            {
                'id': 'btn_overview_open_editor',
                'type': 'button',
                'label': self._tr('New Task', 'Новая задача'),
                'msgTag': self.CMD_EDITOR_OPEN,
                'section': 'overview',
            },
            {
                'id': 'btn_overview_refresh',
                'type': 'button',
                'label': self._tr('Refresh Data', 'Обновить данные'),
                'msgTag': self.CMD_REFRESH,
                'section': 'overview',
            },
            {
                'id': 'overview_today_header',
                'type': 'label',
                'label': self._tr('Today', 'Сегодня'),
                'section': 'overview',
            },
            {
                'id': 'metric_today_planned',
                'type': 'metric',
                'label': self._tr('In Progress', 'В работе'),
                'value': metrics.get('today_planned', 0),
                'hint': self._tr('Planned for today', 'Запланировано на сегодня'),
                'icon': 'task',
                'msgTag': self.TAG_APPLY_FILTERS,
                'targetSection': 'tasks',
                'setValues': preset(
                    {
                        'filter_due_from_ms': today_from_ms,
                        'filter_due_to_ms': today_to_ms,
                    }
                ),
            },
            {
                'id': 'metric_today_completed',
                'type': 'metric',
                'label': self._tr('Done', 'Сделано'),
                'value': metrics.get('today_completed', 0),
                'hint': self._tr('Completed today', 'Выполнено сегодня'),
                'icon': 'done',
                'msgTag': self.TAG_APPLY_FILTERS,
                'targetSection': 'tasks',
                'setValues': preset(
                    {
                        'filter_include_terminal': True,
                        'filter_due_from_ms': today_from_ms,
                        'filter_due_to_ms': today_to_ms,
                        'filter_completed_only': True,
                        'filter_sort': 'updated_desc',
                    }
                ),
            },
            {
                'id': 'metric_today_overdue',
                'type': 'metric',
                'label': self._tr('Overdue', 'Просрочено'),
                'value': metrics.get('today_overdue', 0),
                'hint': self._tr('Require attention', 'Требуют внимания'),
                'icon': 'warning',
                'msgTag': self.TAG_APPLY_FILTERS,
                'targetSection': 'tasks',
                'setValues': preset(
                    {
                        'filter_due_from_ms': today_from_ms,
                        'filter_due_to_ms': max(today_from_ms, now_ms - 1),
                    }
                ),
            },
            {
                'id': 'overview_week_header',
                'type': 'label',
                'label': self._tr('Current Week', 'Текущая неделя'),
                'section': 'overview',
            },
            {
                'id': 'metric_week_planned',
                'type': 'metric',
                'label': self._tr('In Progress (Week)', 'В работе (неделя)'),
                'value': metrics.get('week_planned', 0),
                'hint': self._tr('Active this week', 'Активно на этой неделе'),
                'icon': 'timeline',
                'msgTag': self.TAG_APPLY_FILTERS,
                'targetSection': 'tasks',
                'setValues': preset(
                    {
                        'filter_due_from_ms': week_from_ms,
                        'filter_due_to_ms': week_to_ms,
                    }
                ),
            },
            {
                'id': 'metric_week_completed',
                'type': 'metric',
                'label': self._tr('Done (Week)', 'Сделано (неделя)'),
                'value': metrics.get('week_completed', 0),
                'hint': self._tr('Completed this week', 'Выполнено на этой неделе'),
                'icon': 'done',
                'msgTag': self.TAG_APPLY_FILTERS,
                'targetSection': 'tasks',
                'setValues': preset(
                    {
                        'filter_include_terminal': True,
                        'filter_due_from_ms': week_from_ms,
                        'filter_due_to_ms': week_to_ms,
                        'filter_completed_only': True,
                        'filter_sort': 'updated_desc',
                    }
                ),
            },
            {
                'id': 'metric_week_overdue',
                'type': 'metric',
                'label': self._tr('Overdue (Week)', 'Просрочено (неделя)'),
                'value': metrics.get('week_overdue', 0),
                'hint': self._tr('Past due in week view', 'Просрочки в срезе недели'),
                'icon': 'warning',
                'msgTag': self.TAG_APPLY_FILTERS,
                'targetSection': 'tasks',
                'setValues': preset(
                    {
                        'filter_due_from_ms': week_from_ms,
                        'filter_due_to_ms': min(week_to_ms, now_ms - 1),
                    }
                ),
            },
            {
                'id': 'overview_backlog_header',
                'type': 'label',
                'label': self._tr('All Time', 'За всё время'),
                'section': 'overview',
            },
            {
                'id': 'metric_all_planned',
                'type': 'metric',
                'label': self._tr('Active Backlog', 'Активный бэклог'),
                'value': metrics.get('all_planned', 0),
                'hint': self._tr('Open tasks', 'Открытые задачи'),
                'icon': 'list',
                'msgTag': self.TAG_APPLY_FILTERS,
                'targetSection': 'tasks',
                'setValues': preset({}),
            },
            {
                'id': 'metric_all_overdue',
                'type': 'metric',
                'label': self._tr('Overdue Backlog', 'Просрочено в бэклоге'),
                'value': metrics.get('all_overdue', 0),
                'hint': self._tr('Past due and not completed', 'Просрочены и не завершены'),
                'icon': 'warning',
                'msgTag': self.TAG_APPLY_FILTERS,
                'targetSection': 'tasks',
                'setValues': preset(
                    {
                        'filter_due_to_ms': now_ms - 1,
                        'filter_has_due': True,
                    }
                ),
            },
            {
                'id': 'metric_all_no_due',
                'type': 'metric',
                'label': self._tr('No Deadline', 'Без срока'),
                'value': metrics.get('all_no_due', 0),
                'hint': self._tr('Need planning date', 'Нужно назначить срок'),
                'icon': 'clock',
                'msgTag': self.TAG_APPLY_FILTERS,
                'targetSection': 'tasks',
                'setValues': preset(
                    {
                        'filter_has_due': False,
                        'filter_sort': 'updated_desc',
                    }
                ),
            },
            {
                'id': 'metric_all_completion_rate',
                'type': 'metric',
                'label': self._tr('Completion Rate', 'Процент выполнения'),
                'value': f'{completion_rate}%',
                'hint': completion_hint,
                'icon': 'analytics',
                'msgTag': self.TAG_APPLY_FILTERS,
                'targetSection': 'tasks',
                'setValues': preset(
                    {
                        'filter_include_terminal': True,
                        'filter_completed_only': True,
                        'filter_sort': 'updated_desc',
                    }
                ),
            },
        ]

    def _apply_visual_schema(self, controls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for control in controls:
            control_id = self._string(control.get('id'))
            merged = dict(control)
            merged['section'] = self._section_for_control(control_id, merged)

            if control_id == 'btn_overview_open_editor':
                merged['variant'] = 'primary'
                merged['icon'] = 'add'
                merged['prominent'] = True
            elif control_id == 'btn_overview_refresh':
                merged['variant'] = 'outlined'
                merged['icon'] = 'refresh'
            elif control_id == 'btn_apply_filters':
                merged['variant'] = 'primary'
                merged['icon'] = 'filter'
                merged['prominent'] = True
            elif control_id == 'btn_refresh':
                merged['variant'] = 'outlined'
                merged['icon'] = 'refresh'
            elif control_id == 'btn_item_load':
                merged['variant'] = 'outlined'
                merged['icon'] = 'open'
            elif control_id == 'btn_item_create':
                merged['variant'] = 'primary'
                merged['icon'] = 'add'
                merged['prominent'] = True
            elif control_id == 'btn_item_update':
                merged['variant'] = 'secondary'
                merged['icon'] = 'update'
            elif control_id == 'btn_item_delete':
                merged['variant'] = 'danger'
                merged['icon'] = 'delete'
            elif control_id == 'btn_item_reset_notifications':
                merged['variant'] = 'outlined'
                merged['icon'] = 'notification'
            elif control_id == 'btn_apply_settings':
                merged['variant'] = 'primary'
                merged['icon'] = 'save'
                merged['prominent'] = True
            elif control_id == 'btn_process_notifications':
                merged['variant'] = 'outlined'
                merged['icon'] = 'sync'
            elif control_id == 'btn_dictionary_select_kind':
                merged['variant'] = 'outlined'
                merged['icon'] = 'list'
            elif control_id == 'btn_dictionary_upsert':
                merged['variant'] = 'primary'
                merged['icon'] = 'save'
                merged['prominent'] = True
            elif control_id == 'btn_dictionary_delete':
                merged['variant'] = 'danger'
                merged['icon'] = 'delete'

            if control_id in {'items_table', 'dictionary_table', 'item_description', 'item_tags', 'item_payload'}:
                merged['fullWidth'] = True

            if control_id.startswith('metric_'):
                merged['span'] = 1
                merged['minWidth'] = 200

            if control_id in {'btn_overview_open_editor', 'btn_overview_refresh'}:
                merged['span'] = 1
                merged['minWidth'] = 200

            if control_id == 'filter_search':
                merged['span'] = 2
                merged['minWidth'] = 300
            elif control_id in {
                'filter_state',
                'filter_priority',
                'filter_source',
                'filter_tag',
                'filter_limit',
                'filter_has_start',
                'filter_has_due',
                'filter_completed_only',
            }:
                merged['span'] = 1
                merged['minWidth'] = 170
            elif control_id == 'filter_sort':
                merged['span'] = 2
                merged['minWidth'] = 240
            elif control_id == 'filter_include_terminal':
                merged['span'] = 2
            elif control_id == 'filter_external_uid':
                merged['span'] = 2
                merged['minWidth'] = 260
            elif control_id in {'filter_start_from', 'filter_start_to', 'filter_due_from', 'filter_due_to'}:
                merged['span'] = 1
                merged['minWidth'] = 220
            if control_id in {'btn_apply_filters', 'btn_refresh'}:
                merged['span'] = 1
                merged['minWidth'] = 170

            if control_id in {'item_pick', 'item_title', 'item_external_uid'}:
                merged['span'] = 2
            elif control_id in {
                'btn_item_load',
                'item_id',
                'item_state',
                'item_priority',
                'item_source',
                'item_start_at',
                'item_due_at',
                'item_upcoming_lead_min',
                'btn_item_create',
                'btn_item_update',
                'btn_item_delete',
                'btn_item_reset_notifications',
            }:
                merged['span'] = 1
                merged['minWidth'] = 180

            if control_id in {
                'settings_tick_interval_sec',
                'settings_default_upcoming_lead_min',
                'settings_notify_priority',
                'settings_notify_intent',
            }:
                merged['span'] = 1
                merged['minWidth'] = 200
            elif control_id in {
                'settings_notify_via_core',
                'settings_notify_upcoming',
                'settings_notify_started',
            }:
                merged['span'] = 2
            elif control_id in {'btn_apply_settings', 'btn_process_notifications'}:
                merged['span'] = 1
                merged['minWidth'] = 200

            if control_id in {
                'dict_kind',
                'btn_dictionary_select_kind',
                'dict_id',
                'dict_name',
                'dict_order',
                'dict_color',
                'dict_icon',
                'dict_replace_with',
                'btn_dictionary_upsert',
                'btn_dictionary_delete',
            }:
                merged['span'] = 1
                merged['minWidth'] = 200
            elif control_id in {'dict_is_default', 'dict_is_system', 'dict_terminal', 'dict_force'}:
                merged['span'] = 2

            out.append(merged)

        return out

    def _section_for_control(self, control_id: str, control: Dict[str, Any]) -> str:
        explicit = self._string(control.get('section')).lower()
        if explicit:
            return explicit
        if control_id.startswith('overview_') or control_id.startswith('btn_overview_'):
            return 'overview'
        if control_id.startswith('metric_'):
            return 'overview'
        if control_id.startswith('filter_') or control_id in {
            'btn_apply_filters',
            'btn_refresh',
            'items_table',
        }:
            return 'tasks'
        if control_id.startswith('item_') or control_id.startswith('btn_item_'):
            return 'editor'
        if control_id.startswith('settings_') or control_id in {'btn_apply_settings', 'btn_process_notifications'}:
            return 'settings'
        if control_id.startswith('dict_') or control_id.startswith('btn_dictionary_') or control_id == 'dictionary_table':
            return 'dictionaries'
        return 'tasks'

    def _build_controls(self) -> List[Dict[str, Any]]:
        return self._build_main_controls()

    def _build_main_controls(self) -> List[Dict[str, Any]]:
        state_options_any = self._dictionary_options('state', allow_empty=True)
        priority_options_any = self._dictionary_options('priority', allow_empty=True)
        source_options_any = self._dictionary_options('source', allow_empty=True)
        tag_options_any = self._dictionary_options('tag', allow_empty=True)

        dictionary_kind = self._string(self._dictionary_editor.get('kind'))
        dictionary_table_rows = self._dictionary_table_rows(dictionary_kind)
        dictionary_replace_options = self._dictionary_options(dictionary_kind, allow_empty=True)

        items_table_rows = self._items_table_rows()
        items_table_columns = self._visible_items_table_columns()
        metrics = self._metrics()

        controls: List[Dict[str, Any]] = []
        controls.extend(self._metric_cards(metrics))
        controls.extend(
            [
                {
                    'id': 'filter_search',
                    'type': 'text',
                    'label': self._tr('Search', 'Поиск'),
                    'value': self._string(self._filters.get('search')),
                    'placeholder': self._tr('title / description', 'название / описание'),
                },
                {
                    'id': 'filter_state',
                    'type': 'select',
                    'label': self._tr('State', 'Статус'),
                    'value': self._string(self._filters.get('state')),
                    'options': state_options_any,
                },
                {
                    'id': 'filter_priority',
                    'type': 'select',
                    'label': self._tr('Priority', 'Приоритет'),
                    'value': self._string(self._filters.get('priority')),
                    'options': priority_options_any,
                },
                {
                    'id': 'filter_source',
                    'type': 'select',
                    'label': self._tr('Source', 'Источник'),
                    'value': self._string(self._filters.get('source')),
                    'options': source_options_any,
                },
                {
                    'id': 'filter_tag',
                    'type': 'select',
                    'label': self._tr('Tag', 'Тег'),
                    'value': self._string(self._filters.get('tag')),
                    'options': tag_options_any,
                },
                {
                    'id': 'filter_tags_any',
                    'type': 'chips',
                    'label': self._tr('Tags (any)', 'Теги (любой)'),
                    'value': list(self._normalize_chip_values(self._filters.get('tagsAny'))),
                },
                {
                    'id': 'filter_external_uid',
                    'type': 'text',
                    'label': self._tr('External UID', 'Внешний UID'),
                    'value': self._string(self._filters.get('externalUid')),
                },
                {
                    'id': 'filter_has_start',
                    'type': 'select',
                    'label': self._tr('Has start time', 'Есть время старта'),
                    'value': '' if self._nullable_bool(self._filters.get('hasStart')) is None else (
                        'true' if self._nullable_bool(self._filters.get('hasStart')) else 'false'
                    ),
                    'options': [
                        {'value': '', 'label': self._tr('Any', 'Любой')},
                        {'value': 'true', 'label': self._tr('Yes', 'Да')},
                        {'value': 'false', 'label': self._tr('No', 'Нет')},
                    ],
                },
                {
                    'id': 'filter_start_from',
                    'type': 'datetime',
                    'label': self._tr('Start from', 'Старт от'),
                    'value': self._datetime_value(self._filters.get('startFromMs')),
                },
                {
                    'id': 'filter_start_to',
                    'type': 'datetime',
                    'label': self._tr('Start to', 'Старт до'),
                    'value': self._datetime_value(self._filters.get('startToMs')),
                },
                {
                    'id': 'filter_has_due',
                    'type': 'select',
                    'label': self._tr('Has due date', 'Есть срок'),
                    'value': '' if self._nullable_bool(self._filters.get('hasDue')) is None else (
                        'true' if self._nullable_bool(self._filters.get('hasDue')) else 'false'
                    ),
                    'options': [
                        {'value': '', 'label': self._tr('Any', 'Любой')},
                        {'value': 'true', 'label': self._tr('Yes', 'Да')},
                        {'value': 'false', 'label': self._tr('No', 'Нет')},
                    ],
                },
                {
                    'id': 'filter_due_from',
                    'type': 'datetime',
                    'label': self._tr('Due from', 'Срок от'),
                    'value': self._datetime_value(self._filters.get('dueFromMs')),
                },
                {
                    'id': 'filter_due_to',
                    'type': 'datetime',
                    'label': self._tr('Due to', 'Срок до'),
                    'value': self._datetime_value(self._filters.get('dueToMs')),
                },
                {
                    'id': 'filter_completed_only',
                    'type': 'checkbox',
                    'label': self._tr('Completed only', 'Только завершенные'),
                    'value': bool(self._filters.get('completedOnly')),
                },
                {
                    'id': 'filter_include_terminal',
                    'type': 'checkbox',
                    'label': self._tr('Include terminal states', 'Показывать терминальные статусы'),
                    'value': bool(self._filters.get('includeTerminal')),
                },
                {
                    'id': 'filter_sort',
                    'type': 'select',
                    'label': self._tr('Sort', 'Сортировка'),
                    'value': self._string(self._filters.get('sort'), 'start_asc'),
                    'options': [
                        {'value': 'start_asc', 'label': self._tr('Start time ↑', 'Время старта ↑')},
                        {'value': 'start_desc', 'label': self._tr('Start time ↓', 'Время старта ↓')},
                        {'value': 'due_asc', 'label': self._tr('Due time ↑', 'Срок ↑')},
                        {'value': 'due_desc', 'label': self._tr('Due time ↓', 'Срок ↓')},
                        {'value': 'updated_desc', 'label': self._tr('Updated ↓', 'Обновление ↓')},
                        {'value': 'created_desc', 'label': self._tr('Created ↓', 'Создание ↓')},
                        {'value': 'priority_desc', 'label': self._tr('Priority ↓', 'Приоритет ↓')},
                    ],
                },
                {
                    'id': 'filter_limit',
                    'type': 'number',
                    'label': self._tr('Limit', 'Лимит'),
                    'value': self._int(self._filters.get('limit'), 100),
                    'min': 1,
                    'max': 500,
                    'step': 1,
                },
                {
                    'id': 'btn_apply_filters',
                    'type': 'button',
                    'label': self._tr('Apply Filters', 'Применить фильтры'),
                    'msgTag': self.TAG_APPLY_FILTERS,
                },
                {
                    'id': 'btn_refresh',
                    'type': 'button',
                    'label': self._tr('Refresh', 'Обновить'),
                    'msgTag': self.CMD_REFRESH,
                },
            ]
        )

        controls.extend(
            [
                {
                    'id': 'items_table',
                    'type': 'table',
                    'label': self._tr(
                        f'Current items ({self._items_count}/{self._items_total})',
                        f'Текущие задачи ({self._items_count}/{self._items_total})',
                    ),
                    'value': items_table_rows,
                    'readOnly': True,
                    'columns': items_table_columns,
                    'tableTools': ['columns', 'copy', 'csv'],
                    'csvFilePrefix': 'organizer_tasks',
                },
                {
                    'id': 'settings_tick_interval_sec',
                    'type': 'number',
                    'label': self._tr('Tick interval (sec)', 'Интервал тика (сек)'),
                    'value': self._int(self._settings.get('tickIntervalSec'), 15),
                    'min': 1,
                    'max': 3600,
                    'step': 1,
                },
                {
                    'id': 'settings_default_upcoming_lead_min',
                    'type': 'number',
                    'label': self._tr('Default upcoming lead (min)', 'Интервал upcoming по умолчанию (мин)'),
                    'value': int(self._int(self._settings.get('defaultUpcomingLeadMs'), 900000) / 60000),
                    'min': 0,
                    'max': 43200,
                    'step': 1,
                },
                {
                    'id': 'settings_notify_via_core',
                    'type': 'checkbox',
                    'label': self._tr('Emit core notifications', 'Включить core-уведомления'),
                    'value': bool(self._settings.get('notifyViaCore', True)),
                },
                {
                    'id': 'settings_notify_upcoming',
                    'type': 'checkbox',
                    'label': self._tr('Notify upcoming', 'Уведомлять об upcoming'),
                    'value': bool(self._settings.get('notifyUpcoming', True)),
                },
                {
                    'id': 'settings_notify_started',
                    'type': 'checkbox',
                    'label': self._tr('Notify started', 'Уведомлять о старте'),
                    'value': bool(self._settings.get('notifyStarted', True)),
                },
                {
                    'id': 'settings_notify_priority',
                    'type': 'number',
                    'label': self._tr('Notify priority', 'Приоритет уведомления'),
                    'value': self._int(self._settings.get('notifyPriority'), 7000),
                    'min': 1,
                    'max': 100000,
                    'step': 1,
                },
                {
                    'id': 'settings_notify_intent',
                    'type': 'text',
                    'label': self._tr('Notify intent', 'Интент уведомления'),
                    'value': self._string(self._settings.get('notifyIntent'), 'ALARM'),
                },
                {
                    'id': 'btn_apply_settings',
                    'type': 'button',
                    'label': self._tr('Apply Core Settings', 'Применить настройки ядра'),
                    'msgTag': self.TAG_APPLY_SETTINGS,
                },
                {
                    'id': 'btn_process_notifications',
                    'type': 'button',
                    'label': self._tr('Process Notifications Now', 'Запустить цикл уведомлений сейчас'),
                    'msgTag': self.TAG_PROCESS_NOTIFICATIONS,
                },
                {
                    'id': 'dict_kind',
                    'type': 'select',
                    'label': self._tr('Dictionary kind', 'Тип словаря'),
                    'value': dictionary_kind,
                    'options': [
                        {'value': 'state', 'label': 'state'},
                        {'value': 'priority', 'label': 'priority'},
                        {'value': 'tag', 'label': 'tag'},
                        {'value': 'source', 'label': 'source'},
                    ],
                },
                {
                    'id': 'btn_dictionary_select_kind',
                    'type': 'button',
                    'label': self._tr('Show Selected Dictionary', 'Показать выбранный словарь'),
                    'msgTag': self.TAG_SELECT_DICTIONARY_KIND,
                },
                {
                    'id': 'dictionary_table',
                    'type': 'table',
                    'label': self._tr('Dictionary entries', 'Записи словаря'),
                    'value': dictionary_table_rows,
                    'readOnly': True,
                    'columns': [
                        {'id': 'id', 'label': 'id', 'type': 'text', 'readOnly': True},
                        {'id': 'name', 'label': 'name', 'type': 'text', 'readOnly': True},
                        {'id': 'order', 'label': 'order', 'type': 'number', 'readOnly': True},
                        {'id': 'color', 'label': 'color', 'type': 'text', 'readOnly': True},
                        {'id': 'icon', 'label': 'icon', 'type': 'text', 'readOnly': True},
                        {'id': 'isDefault', 'label': 'default', 'type': 'checkbox', 'readOnly': True},
                        {'id': 'isSystem', 'label': 'system', 'type': 'checkbox', 'readOnly': True},
                        {'id': 'terminal', 'label': 'terminal', 'type': 'checkbox', 'readOnly': True},
                        {'id': 'meta', 'label': 'meta', 'type': 'text', 'readOnly': True},
                    ],
                },
                {
                    'id': 'dict_id',
                    'type': 'text',
                    'label': 'id',
                    'value': self._string(self._dictionary_editor.get('id')),
                },
                {
                    'id': 'dict_name',
                    'type': 'text',
                    'label': 'name',
                    'value': self._string(self._dictionary_editor.get('name')),
                },
                {
                    'id': 'dict_order',
                    'type': 'number',
                    'label': 'order',
                    'value': self._string(self._dictionary_editor.get('order')),
                    'step': 1,
                },
                {
                    'id': 'dict_color',
                    'type': 'text',
                    'label': 'color',
                    'value': self._string(self._dictionary_editor.get('color')),
                    'placeholder': '#RRGGBB',
                },
                {
                    'id': 'dict_icon',
                    'type': 'text',
                    'label': 'icon',
                    'value': self._string(self._dictionary_editor.get('icon')),
                },
                {
                    'id': 'dict_is_default',
                    'type': 'checkbox',
                    'label': self._tr('default entry', 'запись по умолчанию'),
                    'value': bool(self._dictionary_editor.get('isDefault')),
                },
                {
                    'id': 'dict_is_system',
                    'type': 'checkbox',
                    'label': self._tr('system entry', 'системная запись'),
                    'value': bool(self._dictionary_editor.get('isSystem')),
                },
                {
                    'id': 'dict_terminal',
                    'type': 'checkbox',
                    'label': self._tr('terminal state', 'терминальный статус'),
                    'value': bool(self._dictionary_editor.get('terminal')),
                    'disabled': dictionary_kind != 'state',
                },
                {
                    'id': 'dict_force',
                    'type': 'checkbox',
                    'label': self._tr('force delete', 'принудительное удаление'),
                    'value': bool(self._dictionary_editor.get('force')),
                },
                {
                    'id': 'dict_replace_with',
                    'type': 'select',
                    'label': self._tr('replace with', 'заменить на'),
                    'value': self._string(self._dictionary_editor.get('replaceWith')),
                    'options': dictionary_replace_options,
                    'disabled': dictionary_kind == 'tag',
                },
                {
                    'id': 'btn_dictionary_upsert',
                    'type': 'button',
                    'label': self._tr('Save Dictionary Entry', 'Сохранить запись словаря'),
                    'msgTag': self.TAG_UPSERT_DICTIONARY,
                },
                {
                    'id': 'btn_dictionary_delete',
                    'type': 'button',
                    'label': self._tr('Delete Dictionary Entry', 'Удалить запись словаря'),
                    'msgTag': self.TAG_DELETE_DICTIONARY,
                },
            ]
        )

        return self._apply_visual_schema(controls)

    def _build_editor_controls(self) -> List[Dict[str, Any]]:
        state_options = self._dictionary_options('state', allow_empty=False)
        priority_options = self._dictionary_options('priority', allow_empty=False)
        source_options = self._dictionary_options('source', allow_empty=False)
        item_options = self._item_options()

        controls: List[Dict[str, Any]] = [
            {
                'id': 'item_pick',
                'type': 'select',
                'label': self._tr('Pick item', 'Выбор задачи'),
                'value': self._string(self._form.get('itemPick')),
                'options': item_options,
            },
            {
                'id': 'btn_item_load',
                'type': 'button',
                'label': self._tr('Load Item', 'Загрузить задачу'),
                'msgTag': self.TAG_LOAD_ITEM,
            },
            {
                'id': 'item_id',
                'type': 'number',
                'label': 'ID',
                'value': self._string(self._form.get('itemId')),
                'min': 1,
                'step': 1,
            },
            {
                'id': 'item_title',
                'type': 'text',
                'label': self._tr('Title', 'Название'),
                'value': self._string(self._form.get('title')),
                'placeholder': self._tr('Required for create/update', 'Обязательное поле для create/update'),
            },
            {
                'id': 'item_description',
                'type': 'textarea',
                'label': self._tr('Description', 'Описание'),
                'value': self._string(self._form.get('description')),
                'rows': 4,
            },
            {
                'id': 'item_state',
                'type': 'select',
                'label': self._tr('State', 'Статус'),
                'value': self._string(self._form.get('state')),
                'options': state_options,
            },
            {
                'id': 'item_priority',
                'type': 'select',
                'label': self._tr('Priority', 'Приоритет'),
                'value': self._string(self._form.get('priority')),
                'options': priority_options,
            },
            {
                'id': 'item_source',
                'type': 'select',
                'label': self._tr('Source', 'Источник'),
                'value': self._string(self._form.get('source')),
                'options': source_options,
            },
            {
                'id': 'item_external_uid',
                'type': 'text',
                'label': self._tr('External UID', 'Внешний UID'),
                'value': self._string(self._form.get('externalUid')),
            },
            {
                'id': 'item_start_at',
                'type': 'datetime',
                'label': self._tr('Start At', 'Время старта'),
                'value': self._string(self._form.get('startAt')),
            },
            {
                'id': 'item_due_at',
                'type': 'datetime',
                'label': self._tr('Due At', 'Срок'),
                'value': self._string(self._form.get('dueAt')),
            },
            {
                'id': 'item_upcoming_lead_min',
                'type': 'number',
                'label': self._tr('Upcoming lead (minutes)', 'Интервал upcoming (минуты)'),
                'value': self._string(self._form.get('upcomingLeadMin')),
                'min': 0,
                'max': 43200,
                'step': 1,
            },
            {
                'id': 'item_tags',
                'type': 'chips',
                'label': self._tr('Tags', 'Теги'),
                'value': list(self._normalize_chip_values(self._form.get('tags'))),
                'placeholder': self._tr('Add tag id and press Enter', 'Введите id тега и нажмите Enter'),
            },
            {
                'id': 'item_payload',
                'type': 'textarea',
                'label': self._tr('Payload JSON', 'Payload JSON'),
                'value': self._string(self._form.get('payload')),
                'rows': 5,
                'placeholder': '{"key": "value"}',
            },
            {
                'id': 'btn_item_create',
                'type': 'button',
                'label': self._tr('Create Item', 'Создать задачу'),
                'msgTag': self.TAG_CREATE_ITEM,
            },
            {
                'id': 'btn_item_update',
                'type': 'button',
                'label': self._tr('Update Item', 'Обновить задачу'),
                'msgTag': self.TAG_UPDATE_ITEM,
            },
            {
                'id': 'btn_item_delete',
                'type': 'button',
                'label': self._tr('Delete Item', 'Удалить задачу'),
                'msgTag': self.TAG_DELETE_ITEM,
            },
            {
                'id': 'btn_item_reset_notifications',
                'type': 'button',
                'label': self._tr('Reset Notifications', 'Сбросить уведомления'),
                'msgTag': self.TAG_RESET_ITEM_NOTIFICATIONS,
            },
        ]
        return self._apply_visual_schema(controls)

    def _all_items_table_columns(self) -> List[Dict[str, Any]]:
        return [
            {'id': 'id', 'label': 'ID', 'type': 'number', 'readOnly': True},
            {'id': 'title', 'label': self._tr('Title', 'Название'), 'type': 'text', 'readOnly': True},
            {'id': 'state', 'label': self._tr('State', 'Статус'), 'type': 'text', 'readOnly': True},
            {'id': 'priority', 'label': self._tr('Priority', 'Приоритет'), 'type': 'text', 'readOnly': True},
            {'id': 'source', 'label': self._tr('Source', 'Источник'), 'type': 'text', 'readOnly': True},
            {'id': 'start', 'label': self._tr('Start', 'Старт'), 'type': 'text', 'readOnly': True},
            {'id': 'due', 'label': self._tr('Due', 'Срок'), 'type': 'text', 'readOnly': True},
            {'id': 'tags', 'label': self._tr('Tags', 'Теги'), 'type': 'text', 'readOnly': True},
            {'id': 'externalUid', 'label': self._tr('External UID', 'Внешний UID'), 'type': 'text', 'readOnly': True},
        ]

    def _visible_items_table_columns(self) -> List[Dict[str, Any]]:
        return self._all_items_table_columns()

    def _status_line(self) -> str:
        marker = {
            'success': self._tr('OK', 'OK'),
            'warning': self._tr('WARN', 'WARN'),
            'error': self._tr('ERR', 'ERR'),
            'info': self._tr('INFO', 'INFO'),
        }.get(self._status_level, 'INFO')
        return f'[{marker}] {self._status_text}'

    def _set_status(self, message: str, level: str = 'info') -> None:
        stamp = datetime.now().strftime('%H:%M:%S')
        self._status_text = f'{stamp} {message}'
        self._status_level = level

    def _build_list_items_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            'includeTerminal': bool(self._filters.get('includeTerminal')),
            'sort': self._normalized_sort(self._string(self._filters.get('sort'), 'start_asc')),
            'limit': self._bounded_int(self._filters.get('limit'), 100, 1, 500),
            'offset': 0,
        }

        search = self._string(self._filters.get('search'))
        if search:
            payload['search'] = search

        state = self._string(self._filters.get('state'))
        if state:
            payload['state'] = state

        priority = self._string(self._filters.get('priority'))
        if priority:
            payload['priority'] = priority

        source = self._string(self._filters.get('source'))
        if source:
            payload['source'] = source

        tag = self._string(self._filters.get('tag'))
        if tag:
            payload['tag'] = tag

        tags_any = self._normalize_chip_values(self._filters.get('tagsAny'))
        if tags_any:
            payload['tagsAny'] = tags_any

        external_uid = self._string(self._filters.get('externalUid'))
        if external_uid:
            payload['externalUid'] = external_uid

        has_start = self._nullable_bool(self._filters.get('hasStart'))
        if has_start is not None:
            payload['hasStart'] = has_start

        start_from = self._int_or_none(self._filters.get('startFromMs'))
        if start_from is not None:
            payload['startFromMs'] = start_from

        start_to = self._int_or_none(self._filters.get('startToMs'))
        if start_to is not None:
            payload['startToMs'] = start_to

        due_from = self._int_or_none(self._filters.get('dueFromMs'))
        if due_from is not None:
            payload['dueFromMs'] = due_from

        due_to = self._int_or_none(self._filters.get('dueToMs'))
        if due_to is not None:
            payload['dueToMs'] = due_to

        has_due = self._nullable_bool(self._filters.get('hasDue'))
        if has_due is not None:
            payload['hasDue'] = has_due

        if bool(self._filters.get('completedOnly')):
            terminal_ids = self._terminal_state_ids()
            if terminal_ids:
                payload['states'] = terminal_ids

        return payload

    def _build_create_item_payload(self) -> Optional[Dict[str, Any]]:
        title = self._string(self._form.get('title'))
        if not title:
            self._set_status(
                self._tr('Title is required for create.', 'Для создания задачи нужно указать название.'),
                level='warning',
            )
            return None

        parsed_start = self._parse_datetime_input(self._form.get('startAt'))
        if parsed_start[1] is not None:
            self._set_status(parsed_start[1], level='warning')
            return None

        parsed_due = self._parse_datetime_input(self._form.get('dueAt'))
        if parsed_due[1] is not None:
            self._set_status(parsed_due[1], level='warning')
            return None

        payload_data = self._parse_payload_json(self._form.get('payload'))
        if payload_data[1] is not None:
            self._set_status(payload_data[1], level='warning')
            return None

        lead_minutes = self._optional_int(
            self._form.get('upcomingLeadMin'),
            minimum=0,
            maximum=43200,
            field_name=self._tr('upcoming lead', 'интервал upcoming'),
        )
        if isinstance(lead_minutes, str):
            self._set_status(lead_minutes, level='warning')
            return None

        payload: Dict[str, Any] = {'title': title}

        description = self._string(self._form.get('description'))
        if description:
            payload['description'] = description

        for key in ('state', 'priority', 'source'):
            value = self._string(self._form.get(key))
            if value:
                payload[key] = value

        external_uid = self._string(self._form.get('externalUid'))
        if external_uid:
            payload['externalUid'] = external_uid

        if parsed_start[0] is not None:
            payload['startAtMs'] = parsed_start[0]
        if parsed_due[0] is not None:
            payload['dueAtMs'] = parsed_due[0]

        if isinstance(lead_minutes, int):
            payload['upcomingLeadMs'] = lead_minutes * 60000

        tags = self._normalize_chip_values(self._form.get('tags'))
        if tags:
            payload['tags'] = tags

        if payload_data[0] is not None:
            payload['payload'] = payload_data[0]

        return payload

    def _build_update_item_payload(self) -> Optional[Dict[str, Any]]:
        item_id = self._int_or_none(self._form.get('itemId'))
        if item_id is None or item_id <= 0:
            self._set_status(
                self._tr('Item id is required for update.', 'Для обновления задачи нужен item id.'),
                level='warning',
            )
            return None

        title = self._string(self._form.get('title'))
        if not title:
            self._set_status(
                self._tr('Title is required for update.', 'Для обновления задачи нужно указать название.'),
                level='warning',
            )
            return None

        parsed_start = self._parse_datetime_input(self._form.get('startAt'))
        if parsed_start[1] is not None:
            self._set_status(parsed_start[1], level='warning')
            return None

        parsed_due = self._parse_datetime_input(self._form.get('dueAt'))
        if parsed_due[1] is not None:
            self._set_status(parsed_due[1], level='warning')
            return None

        payload_data = self._parse_payload_json(self._form.get('payload'))
        if payload_data[1] is not None:
            self._set_status(payload_data[1], level='warning')
            return None

        lead_minutes = self._optional_int(
            self._form.get('upcomingLeadMin'),
            minimum=0,
            maximum=43200,
            field_name=self._tr('upcoming lead', 'интервал upcoming'),
        )
        if isinstance(lead_minutes, str):
            self._set_status(lead_minutes, level='warning')
            return None

        payload: Dict[str, Any] = {
            'id': item_id,
            'title': title,
            'description': self._string(self._form.get('description')),
            'tags': self._normalize_chip_values(self._form.get('tags')),
            'payload': payload_data[0],
            'startAtMs': parsed_start[0],
            'dueAtMs': parsed_due[0],
        }

        state = self._string(self._form.get('state'))
        priority = self._string(self._form.get('priority'))
        source = self._string(self._form.get('source'))

        if state:
            payload['state'] = state
        if priority:
            payload['priority'] = priority
        if source:
            payload['source'] = source

        payload['externalUid'] = self._string(self._form.get('externalUid'))

        if isinstance(lead_minutes, int):
            payload['upcomingLeadMs'] = lead_minutes * 60000

        return payload

    def _build_settings_payload(self, values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        tick = self._optional_int(
            values.get('settings_tick_interval_sec'),
            minimum=1,
            maximum=3600,
            field_name=self._tr('tick interval', 'интервал тика'),
            required=True,
        )
        if isinstance(tick, str):
            self._set_status(tick, level='warning')
            return None

        lead_min = self._optional_int(
            values.get('settings_default_upcoming_lead_min'),
            minimum=0,
            maximum=43200,
            field_name=self._tr('default upcoming lead', 'интервал upcoming по умолчанию'),
            required=True,
        )
        if isinstance(lead_min, str):
            self._set_status(lead_min, level='warning')
            return None

        notify_priority = self._optional_int(
            values.get('settings_notify_priority'),
            minimum=1,
            maximum=100000,
            field_name=self._tr('notify priority', 'приоритет уведомления'),
            required=True,
        )
        if isinstance(notify_priority, str):
            self._set_status(notify_priority, level='warning')
            return None

        notify_intent = self._string(values.get('settings_notify_intent'), 'ALARM') or 'ALARM'

        return {
            'tickIntervalSec': int(tick),
            'defaultUpcomingLeadMs': int(lead_min) * 60000,
            'notifyViaCore': bool(values.get('settings_notify_via_core')),
            'notifyUpcoming': bool(values.get('settings_notify_upcoming')),
            'notifyStarted': bool(values.get('settings_notify_started')),
            'notifyPriority': int(notify_priority),
            'notifyIntent': notify_intent,
        }

    def _build_dictionary_upsert_payload(self) -> Optional[Dict[str, Any]]:
        kind = self._string(self._dictionary_editor.get('kind')).lower()
        if kind not in ('state', 'priority', 'tag', 'source'):
            self._set_status(self._tr('Invalid dictionary kind.', 'Неверный тип словаря.'), level='warning')
            return None

        entry_id = self._string(self._dictionary_editor.get('id'))
        name = self._string(self._dictionary_editor.get('name'))
        if not entry_id and not name:
            self._set_status(
                self._tr('Dictionary id or name is required.', 'Нужно указать id или name записи словаря.'),
                level='warning',
            )
            return None

        order = self._optional_int(
            self._dictionary_editor.get('order'),
            minimum=-100000,
            maximum=100000,
            field_name='order',
            required=False,
        )
        if isinstance(order, str):
            self._set_status(order, level='warning')
            return None

        payload: Dict[str, Any] = {
            'kind': kind,
            'id': entry_id,
            'name': name,
            'isDefault': bool(self._dictionary_editor.get('isDefault')),
            'isSystem': bool(self._dictionary_editor.get('isSystem')),
        }

        if isinstance(order, int):
            payload['order'] = order

        color = self._string(self._dictionary_editor.get('color'))
        icon = self._string(self._dictionary_editor.get('icon'))
        if color:
            payload['color'] = color
        if icon:
            payload['icon'] = icon

        if kind == 'state':
            payload['terminal'] = bool(self._dictionary_editor.get('terminal'))

        return payload

    def _build_dictionary_delete_payload(self) -> Optional[Dict[str, Any]]:
        kind = self._string(self._dictionary_editor.get('kind')).lower()
        if kind not in ('state', 'priority', 'tag', 'source'):
            self._set_status(self._tr('Invalid dictionary kind.', 'Неверный тип словаря.'), level='warning')
            return None

        entry_id = self._string(self._dictionary_editor.get('id'))
        if not entry_id:
            self._set_status(
                self._tr('Dictionary id is required for delete.', 'Для удаления записи словаря нужен id.'),
                level='warning',
            )
            return None

        payload: Dict[str, Any] = {
            'kind': kind,
            'id': entry_id,
            'force': bool(self._dictionary_editor.get('force')),
        }

        replace_with = self._string(self._dictionary_editor.get('replaceWith'))
        if replace_with:
            payload['replaceWith'] = replace_with

        return payload

    def _consume_panel_values(self, payload: Dict[str, Any], reset_drilldown: bool = False) -> None:
        if not payload:
            return

        if reset_drilldown:
            self._filters['hasStart'] = None
            self._filters['startFromMs'] = None
            self._filters['startToMs'] = None
            self._filters['dueFromMs'] = None
            self._filters['dueToMs'] = None
            self._filters['hasDue'] = None
            self._filters['completedOnly'] = False

        if 'filter_search' in payload:
            self._filters['search'] = self._string(payload.get('filter_search'))
        if 'filter_state' in payload:
            self._filters['state'] = self._string(payload.get('filter_state'))
        if 'filter_priority' in payload:
            self._filters['priority'] = self._string(payload.get('filter_priority'))
        if 'filter_source' in payload:
            self._filters['source'] = self._string(payload.get('filter_source'))
        if 'filter_tag' in payload:
            self._filters['tag'] = self._string(payload.get('filter_tag'))
        if 'filter_tags_any' in payload:
            self._filters['tagsAny'] = self._normalize_chip_values(payload.get('filter_tags_any'))
        if 'filter_external_uid' in payload:
            self._filters['externalUid'] = self._string(payload.get('filter_external_uid'))
        if 'filter_has_start' in payload:
            self._filters['hasStart'] = self._nullable_bool(payload.get('filter_has_start'))
        if 'filter_start_from' in payload:
            self._filters['startFromMs'] = self._parse_datetime_input(payload.get('filter_start_from'))[0]
        if 'filter_start_to' in payload:
            self._filters['startToMs'] = self._parse_datetime_input(payload.get('filter_start_to'))[0]
        if 'filter_include_terminal' in payload:
            self._filters['includeTerminal'] = bool(payload.get('filter_include_terminal'))
        if 'filter_sort' in payload:
            self._filters['sort'] = self._normalized_sort(self._string(payload.get('filter_sort'), 'start_asc'))
        if 'filter_limit' in payload:
            self._filters['limit'] = self._bounded_int(payload.get('filter_limit'), 100, 1, 500)
        if 'filter_due_from_ms' in payload:
            self._filters['dueFromMs'] = self._int_or_none(payload.get('filter_due_from_ms'))
        if 'filter_due_to_ms' in payload:
            self._filters['dueToMs'] = self._int_or_none(payload.get('filter_due_to_ms'))
        if 'filter_due_from' in payload:
            self._filters['dueFromMs'] = self._parse_datetime_input(payload.get('filter_due_from'))[0]
        if 'filter_due_to' in payload:
            self._filters['dueToMs'] = self._parse_datetime_input(payload.get('filter_due_to'))[0]
        if 'filter_has_due' in payload:
            self._filters['hasDue'] = self._nullable_bool(payload.get('filter_has_due'))
        if 'filter_completed_only' in payload:
            self._filters['completedOnly'] = bool(payload.get('filter_completed_only'))

        if 'item_pick' in payload:
            self._form['itemPick'] = self._string(payload.get('item_pick'))
        if 'item_id' in payload:
            self._form['itemId'] = self._string(payload.get('item_id'))
        if 'item_title' in payload:
            self._form['title'] = self._string(payload.get('item_title'))
        if 'item_description' in payload:
            self._form['description'] = self._string(payload.get('item_description'))
        if 'item_state' in payload:
            self._form['state'] = self._string(payload.get('item_state'))
        if 'item_priority' in payload:
            self._form['priority'] = self._string(payload.get('item_priority'))
        if 'item_source' in payload:
            self._form['source'] = self._string(payload.get('item_source'))
        if 'item_external_uid' in payload:
            self._form['externalUid'] = self._string(payload.get('item_external_uid'))
        if 'item_start_at' in payload:
            self._form['startAt'] = self._string(payload.get('item_start_at'))
        if 'item_due_at' in payload:
            self._form['dueAt'] = self._string(payload.get('item_due_at'))
        if 'item_upcoming_lead_min' in payload:
            self._form['upcomingLeadMin'] = self._string(payload.get('item_upcoming_lead_min'))
        if 'item_tags' in payload:
            self._form['tags'] = self._normalize_chip_values(payload.get('item_tags'))
        if 'item_payload' in payload:
            self._form['payload'] = self._string(payload.get('item_payload'))

        if 'dict_kind' in payload:
            kind = self._string(payload.get('dict_kind')).lower()
            if kind in ('state', 'priority', 'tag', 'source'):
                self._dictionary_editor['kind'] = kind
        if 'dict_id' in payload:
            self._dictionary_editor['id'] = self._string(payload.get('dict_id'))
        if 'dict_name' in payload:
            self._dictionary_editor['name'] = self._string(payload.get('dict_name'))
        if 'dict_order' in payload:
            self._dictionary_editor['order'] = self._string(payload.get('dict_order'))
        if 'dict_color' in payload:
            self._dictionary_editor['color'] = self._string(payload.get('dict_color'))
        if 'dict_icon' in payload:
            self._dictionary_editor['icon'] = self._string(payload.get('dict_icon'))
        if 'dict_is_default' in payload:
            self._dictionary_editor['isDefault'] = bool(payload.get('dict_is_default'))
        if 'dict_is_system' in payload:
            self._dictionary_editor['isSystem'] = bool(payload.get('dict_is_system'))
        if 'dict_terminal' in payload:
            self._dictionary_editor['terminal'] = bool(payload.get('dict_terminal'))
        if 'dict_force' in payload:
            self._dictionary_editor['force'] = bool(payload.get('dict_force'))
        if 'dict_replace_with' in payload:
            self._dictionary_editor['replaceWith'] = self._string(payload.get('dict_replace_with'))

    def _extract_item_id(self, payload: Dict[str, Any]) -> Optional[int]:
        for key in ('item_id', 'itemId', 'id'):
            value = self._int_or_none(payload.get(key))
            if value is not None and value > 0:
                return value

        pick = self._int_or_none(payload.get('item_pick'))
        if pick is not None and pick > 0:
            return pick

        current = self._int_or_none(self._form.get('itemId'))
        if current is not None and current > 0:
            return current
        return None

    def _extract_item_id_from_payload_only(self, payload: Dict[str, Any]) -> Optional[int]:
        for key in ('item_id', 'itemId', 'id'):
            value = self._int_or_none(payload.get(key))
            if value is not None and value > 0:
                return value
        pick = self._int_or_none(payload.get('item_pick'))
        if pick is not None and pick > 0:
            return pick
        return None

    def _apply_item_to_form(self, item: Dict[str, Any]) -> None:
        item_id = self._int_or_none(item.get('id'))
        self._form['itemId'] = str(item_id) if item_id is not None else ''
        self._form['itemPick'] = str(item_id) if item_id is not None else ''

        self._form['title'] = self._string(item.get('title'))
        self._form['description'] = self._string(item.get('description'))
        self._form['state'] = self._string(item.get('state'))
        self._form['priority'] = self._string(item.get('priority'))
        self._form['source'] = self._string(item.get('source'))
        self._form['externalUid'] = self._string(item.get('externalUid'))

        self._form['startAt'] = self._datetime_value(item.get('startAtMs'))
        self._form['dueAt'] = self._datetime_value(item.get('dueAtMs'))

        lead_ms = self._int_or_none(item.get('upcomingLeadMs'))
        self._form['upcomingLeadMin'] = '' if lead_ms is None else str(max(0, int(lead_ms / 60000)))

        self._form['tags'] = self._normalize_chip_values(item.get('tags'))

        payload_value = item.get('payload')
        if payload_value is None:
            self._form['payload'] = ''
        else:
            try:
                self._form['payload'] = json.dumps(payload_value, ensure_ascii=False, indent=2)
            except Exception:
                self._form['payload'] = self._string(payload_value)

    def _clear_form_item_fields(self) -> None:
        self._form['itemPick'] = ''
        self._form['itemId'] = ''
        self._form['title'] = ''
        self._form['description'] = ''
        self._form['state'] = self._default_dictionary_id('state')
        self._form['priority'] = self._default_dictionary_id('priority')
        self._form['source'] = self._default_dictionary_id('source')
        self._form['externalUid'] = ''
        self._form['startAt'] = ''
        self._form['dueAt'] = ''
        self._form['upcomingLeadMin'] = ''
        self._form['tags'] = []
        self._form['payload'] = ''

    def _ensure_defaults_from_dictionaries(self) -> None:
        if not self._form.get('state'):
            self._form['state'] = self._default_dictionary_id('state')
        if not self._form.get('priority'):
            self._form['priority'] = self._default_dictionary_id('priority')
        if not self._form.get('source'):
            self._form['source'] = self._default_dictionary_id('source')

        if self._dictionary_editor.get('kind') not in ('state', 'priority', 'tag', 'source'):
            self._dictionary_editor['kind'] = 'state'

    def _default_dictionary_id(self, kind: str) -> str:
        entries = self._dictionaries.get(kind) or []
        for entry in entries:
            if bool(entry.get('isDefault')):
                return self._string(entry.get('id'))
        if entries:
            return self._string(entries[0].get('id'))
        return ''

    def _dictionary_options(self, kind: str, allow_empty: bool) -> List[Dict[str, Any]]:
        options: List[Dict[str, Any]] = []
        if allow_empty:
            options.append({'value': '', 'label': self._tr('Any', 'Любой')})

        entries = self._dictionaries.get(kind) or []
        for entry in entries:
            entry_id = self._string(entry.get('id'))
            if not entry_id:
                continue
            name = self._string(entry.get('name'), entry_id) or entry_id
            options.append({'value': entry_id, 'label': f'{name} ({entry_id})'})
        return options

    def _item_options(self) -> List[Dict[str, Any]]:
        options: List[Dict[str, Any]] = [{'value': '', 'label': self._tr('Select item', 'Выберите задачу')}]
        source_items = self._metrics_items if self._metrics_items else self._items
        for item in source_items[:1000]:
            item_id = self._int_or_none(item.get('id'))
            if item_id is None:
                continue
            title = self._string(item.get('title'), 'Untitled') or 'Untitled'
            state = self._string(item.get('state'))
            label = f'#{item_id} {title}'
            if state:
                label = f'{label} [{state}]'
            options.append({'value': str(item_id), 'label': label})
        return options

    def _items_table_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in self._items:
            item_id = self._int_or_none(item.get('id'))
            title = self._string(item.get('title'), 'Untitled') or 'Untitled'
            tags = self._normalize_chip_values(item.get('tags'))
            rows.append(
                {
                    'id': item_id,
                    'title': title,
                    'state': self._string(item.get('state')),
                    'priority': self._string(item.get('priority')),
                    'source': self._string(item.get('source')),
                    'start': self._format_timestamp(item.get('startAtMs')),
                    'due': self._format_timestamp(item.get('dueAtMs')),
                    'tags': ', '.join(tags),
                    'externalUid': self._string(item.get('externalUid')),
                }
            )
        return rows

    def _dictionary_table_rows(self, kind: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        entries = self._dictionaries.get(kind) or []
        for entry in entries:
            meta = entry.get('meta')
            meta_text = ''
            if meta is not None:
                try:
                    meta_text = json.dumps(meta, ensure_ascii=False)
                except Exception:
                    meta_text = self._string(meta)
            rows.append(
                {
                    'id': self._string(entry.get('id')),
                    'name': self._string(entry.get('name')),
                    'order': self._int(entry.get('order'), 0),
                    'color': self._string(entry.get('color')),
                    'icon': self._string(entry.get('icon')),
                    'isDefault': bool(entry.get('isDefault')),
                    'isSystem': bool(entry.get('isSystem')),
                    'terminal': bool(entry.get('terminal')),
                    'meta': meta_text,
                }
            )
        return rows

    def _parse_datetime_input(self, raw: Any) -> Tuple[Optional[int], Optional[str]]:
        if raw is None:
            return None, None
        if isinstance(raw, (int, float)):
            value = int(raw)
            if abs(value) < 1_000_000_000_000:
                value *= 1000
            return value, None

        text = self._string(raw)
        if not text:
            return None, None

        if re.fullmatch(r'-?\d+(?:\.\d+)?', text):
            value = int(float(text))
            if abs(value) < 1_000_000_000_000:
                value *= 1000
            return value, None

        normalized = text.replace('Z', '+00:00')
        try:
            dt = datetime.fromisoformat(normalized)
            return int(dt.timestamp() * 1000), None
        except Exception:
            pass

        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                dt = datetime.strptime(text, fmt)
                return int(dt.timestamp() * 1000), None
            except Exception:
                continue

        return None, self._tr(
            f'Cannot parse datetime: {text}',
            f'Не удалось распознать дату/время: {text}',
        )

    def _parse_payload_json(self, raw: Any) -> Tuple[Any, Optional[str]]:
        text = self._string(raw)
        if not text:
            return None, None
        try:
            return json.loads(text), None
        except Exception as error:
            return None, self._tr(
                f'Invalid payload JSON: {error}',
                f'Некорректный payload JSON: {error}',
            )

    def _optional_int(
        self,
        raw: Any,
        *,
        minimum: int,
        maximum: int,
        field_name: str,
        required: bool = False,
    ) -> Any:
        text = self._string(raw)
        if not text:
            if required:
                return self._tr(
                    f'{field_name} is required.',
                    f'Поле "{field_name}" обязательно.',
                )
            return None

        value = self._int_or_none(text)
        if value is None:
            return self._tr(
                f'Invalid number in field {field_name}.',
                f'Некорректное число в поле {field_name}.',
            )
        if value < minimum or value > maximum:
            return self._tr(
                f'{field_name} must be between {minimum} and {maximum}.',
                f'Поле {field_name} должно быть в диапазоне {minimum}..{maximum}.',
            )
        return value

    def _load_state(self) -> None:
        raw = self.get_property(self._STATE_KEY, None)
        if not isinstance(raw, dict):
            return

        filters = raw.get('filters')
        if isinstance(filters, dict):
            self._filters['search'] = self._string(filters.get('search'))
            self._filters['state'] = self._string(filters.get('state'))
            self._filters['priority'] = self._string(filters.get('priority'))
            self._filters['source'] = self._string(filters.get('source'))
            self._filters['tag'] = self._string(filters.get('tag'))
            self._filters['tagsAny'] = self._normalize_chip_values(filters.get('tagsAny'))
            self._filters['externalUid'] = self._string(filters.get('externalUid'))
            self._filters['includeTerminal'] = bool(filters.get('includeTerminal'))
            self._filters['sort'] = self._normalized_sort(self._string(filters.get('sort'), 'start_asc'))
            self._filters['limit'] = self._bounded_int(filters.get('limit'), 100, 1, 500)
            self._filters['hasStart'] = self._nullable_bool(filters.get('hasStart'))
            self._filters['startFromMs'] = self._int_or_none(filters.get('startFromMs'))
            self._filters['startToMs'] = self._int_or_none(filters.get('startToMs'))
            self._filters['dueFromMs'] = self._int_or_none(filters.get('dueFromMs'))
            self._filters['dueToMs'] = self._int_or_none(filters.get('dueToMs'))
            self._filters['hasDue'] = self._nullable_bool(filters.get('hasDue'))
            self._filters['completedOnly'] = bool(filters.get('completedOnly'))

        form = raw.get('form')
        if isinstance(form, dict):
            self._form['itemPick'] = self._string(form.get('itemPick'))
            self._form['itemId'] = self._string(form.get('itemId'))
            self._form['title'] = self._string(form.get('title'))
            self._form['description'] = self._string(form.get('description'))
            self._form['state'] = self._string(form.get('state'))
            self._form['priority'] = self._string(form.get('priority'))
            self._form['source'] = self._string(form.get('source'))
            self._form['externalUid'] = self._string(form.get('externalUid'))
            self._form['startAt'] = self._string(form.get('startAt'))
            self._form['dueAt'] = self._string(form.get('dueAt'))
            self._form['upcomingLeadMin'] = self._string(form.get('upcomingLeadMin'))
            self._form['tags'] = self._normalize_chip_values(form.get('tags'))
            self._form['payload'] = self._string(form.get('payload'))

        dictionary_editor = raw.get('dictionaryEditor')
        if isinstance(dictionary_editor, dict):
            kind = self._string(dictionary_editor.get('kind')).lower()
            if kind in ('state', 'priority', 'tag', 'source'):
                self._dictionary_editor['kind'] = kind
            self._dictionary_editor['id'] = self._string(dictionary_editor.get('id'))
            self._dictionary_editor['name'] = self._string(dictionary_editor.get('name'))
            self._dictionary_editor['order'] = self._string(dictionary_editor.get('order'), '0')
            self._dictionary_editor['color'] = self._string(dictionary_editor.get('color'))
            self._dictionary_editor['icon'] = self._string(dictionary_editor.get('icon'))
            self._dictionary_editor['isDefault'] = bool(dictionary_editor.get('isDefault'))
            self._dictionary_editor['isSystem'] = bool(dictionary_editor.get('isSystem'))
            self._dictionary_editor['terminal'] = bool(dictionary_editor.get('terminal'))
            self._dictionary_editor['force'] = bool(dictionary_editor.get('force'))
            self._dictionary_editor['replaceWith'] = self._string(dictionary_editor.get('replaceWith'))

    def _save_state(self) -> None:
        state = {
            'filters': {
                'search': self._string(self._filters.get('search')),
                'state': self._string(self._filters.get('state')),
                'priority': self._string(self._filters.get('priority')),
                'source': self._string(self._filters.get('source')),
                'tag': self._string(self._filters.get('tag')),
                'tagsAny': self._normalize_chip_values(self._filters.get('tagsAny')),
                'externalUid': self._string(self._filters.get('externalUid')),
                'includeTerminal': bool(self._filters.get('includeTerminal')),
                'sort': self._normalized_sort(self._string(self._filters.get('sort'), 'start_asc')),
                'limit': self._bounded_int(self._filters.get('limit'), 100, 1, 500),
                'hasStart': self._nullable_bool(self._filters.get('hasStart')),
                'startFromMs': self._int_or_none(self._filters.get('startFromMs')),
                'startToMs': self._int_or_none(self._filters.get('startToMs')),
                'dueFromMs': self._int_or_none(self._filters.get('dueFromMs')),
                'dueToMs': self._int_or_none(self._filters.get('dueToMs')),
                'hasDue': self._nullable_bool(self._filters.get('hasDue')),
                'completedOnly': bool(self._filters.get('completedOnly')),
            },
            'form': {
                'itemPick': self._string(self._form.get('itemPick')),
                'itemId': self._string(self._form.get('itemId')),
                'title': self._string(self._form.get('title')),
                'description': self._string(self._form.get('description')),
                'state': self._string(self._form.get('state')),
                'priority': self._string(self._form.get('priority')),
                'source': self._string(self._form.get('source')),
                'externalUid': self._string(self._form.get('externalUid')),
                'startAt': self._string(self._form.get('startAt')),
                'dueAt': self._string(self._form.get('dueAt')),
                'upcomingLeadMin': self._string(self._form.get('upcomingLeadMin')),
                'tags': self._normalize_chip_values(self._form.get('tags')),
                'payload': self._string(self._form.get('payload')),
            },
            'dictionaryEditor': {
                'kind': self._string(self._dictionary_editor.get('kind')),
                'id': self._string(self._dictionary_editor.get('id')),
                'name': self._string(self._dictionary_editor.get('name')),
                'order': self._string(self._dictionary_editor.get('order')),
                'color': self._string(self._dictionary_editor.get('color')),
                'icon': self._string(self._dictionary_editor.get('icon')),
                'isDefault': bool(self._dictionary_editor.get('isDefault')),
                'isSystem': bool(self._dictionary_editor.get('isSystem')),
                'terminal': bool(self._dictionary_editor.get('terminal')),
                'force': bool(self._dictionary_editor.get('force')),
                'replaceWith': self._string(self._dictionary_editor.get('replaceWith')),
            },
        }
        self.set_property(self._STATE_KEY, state)
        self.save_properties()

    def _format_error(self, response: Dict[str, Any]) -> str:
        error = response.get('error')
        if isinstance(error, dict):
            code = self._string(error.get('code'))
            message = self._string(error.get('message'))
            if code and message:
                return f'{code}: {message}'
            if message:
                return message
            if code:
                return code
        return self._string(error, self._tr('unknown error', 'неизвестная ошибка'))

    def _datetime_value(self, value: Any) -> str:
        ts = self._int_or_none(value)
        if ts is None:
            return ''
        if abs(ts) < 1_000_000_000_000:
            ts *= 1000
        try:
            return datetime.fromtimestamp(ts / 1000.0).strftime('%Y-%m-%dT%H:%M')
        except Exception:
            return ''

    def _format_timestamp(self, value: Any) -> str:
        ts = self._int_or_none(value)
        if ts is None:
            return ''
        if abs(ts) < 1_000_000_000_000:
            ts *= 1000
        try:
            return datetime.fromtimestamp(ts / 1000.0).strftime('%Y-%m-%d %H:%M')
        except Exception:
            return ''

    def _normalized_sort(self, value: str) -> str:
        key = self._string(value, 'start_asc')
        if key not in self._SORT_VALUES:
            return 'start_asc'
        return key

    def _bounded_int(self, raw: Any, default: int, minimum: int, maximum: int) -> int:
        value = self._int(raw, default)
        if value < minimum:
            return minimum
        if value > maximum:
            return maximum
        return value

    def _normalize_chip_values(self, raw: Any) -> List[str]:
        out: List[str] = []
        seen = set()

        if raw is None:
            return out

        values: List[Any]
        if isinstance(raw, list):
            values = list(raw)
        elif isinstance(raw, str):
            values = [part.strip() for part in raw.split(',')]
        else:
            values = [raw]

        for value in values:
            text = self._string(value).strip()
            if not text:
                continue
            if text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out

    def _tr(self, en: str, ru: str) -> str:
        return ru if self._ui_locale.startswith('ru') else en

    @staticmethod
    def _as_map(data: Any) -> Dict[str, Any]:
        if isinstance(data, dict):
            return dict(data)
        return {}

    @staticmethod
    def _string(value: Any, default: str = '') -> str:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    @staticmethod
    def _int(value: Any, default: int = 0) -> int:
        parsed = OrganizerUiPlugin._int_or_none(value)
        if parsed is None:
            return int(default)
        return parsed

    @staticmethod
    def _int_or_none(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(float(str(value).strip()))
        except Exception:
            return None

    @staticmethod
    def _nullable_bool(value: Any) -> Optional[bool]:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if not text:
            return None
        if text in {'true', '1', 'yes'}:
            return True
        if text in {'false', '0', 'no'}:
            return False
        return None


if __name__ == '__main__':
    run_plugin(OrganizerUiPlugin)
