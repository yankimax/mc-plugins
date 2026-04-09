#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
from typing import Any, Dict, List, Tuple

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from minachan_sdk import MinaChanPlugin, run_plugin

REACTIONS = {
    'touch': {
        'cooldown': 0.20,
        'intent': 'TOUCH_TAP',
    },
    'left_click': {
        'cooldown': 0.20,
        'intent': 'TOUCH_LEFT_CLICK',
    },
    'right_click': {
        'cooldown': 0.30,
        'intent': 'TOUCH_RIGHT_CLICK',
    },
    'middle_click': {
        'cooldown': 0.30,
        'intent': 'TOUCH_MIDDLE_CLICK',
    },
    'double_click': {
        'cooldown': 0.35,
        'intent': 'TOUCH_DOUBLE_CLICK',
    },
    'drag_start': {
        'cooldown': 0.30,
        'intent': 'TOUCH_DRAG_START',
    },
    'drag_move': {
        'cooldown': 2.00,
        'intent': 'TOUCH_DRAG_MOVE',
    },
    'drag_stop': {
        'cooldown': 0.30,
        'intent': 'TOUCH_DRAG_STOP',
    },
    'overlay_window_grab': {
        'cooldown': 0.30,
        'intent': 'TOUCH_WINDOW_GRAB',
    },
    'overlay_window_move': {
        'cooldown': 2.50,
        'intent': 'TOUCH_WINDOW_MOVE',
    },
    'overlay_window_release': {
        'cooldown': 0.40,
        'intent': 'TOUCH_WINDOW_RELEASE',
    },
    'scroll': {
        'cooldown': 0.90,
        'intent': 'TOUCH_SCROLL',
    },
}

TAG_TO_KIND = {
    'gui:character-touched': 'touch',
    'gui:character-clicked': 'left_click',
    'gui-events:character-left-click': 'left_click',
    'gui-events:character-right-click': 'right_click',
    'gui-events:character-middle-click': 'middle_click',
    'gui-events:character-double-click': 'double_click',
    'gui-events:character-start-drag': 'drag_start',
    'gui-events:character-drag': 'drag_move',
    'gui-events:character-stop-drag': 'drag_stop',
    'gui-events:overlay-window-grab': 'overlay_window_grab',
    'gui-events:overlay-window-move': 'overlay_window_move',
    'gui-events:overlay-window-release': 'overlay_window_release',
    'gui-events:character-scroll': 'scroll',
}

KIND_PHASE = {
    'drag_start': 'move_start',
    'overlay_window_grab': 'move_start',
    'drag_move': 'move_progress',
    'overlay_window_move': 'move_progress',
    'drag_stop': 'move_stop',
    'overlay_window_release': 'move_stop',
}

PHASE_DEDUPE_SECONDS = {
    'move_start': 0.40,
    'move_progress': 0.65,
    'move_stop': 0.40,
}

LIVE2D_STATE_EVENT_TAG = 'gui-events:skin-changed'
LIVE2D_STATE_REQUEST_TAG = 'gui:get-emotions'

LIVE2D_FEATURE_HINTS = {
    'touch': ('soft', 'smile', 'blush', 'doya', 'cool'),
    'left_click': ('smile', 'blush', 'soft', 'doya', 'cool', 'brow'),
    'right_click': ('hair', 'outfit', 'style', 'swap', 'cool', 'stern', 'dry'),
    'middle_click': ('focus', 'brow', 'cool', 'stern', 'dry'),
    'double_click': (
        'spell',
        'release',
        'charge',
        'ooface',
        'panic',
        'fright',
        'surprise',
        'happy',
    ),
    'drag_start': ('focus', 'stern', 'angry', 'dry', 'worried'),
    'drag_move': ('focus', 'stern', 'angry', 'dry', 'worried'),
    'drag_stop': ('soft', 'smile', 'cool', 'calm'),
    'overlay_window_grab': ('focus', 'stern', 'angry', 'dry', 'worried'),
    'overlay_window_move': ('focus', 'stern', 'angry', 'dry', 'worried'),
    'overlay_window_release': ('soft', 'smile', 'cool', 'calm'),
    'scroll': ('hair', 'outfit', 'style', 'swap', 'spell', 'cool', 'smile'),
}

