#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

_SDK_PYTHON_DIR = os.environ.get('MINACHAN_SDK_PYTHON_DIR', '').strip()
if not _SDK_PYTHON_DIR:
    raise RuntimeError('MINACHAN_SDK_PYTHON_DIR is not set')
if _SDK_PYTHON_DIR not in sys.path:
    sys.path.insert(0, _SDK_PYTHON_DIR)
from minachan_sdk import MinaChanPlugin, run_plugin


class SpineEmotionEditorPlugin(MinaChanPlugin):
    PANEL_ID = 'spine_emotion_editor.panel'
    WINDOW_ID = 'spine_emotion_editor'
    SETTINGS_PANEL_ID = 'spine_emotion_editor.settings'

    CMD_OPEN = 'spine-emotion-editor:open'
    CMD_RELOAD = 'spine-emotion-editor:reload'
    TAG_APPLY_DRAFT = 'spine-emotion-editor:apply-draft'
    TAG_PREVIEW_EMOTION = 'spine-emotion-editor:preview-emotion'
    TAG_PREVIEW_ANIMATION = 'spine-emotion-editor:preview-animation'
    TAG_SAVE = 'spine-emotion-editor:save'

    TAG_GUI_REQUEST_PANELS = 'gui:request-panels'
    TAG_GUI_SKIN_CHANGED = 'gui-events:skin-changed'
    TAG_CHARACTER_CHANGED = 'character:changed'

    _PREVIEW_EMOTION = '__preview__'

    def __init__(self) -> None:
        super().__init__()
        self._ui_locale = 'ru'
        self._window_panel_registered = False
        self._menu_rule = ''

        self._pending_profile_reload = True
        self._suppress_next_character_reload = False

        self._character_id = ''
        self._character_profile: Dict[str, Any] = {}
        self._character_name = ''
        self._spine_pack_path = ''
        self._spine_profile_rel_path = ''
        self._spine_profile_abs_path = ''
        self._spine_profile_is_linked = False

        self._skin_type = ''
        self._runtime_spine_profile_id = ''
        self._current_emotion = 'normal'
        self._current_animation = ''
        self._runtime_emotions: List[str] = []
        self._available_animations: List[str] = []

        self._profile_loaded = False
        self._base_profile_document: Dict[str, Any] = {}
        self._form = self._default_form()

        self._status_text = ''
        self._status_level = 'info'

    def on_init(self) -> None:
        plugin_id = self._plugin_id()
        if plugin_id:
            self.add_listener(
                plugin_id,
                self.on_runtime_reply,
                listener_id='spine_emotion_editor_runtime_reply',
            )

        self.add_listener(self.CMD_OPEN, self.on_open, listener_id='spine_emotion_editor_open')
        self.add_listener(self.CMD_RELOAD, self.on_reload, listener_id='spine_emotion_editor_reload')
        self.add_listener(
            self.TAG_APPLY_DRAFT,
            self.on_apply_draft,
            listener_id='spine_emotion_editor_apply_draft',
        )
        self.add_listener(
            self.TAG_PREVIEW_EMOTION,
            self.on_preview_emotion,
            listener_id='spine_emotion_editor_preview_emotion',
        )
        self.add_listener(
            self.TAG_PREVIEW_ANIMATION,
            self.on_preview_animation,
            listener_id='spine_emotion_editor_preview_animation',
        )
        self.add_listener(
            self.TAG_SAVE,
            self.on_save,
            listener_id='spine_emotion_editor_save',
        )

        self.add_listener(
            self.TAG_GUI_REQUEST_PANELS,
            self.on_gui_request_panels,
            listener_id='spine_emotion_editor_request_panels',
        )
        self.add_listener(
            self.TAG_GUI_SKIN_CHANGED,
            self.on_gui_skin_changed,
            listener_id='spine_emotion_editor_skin_changed',
        )
        self.add_listener(
            self.TAG_CHARACTER_CHANGED,
            self.on_character_changed,
            listener_id='spine_emotion_editor_character_changed',
        )

        self.register_command(
            self.CMD_OPEN,
            {
                'en': 'Open Spine emotion editor',
                'ru': 'Открыть редактор эмоций Spine',
            },
        )
        self.register_command(
            self.CMD_RELOAD,
            {
                'en': 'Reload active Spine profile into editor',
                'ru': 'Перезагрузить активный Spine-профиль в редактор',
            },
        )

        self.add_locale_listener(self._on_locale_changed, default_locale='ru')
        self._sync_menu_link()
        self._publish_ui(force_set=True)
        self._request_context(reload_profile=True)

    def on_unload(self) -> None:
        self._clear_menu_link()
        try:
            self.ui_window_delete(self.WINDOW_ID, close=True)
        except Exception:
            pass
        try:
            self.remove_panel(self.PANEL_ID)
        except Exception:
            pass

    def on_runtime_reply(self, sender: str, data: Any, tag: str) -> None:
        payload = self._as_map(data)
        if not payload:
            return
        if 'profile' in payload and ('active' in payload or 'id' in payload):
            self._consume_character_payload(
                payload,
                force_reload=self._pending_profile_reload,
            )
            self._pending_profile_reload = False
            return
        if (
            'skinType' in payload
            or 'spineAnimations' in payload
            or 'currentEmotion' in payload
            or 'spineProfileId' in payload
        ):
            self._consume_gui_state(payload)

    def on_gui_request_panels(self, sender: str, data: Any, tag: str) -> None:
        self._publish_ui(force_set=True)

    def on_gui_skin_changed(self, sender: str, data: Any, tag: str) -> None:
        self._consume_gui_state(self._as_map(data))

    def on_character_changed(self, sender: str, data: Any, tag: str) -> None:
        force_reload = not self._suppress_next_character_reload
        self._suppress_next_character_reload = False
        self._consume_character_payload(self._as_map(data), force_reload=force_reload)

    def on_open(self, sender: str, data: Any, tag: str) -> None:
        self._consume_panel_values(self._as_map(data))
        self._publish_ui(force_set=False)
        self.ui_window_open(self.WINDOW_ID)
        self._request_context(reload_profile=False)

    def on_reload(self, sender: str, data: Any, tag: str) -> None:
        self._consume_panel_values(self._as_map(data))
        self._pending_profile_reload = True
        self._set_status(
            self._tr('Reloading active Spine profile...', 'Перезагружаю активный Spine-профиль...'),
            level='info',
        )
        self._publish_ui(force_set=False)
        self._request_context(reload_profile=True)

    def on_apply_draft(self, sender: str, data: Any, tag: str) -> None:
        self._consume_panel_values(self._as_map(data))
        if not self._can_edit():
            self._set_status(
                self._tr(
                    'Active character is not a Spine character.',
                    'Активный персонаж не использует Spine.',
                ),
                level='warning',
            )
            self._publish_ui(force_set=False)
            return

        draft = self._build_draft_profile()
        if not self._activate_spine_runtime_if_needed(preserve_draft=True):
            return
        self.send_message('gui:set-spine-profile', {'profile': draft})
        self._set_status(
            self._tr(
                'Draft profile applied to the current Spine character.',
                'Черновик профиля применён к текущему Spine-персонажу.',
            ),
            level='success',
        )
        self._publish_ui(force_set=False)

    def on_preview_emotion(self, sender: str, data: Any, tag: str) -> None:
        self._consume_panel_values(self._as_map(data))
        if not self._can_edit():
            self._set_status(
                self._tr(
                    'Active character is not a Spine character.',
                    'Активный персонаж не использует Spine.',
                ),
                level='warning',
            )
            self._publish_ui(force_set=False)
            return

        emotion = self._normalize_name(self._form.get('previewEmotion'))
        if not emotion:
            self._set_status(
                self._tr('Choose an emotion to preview.', 'Выбери эмоцию для предпросмотра.'),
                level='warning',
            )
            self._publish_ui(force_set=False)
            return

        draft = self._build_draft_profile()
        if not self._activate_spine_runtime_if_needed(preserve_draft=True):
            return
        self.send_message('gui:set-spine-profile', {'profile': draft})
        self.send_message(
            'gui:set-emotion',
            {
                'emotion': emotion,
                'manualOverride': True,
                'source': 'spine_emotion_editor',
            },
        )
        self._set_status(
            self._tr(
                f'Previewing emotion "{emotion}".',
                f'Предпросмотр эмоции "{emotion}".',
            ),
            level='success',
        )
        self._publish_ui(force_set=False)

    def on_preview_animation(self, sender: str, data: Any, tag: str) -> None:
        self._consume_panel_values(self._as_map(data))
        if not self._can_edit():
            self._set_status(
                self._tr(
                    'Active character is not a Spine character.',
                    'Активный персонаж не использует Spine.',
                ),
                level='warning',
            )
            self._publish_ui(force_set=False)
            return

        animation = self._string(self._form.get('previewAnimation'))
        if not animation:
            self._set_status(
                self._tr('Choose an animation to preview.', 'Выбери анимацию для предпросмотра.'),
                level='warning',
            )
            self._publish_ui(force_set=False)
            return

        draft = self._profile_with_preview_animation(self._build_draft_profile(), animation)
        if not self._activate_spine_runtime_if_needed(preserve_draft=True):
            return
        self.send_message('gui:set-spine-profile', {'profile': draft})
        self.send_message(
            'gui:set-emotion',
            {
                'emotion': self._PREVIEW_EMOTION,
                'manualOverride': True,
                'source': 'spine_emotion_editor',
            },
        )
        self._set_status(
            self._tr(
                f'Previewing animation "{animation}".',
                f'Предпросмотр анимации "{animation}".',
            ),
            level='success',
        )
        self._publish_ui(force_set=False)

    def on_save(self, sender: str, data: Any, tag: str) -> None:
        self._consume_panel_values(self._as_map(data))
        if not self._can_edit():
            self._set_status(
                self._tr(
                    'Active character is not a Spine character.',
                    'Активный персонаж не использует Spine.',
                ),
                level='warning',
            )
            self._publish_ui(force_set=False)
            return

        saved = self._save_profile_to_disk()
        if saved is None:
            self._publish_ui(force_set=False)
            return

        self._base_profile_document = saved
        self._profile_loaded = True
        self._load_profile_into_form(saved, preserve_preview=True)
        self._set_status(
            self._tr(
                'Spine profile saved to disk and reapplied.',
                'Spine-профиль сохранён на диск и пере-применён.',
            ),
            level='success',
        )
        self._publish_ui(force_set=False)

        if self._character_id:
            self.send_message('character:set', {'id': self._character_id})

    def _on_locale_changed(self, locale: str, chain: List[str]) -> None:
        self._ui_locale = locale
        self._sync_menu_link()
        self._publish_ui(force_set=True)

    def _request_context(self, reload_profile: bool) -> None:
        if reload_profile:
            self._pending_profile_reload = True
        self.send_message('character:get')
        self.send_message('gui:get-emotions')

    def _consume_character_payload(
        self,
        payload: Dict[str, Any],
        force_reload: bool,
    ) -> None:
        active_id = self._normalize_name(payload.get('active') or payload.get('id'))
        profile = self._as_map(payload.get('profile'))
        if not active_id and not profile:
            return

        previous_character_id = self._character_id
        previous_profile_path = self._spine_profile_abs_path

        self._character_id = active_id or self._character_id
        self._character_profile = profile
        self._character_name = self._localized_profile_name(profile, fallback=self._character_id or '-')
        self._spine_pack_path = self._string(profile.get('spinePack'))

        explicit_profile_path = self._string(profile.get('spineProfilePath'))
        self._spine_profile_is_linked = bool(explicit_profile_path)
        self._spine_profile_rel_path = explicit_profile_path or self._default_profile_rel_path(self._character_id)
        self._spine_profile_abs_path = self._resolve_absolute_path(self._spine_profile_rel_path)

        profile_path_changed = self._spine_profile_abs_path != previous_profile_path
        character_changed = self._character_id != previous_character_id
        should_reload = force_reload or character_changed or profile_path_changed or not self._profile_loaded

        if character_changed or profile_path_changed:
            self._status_text = ''
            self._status_level = 'info'

        if should_reload:
            loaded = self._load_profile_document(self._spine_profile_abs_path)
            if loaded is None:
                self._base_profile_document = self._blank_profile_document()
                self._profile_loaded = False
                self._load_profile_into_form(self._base_profile_document, preserve_preview=False)
            else:
                self._base_profile_document = loaded
                self._profile_loaded = True
                self._load_profile_into_form(loaded, preserve_preview=False)

        self._refresh_context_status()
        self._publish_ui(force_set=False)

    def _consume_gui_state(self, payload: Dict[str, Any]) -> None:
        if not payload:
            return

        self._skin_type = self._normalize_name(payload.get('skinType'))
        self._runtime_spine_profile_id = self._normalize_name(payload.get('spineProfileId'))
        self._current_emotion = self._normalize_name(payload.get('currentEmotion')) or self._current_emotion
        self._current_animation = self._string(payload.get('spineAnimation'))
        self._runtime_emotions = self._normalize_name_list(payload.get('emotions'))
        self._available_animations = self._normalize_text_list(payload.get('spineAnimations'))

        preview_emotion = self._normalize_name(self._form.get('previewEmotion'))
        if not preview_emotion and self._current_emotion:
            self._form['previewEmotion'] = self._current_emotion

        self._refresh_context_status()
        self._publish_ui(force_set=False)

    def _publish_ui(self, force_set: bool) -> None:
        self._register_settings_panel()

        panel_name = self._tr('Spine Emotion Editor', 'Редактор эмоций Spine')
        controls = self._build_window_controls()
        extra = self._panel_extra()

        if force_set or not self._window_panel_registered:
            self.set_panel(
                panel_id=self.PANEL_ID,
                name=panel_name,
                msg_tag='',
                controls=controls,
                panel_type='window',
                extra=extra,
            )
            self._window_panel_registered = True
        else:
            self.update_panel(
                panel_id=self.PANEL_ID,
                name=panel_name,
                msg_tag='',
                controls=controls,
                panel_type='window',
                extra=extra,
            )

        self.ui_window_create(
            window=self.WINDOW_ID,
            panel_id=self.PANEL_ID,
            title=panel_name,
            geometry_kind='spine_emotion_editor',
            open_on_create=False,
            width=940,
            height=900,
        )

    def _register_settings_panel(self) -> None:
        self.setup_options_panel(
            panel_id=self.SETTINGS_PANEL_ID,
            name=self._tr('Spine Emotion Editor', 'Редактор эмоций Spine'),
            msg_tag='',
            controls=[
                {
                    'id': 'settings_description',
                    'type': 'label',
                    'label': self._tr(
                        'Open the in-app editor to change Spine emotion mappings and preview animations.',
                        'Открой встроенный редактор, чтобы менять Spine-эмоции и сразу смотреть анимации.',
                    ),
                },
                {
                    'id': 'settings_character',
                    'type': 'label',
                    'label': self._tr(
                        f'Current character: {self._character_name or "-"}',
                        f'Текущий персонаж: {self._character_name or "-"}',
                    ),
                },
                {
                    'id': 'settings_status',
                    'type': 'label',
                    'label': self._status_line(),
                },
                {
                    'id': 'settings_open',
                    'type': 'button',
                    'label': self._tr('Open Editor', 'Открыть редактор'),
                    'msgTag': self.CMD_OPEN,
                },
                {
                    'id': 'settings_reload',
                    'type': 'button',
                    'label': self._tr('Reload Active Profile', 'Перечитать активный профиль'),
                    'msgTag': self.CMD_RELOAD,
                    'disabled': not bool(self._character_id),
                },
            ],
        )

    def _build_window_controls(self) -> List[Dict[str, Any]]:
        can_edit = self._can_edit()
        can_save = bool(self._resolved_profile_save_path())

        default_animation = self._string(self._form.get('defaultAnimation'))
        preview_emotion = self._normalize_name(self._form.get('previewEmotion'))
        preview_animation = self._string(self._form.get('previewAnimation'))
        emotion_rows = self._coerce_rows(self._form.get('emotionRows'))
        alias_rows = self._coerce_rows(self._form.get('aliasRows'))

        animation_values = self._animation_values_for_ui(
            extras=[
                default_animation,
                preview_animation,
                *(row.get('animation') for row in emotion_rows),
            ],
        )
        emotion_values = self._emotion_values_for_ui(
            extras=[
                preview_emotion,
                self._current_emotion,
                *(row.get('emotion') for row in emotion_rows),
                *(row.get('emotion') for row in alias_rows),
            ],
        )

        controls: List[Dict[str, Any]] = [
            {
                'id': 'context_status',
                'type': 'label',
                'label': self._status_line(),
                'section': 'context',
                'fullWidth': True,
            },
            {
                'id': 'context_character',
                'type': 'label',
                'label': self._tr(
                    f'Character: {self._character_name or "-"}',
                    f'Персонаж: {self._character_name or "-"}',
                ),
                'section': 'context',
            },
            {
                'id': 'context_skin_type',
                'type': 'label',
                'label': self._tr(
                    f'Runtime skin type: {self._skin_type or "-"}',
                    f'Тип текущего рендера: {self._skin_type or "-"}',
                ),
                'section': 'context',
            },
            {
                'id': 'context_profile_id',
                'type': 'label',
                'label': self._tr(
                    f'Runtime profile id: {self._runtime_spine_profile_id or "-"}',
                    f'Runtime profile id: {self._runtime_spine_profile_id or "-"}',
                ),
                'section': 'context',
            },
            {
                'id': 'context_current_state',
                'type': 'label',
                'label': self._tr(
                    f'Current emotion / animation: {self._current_emotion or "-"} / {self._current_animation or "-"}',
                    f'Текущая эмоция / анимация: {self._current_emotion or "-"} / {self._current_animation or "-"}',
                ),
                'section': 'context',
            },
            {
                'id': 'context_profile_path',
                'type': 'label',
                'label': self._tr(
                    f'Profile path: {self._profile_path_display()}',
                    f'Путь профиля: {self._profile_path_display()}',
                ),
                'section': 'context',
                'fullWidth': True,
            },
            {
                'id': 'context_spine_pack',
                'type': 'label',
                'label': self._tr(
                    f'Spine pack: {self._spine_pack_path or "-"}',
                    f'Spine-пак: {self._spine_pack_path or "-"}',
                ),
                'section': 'context',
                'fullWidth': True,
            },
            {
                'id': 'context_reload',
                'type': 'button',
                'label': self._tr('Reload From Disk', 'Перечитать с диска'),
                'msgTag': self.CMD_RELOAD,
                'section': 'context',
                'disabled': not bool(self._character_id),
            },
        ]

        controls.append(
            self._build_choice_or_text_control(
                control_id='spine_default_animation',
                label=self._tr('Default animation', 'Анимация по умолчанию'),
                value=default_animation,
                options=self._select_options(
                    animation_values,
                    include_blank=True,
                    blank_label=self._tr('Use runtime fallback', 'Использовать fallback runtime'),
                ),
                section='mapping',
                disabled=not can_edit,
                hint=self._tr(
                    'Used when no explicit emotion mapping is found.',
                    'Используется, когда для эмоции нет явного сопоставления.',
                ),
            ),
        )

        controls.extend(
            [
                {
                    'id': 'spine_emotion_rows',
                    'type': 'table',
                    'label': self._tr('Emotion to animation mapping', 'Сопоставление эмоций и анимаций'),
                    'value': emotion_rows,
                    'section': 'mapping',
                    'fullWidth': True,
                    'disabled': not can_edit,
                    'hint': self._tr(
                        'One row = one canonical emotion and its animation.',
                        'Одна строка = одна каноническая эмоция и её анимация.',
                    ),
                    'columns': [
                        {
                            'id': 'emotion',
                            'label': self._tr('Emotion', 'Эмоция'),
                            'type': 'text',
                        },
                        {
                            'id': 'animation',
                            'label': self._tr('Animation', 'Анимация'),
                            'type': 'select' if animation_values else 'text',
                            'options': self._select_options(
                                animation_values,
                                include_blank=True,
                                blank_label=self._tr('Not set', 'Не задано'),
                            ),
                        },
                    ],
                },
                {
                    'id': 'spine_alias_rows',
                    'type': 'table',
                    'label': self._tr('Emotion aliases', 'Алиасы эмоций'),
                    'value': alias_rows,
                    'section': 'mapping',
                    'fullWidth': True,
                    'disabled': not can_edit,
                    'hint': self._tr(
                        'Alias names are normalized to canonical emotions from the table above.',
                        'Алиасы нормализуются к каноническим эмоциям из таблицы выше.',
                    ),
                    'columns': [
                        {
                            'id': 'alias',
                            'label': self._tr('Alias', 'Алиас'),
                            'type': 'text',
                        },
                        {
                            'id': 'emotion',
                            'label': self._tr('Canonical emotion', 'Каноническая эмоция'),
                            'type': 'select' if emotion_values else 'text',
                            'options': self._select_options(
                                emotion_values,
                                include_blank=True,
                                blank_label=self._tr('Not set', 'Не задано'),
                            ),
                        },
                    ],
                },
            ]
        )

        controls.append(
            self._build_choice_or_text_control(
                control_id='preview_emotion',
                label=self._tr('Emotion to preview', 'Эмоция для предпросмотра'),
                value=preview_emotion,
                options=self._select_options(emotion_values),
                section='preview',
                disabled=not can_edit,
            ),
        )
        controls.append(
            self._build_choice_or_text_control(
                control_id='preview_animation',
                label=self._tr('Direct animation preview', 'Прямой предпросмотр анимации'),
                value=preview_animation,
                options=self._select_options(
                    animation_values,
                    include_blank=True,
                    blank_label=self._tr('Choose animation', 'Выбери анимацию'),
                ),
                section='preview',
                disabled=not can_edit,
            ),
        )
        controls.extend(
            [
                {
                    'id': 'btn_apply_draft',
                    'type': 'button',
                    'label': self._tr('Apply Draft', 'Применить черновик'),
                    'msgTag': self.TAG_APPLY_DRAFT,
                    'section': 'preview',
                    'disabled': not can_edit,
                },
                {
                    'id': 'btn_preview_emotion',
                    'type': 'button',
                    'label': self._tr('Preview Emotion', 'Предпросмотр эмоции'),
                    'msgTag': self.TAG_PREVIEW_EMOTION,
                    'section': 'preview',
                    'disabled': not can_edit,
                },
                {
                    'id': 'btn_preview_animation',
                    'type': 'button',
                    'label': self._tr('Preview Animation', 'Предпросмотр анимации'),
                    'msgTag': self.TAG_PREVIEW_ANIMATION,
                    'section': 'preview',
                    'disabled': not can_edit,
                },
                {
                    'id': 'btn_save_profile',
                    'type': 'button',
                    'label': self._tr('Save Profile', 'Сохранить профиль'),
                    'msgTag': self.TAG_SAVE,
                    'section': 'preview',
                    'disabled': not (can_edit and can_save),
                    'variant': 'danger',
                },
            ]
        )

        return controls

    def _build_choice_or_text_control(
        self,
        control_id: str,
        label: str,
        value: str,
        options: List[Dict[str, Any]],
        section: str,
        disabled: bool,
        hint: str = '',
    ) -> Dict[str, Any]:
        if options:
            return {
                'id': control_id,
                'type': 'select',
                'label': label,
                'value': value,
                'options': options,
                'section': section,
                'disabled': disabled,
                'hint': hint,
            }
        return {
            'id': control_id,
            'type': 'text',
            'label': label,
            'value': value,
            'section': section,
            'disabled': disabled,
            'hint': hint,
        }

    def _panel_extra(self) -> Dict[str, Any]:
        return {
            'scope': 'window',
            'sections': [
                {
                    'id': 'context',
                    'label': self._tr('Context', 'Контекст'),
                    'icon': 'info',
                    'order': 10,
                    'columns': 2,
                    'minTileWidth': 240,
                },
                {
                    'id': 'mapping',
                    'label': self._tr('Mappings', 'Сопоставления'),
                    'icon': 'merge',
                    'order': 20,
                    'columns': 1,
                    'minTileWidth': 360,
                },
                {
                    'id': 'preview',
                    'label': self._tr('Preview & Save', 'Предпросмотр и сохранение'),
                    'icon': 'play_arrow',
                    'order': 30,
                    'columns': 2,
                    'minTileWidth': 240,
                },
            ],
        }

    def _consume_panel_values(self, payload: Dict[str, Any]) -> None:
        if not payload:
            return

        if 'spine_default_animation' in payload:
            self._form['defaultAnimation'] = self._string(payload.get('spine_default_animation'))
        if 'preview_emotion' in payload:
            self._form['previewEmotion'] = self._normalize_name(payload.get('preview_emotion'))
        if 'preview_animation' in payload:
            self._form['previewAnimation'] = self._string(payload.get('preview_animation'))
        if 'spine_emotion_rows' in payload:
            self._form['emotionRows'] = self._coerce_rows(payload.get('spine_emotion_rows'))
        if 'spine_alias_rows' in payload:
            self._form['aliasRows'] = self._coerce_rows(payload.get('spine_alias_rows'))

    def _load_profile_into_form(self, profile: Dict[str, Any], preserve_preview: bool) -> None:
        current_preview_emotion = self._normalize_name(self._form.get('previewEmotion'))
        current_preview_animation = self._string(self._form.get('previewAnimation'))

        emotion_map = self._as_map(profile.get('emotionAnimations'))
        alias_map = self._as_map(profile.get('emotionAliases'))

        emotion_rows = [
            {
                'emotion': key,
                'animation': self._string(value),
            }
            for key, value in sorted(
                (
                    (self._normalize_name(raw_key), raw_value)
                    for raw_key, raw_value in emotion_map.items()
                    if self._normalize_name(raw_key)
                ),
                key=lambda item: self._natural_sort_key(item[0]),
            )
        ]
        alias_rows = [
            {
                'alias': key,
                'emotion': self._normalize_name(value),
            }
            for key, value in sorted(
                (
                    (self._normalize_name(raw_key), raw_value)
                    for raw_key, raw_value in alias_map.items()
                    if self._normalize_name(raw_key)
                ),
                key=lambda item: self._natural_sort_key(item[0]),
            )
        ]

        preview_emotion = current_preview_emotion if preserve_preview else ''
        if not preview_emotion:
            preview_emotion = self._current_emotion or (emotion_rows[0]['emotion'] if emotion_rows else 'normal')

        self._form = {
            'defaultAnimation': self._string(profile.get('defaultAnimation')),
            'previewEmotion': preview_emotion,
            'previewAnimation': current_preview_animation if preserve_preview else '',
            'emotionRows': emotion_rows,
            'aliasRows': alias_rows,
        }

    def _build_draft_profile(self) -> Dict[str, Any]:
        draft = dict(self._base_profile_document)
        character_id = self._character_id or self._string(draft.get('id'))
        if character_id:
            draft['id'] = character_id

        default_animation = self._string(self._form.get('defaultAnimation'))
        if default_animation:
            draft['defaultAnimation'] = default_animation
        else:
            draft.pop('defaultAnimation', None)

        draft['emotionAnimations'] = self._emotion_animation_map_from_rows(self._form.get('emotionRows'))
        draft['emotionAliases'] = self._emotion_alias_map_from_rows(self._form.get('aliasRows'))
        return draft

    def _profile_with_preview_animation(
        self,
        draft: Dict[str, Any],
        animation: str,
    ) -> Dict[str, Any]:
        preview_profile = dict(draft)
        emotion_map = self._as_map(preview_profile.get('emotionAnimations'))
        emotion_map[self._PREVIEW_EMOTION] = animation
        preview_profile['emotionAnimations'] = emotion_map
        alias_map = self._as_map(preview_profile.get('emotionAliases'))
        alias_map[self._PREVIEW_EMOTION] = self._PREVIEW_EMOTION
        preview_profile['emotionAliases'] = alias_map
        return preview_profile

    def _save_profile_to_disk(self) -> Optional[Dict[str, Any]]:
        path = self._resolved_profile_save_path()
        if not path:
            self._set_status(
                self._tr('Profile path is not available.', 'Путь профиля недоступен.'),
                level='error',
            )
            return None

        current_on_disk = self._load_profile_document(path)
        base_document = current_on_disk if isinstance(current_on_disk, dict) else dict(self._base_profile_document)
        self._base_profile_document = base_document
        draft = self._build_draft_profile()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(draft, fh, ensure_ascii=False, indent=2)
                fh.write('\n')
        except Exception as error:
            self._set_status(
                self._tr(
                    f'Failed to save profile: {error}',
                    f'Не удалось сохранить профиль: {error}',
                ),
                level='error',
            )
            return None

        if not self._spine_profile_rel_path and self._character_id:
            self._spine_profile_rel_path = self._default_profile_rel_path(self._character_id)
        self._spine_profile_abs_path = path
        return draft

    def _activate_spine_runtime_if_needed(self, preserve_draft: bool) -> bool:
        if not self._character_id or not self._spine_pack_path:
            self._set_status(
                self._tr(
                    'There is no active Spine character to preview.',
                    'Нет активного Spine-персонажа для предпросмотра.',
                ),
                level='warning',
            )
            self._publish_ui(force_set=False)
            return False

        if self._skin_type == 'spine':
            return True

        self._suppress_next_character_reload = preserve_draft
        self.send_message('character:set', {'id': self._character_id})
        return True

    def _can_edit(self) -> bool:
        return bool(self._character_id and self._spine_pack_path)

    def _resolved_profile_save_path(self) -> str:
        if self._spine_profile_abs_path:
            return self._spine_profile_abs_path
        if self._character_id:
            return self._resolve_absolute_path(self._default_profile_rel_path(self._character_id))
        return ''

    def _profile_path_display(self) -> str:
        path = self._spine_profile_rel_path or self._default_profile_rel_path(self._character_id)
        if not path:
            return '-'
        if self._spine_profile_is_linked:
            return path
        return self._tr(
            f'{path} (not linked in .chr yet)',
            f'{path} (ещё не привязан в .chr)',
        )

    def _refresh_context_status(self) -> None:
        if not self._character_id:
            if not self._status_text:
                self._set_status(
                    self._tr(
                        'Waiting for active character context...',
                        'Жду контекст активного персонажа...',
                    ),
                    level='info',
                )
            return

        if not self._spine_pack_path:
            self._set_status(
                self._tr(
                    'Active character is not a Spine character.',
                    'Активный персонаж не использует Spine.',
                ),
                level='warning',
            )
            return

        if not self._profile_loaded and self._resolved_profile_save_path():
            self._set_status(
                self._tr(
                    'Profile file is missing on disk. Saving will create a new one.',
                    'Файл профиля пока отсутствует. Сохранение создаст новый.',
                ),
                level='info',
            )
            return

        if not self._available_animations and self._skin_type == 'spine':
            self._set_status(
                self._tr(
                    'Waiting for Spine runtime to report animation names...',
                    'Жду, пока Spine runtime сообщит имена анимаций...',
                ),
                level='info',
            )
            return

        if not self._status_text or self._status_level not in ('success', 'warning', 'error'):
            self._set_status(
                self._tr(
                    'Ready to edit the current Spine profile.',
                    'Можно редактировать текущий Spine-профиль.',
                ),
                level='info',
            )

    def _status_line(self) -> str:
        level = self._status_level.upper() if self._status_level else 'INFO'
        text = self._status_text or self._tr('No status yet.', 'Пока без статуса.')
        return f'[{level}] {text}'

    def _set_status(self, text: str, level: str) -> None:
        self._status_text = self._string(text)
        self._status_level = self._normalize_name(level) or 'info'

    def _sync_menu_link(self) -> None:
        new_rule = self._menu_label()
        if new_rule == self._menu_rule:
            return
        self._clear_menu_link()
        self.set_event_link('gui:menu-action', self.CMD_OPEN, rule=new_rule)
        self._menu_rule = new_rule

    def _clear_menu_link(self) -> None:
        if not self._menu_rule:
            return
        self.remove_event_link('gui:menu-action', self.CMD_OPEN, rule=self._menu_rule)
        self._menu_rule = ''

    def _menu_label(self) -> str:
        return self._tr(
            'Debug/Spine Emotion Editor',
            'Отладка/Редактор эмоций Spine',
        )

    def _default_form(self) -> Dict[str, Any]:
        return {
            'defaultAnimation': '',
            'previewEmotion': 'normal',
            'previewAnimation': '',
            'emotionRows': [],
            'aliasRows': [],
        }

    def _blank_profile_document(self) -> Dict[str, Any]:
        blank: Dict[str, Any] = {
            'emotionAnimations': {},
            'emotionAliases': {},
        }
        if self._character_id:
            blank['id'] = self._character_id
        return blank

    def _load_profile_document(self, path: str) -> Optional[Dict[str, Any]]:
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                decoded = json.loads(fh.read().strip() or '{}')
        except Exception as error:
            self._set_status(
                self._tr(
                    f'Profile JSON is invalid: {error}',
                    f'JSON профиля повреждён: {error}',
                ),
                level='error',
            )
            return None
        return decoded if isinstance(decoded, dict) else None

    def _default_profile_rel_path(self, character_id: str) -> str:
        normalized = self._normalize_name(character_id)
        if not normalized:
            return ''
        return f'assets/characters/spine_profiles/{normalized}.spine.json'

    def _resolve_absolute_path(self, relative_or_absolute_path: Any) -> str:
        raw = self._string(relative_or_absolute_path)
        if not raw:
            return ''
        if os.path.isabs(raw):
            return raw
        root_dir = self._string(self.info.get('rootDirPath'))
        if not root_dir:
            return raw
        return os.path.join(root_dir, raw)

    def _localized_profile_name(self, profile: Dict[str, Any], fallback: str) -> str:
        names = profile.get('name')
        if not isinstance(names, dict):
            return fallback
        ru_name = self._string(names.get('ru'))
        en_name = self._string(names.get('en'))
        if self._is_ru_locale():
            return ru_name or en_name or fallback
        return en_name or ru_name or fallback

    def _animation_values_for_ui(self, extras: Sequence[Any]) -> List[str]:
        values = set(self._available_animations)
        for item in extras:
            text = self._string(item)
            if text:
                values.add(text)
        return sorted(values, key=self._natural_sort_key)

    def _emotion_values_for_ui(self, extras: Sequence[Any]) -> List[str]:
        values = set(self._runtime_emotions)
        for row in self._coerce_rows(self._form.get('emotionRows')):
            name = self._normalize_name(row.get('emotion'))
            if name:
                values.add(name)
        for row in self._coerce_rows(self._form.get('aliasRows')):
            target = self._normalize_name(row.get('emotion'))
            if target:
                values.add(target)
        for item in extras:
            name = self._normalize_name(item)
            if name:
                values.add(name)
        return sorted(values, key=self._natural_sort_key)

    def _select_options(
        self,
        values: Sequence[Any],
        include_blank: bool = False,
        blank_label: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        unique_values = []
        seen = set()
        if include_blank:
            unique_values.append('')
            seen.add('')
        for item in values:
            text = self._string(item)
            if text in seen:
                continue
            seen.add(text)
            unique_values.append(text)
        options = []
        for item in unique_values:
            if not item:
                options.append(
                    {
                        'value': '',
                        'label': blank_label or self._tr('Not set', 'Не задано'),
                    }
                )
                continue
            options.append({'value': item, 'label': item})
        return options

    def _emotion_animation_map_from_rows(self, raw_rows: Any) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for row in self._coerce_rows(raw_rows):
            emotion = self._normalize_name(row.get('emotion'))
            animation = self._string(row.get('animation'))
            if not emotion or not animation:
                continue
            out[emotion] = animation
        return out

    def _emotion_alias_map_from_rows(self, raw_rows: Any) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for row in self._coerce_rows(raw_rows):
            alias = self._normalize_name(row.get('alias'))
            emotion = self._normalize_name(row.get('emotion'))
            if not alias or not emotion:
                continue
            out[alias] = emotion
        return out

    def _coerce_rows(self, raw: Any) -> List[Dict[str, str]]:
        if not isinstance(raw, list):
            return []
        rows: List[Dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            row = {
                str(key): self._string(value)
                for key, value in item.items()
            }
            rows.append(row)
        return rows

    def _plugin_id(self) -> str:
        return self._string(self.info.get('id') or 'spine_emotion_editor')

    def _as_map(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        return {str(key): value for key, value in data.items()}

    def _normalize_name_list(self, raw: Any) -> List[str]:
        if not isinstance(raw, list):
            return []
        values = {
            self._normalize_name(item)
            for item in raw
        }
        values.discard('')
        return sorted(values, key=self._natural_sort_key)

    def _normalize_text_list(self, raw: Any) -> List[str]:
        if not isinstance(raw, list):
            return []
        values = {
            self._string(item)
            for item in raw
        }
        values.discard('')
        return sorted(values, key=self._natural_sort_key)

    def _string(self, value: Any) -> str:
        return str(value or '').strip()

    def _normalize_name(self, value: Any) -> str:
        return self._string(value).lower()

    def _natural_sort_key(self, value: Any) -> Tuple[Any, ...]:
        parts = re.split(r'(\d+)', self._string(value).casefold())
        key: List[Any] = []
        for part in parts:
            if not part:
                continue
            if part.isdigit():
                key.append((0, int(part)))
            else:
                key.append((1, part))
        return tuple(key)

    def _tr(self, en: str, ru: str) -> str:
        return ru if self._is_ru_locale() else en

    def _is_ru_locale(self) -> bool:
        return self._ui_locale.startswith('ru')


if __name__ == '__main__':
    run_plugin(SpineEmotionEditorPlugin)
