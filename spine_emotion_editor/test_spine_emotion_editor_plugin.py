import importlib.machinery
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest


def _load_plugin_module():
    plugin_path = pathlib.Path(__file__).with_name('plugin.py3')
    loader = importlib.machinery.SourceFileLoader('spine_emotion_editor_plugin', str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('failed to create import spec for spine_emotion_editor plugin')
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


_MODULE = _load_plugin_module()
SpineEmotionEditorPlugin = _MODULE.SpineEmotionEditorPlugin


class SpineEmotionEditorPluginDraftTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = SpineEmotionEditorPlugin()
        self.plugin._character_id = 'wife_103'
        self.plugin._base_profile_document = {
            'id': 'wife_103',
            'generatedBy': 'tools/spine_asset_normalizer.py',
            'interaction': {'initialState': 'idle'},
            'features': [{'id': 'soft_smile'}, {'id': 'anim_idle_face_04', 'animation': 'Idle_face_04'}],
        }
        self.plugin._form = {
            'defaultAnimation': 'Idle_face_05',
            'previewEmotion': 'smile',
            'previewAnimation': '',
            'emotionRows': [
                {'emotion': 'normal', 'animation': 'Idle'},
                {'emotion': 'smile', 'animation': 'Idle_face_05'},
                {'emotion': '', 'animation': 'ignored'},
            ],
            'aliasRows': [
                {'alias': 'happy', 'emotion': 'smile'},
                {'alias': 'idle', 'emotion': 'normal'},
                {'alias': '', 'emotion': 'smile'},
            ],
        }

    def test_build_draft_profile_preserves_unknown_sections(self) -> None:
        draft = self.plugin._build_draft_profile()

        self.assertEqual(draft.get('id'), 'wife_103')
        self.assertEqual(draft.get('defaultAnimation'), 'Idle_face_05')
        self.assertEqual(
            draft.get('emotionAnimations'),
            {
                'normal': 'Idle',
                'smile': 'Idle_face_05',
            },
        )
        self.assertEqual(
            draft.get('emotionAliases'),
            {
                'happy': 'smile',
                'idle': 'normal',
            },
        )
        self.assertEqual(draft.get('generatedBy'), 'tools/spine_asset_normalizer.py')
        self.assertEqual(draft.get('interaction'), {'initialState': 'idle'})
        self.assertEqual(
            draft.get('features'),
            [{'id': 'soft_smile'}, {'id': 'anim_idle_face_04', 'animation': 'Idle_face_04'}],
        )


class SpineEmotionEditorPluginSaveTest(unittest.TestCase):
    def test_save_profile_to_disk_updates_only_editable_fields(self) -> None:
        plugin = SpineEmotionEditorPlugin()
        plugin.send_message = lambda tag, data=None: None  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            profile_path = root / 'assets' / 'characters' / 'spine_profiles' / 'hero_13.spine.json'
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            profile_path.write_text(
                json.dumps(
                    {
                        'id': 'hero_13',
                        'generatedBy': 'tools/spine_asset_normalizer.py',
                        'defaultAnimation': 'Idle',
                        'emotionAnimations': {'normal': 'Idle'},
                        'emotionAliases': {'idle': 'normal'},
                        'interaction': {'initialState': 'idle'},
                        'features': [{'id': 'soft_smile'}, {'id': 'anim_idle_sp1u', 'animation': 'Idle_sp1u'}],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding='utf-8',
            )

            plugin.info = {'rootDirPath': str(root)}
            plugin._character_id = 'hero_13'
            plugin._spine_pack_path = 'assets/spine/hero_13/Hero_13.skel'
            plugin._spine_profile_rel_path = 'assets/characters/spine_profiles/hero_13.spine.json'
            plugin._spine_profile_abs_path = str(profile_path)
            plugin._base_profile_document = json.loads(profile_path.read_text(encoding='utf-8'))
            plugin._form = {
                'defaultAnimation': 'Idle_sp1',
                'previewEmotion': 'magic',
                'previewAnimation': '',
                'emotionRows': [
                    {'emotion': 'normal', 'animation': 'Idle'},
                    {'emotion': 'magic', 'animation': 'Idle_sp1'},
                ],
                'aliasRows': [
                    {'alias': 'idle', 'emotion': 'normal'},
                    {'alias': 'excited', 'emotion': 'magic'},
                ],
            }

            saved = plugin._save_profile_to_disk()

            self.assertIsNotNone(saved)
            decoded = json.loads(profile_path.read_text(encoding='utf-8'))
            self.assertEqual(decoded.get('defaultAnimation'), 'Idle_sp1')
            self.assertEqual(
                decoded.get('emotionAnimations'),
                {
                    'normal': 'Idle',
                    'magic': 'Idle_sp1',
                },
            )
            self.assertEqual(
                decoded.get('emotionAliases'),
                {
                    'idle': 'normal',
                    'excited': 'magic',
                },
            )
            self.assertEqual(decoded.get('generatedBy'), 'tools/spine_asset_normalizer.py')
            self.assertEqual(decoded.get('interaction'), {'initialState': 'idle'})
            self.assertEqual(
                decoded.get('features'),
                [{'id': 'soft_smile'}, {'id': 'anim_idle_sp1u', 'animation': 'Idle_sp1u'}],
            )


class SpineEmotionEditorPluginStartupTest(unittest.TestCase):
    def test_on_init_requests_context_without_loading_complete_barrier(self) -> None:
        plugin = SpineEmotionEditorPlugin()
        published = []
        requests = []
        menu_syncs = []

        plugin.add_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.register_command = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.add_locale_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin._sync_menu_link = lambda: menu_syncs.append(True)  # type: ignore[method-assign]
        plugin._publish_ui = lambda force_set=False: published.append(force_set)  # type: ignore[method-assign]
        plugin._request_context = lambda reload_profile: requests.append(reload_profile)  # type: ignore[method-assign]

        plugin.on_init()

        self.assertEqual(menu_syncs, [True])
        self.assertEqual(published, [True])
        self.assertEqual(requests, [True])


if __name__ == '__main__':
    unittest.main()