LIVE2D_EMOTION_HINTS = {
    'touch': ('smile', 'blush', 'love', 'doya', 'cool', 'normal'),
    'left_click': ('love', 'smile', 'happy', 'blush', 'doya', 'cool'),
    'right_click': ('hair_change', 'outfit_change', 'cool', 'serious', 'worried', 'normal'),
    'middle_click': ('cool', 'serious', 'thoughtful', 'browlink', 'normal'),
    'double_click': ('surprised', 'ooface', 'frightened', 'happy', 'zx', 'w', 'anya2'),
    'drag_start': ('angry', 'serious', 'focus', 'ku', 'han', 'worried'),
    'drag_move': ('angry', 'serious', 'focus', 'ku', 'han', 'worried'),
    'drag_stop': ('normal', 'smile', 'cool', 'anya'),
    'overlay_window_grab': ('angry', 'serious', 'focus', 'ku', 'han', 'worried'),
    'overlay_window_move': ('angry', 'serious', 'focus', 'ku', 'han', 'worried'),
    'overlay_window_release': ('normal', 'smile', 'cool', 'anya'),
    'scroll': ('outfit_change', 'hair_change', 'cool', 'normal', 'smile'),
}


class ReactionsPlugin(MinaChanPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._last_event_by_kind: Dict[str, str] = {}
        self._last_ts_by_kind: Dict[str, float] = {}
        self._last_event_by_phase: Dict[str, str] = {}
        self._last_ts_by_phase: Dict[str, float] = {}
        self._is_live2d_active = False
        self._live2d_profile_id = ''
        self._live2d_emotions: List[str] = []
        self._live2d_features: Dict[str, Dict[str, str]] = {}
        self._last_live2d_feature_by_kind: Dict[str, str] = {}
        self._last_live2d_feature_ts_by_kind: Dict[str, float] = {}

    def on_init(self) -> None:
        plugin_id = str(self.info.get('id') or '').strip()
        if plugin_id:
            self.add_listener(
                plugin_id,
                self.on_gui_state_reply,
                listener_id='reactions_gui_state_reply',
            )
        self.add_listener(
            LIVE2D_STATE_EVENT_TAG,
            self.on_gui_state_event,
            listener_id='reactions_live2d_state_event',
        )
        for index, tag in enumerate(TAG_TO_KIND.keys()):
            self.add_listener(tag, self.on_gui_event, listener_id=f'gui_reaction_{index}')
        self.send_message(LIVE2D_STATE_REQUEST_TAG)

    def on_gui_state_event(self, sender: str, data: Any, tag: str) -> None:
        self._consume_gui_state(data)

    def on_gui_state_reply(self, sender: str, data: Any, tag: str) -> None:
        self._consume_gui_state(data)

    def on_gui_event(self, sender: str, data, tag: str) -> None:
        kind = TAG_TO_KIND.get(tag)
        if not kind:
            return
        reaction = REACTIONS.get(kind)
        if reaction is None:
            return
        if self._is_phase_duplicate(kind, data):
            return
        if self._is_rate_limited(kind, data, cooldown=float(reaction.get('cooldown') or 0.0)):
            return

        live2d_feature, live2d_emotion = self._apply_live2d_reaction(kind, data)
        intent = self._resolve_intent(kind, data, str(reaction.get('intent') or 'TOUCH'))
        vars_payload = {'reactionKind': kind}
        if live2d_feature:
            vars_payload['live2dFeature'] = live2d_feature
        if live2d_emotion:
            vars_payload['live2dEmotion'] = live2d_emotion
        if self._live2d_profile_id:
            vars_payload['live2dProfile'] = self._live2d_profile_id
        self.request_say_intent(intent, template_vars=vars_payload)

    def _consume_gui_state(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        if (
            'skinType' not in data
            and 'emotions' not in data
            and 'live2dFeatures' not in data
            and 'live2dProfileId' not in data
        ):
            return
        skin_type = str(data.get('skinType') or '').strip().lower()
        self._is_live2d_active = skin_type == 'live2d'
        if not self._is_live2d_active:
            self._live2d_profile_id = ''
            self._live2d_emotions = []
            self._live2d_features = {}
            self._last_live2d_feature_by_kind = {}
            self._last_live2d_feature_ts_by_kind = {}
            return

        self._live2d_profile_id = str(data.get('live2dProfileId') or '').strip().lower()
        emotion_names: List[str] = []
        emotion_names.extend(self._normalize_names(data.get('emotions')))
        emotion_names.extend(self._normalize_names(data.get('live2dExpressions')))
        emotion_names.extend(self._normalize_names(data.get('live2dMotionGroups')))
        seen = set()
        unique_emotions: List[str] = []
        for emotion in emotion_names:
            if emotion in seen:
                continue
            seen.add(emotion)
            unique_emotions.append(emotion)
        self._live2d_emotions = unique_emotions

        parsed_features: Dict[str, Dict[str, str]] = {}
        raw_features = data.get('live2dFeatures')
        if isinstance(raw_features, list):
            for raw in raw_features:
                if not isinstance(raw, dict):
                    continue
                feature_id = self._normalize_name(raw.get('id'))
                if not feature_id:
                    continue
                title = str(raw.get('title') or '').strip()
                description = str(raw.get('description') or '').strip()
                emotion = self._normalize_name(raw.get('emotion'))
                search_blob = self._to_search_blob(feature_id, title, description, emotion)
                parsed_features[feature_id] = {
                    'id': feature_id,
                    'title': title,
                    'description': description,
                    'emotion': emotion,
                    'search': search_blob,
                }
        self._live2d_features = parsed_features

    def _apply_live2d_reaction(self, kind: str, data: Any) -> Tuple[str, str]:
        if not self._is_live2d_active:
            return '', ''
        feature_id = self._pick_live2d_feature(kind)
        if feature_id:
            self.send_message(
                'gui:trigger-live2d-feature',
                {'id': feature_id, 'source': 'reactions', 'kind': kind},
            )
            emotion = str(self._live2d_features.get(feature_id, {}).get('emotion') or '').strip().lower()
            return feature_id, emotion

        emotion = self._pick_live2d_emotion(kind)
        if emotion:
            self.send_message(
                'gui:set-emotion',
                {'emotion': emotion, 'source': 'reactions_live2d', 'kind': kind},
            )
        return '', emotion

    def _pick_live2d_feature(self, kind: str) -> str:
        if not self._live2d_features:
            return ''
        hints = LIVE2D_FEATURE_HINTS.get(kind, ())
        if not hints:
            return ''
        emotion_hints = LIVE2D_EMOTION_HINTS.get(kind, ())
        ranked: List[Tuple[float, str]] = []
        for feature_id, feature in self._live2d_features.items():
            search = str(feature.get('search') or '')
            score = 0.0
            for index, hint in enumerate(hints):
                if hint and hint in search:
                    score += float(len(hints) - index)
            feature_emotion = str(feature.get('emotion') or '')
            if feature_emotion:
                for index, hint in enumerate(emotion_hints):
                    if self._hint_matches(feature_emotion, hint):
                        score += float(len(emotion_hints) - index) * 0.35
            if score > 0.0:
                ranked.append((score, feature_id))
        if not ranked:
            return ''

        ranked.sort(key=lambda item: (-item[0], item[1]))
        now = time.monotonic()
        last_feature = self._last_live2d_feature_by_kind.get(kind, '')
        last_ts = self._last_live2d_feature_ts_by_kind.get(kind, 0.0)

        for _, feature_id in ranked:
            if feature_id != last_feature or (now - last_ts) >= 1.20:
                self._last_live2d_feature_by_kind[kind] = feature_id
                self._last_live2d_feature_ts_by_kind[kind] = now
                return feature_id

        selected = ranked[0][1]
        self._last_live2d_feature_by_kind[kind] = selected
        self._last_live2d_feature_ts_by_kind[kind] = now
        return selected

    def _pick_live2d_emotion(self, kind: str) -> str:
        if not self._live2d_emotions:
            return ''
        hints = LIVE2D_EMOTION_HINTS.get(kind, ())
        for hint in hints:
            for emotion in self._live2d_emotions:
                if self._hint_matches(emotion, hint):
                    return emotion
        if 'normal' in self._live2d_emotions:
            return 'normal'
        return self._live2d_emotions[0]

    def _hint_matches(self, value: str, hint: str) -> bool:
        left = self._normalize_name(value)
        right = self._normalize_name(hint)
        if not left or not right:
            return False
        if len(left) <= 1 or len(right) <= 1:
            return left == right
        return left == right or right in left or left in right

    def _to_search_blob(self, *parts: Any) -> str:
        raw = ' '.join(str(part or '').strip().lower() for part in parts if str(part or '').strip())
        if not raw:
            return ''
        normalized_chars = []
        for char in raw:
            normalized_chars.append(char if char.isalnum() else ' ')
        compact = ' '.join(''.join(normalized_chars).split())
        return f' {compact} ' if compact else ''

    def _normalize_name(self, raw: Any) -> str:
        return str(raw or '').strip().lower()

    def _normalize_names(self, raw: Any) -> List[str]:
        if not isinstance(raw, list):
            return []
        out: List[str] = []
        for item in raw:
            name = self._normalize_name(item)
            if name:
                out.append(name)
        return out

    def _is_rate_limited(self, kind: str, data: Any, cooldown: float) -> bool:
        event_key = self._event_key(data)
        if isinstance(data, dict):
            x = data.get('x')
            y = data.get('y')
            if x is not None and y is not None:
                event_key = f'{event_key}|{x}:{y}'

        now = time.monotonic()
        last_key = self._last_event_by_kind.get(kind, '')
        last_ts = self._last_ts_by_kind.get(kind, 0.0)
        if event_key == last_key and (now - last_ts) < 0.20:
            return True
        if (now - last_ts) < cooldown:
            return True

        self._last_event_by_kind[kind] = event_key
        self._last_ts_by_kind[kind] = now
        return False

    def _is_phase_duplicate(self, kind: str, data: Any) -> bool:
        phase = KIND_PHASE.get(kind)
        if not phase:
            return False
        dedupe_window = float(PHASE_DEDUPE_SECONDS.get(phase) or 0.0)
        if dedupe_window <= 0.0:
            return False
        event_key = self._phase_event_key(data)
        now = time.monotonic()
        last_key = self._last_event_by_phase.get(phase, '')
        last_ts = self._last_ts_by_phase.get(phase, 0.0)
        if event_key == last_key and (now - last_ts) < dedupe_window:
            return True
        self._last_event_by_phase[phase] = event_key
        self._last_ts_by_phase[phase] = now
        return False

    def _phase_event_key(self, data: Any) -> str:
        if not isinstance(data, dict):
            return 'phase'
        x = data.get('x')
        if x is None:
            x = data.get('windowX')
        y = data.get('y')
        if y is None:
            y = data.get('windowY')
        return f'{self._bucket(x)}:{self._bucket(y)}'

    def _bucket(self, raw: Any, step: float = 24.0) -> str:
        try:
            value = float(raw)
            return str(int(value // step))
        except Exception:
            return 'na'

    def _event_key(self, data: Any) -> str:
        if not isinstance(data, dict):
            return 'event'
        out = []
        for key in ('x', 'y', 'scrollDx', 'scrollDy', 'windowX', 'windowY'):
            if key in data:
                out.append(f'{key}={data.get(key)}')
        if not out:
            return 'event'
        return ';'.join(out)

    def _resolve_intent(self, kind: str, data: Any, fallback: str) -> str:
        if kind != 'scroll':
            return fallback
        direction = self._scroll_direction(data)
        if direction == 'up':
            return 'TOUCH_SCROLL_UP'
        if direction == 'down':
            return 'TOUCH_SCROLL_DOWN'
        return 'TOUCH_SCROLL_NONE'

    def _scroll_direction(self, data: Any) -> str:
        if not isinstance(data, dict):
            return 'none'
        raw = data.get('scrollDy')
        try:
            value = float(raw)
        except Exception:
            return 'none'
        if value < -0.001:
            return 'up'
        if value > 0.001:
            return 'down'
        return 'none'


if __name__ == '__main__':
    run_plugin(ReactionsPlugin)
