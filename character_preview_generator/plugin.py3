#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sdk_python'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'tools'))

from minachan_sdk import MinaChanPlugin, run_plugin
import character_preview_generator


class CharacterPreviewGeneratorPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._ui_locale = 'ru'
        self._menu_rule = ''
        self._batch_token = 0
        self._batch_running = False
        self._batch_sender = ''
        self._batch_app_root: Optional[Path] = None
        self._batch_entries: List[character_preview_generator.CharacterAssetEntry] = []
        self._batch_index = 0
        self._batch_force = False
        self._batch_size = character_preview_generator.DEFAULT_PREVIEW_SIZE
        self._batch_padding = character_preview_generator.DEFAULT_PADDING
        self._batch_characters_covered = 0
        self._batch_unique_roots = 0
        self._batch_skipped_no_binding = 0
        self._batch_skipped_existing = 0
        self._batch_skipped_missing_source = 0
        self._batch_generated = 0
        self._batch_generated_placeholders = 0
        self._batch_generated_by_kind: Dict[str, int] = {
            'skin': 0,
            'live2d': 0,
            'spine': 0,
        }
        self._batch_captured = 0
        self._batch_fallback_generated = 0
        self._batch_warnings: List[str] = []
        self._initial_character_id = ''
        self._initial_visible = True

    def on_init(self) -> None:
        self.add_listener(
            'character-preview:generate-all',
            self.on_generate_all,
            listener_id='character_preview_generate_all',
        )
        self.register_command(
            'character-preview:generate-all',
            {
                'en': 'Generate live preview.png files for all characters',
                'ru': 'Сгенерировать живые preview.png для всех персонажей',
            },
        )
        self.add_locale_listener(
            self._on_locale_changed,
            default_locale='ru',
        )
        self._sync_menu_link()

    def on_unload(self) -> None:
        self._clear_menu_link()

    def on_generate_all(self, sender: str, data: Any, tag: str) -> None:
        if self._batch_running:
            self._reply(
                sender,
                {'ok': False, 'error': 'character preview generation already running'},
            )
            return

        root_dir = str(self.info.get('rootDirPath') or '').strip()
        if not root_dir:
            self._reply(sender, {'ok': False, 'error': 'rootDirPath is missing'})
            return

        payload = data if isinstance(data, dict) else {}
        force = self._payload_force(payload)
        size = self._to_int(payload.get('size'), character_preview_generator.DEFAULT_PREVIEW_SIZE)
        size = max(64, min(size, 4096))
        padding = self._to_int(payload.get('padding'), character_preview_generator.DEFAULT_PADDING)
        padding = max(0, min(padding, size // 3))
        character_ids = self._extract_character_ids(payload)
        app_root = Path(root_dir).resolve()

        discovery = character_preview_generator.collect_character_asset_entries(
            app_root,
            character_ids=character_ids,
        )
        entries = list(discovery.get('entries') or [])

        self._batch_token += 1
        self._batch_running = True
        self._batch_sender = sender
        self._batch_app_root = app_root
        self._batch_entries = entries
        self._batch_index = 0
        self._batch_force = force
        self._batch_size = size
        self._batch_padding = padding
        self._batch_characters_covered = int(discovery.get('charactersCovered') or 0)
        self._batch_unique_roots = int(discovery.get('uniqueRoots') or 0)
        self._batch_skipped_no_binding = int(discovery.get('skippedNoBinding') or 0)
        self._batch_skipped_existing = 0
        self._batch_skipped_missing_source = 0
        self._batch_generated = 0
        self._batch_generated_placeholders = 0
        self._batch_generated_by_kind = {'skin': 0, 'live2d': 0, 'spine': 0}
        self._batch_captured = 0
        self._batch_fallback_generated = 0
        self._batch_warnings = []
        self._initial_character_id = ''
        self._initial_visible = True

        token = self._batch_token
        self.send_message(
            'core-events:log',
            {
                'message': (
                    'character_preview_generator live batch start: '
                    f'uniqueRoots={self._batch_unique_roots} '
                    f'charactersCovered={self._batch_characters_covered} '
                    f'force={force} size={size} padding={padding}'
                )
            },
        )
        self._request_runtime('character:get', None, lambda response: self._on_initial_character(token, response))

    def _on_initial_character(self, token: int, response: Any) -> None:
        if token != self._batch_token or not self._batch_running:
            return
        if isinstance(response, dict):
            active = response.get('active') or response.get('id')
            self._initial_character_id = self._normalize_name(active)
        self._request_runtime(
            'gui:is-character-visible',
            None,
            lambda response: self._on_initial_visibility(token, response),
        )

    def _on_initial_visibility(self, token: int, response: Any) -> None:
        if token != self._batch_token or not self._batch_running:
            return
        self._initial_visible = self._to_bool(response) if response is not None else True
        self.send_message('gui:show-character')
        self._schedule(token, 120, self._process_next_entry)

    def _process_next_entry(self, token: int) -> None:
        if token != self._batch_token or not self._batch_running:
            return

        while self._batch_index < len(self._batch_entries):
            entry = self._batch_entries[self._batch_index]
            output_path = entry.root_dir / character_preview_generator.PREVIEW_FILE_NAME
            if output_path.exists() and not self._batch_force:
                self._batch_skipped_existing += 1
                self._batch_index += 1
                continue
            self._request_runtime(
                'character:set',
                {'id': entry.character_id},
                lambda response, current=entry: self._on_character_selected(token, current, response),
            )
            return

        self._finish_batch(token)

    def _on_character_selected(
        self,
        token: int,
        entry: character_preview_generator.CharacterAssetEntry,
        response: Any,
    ) -> None:
        if token != self._batch_token or not self._batch_running:
            return

        if isinstance(response, dict) and response.get('ok') is False:
            self._batch_warnings.append(
                f'{entry.character_id}: character:set failed: {response.get("error")}'
            )
            self._generate_entry_fallback(entry, reason='character:set failed')
            return

        self.send_message('gui:show-character')
        self.send_message('gui:set-emotion', {'emotion': 'normal'})
        self._schedule_capture(token, entry)

    def _schedule_capture(
        self,
        token: int,
        entry: character_preview_generator.CharacterAssetEntry,
    ) -> None:
        self._schedule(
            token,
            380,
            lambda current_token: self._capture_entry(current_token, entry),
        )

    def _capture_entry(
        self,
        token: int,
        entry: character_preview_generator.CharacterAssetEntry,
    ) -> None:
        if token != self._batch_token or not self._batch_running:
            return

        temp_path = Path(tempfile.gettempdir()) / (
            f'minachan_preview_capture_{os.getpid()}_{token}_{entry.character_id}.png'
        )
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass

        self._request_runtime(
            'ui:capture-character-preview',
            {
                'path': str(temp_path),
                'pixelRatio': 2.0,
                'settleMs': 260,
                'frames': 2,
                'waitForExpectedStateMs': 2200,
            },
            lambda response, current=entry, current_temp=temp_path: self._on_capture_complete(
                token,
                current,
                current_temp,
                response,
            ),
        )

    def _on_capture_complete(
        self,
        token: int,
        entry: character_preview_generator.CharacterAssetEntry,
        temp_path: Path,
        response: Any,
    ) -> None:
        if token != self._batch_token or not self._batch_running:
            self._cleanup_temp(temp_path)
            return

        output_path = entry.root_dir / character_preview_generator.PREVIEW_FILE_NAME
        capture_error = ''
        try:
            if isinstance(response, dict) and response.get('ok') and temp_path.is_file():
                character_preview_generator.render_preview(
                    temp_path,
                    output_path,
                    size=self._batch_size,
                    padding=self._batch_padding,
                )
                self._mark_generated(entry.kind, placeholder=False, captured=True)
            else:
                capture_error = self._capture_error_text(response)
                self._generate_entry_fallback(entry, reason=capture_error)
                return
        except Exception as error:
            capture_error = str(error)
            self._generate_entry_fallback(entry, reason=capture_error)
            return
        finally:
            self._cleanup_temp(temp_path)

        self._advance_batch(token, entry)

    def _generate_entry_fallback(
        self,
        entry: character_preview_generator.CharacterAssetEntry,
        *,
        reason: str,
    ) -> None:
        try:
            result = character_preview_generator.generate_preview_for_entry(
                entry,
                size=self._batch_size,
                padding=self._batch_padding,
            )
            self._batch_fallback_generated += 1
            self._mark_generated(
                entry.kind,
                placeholder=bool(result.get('placeholder')),
                captured=False,
            )
            normalized_reason = reason.strip()
            if normalized_reason:
                self._batch_warnings.append(
                    f'{entry.character_id}: live capture failed ({normalized_reason}); used asset fallback'
                )
            warning = str(result.get('warning') or '').strip()
            if warning:
                self._batch_warnings.append(warning)
        except Exception as error:
            self._batch_skipped_missing_source += 1
            self._batch_warnings.append(
                f'{entry.character_id}: preview generation failed after fallback: {error}'
            )
        self._advance_batch(self._batch_token, entry)

    def _advance_batch(
        self,
        token: int,
        entry: character_preview_generator.CharacterAssetEntry,
    ) -> None:
        if token != self._batch_token or not self._batch_running:
            return
        self._batch_index += 1
        if self._batch_index % 25 == 0 or self._batch_index == len(self._batch_entries):
            self.send_message(
                'core-events:log',
                {
                    'message': (
                        'character_preview_generator progress: '
                        f'{self._batch_index}/{len(self._batch_entries)} '
                        f'generated={self._batch_generated} '
                        f'captured={self._batch_captured} '
                        f'fallback={self._batch_fallback_generated}'
                    )
                },
            )
        self._schedule(token, 1, self._process_next_entry)

    def _finish_batch(self, token: int) -> None:
        if token != self._batch_token or not self._batch_running:
            return

        initial_character_id = self._initial_character_id
        initial_visible = self._initial_visible
        sender = self._batch_sender
        result = {
            'ok': True,
            'mode': 'live_capture',
            'charactersCovered': self._batch_characters_covered,
            'uniqueRoots': self._batch_unique_roots,
            'generated': self._batch_generated,
            'generatedPlaceholders': self._batch_generated_placeholders,
            'generatedByKind': dict(self._batch_generated_by_kind),
            'captured': self._batch_captured,
            'fallbackGenerated': self._batch_fallback_generated,
            'skippedExisting': self._batch_skipped_existing,
            'skippedMissingSource': self._batch_skipped_missing_source,
            'skippedNoBinding': self._batch_skipped_no_binding,
            'size': self._batch_size,
            'padding': self._batch_padding,
            'warnings': list(self._batch_warnings),
        }

        if initial_character_id:
            self.send_message('character:set', {'id': initial_character_id})
        if not initial_visible:
            self.send_message('gui:hide-character')

        summary = (
            'character_preview_generator live batch complete: '
            f'generated={self._batch_generated} '
            f'captured={self._batch_captured} '
            f'fallback={self._batch_fallback_generated} '
            f'skippedExisting={self._batch_skipped_existing} '
            f'skippedMissingSource={self._batch_skipped_missing_source}'
        )
        self.send_message('core-events:log', {'message': summary})
        if self._batch_warnings:
            preview = '; '.join(str(item) for item in self._batch_warnings[:5])
            self.send_message(
                'core-events:log',
                {
                    'message': (
                        f'character_preview_generator warnings={len(self._batch_warnings)} '
                        f'[{preview}]'
                    )
                },
            )

        self._batch_running = False
        self._batch_sender = ''
        self._batch_entries = []
        self._batch_index = 0
        self._reply(sender, result)

    def _request_runtime(
        self,
        tag: str,
        payload: Any,
        callback: Callable[[Any], None],
    ) -> None:
        responded = {'value': False}

        def _finish(data: Any) -> None:
            if responded['value']:
                return
            responded['value'] = True
            callback(data)

        def _on_response(sender: str, data: Any, in_tag: str) -> None:
            _finish(data)

        def _on_complete(sender: str, data: Any, in_tag: str) -> None:
            _finish(None)

        seq = self.send_message_with_response(
            tag,
            payload,
            on_response=_on_response,
            on_complete=_on_complete,
        )
        if seq < 0:
            _finish(None)

    def _schedule(
        self,
        token: int,
        delay_ms: int,
        callback: Callable[[int], None],
    ) -> None:
        self.set_timer_once(delay_ms, lambda *_: callback(token))

    def _mark_generated(self, kind: str, *, placeholder: bool, captured: bool) -> None:
        self._batch_generated += 1
        if placeholder:
            self._batch_generated_placeholders += 1
        self._batch_generated_by_kind[kind] = self._batch_generated_by_kind.get(kind, 0) + 1
        if captured:
            self._batch_captured += 1

    def _capture_error_text(self, response: Any) -> str:
        if isinstance(response, dict):
            return str(response.get('error') or 'unknown overlay capture error').strip()
        if response is None:
            return 'no response from overlay capture'
        return str(response).strip() or 'unknown overlay capture error'

    def _cleanup_temp(self, path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

    def _extract_character_ids(self, payload: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        raw_list = payload.get('characterIds')
        if isinstance(raw_list, list):
            for item in raw_list:
                text = self._normalize_name(item)
                if text:
                    out.append(text)
        single = self._normalize_name(payload.get('character'))
        if single:
            out.append(single)
        return out

    def _sync_menu_link(self) -> None:
        rule = self._menu_rule_text()
        if rule == self._menu_rule:
            return
        self._clear_menu_link()
        self.set_event_link(
            'gui:menu-action',
            'character-preview:generate-all',
            rule=rule,
        )
        self._menu_rule = rule

    def _clear_menu_link(self) -> None:
        if not self._menu_rule:
            return
        self.remove_event_link(
            'gui:menu-action',
            'character-preview:generate-all',
            rule=self._menu_rule,
        )
        self._menu_rule = ''

    def _menu_rule_text(self) -> str:
        if self._is_ru_locale():
            return 'Отладка/Генерация превью'
        return 'Debug/Preview Generation'

    def _reply(self, sender: str, payload: Any) -> None:
        if sender:
            self.reply(sender, payload)

    def _on_locale_changed(self, locale: str, chain) -> None:
        if locale != self._ui_locale:
            self._ui_locale = locale
            self._sync_menu_link()

    def _is_ru_locale(self) -> bool:
        return self._ui_locale.startswith('ru')

    def _normalize_name(self, value: Any) -> str:
        return str(value or '').strip().lower()

    def _to_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'on', 'y')
        return False

    def _to_int(self, value: Any, fallback: int) -> int:
        if isinstance(value, bool):
            return fallback
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except Exception:
                return fallback
        return fallback

    def _payload_force(self, payload: Dict[str, Any]) -> bool:
        if 'force' not in payload:
            return True
        return self._to_bool(payload.get('force'))


if __name__ == '__main__':
    run_plugin(CharacterPreviewGeneratorPlugin)
