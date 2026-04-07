#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sdk_python'))
from minachan_sdk import MinaChanPlugin, run_plugin


class OrganizerCoreError(RuntimeError):
    def __init__(self, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.code = str(code or 'organizer_core_error')
        self.message = str(message or 'Organizer core error')
        self.details = details


class OrganizerCorePlugin(MinaChanPlugin):
    ALLOWED_DICTIONARY_KINDS = ('state', 'priority', 'tag', 'source')

    CMD_GET_CONFIG = 'organizer-core:get-config'
    CMD_UPDATE_SETTINGS = 'organizer-core:update-settings'
    CMD_LIST_DICTIONARY = 'organizer-core:list-dictionary'
    CMD_UPSERT_DICTIONARY = 'organizer-core:upsert-dictionary-entry'
    CMD_DELETE_DICTIONARY = 'organizer-core:delete-dictionary-entry'
    CMD_CREATE_ITEM = 'organizer-core:create-item'
    CMD_GET_ITEM = 'organizer-core:get-item'
    CMD_UPDATE_ITEM = 'organizer-core:update-item'
    CMD_DELETE_ITEM = 'organizer-core:delete-item'
    CMD_LIST_ITEMS = 'organizer-core:list-items'
    CMD_UPSERT_EXTERNAL_ITEM = 'organizer-core:upsert-external-item'
    CMD_SET_ITEM_STATE = 'organizer-core:set-item-state'
    CMD_RESET_ITEM_NOTIFICATIONS = 'organizer-core:reset-item-notifications'
    CMD_PROCESS_NOTIFICATIONS = 'organizer-core:process-notifications'

    EVT_CONFIG_CHANGED = 'organizer-core:config-changed'
    EVT_ITEM_CREATED = 'organizer-core:item-created'
    EVT_ITEM_UPDATED = 'organizer-core:item-updated'
    EVT_ITEM_DELETED = 'organizer-core:item-deleted'
    EVT_ITEM_UPCOMING = 'organizer-core:item-upcoming'
    EVT_ITEM_STARTED = 'organizer-core:item-started'

    DEFAULT_SETTINGS = {
        'tickIntervalSec': 15,
        'defaultUpcomingLeadMs': 15 * 60 * 1000,
        'notifyViaCore': True,
        'notifyUpcoming': True,
        'notifyStarted': True,
        'notifyPriority': 7000,
        'notifyIntent': 'ALARM',
    }

    DEFAULT_DICTIONARIES = {
        'state': [
            {
                'id': 'planned',
                'name': 'Planned',
                'order': 10,
                'isDefault': True,
                'isSystem': True,
                'terminal': False,
            },
            {
                'id': 'in_progress',
                'name': 'In Progress',
                'order': 20,
                'isSystem': True,
                'terminal': False,
            },
            {
                'id': 'done',
                'name': 'Done',
                'order': 90,
                'isSystem': True,
                'terminal': True,
            },
            {
                'id': 'canceled',
                'name': 'Canceled',
                'order': 100,
                'isSystem': True,
                'terminal': True,
            },
        ],
        'priority': [
            {'id': 'low', 'name': 'Low', 'order': 10, 'isSystem': True},
            {
                'id': 'normal',
                'name': 'Normal',
                'order': 20,
                'isDefault': True,
                'isSystem': True,
            },
            {'id': 'high', 'name': 'High', 'order': 30, 'isSystem': True},
            {'id': 'critical', 'name': 'Critical', 'order': 40, 'isSystem': True},
        ],
        'source': [
            {
                'id': 'manual',
                'name': 'Manual',
                'order': 10,
                'isDefault': True,
                'isSystem': True,
            },
        ],
        'tag': [],
    }

    def __init__(self) -> None:
        super().__init__()
        self._db: Optional[sqlite3.Connection] = None
        self._settings: Dict[str, Any] = dict(self.DEFAULT_SETTINGS)
        self._tick_timer_id = -1

    def on_init(self) -> None:
        self._init_db()
        self._load_settings()
        self._ensure_default_dictionaries()

        self.add_listener(self.CMD_GET_CONFIG, self.on_get_config, listener_id='organizer_core_get_config')
        self.add_listener(self.CMD_UPDATE_SETTINGS, self.on_update_settings, listener_id='organizer_core_update_settings')
        self.add_listener(self.CMD_LIST_DICTIONARY, self.on_list_dictionary, listener_id='organizer_core_list_dictionary')
        self.add_listener(self.CMD_UPSERT_DICTIONARY, self.on_upsert_dictionary, listener_id='organizer_core_upsert_dictionary')
        self.add_listener(self.CMD_DELETE_DICTIONARY, self.on_delete_dictionary, listener_id='organizer_core_delete_dictionary')
        self.add_listener(self.CMD_CREATE_ITEM, self.on_create_item, listener_id='organizer_core_create_item')
        self.add_listener(self.CMD_GET_ITEM, self.on_get_item, listener_id='organizer_core_get_item')
        self.add_listener(self.CMD_UPDATE_ITEM, self.on_update_item, listener_id='organizer_core_update_item')
        self.add_listener(self.CMD_DELETE_ITEM, self.on_delete_item, listener_id='organizer_core_delete_item')
        self.add_listener(self.CMD_LIST_ITEMS, self.on_list_items, listener_id='organizer_core_list_items')
        self.add_listener(
            self.CMD_UPSERT_EXTERNAL_ITEM,
            self.on_upsert_external_item,
            listener_id='organizer_core_upsert_external_item',
        )
        self.add_listener(self.CMD_SET_ITEM_STATE, self.on_set_item_state, listener_id='organizer_core_set_item_state')
        self.add_listener(
            self.CMD_RESET_ITEM_NOTIFICATIONS,
            self.on_reset_item_notifications,
            listener_id='organizer_core_reset_item_notifications',
        )
        self.add_listener(
            self.CMD_PROCESS_NOTIFICATIONS,
            self.on_process_notifications,
            listener_id='organizer_core_process_notifications',
        )

        self._register_contract()
        self._arm_tick_timer()

    def on_unload(self) -> None:
        if self._tick_timer_id >= 0:
            try:
                self.cancel_timer(self._tick_timer_id)
            except Exception:
                pass
            self._tick_timer_id = -1
        if self._db is not None:
            self._db.close()
            self._db = None

    def on_get_config(self, sender: str, data: Any, tag: str) -> None:
        try:
            payload = self._build_config_payload()
            self._reply_success(sender, payload)
        except OrganizerCoreError as error:
            self._reply_error(sender, error)

    def on_update_settings(self, sender: str, data: Any, tag: str) -> None:
        try:
            payload = self._as_map(data)
            changed = self._update_settings(payload)
            if changed:
                self._emit_event(
                    self.EVT_CONFIG_CHANGED,
                    {
                        'kind': 'settings',
                        'settings': dict(self._settings),
                        'tsMs': self._now_ms(),
                    },
                )
            self._reply_success(
                sender,
                {
                    'changed': changed,
                    'settings': dict(self._settings),
                },
            )
        except OrganizerCoreError as error:
            self._reply_error(sender, error)

    def on_list_dictionary(self, sender: str, data: Any, tag: str) -> None:
        try:
            payload = self._as_map(data)
            kind = self._normalize_dictionary_kind(payload.get('kind'))
            if kind:
                dictionaries = {kind: self._list_dictionary_entries(kind)}
            else:
                dictionaries = {
                    value: self._list_dictionary_entries(value) for value in self.ALLOWED_DICTIONARY_KINDS
                }
            self._reply_success(sender, {'dictionaries': dictionaries})
        except OrganizerCoreError as error:
            self._reply_error(sender, error)

    def on_upsert_dictionary(self, sender: str, data: Any, tag: str) -> None:
        try:
            payload = self._as_map(data)
            entry = self._upsert_dictionary_entry(payload, emit=False)
            self._emit_event(
                self.EVT_CONFIG_CHANGED,
                {
                    'kind': 'dictionary',
                    'dictionaryKind': entry['kind'],
                    'entry': entry,
                    'tsMs': self._now_ms(),
                },
            )
            self._reply_success(sender, {'entry': entry})
        except OrganizerCoreError as error:
            self._reply_error(sender, error)

    def on_delete_dictionary(self, sender: str, data: Any, tag: str) -> None:
        try:
            payload = self._as_map(data)
            result = self._delete_dictionary_entry(payload)
            self._emit_event(
                self.EVT_CONFIG_CHANGED,
                {
                    'kind': 'dictionary',
                    'dictionaryKind': result.get('kind'),
                    'deletedId': result.get('deletedId'),
                    'replacedWith': result.get('replacedWith'),
                    'tsMs': self._now_ms(),
                },
            )
            self._reply_success(sender, result)
        except OrganizerCoreError as error:
            self._reply_error(sender, error)

    def on_create_item(self, sender: str, data: Any, tag: str) -> None:
        try:
            payload = self._as_map(data)
            item = self._create_item(payload)
            self._emit_event(self.EVT_ITEM_CREATED, {'item': item, 'tsMs': self._now_ms()})
            self._reply_success(sender, {'item': item})
        except OrganizerCoreError as error:
            self._reply_error(sender, error)

    def on_get_item(self, sender: str, data: Any, tag: str) -> None:
        try:
            payload = self._as_map(data)
            item_id = self._require_item_id(payload)
            item = self._load_item(item_id)
            if item is None:
                raise OrganizerCoreError('not_found', f'Item #{item_id} not found', {'id': item_id})
            self._reply_success(sender, {'item': item})
        except OrganizerCoreError as error:
            self._reply_error(sender, error)

    def on_update_item(self, sender: str, data: Any, tag: str) -> None:
        try:
            payload = self._as_map(data)
            item = self._update_item(payload)
            self._emit_event(self.EVT_ITEM_UPDATED, {'item': item, 'tsMs': self._now_ms()})
            self._reply_success(sender, {'item': item})
        except OrganizerCoreError as error:
            self._reply_error(sender, error)

    def on_delete_item(self, sender: str, data: Any, tag: str) -> None:
        try:
            payload = self._as_map(data)
            deleted = self._delete_item(payload)
            self._emit_event(self.EVT_ITEM_DELETED, {'item': deleted, 'tsMs': self._now_ms()})
            self._reply_success(sender, {'item': deleted})
        except OrganizerCoreError as error:
            self._reply_error(sender, error)

    def on_list_items(self, sender: str, data: Any, tag: str) -> None:
        try:
            payload = self._as_map(data)
            result = self._list_items(payload)
            self._reply_success(sender, result)
        except OrganizerCoreError as error:
            self._reply_error(sender, error)

    def on_upsert_external_item(self, sender: str, data: Any, tag: str) -> None:
        try:
            payload = self._as_map(data)
            result = self._upsert_external_item(payload)
            event_tag = self.EVT_ITEM_UPDATED if result.get('op') == 'updated' else self.EVT_ITEM_CREATED
            self._emit_event(event_tag, {'item': result.get('item'), 'tsMs': self._now_ms()})
            self._reply_success(sender, result)
        except OrganizerCoreError as error:
            self._reply_error(sender, error)

    def on_set_item_state(self, sender: str, data: Any, tag: str) -> None:
        try:
            payload = self._as_map(data)
            item_id = self._require_item_id(payload)
            state_raw = payload.get('state')
            if state_raw is None:
                raise OrganizerCoreError('bad_request', '"state" is required')
            state_id = self._resolve_dictionary_entry_id('state', state_raw, allow_autocreate=False)
            item = self._update_item({'id': item_id, 'state': state_id})
            self._emit_event(self.EVT_ITEM_UPDATED, {'item': item, 'tsMs': self._now_ms()})
            self._reply_success(sender, {'item': item})
        except OrganizerCoreError as error:
            self._reply_error(sender, error)

    def on_reset_item_notifications(self, sender: str, data: Any, tag: str) -> None:
        try:
            payload = self._as_map(data)
            item_id = self._require_item_id(payload)
            reset_upcoming = bool(payload.get('upcoming', True))
            reset_started = bool(payload.get('started', True))
            if not reset_upcoming and not reset_started:
                raise OrganizerCoreError('bad_request', 'Both "upcoming" and "started" are false')
            self._reset_item_notifications(item_id, reset_upcoming, reset_started)
            item = self._load_item(item_id)
            if item is None:
                raise OrganizerCoreError('not_found', f'Item #{item_id} not found', {'id': item_id})
            self._emit_event(self.EVT_ITEM_UPDATED, {'item': item, 'tsMs': self._now_ms()})
            self._reply_success(sender, {'item': item})
        except OrganizerCoreError as error:
            self._reply_error(sender, error)

    def on_process_notifications(self, sender: str, data: Any, tag: str) -> None:
        try:
            processed = self._process_notifications()
            self._reply_success(
                sender,
                {
                    'processed': processed,
                    'tsMs': self._now_ms(),
                },
            )
        except OrganizerCoreError as error:
            self._reply_error(sender, error)

    def _register_contract(self) -> None:
        self.register_command(self.CMD_GET_CONFIG, {'en': 'Get organizer core settings and dictionaries'})
        self.register_command(self.CMD_UPDATE_SETTINGS, {'en': 'Update organizer core settings'})
        self.register_command(self.CMD_LIST_DICTIONARY, {'en': 'List dictionary entries (state/priority/tag/source)'})
        self.register_command(self.CMD_UPSERT_DICTIONARY, {'en': 'Create or update dictionary entry'})
        self.register_command(self.CMD_DELETE_DICTIONARY, {'en': 'Delete dictionary entry with optional reassignment'})
        self.register_command(self.CMD_CREATE_ITEM, {'en': 'Create organizer item'})
        self.register_command(self.CMD_GET_ITEM, {'en': 'Get organizer item by id'})
        self.register_command(self.CMD_UPDATE_ITEM, {'en': 'Update organizer item by id'})
        self.register_command(self.CMD_DELETE_ITEM, {'en': 'Delete organizer item by id'})
        self.register_command(self.CMD_LIST_ITEMS, {'en': 'List organizer items with filters'})
        self.register_command(self.CMD_UPSERT_EXTERNAL_ITEM, {'en': 'Create/update organizer item by source+external_uid'})
        self.register_command(self.CMD_SET_ITEM_STATE, {'en': 'Set item state by id'})
        self.register_command(self.CMD_RESET_ITEM_NOTIFICATIONS, {'en': 'Reset upcoming/started notification flags'})
        self.register_command(self.CMD_PROCESS_NOTIFICATIONS, {'en': 'Force process upcoming/start notifications now'})

        self.register_event(self.EVT_CONFIG_CHANGED, 'Organizer core settings/dictionaries changed')
        self.register_event(self.EVT_ITEM_CREATED, 'Organizer item created')
        self.register_event(self.EVT_ITEM_UPDATED, 'Organizer item updated')
        self.register_event(self.EVT_ITEM_DELETED, 'Organizer item deleted')
        self.register_event(self.EVT_ITEM_UPCOMING, 'Organizer item is approaching start time')
        self.register_event(self.EVT_ITEM_STARTED, 'Organizer item reached start time')

    def _init_db(self) -> None:
        path = self._db_path()
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA foreign_keys=ON')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS dictionaries (
                kind TEXT NOT NULL,
                entry_id TEXT NOT NULL,
                name TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                color TEXT NOT NULL DEFAULT '',
                icon TEXT NOT NULL DEFAULT '',
                is_default INTEGER NOT NULL DEFAULT 0,
                is_system INTEGER NOT NULL DEFAULT 0,
                is_terminal INTEGER NOT NULL DEFAULT 0,
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                PRIMARY KEY (kind, entry_id)
            )
            '''
        )
        conn.execute(
            '''
            CREATE INDEX IF NOT EXISTS idx_dictionaries_kind_order
            ON dictionaries(kind, sort_order, name)
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                state_id TEXT NOT NULL,
                priority_id TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                external_uid TEXT NOT NULL DEFAULT '',
                start_at_ms INTEGER,
                due_at_ms INTEGER,
                upcoming_lead_ms INTEGER,
                payload_json TEXT NOT NULL DEFAULT 'null',
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                upcoming_notified_at_ms INTEGER,
                started_notified_at_ms INTEGER
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_items_state ON items(state_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_items_start ON items(start_at_ms)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_items_due ON items(due_at_ms)')
        conn.execute(
            '''
            CREATE INDEX IF NOT EXISTS idx_items_source_external
            ON items(source_id, external_uid)
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS item_tags (
                item_id INTEGER NOT NULL,
                tag_id TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                PRIMARY KEY (item_id, tag_id),
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag_id)')
        conn.commit()
        self._db = conn

    def _db_path(self) -> str:
        data_dir = str(self.info.get('dataDirPath') or '').strip()
        if not data_dir:
            data_dir = os.path.join(
                os.path.dirname(__file__),
                '..',
                '..',
                'data',
                'plugins',
                str(self.info.get('id') or 'organizer_core'),
            )
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, 'organizer_core.sqlite3')

    def _load_settings(self) -> None:
        db = self._require_db()
        cur = db.execute('SELECT key, value_json FROM settings')
        loaded: Dict[str, Any] = {}
        for row in cur.fetchall():
            key = str(row['key'])
            try:
                loaded[key] = json.loads(str(row['value_json']))
            except Exception:
                loaded[key] = row['value_json']

        settings = dict(self.DEFAULT_SETTINGS)
        settings.update(loaded)
        self._settings = self._normalize_settings(settings)
        self._save_settings()

    def _save_settings(self) -> None:
        db = self._require_db()
        now = self._now_ms()
        for key, value in self._settings.items():
            db.execute(
                '''
                INSERT INTO settings(key, value_json, updated_at_ms)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at_ms = excluded.updated_at_ms
                ''',
                (str(key), json.dumps(value, ensure_ascii=False), now),
            )
        db.commit()

    def _normalize_settings(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        interval = self._to_positive_int(raw.get('tickIntervalSec'), self.DEFAULT_SETTINGS['tickIntervalSec'], 1, 3600)
        default_upcoming = self._to_positive_int(
            raw.get('defaultUpcomingLeadMs'),
            self.DEFAULT_SETTINGS['defaultUpcomingLeadMs'],
            0,
            30 * 24 * 60 * 60 * 1000,
        )
        notify_priority = self._to_positive_int(raw.get('notifyPriority'), self.DEFAULT_SETTINGS['notifyPriority'], 1, 100000)
        notify_intent = str(raw.get('notifyIntent') or self.DEFAULT_SETTINGS['notifyIntent']).strip() or 'ALARM'
        return {
            'tickIntervalSec': interval,
            'defaultUpcomingLeadMs': default_upcoming,
            'notifyViaCore': bool(raw.get('notifyViaCore', True)),
            'notifyUpcoming': bool(raw.get('notifyUpcoming', True)),
            'notifyStarted': bool(raw.get('notifyStarted', True)),
            'notifyPriority': notify_priority,
            'notifyIntent': notify_intent,
        }

    def _update_settings(self, payload: Dict[str, Any]) -> bool:
        if not payload:
            return False
        updated = dict(self._settings)

        if 'tickIntervalSec' in payload:
            updated['tickIntervalSec'] = self._to_positive_int(payload.get('tickIntervalSec'), updated['tickIntervalSec'], 1, 3600)
        if 'defaultUpcomingLeadMs' in payload:
            updated['defaultUpcomingLeadMs'] = self._to_positive_int(
                payload.get('defaultUpcomingLeadMs'),
                updated['defaultUpcomingLeadMs'],
                0,
                30 * 24 * 60 * 60 * 1000,
            )
        if 'defaultUpcomingLeadSec' in payload:
            updated['defaultUpcomingLeadMs'] = self._to_positive_int(
                payload.get('defaultUpcomingLeadSec'),
                int(updated['defaultUpcomingLeadMs'] // 1000),
                0,
                30 * 24 * 60 * 60,
            ) * 1000
        if 'notifyViaCore' in payload:
            updated['notifyViaCore'] = bool(payload.get('notifyViaCore'))
        if 'notifyUpcoming' in payload:
            updated['notifyUpcoming'] = bool(payload.get('notifyUpcoming'))
        if 'notifyStarted' in payload:
            updated['notifyStarted'] = bool(payload.get('notifyStarted'))
        if 'notifyPriority' in payload:
            updated['notifyPriority'] = self._to_positive_int(
                payload.get('notifyPriority'),
                updated['notifyPriority'],
                1,
                100000,
            )
        if 'notifyIntent' in payload:
            intent = str(payload.get('notifyIntent') or '').strip()
            if intent:
                updated['notifyIntent'] = intent

        normalized = self._normalize_settings(updated)
        if normalized == self._settings:
            return False

        interval_changed = normalized.get('tickIntervalSec') != self._settings.get('tickIntervalSec')
        self._settings = normalized
        self._save_settings()
        if interval_changed:
            self._restart_tick_timer()
        return True

    def _build_config_payload(self) -> Dict[str, Any]:
        return {
            'settings': dict(self._settings),
            'dictionaries': {
                kind: self._list_dictionary_entries(kind) for kind in self.ALLOWED_DICTIONARY_KINDS
            },
        }

    def _ensure_default_dictionaries(self) -> None:
        for kind, entries in self.DEFAULT_DICTIONARIES.items():
            for entry in entries:
                if self._get_dictionary_entry(kind, str(entry.get('id') or '')) is not None:
                    continue
                self._upsert_dictionary_entry(
                    {
                        'kind': kind,
                        'id': entry.get('id'),
                        'name': entry.get('name'),
                        'order': entry.get('order', 0),
                        'isDefault': bool(entry.get('isDefault') is True),
                        'isSystem': bool(entry.get('isSystem') is True),
                        'terminal': bool(entry.get('terminal') is True),
                    },
                    emit=False,
                )

        for kind in ('state', 'priority', 'source'):
            self._ensure_default_for_kind(kind)
            self._ensure_kind_not_empty(kind)

    def _normalize_dictionary_kind(self, value: Any) -> str:
        text = str(value or '').strip().lower()
        if not text:
            return ''
        if text not in self.ALLOWED_DICTIONARY_KINDS:
            raise OrganizerCoreError(
                'bad_dictionary_kind',
                f'Unknown dictionary kind: {text}',
                {'allowedKinds': list(self.ALLOWED_DICTIONARY_KINDS)},
            )
        return text

    def _list_dictionary_entries(self, kind: str) -> List[Dict[str, Any]]:
        db = self._require_db()
        cur = db.execute(
            '''
            SELECT kind, entry_id, name, sort_order, color, icon, is_default, is_system, is_terminal, meta_json
            FROM dictionaries
            WHERE kind = ?
            ORDER BY sort_order ASC, name ASC, entry_id ASC
            ''',
            (kind,),
        )
        out: List[Dict[str, Any]] = []
        for row in cur.fetchall():
            out.append(self._dictionary_row_to_dict(row))
        return out

    def _dictionary_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        meta = {}
        try:
            meta_raw = json.loads(str(row['meta_json'] or '{}'))
            if isinstance(meta_raw, dict):
                meta = meta_raw
        except Exception:
            meta = {}
        return {
            'kind': str(row['kind']),
            'id': str(row['entry_id']),
            'name': str(row['name']),
            'order': int(row['sort_order']),
            'color': str(row['color'] or ''),
            'icon': str(row['icon'] or ''),
            'isDefault': bool(row['is_default']),
            'isSystem': bool(row['is_system']),
            'terminal': bool(row['is_terminal']),
            'meta': meta,
        }

    def _get_dictionary_entry(self, kind: str, entry_id: str) -> Optional[Dict[str, Any]]:
        db = self._require_db()
        cur = db.execute(
            '''
            SELECT kind, entry_id, name, sort_order, color, icon, is_default, is_system, is_terminal, meta_json
            FROM dictionaries
            WHERE kind = ? AND entry_id = ?
            LIMIT 1
            ''',
            (kind, entry_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return self._dictionary_row_to_dict(row)

    def _upsert_dictionary_entry(self, payload: Dict[str, Any], emit: bool = True) -> Dict[str, Any]:
        kind = self._normalize_dictionary_kind(payload.get('kind'))
        if not kind:
            raise OrganizerCoreError('bad_request', '"kind" is required')

        raw_entry_id = payload.get('id', payload.get('entryId'))
        entry_id = self._normalize_entry_id(raw_entry_id)
        name = str(payload.get('name') or '').strip()

        existing = None
        if entry_id:
            existing = self._get_dictionary_entry(kind, entry_id)
        if existing is None and not entry_id and name:
            guessed = self._normalize_entry_id(name)
            if guessed:
                existing = self._get_dictionary_entry(kind, guessed)
                entry_id = guessed

        if not entry_id and existing is not None:
            entry_id = str(existing.get('id') or '')
        if not entry_id and name:
            entry_id = self._normalize_entry_id(name)
        if not entry_id:
            raise OrganizerCoreError('bad_request', '"id" or "name" is required')

        if existing is None:
            existing = self._get_dictionary_entry(kind, entry_id)

        if not name:
            if existing is not None:
                name = str(existing.get('name') or entry_id)
            else:
                name = entry_id
        if not name.strip():
            raise OrganizerCoreError('bad_request', 'Dictionary entry name cannot be empty')

        order = self._to_int(
            payload.get('order', payload.get('sortOrder')),
            int(existing.get('order', 0)) if existing else 0,
        )
        color = str(payload.get('color') if 'color' in payload else (existing.get('color') if existing else '') or '').strip()
        icon = str(payload.get('icon') if 'icon' in payload else (existing.get('icon') if existing else '') or '').strip()
        is_default = bool(payload.get('isDefault')) if 'isDefault' in payload else bool(existing.get('isDefault') if existing else False)
        is_system = bool(payload.get('isSystem')) if 'isSystem' in payload else bool(existing.get('isSystem') if existing else False)
        if kind == 'state':
            is_terminal = bool(payload.get('terminal')) if 'terminal' in payload else bool(existing.get('terminal') if existing else False)
        else:
            is_terminal = False

        meta = payload.get('meta')
        if isinstance(meta, dict):
            meta_payload = dict(meta)
        elif existing is not None:
            meta_payload = dict(existing.get('meta') or {})
        else:
            meta_payload = {}

        db = self._require_db()
        now = self._now_ms()
        db.execute(
            '''
            INSERT INTO dictionaries(
                kind, entry_id, name, sort_order, color, icon,
                is_default, is_system, is_terminal, meta_json, created_at_ms, updated_at_ms
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kind, entry_id) DO UPDATE SET
                name = excluded.name,
                sort_order = excluded.sort_order,
                color = excluded.color,
                icon = excluded.icon,
                is_default = excluded.is_default,
                is_system = excluded.is_system,
                is_terminal = excluded.is_terminal,
                meta_json = excluded.meta_json,
                updated_at_ms = excluded.updated_at_ms
            ''',
            (
                kind,
                entry_id,
                name,
                int(order),
                color,
                icon,
                1 if is_default else 0,
                1 if is_system else 0,
                1 if is_terminal else 0,
                json.dumps(meta_payload, ensure_ascii=False),
                now,
                now,
            ),
        )
        if is_default:
            db.execute(
                '''
                UPDATE dictionaries
                SET is_default = 0, updated_at_ms = ?
                WHERE kind = ? AND entry_id != ?
                ''',
                (now, kind, entry_id),
            )
        db.commit()

        if kind in ('state', 'priority', 'source'):
            self._ensure_default_for_kind(kind)

        entry = self._get_dictionary_entry(kind, entry_id)
        if entry is None:
            raise OrganizerCoreError('internal_error', 'Failed to read dictionary entry after upsert')

        if emit:
            self._emit_event(
                self.EVT_CONFIG_CHANGED,
                {
                    'kind': 'dictionary',
                    'dictionaryKind': kind,
                    'entry': entry,
                    'tsMs': self._now_ms(),
                },
            )
        return entry

    def _delete_dictionary_entry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        kind = self._normalize_dictionary_kind(payload.get('kind'))
        if not kind:
            raise OrganizerCoreError('bad_request', '"kind" is required')
        entry_id = self._normalize_entry_id(payload.get('id', payload.get('entryId')))
        if not entry_id:
            raise OrganizerCoreError('bad_request', '"id" is required')

        entry = self._get_dictionary_entry(kind, entry_id)
        if entry is None:
            raise OrganizerCoreError('not_found', f'Dictionary entry {kind}:{entry_id} not found')

        force = bool(payload.get('force') is True)
        replace_with_raw = payload.get('replaceWith', payload.get('replace_with'))
        replace_with = self._normalize_entry_id(replace_with_raw)

        if entry.get('isSystem') and not force:
            raise OrganizerCoreError('forbidden', f'{kind}:{entry_id} is system entry; set force=true to delete')

        refs = self._count_dictionary_references(kind, entry_id)
        used_replacement = ''
        if refs > 0:
            if kind == 'tag':
                if not force:
                    raise OrganizerCoreError(
                        'dictionary_in_use',
                        f'Tag "{entry_id}" is used by {refs} item(s)',
                        {'references': refs},
                    )
                self._require_db().execute('DELETE FROM item_tags WHERE tag_id = ?', (entry_id,))
            else:
                if not replace_with:
                    replace_with = self._default_dictionary_entry_id(kind, exclude_id=entry_id)
                if not replace_with:
                    raise OrganizerCoreError(
                        'dictionary_in_use',
                        f'{kind}:{entry_id} is used by {refs} item(s); provide "replaceWith"',
                        {'references': refs},
                    )
                if replace_with == entry_id:
                    raise OrganizerCoreError('bad_request', '"replaceWith" must differ from deleted id')
                replacement_entry = self._get_dictionary_entry(kind, replace_with)
                if replacement_entry is None:
                    raise OrganizerCoreError(
                        'bad_request',
                        f'Replacement entry {kind}:{replace_with} not found',
                    )
                used_replacement = replace_with
                column = self._dictionary_reference_column(kind)
                self._require_db().execute(
                    f'UPDATE items SET {column} = ?, updated_at_ms = ? WHERE {column} = ?',
                    (replace_with, self._now_ms(), entry_id),
                )

        db = self._require_db()
        db.execute('DELETE FROM dictionaries WHERE kind = ? AND entry_id = ?', (kind, entry_id))
        db.commit()

        if kind in ('state', 'priority', 'source'):
            self._ensure_kind_not_empty(kind)
            self._ensure_default_for_kind(kind)

        return {
            'kind': kind,
            'deletedId': entry_id,
            'replacedWith': used_replacement,
            'references': refs,
        }

    def _count_dictionary_references(self, kind: str, entry_id: str) -> int:
        db = self._require_db()
        if kind == 'state':
            row = db.execute('SELECT COUNT(*) FROM items WHERE state_id = ?', (entry_id,)).fetchone()
        elif kind == 'priority':
            row = db.execute('SELECT COUNT(*) FROM items WHERE priority_id = ?', (entry_id,)).fetchone()
        elif kind == 'source':
            row = db.execute('SELECT COUNT(*) FROM items WHERE source_id = ?', (entry_id,)).fetchone()
        else:
            row = db.execute('SELECT COUNT(*) FROM item_tags WHERE tag_id = ?', (entry_id,)).fetchone()
        return int(row[0] if row is not None else 0)

    def _dictionary_reference_column(self, kind: str) -> str:
        if kind == 'state':
            return 'state_id'
        if kind == 'priority':
            return 'priority_id'
        if kind == 'source':
            return 'source_id'
        raise OrganizerCoreError('internal_error', f'Unsupported dictionary reference column for kind {kind}')

    def _ensure_default_for_kind(self, kind: str) -> None:
        db = self._require_db()
        row = db.execute(
            'SELECT entry_id FROM dictionaries WHERE kind = ? AND is_default = 1 LIMIT 1',
            (kind,),
        ).fetchone()
        if row is not None:
            return
        fallback = db.execute(
            'SELECT entry_id FROM dictionaries WHERE kind = ? ORDER BY sort_order ASC, entry_id ASC LIMIT 1',
            (kind,),
        ).fetchone()
        if fallback is None:
            return
        db.execute(
            'UPDATE dictionaries SET is_default = 1, updated_at_ms = ? WHERE kind = ? AND entry_id = ?',
            (self._now_ms(), kind, str(fallback['entry_id'])),
        )
        db.commit()

    def _ensure_kind_not_empty(self, kind: str) -> None:
        db = self._require_db()
        row = db.execute('SELECT COUNT(*) FROM dictionaries WHERE kind = ?', (kind,)).fetchone()
        if int(row[0] if row is not None else 0) > 0:
            return

        fallback_entries = list(self.DEFAULT_DICTIONARIES.get(kind, []))
        if not fallback_entries:
            return
        first = fallback_entries[0]
        self._upsert_dictionary_entry(
            {
                'kind': kind,
                'id': first.get('id'),
                'name': first.get('name'),
                'order': first.get('order', 0),
                'isDefault': True,
                'isSystem': True,
                'terminal': bool(first.get('terminal') is True),
            },
            emit=False,
        )

    def _default_dictionary_entry_id(self, kind: str, exclude_id: str = '') -> str:
        db = self._require_db()
        if exclude_id:
            row = db.execute(
                '''
                SELECT entry_id
                FROM dictionaries
                WHERE kind = ? AND is_default = 1 AND entry_id != ?
                LIMIT 1
                ''',
                (kind, exclude_id),
            ).fetchone()
            if row is not None:
                return str(row['entry_id'])
        else:
            row = db.execute(
                'SELECT entry_id FROM dictionaries WHERE kind = ? AND is_default = 1 LIMIT 1',
                (kind,),
            ).fetchone()
            if row is not None:
                return str(row['entry_id'])

        if exclude_id:
            row = db.execute(
                '''
                SELECT entry_id
                FROM dictionaries
                WHERE kind = ? AND entry_id != ?
                ORDER BY sort_order ASC, entry_id ASC
                LIMIT 1
                ''',
                (kind, exclude_id),
            ).fetchone()
        else:
            row = db.execute(
                '''
                SELECT entry_id
                FROM dictionaries
                WHERE kind = ?
                ORDER BY sort_order ASC, entry_id ASC
                LIMIT 1
                ''',
                (kind,),
            ).fetchone()
        if row is None:
            return ''
        return str(row['entry_id'])

    def _resolve_dictionary_entry_id(
        self,
        kind: str,
        value: Any,
        *,
        allow_autocreate: bool,
        default_if_empty: bool = True,
    ) -> str:
        raw = str(value or '').strip()
        if not raw:
            if default_if_empty:
                default_id = self._default_dictionary_entry_id(kind)
                if default_id:
                    return default_id
            raise OrganizerCoreError('bad_request', f'Missing {kind} id')

        normalized = self._normalize_entry_id(raw)
        if normalized:
            by_id = self._get_dictionary_entry(kind, normalized)
            if by_id is not None:
                return normalized

        by_name = self._find_dictionary_entry_by_name(kind, raw)
        if by_name is not None:
            return str(by_name.get('id') or '')

        if not allow_autocreate:
            raise OrganizerCoreError('bad_request', f'Unknown {kind}: {raw}')

        created = self._upsert_dictionary_entry(
            {
                'kind': kind,
                'id': normalized or raw,
                'name': raw,
                'isDefault': False,
                'isSystem': False,
                'terminal': False,
            },
            emit=False,
        )
        return str(created.get('id') or '')

    def _find_dictionary_entry_by_name(self, kind: str, name: str) -> Optional[Dict[str, Any]]:
        db = self._require_db()
        cur = db.execute(
            '''
            SELECT kind, entry_id, name, sort_order, color, icon, is_default, is_system, is_terminal, meta_json
            FROM dictionaries
            WHERE kind = ? AND lower(name) = lower(?)
            LIMIT 1
            ''',
            (kind, str(name)),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return self._dictionary_row_to_dict(row)

    def _create_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        title = str(payload.get('title') or payload.get('text') or '').strip() or 'Untitled'
        description = str(payload.get('description') or '').strip()

        state_id = self._resolve_dictionary_entry_id(
            'state',
            payload.get('state'),
            allow_autocreate=False,
            default_if_empty=True,
        )
        priority_id = self._resolve_dictionary_entry_id(
            'priority',
            payload.get('priority'),
            allow_autocreate=False,
            default_if_empty=True,
        )
        source_id = self._resolve_dictionary_entry_id(
            'source',
            payload.get('source', 'manual'),
            allow_autocreate=True,
            default_if_empty=True,
        )

        external_uid = str(payload.get('externalUid', payload.get('external_uid')) or '').strip()
        start_at_ms = self._extract_timestamp(payload, ['startAtMs', 'start_at_ms', 'start', 'datetime', 'dateTime'])
        due_at_ms = self._extract_timestamp(payload, ['dueAtMs', 'due_at_ms', 'due'])
        upcoming_lead_ms = self._extract_upcoming_lead_ms(payload)
        payload_json = self._encode_json(payload.get('payload'))

        tags = self._extract_tags(payload.get('tags', payload.get('tagIds')))
        tag_ids = [
            self._resolve_dictionary_entry_id('tag', value, allow_autocreate=True, default_if_empty=False)
            for value in tags
        ]
        tag_ids = [value for value in tag_ids if value]

        now = self._now_ms()
        db = self._require_db()
        cur = db.execute(
            '''
            INSERT INTO items(
                title, description, state_id, priority_id, source_id, external_uid,
                start_at_ms, due_at_ms, upcoming_lead_ms, payload_json,
                created_at_ms, updated_at_ms, upcoming_notified_at_ms, started_notified_at_ms
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            ''',
            (
                title,
                description,
                state_id,
                priority_id,
                source_id,
                external_uid,
                start_at_ms,
                due_at_ms,
                upcoming_lead_ms,
                payload_json,
                now,
                now,
            ),
        )
        item_id = int(cur.lastrowid)
        self._set_item_tags(item_id, tag_ids, commit=False)
        db.commit()

        item = self._load_item(item_id)
        if item is None:
            raise OrganizerCoreError('internal_error', 'Failed to read item after create')
        return item

    def _update_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        item_id = self._require_item_id(payload)
        current = self._load_item(item_id)
        if current is None:
            raise OrganizerCoreError('not_found', f'Item #{item_id} not found', {'id': item_id})

        columns: List[str] = []
        values: List[Any] = []
        reset_notifications = False

        if 'title' in payload:
            title = str(payload.get('title') or '').strip()
            if not title:
                raise OrganizerCoreError('bad_request', 'Title cannot be empty')
            columns.append('title = ?')
            values.append(title)

        if 'description' in payload:
            columns.append('description = ?')
            values.append(str(payload.get('description') or '').strip())

        if 'state' in payload:
            state_id = self._resolve_dictionary_entry_id(
                'state',
                payload.get('state'),
                allow_autocreate=False,
                default_if_empty=True,
            )
            columns.append('state_id = ?')
            values.append(state_id)
            reset_notifications = True

        if 'priority' in payload:
            priority_id = self._resolve_dictionary_entry_id(
                'priority',
                payload.get('priority'),
                allow_autocreate=False,
                default_if_empty=True,
            )
            columns.append('priority_id = ?')
            values.append(priority_id)

        if 'source' in payload:
            source_id = self._resolve_dictionary_entry_id(
                'source',
                payload.get('source'),
                allow_autocreate=True,
                default_if_empty=True,
            )
            columns.append('source_id = ?')
            values.append(source_id)

        if 'externalUid' in payload or 'external_uid' in payload:
            external_uid = str(payload.get('externalUid', payload.get('external_uid')) or '').strip()
            columns.append('external_uid = ?')
            values.append(external_uid)

        if any(key in payload for key in ('startAtMs', 'start_at_ms', 'start', 'datetime', 'dateTime')):
            start_at_ms = self._extract_timestamp(payload, ['startAtMs', 'start_at_ms', 'start', 'datetime', 'dateTime'])
            columns.append('start_at_ms = ?')
            values.append(start_at_ms)
            reset_notifications = True

        if any(key in payload for key in ('dueAtMs', 'due_at_ms', 'due')):
            due_at_ms = self._extract_timestamp(payload, ['dueAtMs', 'due_at_ms', 'due'])
            columns.append('due_at_ms = ?')
            values.append(due_at_ms)

        if any(
            key in payload
            for key in ('upcomingLeadMs', 'upcoming_lead_ms', 'upcomingLeadSec', 'notifyBeforeMs', 'notifyBeforeSec')
        ):
            lead_ms = self._extract_upcoming_lead_ms(payload)
            columns.append('upcoming_lead_ms = ?')
            values.append(lead_ms)
            reset_notifications = True

        if 'payload' in payload:
            columns.append('payload_json = ?')
            values.append(self._encode_json(payload.get('payload')))

        if not columns and 'tags' not in payload and 'tagIds' not in payload:
            return current

        db = self._require_db()
        if columns:
            if reset_notifications:
                columns.append('upcoming_notified_at_ms = NULL')
                columns.append('started_notified_at_ms = NULL')
            columns.append('updated_at_ms = ?')
            values.append(self._now_ms())
            values.append(item_id)
            db.execute(
                f'''
                UPDATE items
                SET {', '.join(columns)}
                WHERE id = ?
                ''',
                tuple(values),
            )

        if 'tags' in payload or 'tagIds' in payload:
            raw_tags = payload.get('tags', payload.get('tagIds'))
            tags = self._extract_tags(raw_tags)
            tag_ids = [
                self._resolve_dictionary_entry_id('tag', value, allow_autocreate=True, default_if_empty=False)
                for value in tags
            ]
            tag_ids = [value for value in tag_ids if value]
            self._set_item_tags(item_id, tag_ids, commit=False)

        db.commit()
        item = self._load_item(item_id)
        if item is None:
            raise OrganizerCoreError('internal_error', 'Failed to read item after update')
        return item

    def _delete_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        item_id = self._require_item_id(payload)
        item = self._load_item(item_id)
        if item is None:
            raise OrganizerCoreError('not_found', f'Item #{item_id} not found', {'id': item_id})
        db = self._require_db()
        db.execute('DELETE FROM items WHERE id = ?', (item_id,))
        db.commit()
        return item

    def _list_items(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        db = self._require_db()
        conditions: List[str] = []
        params: List[Any] = []

        state_raw = payload.get('state')
        if state_raw is not None:
            state_id = self._resolve_dictionary_entry_id(
                'state',
                state_raw,
                allow_autocreate=False,
                default_if_empty=False,
            )
            conditions.append('i.state_id = ?')
            params.append(state_id)

        states_raw = payload.get('states')
        if isinstance(states_raw, list) and states_raw:
            state_ids: List[str] = []
            for value in states_raw:
                state_ids.append(
                    self._resolve_dictionary_entry_id(
                        'state',
                        value,
                        allow_autocreate=False,
                        default_if_empty=False,
                    )
                )
            placeholders = ','.join('?' for _ in state_ids)
            conditions.append(f'i.state_id IN ({placeholders})')
            params.extend(state_ids)

        if payload.get('priority') is not None:
            priority_id = self._resolve_dictionary_entry_id(
                'priority',
                payload.get('priority'),
                allow_autocreate=False,
                default_if_empty=False,
            )
            conditions.append('i.priority_id = ?')
            params.append(priority_id)

        if payload.get('source') is not None:
            source_id = self._resolve_dictionary_entry_id(
                'source',
                payload.get('source'),
                allow_autocreate=False,
                default_if_empty=False,
            )
            conditions.append('i.source_id = ?')
            params.append(source_id)

        external_uid = str(payload.get('externalUid', payload.get('external_uid')) or '').strip()
        if external_uid:
            conditions.append('i.external_uid = ?')
            params.append(external_uid)

        if 'hasStart' in payload:
            has_start = bool(payload.get('hasStart'))
            conditions.append('i.start_at_ms IS NOT NULL' if has_start else 'i.start_at_ms IS NULL')

        start_from = self._extract_timestamp(payload, ['startFromMs', 'start_from_ms'])
        if start_from is not None:
            conditions.append('i.start_at_ms >= ?')
            params.append(start_from)
        start_to = self._extract_timestamp(payload, ['startToMs', 'start_to_ms'])
        if start_to is not None:
            conditions.append('i.start_at_ms <= ?')
            params.append(start_to)

        due_from = self._extract_timestamp(payload, ['dueFromMs', 'due_from_ms'])
        if due_from is not None:
            conditions.append('i.due_at_ms >= ?')
            params.append(due_from)
        due_to = self._extract_timestamp(payload, ['dueToMs', 'due_to_ms'])
        if due_to is not None:
            conditions.append('i.due_at_ms <= ?')
            params.append(due_to)
        if 'hasDue' in payload:
            has_due = bool(payload.get('hasDue'))
            conditions.append('i.due_at_ms IS NOT NULL' if has_due else 'i.due_at_ms IS NULL')

        search = str(payload.get('search') or '').strip()
        if search:
            conditions.append('(lower(i.title) LIKE ? OR lower(i.description) LIKE ?)')
            needle = f'%{search.lower()}%'
            params.extend([needle, needle])

        include_terminal = bool(payload.get('includeTerminal', True))
        if not include_terminal:
            terminal_ids = self._terminal_state_ids()
            if terminal_ids:
                placeholders = ','.join('?' for _ in terminal_ids)
                conditions.append(f'i.state_id NOT IN ({placeholders})')
                params.extend(terminal_ids)

        tag = str(payload.get('tag') or '').strip()
        if tag:
            tag_id = self._resolve_dictionary_entry_id(
                'tag',
                tag,
                allow_autocreate=False,
                default_if_empty=False,
            )
            conditions.append('EXISTS (SELECT 1 FROM item_tags t WHERE t.item_id = i.id AND t.tag_id = ?)')
            params.append(tag_id)

        tags_any_raw = payload.get('tagsAny', payload.get('tags_any'))
        if isinstance(tags_any_raw, list) and tags_any_raw:
            tag_ids = [
                self._resolve_dictionary_entry_id(
                    'tag',
                    value,
                    allow_autocreate=False,
                    default_if_empty=False,
                )
                for value in tags_any_raw
            ]
            if tag_ids:
                placeholders = ','.join('?' for _ in tag_ids)
                conditions.append(
                    f'EXISTS (SELECT 1 FROM item_tags t WHERE t.item_id = i.id AND t.tag_id IN ({placeholders}))'
                )
                params.extend(tag_ids)

        where_clause = f'WHERE {" AND ".join(conditions)}' if conditions else ''
        limit = self._to_positive_int(payload.get('limit'), 100, 1, 1000)
        offset = self._to_positive_int(payload.get('offset'), 0, 0, 1_000_000)
        order_clause = self._sql_order_by(str(payload.get('sort') or 'start_asc'))

        cur = db.execute(
            f'''
            SELECT
                i.id, i.title, i.description, i.state_id, i.priority_id, i.source_id, i.external_uid,
                i.start_at_ms, i.due_at_ms, i.upcoming_lead_ms, i.payload_json,
                i.created_at_ms, i.updated_at_ms, i.upcoming_notified_at_ms, i.started_notified_at_ms
            FROM items i
            {where_clause}
            ORDER BY {order_clause}
            LIMIT ? OFFSET ?
            ''',
            tuple(params + [limit, offset]),
        )
        rows = cur.fetchall()
        ids = [int(row['id']) for row in rows]
        tag_map = self._load_tags_for_item_ids(ids)
        items = [self._item_row_to_dict(row, tag_map.get(int(row['id']), [])) for row in rows]

        total_row = db.execute(
            f'''
            SELECT COUNT(*)
            FROM items i
            {where_clause}
            ''',
            tuple(params),
        ).fetchone()
        total = int(total_row[0] if total_row is not None else 0)
        return {
            'count': len(items),
            'total': total,
            'limit': limit,
            'offset': offset,
            'items': items,
        }

    def _upsert_external_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        source_raw = payload.get('source')
        external_uid = str(payload.get('externalUid', payload.get('external_uid')) or '').strip()
        if source_raw is None:
            raise OrganizerCoreError('bad_request', '"source" is required')
        if not external_uid:
            raise OrganizerCoreError('bad_request', '"externalUid" is required')
        source_id = self._resolve_dictionary_entry_id('source', source_raw, allow_autocreate=True, default_if_empty=True)

        db = self._require_db()
        row = db.execute(
            '''
            SELECT id FROM items
            WHERE source_id = ? AND external_uid = ?
            LIMIT 1
            ''',
            (source_id, external_uid),
        ).fetchone()

        data = dict(payload)
        data['source'] = source_id
        data['externalUid'] = external_uid

        if row is None:
            item = self._create_item(data)
            return {'op': 'created', 'item': item}

        item_id = int(row['id'])
        data['id'] = item_id
        item = self._update_item(data)
        return {'op': 'updated', 'item': item}

    def _reset_item_notifications(self, item_id: int, reset_upcoming: bool, reset_started: bool) -> None:
        parts: List[str] = []
        if reset_upcoming:
            parts.append('upcoming_notified_at_ms = NULL')
        if reset_started:
            parts.append('started_notified_at_ms = NULL')
        parts.append('updated_at_ms = ?')
        sql = f'UPDATE items SET {", ".join(parts)} WHERE id = ?'
        self._require_db().execute(sql, (self._now_ms(), item_id))
        self._require_db().commit()

    def _load_item(self, item_id: int) -> Optional[Dict[str, Any]]:
        db = self._require_db()
        row = db.execute(
            '''
            SELECT
                id, title, description, state_id, priority_id, source_id, external_uid,
                start_at_ms, due_at_ms, upcoming_lead_ms, payload_json,
                created_at_ms, updated_at_ms, upcoming_notified_at_ms, started_notified_at_ms
            FROM items
            WHERE id = ?
            LIMIT 1
            ''',
            (item_id,),
        ).fetchone()
        if row is None:
            return None
        tag_map = self._load_tags_for_item_ids([item_id])
        return self._item_row_to_dict(row, tag_map.get(item_id, []))

    def _set_item_tags(self, item_id: int, tags: Sequence[str], commit: bool = True) -> None:
        db = self._require_db()
        unique: List[str] = []
        seen = set()
        for value in tags:
            text = str(value or '').strip()
            if not text or text in seen:
                continue
            seen.add(text)
            unique.append(text)

        db.execute('DELETE FROM item_tags WHERE item_id = ?', (item_id,))
        now = self._now_ms()
        for tag_id in unique:
            db.execute(
                'INSERT OR IGNORE INTO item_tags(item_id, tag_id, created_at_ms) VALUES(?, ?, ?)',
                (item_id, tag_id, now),
            )
        if commit:
            db.commit()

    def _load_tags_for_item_ids(self, item_ids: Sequence[int]) -> Dict[int, List[str]]:
        if not item_ids:
            return {}
        placeholders = ','.join('?' for _ in item_ids)
        db = self._require_db()
        cur = db.execute(
            f'''
            SELECT item_id, tag_id
            FROM item_tags
            WHERE item_id IN ({placeholders})
            ORDER BY tag_id ASC
            ''',
            tuple(int(value) for value in item_ids),
        )
        out: Dict[int, List[str]] = {int(value): [] for value in item_ids}
        for row in cur.fetchall():
            out[int(row['item_id'])].append(str(row['tag_id']))
        return out

    def _item_row_to_dict(self, row: sqlite3.Row, tags: Sequence[str]) -> Dict[str, Any]:
        payload = None
        raw_payload = row['payload_json']
        if raw_payload is not None:
            try:
                payload = json.loads(str(raw_payload))
            except Exception:
                payload = None
        return {
            'id': int(row['id']),
            'title': str(row['title']),
            'description': str(row['description'] or ''),
            'state': str(row['state_id']),
            'priority': str(row['priority_id'] or ''),
            'source': str(row['source_id'] or ''),
            'externalUid': str(row['external_uid'] or ''),
            'tags': list(tags),
            'startAtMs': self._int_or_none(row['start_at_ms']),
            'dueAtMs': self._int_or_none(row['due_at_ms']),
            'upcomingLeadMs': self._int_or_none(row['upcoming_lead_ms']),
            'payload': payload,
            'createdAtMs': int(row['created_at_ms']),
            'updatedAtMs': int(row['updated_at_ms']),
            'upcomingNotifiedAtMs': self._int_or_none(row['upcoming_notified_at_ms']),
            'startedNotifiedAtMs': self._int_or_none(row['started_notified_at_ms']),
        }

    def _sql_order_by(self, sort_raw: str) -> str:
        sort_value = str(sort_raw or '').strip().lower()
        mapping = {
            'created_asc': 'i.created_at_ms ASC, i.id ASC',
            'created_desc': 'i.created_at_ms DESC, i.id DESC',
            'updated_asc': 'i.updated_at_ms ASC, i.id ASC',
            'updated_desc': 'i.updated_at_ms DESC, i.id DESC',
            'start_asc': 'COALESCE(i.start_at_ms, 9223372036854775807) ASC, i.id ASC',
            'start_desc': 'COALESCE(i.start_at_ms, -1) DESC, i.id DESC',
            'due_asc': 'COALESCE(i.due_at_ms, 9223372036854775807) ASC, i.id ASC',
            'due_desc': 'COALESCE(i.due_at_ms, -1) DESC, i.id DESC',
            'priority_asc': 'i.priority_id ASC, i.id ASC',
            'priority_desc': 'i.priority_id DESC, i.id DESC',
        }
        return mapping.get(sort_value, mapping['start_asc'])

    def _process_notifications(self) -> Dict[str, int]:
        db = self._require_db()
        now_ms = self._now_ms()
        terminal_ids = self._terminal_state_ids()

        cur = db.execute(
            '''
            SELECT
                id, title, description, state_id, priority_id, source_id, external_uid,
                start_at_ms, due_at_ms, upcoming_lead_ms, payload_json,
                created_at_ms, updated_at_ms, upcoming_notified_at_ms, started_notified_at_ms
            FROM items
            WHERE start_at_ms IS NOT NULL
              AND (upcoming_notified_at_ms IS NULL OR started_notified_at_ms IS NULL)
            ORDER BY start_at_ms ASC
            LIMIT 1000
            '''
        )
        rows = cur.fetchall()
        ids = [int(row['id']) for row in rows]
        tag_map = self._load_tags_for_item_ids(ids)

        upcoming_count = 0
        started_count = 0
        updated_ids: List[Tuple[int, bool, bool]] = []

        for row in rows:
            item_id = int(row['id'])
            state_id = str(row['state_id'])
            if state_id in terminal_ids:
                continue

            start_at_ms = self._int_or_none(row['start_at_ms'])
            if start_at_ms is None:
                continue
            upcoming_sent = self._int_or_none(row['upcoming_notified_at_ms']) is not None
            started_sent = self._int_or_none(row['started_notified_at_ms']) is not None
            if started_sent and upcoming_sent:
                continue

            lead_ms = self._int_or_none(row['upcoming_lead_ms'])
            if lead_ms is None:
                lead_ms = int(self._settings.get('defaultUpcomingLeadMs', self.DEFAULT_SETTINGS['defaultUpcomingLeadMs']))
            lead_ms = max(0, int(lead_ms))

            item_payload = self._item_row_to_dict(row, tag_map.get(item_id, []))

            mark_upcoming = False
            mark_started = False

            if not upcoming_sent and now_ms >= start_at_ms - lead_ms and now_ms < start_at_ms:
                mark_upcoming = True
                upcoming_count += 1
                self._emit_event(
                    self.EVT_ITEM_UPCOMING,
                    {
                        'item': item_payload,
                        'leadMs': lead_ms,
                        'startAtMs': start_at_ms,
                        'tsMs': now_ms,
                    },
                )
                if bool(self._settings.get('notifyViaCore')) and bool(self._settings.get('notifyUpcoming')):
                    self._send_notify_payload(item_payload, kind='upcoming', lead_ms=lead_ms)

            if not started_sent and now_ms >= start_at_ms:
                mark_started = True
                started_count += 1
                self._emit_event(
                    self.EVT_ITEM_STARTED,
                    {
                        'item': item_payload,
                        'startAtMs': start_at_ms,
                        'tsMs': now_ms,
                    },
                )
                if bool(self._settings.get('notifyViaCore')) and bool(self._settings.get('notifyStarted')):
                    self._send_notify_payload(item_payload, kind='started', lead_ms=lead_ms)

            # Once "started" is emitted, we also mark upcoming as acknowledged
            # to avoid re-scanning this row forever when upcoming was skipped.
            if mark_started and not upcoming_sent and not mark_upcoming:
                mark_upcoming = True

            if mark_upcoming or mark_started:
                updated_ids.append((item_id, mark_upcoming, mark_started))

        for item_id, mark_upcoming, mark_started in updated_ids:
            if mark_upcoming and mark_started:
                db.execute(
                    '''
                    UPDATE items
                    SET upcoming_notified_at_ms = ?, started_notified_at_ms = ?, updated_at_ms = ?
                    WHERE id = ?
                    ''',
                    (now_ms, now_ms, now_ms, item_id),
                )
            elif mark_upcoming:
                db.execute(
                    '''
                    UPDATE items
                    SET upcoming_notified_at_ms = ?, updated_at_ms = ?
                    WHERE id = ?
                    ''',
                    (now_ms, now_ms, item_id),
                )
            elif mark_started:
                db.execute(
                    '''
                    UPDATE items
                    SET started_notified_at_ms = ?, updated_at_ms = ?
                    WHERE id = ?
                    ''',
                    (now_ms, now_ms, item_id),
                )
        if updated_ids:
            db.commit()

        return {
            'upcoming': upcoming_count,
            'started': started_count,
        }

    def _send_notify_payload(self, item: Dict[str, Any], kind: str, lead_ms: int) -> None:
        title = str(item.get('title') or 'Untitled')
        start_at_ms = self._int_or_none(item.get('startAtMs'))
        when_text = self._format_datetime_ms(start_at_ms) if start_at_ms is not None else 'unknown time'

        if kind == 'upcoming':
            minutes = max(0, int(lead_ms // 60000))
            if minutes > 0:
                message = f'Upcoming in {minutes} min: {title} ({when_text})'
            else:
                message = f'Upcoming soon: {title} ({when_text})'
        else:
            message = f'Started: {title} ({when_text})'

        self.send_message(
            'MinaChan:notify',
            {
                'message': message,
                'speech-intent': str(self._settings.get('notifyIntent') or 'ALARM'),
                'priority': int(self._settings.get('notifyPriority') or 7000),
            },
        )

    def _terminal_state_ids(self) -> List[str]:
        db = self._require_db()
        cur = db.execute(
            '''
            SELECT entry_id
            FROM dictionaries
            WHERE kind = ? AND is_terminal = 1
            ''',
            ('state',),
        )
        return [str(row['entry_id']) for row in cur.fetchall()]

    def _arm_tick_timer(self) -> None:
        if self._tick_timer_id >= 0:
            return
        delay_ms = int(self._settings.get('tickIntervalSec', self.DEFAULT_SETTINGS['tickIntervalSec'])) * 1000
        timer_id = self.set_timer_callback(delay_ms, 1, self._on_tick_timer)
        if timer_id >= 0:
            self._tick_timer_id = timer_id

    def _restart_tick_timer(self) -> None:
        if self._tick_timer_id >= 0:
            try:
                self.cancel_timer(self._tick_timer_id)
            except Exception:
                pass
            self._tick_timer_id = -1
        self._arm_tick_timer()

    def _on_tick_timer(self, sender: str, payload: Any, tag: str) -> None:
        self._tick_timer_id = -1
        try:
            self._process_notifications()
        except Exception as error:
            self.send_message('core-events:error', {'message': f'organizer_core tick error: {error}'})
        finally:
            self._arm_tick_timer()

    def _emit_event(self, tag: str, payload: Dict[str, Any]) -> None:
        self.send_message(tag, payload)

    def _reply_success(self, sender: str, payload: Dict[str, Any]) -> None:
        if not sender:
            return
        out = {'ok': True}
        out.update(payload)
        self.reply(sender, out)

    def _reply_error(self, sender: str, error: OrganizerCoreError) -> None:
        if not sender:
            return
        payload: Dict[str, Any] = {
            'ok': False,
            'error': {
                'code': error.code,
                'message': error.message,
            },
        }
        if error.details is not None:
            payload['error']['details'] = error.details
        self.reply(sender, payload)

    def _as_map(self, data: Any) -> Dict[str, Any]:
        if isinstance(data, dict):
            return dict(data)
        return {}

    def _require_item_id(self, payload: Dict[str, Any]) -> int:
        raw = payload.get('id', payload.get('itemId'))
        value = self._int_or_none(raw)
        if value is None or value <= 0:
            raise OrganizerCoreError('bad_request', '"id" is required and must be positive integer')
        return int(value)

    def _extract_tags(self, raw: Any) -> List[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            candidates = [part.strip() for part in raw.split(',')]
        elif isinstance(raw, list):
            candidates = [str(value).strip() for value in raw]
        else:
            candidates = [str(raw).strip()]

        out: List[str] = []
        seen = set()
        for candidate in candidates:
            if not candidate:
                continue
            normalized = self._normalize_entry_id(candidate)
            if not normalized:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out

    def _extract_timestamp(self, payload: Dict[str, Any], keys: Sequence[str]) -> Optional[int]:
        present = False
        raw_value: Any = None
        for key in keys:
            if key in payload:
                present = True
                raw_value = payload.get(key)
                break
        if not present:
            return None
        if raw_value is None:
            return None
        return self._parse_timestamp(raw_value)

    def _extract_upcoming_lead_ms(self, payload: Dict[str, Any]) -> Optional[int]:
        if 'upcomingLeadMs' in payload:
            return self._to_positive_int(payload.get('upcomingLeadMs'), 0, 0, 30 * 24 * 60 * 60 * 1000)
        if 'upcoming_lead_ms' in payload:
            return self._to_positive_int(payload.get('upcoming_lead_ms'), 0, 0, 30 * 24 * 60 * 60 * 1000)
        if 'notifyBeforeMs' in payload:
            return self._to_positive_int(payload.get('notifyBeforeMs'), 0, 0, 30 * 24 * 60 * 60 * 1000)
        if 'upcomingLeadSec' in payload:
            return self._to_positive_int(payload.get('upcomingLeadSec'), 0, 0, 30 * 24 * 60 * 60) * 1000
        if 'notifyBeforeSec' in payload:
            return self._to_positive_int(payload.get('notifyBeforeSec'), 0, 0, 30 * 24 * 60 * 60) * 1000
        return None

    def _parse_timestamp(self, raw: Any) -> int:
        if isinstance(raw, (int, float)):
            value = int(raw)
            if abs(value) < 1_000_000_000_000:
                value *= 1000
            return value

        text = str(raw or '').strip()
        if not text:
            raise OrganizerCoreError('bad_request', 'Empty timestamp value')

        if re.fullmatch(r'-?\d+(?:\.\d+)?', text):
            value = int(float(text))
            if abs(value) < 1_000_000_000_000:
                value *= 1000
            return value

        normalized = text.replace('T', ' ').replace('Z', '').strip()
        for fmt in (
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%d',
            '%d.%m.%Y %H:%M:%S',
            '%d.%m.%Y %H:%M',
            '%d.%m.%Y',
        ):
            try:
                dt = datetime.strptime(normalized, fmt)
                return int(dt.timestamp() * 1000)
            except Exception:
                continue
        raise OrganizerCoreError('bad_request', f'Cannot parse timestamp: {text}')

    def _encode_json(self, value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return json.dumps(str(value), ensure_ascii=False)

    def _normalize_entry_id(self, raw: Any) -> str:
        text = str(raw or '').strip().lower()
        if not text:
            return ''
        text = re.sub(r'\s+', '_', text, flags=re.UNICODE)
        text = re.sub(r'[^\w.-]+', '_', text, flags=re.UNICODE)
        text = re.sub(r'_+', '_', text).strip('_.-')
        return text

    def _format_datetime_ms(self, ts_ms: Optional[int]) -> str:
        if ts_ms is None:
            return 'unknown'
        try:
            return datetime.fromtimestamp(int(ts_ms) / 1000.0).strftime('%Y-%m-%d %H:%M')
        except Exception:
            return 'unknown'

    def _require_db(self) -> sqlite3.Connection:
        if self._db is None:
            raise OrganizerCoreError('internal_error', 'Organizer core DB is not initialized')
        return self._db

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _int_or_none(raw: Any) -> Optional[int]:
        if raw is None:
            return None
        try:
            return int(raw)
        except Exception:
            return None

    @staticmethod
    def _to_int(raw: Any, default: int) -> int:
        try:
            return int(raw)
        except Exception:
            return int(default)

    @staticmethod
    def _to_positive_int(raw: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(raw)
        except Exception:
            value = int(default)
        if value < minimum:
            value = minimum
        if value > maximum:
            value = maximum
        return value


if __name__ == '__main__':
    run_plugin(OrganizerCorePlugin)
