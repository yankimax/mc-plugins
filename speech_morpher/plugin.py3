#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import importlib.util
import importlib.machinery
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

_PLUGIN_DIR = os.path.dirname(__file__)
_MODULES_DIR = os.path.join(_PLUGIN_DIR, 'modules')
sys.path.insert(0, _PLUGIN_DIR)
sys.path.insert(0, _MODULES_DIR)
sys.path.insert(0, os.path.join(_PLUGIN_DIR, '..', 'sdk_python'))

from base import MorpherBridge
from minachan_sdk import MinaChanPlugin, run_plugin


MODE_OFF = 0
MODE_BY_PRESET = 1
MODE_ALWAYS = 2
MODE_NAMES = {
    MODE_OFF: 'OFF',
    MODE_BY_PRESET: 'BY_PRESET',
    MODE_ALWAYS: 'ALWAYS',
}

REQUEST_SAY_TAG = 'MinaChan:request-say'
REQUEST_SAY_RESOLVE_TAG = 'talking_system:request-say:resolve'
REQUEST_SAY_EMIT_TAG = 'talking_system:request-say:emit'
MORPHER_ALTERNATIVE_TAG = 'speech_morpher:request-say:apply'
MORPHER_ALTERNATIVE_PRIORITY = 6000


@dataclass
class ModuleRecord:
    module_id: str
    source_path: str
    priority: int
    instance: Any


class SpeechMorpherPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._bridge = MorpherBridge()
        self._modules: Dict[str, ModuleRecord] = {}
        self._module_order: List[str] = []
        self._module_modes: Dict[str, int] = {}
        self._locale = 'en'
        self._speech_links: List[Dict[str, Any]] = []
        self._character_id = ''
        self._preset: Dict[str, Any] = {'traits': {}, 'emotions': {}}
        self._rng = random.Random()
        self._rng_seed: Optional[int] = None

    def on_init(self) -> None:
        plugin_id = str(self.info.get('id') or '').strip()
        if plugin_id:
            self.add_listener(plugin_id, self.on_runtime_reply, listener_id='speech_morpher_runtime_reply')

        self.add_listener(MORPHER_ALTERNATIVE_TAG, self.on_request_say_morph, listener_id='speech_morpher_alt')
        self.add_listener('talk:character-updated', self.on_talk_character_updated, listener_id='speech_morpher_preset')
        self.add_listener('character:changed', self.on_character_changed, listener_id='speech_morpher_character')
        self.add_listener('gui:request-panels', self.on_request_panels, listener_id='speech_morpher_panels')

        self.add_listener('speech_morpher:list-modules', self.on_list_modules, listener_id='speech_morpher_list')
        self.add_listener('speech_morpher:get-state', self.on_get_state, listener_id='speech_morpher_state')
        self.add_listener('speech_morpher:set-module-mode', self.on_set_module_mode, listener_id='speech_morpher_set_mode')
        self.add_listener('speech_morpher:set-all-modes', self.on_set_all_modes, listener_id='speech_morpher_set_all')
        self.add_listener('speech_morpher:reload-modules', self.on_reload_modules, listener_id='speech_morpher_reload')
        self.add_listener('speech_morpher:preview', self.on_preview, listener_id='speech_morpher_preview')
        self.add_listener('speech_morpher:update-settings', self.on_update_settings, listener_id='speech_morpher_update_settings')
        self.add_listener('speech_morpher:gui-all-on', self.on_gui_all_on, listener_id='speech_morpher_gui_all_on')
        self.add_listener('speech_morpher:gui-all-off', self.on_gui_all_off, listener_id='speech_morpher_gui_all_off')
        self.add_listener(
            'speech_morpher:gui-all-by-preset',
            self.on_gui_all_by_preset,
            listener_id='speech_morpher_gui_all_by_preset',
        )

        self.set_alternative(
            REQUEST_SAY_TAG,
            MORPHER_ALTERNATIVE_TAG,
            MORPHER_ALTERNATIVE_PRIORITY,
        )

        self._locale = self._normalize_locale(
            self.get_property('locale', self.info.get('locale') or 'en')
        )
        self._load_rng_seed()
        self._reload_modules()
        self._register_settings_gui()
        self.add_locale_listener(
            self._on_locale_changed,
            default_locale=self._locale or 'en',
        )
        self._request_runtime_context()

        self._register_command('speech_morpher:list-modules', 'List loaded speech morpher modules')
        self._register_command('speech_morpher:get-state', 'Get speech morpher state')
        self._register_command('speech_morpher:set-module-mode', 'Set module mode OFF/BY_PRESET/ALWAYS')
        self._register_command('speech_morpher:set-all-modes', 'Set one mode for all modules')
        self._register_command('speech_morpher:reload-modules', 'Reload morpher modules from disk')
        self._register_command('speech_morpher:preview', 'Preview transformed text without MinaChan:say')
        self._sync_speech_links()

        self.send_message(
            'core-events:log',
            {'message': f'speech_morpher initialized: modules={len(self._module_order)}'},
        )

    def on_runtime_reply(self, sender: str, data: Any, tag: str) -> None:
        if not isinstance(data, dict):
            return

        if isinstance(data.get('preset'), dict):
            self._preset = self._normalize_preset_map(data.get('preset'))

        locale_value = data.get('locale')
        if locale_value is not None:
            locale = self._normalize_locale(locale_value)
            if locale and locale != self._locale:
                self._locale = locale
                self._sync_speech_links()

        active = data.get('active')
        if active is not None:
            self._character_id = str(active or '').strip().lower()

    def _on_locale_changed(self, locale: str, chain: List[str]) -> None:
        if locale != self._locale or not self._speech_links:
            self._locale = locale
            self._sync_speech_links()

    def on_unload(self) -> None:
        self._clear_speech_links()

    def on_talk_character_updated(self, sender: str, data: Any, tag: str) -> None:
        if not isinstance(data, dict):
            return
        if isinstance(data.get('preset'), dict):
            self._preset = self._normalize_preset_map(data.get('preset'))

    def on_character_changed(self, sender: str, data: Any, tag: str) -> None:
        if not isinstance(data, dict):
            return
        character_id = str(data.get('id') or '').strip().lower()
        if character_id:
            self._character_id = character_id

    def on_request_panels(self, sender: str, data: Any, tag: str) -> None:
        self._register_settings_gui()

    def on_request_say_morph(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_payload(data)
        if payload is None:
            self.call_next_alternative(sender, REQUEST_SAY_TAG, MORPHER_ALTERNATIVE_TAG, data)
            return

        text = str(payload.get('text') or '').strip()
        if text:
            context = self._build_context(payload)
            payload['text'] = self._apply_modules(text, payload, context)

        self.call_next_alternative(
            sender,
            REQUEST_SAY_TAG,
            MORPHER_ALTERNATIVE_TAG,
            payload,
        )

    def on_list_modules(self, sender: str, data: Any, tag: str) -> None:
        self._reply(
            sender,
            {
                'count': len(self._module_order),
                'modules': self._serialize_modules(),
            },
        )

    def on_get_state(self, sender: str, data: Any, tag: str) -> None:
        self._reply(
            sender,
            {
                'locale': self._locale,
                'characterId': self._character_id,
                'rngSeed': self._rng_seed,
                'modules': self._serialize_modules(),
                'preset': self._normalize_preset_map(self._preset),
            },
        )

    def on_set_module_mode(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        module_id = str(payload.get('id') or payload.get('module') or '').strip().lower()
        if not module_id:
            self._reply(sender, {'ok': False, 'error': 'module id is required'})
            return
        if module_id not in self._modules:
            self._reply(sender, {'ok': False, 'error': f'unknown module: {module_id}'})
            return

        mode = self._parse_mode(payload.get('mode'), self._module_modes.get(module_id, MODE_BY_PRESET))
        self._set_module_mode(module_id, mode, save=True)
        self._register_settings_gui()
        self._reply(sender, {'ok': True, 'id': module_id, 'mode': mode, 'modeName': MODE_NAMES.get(mode)})

    def on_set_all_modes(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        mode = self._parse_mode(payload.get('mode'), MODE_BY_PRESET)
        self._set_all_modes(mode, save=True)
        self._register_settings_gui()
        self._reply(sender, {'ok': True, 'mode': mode, 'modeName': MODE_NAMES.get(mode)})

    def on_reload_modules(self, sender: str, data: Any, tag: str) -> None:
        self._reload_modules()
        self._register_settings_gui()
        self._reply(sender, {'ok': True, 'modules': self._serialize_modules()})

    def on_preview(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_payload(data)
        if payload is None:
            self._reply(sender, {'ok': False, 'error': 'payload must be object with text'})
            return

        text = str(payload.get('text') or '').strip()
        if not text:
            self._reply(sender, {'ok': False, 'error': 'text is required'})
            return

        context = self._build_context(payload)
        transformed = self._apply_modules(text, payload, context)
        self._reply(
            sender,
            {
                'ok': True,
                'text': transformed,
                'applied': list(payload.get('_morphersApplied') or []),
            },
        )

    def on_update_settings(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        changed = 0
        for module_id in self._module_order:
            if module_id not in payload:
                continue
            current = self._module_modes.get(module_id, MODE_BY_PRESET)
            next_mode = self._parse_mode(payload.get(module_id), current)
            if next_mode != current:
                self._set_module_mode(module_id, next_mode, save=False)
                changed += 1

        if changed > 0:
            self.save_properties()
            self._register_settings_gui()

        self._reply(sender, {'ok': True, 'changed': changed})

    def on_gui_all_on(self, sender: str, data: Any, tag: str) -> None:
        self._set_all_modes(MODE_ALWAYS, save=True)
        self._register_settings_gui()

    def on_gui_all_off(self, sender: str, data: Any, tag: str) -> None:
        self._set_all_modes(MODE_OFF, save=True)
        self._register_settings_gui()

    def on_gui_all_by_preset(self, sender: str, data: Any, tag: str) -> None:
        self._set_all_modes(MODE_BY_PRESET, save=True)
        self._register_settings_gui()

    def _request_runtime_context(self) -> None:
        self.send_message('talk:get-preset')
        self.send_message('character:get')

    def _register_command(self, tag: str, info: str) -> None:
        self.register_command(tag, info)

    def _speech_links_for_locale(self) -> List[Dict[str, Any]]:
        if self._locale.startswith('ru'):
            return [
                {
                    'eventName': 'speech:get',
                    'commandName': 'speech_morpher:list-modules',
                    'rule': '(покажи|список|list) (морферы|morphers|speech morphers)',
                },
                {
                    'eventName': 'speech:get',
                    'commandName': 'speech_morpher:get-state',
                    'rule': '(состояние|статус|state) (морферы|морферов|morphers|speech morphers)',
                },
                {
                    'eventName': 'speech:get',
                    'commandName': 'speech_morpher:set-all-modes',
                    'rule': '(включи|enable) (все )?(морферы|morphers|speech morphers)',
                    'msgData': {'mode': 'ALWAYS'},
                },
                {
                    'eventName': 'speech:get',
                    'commandName': 'speech_morpher:set-all-modes',
                    'rule': '(выключи|disable) (все )?(морферы|morphers|speech morphers)',
                    'msgData': {'mode': 'OFF'},
                },
                {
                    'eventName': 'speech:get',
                    'commandName': 'speech_morpher:set-all-modes',
                    'rule': '(режим|mode) (морферы|morphers|speech morphers) (по пресету|preset)',
                    'msgData': {'mode': 'BY_PRESET'},
                },
                {
                    'eventName': 'speech:get',
                    'commandName': 'speech_morpher:reload-modules',
                    'rule': '(перезагрузи|обнови|reload) (морферы|morphers|speech morphers)',
                },
            ]

        return [
            {
                'eventName': 'speech:get',
                'commandName': 'speech_morpher:list-modules',
                'rule': '(show|list) (morphers|speech morphers)',
            },
            {
                'eventName': 'speech:get',
                'commandName': 'speech_morpher:get-state',
                'rule': '(state|status) (morphers|speech morphers)',
            },
            {
                'eventName': 'speech:get',
                'commandName': 'speech_morpher:set-all-modes',
                'rule': '(enable|turn on) (all )?(morphers|speech morphers)',
                'msgData': {'mode': 'ALWAYS'},
            },
            {
                'eventName': 'speech:get',
                'commandName': 'speech_morpher:set-all-modes',
                'rule': '(disable|turn off) (all )?(morphers|speech morphers)',
                'msgData': {'mode': 'OFF'},
            },
            {
                'eventName': 'speech:get',
                'commandName': 'speech_morpher:set-all-modes',
                'rule': '(mode) (morphers|speech morphers) (preset)',
                'msgData': {'mode': 'BY_PRESET'},
            },
            {
                'eventName': 'speech:get',
                'commandName': 'speech_morpher:reload-modules',
                'rule': '(reload|refresh) (morphers|speech morphers)',
            },
        ]

    def _sync_speech_links(self) -> None:
        new_links = self._speech_links_for_locale()
        if new_links == self._speech_links:
            return
        self._clear_speech_links()
        for payload in new_links:
            self.set_event_link(
                str(payload.get('eventName') or 'speech:get'),
                str(payload.get('commandName') or ''),
                payload.get('rule'),
                payload.get('msgData'),
                {
                    str(key): value
                    for key, value in payload.items()
                    if key not in {'eventName', 'commandName', 'rule', 'msgData'}
                },
            )
        self._speech_links = [dict(payload) for payload in new_links]

    def _clear_speech_links(self) -> None:
        for payload in self._speech_links:
            self.remove_event_link(
                str(payload.get('eventName') or 'speech:get'),
                str(payload.get('commandName') or ''),
                payload.get('rule') or '',
            )
        self._speech_links = []

    def _load_rng_seed(self) -> None:
        raw = self.get_property('rngSeed', None)
        if raw is None:
            return
        try:
            self._rng_seed = int(raw)
            self._rng.seed(self._rng_seed)
        except Exception:
            self._rng_seed = None

    def _reload_modules(self) -> None:
        loaded: Dict[str, ModuleRecord] = {}

        for path in self._discover_module_files():
            record = self._load_module(path)
            if record is None:
                continue

            existing = loaded.get(record.module_id)
            if existing is not None:
                self.send_message(
                    'core-events:log',
                    {
                        'message': (
                            f'speech_morpher module override: {record.module_id} '
                            f'{existing.source_path} -> {record.source_path}'
                        )
                    },
                )
            loaded[record.module_id] = record

        self._modules = loaded
        self._module_order = sorted(
            self._modules.keys(),
            key=lambda module_id: (-self._modules[module_id].priority, module_id),
        )

        for module_id in self._module_order:
            self._module_modes[module_id] = self._read_module_mode(module_id)

        self.send_message(
            'core-events:log',
            {
                'message': (
                    f'speech_morpher modules loaded: {len(self._module_order)} '
                    f'[{", ".join(self._module_order)}]'
                )
            },
        )

    def _discover_module_files(self) -> List[str]:
        out: List[str] = []

        for base_dir in (self._builtin_modules_dir(), self._assets_modules_dir()):
            if not base_dir or not os.path.isdir(base_dir):
                continue
            for name in sorted(os.listdir(base_dir)):
                lower = name.lower()
                if lower in ('base.py',):
                    continue
                if not (lower.endswith('.py') or lower.endswith('.py3')):
                    continue
                if name.startswith('_'):
                    continue
                out.append(os.path.join(base_dir, name))

        return out

    def _builtin_modules_dir(self) -> str:
        plugin_dir = str(self.info.get('pluginDirPath') or '').strip()
        if plugin_dir:
            return os.path.join(plugin_dir, 'modules')
        return _MODULES_DIR

    def _assets_modules_dir(self) -> str:
        root_dir = str(self.info.get('rootDirPath') or '').strip()
        if not root_dir:
            return ''
        return os.path.join(root_dir, 'assets', 'speech_morphers')

    def _load_module(self, path: str) -> Optional[ModuleRecord]:
        module_name = re.sub(r'[^a-zA-Z0-9_]', '_', os.path.abspath(path))
        unique_name = f'speech_morpher_dynamic_{module_name}_{int(time.time() * 1000)}'

        try:
            loader = importlib.machinery.SourceFileLoader(unique_name, path)
            spec = importlib.util.spec_from_loader(unique_name, loader)
            if spec is None:
                raise RuntimeError('spec creation failed')
            module = importlib.util.module_from_spec(spec)
            loader.exec_module(module)

            instance = self._instantiate_module(module)
            if instance is None:
                raise RuntimeError('module must expose create_module() or MODULE_CLASS')

            module_id = str(getattr(instance, 'module_id', '')).strip().lower()
            if not module_id:
                raise RuntimeError('module_id is required')
            if not re.match(r'^[a-z0-9_.-]+$', module_id):
                raise RuntimeError(f'invalid module_id: {module_id}')

            if not hasattr(instance, 'apply'):
                raise RuntimeError('apply(text, payload, context) is required')
            if not hasattr(instance, 'is_active'):
                raise RuntimeError('is_active(context) is required')

            if hasattr(instance, 'initialize'):
                instance.initialize(self._bridge)

            priority = int(getattr(instance, 'priority', 0) or 0)
            return ModuleRecord(
                module_id=module_id,
                source_path=path,
                priority=priority,
                instance=instance,
            )
        except Exception as error:
            self.send_message(
                'core-events:error',
                {'message': f'speech_morpher failed to load module {path}: {error}'},
            )
            return None

    def _instantiate_module(self, module: Any) -> Optional[Any]:
        factory = getattr(module, 'create_module', None)
        if callable(factory):
            return factory()

        klass = getattr(module, 'MODULE_CLASS', None)
        if klass is not None:
            return klass()

        for name in dir(module):
            value = getattr(module, name)
            if not isinstance(value, type):
                continue
            if not hasattr(value, 'apply'):
                continue
            if not hasattr(value, 'is_active'):
                continue
            if name.lower().endswith('morpher'):
                return value()
        return None

    def _serialize_modules(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for module_id in self._module_order:
            record = self._modules[module_id]
            mode = self._module_modes.get(module_id, MODE_BY_PRESET)
            display_name = module_id
            if hasattr(record.instance, 'display_name'):
                try:
                    display_name = str(record.instance.display_name(self._locale) or module_id)
                except Exception:
                    display_name = module_id

            out.append(
                {
                    'id': module_id,
                    'name': display_name,
                    'priority': record.priority,
                    'mode': mode,
                    'modeName': MODE_NAMES.get(mode, 'BY_PRESET'),
                    'source': record.source_path,
                }
            )
        return out

    def _read_module_mode(self, module_id: str) -> int:
        key = f'module.{module_id}.mode'
        return self._parse_mode(self.get_property(key, MODE_BY_PRESET), MODE_BY_PRESET)

    def _set_module_mode(self, module_id: str, mode: int, save: bool) -> None:
        self._module_modes[module_id] = mode
        self.set_property(f'module.{module_id}.mode', mode)
        if save:
            self.save_properties()

    def _set_all_modes(self, mode: int, save: bool) -> None:
        for module_id in self._module_order:
            self._module_modes[module_id] = mode
            self.set_property(f'module.{module_id}.mode', mode)
        if save:
            self.save_properties()

    def _register_settings_gui(self) -> None:
        controls: List[Dict[str, Any]] = [
            {
                'id': 'speech_morpher_note',
                'type': 'label',
                'label': 'Режимы: 0=OFF, 1=BY_PRESET, 2=ALWAYS',
            }
        ]

        for module_id in self._module_order:
            record = self._modules[module_id]
            label = module_id
            if hasattr(record.instance, 'display_name'):
                try:
                    label = str(record.instance.display_name(self._locale) or module_id)
                except Exception:
                    label = module_id
            controls.append(
                {
                    'id': module_id,
                    'type': 'spinner',
                    'label': f'{label} ({module_id})',
                    'min': 0,
                    'max': 2,
                    'step': 1,
                    'value': self._module_modes.get(module_id, MODE_BY_PRESET),
                }
            )

        controls.extend(
            [
                {'id': 'speech_morpher_btn_all_on', 'type': 'button', 'label': 'Включить все', 'msgTag': 'speech_morpher:gui-all-on'},
                {'id': 'speech_morpher_btn_all_off', 'type': 'button', 'label': 'Выключить все', 'msgTag': 'speech_morpher:gui-all-off'},
                {
                    'id': 'speech_morpher_btn_all_preset',
                    'type': 'button',
                    'label': 'Все в режим BY_PRESET',
                    'msgTag': 'speech_morpher:gui-all-by-preset',
                },
                {
                    'id': 'speech_morpher_btn_reload',
                    'type': 'button',
                    'label': 'Перезагрузить модули',
                    'msgTag': 'speech_morpher:reload-modules',
                },
            ]
        )

        self.setup_options_panel(
            panel_id='speech_morpher_settings',
            name='Трансформация речи',
            msg_tag='speech_morpher:update-settings',
            controls=controls,
            panel_type='submenu',
        )

    def _parse_mode(self, raw: Any, default: int) -> int:
        if isinstance(raw, bool):
            return default
        if isinstance(raw, (int, float)):
            value = int(raw)
            if value in (MODE_OFF, MODE_BY_PRESET, MODE_ALWAYS):
                return value
            return default

        text = str(raw or '').strip().upper()
        if not text:
            return default
        if text in ('OFF', '0'):
            return MODE_OFF
        if text in ('BY_PRESET', 'PRESET', '1'):
            return MODE_BY_PRESET
        if text in ('ALWAYS', 'ON', '2'):
            return MODE_ALWAYS
        return default

    def _build_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        incoming_preset = payload.get('preset')
        if isinstance(incoming_preset, dict):
            self._preset = self._normalize_preset_map(incoming_preset)

        incoming_locale = payload.get('locale')
        if incoming_locale is not None:
            self._locale = self._normalize_locale(incoming_locale)

        return {
            'traits': dict(self._preset.get('traits') or {}),
            'emotions': dict(self._preset.get('emotions') or {}),
            'character_id': self._character_id,
            'locale': self._locale,
            'rng': self._rng,
        }

    def _apply_modules(self, text: str, payload: Dict[str, Any], context: Dict[str, Any]) -> str:
        value = str(text or '')
        applied: List[str] = []

        for module_id in self._module_order:
            record = self._modules[module_id]
            mode = self._module_modes.get(module_id, MODE_BY_PRESET)
            if mode == MODE_OFF:
                continue

            should_apply = mode == MODE_ALWAYS
            if mode == MODE_BY_PRESET:
                try:
                    should_apply = bool(record.instance.is_active(context))
                except Exception as error:
                    self.send_message(
                        'core-events:error',
                        {'message': f'speech_morpher {module_id} is_active failed: {error}'},
                    )
                    should_apply = False

            if not should_apply:
                continue

            try:
                transformed = record.instance.apply(value, payload, context)
            except Exception as error:
                self.send_message(
                    'core-events:error',
                    {'message': f'speech_morpher {module_id} apply failed: {error}'},
                )
                continue

            if isinstance(transformed, str) and transformed.strip():
                value = transformed
                applied.append(module_id)

        payload['_morphersApplied'] = applied
        return value

    def _as_payload(self, data: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(data, dict):
            return None
        payload = dict(data)
        text = payload.get('text')
        if text is not None:
            payload['text'] = str(text)
        return payload

    def _normalize_preset_map(self, value: Any) -> Dict[str, Any]:
        base = {'traits': {}, 'emotions': {}}
        if not isinstance(value, dict):
            return base

        for scope in ('traits', 'emotions'):
            source = value.get(scope)
            if not isinstance(source, dict):
                continue
            out: Dict[str, float] = {}
            for raw_key, raw_value in source.items():
                key = str(raw_key or '').strip().lower()
                if not key:
                    continue
                try:
                    out[key] = float(raw_value)
                except Exception:
                    out[key] = 0.0
            base[scope] = out

        return base

    def _normalize_locale(self, value: Any) -> str:
        locale = str(value or '').strip().lower()
        if not locale:
            return 'en'
        return locale.split('_')[0].split('-')[0]

    def _as_map(self, data: Any) -> Dict[str, Any]:
        if isinstance(data, dict):
            return dict(data)
        return {}

    def _reply(self, sender: str, data: Any) -> None:
        self.reply(sender, data)


if __name__ == '__main__':
    run_plugin(SpeechMorpherPlugin)
