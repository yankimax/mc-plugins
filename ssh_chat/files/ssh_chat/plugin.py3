#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import pathlib
import shlex
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from minachan_sdk import MinaChanPlugin, run_plugin


@dataclass(frozen=True)
class SshIntent:
    mode: str
    host: str = ''
    port: Optional[int] = None


@dataclass(frozen=True)
class ParseResult:
    intent: Optional[SshIntent]
    error: str = ''


class SshChatPlugin(MinaChanPlugin):
    CMD_CONNECT = 'ssh:connect'
    CMD_OPEN_CONFIG = 'ssh:open-config'

    def __init__(self) -> None:
        super().__init__()
        self._ui_locale = 'ru'
        self._speech_links: List[Tuple[str, str]] = []

    def on_init(self) -> None:
        self.add_listener(self.CMD_CONNECT, self.on_connect, listener_id='ssh_connect')
        self.add_listener(self.CMD_OPEN_CONFIG, self.on_open_config, listener_id='ssh_open_config')

        self.register_command(
            self.CMD_CONNECT,
            {
                'en': 'Open SSH session in terminal',
                'ru': 'Открыть SSH-сессию в терминале',
            },
            {
                'request': {
                    'type': 'Text',
                    'label': {
                        'en': 'Raw ssh request, e.g. "10.0.0.1 2222" or "my-host"',
                        'ru': 'Строка ssh, например "10.0.0.1 2222" или "my-host"',
                    },
                },
                'host': {
                    'type': 'Text',
                    'label': {
                        'en': 'Optional structured host name',
                        'ru': 'Необязательное имя хоста в структурированном виде',
                    },
                },
                'port': {
                    'type': 'Number',
                    'label': {
                        'en': 'Optional structured port',
                        'ru': 'Необязательный порт в структурированном виде',
                    },
                },
            },
        )
        self.register_command(
            self.CMD_OPEN_CONFIG,
            {
                'en': 'Open ~/.ssh/config in editor',
                'ru': 'Открыть ~/.ssh/config в редакторе',
            },
        )

        self.add_locale_listener(
            self._on_locale_changed,
            default_locale='ru',
        )

    def on_unload(self) -> None:
        self._clear_speech_links()

    def on_connect(self, sender: str, data: Any, tag: str) -> None:
        parsed = self._parse_connect_payload(data)
        if parsed.intent is None:
            self._respond(
                sender,
                ok=False,
                text=parsed.error or self._usage_text(),
                extra={'reason': parsed.error or 'invalid request'},
            )
            return

        if parsed.intent.mode == 'config':
            self._open_config(sender)
            return

        intent = parsed.intent
        argv = self._ssh_argv(intent)
        self._request_system(
            'system:spawn-in-terminal',
            {'argv': argv},
            lambda response: self._handle_connect_response(
                sender=sender,
                response=response,
                intent=intent,
                argv=argv,
            ),
        )

    def on_open_config(self, sender: str, data: Any, tag: str) -> None:
        self._open_config(sender)

    def _on_locale_changed(self, locale: str, chain: List[str]) -> None:
        if locale != self._ui_locale or not self._speech_links:
            self._ui_locale = locale
            self._sync_speech_links()

    def _sync_speech_links(self) -> None:
        desired = self._speech_link_specs()
        if desired == self._speech_links:
            return
        self._clear_speech_links()
        for command, rule in desired:
            self.register_speech_rule(command, rule)
        self._speech_links = desired

    def _clear_speech_links(self) -> None:
        for command, rule in self._speech_links:
            self.remove_event_link('speech:get', command, rule=rule)
        self._speech_links = []

    def _speech_link_specs(self) -> List[Tuple[str, str]]:
        return [
            (self.CMD_OPEN_CONFIG, 'ssh config'),
            (self.CMD_CONNECT, 'ssh'),
            (self.CMD_CONNECT, 'ssh {request:Text}'),
        ]

    def _open_config(self, sender: str) -> None:
        config_path = self._ensure_ssh_config_file()
        self._request_system(
            'system:open-in-editor',
            {'path': str(config_path)},
            lambda response: self._handle_config_response(
                sender=sender,
                response=response,
                path=config_path,
            ),
        )

    def _parse_connect_payload(self, data: Any) -> ParseResult:
        if isinstance(data, dict):
            host = self._string(data.get('host') or data.get('target')).strip()
            port = self._parse_port_value(data.get('port'))
            if host:
                if port is None and data.get('port') not in (None, ''):
                    return ParseResult(None, self._invalid_port_text())
                return ParseResult(SshIntent(mode='connect', host=host, port=port))

        text = self.message_text(
            data,
            key='request',
            fallback_keys=['text', 'msgData', 'value', 'target'],
        ).strip()
        if not text:
            text = self.text(data).strip()
        return self._parse_connect_text(text)

    def _parse_connect_text(self, raw: str) -> ParseResult:
        text = self._string(raw).strip()
        if not text:
            return ParseResult(None, self._usage_text())

        try:
            tokens = shlex.split(text)
        except Exception:
            return ParseResult(None, self._tr('Invalid ssh syntax', 'Неверный синтаксис ssh'))

        if tokens and tokens[0].lower() == 'ssh':
            tokens = tokens[1:]
        if not tokens:
            return ParseResult(None, self._usage_text())

        if len(tokens) == 1 and tokens[0].strip().lower() == 'config':
            return ParseResult(SshIntent(mode='config'))

        host = ''
        port: Optional[int] = None
        index = 0
        while index < len(tokens):
            token = tokens[index].strip()
            lowered = token.lower()
            if lowered in ('-p', '--port'):
                if index + 1 >= len(tokens):
                    return ParseResult(None, self._invalid_port_text())
                parsed_port = self._parse_port_value(tokens[index + 1])
                if parsed_port is None:
                    return ParseResult(None, self._invalid_port_text())
                port = parsed_port
                index += 2
                continue
            if not host:
                host = token
                index += 1
                continue
            parsed_port = self._parse_port_value(token)
            if port is None and parsed_port is not None:
                port = parsed_port
                index += 1
                continue
            return ParseResult(
                None,
                self._tr(
                    f'Unexpected ssh argument: {token}',
                    f'Лишний аргумент ssh: {token}',
                ),
            )

        if not host:
            return ParseResult(None, self._usage_text())
        return ParseResult(SshIntent(mode='connect', host=host, port=port))

    def _parse_port_value(self, value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        try:
            port = int(str(value).strip())
        except Exception:
            return None
        if 1 <= port <= 65535:
            return port
        return None

    def _ssh_argv(self, intent: SshIntent) -> List[str]:
        argv = ['ssh']
        if intent.port is not None:
            argv.extend(['-p', str(intent.port)])
        argv.append(intent.host)
        return argv

    def _ensure_ssh_config_file(self) -> pathlib.Path:
        ssh_dir = pathlib.Path.home() / '.ssh'
        ssh_dir.mkdir(parents=True, exist_ok=True)
        try:
            ssh_dir.chmod(0o700)
        except Exception:
            pass
        config_path = ssh_dir / 'config'
        if not config_path.exists():
            config_path.touch()
        try:
            config_path.chmod(0o600)
        except Exception:
            pass
        return config_path

    def _request_system(self, command: str, payload: Dict[str, Any], callback) -> None:
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
            _finish({'ok': False, 'reason': f'Invalid response for {command}'})

        def _on_complete(sender: str, data: Any, tag: str) -> None:
            _finish({'ok': False, 'reason': f'No response for {command}'})

        seq = self.send_message_with_response(
            command,
            payload,
            on_response=_on_response,
            on_complete=_on_complete,
        )
        if seq < 0:
            _finish({'ok': False, 'reason': f'Failed to send {command}'})

    def _handle_connect_response(
        self,
        *,
        sender: str,
        response: Dict[str, Any],
        intent: SshIntent,
        argv: List[str],
    ) -> None:
        ok = bool(response.get('ok') is True)
        reason = self._string(response.get('reason')).strip()
        command_text = shlex.join(argv)
        if ok:
            self._respond(
                sender,
                ok=True,
                text=self._tr(
                    f'Opening SSH session: {command_text}',
                    f'Открываю SSH-сессию: {command_text}',
                ),
                extra={
                    'mode': 'connect',
                    'argv': argv,
                    'host': intent.host,
                    'port': intent.port,
                },
            )
            return
        self._respond(
            sender,
            ok=False,
            text=self._tr(
                f'Failed to open SSH session: {reason}',
                f'Не удалось открыть SSH-сессию: {reason}',
            ),
            extra={
                'mode': 'connect',
                'argv': argv,
                'host': intent.host,
                'port': intent.port,
                'reason': reason,
            },
        )

    def _handle_config_response(
        self,
        *,
        sender: str,
        response: Dict[str, Any],
        path: pathlib.Path,
    ) -> None:
        ok = bool(response.get('ok') is True)
        reason = self._string(response.get('reason')).strip()
        path_text = str(path)
        if ok:
            self._respond(
                sender,
                ok=True,
                text=self._tr(
                    f'Opening SSH config: {path_text}',
                    f'Открываю SSH config: {path_text}',
                ),
                extra={'mode': 'config', 'path': path_text},
            )
            return
        self._respond(
            sender,
            ok=False,
            text=self._tr(
                f'Failed to open SSH config: {reason}',
                f'Не удалось открыть SSH config: {reason}',
            ),
            extra={'mode': 'config', 'path': path_text, 'reason': reason},
        )

    def _respond(
        self,
        sender: str,
        *,
        ok: bool,
        text: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        message = self._string(text).strip()
        if message:
            self.request_say_direct(message)
        if sender:
            payload: Dict[str, Any] = {'ok': bool(ok), 'text': message}
            if isinstance(extra, dict):
                payload.update(extra)
            self.reply(sender, payload)

    def _usage_text(self) -> str:
        return self._tr(
            'Usage: ssh <host>, ssh <host> <port>, ssh <host> -P <port>, ssh <config-name>, ssh config',
            'Использование: ssh <host>, ssh <host> <port>, ssh <host> -P <port>, ssh <имя_из_config>, ssh config',
        )

    def _invalid_port_text(self) -> str:
        return self._tr(
            'Port must be an integer from 1 to 65535',
            'Порт должен быть целым числом от 1 до 65535',
        )

    def _tr(self, en: str, ru: str) -> str:
        return ru if self._is_ru_locale() else en

    def _is_ru_locale(self) -> bool:
        return self._ui_locale.startswith('ru')

    def _string(self, value: Any) -> str:
        return str(value or '')


if __name__ == '__main__':
    run_plugin(SshChatPlugin)
