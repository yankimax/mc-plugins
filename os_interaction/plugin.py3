#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sdk_python'))
from minachan_sdk import MinaChanPlugin, run_plugin

DEFAULT_OPEN_ALIASES = {
    'terminal': '__TERMINAL__',
    'терминал': '__TERMINAL__',
    'explorer': '__FILE_MANAGER__',
    'проводник': '__FILE_MANAGER__',
    'google': 'https://www.google.com',
    'гугл': 'https://www.google.com',
    'yandex': 'https://yandex.ru',
    'яндекс': 'https://yandex.ru',
    'vk': 'https://vk.com',
    'вк': 'https://vk.com',
    'youtube': 'https://www.youtube.com',
    'ютуб': 'https://www.youtube.com',
}

SYSTEM_SNAPSHOT_TAG = 'system-runtime:get-snapshot'
SYSTEM_CHANGED_TAG = 'system-runtime:changed'


class OsInteractionPlugin(MinaChanPlugin):
    _TERMINAL_EDITOR_NAMES = {
        'vi',
        'vim',
        'nvim',
        'nano',
        'micro',
        'joe',
        'pico',
    }

    def __init__(self) -> None:
        super().__init__()
        self._ui_locale = 'en'
        self._system_snapshot: Dict[str, Any] = {}
        self._capabilities: Dict[str, bool] = {}
        self._capability_fallbacks: Dict[str, str] = {}
        self._operations: Dict[str, Dict[str, Any]] = {}

    def on_init(self) -> None:
        self._open_aliases = self._load_open_aliases()

        self.add_listener('system:open', self.on_open, listener_id='system_open')
        self.add_listener('system:close-app', self.on_close_app, listener_id='system_close')
        self.add_listener(
            'system:spawn-in-terminal',
            self.on_spawn_in_terminal,
            listener_id='system_spawn_in_terminal',
        )
        self.add_listener(
            'system:open-in-editor',
            self.on_open_in_editor,
            listener_id='system_open_in_editor',
        )
        self.add_listener(SYSTEM_CHANGED_TAG, self.on_system_snapshot_changed, listener_id='system_runtime_changed')
        self.add_listener(
            'gui:request-panels',
            self.on_request_panels,
            listener_id='system_control_request_panels',
        )
        self.add_listener(
            'system-control:update-settings',
            self.on_update_settings,
            listener_id='system_control_update_settings',
        )

        self.register_command(
            'system:open',
            {
                'en': 'Open URL/file/app using system_runtime',
                'ru': 'Открыть URL/файл/приложение через system_runtime',
            },
        )
        self.register_command(
            'system:close-app',
            {
                'en': 'Close app by process name using system_runtime',
                'ru': 'Закрыть приложение по имени процесса через system_runtime',
            },
        )
        self.register_command(
            'system:spawn-in-terminal',
            {
                'en': 'Spawn command inside a terminal window',
                'ru': 'Запустить команду внутри окна терминала',
            },
        )
        self.register_command(
            'system:open-in-editor',
            {
                'en': 'Open file path in preferred editor',
                'ru': 'Открыть путь в предпочтительном редакторе',
            },
        )

        self.add_locale_listener(
            self._on_locale_changed,
            default_locale='en',
        )
        self._request_system_snapshot()

    def on_request_panels(self, sender: str, data: Any, tag: str) -> None:
        self._request_system_snapshot()
        self._register_settings_gui()

    def on_open(self, sender: str, data: Any, tag: str) -> None:
        target = self._pick_target(data)
        if not target:
            return

        if not self._system_snapshot:
            self._request_system_snapshot()

        resolved = self._resolve_open_target(target)

        ok = False
        reason = ''

        if resolved == '__FILE_MANAGER__':
            ok, reason = self._open_file_manager()
        elif resolved == '__TERMINAL__':
            ok, reason = self._open_terminal()
        elif resolved.startswith('http://') or resolved.startswith('https://'):
            ok, reason = self._open_url(resolved)
        elif os.path.exists(resolved):
            ok, reason = self._open_path(resolved)
        else:
            ok, reason = self._run_command(resolved)

        self.request_say_intent(
            'SYSTEM_OPENED' if ok else 'SYSTEM_OPEN_FAILED',
            template_vars={'target': target, 'reason': reason},
            extra={
                'target': target,
                'reason': reason,
            },
        )

    def on_close_app(self, sender: str, data: Any, tag: str) -> None:
        name = ''
        if isinstance(data, dict):
            name = str(data.get('name') or data.get('target') or '').strip()
        elif isinstance(data, str):
            name = data.strip()
        if not name:
            return

        if not self._system_snapshot:
            self._request_system_snapshot()

        resolved = self._resolve_open_target(name)
        ok, reason = self._close_process(resolved)
        self.request_say_intent(
            'SYSTEM_CLOSED' if ok else 'SYSTEM_CLOSE_FAILED',
            template_vars={'target': name, 'reason': reason},
            extra={
                'target': name,
                'reason': reason,
            },
        )

    def on_spawn_in_terminal(self, sender: str, data: Any, tag: str) -> None:
        argv = self._extract_argv(data)
        if not argv:
            self._reply_action_result(
                sender,
                ok=False,
                reason='empty argv',
                extra={'argv': []},
            )
            return
        ok, reason = self._launch_terminal_command(argv)
        self._reply_action_result(
            sender,
            ok=ok,
            reason=reason,
            extra={'argv': argv},
        )

    def on_open_in_editor(self, sender: str, data: Any, tag: str) -> None:
        path = self._extract_path(data)
        if not path:
            self._reply_action_result(
                sender,
                ok=False,
                reason='empty path',
                extra={'path': ''},
            )
            return
        ok, reason = self._open_in_editor(path)
        self._reply_action_result(
            sender,
            ok=ok,
            reason=reason,
            extra={'path': path},
        )

    def on_update_settings(self, sender: str, data: Any, tag: str) -> None:
        if not isinstance(data, dict):
            return
        aliases_text = str(data.get('open_aliases') or '').strip()
        parsed = self._parse_aliases_text(aliases_text)
        if not parsed:
            self.request_say_intent('SYSTEM_SETTINGS_INVALID_ALIASES')
            return

        self._open_aliases = parsed
        self.set_property('openAliases', self._open_aliases)
        self.save_properties()
        self._register_settings_gui()
        self.request_say_intent(
            'SYSTEM_SETTINGS_SAVED',
            template_vars={'count': len(self._open_aliases)},
            extra={'count': len(self._open_aliases)},
        )

    def on_system_snapshot_changed(self, sender: str, data: Any, tag: str) -> None:
        self._consume_system_payload(data)

    def _on_locale_changed(self, locale: str, chain: List[str]) -> None:
        self._ui_locale = locale
        self._register_settings_gui()

    def _register_settings_gui(self) -> None:
        texts = self._ui_texts()
        self.setup_options_panel(
            panel_id='system_control_settings',
            name=texts['panel_name'],
            msg_tag='system-control:update-settings',
            controls=[
                {
                    'id': 'description',
                    'type': 'label',
                    'label': texts['description'],
                },
                {
                    'id': 'open_aliases',
                    'type': 'textarea',
                    'label': texts['open_aliases_label'],
                    'value': self._aliases_to_text(self._open_aliases),
                },
            ],
        )

    def _is_ru_locale(self) -> bool:
        return self._ui_locale.startswith('ru')

    def _request_system_snapshot(self) -> None:
        self.send_message(SYSTEM_SNAPSHOT_TAG)

    def _consume_system_payload(self, data: Any) -> bool:
        if not isinstance(data, dict):
            return False

        if isinstance(data.get('snapshot'), dict):
            self._apply_system_snapshot(data.get('snapshot'))
            return True

        if 'schemaVersion' in data and isinstance(data.get('capabilities'), dict):
            self._apply_system_snapshot(data)
            return True

        if isinstance(data.get('capabilities'), dict) and isinstance(data.get('operations'), dict):
            merged = dict(self._system_snapshot)
            merged['capabilities'] = data.get('capabilities')
            merged['operations'] = data.get('operations')
            merged['capabilityFallbacks'] = data.get('capabilityFallbacks')
            merged['criticalDependencies'] = data.get('criticalDependencies')
            merged['environmentRestrictions'] = data.get('environmentRestrictions')
            self._apply_system_snapshot(merged)
            return True

        return False

    def _apply_system_snapshot(self, snapshot: Any) -> None:
        if not isinstance(snapshot, dict):
            return
        self._system_snapshot = dict(snapshot)

        raw_caps = snapshot.get('capabilities')
        if isinstance(raw_caps, dict):
            self._capabilities = {str(key): bool(value is True) for key, value in raw_caps.items()}
        else:
            self._capabilities = {}

        raw_fallbacks = snapshot.get('capabilityFallbacks')
        if isinstance(raw_fallbacks, dict):
            self._capability_fallbacks = {
                str(key): str(value or '').strip() for key, value in raw_fallbacks.items() if str(key).strip()
            }
        else:
            self._capability_fallbacks = {}

        raw_operations = snapshot.get('operations')
        if isinstance(raw_operations, dict):
            out: Dict[str, Dict[str, Any]] = {}
            for key, value in raw_operations.items():
                if isinstance(value, dict):
                    out[str(key)] = dict(value)
            self._operations = out
        else:
            self._operations = {}

    def _has_capability(self, name: str) -> bool:
        return bool(self._capabilities.get(name) is True)

    def _fallback_reason(self, capability: str, default: str) -> str:
        text = str(self._capability_fallbacks.get(capability) or '').strip()
        return text or default

    def _ui_texts(self) -> Dict[str, str]:
        if self._is_ru_locale():
            return {
                'panel_name': 'Системное управление',
                'open_aliases_label': 'Алиасы открытия',
                'description': (
                    'Формат алиасов: одна строка = одна пара phrase = command\n'
                    'Примеры:\n'
                    'терминал = __TERMINAL__\n'
                    'ютуб = https://www.youtube.com'
                ),
            }
        return {
            'panel_name': 'System control',
            'open_aliases_label': 'Open aliases',
            'description': (
                'Aliases format: one per line -> phrase = command\n'
                'Examples:\n'
                'terminal = __TERMINAL__\n'
                'youtube = https://www.youtube.com'
            ),
        }

    def _load_open_aliases(self) -> Dict[str, str]:
        raw = self.get_property('openAliases')
        parsed = self._normalize_aliases(raw)
        if parsed:
            return parsed
        self.set_property('openAliases', DEFAULT_OPEN_ALIASES)
        self.save_properties()
        return dict(DEFAULT_OPEN_ALIASES)

    def _normalize_aliases(self, raw: Any) -> Dict[str, str]:
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, str] = {}
        for key, value in raw.items():
            alias = str(key).strip().lower()
            command = str(value).strip()
            if alias and command:
                out[alias] = command
        return out

    def _parse_aliases_text(self, text: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for line in text.splitlines():
            value = line.strip()
            if not value or value.startswith('#'):
                continue
            if '=' not in value:
                continue
            alias, command = value.split('=', 1)
            alias = alias.strip().lower()
            command = command.strip()
            if alias and command:
                out[alias] = command
        return out

    def _aliases_to_text(self, aliases: Dict[str, str]) -> str:
        lines = []
        for alias in sorted(aliases.keys()):
            lines.append(f'{alias} = {aliases[alias]}')
        return '\n'.join(lines)

    def _resolve_open_target(self, target: str) -> str:
        value = target.strip()
        if not value:
            return value
        lowered = value.lower()
        if lowered in self._open_aliases:
            return self._open_aliases[lowered]

        simplified = re.sub(r'^(открой|open)\s+', '', lowered).strip()
        if simplified in self._open_aliases:
            return self._open_aliases[simplified]
        return value

    def _pick_target(self, data: Any) -> str:
        if isinstance(data, str):
            return data.strip()
        if isinstance(data, dict):
            return str(data.get('target') or data.get('value') or data.get('text') or '').strip()
        return ''

    def _extract_argv(self, data: Any) -> List[str]:
        if isinstance(data, dict):
            raw = data.get('argv')
            if isinstance(raw, list):
                return [str(item).strip() for item in raw if str(item).strip()]
            command = str(data.get('command') or '').strip()
            if command:
                try:
                    return [part for part in shlex.split(command) if str(part).strip()]
                except Exception:
                    return []
        if isinstance(data, str):
            try:
                return [part for part in shlex.split(data) if str(part).strip()]
            except Exception:
                return []
        return []

    def _extract_path(self, data: Any) -> str:
        if isinstance(data, dict):
            return str(data.get('path') or data.get('target') or data.get('value') or '').strip()
        if isinstance(data, str):
            return data.strip()
        return ''

    def _open_file_manager(self) -> tuple[bool, str]:
        return self._open_path(os.path.expanduser('~'))

    def _open_terminal(self) -> tuple[bool, str]:
        if not self._has_capability('system.openTerminal'):
            return False, self._fallback_reason('system.openTerminal', 'terminal capability is unavailable')
        operation = self._operations.get('openTerminal')
        if not operation:
            return False, 'openTerminal operation is missing in system snapshot'
        ok = self._execute_operation(operation, values={}, wait=False)
        return (ok, '' if ok else 'failed to execute openTerminal operation')

    def _open_url(self, url: str) -> tuple[bool, str]:
        if not self._has_capability('system.openUrl'):
            return False, self._fallback_reason('system.openUrl', 'openUrl capability is unavailable')
        operation = self._operations.get('openPath')
        if not operation:
            return False, 'openPath operation is missing in system snapshot'
        ok = self._execute_operation(operation, values={'path': url}, wait=False)
        return (ok, '' if ok else 'failed to execute openPath operation for URL')

    def _open_path(self, path: str) -> tuple[bool, str]:
        if not self._has_capability('system.openPath'):
            return False, self._fallback_reason('system.openPath', 'openPath capability is unavailable')
        operation = self._operations.get('openPath')
        if not operation:
            return False, 'openPath operation is missing in system snapshot'
        ok = self._execute_operation(operation, values={'path': path}, wait=False)
        return (ok, '' if ok else 'failed to execute openPath operation')

    def _run_command(self, command: str) -> tuple[bool, str]:
        command = command.strip()
        if not command:
            return False, 'empty command'
        if not self._has_capability('process.spawnDetached'):
            return False, self._fallback_reason('process.spawnDetached', 'spawnDetached capability is unavailable')

        operation = self._operations.get('spawnDetached')
        if not operation:
            return False, 'spawnDetached operation is missing in system snapshot'

        try:
            if shutil.which(command):
                ok = self._execute_operation(operation, values={'argv': [command]}, wait=False)
                return (ok, '' if ok else 'failed to execute spawnDetached operation')
            parts = shlex.split(command)
            if not parts:
                return False, 'invalid command syntax'
            ok = self._execute_operation(operation, values={'argv': parts}, wait=False)
            return (ok, '' if ok else 'failed to execute spawnDetached operation')
        except Exception:
            return False, 'failed to parse command'

    def _spawn_detached(self, args: list[str]) -> bool:
        try:
            subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except Exception:
            return False

    def _launch_terminal_command(self, argv: Sequence[str]) -> tuple[bool, str]:
        if not argv:
            return False, 'empty command'
        if not self._system_snapshot:
            self._request_system_snapshot()
        if not self._has_capability('system.runInTerminal'):
            return False, self._fallback_reason('system.runInTerminal', 'terminal command capability is unavailable')

        operation = self._operations.get('runInTerminal')
        if not operation:
            return False, 'runInTerminal operation is missing in system snapshot'

        argv_list = [str(item) for item in argv if str(item).strip()]
        if not argv_list:
            return False, 'empty command'

        command_text = shlex.join(argv_list)
        ok = self._execute_operation(
            operation,
            values={
                'argv': argv_list,
                'commandLine': subprocess.list2cmdline(argv_list),
                'shellCommand': command_text,
                'appleScriptCommand': self._apple_script_string(command_text),
            },
            wait=False,
        )
        return (ok, '' if ok else 'failed to execute runInTerminal operation')

    def _open_in_editor(self, path: str) -> tuple[bool, str]:
        target = str(path or '').strip()
        if not target:
            return False, 'empty path'

        editor = self._preferred_editor()
        if editor:
            if self._is_terminal_editor(editor):
                return self._launch_terminal_command([*editor, target])
            if self._spawn_detached([*editor, target]):
                return True, ''
            return False, 'failed to start editor'

        return self._open_path(target)

    def _preferred_editor(self) -> List[str]:
        for env_key in ('VISUAL', 'EDITOR'):
            raw = str(os.environ.get(env_key) or '').strip()
            if not raw:
                continue
            try:
                parts = shlex.split(raw)
            except Exception:
                parts = [raw]
            if parts:
                return parts
        return []

    def _is_terminal_editor(self, argv: Sequence[str]) -> bool:
        if not argv:
            return False
        name = os.path.basename(str(argv[0] or '').strip()).lower()
        return name in self._TERMINAL_EDITOR_NAMES

    def _apple_script_string(self, value: str) -> str:
        escaped = str(value or '').replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'

    def _close_process(self, name: str) -> tuple[bool, str]:
        if not name:
            return False, 'empty process name'

        if not self._has_capability('system.closeProcessByName'):
            return (
                False,
                self._fallback_reason('system.closeProcessByName', 'closeProcessByName capability is unavailable'),
            )

        process_name = name.strip()
        if ' ' in process_name:
            try:
                parts = shlex.split(process_name)
                if parts:
                    process_name = os.path.basename(parts[0])
            except Exception:
                process_name = process_name.split(' ', 1)[0]

        operation = self._operations.get('closeProcessByName')
        if not operation:
            return False, 'closeProcessByName operation is missing in system snapshot'

        normalized_name = self._normalize_process_name_for_operation(
            process_name=process_name,
            operation=operation,
        )
        ok = self._execute_operation(operation, values={'name': normalized_name}, wait=True)
        return (ok, '' if ok else 'failed to execute closeProcessByName operation')

    def _normalize_process_name_for_operation(
        self,
        *,
        process_name: str,
        operation: Dict[str, Any],
    ) -> str:
        value = str(process_name or '').strip()
        if not value:
            return ''
        args = operation.get('args')
        if not isinstance(args, list):
            return value

        needs_exe_suffix = False
        for token in args:
            text = str(token or '').lower()
            if '{name}.exe' in text:
                needs_exe_suffix = True
                break

        if needs_exe_suffix and value.lower().endswith('.exe'):
            return value[:-4]
        return value

    def _execute_operation(
        self,
        operation: Dict[str, Any],
        *,
        values: Dict[str, Any],
        wait: bool,
    ) -> bool:
        strategy = str(operation.get('strategy') or '').strip().lower()
        if strategy == 'startfile':
            path = str(values.get('path') or '').strip()
            if not path:
                return False
            startfile = getattr(os, 'startfile', None)
            if not callable(startfile):
                return False
            try:
                startfile(path)  # type: ignore[misc]
                return True
            except Exception:
                return False

        if strategy == 'spawndetached':
            argv_raw = values.get('argv')
            if not isinstance(argv_raw, list):
                return False
            args = [str(item) for item in argv_raw if str(item).strip()]
            if not args:
                return False
            return self._spawn_detached(args)

        if strategy != 'command':
            return False

        command = str(operation.get('command') or '').strip()
        if not command:
            return False

        rendered_args = self._render_operation_args(operation.get('args'), values)
        cmd = [command, *rendered_args]

        try:
            if wait:
                return subprocess.call(cmd) == 0
            return self._spawn_detached(cmd)
        except Exception:
            return False

    def _render_operation_args(self, raw_args: Any, values: Dict[str, Any]) -> list[str]:
        if not isinstance(raw_args, list):
            return []
        out: list[str] = []
        for token in raw_args:
            text = str(token or '')
            if text == '{argv}':
                argv_raw = values.get('argv')
                if isinstance(argv_raw, list):
                    out.extend(str(item) for item in argv_raw if str(item).strip())
                continue
            for key, value in values.items():
                text = text.replace('{' + str(key) + '}', str(value))
            out.append(text)
        return out

    def _reply_action_result(
        self,
        sender: str,
        *,
        ok: bool,
        reason: str = '',
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not sender:
            return
        payload: Dict[str, Any] = {'ok': bool(ok)}
        text = str(reason or '').strip()
        if text:
            payload['reason'] = text
        if isinstance(extra, dict):
            payload.update(extra)
        self.reply(sender, payload)


if __name__ == '__main__':
    run_plugin(OsInteractionPlugin)
