#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys
from typing import Any, Dict, List

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from minachan_sdk import MinaChanPlugin, run_plugin


class EmotionMenuPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._ui_locale = 'ru'
        self._labels: List[str] = []
        self._emotions: List[str] = []
        self._refresh_rule = ''
        self._last_rebuild_signature = ''

    def on_init(self) -> None:
        self.add_listener('emotion:test:set', self.on_set_emotion, listener_id='cmd_set_emotion')
        self.add_listener('emotion:test:refresh-menu', self.on_refresh_menu, listener_id='cmd_refresh_menu')
        self.add_listener('gui-events:skin-changed', self.on_skin_changed, listener_id='evt_skin_changed')

        reply_tag = str(self.info.get('id', 'emotion_menu'))
        self.add_listener(reply_tag, self.on_emotions_reply, listener_id='emotions_reply')

        self.register_command(
            'emotion:test:set',
            {
                'en': 'Set emotion from dynamic menu',
                'ru': 'Установить эмоцию из динамического меню',
            },
        )
        self.register_command(
            'emotion:test:refresh-menu',
            {
                'en': 'Refresh emotion test menu',
                'ru': 'Обновить тестовое меню эмоций',
            },
        )

        self.add_locale_listener(
            self._on_locale_changed,
            default_locale='ru',
        )

        self._request_emotions()

    def on_unload(self) -> None:
        self._clear_refresh_menu_link()
        self._clear_old_links()

    def on_set_emotion(self, sender: str, data: Any, tag: str) -> None:
        emotion = self._extract_emotion(data)
        if not emotion:
            return
        # For manual emotion testing we want random frame selection.
        self.send_message(
            'gui:set-emotion',
            {
                'emotion': emotion,
                'random': True,
                'manualOverride': True,
                'source': 'emotion_menu',
            },
        )

    def on_refresh_menu(self, sender: str, data: Any, tag: str) -> None:
        self._request_emotions()

    def on_skin_changed(self, sender: str, data: Any, tag: str) -> None:
        self._rebuild_menu(data)

    def on_emotions_reply(self, sender: str, data: Any, tag: str) -> None:
        self._rebuild_menu(data)

    def _on_locale_changed(self, locale: str, chain: List[str]) -> None:
        if locale != self._ui_locale or not self._refresh_rule:
            self._ui_locale = locale
            self._sync_refresh_menu_link()
            self._sync_emotion_links(self._emotions)

    def _request_emotions(self) -> None:
        self.send_message('gui:get-emotions', None)

    def _rebuild_menu(self, payload: Any) -> None:
        data = payload if isinstance(payload, dict) else {}
        skin_type_raw = data.get('skinType')
        skin_type = skin_type_raw.strip().lower() if isinstance(skin_type_raw, str) else ''
        emotions_raw = data.get('emotions')
        emotions = self._normalize_emotions(emotions_raw)
        if skin_type == 'live2d':
            if not emotions:
                emotions = self._normalize_emotions(data.get('live2dExpressions'))
            emotions.extend(self._normalize_emotions(data.get('live2dMotionGroups')))
            # Fallback: read expressions directly from the model manifest when
            # payload is stale (e.g. only ["normal"] right after startup).
            normalized_unique = sorted(set(emotions))
            if not emotions or normalized_unique == ['normal']:
                emotions.extend(self._load_emotions_from_live2d(data))
        elif not emotions:
            emotions = self._load_emotions_from_skin(data)

        if not emotions:
            emotions = ['normal']

        unique = sorted(set(emotions))
        signature = f'{skin_type}|{",".join(unique)}'
        if signature != self._last_rebuild_signature:
            self._last_rebuild_signature = signature
        self._sync_emotion_links(unique)

    def _clear_old_links(self) -> None:
        for label in self._labels:
            self.remove_event_link(
                'gui:menu-action',
                'emotion:test:set',
                rule=label,
            )
        self._labels = []

    def _sync_emotion_links(self, emotions: List[str]) -> None:
        unique = sorted(set(emotions))
        labels = [self._label_for(emotion) for emotion in unique]
        if labels == self._labels:
            self._emotions = unique
            return

        self._clear_old_links()

        for emotion in unique:
            self.set_event_link(
                'gui:menu-action',
                'emotion:test:set',
                rule=self._label_for(emotion),
                msg_data={'emotion': emotion},
            )

        self._emotions = unique
        self._labels = labels

    def _extract_emotion(self, data: Any) -> str:
        if isinstance(data, str):
            return data.strip().lower()
        if isinstance(data, dict):
            value = data.get('emotion') or data.get('value')
            if isinstance(value, str):
                return value.strip().lower()
        return ''

    def _label_for(self, emotion: str) -> str:
        return f'{self._emotion_root_label()}/{emotion}'

    def _refresh_label(self) -> str:
        if self._is_ru_locale():
            return f'{self._emotion_root_label()}/Обновить список эмоций'
        return f'{self._emotion_root_label()}/Refresh emotion list'

    def _emotion_root_label(self) -> str:
        return 'Отладка/Эмоции' if self._is_ru_locale() else 'Debug/Emotions'

    def _sync_refresh_menu_link(self) -> None:
        new_rule = self._refresh_label()
        if new_rule == self._refresh_rule:
            return
        self._clear_refresh_menu_link()
        self.set_event_link(
            'gui:menu-action',
            'emotion:test:refresh-menu',
            rule=new_rule,
        )
        self._refresh_rule = new_rule

    def _clear_refresh_menu_link(self) -> None:
        if not self._refresh_rule:
            return
        self.remove_event_link(
            'gui:menu-action',
            'emotion:test:refresh-menu',
            rule=self._refresh_rule,
        )
        self._refresh_rule = ''

    def _is_ru_locale(self) -> bool:
        return self._ui_locale.startswith('ru')

    def _normalize_emotions(self, raw: Any) -> List[str]:
        if not isinstance(raw, list):
            return []
        emotions: List[str] = []
        for value in raw:
            if not isinstance(value, str):
                continue
            name = value.strip().lower()
            if not name:
                continue
            emotions.append(name)
        return emotions

    def _load_emotions_from_live2d(self, payload: Dict[str, Any]) -> List[str]:
        model_path_raw = payload.get('modelPath')
        model_path = model_path_raw.strip() if isinstance(model_path_raw, str) else ''
        if not model_path:
            skin_path_raw = payload.get('skinPath')
            skin_path = skin_path_raw.strip() if isinstance(skin_path_raw, str) else ''
            if skin_path and os.path.isdir(skin_path):
                model_path = self._find_model3_json(skin_path)
        if not model_path or not os.path.isfile(model_path):
            return []
        try:
            with open(model_path, 'r', encoding='utf-8', errors='replace') as fh:
                decoded = json.loads(fh.read().strip() or '{}')
            if not isinstance(decoded, dict):
                return []
            refs = decoded.get('FileReferences')
            if not isinstance(refs, dict):
                return []
            expressions_raw = refs.get('Expressions')
            if not isinstance(expressions_raw, list):
                return []
            out: List[str] = ['normal']
            for item in expressions_raw:
                if not isinstance(item, dict):
                    continue
                name = item.get('Name') or item.get('name')
                if not isinstance(name, str):
                    continue
                normalized = name.strip().lower()
                if normalized and normalized not in out:
                    out.append(normalized)
            return out
        except Exception:
            return []

    def _find_model3_json(self, root: str) -> str:
        try:
            for name in os.listdir(root):
                lower = name.lower()
                if lower.endswith('.model3.json') or lower.endswith('.model.json'):
                    candidate = os.path.join(root, name)
                    if os.path.isfile(candidate):
                        return candidate
        except Exception:
            return ''
        return ''

    def _load_emotions_from_skin(self, payload: Dict[str, Any]) -> List[str]:
        root = payload.get('skinPath')
        if isinstance(root, str):
            root = root.strip()
        else:
            root = ''

        if not root:
            image_path = payload.get('imagePath')
            if isinstance(image_path, str):
                image_path = image_path.strip()
                if image_path:
                    root = os.path.dirname(os.path.dirname(image_path))

        if not root or not os.path.isdir(root):
            return []

        out: List[str] = []
        try:
            for name in os.listdir(root):
                path = os.path.join(root, name)
                if os.path.isdir(path):
                    out.append(name.strip().lower())
        except Exception:
            return []
        return out


if __name__ == '__main__':
    run_plugin(EmotionMenuPlugin)
