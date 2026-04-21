import pathlib
import sys
import unittest

_PLUGIN_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_PLUGIN_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_REPO_ROOT))

from test_support import load_plugin_module

_MODULE = load_plugin_module(__file__, 'character_preview_generator', 'character_preview_generator_plugin')
CharacterPreviewGeneratorPlugin = _MODULE.CharacterPreviewGeneratorPlugin


class CharacterPreviewGeneratorPluginStartupTest(unittest.TestCase):
    def test_on_init_syncs_menu_link_without_loading_complete_barrier(self) -> None:
        plugin = CharacterPreviewGeneratorPlugin()
        links = []

        plugin.add_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.register_command = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.add_locale_listener = lambda *args, **kwargs: None  # type: ignore[method-assign]
        plugin.set_event_link = lambda event, command, rule=None, msg_data=None: links.append((event, command, rule))  # type: ignore[method-assign]
        plugin.remove_event_link = lambda *args, **kwargs: None  # type: ignore[method-assign]

        plugin.on_init()

        self.assertEqual(plugin._menu_rule, 'Отладка/Генерация превью')
        self.assertEqual(
            links,
            [('gui:menu-action', 'character-preview:generate-all', 'Отладка/Генерация превью')],
        )


if __name__ == '__main__':
    unittest.main()
